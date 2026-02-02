import argparse
import random
import os
import tempfile

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

今天的一首诗:

{sentence}
"""

# LeetCode 题目文件路径
LEETCODE_EASY_FILE = "leetcode_easy.txt"
LEETCODE_USED_FILE = "leetcode_used.txt"
# 使用 v2 API 获取完整诗词
SENTENCE_API = "https://v2.jinrishici.com/one.json"

DEFAULT_SENTENCE = """《苦笋》
赏花归去马如飞，
去马如飞酒力微，
酒力微醒时已暮，
醒时已暮赏花归。

—— 宋·苏轼"""
TIMEZONE = "Asia/Shanghai"

BIRTH_YEAR = 1989  # change it to your birth year


def login(token):
    return Github(auth=Auth.Token(token))


def get_one_sentence():
    """获取今天的一首诗

    使用今日诗词 v2 API 获取完整的诗词内容
    返回格式：《诗名》\n诗词内容\n\n—— 朝代·作者
    """
    try:
        r = requests.get(SENTENCE_API, timeout=10)
        if r.ok:
            data = r.json()

            # 获取诗词来源信息
            origin = data.get("data", {}).get("origin", {})
            title = origin.get("title", "")
            dynasty = origin.get("dynasty", "")
            author = origin.get("author", "")
            content_list = origin.get("content", [])

            if content_list and title and author:
                # 将诗词内容数组合并为字符串（每句一行）
                content = "\n".join(content_list)
                # 格式化输出：《诗名》\n内容\n\n—— 朝代·作者
                poem = f"《{title}》\n{content}\n\n—— {dynasty}·{author}"
                return poem

        return DEFAULT_SENTENCE
    except Exception as e:
        print(f"get SENTENCE_API wrong: {e}")
        return DEFAULT_SENTENCE


def _get_script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _get_leetcode_daily_question():
    """获取 LeetCode CN 每日一题

    Returns:
        dict: 包含 id, title, slug, difficulty 的字典，失败返回 None
    """
    try:
        url = "https://leetcode.cn/graphql/"
        headers = {"Content-Type": "application/json"}
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
        response = requests.post(
            url, json={"query": query}, headers=headers, timeout=10
        )
        if response.ok:
            data = response.json()
            records = data.get("data", {}).get("todayRecord", [])
            if records:
                q = records[0].get("question", {})
                if q and not q.get("isPaidOnly", False):
                    return {
                        "id": q.get("questionFrontendId", ""),
                        "title": q.get("title", ""),
                        "slug": q.get("titleSlug", ""),
                        "difficulty": q.get(
                            "difficulty", ""
                        ).upper(),  # EASY, MEDIUM, HARD
                    }
        return None
    except Exception as e:
        print(f"Error getting daily question: {e}")
        return None


def get_daily_leetcode():
    try:
        script_dir = _get_script_dir()
        easy_file = os.path.join(script_dir, LEETCODE_EASY_FILE)
        used_file = os.path.join(script_dir, LEETCODE_USED_FILE)

        now = pendulum.now(TIMEZONE)

        used_slugs = set()
        if os.path.exists(used_file):
            with open(used_file, "r") as f:
                used_slugs = set(line.strip() for line in f if line.strip())

        target_difficulty = "EASY"
        difficulty = "简单"
        difficulty_emoji = "🟢"
        hint = ""
        problem_file = easy_file

        daily_question = _get_leetcode_daily_question()
        use_daily = False
        if daily_question:
            if (
                daily_question["difficulty"] == target_difficulty
                and daily_question["slug"] not in used_slugs
            ):
                use_daily = True
                problem_id = daily_question["id"]
                title = daily_question["title"]
                slug = daily_question["slug"]
                hint = "今日官方每日一题！" + (f" {hint}" if hint else "")

        if not use_daily:
            if not os.path.exists(problem_file):
                return "今日 LeetCode：题库文件不存在，请运行 fetch_leetcode.py 获取题目"

            with open(problem_file, "r") as f:
                problems = [line.strip() for line in f if line.strip()]

            available = []
            for p in problems:
                parts = p.split("|")
                if len(parts) >= 3:
                    slug = parts[2]
                    if slug not in used_slugs:
                        available.append(p)

            if not available:
                return f"今日 LeetCode：所有{difficulty}题都做完啦！🎉"

            day_seed = now.year * 1000 + now.day_of_year
            random.seed(day_seed)
            selected = random.choice(available)
            random.seed()

            parts = selected.split("|")
            problem_id = parts[0]
            title = parts[1]
            slug = parts[2]

        with open(used_file, "a") as f:
            f.write(f"{slug}\n")

        url = f"https://leetcode.cn/problems/{slug}/"

        header = f"今日 LeetCode {difficulty_emoji} {difficulty}题"
        if hint:
            header = f"{hint} {header}"

        return f"""{header}：

[{problem_id}. {title}]({url})"""

    except Exception as e:
        print(f"Error getting daily leetcode: {e}")
        return ""


def get_history_today(birth_year=BIRTH_YEAR, limit=2):
    try:
        now = pendulum.now(TIMEZONE)
        month = now.format("MM")
        day = now.format("DD")

        url = f"https://api.wikimedia.org/feed/v1/wikipedia/zh/onthisday/events/{month}/{day}"

        headers = {"User-Agent": "GetUpBot/1.0 (https://github.com/yihong0618/2026)"}

        response = requests.get(url, headers=headers, timeout=10)

        if not response.ok:
            print(f"Failed to get history today: {response.status_code}")
            return ""

        data = response.json()
        events = data.get("events", [])

        if not events:
            return ""

        current_year = now.year
        filtered_events = [
            event
            for event in events
            if "year" in event and 1989 <= event["year"] <= current_year
        ]

        if not filtered_events:
            filtered_events = [e for e in events if "year" in e]

        if not filtered_events:
            return ""

        selected_events = random.sample(
            filtered_events, min(limit, len(filtered_events))
        )
        selected_events.sort(key=lambda x: x.get("year", 0), reverse=True)

        result_lines = ["历史上的今天：\n"]

        for event in selected_events:
            year = event.get("year")
            text = event.get("text", "")

            pages = event.get("pages", [])
            wiki_url = ""
            if pages and len(pages) > 0:
                content_urls = pages[0].get("content_urls", {})
                desktop = content_urls.get("desktop", {})
                wiki_url = desktop.get("page", "")

            if year and year >= birth_year:
                age = year - birth_year
                age_text = f"（那年我 {age} 岁）"
            elif year and year < birth_year:
                years_before = birth_year - year
                age_text = f"（我出生前 {years_before} 年）"
            else:
                age_text = ""

            text = text.replace("\n", " ").strip()
            text = convert(text, "zh-cn")  # 繁体转简体

            if wiki_url:
                result_lines.append(f"• {year}年：[{text}]({wiki_url}) {age_text}")
            else:
                result_lines.append(f"• {year}年：{text} {age_text}")

        return "\n".join(result_lines)

    except Exception:
        return "fail to get it"


def get_running_distance():
    try:
        url = "https://github.com/yihong0618/run/raw/refs/heads/master/run_page/data.parquet"
        response = requests.get(url)

        if not response.ok:
            return ""

        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(response.content)
            temp_file.flush()

            with duckdb.connect() as conn:
                now = pendulum.now(TIMEZONE)
                yesterday = now.subtract(days=1)
                month_start = now.start_of("month")
                year_start = now.start_of("year")

                yesterday_query = f"""
                SELECT 
                    COUNT(*) as count,
                    ROUND(SUM(distance)/1000, 2) as total_km
                FROM read_parquet('{temp_file.name}')
                WHERE DATE(start_date_local) = '{yesterday.to_date_string()}'
                """

                month_query = f"""
                SELECT 
                    COUNT(*) as count,
                    ROUND(SUM(distance)/1000, 2) as total_km
                FROM read_parquet('{temp_file.name}')
                WHERE start_date_local >= '{month_start.to_date_string()}' 
                    AND start_date_local < '{now.add(days=1).to_date_string()}'
                """

                year_query = f"""
                SELECT 
                    COUNT(*) as count,
                    ROUND(SUM(distance)/1000, 2) as total_km
                FROM read_parquet('{temp_file.name}')
                WHERE start_date_local >= '{year_start.to_date_string()}' 
                    AND start_date_local < '{now.add(days=1).to_date_string()}'
                """

                yesterday_result = conn.execute(yesterday_query).fetchone()
                month_result = conn.execute(month_query).fetchone()
                year_result = conn.execute(year_query).fetchone()

            running_info_parts = []

            if yesterday_result and yesterday_result[0] > 0:
                running_info_parts.append(f"• 昨天跑了 {yesterday_result[1]} 公里")
            else:
                running_info_parts.append("• 昨天没跑")

            if month_result and month_result[0] > 0:
                running_info_parts.append(f"• 本月跑了 {month_result[1]} 公里")
            else:
                running_info_parts.append("• 本月没跑")

            if year_result and year_result[0] > 0:
                running_info_parts.append(f"• 今年跑了 {year_result[1]} 公里")
            else:
                running_info_parts.append("• 今年没跑")

            return "Run：\n\n" + "\n".join(running_info_parts)

    except Exception as e:
        print(f"Error getting running data: {e}")
        return ""

    return ""


def get_day_of_year():
    now = pendulum.now(TIMEZONE)
    return now.day_of_year


def get_year_progress():
    now = pendulum.now(TIMEZONE)
    day_of_year = now.day_of_year

    is_leap_year = now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0)
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
    now = pendulum.now(TIMEZONE)
    latest_day = pendulum.instance(latest_comment.created_at).in_timezone(
        "Asia/Shanghai"
    )
    return latest_day.date() == now.date()


def make_get_up_message(github_token):
    sentence = get_one_sentence()
    now = pendulum.now(TIMEZONE)
    # 3 - 9 means early for me
    # 3 means 我喝多了起来上厕所
    is_get_up_early = 3 <= now.hour <= 9
    try:
        sentence = get_one_sentence()
        print(f"Second: {sentence}")
    except Exception as e:
        print(str(e))

    day_of_year = get_day_of_year()
    year_progress = get_year_progress()
    running_info = get_running_distance()
    history_today = get_history_today()
    leetcode = get_daily_leetcode()

    return (
        sentence,
        is_get_up_early,
        day_of_year,
        year_progress,
        running_info,
        history_today,
        leetcode,
    )


def main(
    github_token,
    repo_name,
    tele_token,
    tele_chat_id,
):
    u = login(github_token)
    repo = u.get_repo(repo_name)
    issue = repo.get_issue(GET_UP_ISSUE_NUMBER)
    is_today = get_today_get_up_status(issue)
    if is_today:
        print("Today I have recorded the wake up time")
        return

    (
        sentence,
        is_get_up_early,
        day_of_year,
        year_progress,
        running_info,
        history_today,
        leetcode,
    ) = make_get_up_message(github_token)
    get_up_time = pendulum.now(TIMEZONE).to_datetime_string()

    body = GET_UP_MESSAGE_TEMPLATE.format(
        get_up_time=get_up_time,
        sentence=sentence,
        day_of_year=day_of_year,
        year_progress=year_progress,
        running_info=running_info,
        history_today=history_today,
        leetcode=leetcode,
    )

    if is_get_up_early:
        if tele_token and tele_chat_id:
            bot = telebot.TeleBot(tele_token)
            try:
                formatted_body = markdownify(body)
                bot.send_message(
                    tele_chat_id,
                    formatted_body,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
            except Exception as e:
                print(str(e))
        issue.create_comment(body)
    else:
        print("You wake up late")


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
