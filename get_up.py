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

{blog_article}
"""

# LeetCode 题目文件路径
LEETCODE_EASY_FILE = "leetcode_easy.txt"
LEETCODE_USED_FILE = "leetcode_used.txt"
LEETCODE_HOT100_FILE = "leetcode_hot100.txt"
LEETCODE_HOT100_USED_FILE = "leetcode_hot100_used.txt"
# 博客文章已使用网站记录文件
BLOG_SITES_USED_FILE = "blog_sites_used.txt"
TIMEZONE = "Asia/Shanghai"

BIRTH_YEAR = 1989  # change it to your birth year


def login(token):
    return Github(auth=Auth.Token(token))


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


def _pick_from_pool(problem_file, used_file, now):
    """从题库文件中随机选一道未做过的题，返回 (problem_id, title, slug, difficulty) 或 None"""
    used_slugs = set()
    if os.path.exists(used_file):
        with open(used_file, "r") as f:
            used_slugs = set(line.strip() for line in f if line.strip())

    if not os.path.exists(problem_file):
        return None

    with open(problem_file, "r") as f:
        problems = [line.strip() for line in f if line.strip()]

    available = []
    for p in problems:
        parts = p.split("|")
        if len(parts) >= 3:
            slug = parts[2]
            if slug not in used_slugs:
                available.append(parts)

    if not available:
        return None

    day_seed = now.year * 1000 + now.day_of_year
    random.seed(day_seed)
    selected = random.choice(available)
    random.seed()

    problem_id = selected[0]
    title = selected[1]
    slug = selected[2]
    difficulty = selected[3].upper() if len(selected) >= 4 else "EASY"

    # 记录已使用
    with open(used_file, "a") as f:
        f.write(f"{slug}\n")

    return (problem_id, title, slug, difficulty)


def get_daily_leetcode():
    try:
        script_dir = _get_script_dir()
        easy_file = os.path.join(script_dir, LEETCODE_EASY_FILE)
        easy_used_file = os.path.join(script_dir, LEETCODE_USED_FILE)
        hot100_file = os.path.join(script_dir, LEETCODE_HOT100_FILE)
        hot100_used_file = os.path.join(script_dir, LEETCODE_HOT100_USED_FILE)

        now = pendulum.now(TIMEZONE)

        # 先加载 easy 的 used_slugs 用于每日一题判断
        easy_used_slugs = set()
        if os.path.exists(easy_used_file):
            with open(easy_used_file, "r") as f:
                easy_used_slugs = set(line.strip() for line in f if line.strip())

        # 检查官方每日一题（仅 EASY）
        daily_question = _get_leetcode_daily_question()
        use_daily = False
        if daily_question:
            if (
                daily_question["difficulty"] == "EASY"
                and daily_question["slug"] not in easy_used_slugs
            ):
                use_daily = True
                problem_id = daily_question["id"]
                title = daily_question["title"]
                slug = daily_question["slug"]
                with open(easy_used_file, "a") as f:
                    f.write(f"{slug}\n")

        results = []

        if use_daily:
            url = f"https://leetcode.cn/problems/{slug}/"
            results.append(
                f"今日官方每日一题！ 今日 LeetCode 🟢 简单题：\n\n[{problem_id}. {title}]({url})"
            )

        # 1/2 概率随机一道热题 100（easy + medium）
        day_seed = now.year * 1000 + now.day_of_year
        random.seed(day_seed + 42)  # 不同 seed 避免和 easy 选题冲突
        do_hot100 = random.random() < 0.5
        random.seed()

        if do_hot100:
            hot100_result = _pick_from_pool(hot100_file, hot100_used_file, now)
            if hot100_result:
                pid, ptitle, pslug, pdiff = hot100_result
                diff_map = {
                    "EASY": ("简单", "🟢"),
                    "MEDIUM": ("中等", "🟡"),
                }
                diff_label, diff_emoji = diff_map.get(pdiff, ("中等", "🟡"))
                url = f"https://leetcode.cn/problems/{pslug}/"
                results.append(
                    f"今日 LeetCode 热题 100 {diff_emoji} {diff_label}题：\n\n[{pid}. {ptitle}]({url})"
                )
            else:
                results.append("热题 100 都做完啦！🎉")

        # 如果没有每日一题、也没有随到热题 100，就从 easy 池子里选
        if not results:
            easy_result = _pick_from_pool(easy_file, easy_used_file, now)
            if easy_result:
                pid, ptitle, pslug, _ = easy_result
                url = f"https://leetcode.cn/problems/{pslug}/"
                results.append(f"今日 LeetCode 🟢 简单题：\n\n[{pid}. {ptitle}]({url})")
            else:
                results.append("今日 LeetCode：所有简单题都做完啦！🎉")

        return "\n\n".join(results)

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


def _extract_domain(url):
    """从 URL 中提取域名"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _load_used_sites():
    """加载已使用的网站域名列表"""
    script_dir = _get_script_dir()
    used_file = os.path.join(script_dir, BLOG_SITES_USED_FILE)
    if os.path.exists(used_file):
        with open(used_file, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def _save_used_site(domain):
    """保存已使用的网站域名"""
    script_dir = _get_script_dir()
    used_file = os.path.join(script_dir, BLOG_SITES_USED_FILE)
    with open(used_file, "a") as f:
        f.write(f"{domain}\n")


def _check_link_available(url, timeout=10):
    """验证链接是否可用"""
    if not url:
        return False
    try:
        # 使用 HEAD 请求快速检查链接
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        # 2xx 状态码认为是可用的
        return 200 <= response.status_code < 300
    except requests.exceptions.RequestException:
        # 超时或请求失败，认为不可用
        return False


def _extract_text_snippet(content, max_length=200):
    """从 HTML/Markdown 内容中提取纯文本摘要"""
    import re

    if not content:
        return ""

    # 移除 HTML 标签
    text = re.sub(r"<[^>]+>", "", content)
    # 移除 Markdown 链接 [text](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 移除 Markdown 标题标记
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    # 移除其他 Markdown 标记
    text = re.sub(r"[*`_~]", "", text)
    # 移除多余空白
    text = " ".join(text.split())

    # 截取合适长度，尽量在句子边界截断
    if len(text) <= max_length:
        return text

    # 尝试在句子边界截断
    truncated = text[:max_length]
    # 查找最后一个句号、问号或感叹号
    for punct in ["。", "？", "！", ". ", "? ", "! "]:
        last_pos = truncated.rfind(punct)
        if last_pos > max_length * 0.5:  # 确保至少保留一半内容
            return text[: last_pos + len(punct)].strip()

    # 如果没有找到合适的句子边界，在空格处截断
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return text[:last_space] + "..."

    return truncated + "..."


def get_blog_article_from_history():
    """
    获取历史上今天的博客文章 (2014-2025年)

    从 saveweb.org API 随机获取一篇历史上今天发布的博客文章
    会记录已使用的网站域名，尽量返回不同网站的文章
    如果当天没有文章，则随机搜索其他日期
    """
    try:
        used_sites = _load_used_sites()
        now = pendulum.now(TIMEZONE)
        current_year = now.year

        # 尝试当前日期，范围 2014-2025
        month = now.month
        day = now.day

        all_articles = []

        # 搜索 2014-2025 年每年这一天的文章
        for year in range(2014, 2026):
            try:
                # 构建 API URL
                date_str = f"{year}-{month}-{day}"
                api_url = f"https://search-api.saveweb.org/api/search?q=(date%20%3D%20sec({date_str}))&f=false&p=0&h=true"

                response = requests.get(api_url, timeout=10)
                if response.ok:
                    data = response.json()
                    hits = data.get("hits", [])
                    for hit in hits:
                        link = hit.get("link", "")
                        if link:
                            domain = _extract_domain(link)
                            hit["domain"] = domain
                            all_articles.append(hit)
            except Exception as e:
                print(f"Error fetching articles for {year}-{month}-{day}: {e}")
                continue

        if not all_articles:
            # 如果当天没有文章，随机选一天搜索
            print("No articles found for today, trying random date...")
            for _ in range(5):  # 尝试5次随机日期
                try:
                    random_year = random.randint(2014, 2025)
                    random_month = random.randint(1, 12)
                    random_day = random.randint(1, 28)
                    date_str = f"{random_year}-{random_month}-{random_day}"
                    api_url = f"https://search-api.saveweb.org/api/search?q=(date%20%3D%20sec({date_str}))&f=false&p=0&h=true"

                    response = requests.get(api_url, timeout=10)
                    if response.ok:
                        data = response.json()
                        hits = data.get("hits", [])
                        for hit in hits:
                            link = hit.get("link", "")
                            if link:
                                domain = _extract_domain(link)
                                hit["domain"] = domain
                                all_articles.append(hit)
                        if all_articles:
                            break
                except Exception:
                    continue

        if not all_articles:
            return ""

        # 优先选择未使用过的网站
        unused_articles = [
            a for a in all_articles if a.get("domain") and a["domain"] not in used_sites
        ]

        # 设置随机种子（基于日期）以确保同一天返回相同结果
        day_seed = now.year * 1000 + now.day_of_year
        random.seed(day_seed)

        # 尝试选择一个可用的链接（最多尝试10次）
        selected = None
        checked_articles = []
        max_attempts = min(10, len(all_articles))

        for _ in range(max_attempts):
            # 优先从未使用过的文章中选择
            if unused_articles:
                candidate = random.choice(unused_articles)
                unused_articles.remove(candidate)
            elif all_articles:
                candidate = random.choice(all_articles)
                all_articles.remove(candidate)
            else:
                break

            link = candidate.get("link", "")
            # 验证链接是否可用
            if link and _check_link_available(link):
                selected = candidate
                break
            else:
                # 记录已检查的文章，如果都不可用，最后选一个用
                checked_articles.append(candidate)

        random.seed()  # 重置随机种子

        # 如果所有链接都不可用，从已检查的文章中选第一个（会返回失效链接，但总比没有好）
        if selected is None and checked_articles:
            selected = checked_articles[0]

        if selected is None:
            return ""

        # 记录使用的网站
        domain = selected.get("domain", "")
        if domain and domain not in used_sites:
            _save_used_site(domain)

        title = selected.get("title", "未知标题")
        link = selected.get("link", "")

        # 格式化日期
        article_date = ""
        if selected.get("date"):
            try:
                article_ts = int(selected["date"])
                article_dt = pendulum.from_timestamp(article_ts)
                article_date = article_dt.format("YYYY-MM-DD")
            except Exception:
                pass

        # 计算文章发布距今多少年（类似年龄计算）
        years_ago = None
        if article_date:
            try:
                article_year = int(article_date.split("-")[0])
                years_ago = current_year - article_year
            except Exception:
                pass

        # 提取内容摘要作为引用
        content = selected.get("content", "")
        snippet = _extract_text_snippet(content, max_length=200)

        # 构建结果
        result_lines = []

        # 标题：来自 xx 年前的博客
        if years_ago is not None and years_ago >= 0:
            header = f"**来自 {years_ago} 年前的博客** ({article_date})"
        else:
            header = "**历史上的博客**"

        if link:
            result_lines.append(f"{header}：[{title}]({link})")
        else:
            result_lines.append(f"{header}：{title}")

        # 添加引用（如果有内容）
        if snippet:
            result_lines.append("")
            # 将摘要转换为 Markdown 引用格式
            quoted_lines = [f"> {line}" for line in snippet.split("\n") if line.strip()]
            result_lines.extend(quoted_lines)

        return "\n".join(result_lines)

    except Exception as e:
        print(f"Error getting blog article: {e}")
        return ""


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
    now = pendulum.now(TIMEZONE)
    # 3 - 9 means early for me
    # 3 means 我喝多了起来上厕所
    is_get_up_early = 3 <= now.hour <= 9

    day_of_year = get_day_of_year()
    year_progress = get_year_progress()
    running_info = get_running_distance()
    history_today = get_history_today()
    leetcode = get_daily_leetcode()
    blog_article = get_blog_article_from_history()

    return (
        is_get_up_early,
        day_of_year,
        year_progress,
        running_info,
        history_today,
        leetcode,
        blog_article,
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
        is_get_up_early,
        day_of_year,
        year_progress,
        running_info,
        history_today,
        leetcode,
        blog_article,
    ) = make_get_up_message(github_token)
    get_up_time = pendulum.now(TIMEZONE).to_datetime_string()

    body = GET_UP_MESSAGE_TEMPLATE.format(
        get_up_time=get_up_time,
        day_of_year=day_of_year,
        year_progress=year_progress,
        running_info=running_info,
        history_today=history_today,
        leetcode=leetcode,
        blog_article=blog_article,
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
