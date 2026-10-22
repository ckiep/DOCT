from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .channel import ChannelSettings


class ConfigError(Exception):
    """Ошибка в файле настроек."""


@dataclass(frozen=True)
class Config:
    travelpayouts_token: str
    currency: str
    market: str
    marker: str
    telegram_bot_token: str
    telegram_chat_id: int | None
    public: bool
    interval_minutes: int
    state_file: Path
    legacy_routes: bool  # в файле остались [[routes]] из старой версии
    channel: ChannelSettings | None = None  # автопостинг в канал, если задан [channel] id


# Партнёрский ID и необязательный доп. маркер через точку: 123456 или 123456.bot
_MARKER = re.compile(r"^\d+(\.[A-Za-z0-9_]{1,32})?$")
_BOT_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_CHAT_ID = re.compile(r"^-?\d+$")
_IATA = re.compile(r"^[A-Z]{3}$")
_SUB_ID = re.compile(r"^[A-Za-z0-9_]{0,32}$")


def load_config(path: str | Path, env: Mapping[str, str] = os.environ) -> Config:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)

    tp = raw.get("travelpayouts", {})
    tg = raw.get("telegram", {})
    check = raw.get("check", {})

    # Секреты можно держать в переменных окружения, а не в файле.
    token = (env.get("TRAVELPAYOUTS_TOKEN") or str(tp.get("token", ""))).strip()
    if not token:
        raise ConfigError(
            "не задан токен Travelpayouts: [travelpayouts] token или TRAVELPAYOUTS_TOKEN"
        )

    marker = str(tp.get("marker", "")).strip()
    if marker and not _MARKER.match(marker):
        raise ConfigError(
            "[travelpayouts] marker должен быть вашим партнёрским ID из цифр, например 123456"
        )

    bot_token = (env.get("TELEGRAM_BOT_TOKEN") or str(tg.get("bot_token", ""))).strip()
    if bot_token and not _BOT_TOKEN.match(bot_token):
        raise ConfigError(
            "[telegram] bot_token должен выглядеть как 1234567890:AAH… — его выдаёт @BotFather"
        )

    raw_chat_id = (env.get("TELEGRAM_CHAT_ID") or str(tg.get("chat_id", ""))).strip()
    if raw_chat_id and ":" in raw_chat_id:
        raise ConfigError(
            "[telegram] в chat_id вписан токен бота — перенесите его в bot_token, "
            "а в chat_id впишите свой ID в Telegram (число)"
        )
    if raw_chat_id and not _CHAT_ID.match(raw_chat_id):
        raise ConfigError("[telegram] chat_id должен быть числом — вашим ID в Telegram, например 123456789")

    public = tg.get("public", False)
    if not isinstance(public, bool):
        raise ConfigError("[telegram] public должен быть true или false")

    interval = check.get("interval_minutes", 60)
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        raise ConfigError("[check] interval_minutes должен быть целым числом ≥ 1")

    state_file = Path(check.get("state_file", "ticket_alert.db"))
    if not state_file.is_absolute():
        # Относительно файла настроек — чтобы программу можно было запускать из любой папки.
        state_file = path.parent / state_file

    return Config(
        travelpayouts_token=token,
        currency=str(tp.get("currency", "kzt")).lower(),
        market=str(tp.get("market", "kz")).lower(),
        marker=marker,
        telegram_bot_token=bot_token,
        telegram_chat_id=int(raw_chat_id) if raw_chat_id else None,
        public=public,
        interval_minutes=interval,
        state_file=state_file,
        legacy_routes=bool(raw.get("routes")),
        channel=_parse_channel(raw.get("channel") or {}),
    )


def _int(raw: Mapping[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"[channel] {key} должен быть целым числом от {low} до {high}")
    return value


def _parse_channel(raw: Mapping[str, Any]) -> ChannelSettings | None:
    chat = str(raw.get("id", "")).strip()
    if not chat:
        return None
    if not (chat.startswith("@") and len(chat) > 1 or _CHAT_ID.match(chat)):
        raise ConfigError('[channel] id — это @адрес канала, например "@traveleasy_kz", или его числовой ID')

    origins = raw.get("origins", ["ALA", "NQZ"])
    if isinstance(origins, str):
        origins = [origins]
    origins = tuple(str(o).strip().upper() for o in origins)
    if not origins or not all(_IATA.match(o) for o in origins):
        raise ConfigError('[channel] origins — список кодов городов, например ["ALA", "NQZ"]')

    hours = raw.get("post_hours", [9, 22])
    if (
        not isinstance(hours, list)
        or len(hours) != 2
        or not all(isinstance(h, int) and 0 <= h <= 23 for h in hours)
        or hours[0] > hours[1]
    ):
        raise ConfigError("[channel] post_hours — два часа от 0 до 23, например [9, 22]")

    round_trip = raw.get("round_trip", True)
    if not isinstance(round_trip, bool):
        raise ConfigError("[channel] round_trip должен быть true или false")
    sub_id = str(raw.get("sub_id", "channel"))
    if not _SUB_ID.match(sub_id):
        raise ConfigError("[channel] sub_id — латинские буквы, цифры и _, до 32 символов")

    return ChannelSettings(
        chat=int(chat) if _CHAT_ID.match(chat) else chat,
        origins=origins,
        min_discount=_int(raw, "min_discount", 20, 1, 90),
        months_ahead=_int(raw, "months_ahead", 2, 1, 12),
        round_trip=round_trip,
        max_posts_per_day=_int(raw, "max_posts_per_day", 5, 1, 100),
        max_posts_per_run=_int(raw, "max_posts_per_run", 2, 1, 100),
        every_minutes=_int(raw, "every_minutes", 180, 10, 24 * 60),
        post_hours=(hours[0], hours[1]),
        sub_id=sub_id,
    )
