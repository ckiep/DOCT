"""SQLite: направления пользователей и история отправленных оповещений."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from .dates import is_expired
from .models import Offer, Route

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notified (
    route_key TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    price INTEGER NOT NULL,
    departure_date TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (route_key, offer_key)
);
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT,
    departure TEXT NOT NULL,
    max_price INTEGER NOT NULL,
    direct_only INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    min_days INTEGER,
    max_days INTEGER,
    smart INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    home TEXT
);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending, allowed или rejected
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS best_prices (
    route_key TEXT NOT NULL,
    scope TEXT NOT NULL,  -- город для «куда угодно», иначе *
    price INTEGER NOT NULL,
    PRIMARY KEY (route_key, scope)
);
"""

# Колонки, добавленные после первой версии: старую базу дополняем, а не пересоздаём.
_ROUTE_COLUMNS = {
    "min_days": "INTEGER",
    "max_days": "INTEGER",
    "smart": "INTEGER NOT NULL DEFAULT 0",
}

_ROUTE_FIELDS = "id, chat_id, origin, destination, departure, max_price, direct_only, min_days, max_days, smart"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(routes)")}
        for column, kind in _ROUTE_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE routes ADD COLUMN {column} {kind}")
        self.conn.commit()

    # --- направления

    def add_route(self, route: Route) -> Route:
        cursor = self.conn.execute(
            """
            INSERT INTO routes (chat_id, origin, destination, departure, max_price, direct_only,
                                created_at, min_days, max_days, smart)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route.chat_id,
                route.origin,
                route.destination,
                route.departure,
                route.max_price,
                int(route.direct_only),
                _now(),
                route.min_days,
                route.max_days,
                int(route.smart),
            ),
        )
        self.conn.commit()
        return replace(route, id=cursor.lastrowid)

    def routes(self, chat_id: int | None = None) -> list[Route]:
        query = f"SELECT {_ROUTE_FIELDS} FROM routes"
        args: tuple = ()
        if chat_id is not None:
            query += " WHERE chat_id = ?"
            args = (chat_id,)
        rows = self.conn.execute(query + " ORDER BY id", args).fetchall()
        return [
            Route(
                id=row[0],
                chat_id=row[1],
                origin=row[2],
                destination=row[3],
                departure=row[4],
                max_price=row[5],
                direct_only=bool(row[6]),
                min_days=row[7],
                max_days=row[8],
                smart=bool(row[9]),
            )
            for row in rows
        ]

    def count_routes(self, chat_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM routes WHERE chat_id = ?", (chat_id,)).fetchone()[0]

    def delete_route(self, chat_id: int, route_id: int) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM routes WHERE id = ? AND chat_id = ?", (route_id, chat_id)
        )
        if cursor.rowcount:
            for table in ("notified", "best_prices"):
                self.conn.execute(f"DELETE FROM {table} WHERE route_key = ?", (f"route:{route_id}",))
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_expired_routes(self, today: date) -> list[Route]:
        expired = [r for r in self.routes() if is_expired(r.departure, today)]
        for route in expired:
            self.delete_route(route.chat_id, route.id)
        return expired

    def delete_routes(self, chat_id: int) -> None:
        for route in self.routes(chat_id):
            self.delete_route(chat_id, route.id)

    # --- кто, кроме владельца, может пользоваться ботом

    def user_status(self, chat_id: int) -> str | None:
        row = self.conn.execute("SELECT status FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0] if row else None

    def user_name(self, chat_id: int) -> str | None:
        row = self.conn.execute("SELECT name FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0] if row else None

    def set_user(self, chat_id: int, name: str, status: str) -> None:
        self.conn.execute(
            "INSERT INTO users (chat_id, name, status, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (chat_id) DO UPDATE SET name = excluded.name, status = excluded.status, "
            "updated_at = excluded.updated_at",
            (chat_id, name, status, _now()),
        )
        self.conn.commit()

    def users(self, status: str) -> list[tuple[int, str]]:
        return self.conn.execute(
            "SELECT chat_id, name FROM users WHERE status = ? ORDER BY updated_at", (status,)
        ).fetchall()

    def forget_user(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        self.conn.commit()

    # --- город вылета по умолчанию

    def home(self, chat_id: int) -> str | None:
        row = self.conn.execute("SELECT home FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0] if row else None

    def set_home(self, chat_id: int, code: str) -> None:
        self.conn.execute(
            "INSERT INTO chats (chat_id, home) VALUES (?, ?) "
            "ON CONFLICT (chat_id) DO UPDATE SET home = excluded.home",
            (chat_id, code),
        )
        self.conn.commit()

    # --- история оповещений

    def last_price(self, route_key: str, offer_key: str) -> int | None:
        row = self.conn.execute(
            "SELECT price FROM notified WHERE route_key = ? AND offer_key = ?",
            (route_key, offer_key),
        ).fetchone()
        return row[0] if row else None

    def last_notified(self, route_key: str, offer_key: str) -> tuple[int, datetime] | None:
        """Цена и время последнего оповещения (UTC)."""
        row = self.conn.execute(
            "SELECT price, notified_at FROM notified WHERE route_key = ? AND offer_key = ?",
            (route_key, offer_key),
        ).fetchone()
        return (row[0], datetime.fromisoformat(row[1])) if row else None

    def count_notified_since(self, route_key: str, since: datetime) -> int:
        since_text = since.astimezone(timezone.utc).isoformat(timespec="seconds")
        return self.conn.execute(
            "SELECT COUNT(*) FROM notified WHERE route_key = ? AND notified_at >= ?",
            (route_key, since_text),
        ).fetchone()[0]

    def remember(
        self, route_key: str, offers: Iterable[tuple[str, Offer]], at: datetime | None = None
    ) -> None:
        """Запоминает отправленные варианты: пары (ключ варианта, предложение)."""
        now = at.astimezone(timezone.utc).isoformat(timespec="seconds") if at else _now()
        self.conn.executemany(
            """
            INSERT INTO notified (route_key, offer_key, price, departure_date, notified_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (route_key, offer_key)
            DO UPDATE SET price = excluded.price,
                          departure_date = excluded.departure_date,
                          notified_at = excluded.notified_at
            """,
            [
                (route_key, key, o.price, o.departure_at.date().isoformat(), now)
                for key, o in offers
            ],
        )
        self.conn.commit()

    def best_price(self, route_key: str, scope: str) -> int | None:
        row = self.conn.execute(
            "SELECT price FROM best_prices WHERE route_key = ? AND scope = ?", (route_key, scope)
        ).fetchone()
        return row[0] if row else None

    def update_best(self, route_key: str, prices: dict[str, int]) -> None:
        """Запоминает самую низкую увиденную цену по каждому scope."""
        self.conn.executemany(
            "INSERT INTO best_prices (route_key, scope, price) VALUES (?, ?, ?) "
            "ON CONFLICT (route_key, scope) DO UPDATE SET price = MIN(price, excluded.price)",
            [(route_key, scope, price) for scope, price in prices.items()],
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
