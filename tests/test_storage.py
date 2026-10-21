import sqlite3
import tempfile
import unittest
from pathlib import Path

from ticket_alert.storage import Storage

from tests.helpers import make_route, memory_storage


class StorageTest(unittest.TestCase):
    def test_old_database_is_upgraded(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "ticket_alert.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE routes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, "
            "origin TEXT NOT NULL, destination TEXT, departure TEXT NOT NULL, max_price INTEGER NOT NULL, "
            "direct_only INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO routes (chat_id, origin, destination, departure, max_price, created_at) "
            "VALUES (1, 'ALA', 'IST', '2026-12', 60000, 'now')"
        )
        conn.commit()
        conn.close()

        storage = Storage(path)
        self.addCleanup(storage.close)
        [old] = storage.routes()
        self.assertEqual((old.destination, old.min_days, old.smart), ("IST", None, False))

        new = storage.add_route(make_route(min_days=5, max_days=9, smart=True))
        self.assertEqual(storage.routes()[-1], new)

    def test_best_price_keeps_the_minimum_and_is_removed_with_route(self):
        storage = memory_storage(self)
        route = storage.add_route(make_route())
        storage.update_best(route.key, {"*": 60000})
        storage.update_best(route.key, {"*": 65000})
        self.assertEqual(storage.best_price(route.key, "*"), 60000)
        storage.update_best(route.key, {"*": 55000})
        self.assertEqual(storage.best_price(route.key, "*"), 55000)

        storage.delete_route(route.chat_id, route.id)
        self.assertIsNone(storage.best_price(route.key, "*"))


if __name__ == "__main__":
    unittest.main()
