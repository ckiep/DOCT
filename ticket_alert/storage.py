"""История отправленных оповещений, чтобы не присылать одно и то же повторно."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Offer


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notified (
                route_key TEXT NOT NULL,
                offer_key TEXT NOT NULL,
                price INTEGER NOT NULL,
                departure_date TEXT NOT NULL,
                notified_at TEXT NOT NULL,
                PRIMARY KEY (route_key, offer_key)
            )
            """
        )
        self.conn.commit()

    def last_price(self, route_key: str, offer_key: str) -> int | None:
        row = self.conn.execute(
            "SELECT price FROM notified WHERE route_key = ? AND offer_key = ?",
            (route_key, offer_key),
        ).fetchone()
        return row[0] if row else None

    def remember(self, route_key: str, offers: Iterable[Offer]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.executemany(
            """
            INSERT INTO notified (route_key, offer_key, price, departure_date, notified_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (route_key, offer_key)
            DO UPDATE SET price = excluded.price, notified_at = excluded.notified_at
            """,
            [
                (route_key, o.key, o.price, o.departure_at.date().isoformat(), now)
                for o in offers
            ],
        )
        self.conn.commit()

    def prune(self, today: date) -> int:
        """Удаляет записи о рейсах, дата вылета которых уже прошла."""
        cursor = self.conn.execute(
            "DELETE FROM notified WHERE departure_date < ?", (today.isoformat(),)
        )
        self.conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self.conn.close()
