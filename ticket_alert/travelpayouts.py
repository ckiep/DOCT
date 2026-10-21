"""Источник цен: Aviasales Data API (Travelpayouts).

Цены берутся из кэша поисков пользователей Aviasales за последние дни,
поэтому это не «живая» выдача — перед покупкой цену нужно проверить на сайте.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable

from .models import Offer, Route

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
SITE_URLS = {"kz": "https://www.aviasales.kz", "ru": "https://www.aviasales.ru", "uz": "https://www.aviasales.uz"}
DEFAULT_SITE_URL = "https://www.aviasales.com"


def site_url(market: str) -> str:
    """Ссылки на покупку ведут на сайт Aviasales своей страны."""
    return SITE_URLS.get(market, DEFAULT_SITE_URL)


def partner_link(site: str, path: str, marker: str = "") -> str:
    """Ссылка на покупку; с маркером Travelpayouts она становится партнёрской."""
    link = site + path
    if marker:
        link += ("&" if "?" in link else "?") + urllib.parse.urlencode({"marker": marker})
    return link


class SourceError(Exception):
    """Не удалось получить цены."""


def build_queries(route: Route, currency: str, market: str) -> list[dict[str, str]]:
    """Параметры запросов к API; результаты всех запросов складываются вместе."""
    base = {
        "origin": route.origin,
        "departure_at": route.departure,
        # Без return_at и с one_way=false API отдаёт билеты туда-обратно на любой срок,
        # нужную длительность поездки отбираем сами.
        "one_way": "false" if route.round_trip else "true",
        "direct": "true" if route.direct_only else "false",
        "sorting": "price",
        "currency": currency,
        "market": market,
        "page": "1",
    }
    if route.destination:
        limit = "1000" if route.round_trip else "100"
        return [{**base, "destination": route.destination, "unique": "false", "limit": limit}]
    # Без города назначения unique=true даёт самый дешёвый билет в каждый город.
    queries = [{**base, "unique": "true", "limit": "1000"}]
    if route.round_trip:
        # Самый дешёвый билет в город может оказаться не той длительности —
        # добавляем просто самые дешёвые варианты, чтобы было из чего выбирать.
        queries.append({**base, "unique": "false", "limit": "1000"})
    return queries


def parse_offers(
    payload: dict[str, Any], site: str = DEFAULT_SITE_URL, marker: str = ""
) -> list[Offer]:
    if not payload.get("success", False):
        raise SourceError(f"API вернул ошибку: {payload.get('error') or payload}")

    offers = []
    for item in payload.get("data") or []:
        try:
            return_at = item.get("return_at")
            offers.append(
                Offer(
                    origin=item.get("origin") or "",
                    destination=item["destination"],
                    price=int(item["price"]),
                    departure_at=datetime.fromisoformat(item["departure_at"]),
                    airline=item.get("airline") or "",
                    flight_number=str(item.get("flight_number") or ""),
                    transfers=int(item.get("transfers") or 0),
                    link=partner_link(site, item.get("link") or "", marker),
                    return_at=datetime.fromisoformat(return_at) if return_at else None,
                    return_transfers=int(item.get("return_transfers") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Битая запись не должна ломать весь маршрут.
            continue
    return offers


class TravelpayoutsClient:
    def __init__(
        self,
        token: str,
        currency: str = "kzt",
        market: str = "kz",
        marker: str = "",
        timeout: float = 20,
        cache_seconds: float = 600,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token = token
        self.currency = currency
        self.market = market
        self.marker = marker
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._urlopen = urlopen
        self._clock = clock
        # Одинаковые запросы (подсказка бюджета, проверка сразу после сохранения)
        # не гоняем в API повторно.
        self._cache: dict[str, tuple[float, list[Offer]]] = {}

    def search(self, route: Route) -> list[Offer]:
        offers: list[Offer] = []
        for params in build_queries(route, self.currency, self.market):
            offers += self._fetch(urllib.parse.urlencode(params))
        return offers

    def _fetch(self, query: str) -> list[Offer]:
        cached = self._cache.get(query)
        if cached and self._clock() - cached[0] < self.cache_seconds:
            return cached[1]

        request = urllib.request.Request(
            f"{API_URL}?{query}",
            headers={
                # Токен в заголовке, а не в URL, чтобы он не попадал в логи.
                "X-Access-Token": self.token,
                "Accept-Encoding": "gzip",
                "Accept": "application/json",
            },
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SourceError("неверный токен Travelpayouts (HTTP 401)") from e
            raise SourceError(f"HTTP {e.code} от Travelpayouts") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceError(f"сеть недоступна: {e}") from e

        try:
            payload = json.loads(body)
        except ValueError as e:
            raise SourceError("Travelpayouts вернул не JSON") from e
        offers = parse_offers(payload, site_url(self.market), self.marker)
        self._cache[query] = (self._clock(), offers)
        return offers
