"""Telegram Bot API и вывод оповещений в консоль."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol


class NotifyError(Exception):
    """Ошибка Telegram: не удалось отправить сообщение или получить обновления."""


class Notifier(Protocol):
    def send(self, chat_id: int, text: str) -> None: ...


Button = tuple[str, str]  # (текст, callback_data)


def inline_keyboard(rows: list[list[Button]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


class TelegramAPI:
    def __init__(
        self,
        bot_token: str,
        timeout: float = 20,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.bot_token = bot_token
        self.timeout = timeout
        self._urlopen = urlopen

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/{method}",
            data=json.dumps(params or {}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._urlopen(request, timeout=timeout or self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise NotifyError(f"Telegram: {_telegram_description(e)}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NotifyError(f"Telegram недоступен: {e}") from e
        except ValueError as e:
            raise NotifyError("Telegram вернул не JSON") from e

        if not payload.get("ok"):
            raise NotifyError(f"Telegram: {payload.get('description', payload)}")
        return payload.get("result")

    def send(self, chat_id: int, text: str) -> None:
        self.send_message(chat_id, text)

    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> Any:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", params)

    def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        try:
            self.call("editMessageText", params)
        except NotifyError as e:
            # Повторное нажатие той же кнопки — не ошибка.
            if "message is not modified" not in str(e):
                raise

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        self.call("answerCallbackQuery", params)

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        # Long polling: HTTP-таймаут должен быть больше времени ожидания Telegram.
        return self.call("getUpdates", params, timeout=timeout + 10) or []

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call(
            "setMyCommands",
            {"commands": [{"command": c, "description": d} for c, d in commands]},
        )


def _telegram_description(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read()).get("description") or f"HTTP {error.code}"
    except (ValueError, OSError, AttributeError):
        return f"HTTP {error.code}"


class ConsoleNotifier:
    """Печатает оповещения в консоль — для проверки настроек без Telegram."""

    def send(self, chat_id: int, text: str) -> None:
        print(html_to_text(text))
        print()


def html_to_text(text: str) -> str:
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2: \1", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))
