import tempfile
import unittest
from pathlib import Path

from ticket_alert.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent


class ConfigTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_example_config_loads_with_env_tokens(self):
        env = {"TRAVELPAYOUTS_TOKEN": "tp", "TELEGRAM_BOT_TOKEN": "bot", "TELEGRAM_CHAT_ID": "42"}
        config = load_config(ROOT / "config.example.toml", env=env)

        self.assertEqual(config.travelpayouts_token, "tp")
        self.assertEqual(config.marker, "")
        self.assertEqual(config.telegram_bot_token, "bot")
        self.assertEqual(config.telegram_chat_id, "42")
        self.assertEqual(config.state_file, ROOT / "ticket_alert.db")
        self.assertEqual(len(config.routes), 2)
        self.assertEqual(config.routes[1].return_date, "2027-01")
        self.assertTrue(config.routes[1].direct_only)

    def test_requires_token(self):
        path = self.write('[[routes]]\norigin="MOW"\ndestination="IST"\ndeparture="2026-12"\nmax_price=1\n')
        with self.assertRaisesRegex(ConfigError, "токен"):
            load_config(path, env={})

    def test_requires_routes(self):
        path = self.write('[travelpayouts]\ntoken="x"\n')
        with self.assertRaisesRegex(ConfigError, "маршрут"):
            load_config(path, env={})

    def test_validates_route_fields(self):
        cases = {
            'origin="Moscow"': "IATA",
            'departure="12.2026"': "departure",
            "max_price=0": "max_price",
            'max_price="дёшево"': "max_price",
        }
        base = {"origin": '"MOW"', "destination": '"IST"', "departure": '"2026-12"', "max_price": "5000"}
        for override, message in cases.items():
            key = override.split("=")[0]
            fields = {**base, key: override.split("=", 1)[1]}
            body = "\n".join(f"{k}={v}" for k, v in fields.items())
            path = self.write(f'[travelpayouts]\ntoken="x"\n[[routes]]\n{body}\n')
            with self.subTest(override), self.assertRaisesRegex(ConfigError, message):
                load_config(path, env={})

    def test_marker(self):
        route = '[[routes]]\norigin="ALA"\ndestination="IST"\ndeparture="2026-12"\nmax_price=1\n'
        for value, expected in (('"123456"', "123456"), ("123456", "123456"), ('"123456.bot"', "123456.bot")):
            path = self.write(f'[travelpayouts]\ntoken="x"\nmarker={value}\n{route}')
            with self.subTest(value):
                self.assertEqual(load_config(path, env={}).marker, expected)

        for value in ('"marker=123456"', '"12 34"', '"abc"'):
            path = self.write(f'[travelpayouts]\ntoken="x"\nmarker={value}\n{route}')
            with self.subTest(value), self.assertRaisesRegex(ConfigError, "marker"):
                load_config(path, env={})

    def test_lowercase_codes_are_normalized(self):
        path = self.write(
            '[travelpayouts]\ntoken="x"\n[[routes]]\norigin="mow"\ndestination="ist"\n'
            'departure="2026-12-05"\nmax_price=5000\n'
        )
        route = load_config(path, env={}).routes[0]
        self.assertEqual((route.origin, route.destination), ("MOW", "IST"))
        self.assertEqual(route.name, "MOW → IST")


if __name__ == "__main__":
    unittest.main()
