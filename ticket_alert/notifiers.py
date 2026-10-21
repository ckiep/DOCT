from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol


class NotifyError(Exception):
    """Не удалось отправить оповещение."""


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class TelegramNotifier:
    """Отправляет сообщения (HTML-разметка) через Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 20,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._urlopen = urlopen

    def send(self, text: str) -> None:
        body = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise NotifyError(f"Telegram: {_telegram_description(e)}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NotifyError(f"Telegram недоступен: {e}") from e

        if not payload.get("ok"):
            raise NotifyError(f"Telegram: {payload.get('description', payload)}")


def _telegram_description(error: urllib.error.HTTPError) -> str:
    try:
        return json.loads(error.read()).get("description") or f"HTTP {error.code}"
    except (ValueError, OSError, AttributeError):
        return f"HTTP {error.code}"


class ConsoleNotifier:
    """Печатает оповещения в консоль — для проверки настроек без Telegram."""

    def send(self, text: str) -> None:
        print(html_to_text(text))
        print()


def html_to_text(text: str) -> str:
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2: \1", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))
