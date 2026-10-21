from __future__ import annotations

import argparse
import logging
import sys
import tomllib

from .bot import Bot, serve
from .checker import RunSummary, run_once
from .config import ConfigError, load_config
from .places import Places
from .storage import Storage
from .telegram import ConsoleNotifier, Notifier, NotifyError, TelegramAPI
from .travelpayouts import TravelpayoutsClient

log = logging.getLogger("ticket_alert")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ticket_alert",
        description="Telegram-бот: следит за ценами на авиабилеты и присылает дешёвые варианты.",
    )
    parser.add_argument("-c", "--config", default="config.toml", help="файл настроек (по умолчанию config.toml)")
    parser.add_argument(
        "--once", action="store_true", help="один раз проверить цены, разослать оповещения и выйти (для cron)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="один раз проверить цены и напечатать найденное в консоль вместо Telegram, ничего не запоминая",
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

    if config.legacy_routes:
        log.warning(
            "маршруты [[routes]] из config.toml больше не используются — "
            "добавляйте направления в боте кнопкой «Добавить направление»"
        )
    if not args.dry_run and not config.telegram_bot_token:
        log.error("не задан [telegram] bot_token (или TELEGRAM_BOT_TOKEN); для проверки без Telegram — --dry-run")
        return 2
    if not args.dry_run and not args.once and config.telegram_chat_id is None and not config.public:
        log.error("не задан [telegram] chat_id — ваш ID в Telegram, иначе бот никому не будет отвечать")
        return 2

    storage = Storage(config.state_file)
    places = Places.load(config.state_file.parent)
    source = TravelpayoutsClient(
        config.travelpayouts_token, config.currency, config.market, marker=config.marker
    )

    def check(notifier: Notifier, dry_run: bool = False) -> int:
        summary: RunSummary = run_once(storage, source, notifier, config.currency, places, dry_run)
        log.info("проверка завершена: вариантов %d, ошибок %d", summary.deals, summary.failed_routes)
        return 1 if summary.failed_routes else 0

    try:
        if args.dry_run:
            return check(ConsoleNotifier(), dry_run=True)

        api = TelegramAPI(config.telegram_bot_token)
        if args.once:
            return check(api)

        bot = Bot(api, storage, source, places, config.currency, config.telegram_chat_id, config.public)
        serve(api, bot, lambda: check(api), config.interval_minutes)
        return 0
    except NotifyError as e:
        log.error("%s — проверьте bot_token и подключение к интернету", e)
        return 2
    except KeyboardInterrupt:
        log.info("остановлено")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
