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


class GetUpHackerNewsHistoryTests(unittest.TestCase):
    def test_fetch_hacker_news_top_stories_for_date_sorts_by_points_and_limits(self):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "hits": [
                {
                    "objectID": "1",
                    "title": "Low",
                    "url": "https://example.com/low",
                    "points": 10,
                    "num_comments": 5,
                },
                {
                    "objectID": "2",
                    "title": "High",
                    "url": "https://example.com/high",
                    "points": 100,
                    "num_comments": 7,
                },
                {
                    "objectID": "3",
                    "title": "Middle",
                    "url": "https://example.com/middle",
                    "points": 50,
                    "num_comments": 30,
                },
            ]
        }

        with mock.patch.object(get_up.requests, "get", return_value=response) as get:
            stories = get_up._fetch_hacker_news_top_stories_for_date(2020, 5, 7)

        self.assertEqual([story.object_id for story in stories], ["2", "3", "1"])
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["tags"], "story")
        self.assertEqual(
            params["hitsPerPage"],
            str(get_up.HACKER_NEWS_STORIES_PER_PAGE),
        )
        self.assertIn("created_at_i>=", params["numericFilters"])
        self.assertIn("created_at_i<", params["numericFilters"])

    def test_get_hacker_news_history_skips_used_story_and_saves_new_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                (Path(tmpdir) / "data").mkdir()
                used_story = get_up.HackerNewsStory(
                    object_id="1",
                    title="Old story",
                    url="https://example.com/old",
                    points=100,
                    num_comments=20,
                    author="olduser",
                    created_at="2020-05-07T00:00:00Z",
                )
                new_story = get_up.HackerNewsStory(
                    object_id="2",
                    title="New story",
                    url="https://example.com/new",
                    points=200,
                    num_comments=30,
                    author="newuser",
                    created_at="2020-05-07T01:00:00Z",
                )
                used_path = Path(tmpdir) / get_up.HACKER_NEWS_USED_FILE
                used_path.write_text(f"{used_story.key}\n", encoding="utf-8")

                with (
                    mock.patch.object(
                        get_up,
                        "_now",
                        return_value=pendulum.datetime(2026, 5, 7, tz=get_up.TIMEZONE),
                    ),
                    mock.patch.object(
                        get_up,
                        "_hacker_news_candidate_years",
                        return_value=[2020],
                    ),
                    mock.patch.object(
                        get_up,
                        "_fetch_hacker_news_top_stories_for_date",
                        return_value=[used_story, new_story],
                    ),
                    mock.patch.object(
                        get_up,
                        "_is_hacker_news_story_link_available",
                        return_value=True,
                    ),
                ):
                    result = get_up.get_hacker_news_history()

                self.assertIn("HN 历史今日（2020-05-07）：", result)
                self.assertIn("• New story", result)
                self.assertIn(
                    "原文：[New story](https://example.com/new)",
                    result,
                )
                self.assertIn("[2](https://news.ycombinator.com/item?id=2)", result)
                used_keys = used_path.read_text(encoding="utf-8").splitlines()
                self.assertIn(used_story.key, used_keys)
                self.assertIn(new_story.key, used_keys)
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_get_hacker_news_history_uses_target_year_when_provided(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                (Path(tmpdir) / "data").mkdir()
                story = get_up.HackerNewsStory(
                    object_id="9",
                    title="Target year story",
                    url="https://example.com/target",
                    points=300,
                    num_comments=40,
                    author="targetuser",
                    created_at="2018-05-07T00:00:00Z",
                )

                with (
                    mock.patch.object(
                        get_up,
                        "_now",
                        return_value=pendulum.datetime(2026, 5, 7, tz=get_up.TIMEZONE),
                    ),
                    mock.patch.object(
                        get_up,
                        "_hacker_news_candidate_years",
                    ) as candidate_years,
                    mock.patch.object(
                        get_up,
                        "_fetch_hacker_news_top_stories_for_date",
                        return_value=[story],
                    ) as fetch_stories,
                    mock.patch.object(
                        get_up,
                        "_is_hacker_news_story_link_available",
                        return_value=True,
                    ),
                ):
                    result = get_up.get_hacker_news_history(2018)

                self.assertIn("HN 历史今日（2018-05-07）：", result)
                fetch_stories.assert_called_once_with(2018, 5, 7)
                candidate_years.assert_not_called()
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_get_hacker_news_history_skips_unavailable_story_link(self):
        class NoShuffleRng:
            def shuffle(self, values):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                (Path(tmpdir) / "data").mkdir()
                bad_story = get_up.HackerNewsStory(
                    object_id="1",
                    title="Unavailable story",
                    url="https://example.com/bad",
                    points=300,
                    num_comments=40,
                    author="baduser",
                    created_at="2018-05-07T00:00:00Z",
                )
                good_story = get_up.HackerNewsStory(
                    object_id="2",
                    title="Available story",
                    url="https://example.com/good",
                    points=250,
                    num_comments=35,
                    author="gooduser",
                    created_at="2018-05-07T01:00:00Z",
                )

                with (
                    mock.patch.object(
                        get_up,
                        "_now",
                        return_value=pendulum.datetime(2026, 5, 7, tz=get_up.TIMEZONE),
                    ),
                    mock.patch.object(
                        get_up,
                        "_daily_rng",
                        return_value=NoShuffleRng(),
                    ),
                    mock.patch.object(
                        get_up,
                        "_fetch_hacker_news_top_stories_for_date",
                        return_value=[bad_story, good_story],
                    ),
                    mock.patch.object(
                        get_up,
                        "_is_hacker_news_story_link_available",
                        side_effect=lambda story: story.object_id == "2",
                    ) as link_available,
                ):
                    result = get_up.get_hacker_news_history(2018)

                self.assertIn("• Available story", result)
                self.assertNotIn("Unavailable story", result)
                self.assertEqual(link_available.call_count, 2)
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_get_hacker_news_history_does_not_repeat_when_everything_is_used(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_script_dir = get_up.SCRIPT_DIR
            get_up.SCRIPT_DIR = Path(tmpdir)
            try:
                (Path(tmpdir) / "data").mkdir()
                story = get_up.HackerNewsStory(
                    object_id="1",
                    title="Seen story",
                    url="https://example.com/seen",
                    points=100,
                    num_comments=20,
                    author="seenuser",
                    created_at="2020-05-07T00:00:00Z",
                )
                used_path = Path(tmpdir) / get_up.HACKER_NEWS_USED_FILE
                used_path.write_text(f"{story.key}\n", encoding="utf-8")

                with (
                    mock.patch.object(
                        get_up,
                        "_hacker_news_candidate_years",
                        return_value=[2020],
                    ),
                    mock.patch.object(
                        get_up,
                        "_fetch_hacker_news_top_stories_for_date",
                        return_value=[story],
                    ),
                    mock.patch.object(
                        get_up,
                        "_is_hacker_news_story_link_available",
                        return_value=True,
                    ),
                ):
                    result = get_up.get_hacker_news_history()

                self.assertEqual(result, "")
                self.assertEqual(
                    used_path.read_text(encoding="utf-8").splitlines(),
                    [story.key],
                )
            finally:
                get_up.SCRIPT_DIR = original_script_dir

    def test_format_hacker_news_history_story_includes_article_and_hn_links(self):
        story = get_up.HackerNewsStory(
            object_id="42",
            title="Article Example",
            url="https://example.com/article",
            points=100,
            num_comments=25,
            author="asker",
            created_at="2020-05-07T00:00:00Z",
        )

        result = get_up._format_hacker_news_history_story(2020, 5, 7, story)

        self.assertIn("• Article Example", result)
        self.assertIn(
            "原文：[Article Example](https://example.com/article)",
            result,
        )
        self.assertIn(
            "HN 讨论：[42](https://news.ycombinator.com/item?id=42)",
            result,
        )

    def test_check_link_available_falls_back_to_get_when_head_is_blocked(self):
        head_response = mock.Mock()
        head_response.status_code = 405
        get_response = mock.Mock()
        get_response.status_code = 200

        with (
            mock.patch.object(get_up.requests, "head", return_value=head_response),
            mock.patch.object(get_up.requests, "get", return_value=get_response) as get,
        ):
            result = get_up._check_link_available("https://example.com/article")

        self.assertTrue(result)
        get_response.close.assert_called_once()
        get.assert_called_once()

    def test_check_link_available_rejects_cross_domain_head_redirect(self):
        head_response = mock.Mock()
        head_response.status_code = 200
        head_response.url = "https://parking.example/sale"

        with (
            mock.patch.object(get_up.requests, "head", return_value=head_response),
            mock.patch.object(get_up.requests, "get") as get,
        ):
            result = get_up._check_link_available("https://blog.example.com/article")

        self.assertFalse(result)
        get.assert_not_called()

    def test_check_link_available_rejects_cross_domain_get_redirect(self):
        head_response = mock.Mock()
        head_response.status_code = 405
        head_response.url = "https://blog.example.com/article"
        get_response = mock.Mock()
        get_response.status_code = 200
        get_response.url = "https://parking.example/sale"

        with (
            mock.patch.object(get_up.requests, "head", return_value=head_response),
            mock.patch.object(get_up.requests, "get", return_value=get_response),
        ):
            result = get_up._check_link_available("https://blog.example.com/article")

        self.assertFalse(result)
        get_response.close.assert_called_once()

    def test_check_link_available_allows_same_site_redirect(self):
        head_response = mock.Mock()
        head_response.status_code = 200
        head_response.url = "https://example.com/article"

        with mock.patch.object(get_up.requests, "head", return_value=head_response):
            result = get_up._check_link_available("https://blog.example.com/article")

        self.assertTrue(result)

    def test_format_hacker_news_history_story_falls_back_to_hn_as_article_link(self):
        story = get_up.HackerNewsStory(
            object_id="42",
            title="Ask HN: Example",
            url="",
            points=100,
            num_comments=25,
            author="asker",
            created_at="2020-05-07T00:00:00Z",
        )

        result = get_up._format_hacker_news_history_story(2020, 5, 7, story)

        self.assertIn(
            "原文：[Ask HN: Example](https://news.ycombinator.com/item?id=42)",
            result,
        )
        self.assertIn(
            "HN 讨论：[42](https://news.ycombinator.com/item?id=42)",
            result,
        )

    def test_build_get_up_message_parts_uses_selected_blog_year_for_hn(self):
        now = pendulum.datetime(2026, 5, 7, 8, 0, tz=get_up.TIMEZONE)

        with (
            mock.patch.object(get_up, "get_random_city", return_value=("", "", 0)),
            mock.patch.object(get_up, "get_running_distance", return_value="run"),
            mock.patch.object(get_up, "get_daily_leetcode", return_value="leetcode"),
            mock.patch.object(
                get_up,
                "_get_blog_article_from_history_parts",
                return_value=("blog", 2018),
            ),
            mock.patch.object(
                get_up,
                "get_hacker_news_history",
                return_value="hn",
            ) as hacker_news,
        ):
            parts = get_up._build_get_up_message_parts(now)

        hacker_news.assert_called_once_with(2018)
        self.assertEqual(parts.history_today, "hn")
        self.assertEqual(parts.blog_article, "blog")


if __name__ == "__main__":
    unittest.main()
