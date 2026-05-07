import argparse
import random
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
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
CJK_FONT_FAMILY_KEYWORDS = (
    "Noto Sans CJK",
    "Noto Sans SC",
    "Noto Serif CJK",
    "Source Han Sans",
    "Source Han Serif",
    "WenQuanYi",
    "Arial Unicode",
    "Hiragino Sans GB",
    "Heiti SC",
    "Songti SC",
)
CJK_FONT_FILE_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

LEETCODE_EASY_FILE = "data/leetcode_easy.txt"
LEETCODE_USED_FILE = "data/leetcode_used.txt"
LEETCODE_HOT100_FILE = "data/leetcode_hot100.txt"
LEETCODE_HOT100_USED_FILE = "data/leetcode_hot100_used.txt"
BLOG_SITES_USED_FILE = "data/blog_sites_used.txt"
CHINESE_CITIES_FILE = "data/chinese_cities.txt"
CITIES_USED_FILE = "data/cities_used.txt"

CITY_WIKI_BASE_URL = "https://zh.wikipedia.org/wiki/{city}"
CITY_RANDOM_SALT = 77

TIMEZONE = "Asia/Shanghai"
SCRIPT_DIR = Path(__file__).resolve().parent
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
BLOG_HISTORY_START_YEAR = 2005
BLOG_HISTORY_END_YEAR = 2025
BLOG_RANDOM_SEARCH_ATTEMPTS = 5
BLOG_LINK_CHECK_ATTEMPTS = 10
HOT100_RANDOM_SALT = 42
BLOG_RANDOM_SALT = 99
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

        if not results and _daily_rng(now, HOT100_RANDOM_SALT).random() < 0.5:
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
    for candidate in LOCAL_CJK_FONT_FILE_CANDIDATES:
        path = SCRIPT_DIR / candidate
        if path.exists():
            return path

    font_file = _find_fontconfig_cjk_font()
    if font_file is not None:
        return font_file

    for candidate in CJK_FONT_FILE_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _find_fontconfig_cjk_font():
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "-f", "%{family}\t%{file}\n"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    fallback = None
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        family, _, file_path = line.partition("\t")
        path = Path(file_path.strip())
        if not file_path or not path.exists():
            continue
        if fallback is None:
            fallback = path
        normalized_family = family.lower()
        if any(
            keyword.lower() in normalized_family for keyword in CJK_FONT_FAMILY_KEYWORDS
        ):
            return path
    return fallback


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


def _compute_label_offsets(lons, lats, labels, ax, fontsize=9):
    import math

    n = len(lons)
    if n == 0:
        return []
    fig = ax.get_figure()
    dpi = fig.dpi
    pts_to_px = dpi / 72.0
    avg_char_w = fontsize * 0.65 * pts_to_px
    label_h = fontsize * 1.6 * pts_to_px
    display_pts = [ax.transData.transform((lon, lat)) for lon, lat in zip(lons, lats)]
    label_widths = [len(lbl) * avg_char_w + 10 * pts_to_px for lbl in labels]
    base_angles = [30, 330, 60, 300, 0, 90, 270, 150, 210, 120, 240, 180]
    candidates = []
    for dist in (10, 20, 34, 52, 74):
        for angle_deg in base_angles:
            rad = math.radians(angle_deg)
            candidates.append(
                (round(dist * math.cos(rad), 1), round(dist * math.sin(rad), 1))
            )
    dot_radius = 8
    dot_boxes = [
        (px - dot_radius, py - dot_radius, px + dot_radius, py + dot_radius)
        for px, py in display_pts
    ]
    offsets = [None] * n
    placed_boxes = []
    proximity_threshold = 18 * pts_to_px
    for i in range(n):
        px, py = display_pts[i]
        w = label_widths[i]
        h = label_h
        best_offset = candidates[0]
        best_cost = float("inf")
        best_box = (0.0, 0.0, 0.0, 0.0)
        for dx, dy in candidates:
            dx_px = dx * pts_to_px
            dy_px = dy * pts_to_px
            lx = (px + dx_px - w) if dx < 0 else (px + dx_px)
            ly = py + dy_px
            box = (lx, ly, lx + w, ly + h)
            cost = 0.0
            for pb in placed_boxes:
                overlap = _rect_overlap_area(box, pb)
                if overlap > 0:
                    cost += overlap * 10
                else:
                    cx1 = (box[0] + box[2]) * 0.5
                    cy1 = (box[1] + box[3]) * 0.5
                    cx2 = (pb[0] + pb[2]) * 0.5
                    cy2 = (pb[1] + pb[3]) * 0.5
                    d = math.hypot(cx1 - cx2, cy1 - cy2)
                    if d < proximity_threshold:
                        cost += (proximity_threshold - d) * 0.5
            for j, db in enumerate(dot_boxes):
                if j != i:
                    cost += _rect_overlap_area(box, db) * 5
            cost += math.hypot(dx, dy) * 0.15
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
    view_min_lat = max(15, min_lat - pad_lat)
    view_max_lat = min(55, max_lat + pad_lat)

    world = _load_world_geodata()

    fig = Figure(figsize=(12, 8), dpi=150)
    fig.set_facecolor("#F8FAFC")
    canvas = FigureCanvasAgg(fig)
    ax = fig.subplots(1, 1)
    ax.set_facecolor("#DDECF8")

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
            color="#ECE9E1",
            edgecolor="#B0B5BB",
            linewidth=0.6,
            zorder=1,
        )

    # Other cities
    if other_coords:
        ax.scatter(
            [c[2] for c in other_coords],
            [c[1] for c in other_coords],
            s=80,
            c="#E76F51",
            alpha=0.9,
            edgecolors="#FFFFFF",
            linewidths=1.5,
            zorder=3,
        )

    # Today city
    if today_coords:
        ax.scatter(
            [c[2] for c in today_coords],
            [c[1] for c in today_coords],
            s=220,
            c="#F4A261",
            alpha=0.95,
            edgecolors="#264653",
            linewidths=2.0,
            zorder=5,
        )

    lons = [c[2] for c in city_coords]
    lats = [c[1] for c in city_coords]
    labels = [c[0] for c in city_coords]

    label_offsets = _compute_label_offsets(lons, lats, labels, ax, fontsize=9)
    for lon, lat, label, offset in zip(lons, lats, labels, label_offsets):
        is_today = label == today_city
        ha = "left" if offset[0] >= 0 else "right"
        ann = dict(
            textcoords="offset points",
            xytext=offset,
            fontsize=10 if is_today else 9,
            fontweight="bold",
            color="#1F2937" if is_today else "#264653",
            ha=ha,
            bbox=dict(
                boxstyle="round,pad=0.35" if is_today else "round,pad=0.3",
                facecolor="#FEF3C7" if is_today else "white",
                alpha=0.98 if is_today else 0.95,
                edgecolor="#F59E0B" if is_today else "#A8B0BA",
                linewidth=1.2 if is_today else 0.8,
            ),
            arrowprops=dict(
                arrowstyle="-",
                color="#F59E0B" if is_today else "#B0B5BB",
                linewidth=0.8 if is_today else 0.5,
                shrinkA=0,
                shrinkB=3,
            ),
            zorder=6 if is_today else 4,
        )
        ax.annotate(label, (lon, lat), **ann)

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(view_min_lon, view_max_lon)
    ax.set_ylim(view_min_lat, view_max_lat)
    ax.grid(color="#CBD5E1", linestyle="--", linewidth=0.6, alpha=0.55, zorder=0)
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
    ax.set_xlabel("经度", fontsize=10, color="#334155")
    ax.set_ylabel("纬度", fontsize=10, color="#334155")
    ax.tick_params(labelsize=8, colors="#64748B")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#B8C0CA")
    ax.spines["bottom"].set_color("#B8C0CA")

    fig.tight_layout(pad=1.0)
    output_dir = SCRIPT_DIR / CITY_POSTERS_DIR
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / CITY_MAP_FILE
    canvas.print_png(str(output_path))
    return str(output_path)


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
    city_info, city_name, _ = get_random_city()
    city_poster_path = _generate_city_poster(city_name) if city_name else ""
    city_map_path = _generate_cities_map(city_name) if city_name else ""
    return GetUpMessageParts(
        is_get_up_early=_is_get_up_early(current_time),
        day_of_year=get_day_of_year(current_time),
        year_progress=get_year_progress(current_time),
        running_info=get_running_distance(),
        history_today=get_history_today(),
        leetcode=get_daily_leetcode(),
        blog_article=get_blog_article_from_history(),
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
