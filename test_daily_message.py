#!/usr/bin/env python3
"""测试每天生成的完整起床消息"""

import os
import sys
import pendulum

# 导入 get_up.py 中的函数
sys.path.insert(0, os.path.dirname(__file__))
from get_up import (
    GET_UP_MESSAGE_TEMPLATE,
    TIMEZONE,
    get_day_of_year,
    get_year_progress,
    get_running_distance,
    get_history_today,
    get_daily_leetcode,
    get_blog_article_from_history,
    make_get_up_message,
)


def test_full_message():
    """测试完整的起床消息"""
    print("=" * 60)
    print("测试每天生成的起床消息")
    print("=" * 60)
    print()

    # 使用 None 作为 github_token（因为我们已经移除了 GitHub 动态）
    (
        is_get_up_early,
        day_of_year,
        year_progress,
        running_info,
        history_today,
        leetcode,
        blog_article,
    ) = make_get_up_message(None)

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

    print(body)
    print()
    print("=" * 60)
    print(f"是否早起: {'是' if is_get_up_early else '否'}")
    print("=" * 60)


def test_individual_components():
    """测试各个组件"""
    print("\n" + "=" * 60)
    print("测试各个组件")
    print("=" * 60)
    print()

    print("📅 今年的第几天:")
    print(f"   {get_day_of_year()}")
    print()

    print("📊 年度进度:")
    print(f"   {get_year_progress()}")
    print()

    print("🏃 跑步信息:")
    running = get_running_distance()
    if running:
        print(f"   {running}")
    else:
        print("   无法获取跑步数据")
    print()

    print("📜 历史上的今天:")
    history = get_history_today()
    if history:
        print(f"   {history}")
    else:
        print("   无法获取历史事件")
    print()

    print("💻 今日 LeetCode:")
    leetcode = get_daily_leetcode()
    if leetcode:
        print(f"   {leetcode}")
    else:
        print("   无法获取 LeetCode 题目")
    print()

    print("📖 历史上的今天博客:")
    blog = get_blog_article_from_history()
    if blog:
        print(f"   {blog}")
    else:
        print("   无法获取博客文章")
    print()


if __name__ == "__main__":
    test_full_message()
    test_individual_components()
