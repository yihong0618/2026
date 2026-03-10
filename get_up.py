import argparse
import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pendulum
import requests
import telebot
from github import Auth, Github
from telegramify_markdown import markdownify
from zhconv import convert

# 1 real get up
GET_UP_ISSUE_NUMBER = 1
GET_UP_MESSAGE_TEMPLATE = """今天的起床时间是--{get_up_time}。

起床啦。

今天是今年的第 {day_of_year} 天。

{year_progress}

{leetcode}

{running_info}

{history_today}

{blog_article}
"""
TG_MORNING_TAG = "#morning"

LEETCODE_EASY_FILE = "leetcode_easy.txt"
LEETCODE_USED_FILE = "leetcode_used.txt"
LEETCODE_HOT100_FILE = "leetcode_hot100.txt"
LEETCODE_HOT100_USED_FILE = "leetcode_hot100_used.txt"
BLOG_SITES_USED_FILE = "blog_sites_used.txt"

TIMEZONE = "Asia/Shanghai"
SCRIPT_DIR = Path(__file__).resolve().parent

LEETCODE_BASE_URL = "https://leetcode.cn/problems/{slug}/"
LEETCODE_DAILY_URL = "https://leetcode.cn/graphql/"
RUN_DATA_URL = (
    "https://github.com/yihong0618/run/raw/refs/heads/master/run_page/data.parquet"
)
WIKIMEDIA_USER_AGENT = "GetUpBot/1.0 (https://github.com/yihong0618/2026)"
BLOG_HISTORY_START_YEAR = 2005
BLOG_HISTORY_END_YEAR = 2025
BLOG_RANDOM_SEARCH_ATTEMPTS = 5
BLOG_LINK_CHECK_ATTEMPTS = 10
HOT100_RANDOM_SALT = 42
BLOG_RANDOM_SALT = 99
EARLY_GET_UP_HOURS = range(3, 10)

BIRTH_YEAR = 1989  # change it to your birth year


@dataclass(frozen=True)
class LeetCodeProblem:
    problem_id: str
    title: str
    slug: str
    difficulty: str = "EASY"

    @property
    def url(self):
        return LEETCODE_BASE_URL.format(slug=self.slug)


@dataclass(frozen=True)
class GetUpMessageParts:
    is_get_up_early: bool
    day_of_year: int
    year_progress: str
    running_info: str
    history_today: str
    leetcode: str
    blog_article: str

    def as_tuple(self):
        return (
            self.is_get_up_early,
            self.day_of_year,
            self.year_progress,
            self.running_info,
            self.history_today,
            self.leetcode,
            self.blog_article,
        )

    def render(self, get_up_time):
        return GET_UP_MESSAGE_TEMPLATE.format(
            get_up_time=get_up_time,
            day_of_year=self.day_of_year,
            year_progress=self.year_progress,
            running_info=self.running_info,
            history_today=self.history_today,
            leetcode=self.leetcode,
            blog_article=self.blog_article,
        )


def login(token):
    return Github(auth=Auth.Token(token))


def _now():
    return pendulum.now(TIMEZONE)


def _data_file_path(filename):
    return SCRIPT_DIR / filename


def _daily_rng(now, salt=0):
    day_seed = now.year * 1000 + now.day_of_year + salt
    return random.Random(day_seed)


def _read_non_empty_lines(path):
    file_path = Path(path)
    if not file_path.exists():
        return []

    with file_path.open("r", encoding="utf-8") as file:
        return [line.strip() for line in file if line.strip()]


def _append_line(path, value):
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(f"{value}\n")


def _parse_problem_line(line):
    parts = line.split("|")
    if len(parts) < 3:
        return None

    difficulty = parts[3].upper() if len(parts) >= 4 else "EASY"
    return LeetCodeProblem(
        problem_id=parts[0],
        title=parts[1],
        slug=parts[2],
        difficulty=difficulty,
    )


def _load_problem_pool(problem_file):
    problems = []
    for line in _read_non_empty_lines(problem_file):
        problem = _parse_problem_line(line)
        if problem:
            problems.append(problem)
    return problems


def _pick_problem_from_pool(problem_file, used_file, now):
    used_slugs = set(_read_non_empty_lines(used_file))
    available = [
        problem
        for problem in _load_problem_pool(problem_file)
        if problem.slug not in used_slugs
    ]
    if not available:
        return None

    selected = _daily_rng(now).choice(available)
    _append_line(used_file, selected.slug)
    return selected


def _get_leetcode_daily_question():
    query = """
    query questionOfToday {
        todayRecord {
            question {
                questionFrontendId
                title
                titleSlug
                difficulty
                isPaidOnly
            }
        }
    }
    """

    try:
        response = requests.post(
            LEETCODE_DAILY_URL,
            json={"query": query},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if not response.ok:
            return None

        records = response.json().get("data", {}).get("todayRecord", [])
        if not records:
            return None

        question = records[0].get("question", {})
        if not question or question.get("isPaidOnly", False):
            return None

        return LeetCodeProblem(
            problem_id=question.get("questionFrontendId", ""),
            title=question.get("title", ""),
            slug=question.get("titleSlug", ""),
            difficulty=question.get("difficulty", "").upper(),
        )
    except Exception as error:
        print(f"Error getting daily question: {error}")
        return None


def _format_easy_problem(problem, prefix="今日 LeetCode 🟢 简单题："):
    return f"{prefix}\n\n[{problem.problem_id}. {problem.title}]({problem.url})"


def _format_hot100_problem(problem):
    diff_map = {
        "EASY": ("简单", "🟢"),
        "MEDIUM": ("中等", "🟡"),
    }
    diff_label, diff_emoji = diff_map.get(problem.difficulty, ("中等", "🟡"))
    return (
        f"今日 LeetCode 热题 100 {diff_emoji} {diff_label}题：\n\n"
        f"[{problem.problem_id}. {problem.title}]({problem.url})"
    )


def get_daily_leetcode():
    try:
        now = _now()
        easy_file = _data_file_path(LEETCODE_EASY_FILE)
        easy_used_file = _data_file_path(LEETCODE_USED_FILE)
        hot100_file = _data_file_path(LEETCODE_HOT100_FILE)
        hot100_used_file = _data_file_path(LEETCODE_HOT100_USED_FILE)

        results = []
        easy_used_slugs = set(_read_non_empty_lines(easy_used_file))
        daily_question = _get_leetcode_daily_question()
        if (
            daily_question
            and daily_question.difficulty == "EASY"
            and daily_question.slug not in easy_used_slugs
        ):
            _append_line(easy_used_file, daily_question.slug)
            results.append(
                _format_easy_problem(
                    daily_question,
                    prefix="今日官方每日一题！ 今日 LeetCode 🟢 简单题：",
                )
            )

        if _daily_rng(now, HOT100_RANDOM_SALT).random() < 0.5:
            hot100_problem = _pick_problem_from_pool(hot100_file, hot100_used_file, now)
            if hot100_problem:
                results.append(_format_hot100_problem(hot100_problem))
            else:
                results.append("热题 100 都做完啦！🎉")

        if not results:
            easy_problem = _pick_problem_from_pool(easy_file, easy_used_file, now)
            if easy_problem:
                results.append(_format_easy_problem(easy_problem))
            else:
                results.append("今日 LeetCode：所有简单题都做完啦！🎉")

        return "\n\n".join(results)
    except Exception as error:
        print(f"Error getting daily leetcode: {error}")
        return ""


def _extract_wiki_url(event):
    pages = event.get("pages") or []
    if not pages:
        return ""

    return pages[0].get("content_urls", {}).get("desktop", {}).get("page", "")


def _format_age_text(year, birth_year):
    if not year:
        return ""
    if year >= birth_year:
        return f"（那年我 {year - birth_year} 岁）"
    return f"（我出生前 {birth_year - year} 年）"


def _format_history_event(event, birth_year):
    year = event.get("year")
    text = convert(event.get("text", "").replace("\n", " ").strip(), "zh-cn")
    wiki_url = _extract_wiki_url(event)
    age_text = _format_age_text(year, birth_year)

    if wiki_url:
        line = f"• {year}年：[{text}]({wiki_url}) {age_text}"
    else:
        line = f"• {year}年：{text} {age_text}"
    return line.rstrip()


def get_history_today(birth_year=BIRTH_YEAR, limit=2):
    try:
        now = _now()
        month = now.format("MM")
        day = now.format("DD")

        response = requests.get(
            f"https://api.wikimedia.org/feed/v1/wikipedia/zh/onthisday/events/{month}/{day}",
            headers={"User-Agent": WIKIMEDIA_USER_AGENT},
            timeout=10,
        )
        if not response.ok:
            print(f"Failed to get history today: {response.status_code}")
            return ""

        events = response.json().get("events", [])
        if not events:
            return ""

        filtered_events = [
            event
            for event in events
            if "year" in event and birth_year <= event["year"] <= now.year
        ]
        if not filtered_events:
            filtered_events = [event for event in events if "year" in event]
        if not filtered_events:
            return ""

        selected_events = random.sample(
            filtered_events, min(limit, len(filtered_events))
        )
        selected_events.sort(key=lambda event: event.get("year", 0), reverse=True)

        lines = [_format_history_event(event, birth_year) for event in selected_events]
        return "历史上的今天：\n\n" + "\n".join(lines)
    except Exception:
        return "fail to get it"


def _extract_domain(url):
    if not url:
        return ""

    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            return domain[4:]
        return domain
    except Exception:
        return ""


def _load_used_sites():
    return set(_read_non_empty_lines(_data_file_path(BLOG_SITES_USED_FILE)))


def _save_used_site(domain):
    _append_line(_data_file_path(BLOG_SITES_USED_FILE), domain)


def _check_link_available(url, timeout=10):
    if not url:
        return False

    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return 200 <= response.status_code < 300
    except requests.exceptions.RequestException:
        return False


def _extract_text_snippet(content, max_length=200):
    if not content:
        return ""

    text = re.sub(r"<[^>]+>", "", content)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*`_~]", "", text)
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text

    truncated = text[:max_length]
    for punct in ["。", "？", "！", ". ", "? ", "! "]:
        last_pos = truncated.rfind(punct)
        if last_pos > max_length * 0.5:
            return text[: last_pos + len(punct)].strip()

    last_space = truncated.rfind(" ")
    if last_space > 0:
        return text[:last_space] + "..."
    return truncated + "..."


def _saveweb_api_url(date_str):
    return (
        "https://search-api.saveweb.org/api/search"
        f"?q=(date%20%3D%20sec({date_str}))&f=false&p=0&h=true"
    )


def _fetch_saveweb_articles(year, month, day):
    date_str = f"{year}-{month}-{day}"
    response = requests.get(_saveweb_api_url(date_str), timeout=10)
    if not response.ok:
        return []

    articles = []
    for hit in response.json().get("hits", []):
        link = hit.get("link", "")
        if not link:
            continue

        article = dict(hit)
        article["domain"] = _extract_domain(link)
        articles.append(article)
    return articles


def _collect_today_articles(month, day):
    all_articles = []
    for year in range(BLOG_HISTORY_START_YEAR, BLOG_HISTORY_END_YEAR + 1):
        try:
            all_articles.extend(_fetch_saveweb_articles(year, month, day))
        except Exception as error:
            print(f"Error fetching articles for {year}-{month}-{day}: {error}")
    return all_articles


def _collect_random_articles(now):
    rng = _daily_rng(now, BLOG_RANDOM_SALT)
    for _ in range(BLOG_RANDOM_SEARCH_ATTEMPTS):
        try:
            random_year = rng.randint(BLOG_HISTORY_START_YEAR, BLOG_HISTORY_END_YEAR)
            random_month = rng.randint(1, 12)
            random_day = rng.randint(1, 28)
            articles = _fetch_saveweb_articles(random_year, random_month, random_day)
            if articles:
                return articles
        except Exception:
            continue
    return []


def _pick_random_candidate(candidates, rng):
    index = rng.randrange(len(candidates))
    return candidates.pop(index)


def _select_blog_article(all_articles, used_sites, now):
    rng = _daily_rng(now)
    unused_articles = [
        article
        for article in all_articles
        if article.get("domain") and article["domain"] not in used_sites
    ]
    remaining_articles = list(all_articles)
    checked_articles = []

    for _ in range(min(BLOG_LINK_CHECK_ATTEMPTS, len(all_articles))):
        if unused_articles:
            candidate = _pick_random_candidate(unused_articles, rng)
            if candidate in remaining_articles:
                remaining_articles.remove(candidate)
        elif remaining_articles:
            candidate = _pick_random_candidate(remaining_articles, rng)
        else:
            break

        link = candidate.get("link", "")
        if link and _check_link_available(link):
            return candidate
        checked_articles.append(candidate)

    if checked_articles:
        return checked_articles[0]
    if remaining_articles:
        return remaining_articles[0]
    return None


def _get_article_date(selected):
    timestamp = selected.get("date")
    if not timestamp:
        return ""

    try:
        article_dt = pendulum.from_timestamp(int(timestamp))
        return article_dt.format("YYYY-MM-DD")
    except Exception:
        return ""


def _get_years_ago(article_date, current_year):
    if not article_date:
        return None

    try:
        article_year = int(article_date.split("-")[0])
        return current_year - article_year
    except Exception:
        return None


def _format_blog_article(selected, current_year):
    title = selected.get("title", "未知标题")
    link = selected.get("link", "")
    article_date = _get_article_date(selected)
    years_ago = _get_years_ago(article_date, current_year)
    snippet = _extract_text_snippet(selected.get("content", ""), max_length=200)

    if years_ago is not None and years_ago >= 0:
        header = f"**来自 {years_ago} 年前的博客** ({article_date})"
    else:
        header = "**历史上的博客**"

    lines = [f"{header}：[{title}]({link})" if link else f"{header}：{title}"]
    if snippet:
        lines.append("")
        lines.extend(f"> {line}" for line in snippet.splitlines() if line.strip())
    return "\n".join(lines)


def get_blog_article_from_history():
    """
    获取历史上今天的博客文章 (2005-2025年)

    从 saveweb.org API 随机获取一篇历史上今天发布的博客文章
    会记录已使用的网站域名，尽量返回不同网站的文章
    如果当天没有文章，则随机搜索其他日期
    """
    try:
        now = _now()
        used_sites = _load_used_sites()
        all_articles = _collect_today_articles(now.month, now.day)

        if not all_articles:
            print("No articles found for today, trying random date...")
            all_articles = _collect_random_articles(now)
        if not all_articles:
            return ""

        selected = _select_blog_article(all_articles, used_sites, now)
        if not selected:
            return ""

        domain = selected.get("domain", "")
        if domain and domain not in used_sites:
            _save_used_site(domain)

        return _format_blog_article(selected, now.year)
    except Exception as error:
        print(f"Error getting blog article: {error}")
        return ""


def _query_running_summary(conn, parquet_path, where_clause):
    query = f"""
    SELECT
        COUNT(*) as count,
        ROUND(SUM(distance)/1000, 2) as total_km
    FROM read_parquet('{parquet_path}')
    WHERE {where_clause}
    """
    return conn.execute(query).fetchone()


def _format_running_line(label, result):
    if result and result[0] > 0:
        return f"• {label}跑了 {result[1]} 公里"
    return f"• {label}没跑"


def get_running_distance():
    try:
        response = requests.get(RUN_DATA_URL)
        if not response.ok:
            return ""

        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(response.content)
            temp_file.flush()

            now = _now()
            yesterday = now.subtract(days=1)
            tomorrow = now.add(days=1)

            with duckdb.connect() as conn:
                yesterday_result = _query_running_summary(
                    conn,
                    temp_file.name,
                    f"DATE(start_date_local) = '{yesterday.to_date_string()}'",
                )
                month_result = _query_running_summary(
                    conn,
                    temp_file.name,
                    (
                        f"start_date_local >= '{now.start_of('month').to_date_string()}' "
                        f"AND start_date_local < '{tomorrow.to_date_string()}'"
                    ),
                )
                year_result = _query_running_summary(
                    conn,
                    temp_file.name,
                    (
                        f"start_date_local >= '{now.start_of('year').to_date_string()}' "
                        f"AND start_date_local < '{tomorrow.to_date_string()}'"
                    ),
                )

        running_info_parts = [
            _format_running_line("昨天", yesterday_result),
            _format_running_line("本月", month_result),
            _format_running_line("今年", year_result),
        ]
        return "Run：\n\n" + "\n".join(running_info_parts)
    except Exception as error:
        print(f"Error getting running data: {error}")
        return ""


def get_day_of_year(now=None):
    current_time = now or _now()
    return current_time.day_of_year


def get_year_progress(now=None):
    current_time = now or _now()
    day_of_year = current_time.day_of_year

    is_leap_year = current_time.year % 4 == 0 and (
        current_time.year % 100 != 0 or current_time.year % 400 == 0
    )
    total_days = 366 if is_leap_year else 365

    progress_percent = (day_of_year / total_days) * 100
    progress_bar_width = 20
    filled_blocks = int((day_of_year / total_days) * progress_bar_width)
    empty_blocks = progress_bar_width - filled_blocks
    progress_bar = "█" * filled_blocks + "░" * empty_blocks

    return f"{progress_bar} {progress_percent:.1f}% ({day_of_year}/{total_days})"


def get_today_get_up_status(issue):
    comments = list(issue.get_comments())
    if not comments:
        return False

    latest_comment = comments[-1]
    latest_day = pendulum.instance(latest_comment.created_at).in_timezone(TIMEZONE)
    return latest_day.date() == _now().date()


def _is_get_up_early(now):
    return now.hour in EARLY_GET_UP_HOURS


def _build_get_up_message_parts(now=None):
    current_time = now or _now()
    return GetUpMessageParts(
        is_get_up_early=_is_get_up_early(current_time),
        day_of_year=get_day_of_year(current_time),
        year_progress=get_year_progress(current_time),
        running_info=get_running_distance(),
        history_today=get_history_today(),
        leetcode=get_daily_leetcode(),
        blog_article=get_blog_article_from_history(),
    )


def _send_telegram_message(body, tele_token, tele_chat_id):
    if not tele_token or not tele_chat_id:
        return

    bot = telebot.TeleBot(tele_token)
    try:
        formatted_body = markdownify(body)
        morning_tag = markdownify(TG_MORNING_TAG).strip()
        telegram_body = f"{formatted_body.rstrip()}\n\n{morning_tag}"
        bot.send_message(
            tele_chat_id,
            telegram_body,
            parse_mode="MarkdownV2",
            disable_notification=True,
        )
    except Exception as error:
        print(str(error))


def make_get_up_message(github_token):
    _ = github_token
    return _build_get_up_message_parts().as_tuple()


def main(
    github_token,
    repo_name,
    tele_token,
    tele_chat_id,
):
    repo = login(github_token).get_repo(repo_name)
    issue = repo.get_issue(GET_UP_ISSUE_NUMBER)
    if get_today_get_up_status(issue):
        print("Today I have recorded the wake up time")
        return

    now = _now()
    if not _is_get_up_early(now):
        print("You wake up late")
        return

    message_parts = _build_get_up_message_parts(now)
    body = message_parts.render(now.to_datetime_string())

    _send_telegram_message(body, tele_token, tele_chat_id)
    issue.create_comment(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("github_token", help="github_token")
    parser.add_argument("repo_name", help="repo_name")
    parser.add_argument(
        "--weather_message", help="weather_message", nargs="?", default="", const=""
    )
    parser.add_argument(
        "--tele_token", help="tele_token", nargs="?", default="", const=""
    )
    parser.add_argument(
        "--tele_chat_id", help="tele_chat_id", nargs="?", default="", const=""
    )
    options = parser.parse_args()
    main(
        options.github_token,
        options.repo_name,
        options.tele_token,
        options.tele_chat_id,
    )
