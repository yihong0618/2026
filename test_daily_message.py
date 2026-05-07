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
    get_up.HACKER_NEWS_USED_FILE,
    get_up.CHINESE_CITIES_FILE,
    get_up.CITIES_USED_FILE,
    get_up.CITY_GEOCODE_DB,
]
WORLD_CACHE_FILE = get_up._WORLD_CACHE_FILE
TEST_SEED_ENV = "DAILY_MESSAGE_TEST_SEED"


def _new_test_seed():
    seed = os.environ.get(TEST_SEED_ENV)
    if seed is not None:
        return int(seed)
    return int.from_bytes(os.urandom(8), "big")


@contextmanager
def isolated_state_files():
    original_script_dir = get_up.SCRIPT_DIR
    original_world_cache = get_up._WORLD_CACHE_FILE

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        for filename in STATE_FILES:
            source = original_script_dir / filename
            target = temp_dir / filename
            # 创建父目录（如 data/ 子目录）
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.copy2(source, target)
            else:
                target.touch()

        get_up.SCRIPT_DIR = temp_dir
        get_up._WORLD_CACHE_FILE = temp_dir / "data/ne_110m_countries.gpkg"
        try:
            yield
        finally:
            get_up.SCRIPT_DIR = original_script_dir
            get_up._WORLD_CACHE_FILE = original_world_cache


@contextmanager
def randomized_daily_choices():
    original_daily_rng = get_up._daily_rng
    test_seed = _new_test_seed()

    def test_daily_rng(now, salt=0):
        return original_daily_rng(now, salt + test_seed)

    get_up._daily_rng = test_daily_rng
    try:
        yield test_seed
    finally:
        get_up._daily_rng = original_daily_rng


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

    with isolated_state_files(), randomized_daily_choices() as test_seed:
        print(f"测试随机 seed: {test_seed}", flush=True)
        print(flush=True)

        now = pendulum.now(get_up.TIMEZONE)
        is_get_up_early = 3 <= now.hour <= 9

        day_of_year = run_component("今年的第几天", get_up.get_day_of_year)
        year_progress = run_component("年度进度", get_up.get_year_progress)
        running_info = run_component("跑步信息", get_up.get_running_distance)
        blog_article = run_component(
            "历史上的今天博客", get_up.get_blog_article_from_history
        )
        blog_year = get_up._extract_blog_year_from_text(blog_article)
        history_today = run_component(
            "HN 历史今日",
            lambda: get_up.get_hacker_news_history(blog_year),
        )
        leetcode = run_component("今日 LeetCode", get_up.get_daily_leetcode)
        city_info_result = run_component("今日城市", get_up.get_random_city)
        city_info = city_info_result[0] if city_info_result else ""
        city_name = city_info_result[1] if city_info_result else ""
        poster_path = ""
        map_path = ""
        if city_name:
            poster_path = run_component(
                "城市海报",
                lambda: get_up._generate_city_poster(city_name),
            )
            map_path = run_component(
                "城市地图",
                lambda: get_up._generate_cities_map(city_name),
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
            city_info=city_info,
        )

    print("=" * 60, flush=True)
    print("完整消息", flush=True)
    print("=" * 60, flush=True)
    print(body, flush=True)
    print(flush=True)
    print("=" * 60, flush=True)
    print(f"是否早起: {'是' if is_get_up_early else '否'}", flush=True)
    if poster_path:
        print(f"城市海报: {poster_path}", flush=True)
    if map_path:
        print(f"城市地图: {map_path}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
