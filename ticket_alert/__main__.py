from __future__ import annotations

import argparse
import logging
import sys
import time
import tomllib

from .checker import run_once
from .config import ConfigError, load_config
from .notifiers import ConsoleNotifier, Notifier, TelegramNotifier
from .storage import Storage
from .travelpayouts import TravelpayoutsClient

log = logging.getLogger("ticket_alert")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ticket_alert",
        description="Следит за ценами на авиабилеты и присылает дешёвые варианты в Telegram.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="файл настроек (по умолчанию config.toml)")
    parser.add_argument("--once", action="store_true", help="проверить один раз и выйти (для cron)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="печатать найденное в консоль вместо Telegram и ничего не запоминать",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        log.error("нет файла настроек %s — скопируйте config.example.toml в config.toml", args.config)
        return 2
    except (ConfigError, tomllib.TOMLDecodeError, OSError) as e:
        log.error("ошибка в настройках: %s", e)
        return 2

    notifier: Notifier
    if args.dry_run:
        notifier = ConsoleNotifier()
    elif config.telegram_bot_token and config.telegram_chat_id:
        notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
    else:
        log.error(
            "не настроен Telegram: [telegram] bot_token и chat_id "
            "(или TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); для проверки без него — --dry-run"
        )
        return 2

    source = TravelpayoutsClient(config.travelpayouts_token, config.currency, config.market)
    storage = Storage(config.state_file)

    def check() -> int:
        summary = run_once(config.routes, source, storage, notifier, config.currency, args.dry_run)
        log.info("проверка завершена: вариантов %d, ошибок %d", summary.deals, summary.failed_routes)
        return 1 if summary.failed_routes else 0

    try:
        if args.once:
            return check()
        log.info("слежу за маршрутами: %d, проверка каждые %d мин", len(config.routes), config.interval_minutes)
        while True:
            check()
            time.sleep(config.interval_minutes * 60)
    except KeyboardInterrupt:
        log.info("остановлено")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
