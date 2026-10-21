"""Тексты сообщений (HTML-разметка Telegram)."""

from __future__ import annotations

from html import escape
from typing import Callable, Iterable

from .dates import describe_departure
from .models import Deal, Offer, Route

Names = Callable[[str], str]

CURRENCY_SYMBOLS = {"rub": "₽", "usd": "$", "eur": "€", "kzt": "₸", "uah": "₴", "byn": "Br"}

MAX_ITEMS_PER_MESSAGE = 10
MAX_CHEAPEST = 20


def currency_symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(currency.lower(), currency.upper())


def format_price(amount: int, currency: str) -> str:
    return f"{amount:,}".replace(",", " ") + f" {currency_symbol(currency)}"


def stops(count: int) -> str:
    if count == 0:
        return "прямой"
    if count == 1:
        return "1 пересадка"
    if count < 5:
        return f"{count} пересадки"
    return f"{count} пересадок"


def route_title(route: Route, names: Names) -> str:
    destination = "за границу" if route.anywhere else names(route.destination)
    return f"{names(route.origin)} → {destination}, {describe_departure(route.departure)}"


def describe_route(route: Route, names: Names, currency: str) -> str:
    text = f"{route_title(route, names)}, до {format_price(route.max_price, currency)}"
    if route.direct_only:
        text += ", только прямые"
    return text


def format_deal(deal: Deal, currency: str, names: Names, with_city: bool) -> str:
    offer = deal.offer
    price = f"<b>{format_price(offer.price, currency)}</b>"
    if deal.previous_price is not None:
        price += f" (было {format_price(deal.previous_price, currency)})"
    when = offer.departure_at.strftime("%d.%m %H:%M")

    parts = [f"{escape(names(offer.destination))} — {price}, {when}" if with_city else f"{when} — {price}"]
    parts.append(stops(offer.transfers))
    flight = f"{offer.airline} {offer.flight_number}".strip()
    if flight:
        parts.append(escape(flight))
    link = escape(offer.link, quote=True)
    return "• " + ", ".join(parts) + f' — <a href="{link}">купить</a>'


def format_alert(
    route: Route, deals: list[Deal], currency: str, names: Names, limit: int = MAX_ITEMS_PER_MESSAGE
) -> str:
    icon = "🌍" if route.anywhere else "✈️"
    lines = [
        f"{icon} <b>{escape(route_title(route, names))}</b>",
        f"Билеты не дороже {format_price(route.max_price, currency)}:",
        "",
    ]
    lines += [format_deal(d, currency, names, route.anywhere) for d in deals[:limit]]
    if len(deals) > limit:
        lines.append(f"…и ещё вариантов: {len(deals) - limit}")
    return "\n".join(lines)


def cheapest_by_destination(offers: Iterable[Offer]) -> list[Offer]:
    best: dict[str, Offer] = {}
    for offer in offers:
        current = best.get(offer.destination)
        if current is None or offer.price < current.price:
            best[offer.destination] = offer
    return sorted(best.values(), key=lambda o: (o.price, o.departure_at))


def format_cheapest(
    origin: str,
    departure: str,
    offers: Iterable[Offer],
    currency: str,
    names: Names,
    limit: int = MAX_CHEAPEST,
) -> str:
    where = f"из {escape(names(origin))}, {describe_departure(departure)}"
    best = cheapest_by_destination(offers)
    if not best:
        return f"Не нашёл билетов {where}. Попробуйте другой месяц."
    lines = [f"🔥 <b>Самые дешёвые билеты за границу {where}</b>", ""]
    lines += [format_deal(Deal(o, None), currency, names, with_city=True) for o in best[:limit]]
    lines += ["", "Цены из недавних поисков на Aviasales — перед покупкой проверьте по ссылке."]
    return "\n".join(lines)
