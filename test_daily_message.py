#!/usr/bin/env python3
"""在线 smoke test：真实请求外部服务，但隔离本地状态文件。"""

import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pendulum

sys.path.insert(0, os.path.dirname(__file__))
import get_up


STATE_FILES = [
    get_up.LEETCODE_EASY_FILE,
    get_up.LEETCODE_USED_FILE,
    get_up.LEETCODE_HOT100_FILE,
    get_up.LEETCODE_HOT100_USED_FILE,
    get_up.BLOG_SITES_USED_FILE,
]


@contextmanager
def isolated_state_files():
    original_script_dir = get_up.SCRIPT_DIR

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        for filename in STATE_FILES:
            source = original_script_dir / filename
            target = temp_dir / filename
            if source.exists():
                shutil.copy2(source, target)
            else:
                target.touch()

        get_up.SCRIPT_DIR = temp_dir
        try:
            yield
        finally:
            get_up.SCRIPT_DIR = original_script_dir


def run_component(label, func):
    print(f"[START] {label}", flush=True)
    started_at = time.time()

    result = func()
    elapsed = time.time() - started_at

    if result:
        preview = str(result).strip().splitlines()[0]
        print(f"[ OK ] {label} ({elapsed:.1f}s): {preview}", flush=True)
    else:
        print(f"[WARN] {label} ({elapsed:.1f}s): 返回空内容", flush=True)
    print(flush=True)
    return result


def main():
    print("=" * 60, flush=True)
    print("在线测试每天生成的起床消息", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)

    with isolated_state_files():
        now = pendulum.now(get_up.TIMEZONE)
        is_get_up_early = 3 <= now.hour <= 9

        day_of_year = run_component("今年的第几天", get_up.get_day_of_year)
        year_progress = run_component("年度进度", get_up.get_year_progress)
        running_info = run_component("跑步信息", get_up.get_running_distance)
        history_today = run_component("历史上的今天", get_up.get_history_today)
        leetcode = run_component("今日 LeetCode", get_up.get_daily_leetcode)
        blog_article = run_component(
            "历史上的今天博客", get_up.get_blog_article_from_history
        )

        get_up_time = now.to_datetime_string()
        body = get_up.GET_UP_MESSAGE_TEMPLATE.format(
            get_up_time=get_up_time,
            day_of_year=day_of_year,
            year_progress=year_progress,
            running_info=running_info,
            history_today=history_today,
            leetcode=leetcode,
            blog_article=blog_article,
        )

    print("=" * 60, flush=True)
    print("完整消息", flush=True)
    print("=" * 60, flush=True)
    print(body, flush=True)
    print(flush=True)
    print("=" * 60, flush=True)
    print(f"是否早起: {'是' if is_get_up_early else '否'}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
