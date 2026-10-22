"""Тексты сообщений (HTML-разметка Telegram)."""

from __future__ import annotations

from html import escape
from typing import Callable, Iterable

from .dates import describe_departure
from .models import SMART_DISCOUNT, Deal, Offer, Route

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


def days(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} день"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} дня"
    return f"{count} дней"


def trip_length(min_days: int, max_days: int) -> str:
    """«на 5–9 дней»; склоняем по верхней границе."""
    return f"на {min_days}–{days(max_days)}"


def about_price(amount: int, currency: str) -> str:
    return "около " + format_price(round(amount, -2), currency)


def route_title(route: Route, names: Names) -> str:
    destination = "за границу" if route.anywhere else names(route.destination)
    arrow = "⇄" if route.round_trip else "→"
    title = f"{names(route.origin)} {arrow} {destination}, {describe_departure(route.departure)}"
    if route.round_trip:
        title += f", туда-обратно {trip_length(route.min_days, route.max_days)}"
    return title


def describe_route(route: Route, names: Names, currency: str) -> str:
    if route.smart:
        budget = f"только выгодные цены (от −{SMART_DISCOUNT}% к обычной)"
    else:
        budget = f"до {format_price(route.max_price, currency)}"
    text = f"{route_title(route, names)}, {budget}"
    if route.direct_only:
        text += ", только прямые"
    return text


def discount(price: int, typical: int | None) -> int:
    """На сколько процентов цена ниже обычной (0, если не ниже или сравнить не с чем)."""
    if not typical or price >= typical:
        return 0
    return round((typical - price) * 100 / typical)


def format_deal(deal: Deal, currency: str, names: Names, with_city: bool) -> str:
    offer = deal.offer
    price = f"<b>{format_price(offer.price, currency)}</b>"
    if deal.previous_price is not None:
        price += f" (было {format_price(deal.previous_price, currency)})"
    when = offer.departure_at.strftime("%d.%m %H:%M")
    if offer.return_at:
        when += f" → {offer.return_at:%d.%m} ({days(offer.trip_days)})"

    parts = [f"{escape(names(offer.destination))} — {price}, {when}" if with_city else f"{when} — {price}"]
    percent = discount(offer.price, deal.typical_price)
    if percent >= 5:
        parts.append(f"−{percent}% к обычной цене")
    if offer.return_at is None:
        parts.append(stops(offer.transfers))
    elif offer.transfers == offer.return_transfers == 0:
        parts.append("прямые рейсы")
    else:
        parts.append(f"туда {stops(offer.transfers)}, обратно {stops(offer.return_transfers)}")
    flight = f"{offer.airline} {offer.flight_number}".strip()
    if flight:
        parts.append(escape(flight))
    link = escape(offer.link, quote=True)
    mark = "🔥 " if deal.record else ""
    return f"• {mark}" + ", ".join(parts) + f' — <a href="{link}">купить</a>'


def format_alert(
    route: Route, deals: list[Deal], currency: str, names: Names, limit: int = MAX_ITEMS_PER_MESSAGE
) -> str:
    icon = "🌍" if route.anywhere else "✈️"
    typical = deals[0].typical_price if deals else None
    if route.smart and typical:
        summary = f"Выгодные цены — обычно на эти даты {about_price(typical, currency)}:"
    else:
        summary = f"Билеты не дороже {format_price(route.max_price, currency)}:"
    lines = [f"{icon} <b>{escape(route_title(route, names))}</b>", summary, ""]
    lines += [format_deal(d, currency, names, route.anywhere) for d in deals[:limit]]
    if len(deals) > limit:
        lines.append(f"…и ещё вариантов: {len(deals) - limit}")
    if any(d.record for d in deals[:limit]):
        lines += ["", "🔥 — самая низкая цена с тех пор, как я слежу за направлением"]
    return "\n".join(lines)


def format_channel_post(offer: Offer, typical: int, currency: str, names: Names, record: bool = False) -> str:
    """Пост для канала: одна находка, крупно и понятно без контекста."""
    percent = discount(offer.price, typical)
    arrow = "⇄" if offer.return_at else "→"
    icon = "🔥" if percent >= 30 else "✈️"
    lines = [
        f"{icon} <b>{escape(names(offer.origin))} {arrow} {escape(names(offer.destination))} — "
        f"{format_price(offer.price, currency)}</b>"
    ]
    if offer.return_at:
        lines.append(
            f"📅 Туда-обратно: {offer.departure_at:%d.%m %H:%M} → {offer.return_at:%d.%m} ({days(offer.trip_days)})"
        )
        legs = (
            "прямые рейсы"
            if offer.transfers == offer.return_transfers == 0
            else f"туда {stops(offer.transfers)}, обратно {stops(offer.return_transfers)}"
        )
    else:
        lines.append(f"📅 В одну сторону: {offer.departure_at:%d.%m} в {offer.departure_at:%H:%M}")
        legs = "прямой рейс" if offer.transfers == 0 else stops(offer.transfers)
    flight = f"{offer.airline} {offer.flight_number}".strip()
    lines.append(f"✈️ {legs.capitalize()}" + (f", {escape(flight)}" if flight else ""))
    lines.append(f"📉 −{percent}% к обычной цене ({about_price(typical, currency)})")
    if record:
        lines.append("🏆 Самая низкая цена, что я видел на этом направлении")
    lines += ["", "<i>Цена из недавних поисков на Aviasales — проверьте её перед покупкой.</i>"]
    return "\n".join(lines)


def cheapest_by_destination(offers: Iterable[Offer]) -> list[Offer]:
    best: dict[str, Offer] = {}
    for offer in offers:
        current = best.get(offer.destination)
        if current is None or offer.price < current.price:
            best[offer.destination] = offer
    return sorted(best.values(), key=lambda o: (o.price, o.departure_at))


def format_cheapest(
    route: Route,
    offers: Iterable[Offer],
    currency: str,
    names: Names,
    limit: int = MAX_CHEAPEST,
) -> str:
    where = f"из {escape(names(route.origin))}, {describe_departure(route.departure)}"
    if route.round_trip:
        where += f", туда-обратно {trip_length(route.min_days, route.max_days)}"
    best = cheapest_by_destination(offers)
    if not best:
        return f"Не нашёл билетов {where}. Попробуйте другой месяц."
    lines = [f"🔥 <b>Самые дешёвые билеты за границу {where}</b>", ""]
    lines += [format_deal(Deal(o, None), currency, names, with_city=True) for o in best[:limit]]
    lines += ["", "Цены из недавних поисков на Aviasales — перед покупкой проверьте по ссылке."]
    return "\n".join(lines)
