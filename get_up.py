import argparse
import re
import tempfile

import duckdb
import pendulum
import requests
import telebot
from github import Github
from telegramify_markdown import markdownify

# 1 real get up #5 for test
GET_UP_ISSUE_NUMBER = 1
GET_UP_MESSAGE_TEMPLATE = """今天的起床时间是--{get_up_time}。

起床啦。

今天是今年的第 {day_of_year} 天。

{year_progress}

{github_activity}

{running_info}

今天的一句诗:

{sentence}
"""
# in 2024-06-15 this one ssl error
SENTENCE_API = "https://v1.jinrishici.com/all"

DEFAULT_SENTENCE = "赏花归去马如飞\r\n去马如飞酒力微\r\n酒力微醒时已暮\r\n醒时已暮赏花归\r\n"
TIMEZONE = "Asia/Shanghai"


def login(token):
    return Github(token)


def get_one_sentence():
    try:
        r = requests.get(SENTENCE_API)
        if r.ok:
            return r.json()["content"]
        return DEFAULT_SENTENCE
    except Exception:
        print("get SENTENCE_API wrong")
        return DEFAULT_SENTENCE


def _get_repo_name_from_url(url):
    """从仓库 URL 中提取仓库名称"""
    return "/".join(url.split("/")[-2:])


def _make_api_request(url, headers, params=None):
    """统一的 API 请求函数"""
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json(), None
        else:
            return None, f"API 请求失败: {response.status_code}"
    except Exception as e:
        return None, f"请求出错: {e}"


def _process_search_items(items, username, item_type):
    """处理搜索结果（PR 或 Issue）"""
    activities = []
    action_text = "创建了 PR" if item_type == "pr" else "创建了 Issue"

    for item in items:
        if item["user"]["login"] == username:
            repo_name = _get_repo_name_from_url(item["repository_url"])
            title = item["title"]
            url = item["html_url"]
            activities.append(f"{action_text}: [{title}]({url}) ({repo_name})")

    return activities


def _process_events(events, yesterday_start, yesterday_end):
    """处理用户事件"""
    activities = []

    for event in events[:100]:
        event_created = pendulum.parse(event["created_at"])

        if event_created < yesterday_start:
            break

        if not (yesterday_start <= event_created <= yesterday_end):
            continue

        if not event.get("public", True):
            continue

        event_type = event["type"]
        repo_name = event["repo"]["name"]

        if event_type == "PullRequestEvent":
            action = event["payload"].get("action")
            if action == "merged":
                pr_data = event["payload"]["pull_request"]
                activities.append(
                    f"合并了 PR: [{pr_data['title']}]({pr_data['html_url']}) ({repo_name})"
                )
        elif event_type == "IssuesEvent":
            action = event["payload"].get("action")
            if action == "closed":
                issue_data = event["payload"]["issue"]
                activities.append(
                    f"关闭了 Issue: [{issue_data['title']}]({issue_data['html_url']}) ({repo_name})"
                )
        elif event_type == "WatchEvent":
            action = event["payload"].get("action")
            if action == "started":
                repo_url = f"https://github.com/{repo_name}"
                activities.append(f"Star 了项目: [{repo_name}]({repo_url})")

    return activities


def get_yesterday_github_activity(github_token=None, username="yihong0618"):
    """获取昨天的 GitHub 活动"""
    try:
        # 时间设置
        yesterday = pendulum.now(TIMEZONE).subtract(days=1)
        yesterday_start = yesterday.start_of("day").in_timezone("UTC")
        yesterday_end = yesterday.end_of("day").in_timezone("UTC")
        yesterday_date = yesterday.format("YYYY-MM-DD")

        # 请求头设置
        headers = {}
        if github_token:
            headers.update(
                {
                    "Authorization": f"token {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                }
            )

        activities = []

        # 获取创建的 PR
        search_url = "https://api.github.com/search/issues"
        pr_query = f"is:pr is:public author:{username} created:{yesterday_date}"
        print(f"PR 搜索查询: {pr_query}")
        pr_data, error = _make_api_request(
            search_url,
            headers,
            {
                "q": pr_query,
                "per_page": 100,
            },
        )
        if pr_data:
            pr_items = pr_data.get("items", [])
            print(f"找到 {len(pr_items)} 个 PR")
            pr_activities = _process_search_items(pr_items, username, "pr")
            print(f"处理后的 PR 活动: {pr_activities}")
            activities.extend(pr_activities)
        elif error:
            print(f"搜索 PR 时出错: {error}")

        # 获取创建的 Issue
        issue_query = f"is:issue is:public author:{username} created:{yesterday_date}"
        print(f"Issue 搜索查询: {issue_query}")
        issue_data, error = _make_api_request(
            search_url,
            headers,
            {
                "q": issue_query,
                "per_page": 100,
            },
        )
        if issue_data:
            issue_items = issue_data.get("items", [])
            print(f"找到 {len(issue_items)} 个 Issue")
            issue_activities = _process_search_items(issue_items, username, "issue")
            print(f"处理后的 Issue 活动: {issue_activities}")
            activities.extend(issue_activities)
        elif error:
            print(f"搜索 Issue 时出错: {error}")

        # 获取其他事件（合并、关闭、Star 等）
        # 检查多页事件，因为 Star 事件可能不在第一页
        events_url = f"https://api.github.com/users/{username}/events"
        all_activities = []

        for page in range(1, 4):  # 检查前3页，总共约90个事件
            page_params = {"page": page, "per_page": 30}
            events_data, error = _make_api_request(events_url, headers, page_params)

            if error:
                print(f"获取第 {page} 页 Events 时出错: {error}")
                continue

            if not events_data:
                break  # 没有更多事件了

            page_activities = _process_events(
                events_data, yesterday_start, yesterday_end
            )
            all_activities.extend(page_activities)

            # 如果这一页事件数少于30，说明已经到底了
            if len(events_data) < 30:
                break

        activities.extend(all_activities)

        # 返回结果
        print(f"所有活动总数: {len(activities)}")
        print(f"所有活动: {activities}")
        if activities:
            # 去重并限制数量
            unique_activities = list(dict.fromkeys(activities))
            print(f"去重后活动数: {len(unique_activities)}")
            result = "GitHub：\n\n" + "\n".join(
                f"• {activity}" for activity in unique_activities[:15]
            )
            print(f"最终结果:\n{result}")
            return result

        return ""

    except Exception as e:
        print(f"Error getting GitHub activity: {e}")
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
    """获取今年的进度条"""
    now = pendulum.now(TIMEZONE)
    day_of_year = now.day_of_year

    # 判断是否为闰年
    is_leap_year = now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0)
    total_days = 366 if is_leap_year else 365

    # 计算进度百分比
    progress_percent = (day_of_year / total_days) * 100

    # 生成进度条 (20个字符宽度)
    progress_bar_width = 20
    filled_blocks = int((day_of_year / total_days) * progress_bar_width)
    empty_blocks = progress_bar_width - filled_blocks

    progress_bar = "█" * filled_blocks + "░" * empty_blocks

    return f"{progress_bar} {progress_percent:.1f}% ({day_of_year}/{total_days})"


def get_today_get_up_status(issue):
    comments = list(issue.get_comments())
    if not comments:
        return False, []
    latest_comment = comments[-1]
    now = pendulum.now(TIMEZONE)
    latest_day = pendulum.instance(latest_comment.created_at).in_timezone(
        "Asia/Shanghai"
    )
    is_today = (latest_day.day == now.day) and (latest_day.month == now.month)
    return is_today


def make_get_up_message(github_token):
    sentence = get_one_sentence()
    now = pendulum.now(TIMEZONE)
    # 3 - 7 means early for me
    ###  make it to 9 in 2024.10.15 for maybe I forgot it ###
    is_get_up_early = 3 <= now.hour <= 9
    try:
        sentence = get_one_sentence()
        print(f"Second: {sentence}")
    except Exception as e:
        print(str(e))

    day_of_year = get_day_of_year()
    year_progress = get_year_progress()
    github_activity = get_yesterday_github_activity(github_token)
    running_info = get_running_distance()

    return (
        sentence,
        is_get_up_early,
        day_of_year,
        year_progress,
        github_activity,
        running_info,
    )


def remove_github_links(text):
    # 移除所有 GitHub 链接，保留链接文本
    pattern = r"\[([^\]]+)\]\(https://github\.com/[^\)]+\)"
    cleaned_text = re.sub(pattern, r"\1", text)
    return cleaned_text


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
        github_activity,
        running_info,
    ) = make_get_up_message(github_token)
    get_up_time = pendulum.now(TIMEZONE).to_datetime_string()

    body = GET_UP_MESSAGE_TEMPLATE.format(
        get_up_time=get_up_time,
        sentence=sentence,
        day_of_year=day_of_year,
        year_progress=year_progress,
        github_activity=github_activity,
        running_info=running_info,
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

        cleaned_body = remove_github_links(body)
        issue.create_comment(cleaned_body)
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
