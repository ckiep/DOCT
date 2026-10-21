"""Проверка цен: найти дешёвые варианты и сообщить только о новых или подешевевших."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Iterable, Protocol

from .dates import is_expired
from .formatting import describe_route, format_alert
from .models import Deal, Offer, Route
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
    """«Куда угодно» означает за границу: внутренние рейсы своей страны не показываем."""
    if not route.anywhere:
        return list(offers)
    return [o for o in offers if not places.same_country(route.origin, o.destination)]


@dataclass(frozen=True)
class RunSummary:
    deals: int
    failed_routes: int


def deal_key(route: Route, offer: Offer) -> str:
    # «Куда угодно»: следим за ценой в каждый город, а не за каждой датой,
    # иначе смена самой дешёвой даты присылала бы город повторно.
    if route.anywhere:
        return offer.destination
    return offer.departure_at.date().isoformat()


def find_deals(route: Route, offers: Iterable[Offer], storage: Storage) -> list[Deal]:
    cheapest: dict[str, Offer] = {}
    for offer in offers:
        if offer.price > route.max_price:
            continue
        if route.direct_only and offer.transfers:
            continue
        key = deal_key(route, offer)
        current = cheapest.get(key)
        if current is None or offer.price < current.price:
            cheapest[key] = offer

    deals = []
    for key, offer in cheapest.items():
        previous = storage.last_price(route.key, key)
        if previous is None or offer.price < previous:
            deals.append(Deal(offer, previous))
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
    deals = find_deals(route, offers, storage)
    title = describe_route(route, places.name, currency)
    if not deals:
        log.info("%s: новых дешёвых билетов нет", title)
        return deals

    notifier.send(route.chat_id, format_alert(route, deals, currency, places.name))
    # Запоминаем только после успешной отправки, иначе оповещение потеряется.
    if not dry_run:
        storage.remember(route.key, [(deal_key(route, d.offer), d.offer) for d in deals])
    log.info("%s: найдено вариантов — %d", title, len(deals))
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
