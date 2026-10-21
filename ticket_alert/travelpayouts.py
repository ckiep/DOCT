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


def build_params(route: Route, currency: str, market: str) -> dict[str, str]:
    params = {
        "origin": route.origin,
        "departure_at": route.departure,
        "one_way": "true",
        "direct": "true" if route.direct_only else "false",
        "sorting": "price",
        "currency": currency,
        "market": market,
        "page": "1",
    }
    if route.destination:
        params.update(destination=route.destination, unique="false", limit="100")
    else:
        # Без города назначения API отдаёт самый дешёвый билет в каждый город.
        params.update(unique="true", limit="1000")
    return params


def parse_offers(
    payload: dict[str, Any], site: str = DEFAULT_SITE_URL, marker: str = ""
) -> list[Offer]:
    if not payload.get("success", False):
        raise SourceError(f"API вернул ошибку: {payload.get('error') or payload}")

    offers = []
    for item in payload.get("data") or []:
        try:
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
        query = urllib.parse.urlencode(build_params(route, self.currency, self.market))
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
