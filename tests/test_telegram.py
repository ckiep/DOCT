import json
import unittest
import urllib.error
from io import BytesIO

from ticket_alert.telegram import NotifyError, TelegramAPI, html_to_text, inline_keyboard

from tests.helpers import FakeResponse, FakeUrlopen


def http_error(code: int, description: str) -> urllib.error.HTTPError:
    body = BytesIO(json.dumps({"ok": False, "description": description}).encode())
    return urllib.error.HTTPError("url", code, "error", {}, body)


class TelegramTest(unittest.TestCase):
    def test_send_message_with_keyboard(self):
        urlopen = FakeUrlopen(FakeResponse({"ok": True, "result": {"message_id": 5}}))
        api = TelegramAPI("123:abc", urlopen=urlopen)

        result = api.send_message(42, "<b>hi</b>", inline_keyboard([[("Да", "yes")]]))

        self.assertEqual(result, {"message_id": 5})
        request = urlopen.requests[0]
        self.assertTrue(request.full_url.endswith("/bot123:abc/sendMessage"))
        body = json.loads(request.data)
        self.assertEqual(body["chat_id"], 42)
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertEqual(body["reply_markup"], {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]})

    def test_error_uses_telegram_description(self):
        api = TelegramAPI("123:abc", urlopen=FakeUrlopen(http_error(400, "Bad Request: chat not found")))
        with self.assertRaisesRegex(NotifyError, "chat not found"):
            api.send(42, "hi")

    def test_edit_of_unchanged_message_is_not_an_error(self):
        error = http_error(400, "Bad Request: message is not modified")
        TelegramAPI("123:abc", urlopen=FakeUrlopen(error)).edit_message(42, 1, "same")

    def test_get_updates_waits_longer_than_long_poll(self):
        seen = {}

        def urlopen(request, timeout=None):
            seen["timeout"] = timeout
            seen["body"] = json.loads(request.data)
            return FakeResponse({"ok": True, "result": [{"update_id": 1}]})

        updates = TelegramAPI("123:abc", urlopen=urlopen).get_updates(offset=7, timeout=25)

        self.assertEqual(updates, [{"update_id": 1}])
        self.assertGreater(seen["timeout"], 25)
        self.assertEqual(seen["body"]["offset"], 7)

    def test_html_to_text(self):
        text = html_to_text('<b>58 802 ₸</b> &amp; <a href="https://x.kz/s">купить</a>')
        self.assertEqual(text, "58 802 ₸ & купить: https://x.kz/s")


if __name__ == "__main__":
    unittest.main()
