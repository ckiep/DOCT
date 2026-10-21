import json
import unittest
import urllib.error
from io import BytesIO

from ticket_alert.notifiers import NotifyError, TelegramNotifier, html_to_text
from ticket_alert.travelpayouts import SourceError, TravelpayoutsClient, build_params, parse_offers

from tests.helpers import FakeResponse, FakeUrlopen, make_route

SAMPLE = {
    "success": True,
    "currency": "rub",
    "data": [
        {
            "origin": "MOW",
            "destination": "IST",
            "origin_airport": "VKO",
            "destination_airport": "SAW",
            "price": 9870,
            "airline": "PC",
            "flight_number": "395",
            "departure_at": "2026-12-05T10:15:00+03:00",
            "transfers": 0,
            "return_transfers": 0,
            "duration": 255,
            "link": "/search/VKO0512SAW1?t=PC17649",
        },
        {"price": "not a number", "departure_at": "2026-12-06T10:15:00+03:00"},
    ],
}


class TravelpayoutsTest(unittest.TestCase):
    def test_params_one_way(self):
        params = build_params(make_route(), "rub", "ru")
        self.assertEqual(params["one_way"], "true")
        self.assertEqual(params["direct"], "false")
        self.assertNotIn("return_at", params)

    def test_params_round_trip_direct(self):
        params = build_params(make_route(return_date="2026-12-20", direct_only=True), "usd", "us")
        self.assertEqual(params["one_way"], "false")
        self.assertEqual(params["return_at"], "2026-12-20")
        self.assertEqual(params["direct"], "true")
        self.assertEqual(params["currency"], "usd")

    def test_parse_skips_broken_rows_and_builds_link(self):
        offers = parse_offers(SAMPLE, "https://www.aviasales.kz")
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.price, 9870)
        self.assertEqual(offer.link, "https://www.aviasales.kz/search/VKO0512SAW1?t=PC17649")
        self.assertIsNone(offer.return_at)
        self.assertEqual(offer.key, "2026-12-05|")

    def test_parse_error_payload(self):
        with self.assertRaises(SourceError):
            parse_offers({"success": False, "error": "bad params"})

    def test_search_sends_token_in_header_and_handles_gzip(self):
        urlopen = FakeUrlopen(FakeResponse(SAMPLE, gzipped=True))
        client = TravelpayoutsClient("secret", urlopen=urlopen)

        offers = client.search(make_route())

        self.assertEqual(len(offers), 1)
        request = urlopen.requests[0]
        self.assertEqual(request.get_header("X-access-token"), "secret")
        self.assertNotIn("secret", request.full_url)
        self.assertIn("origin=MOW", request.full_url)
        self.assertIn("market=kz", request.full_url)
        self.assertTrue(offers[0].link.startswith("https://www.aviasales.kz/"))

    def test_search_unauthorized(self):
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO(b""))
        client = TravelpayoutsClient("bad", urlopen=FakeUrlopen(error))
        with self.assertRaisesRegex(SourceError, "токен"):
            client.search(make_route())


class TelegramTest(unittest.TestCase):
    def test_send(self):
        urlopen = FakeUrlopen(FakeResponse({"ok": True, "result": {}}))
        TelegramNotifier("123:abc", "42", urlopen=urlopen).send("<b>hi</b>")

        request = urlopen.requests[0]
        self.assertTrue(request.full_url.endswith("/bot123:abc/sendMessage"))
        body = json.loads(request.data)
        self.assertEqual(body["chat_id"], "42")
        self.assertEqual(body["parse_mode"], "HTML")

    def test_send_error_uses_telegram_description(self):
        body = BytesIO(json.dumps({"ok": False, "description": "Bad Request: chat not found"}).encode())
        error = urllib.error.HTTPError("url", 400, "Bad Request", {}, body)
        notifier = TelegramNotifier("123:abc", "42", urlopen=FakeUrlopen(error))
        with self.assertRaisesRegex(NotifyError, "chat not found"):
            notifier.send("hi")

    def test_html_to_text(self):
        text = html_to_text('<b>9 870 ₽</b> &amp; <a href="https://x.ru/s">купить</a>')
        self.assertEqual(text, "9 870 ₽ & купить: https://x.ru/s")


if __name__ == "__main__":
    unittest.main()
