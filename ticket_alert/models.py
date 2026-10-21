from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# «Умный» режим: оповещать, когда цена хотя бы на столько процентов ниже обычной.
SMART_DISCOUNT = 15


@dataclass(frozen=True)
class Route:
    """Направление, за которым следим. destination=None — «куда угодно за границу»."""

    origin: str
    destination: str | None
    departure: str  # YYYY-MM или YYYY-MM-DD
    max_price: int
    direct_only: bool = False
    chat_id: int = 0
    id: int | None = None
    # Туда-обратно: сколько дней длится поездка. None — билет в одну сторону.
    min_days: int | None = None
    max_days: int | None = None
    # «Умный» режим: вместо бюджета — цена заметно ниже обычной.
    smart: bool = False

    @property
    def anywhere(self) -> bool:
        return self.destination is None

    @property
    def round_trip(self) -> bool:
        return self.min_days is not None

    @property
    def key(self) -> str:
        """Идентификатор для истории оповещений."""
        return f"route:{self.id}"


@dataclass(frozen=True)
class Offer:
    """Конкретное предложение из источника цен: в одну сторону или туда-обратно."""

    origin: str
    destination: str
    price: int
    departure_at: datetime
    airline: str
    flight_number: str
    transfers: int
    link: str
    return_at: datetime | None = None
    return_transfers: int = 0

    @property
    def trip_days(self) -> int | None:
        if self.return_at is None:
            return None
        return (self.return_at.date() - self.departure_at.date()).days


@dataclass(frozen=True)
class Deal:
    offer: Offer
    previous_price: int | None  # цена из прошлого оповещения, если было
    typical_price: int | None = None  # обычная цена на направление, если её можно оценить
    record: bool = False  # самая низкая цена с тех пор, как бот следит за направлением
