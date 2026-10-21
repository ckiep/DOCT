from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Route:
    """Маршрут, за которым следим."""

    name: str
    origin: str
    destination: str
    departure: str  # YYYY-MM или YYYY-MM-DD
    max_price: int
    return_date: str | None = None  # YYYY-MM или YYYY-MM-DD; None — в одну сторону
    direct_only: bool = False

    @property
    def key(self) -> str:
        """Идентификатор маршрута для хранения истории оповещений."""
        mode = "direct" if self.direct_only else "any"
        return f"{self.origin}-{self.destination}:{self.departure}:{self.return_date or ''}:{mode}"


@dataclass(frozen=True)
class Offer:
    """Конкретное предложение (билет) из источника цен."""

    price: int
    departure_at: datetime
    airline: str
    flight_number: str
    transfers: int
    link: str
    return_at: datetime | None = None
    return_transfers: int = 0

    @property
    def key(self) -> str:
        """Предложения с одинаковыми датами считаем одним вариантом: следим за его ценой."""
        back = self.return_at.date().isoformat() if self.return_at else ""
        return f"{self.departure_at.date().isoformat()}|{back}"
