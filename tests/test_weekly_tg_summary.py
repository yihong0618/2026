import pendulum

from y2026 import weekly_tg_summary


def test_last_week_window_uses_monday_boundaries():
    now = pendulum.datetime(2026, 6, 1, 9, tz=weekly_tg_summary.TIMEZONE)

    since, until = weekly_tg_summary.last_week_window(now)

    assert since.to_datetime_string() == "2026-05-25 00:00:00"
    assert until.to_datetime_string() == "2026-06-01 00:00:00"


def test_telegram_archive_parser_extracts_text_date_and_links():
    html = """
    <div class="tgme_widget_message js-widget_message" data-post="hyi0618/123">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        分享一篇文章 <a href="https://example.com/a">链接</a><br>
        #selected
      </div>
      <a class="tgme_widget_message_date" href="/hyi0618/123">
        <time datetime="2026-05-28T02:30:00+00:00"></time>
      </a>
    </div>
    """
    parser = weekly_tg_summary.TelegramArchiveParser("@hyi0618")

    parser.feed(html)

    assert len(parser.messages) == 1
    message = parser.messages[0]
    assert message.message_id == 123
    assert message.url == "https://t.me/hyi0618/123"
    assert message.date.to_datetime_string() == "2026-05-28 10:30:00"
    assert message.text == "分享一篇文章 链接\n#selected"
    assert message.links == ("https://example.com/a",)
    assert message.has_tag("#selected")


def test_render_markdown_keeps_selected_raw_material():
    message = weekly_tg_summary.ChannelMessage(
        message_id=123,
        post="hyi0618/123",
        url="https://t.me/hyi0618/123",
        date=pendulum.datetime(2026, 5, 28, 10, 30, tz=weekly_tg_summary.TIMEZONE),
        text="分享一篇文章 #selected",
        links=("https://example.com/a",),
        source="web",
    )

    markdown = weekly_tg_summary.render_markdown(
        [message],
        "AI 草稿",
        "#selected",
        "@hyi0618",
        pendulum.datetime(2026, 5, 25, tz=weekly_tg_summary.TIMEZONE),
        pendulum.datetime(2026, 6, 1, tz=weekly_tg_summary.TIMEZONE),
        "web",
        [],
    )

    assert "# Telegram 周总结素材" in markdown
    assert "## #selected 原始素材" in markdown
    assert "### [分享一篇文章](https://t.me/hyi0618/123)" in markdown
    assert "- Telegram：[123](https://t.me/hyi0618/123)" in markdown
    assert "https://example.com/a" in markdown
    assert "05-28 10:30 [123](https://t.me/hyi0618/123) #selected" in markdown


def test_render_markdown_allows_empty_text_messages():
    message = weekly_tg_summary.ChannelMessage(
        message_id=124,
        post="hyi0618/124",
        url="https://t.me/hyi0618/124",
        date=pendulum.datetime(2026, 5, 28, 11, 30, tz=weekly_tg_summary.TIMEZONE),
        text="",
        links=(),
        source="web",
    )

    markdown = weekly_tg_summary.render_markdown(
        [message],
        "AI 草稿",
        "#selected",
        "@hyi0618",
        pendulum.datetime(2026, 5, 25, tz=weekly_tg_summary.TIMEZONE),
        pendulum.datetime(2026, 6, 1, tz=weekly_tg_summary.TIMEZONE),
        "web",
        [],
    )

    assert "[无文本消息]" in markdown


def test_link_selected_titles_to_telegram_links_title_lines():
    first = weekly_tg_summary.ChannelMessage(
        message_id=123,
        post="hyi0618/123",
        url="https://t.me/hyi0618/123",
        date=pendulum.datetime(2026, 5, 28, 10, 30, tz=weekly_tg_summary.TIMEZONE),
        text="The Eternal Sloptember #selected",
        links=("https://example.com/a",),
        source="web",
    )
    second = weekly_tg_summary.ChannelMessage(
        message_id=124,
        post="hyi0618/124",
        url="https://t.me/hyi0618/124",
        date=pendulum.datetime(2026, 5, 28, 11, 30, tz=weekly_tg_summary.TIMEZONE),
        text="Global Rail #selected",
        links=("https://example.com/b",),
        source="web",
    )
    markdown = """## 上周频道内容提纲
- 技术文章

## #selected 文章
1. **The Eternal Sloptember**
   [链接](https://example.com/a)

2. **[Global Rail](https://old.example.com)**
   [链接](https://example.com/b)

## 可以展开写的角度
- 文章分享
"""

    linked = weekly_tg_summary.link_selected_titles_to_telegram(
        markdown, [first, second], "#selected"
    )

    assert "1. **[The Eternal Sloptember](https://t.me/hyi0618/123)**" in linked
    assert "2. **[Global Rail](https://t.me/hyi0618/124)**" in linked
    assert "[链接](https://example.com/a)" in linked


def test_write_outputs_skips_raw_json_by_default(tmp_path):
    output_path = tmp_path / "summary.md"
    raw_output_path = tmp_path / "messages.json"

    weekly_tg_summary.write_outputs([], "summary", output_path)

    assert output_path.read_text(encoding="utf-8") == "summary"
    assert not raw_output_path.exists()


def test_chat_completions_url_accepts_base_or_full_endpoint():
    assert (
        weekly_tg_summary.chat_completions_url("https://api.example.com")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        weekly_tg_summary.chat_completions_url("https://api.example.com/v1")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        weekly_tg_summary.chat_completions_url(
            "https://api.example.com/v1/chat/completions"
        )
        == "https://api.example.com/v1/chat/completions"
    )
