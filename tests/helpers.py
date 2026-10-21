from __future__ import annotations

import gzip
import io
import json
from datetime import datetime

from ticket_alert.models import Offer, Route
from ticket_alert.places import Places
from ticket_alert.storage import Storage


def make_route(**overrides) -> Route:
    fields = dict(origin="ALA", destination="IST", departure="2026-12", max_price=60000, chat_id=42)
    fields.update(overrides)
    return Route(**fields)


def make_offer(price: int, day: int = 5, destination: str = "IST", **overrides) -> Offer:
    fields = dict(
        origin="ALA",
        destination=destination,
        price=price,
        departure_at=datetime.fromisoformat(f"2026-12-{day:02d}T05:45:00+05:00"),
        airline="PC",
        flight_number="101",
        transfers=0,
        link=f"https://www.aviasales.kz/search/ALA{day:02d}12{destination}1",
    )
    fields.update(overrides)
    return Offer(**fields)


PLACES = Places.builtin()
names = PLACES.name


class FakeResponse(io.BytesIO):
    def __init__(self, payload, gzipped: bool = False) -> None:
        body = json.dumps(payload).encode()
        self.headers = {"Content-Encoding": "gzip"} if gzipped else {}
        super().__init__(gzip.compress(body) if gzipped else body)


class FakeUrlopen:
    """Подменяет urllib.request.urlopen и запоминает запросы."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class FakeSource:
    """offers — список или функция route → список."""

    def __init__(self, offers=None, error: Exception | None = None) -> None:
        self.offers = offers if offers is not None else []
        self.error = error
        self.searches = []

    def search(self, route):
        self.searches.append(route)
        if self.error:
            raise self.error
        return list(self.offers(route) if callable(self.offers) else self.offers)


class FakeNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages: list[tuple[int, str]] = []
        self.error = error

    def send(self, chat_id: int, text: str) -> None:
        if self.error:
            raise self.error
        self.messages.append((chat_id, text))


class FakeTelegram:
    """Заменяет TelegramAPI в тестах бота."""

    def __init__(self) -> None:
        self.sent: list[dict] = []  # sendMessage и editMessageText по порядку
        self.answers: list[tuple[str, str | None]] = []
        self._next_id = 100

    def send(self, chat_id, text):
        self.send_message(chat_id, text)

    def send_message(self, chat_id, text, reply_markup=None):
        self._next_id += 1
        self.sent.append(
            {"method": "send", "chat_id": chat_id, "text": text, "markup": reply_markup, "message_id": self._next_id}
        )
        return {"message_id": self._next_id}

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.sent.append(
            {"method": "edit", "chat_id": chat_id, "text": text, "markup": reply_markup, "message_id": message_id}
        )

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))

    @property
    def last(self) -> dict:
        return self.sent[-1]

    def buttons(self, index: int = -1) -> list[tuple[str, str]]:
        markup = self.sent[index]["markup"] or {}
        return [(b["text"], b.get("callback_data")) for row in markup.get("inline_keyboard", []) for b in row]


def memory_storage(test) -> Storage:
    storage = Storage(":memory:")
    test.addCleanup(storage.close)
    return storage
