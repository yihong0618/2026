import argparse
import json
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pendulum
import requests

TIMEZONE = "Asia/Shanghai"
DEFAULT_CHANNEL = "@hyi0618"
DEFAULT_SELECTED_TAG = "#selected"
DEFAULT_OUTPUT = "summaries/tg_weekly_summary.md"
DEFAULT_MODEL = "gpt-4o-mini"
TELEGRAM_ARCHIVE_URL = "https://t.me/s/{channel}"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
URL_RE = re.compile(r"https?://[^\s<>()\]）\"']+")
SELECTED_TITLE_RE = re.compile(
    r"^(\s*\d+\.\s+\*\*)(?:\[([^\]]+)\]\([^)]+\)|([^*]+?))(\*\*)(.*)$"
)


@dataclass(frozen=True)
class ChannelMessage:
    message_id: int
    post: str
    url: str
    date: pendulum.DateTime
    text: str
    links: tuple[str, ...]
    source: str

    def has_tag(self, tag: str) -> bool:
        return tag.lower() in self.text.lower()

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "post": self.post,
            "url": self.url,
            "date": self.date.to_iso8601_string(),
            "text": self.text,
            "links": list(self.links),
            "source": self.source,
        }


class TelegramArchiveParser(HTMLParser):
    def __init__(self, channel: str):
        super().__init__(convert_charrefs=True)
        self.channel = normalize_channel(channel)
        self.messages: list[ChannelMessage] = []
        self._current: dict[str, Any] | None = None
        self._message_div_depth = 0
        self._collect_text = False
        self._text_div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())

        if (
            tag == "div"
            and "tgme_widget_message" in classes
            and attr_map.get("data-post")
            and self._current is None
        ):
            self._current = {
                "post": attr_map["data-post"],
                "datetime": "",
                "text_parts": [],
                "links": [],
            }
            self._message_div_depth = 1
            return

        if self._current is None:
            return

        if tag == "div":
            self._message_div_depth += 1
            if "tgme_widget_message_text" in classes:
                self._collect_text = True
                self._text_div_depth = 1
                return
            if self._collect_text:
                self._text_div_depth += 1

        if tag == "time" and attr_map.get("datetime"):
            self._current["datetime"] = attr_map["datetime"]

        if tag == "br" and self._collect_text:
            self._current["text_parts"].append("\n")

        href = attr_map.get("href", "")
        if tag == "a" and href and self._collect_text:
            self._current["links"].append(urljoin("https://t.me", href))

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return

        if tag == "div" and self._collect_text:
            self._text_div_depth -= 1
            if self._text_div_depth <= 0:
                self._collect_text = False

        if tag == "div":
            self._message_div_depth -= 1
            if self._message_div_depth <= 0:
                self._finish_message()

    def handle_data(self, data: str) -> None:
        if self._current is None or not self._collect_text:
            return
        self._current["text_parts"].append(data)

    def _finish_message(self) -> None:
        if self._current is None:
            return

        post = str(self._current["post"])
        date_text = str(self._current["datetime"])
        text = normalize_message_text("".join(self._current["text_parts"]))
        try:
            message_id = int(post.rsplit("/", 1)[-1])
            parsed_date = pendulum.parse(date_text)
            if not isinstance(parsed_date, pendulum.DateTime):
                raise ValueError(f"unsupported Telegram date: {date_text}")
            date = parsed_date.in_timezone(TIMEZONE)
        except (TypeError, ValueError):
            self._reset_current()
            return

        links = unique_links(
            [
                *self._current["links"],
                *extract_urls(text),
            ]
        )
        self.messages.append(
            ChannelMessage(
                message_id=message_id,
                post=post,
                url=f"https://t.me/{post}",
                date=date,
                text=text,
                links=links,
                source="web",
            )
        )
        self._reset_current()

    def _reset_current(self) -> None:
        self._current = None
        self._message_div_depth = 0
        self._collect_text = False
        self._text_div_depth = 0


def normalize_channel(channel: str) -> str:
    return channel.strip().removeprefix("@").removeprefix("https://t.me/")


def normalize_message_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        clean_line = " ".join(line.split())
        if clean_line:
            lines.append(clean_line)
    return "\n".join(lines).strip()


def extract_urls(text: str) -> tuple[str, ...]:
    links = []
    for match in URL_RE.finditer(text):
        links.append(match.group(0).rstrip(".,;:!?，。；：！？"))
    return unique_links(links)


def unique_links(links: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen = set()
    result = []
    for link in links:
        clean_link = link.strip()
        if not clean_link or clean_link in seen:
            continue
        seen.add(clean_link)
        result.append(clean_link)
    return tuple(result)


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        env[key] = value
    return env


def apply_env_file(path: Path) -> None:
    for key, value in load_env_file(path).items():
        os.environ.setdefault(key, value)


def last_week_window(
    now: pendulum.DateTime | None = None,
    timezone: str = TIMEZONE,
) -> tuple[pendulum.DateTime, pendulum.DateTime]:
    current = (now or pendulum.now(timezone)).in_timezone(timezone)
    monday_start = current.start_of("day").subtract(days=current.day_of_week)
    return monday_start.subtract(weeks=1), monday_start


def parse_window(
    since: str | None,
    until: str | None,
    timezone: str = TIMEZONE,
) -> tuple[pendulum.DateTime, pendulum.DateTime]:
    default_since, default_until = last_week_window(timezone=timezone)
    return (
        parse_datetime(since, timezone) if since else default_since,
        parse_datetime(until, timezone) if until else default_until,
    )


def parse_datetime(value: str, timezone: str) -> pendulum.DateTime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return pendulum.from_format(value, "YYYY-MM-DD", tz=timezone)
    parsed = pendulum.parse(value, tz=timezone)
    if not isinstance(parsed, pendulum.DateTime):
        raise ValueError(f"unsupported datetime: {value}")
    return parsed.in_timezone(timezone)


def fetch_archive_messages(
    channel: str,
    since: pendulum.DateTime,
    until: pendulum.DateTime,
    limit_pages: int,
    timeout: float,
) -> list[ChannelMessage]:
    channel_name = normalize_channel(channel)
    session = requests.Session()
    before: int | None = None
    by_post: dict[str, ChannelMessage] = {}

    for _ in range(limit_pages):
        params = {"before": before} if before else None
        response = session.get(
            TELEGRAM_ARCHIVE_URL.format(channel=channel_name),
            params=params,
            timeout=timeout,
            headers={"User-Agent": "y2026-weekly-tg-summary/1.0"},
        )
        response.raise_for_status()

        parser = TelegramArchiveParser(channel_name)
        parser.feed(response.text)
        page_messages = parser.messages
        if not page_messages:
            break

        for message in page_messages:
            by_post.setdefault(message.post, message)

        oldest = min(page_messages, key=lambda message: message.message_id)
        newest_date = max(message.date for message in page_messages)
        before = oldest.message_id
        if newest_date < since:
            break

    return filter_messages(by_post.values(), since, until)


def fetch_bot_update_messages(
    token: str,
    channel: str,
    since: pendulum.DateTime,
    until: pendulum.DateTime,
    timeout: float,
) -> list[ChannelMessage]:
    channel_name = normalize_channel(channel)
    params: dict[str, str | int] = {
        "limit": 100,
        "allowed_updates": json.dumps(
            ["channel_post", "edited_channel_post"],
            ensure_ascii=False,
        ),
    }
    response = requests.get(
        TELEGRAM_API_URL.format(token=token, method="getUpdates"),
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram getUpdates failed"))

    messages = []
    for update in payload.get("result", []):
        raw_message = update.get("channel_post") or update.get("edited_channel_post")
        if not raw_message:
            continue
        chat = raw_message.get("chat", {})
        if channel_name and chat.get("username") != channel_name:
            continue

        text = normalize_message_text(
            raw_message.get("text") or raw_message.get("caption") or ""
        )
        if not text:
            continue

        message_id = int(raw_message["message_id"])
        date = pendulum.from_timestamp(raw_message["date"], tz="UTC").in_timezone(
            TIMEZONE
        )
        username = chat.get("username", channel_name)
        links = list(extract_urls(text))
        for entity in [
            *raw_message.get("entities", []),
            *raw_message.get("caption_entities", []),
        ]:
            if entity.get("type") == "text_link" and entity.get("url"):
                links.append(entity["url"])

        messages.append(
            ChannelMessage(
                message_id=message_id,
                post=f"{username}/{message_id}",
                url=f"https://t.me/{username}/{message_id}" if username else "",
                date=date,
                text=text,
                links=unique_links(links),
                source="bot-updates",
            )
        )

    return filter_messages(messages, since, until)


def filter_messages(
    messages: list[ChannelMessage] | Any,
    since: pendulum.DateTime,
    until: pendulum.DateTime,
) -> list[ChannelMessage]:
    return sorted(
        [message for message in messages if since <= message.date < until],
        key=lambda message: message.date,
    )


def get_env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def collect_messages(
    source: str,
    channel: str,
    since: pendulum.DateTime,
    until: pendulum.DateTime,
    limit_pages: int,
    timeout: float,
) -> tuple[list[ChannelMessage], str, list[str]]:
    warnings: list[str] = []
    token = get_env_value("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN", "TG_TOKEN")

    if source in {"auto", "web"}:
        try:
            messages = fetch_archive_messages(
                channel, since, until, limit_pages, timeout
            )
            if messages or source == "web":
                return messages, "web", warnings
            warnings.append("公开频道网页没有抓到消息，改用 bot getUpdates。")
        except requests.RequestException as error:
            if source == "web":
                raise
            warnings.append(f"公开频道网页抓取失败：{error}")

    if source in {"auto", "bot-updates"}:
        if not token:
            if source == "bot-updates":
                raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN，无法使用 bot-updates。")
            warnings.append("缺少 TELEGRAM_BOT_TOKEN，跳过 bot getUpdates。")
            return [], "auto", warnings
        return (
            fetch_bot_update_messages(token, channel, since, until, timeout),
            "bot-updates",
            warnings,
        )

    raise ValueError(f"unknown source: {source}")


def summarize_with_openai(
    messages: list[ChannelMessage],
    selected_tag: str,
    model: str,
    timeout: float,
) -> str:
    api_key = get_env_value("OPENAI_API_KEY", "OPENAI_TOKEN")
    base_url = get_env_value("OPENAI_BASE_URL", "OPENAI_API_BASE")
    if not api_key or not base_url:
        return "未配置 OPENAI_API_KEY/OPENAI_API_BASE，跳过 AI 草稿。"

    selected = [message for message in messages if message.has_tag(selected_tag)]
    payload_messages = [
        {
            "date": message.date.format("YYYY-MM-DD HH:mm"),
            "telegram_url": message.url,
            "text": truncate_text(message.text, 900),
            "links": list(message.links),
            "selected": message in selected,
        }
        for message in messages
    ]
    system_prompt = (
        "你是中文周总结素材整理助手。只根据用户给出的 Telegram 消息整理素材，"
        "不要编造原文没有的信息。输出 Markdown。"
    )
    user_prompt = (
        "请整理这些频道消息，给我一份可继续手工改写的周总结素材。\n"
        "结构固定为：\n"
        "## 上周频道内容提纲\n"
        "按主题归类，用短 bullet。\n\n"
        f"## {selected_tag} 文章\n"
        "字段说明：telegram_url 是频道原消息链接，links 是文章原文链接。\n"
        "只列带 selected 标记的文章或链接。每条标题必须写成 "
        "`1. **[标题或主题](实际 telegram_url)**`，标题链接使用 Telegram 原消息链接；"
        "下一行再写文章原文链接；再下一行写 50 字以内简介。\n\n"
        "## 可以展开写的角度\n"
        "给 3-5 个适合写周总结的角度。\n\n"
        "消息 JSON：\n"
        f"{json.dumps(payload_messages, ensure_ascii=False, indent=2)}"
    )
    response = requests.post(
        chat_completions_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    ai_summary = payload["choices"][0]["message"]["content"].strip()
    return link_selected_titles_to_telegram(ai_summary, selected, selected_tag)


def link_selected_titles_to_telegram(
    markdown: str, selected_messages: list[ChannelMessage], selected_tag: str
) -> str:
    selected_iter = iter(selected_messages)
    lines = []
    in_selected_section = False

    for line in markdown.splitlines():
        if line.startswith("## "):
            in_selected_section = selected_tag in line
        if in_selected_section:
            match = SELECTED_TITLE_RE.match(line)
            if match:
                message = next(selected_iter, None)
                if message is not None:
                    title = (match.group(2) or match.group(3) or "").strip()
                    line = (
                        f"{match.group(1)}[{title}]({message.url})"
                        f"{match.group(4)}{match.group(5)}"
                    )
        lines.append(line)

    return "\n".join(lines)


def chat_completions_url(base_url: str) -> str:
    clean_base = base_url.rstrip("/")
    if clean_base.endswith("/chat/completions"):
        return clean_base
    if clean_base.endswith("/v1"):
        return f"{clean_base}/chat/completions"
    return f"{clean_base}/v1/chat/completions"


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def render_markdown(
    messages: list[ChannelMessage],
    ai_summary: str,
    selected_tag: str,
    channel: str,
    since: pendulum.DateTime,
    until: pendulum.DateTime,
    source: str,
    warnings: list[str],
) -> str:
    selected = [message for message in messages if message.has_tag(selected_tag)]
    lines = [
        "# Telegram 周总结素材",
        "",
        f"- 频道：{channel}",
        f"- 时间：{since.format('YYYY-MM-DD HH:mm')} - {until.format('YYYY-MM-DD HH:mm')} ({TIMEZONE})",
        f"- 来源：{source}",
        f"- 消息数：{len(messages)}",
        f"- {selected_tag}：{len(selected)}",
        "",
    ]
    if warnings:
        lines.extend(["## 抓取提示", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    lines.extend(
        ["## AI 草稿", "", ai_summary.strip(), "", f"## {selected_tag} 原始素材", ""]
    )
    if selected:
        for message in selected:
            lines.extend(render_message_block(message))
    else:
        lines.extend([f"没有找到带 {selected_tag} 的消息。", ""])

    lines.extend(["## 全部消息索引", ""])
    if messages:
        for message in messages:
            text_lines = message.text.splitlines()
            first_line = truncate_text(
                text_lines[0] if text_lines else "[无文本消息]", 90
            )
            tag = f" {selected_tag}" if message in selected else ""
            lines.append(
                f"- {message.date.format('MM-DD HH:mm')} [{message.message_id}]({message.url}){tag}：{first_line}"
            )
    else:
        lines.append("没有抓到这个时间范围内的消息。")
    lines.append("")
    return "\n".join(lines)


def render_message_block(message: ChannelMessage) -> list[str]:
    lines = [
        f"### [{message_title(message)}]({message.url})",
        "",
        f"- 时间：{message.date.format('MM-DD HH:mm')}",
        f"- Telegram：[{message.message_id}]({message.url})",
        "",
        message.text,
        "",
    ]
    if message.links:
        lines.append("链接：")
        lines.extend(f"- {link}" for link in message.links)
        lines.append("")
    return lines


def message_title(message: ChannelMessage) -> str:
    for line in message.text.splitlines():
        title = line.replace(DEFAULT_SELECTED_TAG, "").strip()
        if title:
            return truncate_text(title, 80)
    return str(message.message_id)


def write_outputs(
    messages: list[ChannelMessage],
    markdown: str,
    output_path: Path,
    raw_output_path: Path | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    if raw_output_path is None:
        return

    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path.write_text(
        json.dumps(
            [message.as_dict() for message in messages], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="整理 Telegram 频道上周消息，生成周总结素材。"
    )
    parser.add_argument(
        "--channel", default=DEFAULT_CHANNEL, help="频道名，如 @hyi0618"
    )
    parser.add_argument(
        "--env-file", default=".env", help="读取 token/base url 的 .env"
    )
    parser.add_argument(
        "--source",
        choices=["auto", "web", "bot-updates"],
        default="auto",
        help="消息来源：公开视频页或 Telegram Bot getUpdates",
    )
    parser.add_argument("--since", help="开始时间，默认上周一 00:00")
    parser.add_argument("--until", help="结束时间，默认本周一 00:00")
    parser.add_argument("--selected-tag", default=DEFAULT_SELECTED_TAG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-output", default="", help="可选：输出原始消息 JSON")
    parser.add_argument("--limit-pages", type=int, default=8)
    parser.add_argument("--model", default="", help="OpenAI-compatible 模型名")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-ai", action="store_true", help="只抓取和整理原始素材")
    return parser


def run(options: argparse.Namespace) -> tuple[Path, Path | None]:
    apply_env_file(Path(options.env_file))
    since, until = parse_window(options.since, options.until)
    messages, used_source, warnings = collect_messages(
        options.source,
        options.channel,
        since,
        until,
        options.limit_pages,
        options.timeout,
    )
    model = options.model or get_env_value("OPENAI_MODEL") or DEFAULT_MODEL
    if options.no_ai:
        ai_summary = "已通过 --no-ai 跳过 AI 草稿。"
    else:
        try:
            ai_summary = summarize_with_openai(
                messages,
                options.selected_tag,
                model,
                options.timeout,
            )
        except (KeyError, requests.RequestException, RuntimeError, ValueError) as error:
            ai_summary = f"AI 草稿生成失败：{error}"

    markdown = render_markdown(
        messages,
        ai_summary,
        options.selected_tag,
        options.channel,
        since,
        until,
        used_source,
        warnings,
    )
    output_path = Path(options.output)
    raw_output_path = Path(options.raw_output) if options.raw_output else None
    write_outputs(messages, markdown, output_path, raw_output_path)
    return output_path, raw_output_path


def cli(argv: list[str] | None = None) -> None:
    options = build_parser().parse_args(argv)
    output_path, raw_output_path = run(options)
    print(f"Wrote {output_path}")
    if raw_output_path is not None:
        print(f"Wrote {raw_output_path}")


if __name__ == "__main__":
    cli()
