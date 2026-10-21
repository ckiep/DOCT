from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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


# Партнёрский ID и необязательный доп. маркер через точку: 123456 или 123456.bot
_MARKER = re.compile(r"^\d+(\.[A-Za-z0-9_]{1,32})?$")
_BOT_TOKEN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_CHAT_ID = re.compile(r"^-?\d+$")


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
    )
