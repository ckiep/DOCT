from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Route:
    """Направление, за которым следим. destination=None — «куда угодно»."""

    origin: str
    destination: str | None
    departure: str  # YYYY-MM или YYYY-MM-DD
    max_price: int
    direct_only: bool = False
    chat_id: int = 0
    id: int | None = None

    @property
    def anywhere(self) -> bool:
        return self.destination is None

    @property
    def key(self) -> str:
        """Идентификатор для истории оповещений."""
        return f"route:{self.id}"


@dataclass(frozen=True)
class Offer:
    """Конкретное предложение (билет в одну сторону) из источника цен."""

    origin: str
    destination: str
    price: int
    departure_at: datetime
    airline: str
    flight_number: str
    transfers: int
    link: str


@dataclass(frozen=True)
class Deal:
    offer: Offer
    previous_price: int | None  # цена из прошлого оповещения, если было
