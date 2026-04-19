import tempfile
import unittest
from pathlib import Path
from unittest import mock

import get_up


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
                    mock.patch.object(get_up.time, "sleep") as sleep,
                ):
                    result = get_up._generate_city_poster("徐州")

                self.assertEqual(result, str(poster_path))
                self.assertEqual(generate_poster.call_count, 3)
                sleep.assert_has_calls([mock.call(2), mock.call(4)])
            finally:
                get_up.SCRIPT_DIR = original_script_dir


if __name__ == "__main__":
    unittest.main()
