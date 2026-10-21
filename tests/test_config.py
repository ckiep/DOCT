import tempfile
import unittest
from pathlib import Path

from ticket_alert.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
BOT_TOKEN = "1234567890:AAH_test-token"


class ConfigTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, telegram: str = "", travelpayouts: str = 'token="x"', extra: str = ""):
        path = self.write(f"[travelpayouts]\n{travelpayouts}\n[telegram]\n{telegram}\n{extra}")
        return load_config(path, env={})

    def test_example_config_loads_with_env_tokens(self):
        env = {"TRAVELPAYOUTS_TOKEN": "tp", "TELEGRAM_BOT_TOKEN": BOT_TOKEN, "TELEGRAM_CHAT_ID": "42"}
        config = load_config(ROOT / "config.example.toml", env=env)

        self.assertEqual(config.travelpayouts_token, "tp")
        self.assertEqual(config.telegram_bot_token, BOT_TOKEN)
        self.assertEqual(config.telegram_chat_id, 42)
        self.assertEqual((config.currency, config.market, config.marker), ("kzt", "kz", ""))
        self.assertFalse(config.public)
        self.assertFalse(config.legacy_routes)
        self.assertEqual(config.state_file, ROOT / "ticket_alert.db")

    def test_requires_travelpayouts_token(self):
        with self.assertRaisesRegex(ConfigError, "токен Travelpayouts"):
            self.load(travelpayouts='token=""')

    def test_bot_token_in_chat_id_is_explained(self):
        with self.assertRaisesRegex(ConfigError, "в chat_id вписан токен бота"):
            self.load(f'bot_token=""\nchat_id="{BOT_TOKEN}"')

    def test_validates_bot_token_and_chat_id(self):
        with self.assertRaisesRegex(ConfigError, "bot_token"):
            self.load('bot_token="472989828"')
        with self.assertRaisesRegex(ConfigError, "chat_id должен быть числом"):
            self.load('chat_id="@me"')

    def test_chat_id_as_string_or_number(self):
        self.assertEqual(self.load('chat_id="472989828"').telegram_chat_id, 472989828)
        self.assertEqual(self.load("chat_id=472989828").telegram_chat_id, 472989828)
        self.assertEqual(self.load("chat_id=-100123").telegram_chat_id, -100123)
        self.assertIsNone(self.load('chat_id=""').telegram_chat_id)

    def test_marker(self):
        for value, expected in (('"123456"', "123456"), ("123456", "123456"), ('"123456.bot"', "123456.bot")):
            with self.subTest(value):
                self.assertEqual(self.load(travelpayouts=f'token="x"\nmarker={value}').marker, expected)
        for value in ('"marker=123456"', '"abc"'):
            with self.subTest(value), self.assertRaisesRegex(ConfigError, "marker"):
                self.load(travelpayouts=f'token="x"\nmarker={value}')

    def test_old_routes_are_detected(self):
        config = self.load(extra='[[routes]]\norigin="ALA"\ndestination="IST"\ndeparture="2026-12"\nmax_price=1\n')
        self.assertTrue(config.legacy_routes)


if __name__ == "__main__":
    unittest.main()
