from __future__ import annotations

import gzip
import io
import json
from datetime import datetime

from ticket_alert.models import Offer, Route


def make_route(**overrides) -> Route:
    fields = dict(
        name="Москва → Стамбул",
        origin="MOW",
        destination="IST",
        departure="2026-12",
        max_price=10000,
    )
    fields.update(overrides)
    return Route(**fields)


def make_offer(price: int, day: int = 5, **overrides) -> Offer:
    fields = dict(
        price=price,
        departure_at=datetime.fromisoformat(f"2026-12-{day:02d}T10:15:00+03:00"),
        airline="PC",
        flight_number="395",
        transfers=0,
        link=f"https://www.aviasales.ru/search/MOW{day:02d}12IST1",
    )
    fields.update(overrides)
    return Offer(**fields)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, gzipped: bool = False) -> None:
        body = json.dumps(payload).encode()
        self.headers = {"Content-Encoding": "gzip"} if gzipped else {}
        super().__init__(gzip.compress(body) if gzipped else body)


class FakeUrlopen:
    """Подменяет urllib.request.urlopen и запоминает запросы."""

    def __init__(self, response) -> None:
        self.response = response
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeSource:
    def __init__(self, offers=None, error: Exception | None = None) -> None:
        self.offers = offers or []
        self.error = error

    def search(self, route):
        if self.error:
            raise self.error
        return list(self.offers)


class FakeNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages: list[str] = []
        self.error = error

    def send(self, text: str) -> None:
        if self.error:
            raise self.error
        self.messages.append(text)
