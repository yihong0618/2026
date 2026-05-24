import argparse
import html
import random
import re
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from urllib.parse import quote, urlparse

import duckdb
import pendulum
import requests
import telebot
from github import Auth, Github
from telegramify_markdown import markdownify
from terraink_py.api import generate_poster
from terraink_py.models import PosterRequest
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

{city_info}

{blog_article}
"""
TG_MORNING_TAG = "#morning"
TELEGRAM_CAPTION_LIMIT = 1024
CITY_POSTERS_DIR = "city_posters"

LEETCODE_EASY_FILE = "data/leetcode_easy.txt"
LEETCODE_USED_FILE = "data/leetcode_used.txt"
LEETCODE_HOT100_FILE = "data/leetcode_hot100.txt"
LEETCODE_HOT100_USED_FILE = "data/leetcode_hot100_used.txt"
BLOG_SITES_USED_FILE = "data/blog_sites_used.txt"
HACKER_NEWS_USED_FILE = "data/hacker_news_used.txt"
CLASSIC_MEDIA_USED_FILE = "data/classic_media_used.txt"
CHINESE_CITIES_FILE = "data/chinese_cities.txt"
CITIES_USED_FILE = "data/cities_used.txt"

CITY_WIKI_BASE_URL = "https://zh.wikipedia.org/wiki/{city}"
CITY_RANDOM_SALT = 77

TIMEZONE = "Asia/Shanghai"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = PROJECT_ROOT
SCRIPT_DIR = PROJECT_ROOT
LOCAL_CJK_FONT_FILE_CANDIDATES = (
    "fonts/AlibabaPuHuiTi-Regular.ttf",
    "fonts/AlibabaPuHuiTi-Bold.ttf",
    "fonts/ZenMaruGothic-Regular.ttf",
    "fonts/ZenMaruGothic-Bold.ttf",
)

LEETCODE_BASE_URL = "https://leetcode.cn/problems/{slug}/"
LEETCODE_DAILY_URL = "https://leetcode.cn/graphql/"
RUN_DATA_URL = (
    "https://github.com/yihong0618/run/raw/refs/heads/master/run_page/data.parquet"
)
WIKIMEDIA_USER_AGENT = "GetUpBot/1.0 (https://github.com/yihong0618/2026)"
HACKER_NEWS_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HACKER_NEWS_ITEM_URL = "https://news.ycombinator.com/item?id={object_id}"
HACKER_NEWS_START_YEAR = 2007
HACKER_NEWS_STORIES_PER_PAGE = 1000
HACKER_NEWS_TOP_LIMIT = 10
INTERNET_ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
INTERNET_ARCHIVE_ITEM_URL = "https://archive.org/details/{identifier}"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_ITEM_URL = "https://www.wikidata.org/wiki/{qid}"
NEODB_BASE_URL = "https://neodb.social"
NEODB_TRENDING_URLS = {
    "book": f"{NEODB_BASE_URL}/api/trending/book/",
    "film": f"{NEODB_BASE_URL}/api/trending/movie/",
    "game": f"{NEODB_BASE_URL}/api/trending/game/",
    "music": f"{NEODB_BASE_URL}/api/trending/music/",
}
NEODB_DETAIL_API_URLS = {
    "book": f"{NEODB_BASE_URL}/api/book/{{uuid}}",
    "film": f"{NEODB_BASE_URL}/api/movie/{{uuid}}",
    "game": f"{NEODB_BASE_URL}/api/game/{{uuid}}",
    "music": f"{NEODB_BASE_URL}/api/album/{{uuid}}",
}
CLASSIC_NEODB_DETAIL_LIMIT = 24
CLASSIC_MEDIA_SEARCH_ROWS = 50
CLASSIC_MEDIA_PAGE_ATTEMPTS = 5
CLASSIC_MEDIA_MIN_AGE_YEARS = 10
CLASSIC_MEDIA_RELEASE_LIMIT = 80
CLASSIC_MEDIA_RANDOM_DATE_ATTEMPTS = 4
CLASSIC_CHINESE_BOOK_PAGE_LIMIT = 20
CLASSIC_GAME_SEARCHES = (
    (
        "MS-DOS Games",
        "collection:softwarelibrary_msdos_games AND mediatype:software AND subject:game",
    ),
    ("Internet Arcade", "collection:internetarcade AND mediatype:software"),
)
CLASSIC_CHINESE_BOOK_SEARCH_TEMPLATE = (
    "mediatype:texts AND language:chi AND date:[1800 TO {max_year}]"
)
CLASSIC_MEDIA_KINDS = (
    {
        "key": "game",
        "label": "游戏",
        "release_word": "发售",
        "creator_fields": ("developer", "publisher"),
        "wikidata_filter": "?item wdt:P31/wdt:P279* wd:Q7889 .",
    },
    {
        "key": "film",
        "label": "电影",
        "release_word": "上映",
        "creator_fields": ("director",),
        "wikidata_filter": "?item wdt:P31/wdt:P279* wd:Q11424 .",
    },
    {
        "key": "music",
        "label": "音乐",
        "release_word": "发行",
        "creator_fields": ("artist", "company"),
        "wikidata_filter": """
  {
    ?item wdt:P31/wdt:P279* wd:Q482994 .
  }
  UNION
  {
    ?item wdt:P31/wdt:P279* wd:Q134556 .
  }
  UNION
  {
    ?item wdt:P31/wdt:P279* wd:Q7366 .
  }
""",
    },
    {
        "key": "book",
        "label": "book",
        "release_word": "出版",
        "creator_fields": ("author", "publisher"),
        "require_chinese_label": True,
        "wikidata_filter": """
  {
    ?item wdt:P31/wdt:P279* wd:Q571 .
  }
  UNION
  {
    ?item wdt:P31/wdt:P279* wd:Q47461344 .
  }
  ?item wdt:P407 wd:Q7850 .
""",
    },
)
BLOG_HISTORY_START_YEAR = 2005
BLOG_HISTORY_END_YEAR = 2025
BLOG_RANDOM_SEARCH_ATTEMPTS = 5
BLOG_LINK_CHECK_ATTEMPTS = 10
HOT100_RANDOM_SALT = 42
BLOG_RANDOM_SALT = 99
HACKER_NEWS_RANDOM_SALT = 123
CLASSIC_MEDIA_RANDOM_SALT = 321
EARLY_GET_UP_HOURS = range(3, 10)

BIRTH_YEAR = 1989  # change it to your birth year

CITY_GEOCODE_DB = "data/city_geocode_cache.db"
CITY_MAP_FILE = "cities_map.png"
CITY_POSTER_MAX_ATTEMPTS = 3
CITY_POSTER_RETRY_DELAY_SECONDS = 2
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_REQUEST_AT = 0.0
_NOMINATIM_MIN_INTERVAL = 1.1


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
class HackerNewsStory:
    object_id: str
    title: str
    url: str
    points: int
    num_comments: int
    author: str
    created_at: str

    @property
    def hn_url(self):
        return HACKER_NEWS_ITEM_URL.format(object_id=self.object_id)

    @property
    def link_url(self):
        return self.url or self.hn_url

    @property
    def key(self):
        return f"hn:{self.object_id}"


@dataclass(frozen=True)
class ClassicGame:
    identifier: str
    title: str
    creator: str
    year: str
    description: str
    downloads: int
    source: str
    release_date: str = ""
    chinese_title: str = ""
    source_url: str = ""
    wikidata_url: str = ""
    external_url: str = ""
    media_type: str = "game"
    media_label: str = "游戏"
    release_word: str = "发售"

    @property
    def archive_url(self):
        return INTERNET_ARCHIVE_ITEM_URL.format(identifier=self.identifier)

    @property
    def url(self):
        return self.source_url or self.archive_url

    @property
    def key(self):
        return f"classic-media:{self.media_type}:{self.identifier}"


@dataclass(frozen=True)
class GetUpMessageParts:
    is_get_up_early: bool
    day_of_year: int
    year_progress: str
    running_info: str
    history_today: str
    leetcode: str
    blog_article: str
    city_info: str
    city_poster_path: str
    city_map_path: str

    def as_tuple(self):
        return (
            self.is_get_up_early,
            self.day_of_year,
            self.year_progress,
            self.running_info,
            self.history_today,
            self.leetcode,
            self.blog_article,
            self.city_info,
            self.city_poster_path,
            self.city_map_path,
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
            city_info=self.city_info,
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


def _load_used_problem_slugs(used_files):
    used_slugs = set()
    for used_file in used_files:
        used_slugs.update(_read_non_empty_lines(used_file))
    return used_slugs


def _pick_problem_from_pool(problem_file, used_file, now, global_used_files=None):
    used_slugs = _load_used_problem_slugs(global_used_files or (used_file,))
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
        global_used_files = (easy_used_file, hot100_used_file)

        results = []
        used_slugs = _load_used_problem_slugs(global_used_files)
        daily_question = _get_leetcode_daily_question()
        if (
            daily_question
            and daily_question.difficulty == "EASY"
            and daily_question.slug not in used_slugs
        ):
            _append_line(easy_used_file, daily_question.slug)
            results.append(
                _format_easy_problem(
                    daily_question,
                    prefix="今日官方每日一题！ 今日 LeetCode 🟢 简单题：",
                )
            )

        if not results and _daily_rng(now, HOT100_RANDOM_SALT).random() < 0.5:
            hot100_problem = _pick_problem_from_pool(
                hot100_file,
                hot100_used_file,
                now,
                global_used_files,
            )
            if hot100_problem:
                results.append(_format_hot100_problem(hot100_problem))
            else:
                results.append("热题 100 都做完啦！🎉")

        if not results:
            easy_problem = _pick_problem_from_pool(
                easy_file,
                easy_used_file,
                now,
                global_used_files,
            )
            if easy_problem:
                results.append(_format_easy_problem(easy_problem))
            else:
                results.append("今日 LeetCode：所有简单题都做完啦！🎉")

        return "\n\n".join(results)
    except Exception as error:
        print(f"Error getting daily leetcode: {error}")
        return ""


def _get_city_wiki_url(wiki_title):
    return CITY_WIKI_BASE_URL.format(city=quote(wiki_title, safe=""))


def _parse_city_line(line):
    parts = [part.strip() for part in line.split("|")]
    city_name = parts[0].strip()
    wiki_title = parts[1].strip() if len(parts) > 1 else city_name + "市"
    province_name = parts[2].strip() if len(parts) > 2 else ""
    return city_name, wiki_title, province_name


def _format_city_post_label(city_name, province_name):
    if province_name:
        return f"{province_name}·{city_name}"
    return city_name


def _get_city_province_name(city_name):
    city_name = city_name.strip()
    if not city_name:
        return ""
    for line in _read_non_empty_lines(_data_file_path(CHINESE_CITIES_FILE)):
        name, _wiki_title, province_name = _parse_city_line(line)
        if name == city_name:
            return province_name
    return ""


def _format_city_poster_subtitle(city_name):
    province_name = _get_city_province_name(city_name)
    if province_name:
        return f"{province_name}, 中国"
    return "中国"


def get_random_city():
    try:
        now = _now()
        cities_file = _data_file_path(CHINESE_CITIES_FILE)
        used_file = _data_file_path(CITIES_USED_FILE)

        raw_lines = _read_non_empty_lines(cities_file)
        all_entries = [_parse_city_line(line) for line in raw_lines]
        used_cities = set(_read_non_empty_lines(used_file))
        total_used = len(used_cities)

        available = [
            (name, wiki, province)
            for name, wiki, province in all_entries
            if name not in used_cities
        ]
        if not available:
            return "", "", len(all_entries)

        city_name, wiki_title, province_name = _daily_rng(now, CITY_RANDOM_SALT).choice(
            available
        )
        _append_line(used_file, city_name)
        total_used += 1

        wiki_url = _get_city_wiki_url(wiki_title)
        post_label = _format_city_post_label(city_name, province_name)
        city_info = (
            f"今日城市 🏙️：[{post_label}]({wiki_url})"
            f"（已探索 {total_used}/{len(all_entries)} 个地级市）"
        )
        return city_info, city_name, total_used
    except Exception as error:
        print(f"Error getting random city: {error}")
        return "", "", 0


def _generate_city_poster(city_name):
    if not city_name:
        return ""
    output_path = _city_poster_output_path(city_name)
    if output_path.exists():
        return str(output_path)
    font_file = _resolve_city_poster_font_file()

    coord = _geocode_city(city_name)
    if coord is None:
        lat, lon = None, None
        location = city_name
    else:
        lat, lon = coord
        location = None

    request = PosterRequest(
        output=output_path,
        formats=("png",),
        location=location,
        language="zh",
        title=city_name,
        subtitle=_format_city_poster_subtitle(city_name),
        distance_m=8000.0,
        dpi=150,
        theme="random",
        lat=lat,
        lon=lon,
        font_file=font_file,
        cache_dir=SCRIPT_DIR / ".terraink-cache",
    )

    for attempt in range(1, CITY_POSTER_MAX_ATTEMPTS + 1):
        try:
            result = generate_poster(request)
            if result.files:
                return str(result.files[0])
        except Exception as error:
            print(
                f"Error generating city poster (attempt "
                f"{attempt}/{CITY_POSTER_MAX_ATTEMPTS}): {error}"
            )
            if output_path.exists():
                return str(output_path)
            if attempt < CITY_POSTER_MAX_ATTEMPTS:
                time.sleep(CITY_POSTER_RETRY_DELAY_SECONDS * attempt)
    return ""


def _city_poster_output_path(city_name):
    output_dir = SCRIPT_DIR / CITY_POSTERS_DIR
    output_dir.mkdir(exist_ok=True)
    return output_dir / f"{city_name}.png"


def _resolve_city_poster_font_file():
    search_dirs = (SCRIPT_DIR,)
    if SCRIPT_DIR != MODULE_DIR:
        search_dirs += (MODULE_DIR,)

    for base_dir in search_dirs:
        for candidate in LOCAL_CJK_FONT_FILE_CANDIDATES:
            path = base_dir / candidate
            if path.exists():
                return path
    return None


def _get_geocode_db_path():
    return _data_file_path(CITY_GEOCODE_DB)


def _init_geocode_cache():
    db_path = _get_geocode_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS geocache ("
            "city TEXT PRIMARY KEY, lat REAL, lon REAL"
            ")"
        )
        conn.commit()


def _get_cached_geocode(city):
    try:
        with sqlite3.connect(str(_get_geocode_db_path())) as conn:
            row = conn.execute(
                "SELECT lat, lon FROM geocache WHERE city = ?", (city,)
            ).fetchone()
            return (row[0], row[1]) if row else None
    except Exception:
        return None


def _set_cached_geocode(city, lat, lon):
    try:
        _init_geocode_cache()
        with sqlite3.connect(str(_get_geocode_db_path())) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO geocache VALUES (?, ?, ?)", (city, lat, lon)
            )
            conn.commit()
    except Exception as error:
        print(f"Error caching geocode: {error}")


def _geocode_city(city):
    city = city.strip()
    if not city:
        return None
    cached = _get_cached_geocode(city)
    if cached:
        return cached
    queries = [f"{city}市, 中国", f"{city}市", city]
    for query in queries:
        try:
            global _LAST_NOMINATIM_REQUEST_AT
            with _NOMINATIM_LOCK:
                now = time.monotonic()
                wait = _NOMINATIM_MIN_INTERVAL - (now - _LAST_NOMINATIM_REQUEST_AT)
                if wait > 0:
                    time.sleep(wait)
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": query,
                        "format": "json",
                        "limit": "1",
                        "countrycodes": "cn",
                    },
                    headers={
                        "User-Agent": WIKIMEDIA_USER_AGENT,
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                    timeout=10,
                )
                _LAST_NOMINATIM_REQUEST_AT = time.monotonic()
            results = resp.json()
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                _set_cached_geocode(city, lat, lon)
                return (lat, lon)
        except Exception:
            pass
        time.sleep(1)
    return None


_NATURALEARTH_URL = (
    "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
)
_WORLD_CACHE_FILE = _data_file_path("data/ne_110m_countries.gpkg")


def _load_world_geodata():
    try:
        import geopandas as gpd
    except ImportError:
        return None
    if _WORLD_CACHE_FILE.exists():
        try:
            return gpd.read_file(str(_WORLD_CACHE_FILE))
        except Exception:
            pass
    try:
        world = gpd.read_file(_NATURALEARTH_URL)
        _WORLD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        world.to_file(str(_WORLD_CACHE_FILE), driver="GPKG")
        return world
    except Exception as error:
        print(f"Error loading world geodata: {error}")
        return None


def _setup_matplotlib_font():
    try:
        import matplotlib
        from matplotlib.font_manager import FontProperties

        font_path = _resolve_city_poster_font_file()
        if font_path is None:
            return None
        fp = FontProperties(fname=str(font_path))
        matplotlib.font_manager.fontManager.addfont(str(font_path))
        matplotlib.rcParams["font.family"] = fp.get_name()
        matplotlib.rcParams["axes.unicode_minus"] = False
        return fp
    except Exception:
        return None


def _generate_cities_map(today_city=""):
    try:
        used_cities = _read_non_empty_lines(_data_file_path(CITIES_USED_FILE))
        if not used_cities:
            return ""
        city_coords = []
        for city in used_cities:
            coord = _geocode_city(city)
            if coord:
                city_coords.append((city, coord[0], coord[1]))
        if not city_coords:
            return ""
        return _render_cities_map(city_coords, today_city=today_city)
    except Exception as error:
        print(f"Error generating cities map: {error}")
        return ""


def _rect_overlap_area(a, b):
    dx = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    dy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return float(dx * dy)


def _rect_outside_area(rect, bounds):
    left = max(0.0, bounds[0] - rect[0])
    right = max(0.0, rect[2] - bounds[2])
    bottom = max(0.0, bounds[1] - rect[1])
    top = max(0.0, rect[3] - bounds[3])
    return (
        left * (rect[3] - rect[1])
        + right * (rect[3] - rect[1])
        + bottom * (rect[2] - rect[0])
        + top * (rect[2] - rect[0])
    )


def _rect_center_distance(a, b):
    import math

    ax = (a[0] + a[2]) * 0.5
    ay = (a[1] + a[3]) * 0.5
    bx = (b[0] + b[2]) * 0.5
    by = (b[1] + b[3]) * 0.5
    return math.hypot(ax - bx, ay - by)


def _label_box_for_offset(px, py, width, height, dx, dy, pts_to_px):
    anchor_x = px + dx * pts_to_px
    anchor_y = py + dy * pts_to_px
    if dx < 0:
        x0 = anchor_x - width
        x1 = anchor_x
    else:
        x0 = anchor_x
        x1 = anchor_x + width
    y0 = anchor_y - height * 0.5
    y1 = anchor_y + height * 0.5
    return (x0, y0, x1, y1)


def _measure_label_sizes(labels, ax, fontsize=8, priority_labels=()):
    fig = ax.get_figure()
    dpi = fig.dpi
    pts_to_px = dpi / 72.0
    priority_labels = set(priority_labels)

    def fallback_size(label):
        label_fontsize = fontsize + 2 if label in priority_labels else fontsize
        avg_char_w = label_fontsize * 0.95 * pts_to_px
        return (
            len(label) * avg_char_w + 14 * pts_to_px,
            label_fontsize * 2.0 * pts_to_px,
        )

    canvas = getattr(fig, "canvas", None)
    if canvas is None:
        return [fallback_size(label) for label in labels]

    texts = []
    try:
        for label in labels:
            is_priority = label in priority_labels
            label_fontsize = fontsize + 2 if is_priority else fontsize
            texts.append(
                ax.text(
                    0,
                    0,
                    label,
                    fontsize=label_fontsize,
                    fontweight="bold" if is_priority else "normal",
                    ha="left",
                    va="center",
                    alpha=0.0,
                    bbox=dict(
                        boxstyle="round,pad=0.34" if is_priority else "round,pad=0.24",
                        facecolor="white",
                        edgecolor="white",
                    ),
                )
            )
        canvas.draw()
        renderer = canvas.get_renderer()
        sizes = []
        for text in texts:
            bbox_patch = text.get_bbox_patch()
            extent = (
                bbox_patch.get_window_extent(renderer)
                if bbox_patch is not None
                else text.get_window_extent(renderer)
            )
            sizes.append((extent.width + 2 * pts_to_px, extent.height + 2 * pts_to_px))
        return sizes
    except Exception:
        return [fallback_size(label) for label in labels]
    finally:
        for text in texts:
            text.remove()


def _compute_label_offsets(lons, lats, labels, ax, fontsize=8, priority_labels=()):
    import math

    n = len(lons)
    if n == 0:
        return []
    fig = ax.get_figure()
    dpi = fig.dpi
    pts_to_px = dpi / 72.0
    display_pts = [ax.transData.transform((lon, lat)) for lon, lat in zip(lons, lats)]
    label_sizes = _measure_label_sizes(
        labels, ax, fontsize=fontsize, priority_labels=priority_labels
    )
    base_angles = [25, 335, 60, 300, 0, 90, 270, 150, 210, 120, 240, 180]
    candidates = []
    for dist in (9, 16, 26, 40, 58, 80, 108, 140):
        for angle_deg in base_angles:
            rad = math.radians(angle_deg)
            candidates.append(
                (round(dist * math.cos(rad), 1), round(dist * math.sin(rad), 1))
            )
    dot_radius = 7 * pts_to_px
    dot_boxes = [
        (px - dot_radius, py - dot_radius, px + dot_radius, py + dot_radius)
        for px, py in display_pts
    ]
    offsets = [None] * n
    placed_boxes = []
    priority_labels = set(priority_labels)
    density_radius = 95 * pts_to_px
    densities = []
    for i, point in enumerate(display_pts):
        density = 0.0
        for j, other in enumerate(display_pts):
            if i == j:
                continue
            distance = math.hypot(point[0] - other[0], point[1] - other[1])
            if distance < density_radius:
                density += (density_radius - distance) / density_radius
        densities.append(density)
    placement_order = sorted(
        range(n),
        key=lambda i: (
            labels[i] not in priority_labels,
            -densities[i],
            -len(labels[i]),
            i,
        ),
    )
    proximity_threshold = 11 * pts_to_px
    axes_bounds = ax.get_window_extent().bounds
    axes_bounds = (
        axes_bounds[0] + 6 * pts_to_px,
        axes_bounds[1] + 6 * pts_to_px,
        axes_bounds[0] + axes_bounds[2] - 6 * pts_to_px,
        axes_bounds[1] + axes_bounds[3] - 6 * pts_to_px,
    )
    for i in placement_order:
        px, py = display_pts[i]
        w, h = label_sizes[i]
        best_offset = candidates[0]
        best_cost = float("inf")
        best_box = (0.0, 0.0, 0.0, 0.0)
        for dx, dy in candidates:
            box = _label_box_for_offset(px, py, w, h, dx, dy, pts_to_px)
            cost = 0.0
            for pb in placed_boxes:
                overlap = _rect_overlap_area(box, pb)
                if overlap > 0:
                    cost += overlap * 40
                else:
                    d = _rect_center_distance(box, pb)
                    if d < proximity_threshold:
                        cost += (proximity_threshold - d) * 0.8
            for j, db in enumerate(dot_boxes):
                cost += _rect_overlap_area(box, db) * (18 if j == i else 12)
            cost += _rect_outside_area(box, axes_bounds) * 80
            cost += math.hypot(dx, dy) * 0.08
            if abs(dy) < 4:
                cost += 5
            if cost < best_cost:
                best_cost = cost
                best_offset = (dx, dy)
                best_box = box
        offsets[i] = (round(best_offset[0]), round(best_offset[1]))
        placed_boxes.append(best_box)
    return offsets


def _render_cities_map(city_coords, today_city=""):
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    matplotlib.use("Agg")
    _setup_matplotlib_font()

    today_city = today_city.strip()

    other_coords = [c for c in city_coords if c[0] != today_city]
    today_coords = [c for c in city_coords if c[0] == today_city]

    all_lons = [c[2] for c in city_coords]
    all_lats = [c[1] for c in city_coords]

    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)
    pad_lon = max((max_lon - min_lon) * 0.15, 4)
    pad_lat = max((max_lat - min_lat) * 0.15, 3)
    view_min_lon = max(70, min_lon - pad_lon)
    view_max_lon = min(140, max_lon + pad_lon)
    view_min_lat = max(10, min_lat - pad_lat)
    view_max_lat = min(55, max_lat + pad_lat)

    world = _load_world_geodata()

    fig = Figure(figsize=(13.5, 8.8), dpi=160)
    fig.set_facecolor("#F6F8FB")
    canvas = FigureCanvasAgg(fig)
    ax = fig.subplots(1, 1)
    ax.set_facecolor("#DCECF8")

    if world is not None:
        try:
            world_clipped = world.cx[
                view_min_lon:view_max_lon, view_min_lat:view_max_lat
            ]
        except Exception:
            world_clipped = world
        if getattr(world_clipped, "empty", False):
            world_clipped = world
        world_clipped.plot(
            ax=ax,
            color="#F1EEE6",
            edgecolor="#AEB8C2",
            linewidth=0.55,
            zorder=1,
        )

    # Other cities
    if other_coords:
        ax.scatter(
            [c[2] for c in other_coords],
            [c[1] for c in other_coords],
            s=62,
            c="#E66A4F",
            alpha=0.92,
            edgecolors="#FFFFFF",
            linewidths=1.2,
            zorder=3,
        )

    # Today city
    if today_coords:
        ax.scatter(
            [c[2] for c in today_coords],
            [c[1] for c in today_coords],
            s=210,
            c="#F4A261",
            alpha=0.95,
            edgecolors="#214C5C",
            linewidths=2.2,
            zorder=5,
        )

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(view_min_lon, view_max_lon)
    ax.set_ylim(view_min_lat, view_max_lat)
    ax.margins(0)
    canvas.draw()

    lons = [c[2] for c in city_coords]
    lats = [c[1] for c in city_coords]
    labels = [c[0] for c in city_coords]

    label_offsets = _compute_label_offsets(
        lons, lats, labels, ax, fontsize=8, priority_labels=(today_city,)
    )
    for lon, lat, label, offset in zip(lons, lats, labels, label_offsets):
        is_today = label == today_city
        ha = "left" if offset[0] >= 0 else "right"
        leader_len = (offset[0] ** 2 + offset[1] ** 2) ** 0.5
        arrowprops = None
        if leader_len >= 14 or is_today:
            arrowprops = dict(
                arrowstyle="-",
                color="#D97706" if is_today else "#9AA7B3",
                linewidth=0.8 if is_today else 0.45,
                alpha=0.82 if is_today else 0.48,
                shrinkA=2,
                shrinkB=3,
            )
        ann = dict(
            textcoords="offset points",
            xytext=offset,
            fontsize=10 if is_today else 8,
            fontweight="bold" if is_today else "normal",
            color="#1F2937" if is_today else "#264653",
            ha=ha,
            va="center",
            bbox=dict(
                boxstyle="round,pad=0.34" if is_today else "round,pad=0.24",
                facecolor="#FEF3C7" if is_today else "white",
                alpha=0.98 if is_today else 0.88,
                edgecolor="#F59E0B" if is_today else "#A8B0BA",
                linewidth=1.2 if is_today else 0.8,
            ),
            arrowprops=arrowprops,
            zorder=6 if is_today else 4,
        )
        ax.annotate(label, (lon, lat), **ann)

    ax.grid(color="#CBD5E1", linestyle="--", linewidth=0.55, alpha=0.35, zorder=0)
    title = (
        f"已探索城市地图（{len(city_coords)} 个城市）"
        if not today_city
        else f"已探索城市地图（{len(city_coords)} 个城市）· 今日：{today_city}"
    )
    ax.set_title(
        title,
        fontsize=15,
        fontweight="bold",
        pad=12,
        color="#1F2937",
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(labelsize=8, colors="#64748B", length=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CBD5E1")
    ax.spines["bottom"].set_color("#CBD5E1")

    fig.tight_layout(pad=1.0)
    output_dir = SCRIPT_DIR / CITY_POSTERS_DIR
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / CITY_MAP_FILE
    canvas.print_png(str(output_path))
    return str(output_path)


def _clean_hn_text(value, max_length=None):
    if not value:
        return ""

    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*`_~]", "", text)
    text = " ".join(text.split())
    if max_length is None or len(text) <= max_length:
        return text

    truncated = text[:max_length]
    for punct in ["。", "？", "！", ". ", "? ", "! "]:
        last_pos = truncated.rfind(punct)
        if last_pos > max_length * 0.45:
            return text[: last_pos + len(punct)].strip()
    return truncated.rstrip() + "..."


def _load_used_hacker_news():
    return set(_read_non_empty_lines(_data_file_path(HACKER_NEWS_USED_FILE)))


def _save_used_hacker_news(key):
    if key:
        _append_line(_data_file_path(HACKER_NEWS_USED_FILE), key)


def _parse_hn_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hn_story_from_hit(hit):
    object_id = str(hit.get("objectID") or hit.get("id") or "").strip()
    title = _clean_hn_text(hit.get("title") or hit.get("story_title") or "", 260)
    if not object_id or not title:
        return None

    return HackerNewsStory(
        object_id=object_id,
        title=title,
        url=(hit.get("url") or "").strip(),
        points=_parse_hn_int(hit.get("points")),
        num_comments=_parse_hn_int(hit.get("num_comments")),
        author=_clean_hn_text(hit.get("author", ""), 80),
        created_at=hit.get("created_at", ""),
    )


def _valid_hn_date(year, month, day):
    try:
        pendulum.datetime(year, month, day, tz="UTC")
        return True
    except ValueError:
        return False


def _hn_day_timestamps(year, month, day):
    start = pendulum.datetime(year, month, day, tz="UTC")
    end = start.add(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _hacker_news_candidate_years(now):
    month = now.month
    day = now.day
    years = [
        year
        for year in range(HACKER_NEWS_START_YEAR, now.year)
        if _valid_hn_date(year, month, day)
    ]
    rng = _daily_rng(now, HACKER_NEWS_RANDOM_SALT)
    rng.shuffle(years)
    return years


def _fetch_hacker_news_top_stories_for_date(year, month, day):
    start_ts, end_ts = _hn_day_timestamps(year, month, day)
    response = requests.get(
        HACKER_NEWS_SEARCH_URL,
        params={
            "tags": "story",
            "hitsPerPage": str(HACKER_NEWS_STORIES_PER_PAGE),
            "numericFilters": f"created_at_i>={start_ts},created_at_i<{end_ts}",
        },
        timeout=10,
    )
    if not response.ok:
        return []

    stories = []
    seen_ids = set()
    for hit in response.json().get("hits", []):
        story = _hn_story_from_hit(hit)
        if story is None or story.object_id in seen_ids:
            continue
        seen_ids.add(story.object_id)
        stories.append(story)

    stories.sort(key=lambda story: (story.points, story.num_comments), reverse=True)
    return stories[:HACKER_NEWS_TOP_LIMIT]


def _is_hacker_news_story_link_available(story):
    return _check_link_available(story.link_url)


def _select_hacker_news_history_story(now, used_keys, target_year=None):
    rng = _daily_rng(now, HACKER_NEWS_RANDOM_SALT)

    if target_year is None:
        years = _hacker_news_candidate_years(now)
    elif HACKER_NEWS_START_YEAR <= target_year < now.year and _valid_hn_date(
        target_year, now.month, now.day
    ):
        years = [target_year]
    else:
        years = []

    for year in years:
        stories = _fetch_hacker_news_top_stories_for_date(year, now.month, now.day)
        available = [story for story in stories if story.key not in used_keys]
        rng.shuffle(available)
        for story in available:
            if _is_hacker_news_story_link_available(story):
                return year, story
            print(f"Skip unavailable HN story link: {story.link_url}")
    return None, None


def _format_hacker_news_history_story(year, month, day, story):
    date = f"{year}-{month:02d}-{day:02d}"
    title = _clean_hn_text(story.title, 260)
    lines = [
        f"HN 历史今日（{date}）：",
        "",
        f"• {title}",
    ]

    meta_parts = [f"{story.points} points", f"{story.num_comments} comments"]
    if story.author:
        meta_parts.append(f"by {story.author}")
    lines.append(" / ".join(meta_parts))
    lines.append(f"原文：[{title}]({story.link_url})")
    lines.append(f"HN 讨论：[{story.object_id}]({story.hn_url})")
    return "\n".join(lines)


def get_hacker_news_history(target_year=None):
    try:
        now = _now()
        used_keys = _load_used_hacker_news()
        year, story = _select_hacker_news_history_story(
            now,
            used_keys,
            target_year=target_year,
        )
        if story is None:
            return ""

        _save_used_hacker_news(story.key)
        return _format_hacker_news_history_story(year, now.month, now.day, story)
    except Exception as error:
        print(f"Error getting Hacker News history: {error}")
        return ""


def _load_used_classic_media():
    return set(_read_non_empty_lines(_data_file_path(CLASSIC_MEDIA_USED_FILE)))


def _save_used_classic_media(key):
    if key:
        _append_line(_data_file_path(CLASSIC_MEDIA_USED_FILE), key)


def _wikidata_headers(accept=None):
    headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return headers


def _neodb_headers():
    return {"User-Agent": WIKIMEDIA_USER_AGENT, "Accept": "application/json"}


def _binding_value(binding, name):
    value = binding.get(name, {})
    if isinstance(value, dict):
        return value.get("value", "")
    return ""


def _wikidata_qid(value):
    if not value:
        return ""
    return value.rstrip("/").split("/")[-1]


def _preferred_text(*values, zh=False):
    for value in values:
        if value:
            text = _clean_hn_text(value, 240)
            if text:
                return convert(text, "zh-cn") if zh else text
    return ""


def classic_media_chinese_label_filter(kind):
    if kind.get("require_chinese_label"):
        return "FILTER(BOUND(?zhLabel) || BOUND(?zhCnLabel) || BOUND(?zhHansLabel))"
    return ""


def _classic_media_release_query(kind, now, month, day):
    max_year = now.year - CLASSIC_MEDIA_MIN_AGE_YEARS
    wikidata_filter = kind["wikidata_filter"]
    return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX psv: <http://www.wikidata.org/prop/statement/value/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
SELECT ?item ?release ?zhLabel ?zhCnLabel ?zhHansLabel ?enLabel ?zhDescription ?enDescription WHERE {{
  {wikidata_filter}
  ?item p:P577 ?releaseStatement .
  ?releaseStatement psv:P577 ?releaseNode .
  ?releaseNode wikibase:timeValue ?release ; wikibase:timePrecision ?precision .
  FILTER(?precision >= 11)
  FILTER(MONTH(?release) = {month} && DAY(?release) = {day})
  FILTER(YEAR(?release) <= {max_year})
  OPTIONAL {{ ?item rdfs:label ?zhLabel FILTER(LANG(?zhLabel) = "zh") }}
  OPTIONAL {{ ?item rdfs:label ?zhCnLabel FILTER(LANG(?zhCnLabel) = "zh-cn") }}
  OPTIONAL {{ ?item rdfs:label ?zhHansLabel FILTER(LANG(?zhHansLabel) = "zh-hans") }}
  OPTIONAL {{ ?item rdfs:label ?enLabel FILTER(LANG(?enLabel) = "en") }}
  OPTIONAL {{ ?item schema:description ?zhDescription FILTER(LANG(?zhDescription) = "zh") }}
  OPTIONAL {{ ?item schema:description ?enDescription FILTER(LANG(?enDescription) = "en") }}
  {classic_media_chinese_label_filter(kind)}
}}
ORDER BY ?release ?item
LIMIT {CLASSIC_MEDIA_RELEASE_LIMIT}
"""


def _fetch_classic_media_releases(kind, now, month, day):
    try:
        response = requests.get(
            WIKIDATA_SPARQL_URL,
            params={
                "query": _classic_media_release_query(kind, now, month, day),
                "format": "json",
            },
            headers=_wikidata_headers("application/sparql-results+json"),
            timeout=10,
        )
        if not response.ok:
            return []

        games = []
        for binding in response.json().get("results", {}).get("bindings", []):
            game = _classic_media_from_wikidata_binding(binding, kind)
            if game is not None:
                games.append(game)
        return games
    except Exception as error:
        print(f"Error fetching Wikidata {kind['label']} releases: {error}")
        return []


def _classic_media_from_wikidata_binding(binding, kind):
    qid = _wikidata_qid(_binding_value(binding, "item"))
    if not qid:
        return None

    chinese_title = _preferred_text(
        _binding_value(binding, "zhHansLabel"),
        _binding_value(binding, "zhCnLabel"),
        _binding_value(binding, "zhLabel"),
        zh=True,
    )
    english_title = _preferred_text(_binding_value(binding, "enLabel"))
    if kind["key"] == "book" and chinese_title:
        title = chinese_title
    else:
        title = english_title or chinese_title
    if not title:
        return None

    release_date = _binding_value(binding, "release").split("T", 1)[0]
    description = _preferred_text(
        _binding_value(binding, "zhDescription"),
        zh=True,
    )
    if not description:
        description = _preferred_text(_binding_value(binding, "enDescription"))

    wikidata_url = WIKIDATA_ITEM_URL.format(qid=qid)
    return ClassicGame(
        identifier=f"wikidata-{qid}",
        title=title,
        creator="",
        year=release_date[:4],
        description=description,
        downloads=0,
        source="Wikidata",
        release_date=release_date,
        chinese_title=chinese_title if chinese_title != title else "",
        source_url=wikidata_url,
        wikidata_url=wikidata_url,
        media_type=kind["key"],
        media_label=kind["label"],
        release_word=kind["release_word"],
    )


def _archive_field_text(value):
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def _archive_year_text(value):
    text = _archive_field_text(value)
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if match:
        return match.group(1)
    return text[:20]


def _clean_classic_game_description(value, max_length=220):
    text = _clean_hn_text(_archive_field_text(value), max_length=900)
    if not text:
        return ""

    text = re.sub(r"\bClick here for the manual\.?\s*", "", text, flags=re.I)
    text = re.sub(r"\bAlso For\b", "Also for", text)
    text = re.sub(r"\bDeveloped by\b", "Developed by", text)
    text = re.sub(r"\bPublished by\b", "Published by", text)
    text = re.sub(r"\bReleased\b", "Released", text)
    text = " ".join(text.split())

    control_markers = (
        "1 Player Start",
        "2 Players Start",
        "Coin 1",
        "Service Mode",
        "P1 Button",
        "Paddle Analog",
    )
    if any(marker in text[:350] for marker in control_markers):
        return ""

    return _clean_hn_text(text, max_length)


def _classic_game_from_doc(doc, source):
    identifier = _archive_field_text(doc.get("identifier"))
    title = _clean_hn_text(doc.get("title"), 180).replace("[", "(").replace("]", ")")
    if not identifier or not title:
        return None

    year = _archive_year_text(doc.get("year") or doc.get("date"))
    return ClassicGame(
        identifier=identifier,
        title=title,
        creator=_clean_hn_text(doc.get("creator"), 120),
        year=year,
        description=_clean_classic_game_description(doc.get("description")),
        downloads=_parse_hn_int(doc.get("downloads")),
        source=source,
    )


def _classic_chinese_book_from_doc(doc):
    identifier = _archive_field_text(doc.get("identifier"))
    title = convert(
        _clean_hn_text(doc.get("title"), 180).replace("[", "(").replace("]", ")"),
        "zh-cn",
    )
    if not identifier or not title or not _has_cjk(title):
        return None

    year = _archive_year_text(doc.get("year") or doc.get("date"))
    return ClassicGame(
        identifier=identifier,
        title=title,
        creator=convert(_clean_hn_text(doc.get("creator"), 120), "zh-cn"),
        year=year,
        description=convert(
            _clean_classic_game_description(doc.get("description")), "zh-cn"
        ),
        downloads=_parse_hn_int(doc.get("downloads")),
        source="Internet Archive 中文文本",
        media_type="book",
        media_label="老书",
        release_word="出版",
    )


def _neodb_absolute_url(url):
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{NEODB_BASE_URL}{url}"
    return f"{NEODB_BASE_URL}/{url}"


def _neodb_localized_text(
    values,
    preferred_languages=("zh-cn", "zh-hans", "zh", "zh-tw", "zh-hant"),
):
    if not isinstance(values, list):
        return ""

    by_lang = {
        str(item.get("lang", "")).lower(): str(item.get("text", "")).strip()
        for item in values
        if isinstance(item, dict) and item.get("text")
    }
    for language in preferred_languages:
        if by_lang.get(language):
            return convert(by_lang[language], "zh-cn")
    return ""


def _neodb_external_resource_url(item, preferred_domain="douban.com"):
    for resource in item.get("external_resources") or []:
        url = resource.get("url", "") if isinstance(resource, dict) else ""
        if preferred_domain in url:
            return url
    for resource in item.get("external_resources") or []:
        url = resource.get("url", "") if isinstance(resource, dict) else ""
        if url:
            return url
    return ""


def _neodb_list_text(item, fields):
    values = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, list):
            values.extend(str(part).strip() for part in value if str(part).strip())
        elif value:
            values.append(str(value).strip())
        if values:
            break
    return " / ".join(values)


def _neodb_release_date_text(item, kind):
    release_date = _archive_field_text(item.get("release_date"))
    if release_date:
        return release_date

    if kind["key"] == "film" and item.get("year"):
        return str(item["year"])

    if kind["key"] == "book" and item.get("pub_year"):
        year = str(item["pub_year"])
        month = item.get("pub_month")
        if month:
            return f"{year}-{int(month):02d}"
        return year

    return ""


def _classic_media_from_neodb_item(item, kind):
    uuid = _archive_field_text(item.get("uuid"))
    title = _preferred_text(item.get("display_title"), item.get("title"))
    if not uuid or not title:
        return None
    if _has_cjk(title):
        title = convert(title, "zh-cn")

    chinese_title = _neodb_localized_text(item.get("localized_title"))
    description = _preferred_text(
        _neodb_localized_text(item.get("localized_description")),
        item.get("description"),
        zh=_has_cjk(str(item.get("description", ""))),
    )
    if kind["key"] == "book" and not _has_cjk(title):
        return None

    creator_text = _neodb_list_text(item, kind.get("creator_fields", ()))
    release_date = _neodb_release_date_text(item, kind)

    source_url = _neodb_absolute_url(item.get("url")) or _neodb_absolute_url(
        item.get("id")
    )
    external_url = _neodb_external_resource_url(item)
    return ClassicGame(
        identifier=f"neodb-{kind['key']}-{uuid}",
        title=title,
        creator=creator_text,
        year=release_date[:4],
        description=description,
        downloads=0,
        source="NeoDB",
        release_date=release_date,
        chinese_title=chinese_title if chinese_title and chinese_title != title else "",
        source_url=source_url,
        wikidata_url="",
        external_url=external_url,
        media_type=kind["key"],
        media_label=kind["label"],
        release_word=kind["release_word"],
    )


def _classic_music_from_neodb_item(item):
    return _classic_media_from_neodb_item(item, CLASSIC_MEDIA_KINDS[2])


def _has_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _classic_game_translation_search_terms(title):
    terms = [title]
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", title)
    cleaned = " ".join(cleaned.split())
    if cleaned and cleaned not in terms:
        terms.append(cleaned)
    return terms


def _fetch_wikidata_chinese_title(title):
    for search_term in _classic_game_translation_search_terms(title):
        try:
            response = requests.get(
                WIKIDATA_API_URL,
                params={
                    "action": "wbsearchentities",
                    "search": search_term,
                    "language": "en",
                    "uselang": "zh",
                    "format": "json",
                    "type": "item",
                    "limit": "5",
                },
                headers=_wikidata_headers(),
                timeout=8,
            )
        except requests.exceptions.RequestException as error:
            print(f"Error fetching Wikidata Chinese title: {error}")
            continue

        if not response.ok:
            continue

        for result in response.json().get("search", []):
            label = convert(_clean_hn_text(result.get("label"), 120), "zh-cn")
            description = _clean_hn_text(result.get("description"), 160).lower()
            if not label or not _has_cjk(label) or label == title:
                continue
            if "video game" not in description and "游戏" not in description:
                continue

            qid = result.get("id", "")
            return label, WIKIDATA_ITEM_URL.format(qid=qid) if qid else ""
    return "", ""


def _with_wikidata_chinese_title(game):
    if game.chinese_title:
        return game

    chinese_title, wikidata_url = _fetch_wikidata_chinese_title(game.title)
    if not chinese_title:
        return game

    return ClassicGame(
        identifier=game.identifier,
        title=game.title,
        creator=game.creator,
        year=game.year,
        description=game.description,
        downloads=game.downloads,
        source=game.source,
        release_date=game.release_date,
        chinese_title=chinese_title,
        source_url=game.source_url,
        wikidata_url=wikidata_url,
        external_url=game.external_url,
        media_type=game.media_type,
        media_label=game.media_label,
        release_word=game.release_word,
    )


def _is_classic_game_candidate(game):
    title = game.title.lower()
    identifier = game.identifier.lower()
    blocked_words = (
        " patch",
        "patch ",
        "trainer",
        "walkthrough",
        "manual",
        "soundtrack",
        "dosbox",
        "driver",
        "utility",
        "level editor",
        "map editor",
        "music creator",
    )
    combined = f"{identifier} {title}"
    return not any(word in combined for word in blocked_words)


def _is_classic_chinese_book_candidate(book):
    title = book.title.lower()
    blocked_words = (
        "杂志",
        "期刊",
        "报纸",
        "广告",
        "词典",
        "辞典",
        "字典",
        "年鉴",
        "报告",
        "规则",
        "指示",
        "dictionary",
        "magazine",
        "newspaper",
        "journal",
        "catalog",
        "manual",
    )
    if not _has_cjk(book.title) or any(word in title for word in blocked_words):
        return False
    if re.search(r"第\s*\d+\s*期", book.title):
        return False
    return True


def _classic_game_search_params(query, rows, page=1):
    return {
        "q": query,
        "fl[]": [
            "identifier",
            "title",
            "creator",
            "year",
            "date",
            "description",
            "downloads",
        ],
        "rows": str(rows),
        "page": str(page),
        "output": "json",
        "sort[]": "identifier asc",
    }


def _classic_chinese_book_search_params(query, rows, page=1):
    params = _classic_game_search_params(query, rows, page)
    params["sort[]"] = "downloads desc"
    return params


def _fetch_classic_game_page_count(query):
    response = requests.get(
        INTERNET_ARCHIVE_SEARCH_URL,
        params=_classic_game_search_params(query, rows=0),
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        timeout=10,
    )
    if not response.ok:
        return 0

    total = _parse_hn_int(response.json().get("response", {}).get("numFound"))
    if total <= 0:
        return 0
    return ceil(total / CLASSIC_MEDIA_SEARCH_ROWS)


def _fetch_classic_games(query, source, page):
    response = requests.get(
        INTERNET_ARCHIVE_SEARCH_URL,
        params=_classic_game_search_params(query, CLASSIC_MEDIA_SEARCH_ROWS, page),
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        timeout=10,
    )
    if not response.ok:
        return []

    games = []
    for doc in response.json().get("response", {}).get("docs", []):
        game = _classic_game_from_doc(doc, source)
        if game is not None and _is_classic_game_candidate(game):
            games.append(game)
    return games


def _fetch_classic_chinese_books(now, page):
    query = CLASSIC_CHINESE_BOOK_SEARCH_TEMPLATE.format(
        max_year=now.year - CLASSIC_MEDIA_MIN_AGE_YEARS
    )
    response = requests.get(
        INTERNET_ARCHIVE_SEARCH_URL,
        params=_classic_chinese_book_search_params(
            query,
            CLASSIC_MEDIA_SEARCH_ROWS,
            page,
        ),
        headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        timeout=10,
    )
    if not response.ok:
        return []

    books = []
    for doc in response.json().get("response", {}).get("docs", []):
        book = _classic_chinese_book_from_doc(doc)
        if book is not None and _is_classic_chinese_book_candidate(book):
            books.append(book)
    return books


def _is_old_release(release_date, now):
    year = _release_year(release_date)
    if year is None:
        return False
    return year <= now.year - CLASSIC_MEDIA_MIN_AGE_YEARS


def _release_year(release_date):
    match = re.match(r"^(\d{4})", str(release_date or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_history_today_release(release_date, now):
    match = re.match(r"^\d{4}-(\d{2})-(\d{2})$", str(release_date or ""))
    if not match:
        return False
    return int(match.group(1)) == now.month and int(match.group(2)) == now.day


def _fetch_neodb_trending_items(kind):
    url = NEODB_TRENDING_URLS.get(kind["key"], "")
    if not url:
        return []

    try:
        response = requests.get(
            url,
            headers=_neodb_headers(),
            timeout=8,
        )
        if not response.ok:
            return []
        return response.json()
    except requests.exceptions.RequestException as error:
        print(f"Error fetching NeoDB {kind['label']}: {error}")
        return []


def _fetch_neodb_item_detail(kind, uuid):
    url = NEODB_DETAIL_API_URLS.get(kind["key"], "").format(uuid=uuid)
    if not url:
        return None

    try:
        response = requests.get(
            url,
            headers=_neodb_headers(),
            timeout=8,
        )
        if not response.ok:
            return None
        return response.json()
    except requests.exceptions.RequestException as error:
        print(f"Error fetching NeoDB {kind['label']} detail: {error}")
        return None


def _candidate_priority(media, now):
    is_today = _is_history_today_release(media.release_date, now)
    is_old = _is_old_release(media.release_date, now)
    if is_today and is_old:
        return 0
    if is_today:
        return 1
    if is_old:
        return 2
    return 3


def _select_neodb_media(now, used_keys, kind):
    items = _fetch_neodb_trending_items(kind)
    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT + 3)

    candidates = []
    for item in list(items)[:CLASSIC_NEODB_DETAIL_LIMIT]:
        uuid = _archive_field_text(item.get("uuid"))
        if not uuid:
            continue

        detail = _fetch_neodb_item_detail(kind, uuid) or item
        media = _classic_media_from_neodb_item(detail, kind)
        if media is None or media.key in used_keys:
            continue
        candidates.append(media)

    if not candidates:
        return None

    priority = min(_candidate_priority(media, now) for media in candidates)
    best_candidates = [
        media for media in candidates if _candidate_priority(media, now) == priority
    ]
    return rng.choice(best_candidates)


def _fetch_neodb_trending_music():
    return _fetch_neodb_trending_items(CLASSIC_MEDIA_KINDS[2])


def _fetch_neodb_album(uuid):
    return _fetch_neodb_item_detail(CLASSIC_MEDIA_KINDS[2], uuid)


def _select_neodb_music(now, used_keys):
    return _select_neodb_media(now, used_keys, CLASSIC_MEDIA_KINDS[2])


def _select_same_day_classic_media_release(now, used_keys, kind):
    games = [
        game
        for game in _fetch_classic_media_releases(kind, now, now.month, now.day)
        if game.key not in used_keys
    ]
    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT)
    rng.shuffle(games)
    if games:
        return games[0]
    return None


def _select_other_day_classic_media_release(now, used_keys, kind):
    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT + 1)
    checked_dates = {(now.month, now.day)}
    for _ in range(CLASSIC_MEDIA_RANDOM_DATE_ATTEMPTS):
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        if (month, day) in checked_dates:
            continue
        checked_dates.add((month, day))

        games = [
            game
            for game in _fetch_classic_media_releases(kind, now, month, day)
            if game.key not in used_keys
        ]
        rng.shuffle(games)
        if games:
            return games[0]
    return None


def _select_classic_game(now, used_keys):
    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT)
    searches = list(CLASSIC_GAME_SEARCHES)
    rng.shuffle(searches)

    for source, query in searches:
        page_count = _fetch_classic_game_page_count(query)
        if page_count <= 0:
            continue

        pages = list(range(1, page_count + 1))
        rng.shuffle(pages)
        for page in pages[:CLASSIC_MEDIA_PAGE_ATTEMPTS]:
            games = [
                game
                for game in _fetch_classic_games(query, source, page)
                if game.key not in used_keys
            ]
            rng.shuffle(games)
            if games:
                return games[0]
    return None


def _select_classic_chinese_book(now, used_keys):
    query = CLASSIC_CHINESE_BOOK_SEARCH_TEMPLATE.format(
        max_year=now.year - CLASSIC_MEDIA_MIN_AGE_YEARS
    )
    page_count = _fetch_classic_game_page_count(query)
    if page_count <= 0:
        return None

    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT + 2)
    pages = list(range(1, min(page_count, CLASSIC_CHINESE_BOOK_PAGE_LIMIT) + 1))
    rng.shuffle(pages)
    for page in pages[:CLASSIC_MEDIA_PAGE_ATTEMPTS]:
        books = [
            book
            for book in _fetch_classic_chinese_books(now, page)
            if book.key not in used_keys
        ]
        rng.shuffle(books)
        if books:
            return books[0]
    return None


def _format_release_age(release_date, now=None):
    if not release_date:
        return ""

    current_time = now or _now()
    text = str(release_date)
    exact_date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if exact_date_match:
        year = int(exact_date_match.group(1))
        month = int(exact_date_match.group(2))
        day = int(exact_date_match.group(3))
        years_ago = current_time.year - year
        if month == current_time.month and day == current_time.day:
            return f"{text}（{years_ago} 年前的今天）"
        return f"{text}（{years_ago} 年前）"

    year = _release_year(text)
    if year is None:
        return text
    return f"{text}（{current_time.year - year} 年前）"


def _classic_game_display_title(game):
    if game.chinese_title and game.chinese_title != game.title:
        return f"{game.chinese_title}（{game.title}）"
    return game.title


def _format_classic_game_intro(game, now=None):
    lines = [
        f"good old days：{game.media_label}",
        "",
        f"• [{_classic_game_display_title(game)}]({game.url})",
    ]

    meta_parts = [game.source]
    if game.release_date:
        meta_parts.append(_format_release_age(game.release_date, now))
    elif game.year:
        meta_parts.append(game.year)
    if game.creator:
        meta_parts.append(game.creator)
    if game.downloads > 0:
        meta_parts.append(f"{game.downloads} downloads")
    lines.append(" / ".join(meta_parts))

    if game.chinese_title and game.chinese_title != game.title:
        lines.append(f"中文名：{game.chinese_title}")

    if game.description:
        lines.append(f"简介：{game.description}")
    else:
        lines.append(f"简介：NeoDB 收录的{game.media_label}。")

    if game.source == "Wikidata":
        lines.append(
            f"Wikidata：[{game.identifier.removeprefix('wikidata-')}]({game.url})"
        )
    elif game.source == "NeoDB":
        neodb_id = game.identifier.removeprefix(f"neodb-{game.media_type}-")
        lines.append(f"NeoDB：[{neodb_id}]({game.url})")
    else:
        lines.append(f"Archive：[{game.identifier}]({game.archive_url})")
        if game.wikidata_url and game.chinese_title:
            lines.append(f"中文名来源：[Wikidata]({game.wikidata_url})")
    if game.external_url:
        lines.append(
            f"外部条目：[{_extract_domain(game.external_url)}]({game.external_url})"
        )
    return "\n".join(lines)


def _select_classic_media_kind(now):
    rng = _daily_rng(now, CLASSIC_MEDIA_RANDOM_SALT)
    return rng.choice(CLASSIC_MEDIA_KINDS)


def get_classic_media_intro():
    try:
        now = _now()
        used_keys = _load_used_classic_media()
        kind = _select_classic_media_kind(now)
        game = _select_neodb_media(now, used_keys, kind)
        if game is None:
            return ""

        _save_used_classic_media(game.key)
        return _format_classic_game_intro(game, now)
    except Exception as error:
        print(f"Error getting classic media: {error}")
        return ""


def get_classic_game_intro():
    return get_classic_media_intro()


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


def get_history_today(birth_year=BIRTH_YEAR, limit=1):
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

        rng = _daily_rng(now)
        selected_events = rng.sample(filtered_events, min(limit, len(filtered_events)))
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


def _normalize_link_domain(url):
    if not isinstance(url, str) or not url:
        return ""

    try:
        domain = urlparse(url).hostname or ""
    except Exception:
        return ""

    domain = domain.lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _is_same_link_site(original_url, final_url):
    original_domain = _normalize_link_domain(original_url)
    final_domain = _normalize_link_domain(final_url)
    if not original_domain or not final_domain:
        return False
    return (
        original_domain == final_domain
        or original_domain.endswith(f".{final_domain}")
        or final_domain.endswith(f".{original_domain}")
    )


def _response_stays_on_link_site(original_url, response):
    final_url = getattr(response, "url", None)
    if not isinstance(final_url, str) or not final_url:
        final_url = original_url
    return _is_same_link_site(original_url, final_url)


def _load_used_sites():
    return set(_read_non_empty_lines(_data_file_path(BLOG_SITES_USED_FILE)))


def _save_used_site(domain):
    _append_line(_data_file_path(BLOG_SITES_USED_FILE), domain)


def _check_link_available(url, timeout=10):
    if not url:
        return False

    headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
    try:
        response = requests.head(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        if not _response_stays_on_link_site(url, response):
            return False
        if 200 <= response.status_code < 400:
            return True
        if response.status_code not in {403, 405, 406, 429, 500, 501}:
            return False

        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        try:
            return (
                _response_stays_on_link_site(url, response)
                and 200 <= response.status_code < 400
            )
        finally:
            response.close()
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


def _get_article_year(selected):
    article_date = _get_article_date(selected)
    if not article_date:
        return None

    try:
        return int(article_date.split("-")[0])
    except (ValueError, IndexError):
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


def _select_blog_article_from_history(now):
    used_sites = _load_used_sites()
    all_articles = _collect_today_articles(now.month, now.day)

    if not all_articles:
        print("No articles found for today, trying random date...")
        all_articles = _collect_random_articles(now)
    if not all_articles:
        return None, used_sites

    return _select_blog_article(all_articles, used_sites, now), used_sites


def _get_blog_article_from_history_parts(now=None):
    try:
        current_time = now or _now()
        selected, used_sites = _select_blog_article_from_history(current_time)
        if not selected:
            return "", None

        domain = selected.get("domain", "")
        if domain and domain not in used_sites:
            _save_used_site(domain)

        return (
            _format_blog_article(selected, current_time.year),
            _get_article_year(selected),
        )
    except Exception as error:
        print(f"Error getting blog article: {error}")
        return "", None


def get_blog_article_from_history():
    """
    获取历史上今天的博客文章 (2005-2025年)

    从 saveweb.org API 随机获取一篇历史上今天发布的博客文章
    会记录已使用的网站域名，尽量返回不同网站的文章
    如果当天没有文章，则随机搜索其他日期
    """
    article, _article_year = _get_blog_article_from_history_parts()
    return article


def _extract_blog_year_from_text(blog_article):
    match = re.search(r"\((\d{4})-\d{2}-\d{2}\)", blog_article)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


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
    city_info, city_name, _ = get_random_city()
    city_poster_path = _generate_city_poster(city_name) if city_name else ""
    city_map_path = _generate_cities_map(city_name) if city_name else ""
    blog_article, _blog_year = _get_blog_article_from_history_parts(current_time)
    return GetUpMessageParts(
        is_get_up_early=_is_get_up_early(current_time),
        day_of_year=get_day_of_year(current_time),
        year_progress=get_year_progress(current_time),
        running_info=get_running_distance(),
        history_today=get_classic_media_intro(),
        leetcode=get_daily_leetcode(),
        blog_article=blog_article,
        city_info=city_info,
        city_poster_path=city_poster_path,
        city_map_path=city_map_path,
    )


def _send_telegram_message(
    body, tele_token, tele_chat_id, poster_path="", city_info="", map_path=""
):
    if not tele_token or not tele_chat_id:
        return

    bot = telebot.TeleBot(tele_token)
    try:
        telegram_body = _build_telegram_body(body)
        poster_file = Path(poster_path) if poster_path else None
        map_file = Path(map_path) if map_path else None
        has_poster = bool(poster_file and poster_file.exists())
        has_map = bool(map_file and map_file.exists())

        if has_poster and has_map:
            from telebot.types import InputMediaPhoto

            poster_bytes = poster_file.read_bytes()
            map_bytes = map_file.read_bytes()

            if _can_send_as_single_telegram_photo_post(telegram_body):
                media = [
                    InputMediaPhoto(
                        poster_bytes,
                        caption=telegram_body,
                        parse_mode="MarkdownV2",
                    ),
                    InputMediaPhoto(map_bytes),
                ]
                bot.send_media_group(
                    tele_chat_id,
                    media,
                    disable_notification=True,
                )
            else:
                bot.send_message(
                    tele_chat_id,
                    telegram_body,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
                caption = markdownify(city_info).strip() if city_info else None
                media = [
                    InputMediaPhoto(
                        poster_bytes,
                        caption=caption,
                        parse_mode="MarkdownV2" if caption else None,
                    ),
                    InputMediaPhoto(map_bytes),
                ]
                bot.send_media_group(
                    tele_chat_id,
                    media,
                    disable_notification=True,
                )
        elif has_poster:
            if _can_send_as_single_telegram_photo_post(telegram_body):
                with poster_file.open("rb") as photo:
                    bot.send_photo(
                        tele_chat_id,
                        photo,
                        caption=telegram_body,
                        parse_mode="MarkdownV2",
                        disable_notification=True,
                    )
            else:
                bot.send_message(
                    tele_chat_id,
                    telegram_body,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
                caption = markdownify(city_info).strip() if city_info else None
                with poster_file.open("rb") as photo:
                    bot.send_photo(
                        tele_chat_id,
                        photo,
                        caption=caption or None,
                        parse_mode="MarkdownV2" if caption else None,
                        disable_notification=True,
                    )
        elif has_map:
            if _can_send_as_single_telegram_photo_post(telegram_body):
                caption = telegram_body
            else:
                bot.send_message(
                    tele_chat_id,
                    telegram_body,
                    parse_mode="MarkdownV2",
                    disable_notification=True,
                )
                caption = markdownify(city_info).strip() if city_info else None

            with map_file.open("rb") as photo:
                bot.send_photo(
                    tele_chat_id,
                    photo,
                    caption=caption or None,
                    parse_mode="MarkdownV2" if caption else None,
                    disable_notification=True,
                )
        else:
            bot.send_message(
                tele_chat_id,
                telegram_body,
                parse_mode="MarkdownV2",
                disable_notification=True,
            )
    except Exception as error:
        print(str(error))


def _build_telegram_body(body):
    formatted_body = markdownify(body).rstrip()
    morning_tag = markdownify(TG_MORNING_TAG).strip()
    return f"{formatted_body}\n\n{morning_tag}"


def _can_send_as_single_telegram_photo_post(telegram_body):
    return len(telegram_body) <= TELEGRAM_CAPTION_LIMIT


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

    _send_telegram_message(
        body,
        tele_token,
        tele_chat_id,
        message_parts.city_poster_path,
        message_parts.city_info,
        message_parts.city_map_path,
    )
    issue.create_comment(body)


def cli(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("github_token", help="github_token")
    parser.add_argument("repo_name", help="repo_name")
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


if __name__ == "__main__":
    cli()
