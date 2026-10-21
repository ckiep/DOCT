"""Источник цен: Aviasales Data API (Travelpayouts).

Цены берутся из кэша поисков пользователей Aviasales за последние дни,
поэтому это не «живая» выдача — перед покупкой цену нужно проверить на сайте.
"""

from __future__ import annotations

import gzip
import json
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


class SourceError(Exception):
    """Не удалось получить цены."""


def build_params(route: Route, currency: str, market: str, limit: int = 100) -> dict[str, str]:
    params = {
        "origin": route.origin,
        "destination": route.destination,
        "departure_at": route.departure,
        "one_way": "false" if route.return_date else "true",
        "direct": "true" if route.direct_only else "false",
        "sorting": "price",
        "unique": "false",
        "currency": currency,
        "market": market,
        "limit": str(limit),
        "page": "1",
    }
    if route.return_date:
        params["return_at"] = route.return_date
    return params


def parse_offers(payload: dict[str, Any], site: str = DEFAULT_SITE_URL) -> list[Offer]:
    if not payload.get("success", False):
        raise SourceError(f"API вернул ошибку: {payload.get('error') or payload}")

    offers = []
    for item in payload.get("data") or []:
        try:
            return_at = item.get("return_at")
            offers.append(
                Offer(
                    price=int(item["price"]),
                    departure_at=datetime.fromisoformat(item["departure_at"]),
                    return_at=datetime.fromisoformat(return_at) if return_at else None,
                    airline=item.get("airline") or "",
                    flight_number=str(item.get("flight_number") or ""),
                    transfers=int(item.get("transfers") or 0),
                    return_transfers=int(item.get("return_transfers") or 0),
                    link=site + item["link"] if item.get("link") else site,
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
        timeout: float = 20,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.token = token
        self.currency = currency
        self.market = market
        self.timeout = timeout
        self._urlopen = urlopen

    def search(self, route: Route) -> list[Offer]:
        query = urllib.parse.urlencode(build_params(route, self.currency, self.market))
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
        return parse_offers(payload, site_url(self.market))
