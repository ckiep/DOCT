"""Проверка цен: найти дешёвые варианты и сообщить только о новых или подешевевших."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, replace
from datetime import date
from html import escape
from typing import Iterable, Protocol

from .dates import is_expired
from .formatting import describe_route, format_alert
from .models import SMART_DISCOUNT, Deal, Offer, Route
from .storage import Storage
from .telegram import Notifier, NotifyError
from .travelpayouts import SourceError

log = logging.getLogger(__name__)


class PriceSource(Protocol):
    def search(self, route: Route) -> list[Offer]: ...


class Directory(Protocol):
    """Справочник городов (см. places.Places)."""

    def name(self, code: str) -> str: ...

    def same_country(self, a: str, b: str) -> bool: ...


def relevant_offers(route: Route, offers: Iterable[Offer], places: Directory) -> list[Offer]:
    """Отбирает билеты, которые подходят направлению.

    «Куда угодно» означает за границу: внутренние рейсы своей страны не показываем.
    Для туда-обратно оставляем только поездки нужной длительности.
    """
    result = []
    for offer in offers:
        if route.anywhere and places.same_country(route.origin, offer.destination):
            continue
        days = offer.trip_days
        if route.round_trip and (days is None or not route.min_days <= days <= route.max_days):
            continue
        if not route.round_trip and days is not None:
            continue
        result.append(offer)
    return result


def typical_price(offers: Iterable[Offer]) -> int | None:
    """Обычная цена: медиана самых дешёвых билетов по каждой дате вылета.

    Нужна хотя бы на 4 дня, иначе сравнивать не с чем.
    """
    by_date: dict[date, int] = {}
    for offer in offers:
        day = offer.departure_at.date()
        by_date[day] = min(offer.price, by_date.get(day, offer.price))
    if len(by_date) < 4:
        return None
    return round(statistics.median(by_date.values()))


def smart_limit(typical: int) -> int:
    return typical * (100 - SMART_DISCOUNT) // 100


@dataclass(frozen=True)
class RunSummary:
    deals: int
    failed_routes: int


def deal_key(route: Route, offer: Offer) -> str:
    # «Куда угодно»: следим за ценой в каждый город, а не за каждой датой,
    # иначе смена самой дешёвой даты присылала бы город повторно.
    if route.anywhere:
        return offer.destination
    key = offer.departure_at.date().isoformat()
    if offer.return_at:
        key += f"|{offer.return_at.date().isoformat()}"
    return key


def _scope(route: Route, offer: Offer) -> str:
    """По чему считаем «самую низкую цену»: по каждому городу или по направлению целиком."""
    return offer.destination if route.anywhere else "*"


def find_deals(
    route: Route, offers: Iterable[Offer], storage: Storage, typical: int | None = None
) -> list[Deal]:
    if route.smart:
        if typical is None:
            return []
        limit = smart_limit(typical)
    else:
        limit = route.max_price

    cheapest: dict[str, Offer] = {}
    for offer in offers:
        if offer.price > limit:
            continue
        if route.direct_only and (offer.transfers or offer.return_transfers):
            continue
        key = deal_key(route, offer)
        current = cheapest.get(key)
        if current is None or offer.price < current.price:
            cheapest[key] = offer

    deals = []
    for key, offer in cheapest.items():
        previous = storage.last_price(route.key, key)
        if previous is None or offer.price < previous:
            deals.append(Deal(offer, previous, typical))
    deals.sort(key=lambda d: (d.offer.price, d.offer.departure_at))
    return deals


def check_route(
    route: Route,
    source: PriceSource,
    storage: Storage,
    notifier: Notifier,
    currency: str,
    places: Directory,
    dry_run: bool = False,
) -> list[Deal]:
    offers = relevant_offers(route, source.search(route), places)
    typical = None if route.anywhere else typical_price(offers)
    deals = find_deals(route, offers, storage, typical)

    # Самая низкая цена по каждому scope в этой проверке — для отметки 🔥 «рекорд».
    lowest: dict[str, int] = {}
    for offer in offers:
        scope = _scope(route, offer)
        lowest[scope] = min(offer.price, lowest.get(scope, offer.price))
    marked = []
    for deal in deals:
        best = storage.best_price(route.key, _scope(route, deal.offer))
        marked.append(replace(deal, record=best is not None and deal.offer.price < best))
    deals = marked

    title = describe_route(route, places.name, currency)
    if deals:
        notifier.send(route.chat_id, format_alert(route, deals, currency, places.name))
        log.info("%s: найдено вариантов — %d", title, len(deals))
    else:
        log.info("%s: новых дешёвых билетов нет", title)

    # Запоминаем только после успешной отправки, иначе оповещение потеряется.
    if not dry_run:
        storage.remember(route.key, [(deal_key(route, d.offer), d.offer) for d in deals])
        storage.update_best(route.key, lowest)
    return deals


def run_once(
    storage: Storage,
    source: PriceSource,
    notifier: Notifier,
    currency: str,
    places: Directory,
    dry_run: bool = False,
    today: date | None = None,
) -> RunSummary:
    today = today or date.today()
    if dry_run:
        routes = [r for r in storage.routes() if not is_expired(r.departure, today)]
    else:
        for route in storage.delete_expired_routes(today):
            _say_expired(route, notifier, currency, places)
        storage.prune(today)
        routes = storage.routes()

    if not routes:
        log.info("направлений пока нет — добавьте их в боте")

    found = failed = 0
    for route in routes:
        try:
            found += len(check_route(route, source, storage, notifier, currency, places, dry_run))
        except (SourceError, NotifyError) as e:
            failed += 1
            log.error("%s: %s", describe_route(route, places.name, currency), e)
    return RunSummary(found, failed)


def _say_expired(route: Route, notifier: Notifier, currency: str, places: Directory) -> None:
    text = f"Даты прошли, больше не слежу: {escape(describe_route(route, places.name, currency))}"
    try:
        notifier.send(route.chat_id, text)
    except NotifyError as e:
        log.error("не удалось сообщить об устаревшем направлении: %s", e)
