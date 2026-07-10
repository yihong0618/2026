import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pendulum

from y2026 import get_up


class FakeBot:
    instances: list["FakeBot"] = []

    def __init__(self, token):
        self.token = token
        self.calls = []
        self.__class__.instances.append(self)

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", chat_id, text, kwargs))

    def send_photo(self, chat_id, photo, **kwargs):
        payload = photo.read() if hasattr(photo, "read") else photo
        self.calls.append(("send_photo", chat_id, payload, kwargs))

    def send_media_group(self, chat_id, media, **kwargs):
        self.calls.append(("send_media_group", chat_id, media, kwargs))


class GetUpTelegramTests(unittest.TestCase):
    def setUp(self):
        FakeBot.instances.clear()

    def test_map_only_sends_body_as_caption_when_it_fits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / "cities_map.png"
            map_path.write_bytes(b"map")

            with mock.patch.object(get_up.telebot, "TeleBot", FakeBot):
                get_up._send_telegram_message(
                    "hello",
                    "token",
                    "chat",
                    map_path=str(map_path),
                )

        bot = FakeBot.instances[-1]
        self.assertEqual([call[0] for call in bot.calls], ["send_photo"])
        self.assertEqual(
            bot.calls[0][3]["caption"],
            get_up._build_telegram_body("hello"),
        )
        self.assertEqual(bot.calls[0][3]["parse_mode"], "MarkdownV2")

    def test_map_only_sends_message_first_when_caption_is_too_long(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / "cities_map.png"
            map_path.write_bytes(b"map")
            body = "a" * (get_up.TELEGRAM_CAPTION_LIMIT + 10)
            city_info = "今日城市 🏙️：[江苏·徐州](https://example.com)"

            with mock.patch.object(get_up.telebot, "TeleBot", FakeBot):
                get_up._send_telegram_message(
                    body,
                    "token",
                    "chat",
                    city_info=city_info,
                    map_path=str(map_path),
                )

        bot = FakeBot.instances[-1]
        self.assertEqual(
            [call[0] for call in bot.calls],
            ["send_message", "send_photo"],
        )
        self.assertEqual(
            bot.calls[0][2],
            get_up._build_telegram_body(body),
        )
        self.assertEqual(
            bot.calls[1][3]["caption"],
            get_up.markdownify(city_info).strip(),
        )
        self.assertEqual(bot.calls[1][3]["parse_mode"], "MarkdownV2")


class GetUpRunningTests(unittest.TestCase):
    def test_running_distance_uses_previous_month_on_first_day(self):
        response = mock.Mock(ok=True, content=b"parquet")
        now = pendulum.datetime(2026, 6, 1, 7, tz=get_up.TIMEZONE)

        with (
            mock.patch.object(get_up.requests, "get", return_value=response),
            mock.patch.object(get_up, "_now", return_value=now),
            mock.patch.object(
                get_up,
                "_query_running_summary",
                side_effect=[(1, 5.0), (8, 42.5), (20, 180.0)],
            ) as query_summary,
        ):
            result = get_up.get_running_distance()

        self.assertIn("• 昨天跑了 5.0 公里", result)
        self.assertIn("• 上个月跑了 42.5 公里", result)
        self.assertNotIn("• 本月", result)
        self.assertEqual(
            query_summary.call_args_list[1].args[2],
            "start_date_local >= '2026-05-01' AND start_date_local < '2026-06-01'",
        )


class GetUpGeocodeTests(unittest.TestCase):
    def test_geocode_city_uses_center_override_before_cache(self):
        original_script_dir = get_up.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                get_up.SCRIPT_DIR = Path(tmpdir)
                get_up._set_cached_geocode("丹东", 40.5791477, 124.4407146)

                with mock.patch.object(get_up.requests, "get") as get:
                    result = get_up._geocode_city("丹东")

                self.assertEqual(result, get_up.CITY_CENTER_COORD_OVERRIDES["丹东"])
                get.assert_not_called()
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_geocode_city_prefers_place_city_over_admin_boundary(self):
        original_script_dir = get_up.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                get_up.SCRIPT_DIR = Path(tmpdir)
                response = mock.Mock()
                response.json.return_value = [
                    {
                        "lat": "40.5791477",
                        "lon": "124.4407146",
                        "class": "boundary",
                        "type": "administrative",
                        "addresstype": "region",
                    },
                    {
                        "lat": "40.1237658",
                        "lon": "124.3821748",
                        "class": "place",
                        "type": "city",
                        "addresstype": "city",
                    },
                ]

                with (
                    mock.patch.object(
                        get_up.requests, "get", return_value=response
                    ) as get,
                    mock.patch.object(get_up.time, "sleep"),
                ):
                    result = get_up._geocode_city("测试城")

                self.assertEqual(result, (40.1237658, 124.3821748))
                self.assertEqual(get.call_args.kwargs["params"]["limit"], "10")
                self.assertEqual(
                    get_up._get_cached_geocode("测试城"),
                    (40.1237658, 124.3821748),
                )
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_geocode_city_tries_fallback_query_before_caching_weak_result(self):
        original_script_dir = get_up.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                get_up.SCRIPT_DIR = Path(tmpdir)
                weak_response = mock.Mock()
                weak_response.json.return_value = [
                    {
                        "lat": "40.0",
                        "lon": "120.0",
                        "class": "boundary",
                        "type": "administrative",
                        "addresstype": "region",
                    }
                ]
                city_response = mock.Mock()
                city_response.json.return_value = [
                    {
                        "lat": "40.5",
                        "lon": "120.5",
                        "class": "place",
                        "type": "city",
                        "addresstype": "city",
                    }
                ]

                with (
                    mock.patch.object(
                        get_up.requests,
                        "get",
                        side_effect=[weak_response, city_response],
                    ) as get,
                    mock.patch.object(get_up.time, "sleep"),
                ):
                    result = get_up._geocode_city("测试城")

                self.assertEqual(result, (40.5, 120.5))
                self.assertEqual(get.call_count, 2)
                self.assertEqual(get_up._get_cached_geocode("测试城"), (40.5, 120.5))
            finally:
                get_up.SCRIPT_DIR = original_script_dir


class GetUpPosterTests(unittest.TestCase):
    def test_resolve_city_poster_font_prefers_local_fonts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            original_local_candidates = get_up.LOCAL_CJK_FONT_FILE_CANDIDATES
            get_up.SCRIPT_DIR = Path(tmpdir)
            get_up.LOCAL_CJK_FONT_FILE_CANDIDATES = ("fonts/local-cn.ttf",)
            try:
                font_path = Path(tmpdir) / "fonts" / "local-cn.ttf"
                font_path.parent.mkdir()
                font_path.write_bytes(b"font")

                result = get_up._resolve_city_poster_font_file()

                self.assertEqual(result, font_path)
            finally:
                get_up.SCRIPT_DIR = original_script_dir
                get_up.LOCAL_CJK_FONT_FILE_CANDIDATES = original_local_candidates

    def test_resolve_city_poster_font_uses_module_fonts_with_isolated_script_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            original_module_dir = get_up.MODULE_DIR
            original_local_candidates = get_up.LOCAL_CJK_FONT_FILE_CANDIDATES
            isolated_dir = Path(tmpdir) / "isolated"
            module_dir = Path(tmpdir) / "module"
            get_up.SCRIPT_DIR = isolated_dir
            get_up.MODULE_DIR = module_dir
            get_up.LOCAL_CJK_FONT_FILE_CANDIDATES = ("fonts/module-cn.ttf",)
            try:
                font_path = module_dir / "fonts" / "module-cn.ttf"
                font_path.parent.mkdir(parents=True)
                font_path.write_bytes(b"font")

                result = get_up._resolve_city_poster_font_file()

                self.assertEqual(result, font_path)
            finally:
                get_up.SCRIPT_DIR = original_script_dir
                get_up.MODULE_DIR = original_module_dir
                get_up.LOCAL_CJK_FONT_FILE_CANDIDATES = original_local_candidates

    def test_generate_city_poster_reuses_cached_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                poster_path = get_up._city_poster_output_path("徐州")
                poster_path.write_bytes(b"poster")

                with mock.patch.object(get_up, "generate_poster") as generate_poster:
                    result = get_up._generate_city_poster("徐州")

                self.assertEqual(result, str(poster_path))
                generate_poster.assert_not_called()
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_generate_city_poster_retries_until_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                poster_path = get_up._city_poster_output_path("徐州")

                result_obj = mock.Mock()
                result_obj.files = [poster_path]

                with (
                    mock.patch.object(
                        get_up,
                        "generate_poster",
                        side_effect=[
                            RuntimeError("timeout-1"),
                            RuntimeError("timeout-2"),
                            result_obj,
                        ],
                    ) as generate_poster,
                    mock.patch.object(
                        get_up, "_geocode_city", return_value=(34.2044, 117.2841)
                    ),
                    mock.patch.object(get_up.time, "sleep") as sleep,
                ):
                    result = get_up._generate_city_poster("徐州")

                self.assertEqual(result, str(poster_path))
                self.assertEqual(generate_poster.call_count, 3)
                sleep.assert_has_calls([mock.call(2), mock.call(4)])
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_generate_city_poster_uses_city_name_as_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                data_dir = Path(tmpdir) / "data"
                data_dir.mkdir()
                (data_dir / "chinese_cities.txt").write_text(
                    "榆林|榆林市|陕西省\n", encoding="utf-8"
                )
                poster_path = get_up._city_poster_output_path("榆林")

                result_obj = mock.Mock()
                result_obj.files = [poster_path]

                with (
                    mock.patch.object(
                        get_up, "_geocode_city", return_value=(38.2851, 109.7290)
                    ),
                    mock.patch.object(
                        get_up, "generate_poster", return_value=result_obj
                    ) as generate_poster,
                ):
                    result = get_up._generate_city_poster("榆林")

                request = generate_poster.call_args.args[0]
                self.assertEqual(result, str(poster_path))
                self.assertEqual(request.title, "榆林")
                self.assertEqual(request.subtitle, "陕西省, 中国")
                self.assertEqual(request.lat, 38.2851)
                self.assertEqual(request.lon, 109.7290)
                self.assertIsNone(request.location)
            finally:
                get_up.SCRIPT_DIR = original_script_dir


class GetUpMapTests(unittest.TestCase):
    def test_filter_china_admin1_keeps_taiwan_records(self):
        import pandas as pd

        admin1 = pd.DataFrame(
            {
                "adm0_a3": ["CHN", "TWN", "JPN"],
                "admin": ["China", "Taiwan", "Japan"],
            }
        )

        china = get_up._filter_china_admin1(admin1)

        self.assertEqual(china["adm0_a3"].tolist(), ["CHN", "TWN"])

    def test_render_cities_map_draws_city_points_and_offset_labels(self):
        from matplotlib.axes import Axes

        original_script_dir = get_up.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                get_up.SCRIPT_DIR = Path(tmpdir)
                city_coords = [
                    ("北京", 39.9042, 116.4074),
                    ("上海", 31.2304, 121.4737),
                    ("百色", 23.9054, 106.6149),
                ]
                with (
                    mock.patch.object(get_up, "_load_china_geodata", return_value=None),
                    mock.patch.object(get_up, "_load_world_geodata", return_value=None),
                    mock.patch.object(
                        get_up,
                        "_compute_label_offsets",
                        return_value=[(9, 9), (-9, 9), (12, -12)],
                    ) as compute_offsets,
                    mock.patch.object(Axes, "scatter", autospec=True) as scatter,
                ):
                    result = get_up._render_cities_map(
                        city_coords, today_city="百色", recent_cities=("上海", "百色")
                    )

                self.assertEqual(
                    result,
                    str(Path(tmpdir) / get_up.CITY_POSTERS_DIR / get_up.CITY_MAP_FILE),
                )
                self.assertTrue(Path(result).exists())
                self.assertEqual(scatter.call_count, 3)
                compute_offsets.assert_called_once()
                args, _kwargs = compute_offsets.call_args
                self.assertEqual(args[0], [116.4074, 121.4737, 106.6149])
                self.assertEqual(args[1], [39.9042, 31.2304, 23.9054])
                self.assertEqual(args[2], ["北京", "上海", "百色"])
                specs = args[4]
                self.assertEqual(
                    [spec["kind"] for spec in specs],
                    ["regular", "recent", "today"],
                )
                self.assertFalse(specs[0]["has_box"])
                self.assertTrue(specs[1]["has_box"])
                self.assertTrue(specs[2]["has_box"])
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_compute_label_offsets_separates_dense_labels(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        get_up._setup_matplotlib_font()

        labels = ["北京", "天津", "廊坊", "唐山", "保定", "沧州", "济南", "德州"]
        lons = [116.40, 117.20, 116.70, 118.18, 115.48, 116.84, 117.12, 116.36]
        lats = [39.90, 39.12, 39.53, 39.63, 38.87, 38.31, 36.65, 37.43]

        fig = Figure(figsize=(8, 5), dpi=120)
        canvas = FigureCanvasAgg(fig)
        ax = fig.subplots(1, 1)
        ax.set_xlim(114, 120)
        ax.set_ylim(35.8, 40.6)
        ax.set_aspect("equal", adjustable="box")
        canvas.draw()

        specs = get_up._city_label_specs(labels, "北京", ("北京", "天津", "廊坊"))
        offsets = get_up._compute_label_offsets(lons, lats, labels, ax, specs)

        pts_to_px = fig.dpi / 72.0
        label_sizes = get_up._measure_label_sizes(labels, ax, specs)
        boxes = []
        for lon, lat, label_size, offset in zip(lons, lats, label_sizes, offsets):
            px, py = ax.transData.transform((lon, lat))
            boxes.append(
                get_up._label_box_for_offset(
                    px,
                    py,
                    label_size[0],
                    label_size[1],
                    offset[0],
                    offset[1],
                    pts_to_px,
                )
            )

        for i, box in enumerate(boxes):
            for other in boxes[i + 1 :]:
                self.assertEqual(get_up._rect_overlap_area(box, other), 0.0)


class GetUpLeetCodeTests(unittest.TestCase):
    def test_pick_problem_from_pool_skips_slugs_used_by_another_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            problem_file = data_dir / "pool.txt"
            used_file = data_dir / "leetcode_used.txt"
            other_used_file = data_dir / "leetcode_hot100_used.txt"
            problem_file.write_text(
                "\n".join(
                    [
                        "1|Seen Elsewhere|seen-elsewhere|EASY",
                        "2|Fresh Problem|fresh-problem|EASY",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            used_file.write_text("", encoding="utf-8")
            other_used_file.write_text("seen-elsewhere\n", encoding="utf-8")

            selected = get_up._pick_problem_from_pool(
                problem_file,
                used_file,
                pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE),
                (used_file, other_used_file),
            )

            self.assertEqual(selected.slug, "fresh-problem")
            self.assertEqual(
                used_file.read_text(encoding="utf-8").splitlines(),
                ["fresh-problem"],
            )

    def test_daily_leetcode_skips_daily_question_used_by_hot100(self):
        class StableRng:
            def random(self):
                return 0.6

            def choice(self, values):
                return values[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                data_dir = Path(tmpdir) / "data"
                data_dir.mkdir()
                (data_dir / "leetcode_easy.txt").write_text(
                    "\n".join(
                        [
                            "1|Daily Used|daily-used|EASY",
                            "2|Fresh Easy|fresh-easy|EASY",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (data_dir / "leetcode_used.txt").write_text("", encoding="utf-8")
                (data_dir / "leetcode_hot100.txt").write_text("", encoding="utf-8")
                (data_dir / "leetcode_hot100_used.txt").write_text(
                    "daily-used\n",
                    encoding="utf-8",
                )
                daily_question = get_up.LeetCodeProblem(
                    problem_id="1",
                    title="Daily Used",
                    slug="daily-used",
                    difficulty="EASY",
                )

                with (
                    mock.patch.object(
                        get_up,
                        "_now",
                        return_value=pendulum.datetime(
                            2026,
                            5,
                            21,
                            tz=get_up.TIMEZONE,
                        ),
                    ),
                    mock.patch.object(
                        get_up,
                        "_get_leetcode_daily_question",
                        return_value=daily_question,
                    ),
                    mock.patch.object(get_up, "_daily_rng", return_value=StableRng()),
                ):
                    result = get_up.get_daily_leetcode()

                self.assertIn("Fresh Easy", result)
                self.assertNotIn("Daily Used", result)
                self.assertEqual(
                    (data_dir / "leetcode_used.txt")
                    .read_text(encoding="utf-8")
                    .splitlines(),
                    ["fresh-easy"],
                )
            finally:
                get_up.SCRIPT_DIR = original_script_dir


class GetUpClassicGameTests(unittest.TestCase):
    def test_classic_media_from_wikidata_binding_uses_chinese_label_and_date(self):
        game = get_up._classic_media_from_wikidata_binding(
            {
                "item": {"value": "http://www.wikidata.org/entity/Q217423"},
                "release": {"value": "1998-05-24T00:00:00Z"},
                "zhLabel": {"value": "雷神之锤"},
                "enLabel": {"value": "Quake"},
                "enDescription": {"value": "1996 first-person shooter video game"},
            },
            get_up.CLASSIC_MEDIA_KINDS[0],
        )

        self.assertEqual(game.identifier, "wikidata-Q217423")
        self.assertEqual(game.title, "Quake")
        self.assertEqual(game.chinese_title, "雷神之锤")
        self.assertEqual(game.release_date, "1998-05-24")
        self.assertEqual(game.year, "1998")
        self.assertEqual(game.url, "https://www.wikidata.org/wiki/Q217423")

    def test_classic_game_from_doc_cleans_archive_fields(self):
        game = get_up._classic_game_from_doc(
            {
                "identifier": "Doom-2",
                "title": "Doom 2 [MS-DOS]",
                "creator": "Id Software",
                "year": "1994",
                "description": "<p>Video game <b>Doom 2</b> for MS-DOS.</p>",
                "downloads": "123",
            },
            "MS-DOS Games",
        )

        self.assertEqual(game.identifier, "Doom-2")
        self.assertEqual(game.title, "Doom 2 (MS-DOS)")
        self.assertEqual(game.creator, "Id Software")
        self.assertEqual(game.year, "1994")
        self.assertEqual(game.description, "Video game Doom 2 for MS-DOS.")
        self.assertEqual(game.downloads, 123)

    def test_get_classic_media_intro_returns_random_selected_tg_message(self):
        class StableRng:
            def choice(self, values):
                return values[1]

        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                (Path(tmpdir) / "data").mkdir()
                now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
                first = get_up.weekly_tg_summary.ChannelMessage(
                    message_id=101,
                    post="hyi0618/101",
                    url="https://t.me/hyi0618/101",
                    date=now,
                    text="第一篇 #selected",
                    links=("https://example.com/one",),
                    source="web",
                )
                second = get_up.weekly_tg_summary.ChannelMessage(
                    message_id=102,
                    post="hyi0618/102",
                    url="https://t.me/hyi0618/102",
                    date=now,
                    text="第二篇 #selected\nhttps://example.com/two#selected",
                    links=(
                        "https://t.me?q=%23selected",
                        "https://example.com/two#selected",
                    ),
                    source="web",
                )

                with (
                    mock.patch.object(get_up, "_now", return_value=now),
                    mock.patch.object(
                        get_up,
                        "_sync_selected_tg_message_pool",
                        return_value=[first, second],
                    ),
                    mock.patch.object(get_up, "_daily_rng", return_value=StableRng()),
                ):
                    result = get_up.get_classic_media_intro()

                self.assertIn("今天选读：", result)
                self.assertIn("[第二篇](https://t.me/hyi0618/102)", result)
                self.assertIn("链接：https://example.com/two", result)
                self.assertNotIn("#selected", result)
                self.assertNotIn("https://t.me?q=%23selected", result)
                self.assertNotIn("good old " + "days", result)
                self.assertEqual(
                    (Path(tmpdir) / get_up.SELECTED_TG_USED_FILE)
                    .read_text(encoding="utf-8")
                    .splitlines(),
                    ["hyi0618/102"],
                )
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_format_selected_tg_message_does_not_truncate_text_or_links(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        long_url = "https://example.com/" + "very-long-path-" * 30
        message = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text=f"一篇很长的文章 #selected\n{long_url}",
            links=(long_url,),
            source="web",
        )

        result = get_up._format_selected_tg_message(message)

        self.assertIn(long_url, result)
        self.assertNotIn("...", result)
        self.assertNotIn("…", result)

    def test_hydrate_selected_tg_reply_message_uses_full_replied_message(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        full_url = (
            "https://blog.mrcroxx.com/posts/"
            "foyer-a-hybrid-cache-in-rust-past-present-and-future/"
        )
        original = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text=f"Foyer: A Hybrid Cache in Rust\n{full_url}",
            links=(full_url,),
            source="web",
        )
        selected = get_up.weekly_tg_summary.ChannelMessage(
            message_id=102,
            post="hyi0618/102",
            url="https://t.me/hyi0618/102",
            date=now,
            text="Foyer: A Hybrid Cache in Rust https://blog.mrcroxx.com/posts/foyer-a-hybrid… #selected",
            links=("https://blog.mrcroxx.com/posts/foyer-a-hybrid…#selected",),
            source="web",
        )

        result = get_up._hydrate_selected_tg_reply_messages(
            [original, selected],
            {"hyi0618/102": "https://t.me/hyi0618/101"},
        )
        hydrated = result[1]

        self.assertEqual(hydrated.post, "hyi0618/102")
        self.assertIn(full_url, hydrated.text)
        self.assertEqual(hydrated.links, (full_url,))
        self.assertNotIn("foyer-a-hybrid…", hydrated.text)
        self.assertTrue(hydrated.has_tag(get_up.SELECTED_TG_TAG))

    def test_fetch_selected_tg_messages_since_filters_selected_tag(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        selected = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text="选中 #selected",
            links=(),
            source="web",
        )
        regular = get_up.weekly_tg_summary.ChannelMessage(
            message_id=102,
            post="hyi0618/102",
            url="https://t.me/hyi0618/102",
            date=now,
            text="普通消息",
            links=(),
            source="web",
        )

        with mock.patch.object(
            get_up,
            "_fetch_selected_tg_archive_page",
            side_effect=[[selected, regular], []],
        ) as fetch_selected_tg_archive_page:
            result = get_up._fetch_selected_tg_messages_since()

        self.assertEqual(result, [selected])
        self.assertEqual(fetch_selected_tg_archive_page.call_count, 2)
        self.assertEqual(
            fetch_selected_tg_archive_page.call_args_list[0].args[1],
            "hyi0618",
        )
        self.assertIsNone(fetch_selected_tg_archive_page.call_args_list[0].args[2])
        self.assertEqual(fetch_selected_tg_archive_page.call_args_list[1].args[2], 101)

    def test_fetch_selected_tg_messages_since_stops_after_cached_latest(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        cached = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text="已缓存 #selected",
            links=(),
            source="web",
        )
        new = get_up.weekly_tg_summary.ChannelMessage(
            message_id=103,
            post="hyi0618/103",
            url="https://t.me/hyi0618/103",
            date=now,
            text="新消息 #selected",
            links=(),
            source="web",
        )

        with mock.patch.object(
            get_up,
            "_fetch_selected_tg_archive_page",
            return_value=[new, cached],
        ) as fetch_selected_tg_archive_page:
            result = get_up._fetch_selected_tg_messages_since(101)

        self.assertEqual(result, [new])
        fetch_selected_tg_archive_page.assert_called_once()

    def test_sync_selected_tg_message_pool_saves_initial_cache(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        selected = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text="选中 #selected",
            links=("https://example.com/one",),
            source="web",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                with mock.patch.object(
                    get_up,
                    "_fetch_selected_tg_messages_since",
                    return_value=[selected],
                ) as fetch_selected_tg_messages_since:
                    result = get_up._sync_selected_tg_message_pool()

                self.assertEqual(result, [selected])
                fetch_selected_tg_messages_since.assert_called_once_with(None)
                cached = get_up._load_selected_tg_messages()
                self.assertEqual([message.post for message in cached], ["hyi0618/101"])
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_sync_selected_tg_message_pool_fetches_after_cached_latest(self):
        now = pendulum.datetime(2026, 5, 21, tz=get_up.TIMEZONE)
        cached = get_up.weekly_tg_summary.ChannelMessage(
            message_id=101,
            post="hyi0618/101",
            url="https://t.me/hyi0618/101",
            date=now,
            text="已缓存 #selected",
            links=(),
            source="web",
        )
        new = get_up.weekly_tg_summary.ChannelMessage(
            message_id=103,
            post="hyi0618/103",
            url="https://t.me/hyi0618/103",
            date=now,
            text="新消息 #selected",
            links=(),
            source="web",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                get_up._save_selected_tg_messages([cached])
                with mock.patch.object(
                    get_up,
                    "_fetch_selected_tg_messages_since",
                    return_value=[new],
                ) as fetch_selected_tg_messages_since:
                    result = get_up._sync_selected_tg_message_pool()

                self.assertEqual(
                    [message.post for message in result], ["hyi0618/101", "hyi0618/103"]
                )
                fetch_selected_tg_messages_since.assert_called_once_with(101)
                cached_messages = get_up._load_selected_tg_messages()
                self.assertEqual(
                    [message.post for message in cached_messages],
                    ["hyi0618/101", "hyi0618/103"],
                )
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_get_classic_media_intro_returns_empty_without_selected_tg_message(self):
        with mock.patch.object(
            get_up,
            "_sync_selected_tg_message_pool",
            return_value=[],
        ):
            self.assertEqual(get_up.get_classic_media_intro(), "")

    def test_select_neodb_media_prefers_same_day_release(self):
        now = pendulum.datetime(2026, 5, 24, tz=get_up.TIMEZONE)
        kind = get_up.CLASSIC_MEDIA_KINDS[0]
        details = {
            "old": {
                "uuid": "old",
                "title": "Old Game",
                "display_title": "Old Game",
                "description": "Old but not today.",
                "url": "/game/old",
                "developer": ["Old Studio"],
                "release_date": "1998-05-23",
            },
            "today": {
                "uuid": "today",
                "title": "Today Game",
                "display_title": "Today Game",
                "description": "Released today.",
                "url": "/game/today",
                "developer": ["Today Studio"],
                "release_date": "1997-05-24",
            },
        }

        with (
            mock.patch.object(
                get_up,
                "_fetch_neodb_trending_items",
                return_value=[{"uuid": "old"}, {"uuid": "today"}],
            ),
            mock.patch.object(
                get_up,
                "_fetch_neodb_item_detail",
                side_effect=lambda _kind, uuid: details[uuid],
            ),
        ):
            selected = get_up._select_neodb_media(now, set(), kind)

        self.assertEqual(selected.title, "Today Game")
        self.assertEqual(selected.release_date, "1997-05-24")

    def test_fetch_wikidata_chinese_title_uses_video_game_result(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "search": [
                {
                    "id": "Q755186",
                    "label": "毁灭战士II",
                    "description": "1994 first-person shooter video game",
                }
            ]
        }

        with mock.patch.object(get_up.requests, "get", return_value=response):
            title, url = get_up._fetch_wikidata_chinese_title("Doom II")

        self.assertEqual(title, "毁灭战士II")
        self.assertEqual(url, "https://www.wikidata.org/wiki/Q755186")

    def test_format_classic_game_intro_has_description_fallback(self):
        game = get_up.ClassicGame(
            identifier="arcade_example",
            title="Arcade Example",
            creator="",
            year="1986",
            description="",
            downloads=0,
            source="Internet Arcade",
        )

        result = get_up._format_classic_game_intro(game)

        self.assertIn("Internet Arcade / 1986", result)
        self.assertIn("NeoDB 收录", result)


if __name__ == "__main__":
    unittest.main()
