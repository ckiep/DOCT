"""Автопостинг лучших находок в Telegram-канал."""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .checker import Directory, PriceSource, relevant_offers, typical_price
from .dates import upcoming_months
from .formatting import cheapest_by_destination, discount, format_channel_post
from .models import Offer, Route
from .storage import Storage
from .telegram import TelegramAPI, link_keyboard
from .travelpayouts import SourceError

log = logging.getLogger(__name__)

CHANNEL_KEY = "channel"
REPOST_AFTER = timedelta(days=7)
REPOST_IF_CHEAPER = 0.95  # раньше недели — только если стало ещё на 5% дешевле
ROUND_TRIP_DAYS = (3, 14)


@dataclass(frozen=True)
class ChannelSettings:
    chat: str | int  # @username канала или его числовой ID
    origins: tuple[str, ...]
    min_discount: int = 15
    months_ahead: int = 2
    candidates: int = 15  # сколько самых дешёвых городов каждой подборки проверять подробно
    round_trip: bool = True
    max_posts_per_day: int = 5
    max_posts_per_run: int = 2
    every_minutes: int = 180
    post_hours: tuple[int, int] = (9, 22)  # по местному времени, включительно
    sub_id: str = "channel"


@dataclass(frozen=True)
class ChannelDeal:
    offer: Offer
    typical: int
    discount: int
    record: bool = False

    @property
    def key(self) -> str:
        # Месяц в ключе: Стамбул в ноябре и Стамбул в декабре — разные находки.
        kind = "rt" if self.offer.return_at else "ow"
        return f"{self.offer.origin}-{self.offer.destination}-{kind}-{self.offer.departure_at:%Y-%m}"


@dataclass
class ScanStats:
    """Почему находок столько, сколько есть, — для лога и ответа на /post."""

    cities: int = 0  # сколько городов проверили подробно
    no_typical: int = 0  # мало дат, чтобы понять обычную цену
    small_discount: int = 0  # скидка меньше порога
    found: int = 0  # подходят по скидке
    already_posted: int = 0  # уже были в канале недавно
    posted: int = 0
    blocked: str | None = None  # "night" или "limit", если поиск не запускался


def channel_months(today: date, count: int) -> list[str]:
    """Месяцы для поиска. Почти закончившийся месяц пропускаем — в нём уже нечего искать."""
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    start = next_month if (next_month - today).days < 10 else today
    return upcoming_months(start, count)


def with_sub_id(link: str, sub_id: str) -> str:
    """marker=123456 → marker=123456.channel, чтобы в статистике было видно, что пришло из канала."""
    if not sub_id:
        return link
    parts = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    changed = [(k, f"{v.split('.')[0]}.{sub_id}" if k == "marker" and v else v) for k, v in query]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(changed)))


class ChannelPoster:
    def __init__(
        self,
        api: TelegramAPI,
        source: PriceSource,
        storage: Storage,
        places: Directory,
        currency: str,
        settings: ChannelSettings,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self.api = api
        self.source = source
        self.storage = storage
        self.places = places
        self.currency = currency
        self.settings = settings
        self.clock = clock
        self._bot_username: str | None = None
        self.last_stats = ScanStats()

    # --- поиск

    def _search(self, route: Route) -> list[Offer]:
        try:
            return relevant_offers(route, self.source.search(route), self.places)
        except SourceError as e:
            log.warning("канал: поиск %s → %s: %s", route.origin, route.destination or "*", e)
            return []

    def find_deals(self, today: date, stats: ScanStats | None = None) -> list[ChannelDeal]:
        """Города, где самый дешёвый билет заметно ниже обычной цены, от лучших к худшим."""
        stats = stats or ScanStats()
        trips: list[tuple[int | None, int | None]] = [(None, None)]
        if self.settings.round_trip:
            trips.append(ROUND_TRIP_DAYS)

        best: dict[str, ChannelDeal] = {}
        for origin in self.settings.origins:
            for month in channel_months(today, self.settings.months_ahead):
                for min_days, max_days in trips:
                    anywhere = Route(origin, None, month, 0, min_days=min_days, max_days=max_days)
                    candidates = cheapest_by_destination(self._search(anywhere))[: self.settings.candidates]
                    for candidate in candidates:
                        deal = self._evaluate(replace(anywhere, destination=candidate.destination), candidate, stats)
                        if deal and (deal.key not in best or deal.discount > best[deal.key].discount):
                            best[deal.key] = deal
        stats.found = len(best)
        return sorted(best.values(), key=lambda d: (-d.discount, d.offer.price))

    def _evaluate(self, route: Route, candidate: Offer, stats: ScanStats) -> ChannelDeal | None:
        stats.cities += 1
        # Обычная цена — по всем датам месяца в этот город.
        offers = self._search(route) + [candidate]
        typical = typical_price(offers)
        if typical is None:
            stats.no_typical += 1
            return None
        cheapest = min(offers, key=lambda o: o.price)
        percent = discount(cheapest.price, typical)
        if percent < self.settings.min_discount:
            stats.small_discount += 1
            return None
        return ChannelDeal(cheapest, typical, percent)

    def _is_new(self, deal: ChannelDeal, now: datetime) -> bool:
        previous = self.storage.last_notified(CHANNEL_KEY, deal.key)
        if previous is None:
            return True
        price, posted_at = previous
        return deal.offer.price <= price * REPOST_IF_CHEAPER or now - posted_at >= REPOST_AFTER

    # --- публикация

    def bot_username(self) -> str | None:
        if self._bot_username is None:
            self._bot_username = (self.api.call("getMe") or {}).get("username") or ""
        return self._bot_username or None

    def _post(self, deal: ChannelDeal, now: datetime) -> None:
        offer = replace(deal.offer, link=with_sub_id(deal.offer.link, self.settings.sub_id))
        best = self.storage.best_price(CHANNEL_KEY, deal.key)
        record = best is not None and offer.price < best
        text = format_channel_post(offer, deal.typical, self.currency, self.places.name, record)
        rows = [[("✈️ Купить билет", offer.link)]]
        username = self.bot_username()
        if username:
            rows.append([("🔔 Следить за своими направлениями", f"https://t.me/{username}")])
        self.api.send_message(self.settings.chat, text, link_keyboard(rows))
        self.storage.remember(CHANNEL_KEY, [(deal.key, deal.offer)], at=now)

    def run(self, force: bool = False) -> int:
        """Публикует новые находки. force — вне расписания и часов тишины (команда /post)."""
        now = self.clock()
        stats = self.last_stats = ScanStats()
        first, last = self.settings.post_hours
        if not force and not first <= now.hour <= last:
            stats.blocked = "night"
            log.info("канал: сейчас ночь по местному времени — не публикую")
            return 0

        posted_today = self.storage.count_notified_since(CHANNEL_KEY, now - timedelta(days=1))
        room = min(self.settings.max_posts_per_day - posted_today, self.settings.max_posts_per_run)
        if room <= 0:
            stats.blocked = "limit"
            log.info("канал: лимит постов на сутки исчерпан")
            return 0

        now_utc = now.astimezone(timezone.utc)
        found = self.find_deals(now.date(), stats)
        fresh = [d for d in found if self._is_new(d, now_utc)]
        for deal in fresh[:room]:
            self._post(deal, now)
        # Запоминаем увиденные цены, чтобы в следующих постах отмечать рекорды 🏆.
        self.storage.update_best(CHANNEL_KEY, {d.key: d.offer.price for d in found})
        stats.already_posted = len(found) - len(fresh)
        stats.posted = min(room, len(fresh))
        log.info(
            "канал: проверено городов %d, мало данных об обычной цене %d, скидка меньше %d%% у %d, "
            "подходит %d, уже были в канале %d, опубликовано %d",
            stats.cities, stats.no_typical, self.settings.min_discount, stats.small_discount,
            stats.found, stats.already_posted, stats.posted,
        )
        return stats.posted


def run_safely(poster: ChannelPoster) -> Any:
    """Для расписания: ошибки сети не должны останавливать бота."""
    try:
        return poster.run()
    except Exception:
        log.exception("канал: ошибка автопостинга")
        return 0
