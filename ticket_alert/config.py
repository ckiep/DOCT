from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import Route


class ConfigError(Exception):
    """Ошибка в файле настроек."""


@dataclass(frozen=True)
class Config:
    travelpayouts_token: str
    currency: str
    market: str
    marker: str
    telegram_bot_token: str
    telegram_chat_id: str
    interval_minutes: int
    state_file: Path
    routes: list[Route]


_IATA = re.compile(r"^[A-Z]{3}$")
_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
# Партнёрский ID и необязательный доп. маркер через точку: 123456 или 123456.bot
_MARKER = re.compile(r"^\d+(\.[A-Za-z0-9_]{1,32})?$")


def load_config(path: str | Path, env: Mapping[str, str] = os.environ) -> Config:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)

    tp = raw.get("travelpayouts", {})
    tg = raw.get("telegram", {})
    check = raw.get("check", {})

    # Секреты можно держать в переменных окружения, а не в файле.
    token = env.get("TRAVELPAYOUTS_TOKEN") or tp.get("token", "")
    if not token:
        raise ConfigError(
            "не задан токен Travelpayouts: [travelpayouts] token или TRAVELPAYOUTS_TOKEN"
        )

    marker = str(tp.get("marker", "")).strip()
    if marker and not _MARKER.match(marker):
        raise ConfigError(
            "[travelpayouts] marker должен быть вашим партнёрским ID из цифр, например 123456"
        )

    interval = check.get("interval_minutes", 60)
    if not isinstance(interval, int) or interval < 1:
        raise ConfigError("[check] interval_minutes должен быть целым числом ≥ 1")

    state_file = Path(check.get("state_file", "ticket_alert.db"))
    if not state_file.is_absolute():
        # Относительно файла настроек — чтобы cron мог запускать из любой папки.
        state_file = path.parent / state_file

    routes = [_parse_route(i, r) for i, r in enumerate(raw.get("routes", []), start=1)]
    if not routes:
        raise ConfigError("не задано ни одного маршрута [[routes]]")

    return Config(
        travelpayouts_token=token,
        currency=str(tp.get("currency", "kzt")).lower(),
        market=str(tp.get("market", "kz")).lower(),
        marker=marker,
        telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", ""),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID") or str(tg.get("chat_id", "")),
        interval_minutes=interval,
        state_file=state_file,
        routes=routes,
    )


def _parse_route(index: int, raw: Mapping[str, Any]) -> Route:
    where = f"маршрут №{index}"

    origin = str(raw.get("origin", "")).upper()
    destination = str(raw.get("destination", "")).upper()
    for field, value in (("origin", origin), ("destination", destination)):
        if not _IATA.match(value):
            raise ConfigError(f"{where}: {field} должен быть IATA-кодом из 3 букв, например MOW")

    departure = str(raw.get("departure", ""))
    if not _DATE.match(departure):
        raise ConfigError(f"{where}: departure должен быть YYYY-MM или YYYY-MM-DD")

    return_date = raw.get("return")
    if return_date is not None:
        return_date = str(return_date)
        if not _DATE.match(return_date):
            raise ConfigError(f"{where}: return должен быть YYYY-MM или YYYY-MM-DD")

    max_price = raw.get("max_price")
    if not isinstance(max_price, int) or isinstance(max_price, bool) or max_price <= 0:
        raise ConfigError(f"{where}: max_price должен быть положительным целым числом")

    return Route(
        name=str(raw.get("name") or f"{origin} → {destination}"),
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
        max_price=max_price,
        direct_only=bool(raw.get("direct_only", False)),
    )
