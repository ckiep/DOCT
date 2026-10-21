"""Основная логика: найти дешёвые варианты и сообщить только о новых или подешевевших."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Iterable, Protocol

from .models import Offer, Route
from .notifiers import Notifier, NotifyError
from .storage import Storage
from .travelpayouts import SourceError

log = logging.getLogger(__name__)

MAX_ITEMS_PER_MESSAGE = 10

CURRENCY_SYMBOLS = {"rub": "₽", "usd": "$", "eur": "€", "kzt": "₸", "uah": "₴", "byn": "Br"}


class PriceSource(Protocol):
    def search(self, route: Route) -> list[Offer]: ...


@dataclass(frozen=True)
class Deal:
    offer: Offer
    previous_price: int | None  # цена из прошлого оповещения, если было


@dataclass(frozen=True)
class RunSummary:
    deals: int
    failed_routes: int


def find_deals(route: Route, offers: Iterable[Offer], storage: Storage) -> list[Deal]:
    # Самый дешёвый вариант на каждую комбинацию дат в пределах бюджета.
    cheapest: dict[str, Offer] = {}
    for offer in offers:
        if offer.price > route.max_price:
            continue
        if route.direct_only and (offer.transfers or offer.return_transfers):
            continue
        current = cheapest.get(offer.key)
        if current is None or offer.price < current.price:
            cheapest[offer.key] = offer

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
    dry_run: bool = False,
) -> list[Deal]:
    deals = find_deals(route, source.search(route), storage)
    if not deals:
        log.info("%s: новых дешёвых билетов нет", route.name)
        return deals

    notifier.send(format_message(route, deals, currency))
    # Запоминаем только после успешной отправки, иначе оповещение потеряется.
    if not dry_run:
        storage.remember(route.key, [d.offer for d in deals])
    log.info("%s: отправлено вариантов — %d", route.name, len(deals))
    return deals


def run_once(
    routes: Iterable[Route],
    source: PriceSource,
    storage: Storage,
    notifier: Notifier,
    currency: str,
    dry_run: bool = False,
    today: date | None = None,
) -> RunSummary:
    if not dry_run:
        storage.prune(today or date.today())

    found = failed = 0
    for route in routes:
        try:
            found += len(check_route(route, source, storage, notifier, currency, dry_run))
        except (SourceError, NotifyError) as e:
            failed += 1
            log.error("%s: %s", route.name, e)
    return RunSummary(found, failed)


def format_price(amount: int, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency.lower(), currency.upper())
    return f"{amount:,}".replace(",", " ") + f" {symbol}"


def _stops(count: int) -> str:
    if count == 0:
        return "прямой"
    if count == 1:
        return "1 пересадка"
    if count < 5:
        return f"{count} пересадки"
    return f"{count} пересадок"


def format_deal(deal: Deal, currency: str) -> str:
    offer = deal.offer
    dates = offer.departure_at.strftime("%d.%m %H:%M")
    if offer.return_at:
        dates += " → " + offer.return_at.strftime("%d.%m")
        stops = f"туда {_stops(offer.transfers)}, обратно {_stops(offer.return_transfers)}"
    else:
        stops = _stops(offer.transfers)

    price = f"<b>{format_price(offer.price, currency)}</b>"
    if deal.previous_price is not None:
        price += f" (было {format_price(deal.previous_price, currency)})"

    flight = escape(f"{offer.airline} {offer.flight_number}".strip())
    link = escape(offer.link, quote=True)
    return f'• {dates} — {price}, {stops}, {flight} — <a href="{link}">купить</a>'


def format_message(
    route: Route, deals: list[Deal], currency: str, limit: int = MAX_ITEMS_PER_MESSAGE
) -> str:
    lines = [
        f"✈️ <b>{escape(route.name)}</b>",
        f"Билеты не дороже {format_price(route.max_price, currency)}:",
        "",
    ]
    lines += [format_deal(d, currency) for d in deals[:limit]]
    if len(deals) > limit:
        lines.append(f"…и ещё вариантов: {len(deals) - limit}")
    return "\n".join(lines)
