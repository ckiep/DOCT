import unittest
from datetime import date

from ticket_alert.bot import (
    MAX_ROUTES_PER_CHAT,
    MENU_ADD,
    MENU_CHEAP,
    MENU_CHECK,
    MENU_LIST,
    Bot,
    budget_options,
    parse_amount,
)
from ticket_alert.places import Places

from tests.helpers import FakeSource, FakeTelegram, make_offer, make_route, memory_storage

OWNER = 472989828


def message(text, chat_id=OWNER):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def callback(data, chat_id=OWNER, message_id=500):
    return {
        "update_id": 1,
        "callback_query": {"id": "cb", "data": data, "message": {"chat": {"id": chat_id}, "message_id": message_id}},
    }


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeTelegram()
        self.storage = memory_storage(self)
        self.source = FakeSource(self.offers)
        self.bot = Bot(
            self.api, self.storage, self.source, Places.builtin(), "kzt", OWNER, today=lambda: date(2026, 10, 22)
        )

    @staticmethod
    def offers(route):
        if route.destination is None:
            return [
                make_offer(20000, day=7, destination="CIT"),
                make_offer(25000, day=3, destination="DXB", transfers=1),
                make_offer(58802, day=4, destination="IST"),
            ]
        return [make_offer(58802, day=4), make_offer(61000, day=9)]

    def send(self, *updates):
        for update in updates:
            self.bot.handle_update(update)


class AddRouteTest(BotTestCase):
    def test_start_shows_help_and_menu(self):
        self.send(message("/start"))
        self.assertIn("Я слежу за ценами", self.api.last["text"])
        self.assertIn("keyboard", self.api.last["markup"])

    def test_add_route_with_buttons(self):
        self.send(message(MENU_ADD))
        self.assertIn("Откуда летим?", self.api.last["text"])
        self.assertIn(("Алматы", "o:ALA"), self.api.buttons())

        self.send(callback("o:ALA"))
        self.assertEqual(self.api.last["method"], "edit")
        self.assertIn(("🌍 Куда угодно — самые дешёвые", "d:*"), self.api.buttons())
        self.assertNotIn(("Алматы", "d:ALA"), self.api.buttons())

        self.send(callback("d:IST"))
        self.assertIn("Алматы → Стамбул", self.api.last["text"])
        self.assertIn(("Декабрь 2026", "m:2026-12"), self.api.buttons())

        self.send(callback("m:2026-12"))
        self.assertIn("58 802 ₸", self.api.last["text"])
        self.assertIn(("до 52 000 ₸ (−10%)", "b:52000"), self.api.buttons())
        self.assertIn(("до 58 802 ₸ (уже есть)", "b:58802"), self.api.buttons())

        self.send(callback("b:52000"))
        self.assertIn("Алматы → Стамбул, декабрь 2026, до 52 000 ₸", self.api.last["text"])

        self.send(callback("s:all"))
        routes = self.storage.routes(OWNER)
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(
            (route.origin, route.destination, route.departure, route.max_price, route.direct_only),
            ("ALA", "IST", "2026-12", 52000, False),
        )
        self.assertIn("✅ Слежу", self.api.last["text"])  # дешевле бюджета пока ничего — оповещения нет
        self.assertEqual(self.storage.home(OWNER), "ALA")

    def test_add_anywhere_route_by_typing(self):
        self.send(message(MENU_ADD), message("алматы"), callback("d:*"))
        self.send(message("декабрь"))
        self.assertIn("Сейчас самый дешёвый билет: <b>20 000 ₸</b>, Шымкент, 07.12.", self.api.last["text"])

        self.send(message("30к"))
        self.assertIn("куда угодно, декабрь 2026, до 30 000 ₸", self.api.last["text"])

        self.send(callback("s:direct"))
        route = self.storage.routes(OWNER)[0]
        self.assertIsNone(route.destination)
        self.assertTrue(route.direct_only)
        # Сразу после сохранения приходит подходящий билет: Шымкент прямым рейсом за 20 000.
        alert = self.api.last["text"]
        self.assertIn("🌍", alert)
        self.assertIn("Шымкент — <b>20 000 ₸</b>", alert)
        self.assertNotIn("Дубай", alert)  # с пересадкой

    def test_ambiguous_and_unknown_city(self):
        self.send(message(MENU_ADD), message("Ка"))
        self.assertEqual(self.api.last["text"], "Какой именно?")
        self.assertIn(("Караганда, Казахстан", "o:KGF"), self.api.buttons())

        self.send(message("Атлантида"))
        self.assertIn("Не нашёл такой город", self.api.last["text"])

    def test_same_city_twice_is_rejected(self):
        self.send(message(MENU_ADD), callback("o:ALA"), message("Алматы"))
        self.assertIn("совпадает с городом вылета", self.api.last["text"])

    def test_bad_date_and_amount(self):
        self.send(message(MENU_ADD), callback("o:ALA"), callback("d:IST"), message("вчера"))
        self.assertIn("Не понял дату", self.api.last["text"])
        self.send(message("05.12"), message("дёшево"))
        self.assertIn("Напишите сумму числом", self.api.last["text"])
        self.send(message("40 000 ₸"), callback("s:all"))
        self.assertEqual(self.storage.routes(OWNER)[0].departure, "2026-12-05")

    def test_cancel(self):
        self.send(message(MENU_ADD), callback("x"))
        self.assertEqual(self.api.last["text"], "Отменено.")
        self.send(callback("o:ALA"))
        self.assertEqual(self.api.answers[-1][1], "Это меню устарело — начните заново")

    def test_route_limit(self):
        for _ in range(MAX_ROUTES_PER_CHAT):
            self.storage.add_route(make_route(chat_id=OWNER))
        self.send(message(MENU_ADD), callback("o:ALA"), callback("d:IST"), callback("m:2026-12"))
        self.send(callback("b:52000"), callback("s:all"))
        self.assertIn("удалите лишние", self.api.last["text"])
        self.assertEqual(self.storage.count_routes(OWNER), MAX_ROUTES_PER_CHAT)


class CheapestTest(BotTestCase):
    def test_cheapest_anywhere_and_subscribe(self):
        self.send(message(MENU_CHEAP), callback("o:ALA"))
        self.assertIn("Из Алматы, куда угодно", self.api.last["text"])

        self.send(callback("m:2026-12"))
        text = self.api.last["text"]
        self.assertIn("Самые дешёвые билеты из Алматы, декабрь 2026", text)
        self.assertLess(text.index("Шымкент"), text.index("Дубай"))
        self.assertIsNone(self.source.searches[-1].destination)

        self.send(callback("w"))
        self.assertEqual(self.api.last["method"], "send")  # подборка остаётся в чате
        self.send(callback("b:18000"), callback("s:all"))
        route = self.storage.routes(OWNER)[0]
        self.assertEqual((route.destination, route.max_price), (None, 18000))

    def test_other_month(self):
        self.send(message(MENU_CHEAP), callback("o:ALA"), callback("m:2026-12"), callback("r"))
        self.assertIn("Когда летим?", self.api.last["text"])
        self.send(callback("m:2027-01"))
        self.assertEqual(self.source.searches[-1].departure, "2027-01")


class ListAndCheckTest(BotTestCase):
    def test_list_and_delete(self):
        first = self.storage.add_route(make_route(chat_id=OWNER))
        self.storage.add_route(make_route(chat_id=OWNER, destination=None, max_price=30000))
        self.storage.add_route(make_route(chat_id=1))  # чужое направление не показываем

        self.send(message(MENU_LIST))
        text = self.api.last["text"]
        self.assertIn("1. Алматы → Стамбул, декабрь 2026, до 60 000 ₸", text)
        self.assertIn("2. Алматы → куда угодно", text)
        self.assertIn(("🗑 1", f"del:{first.id}"), self.api.buttons())

        self.send(callback(f"del:{first.id}"))
        self.assertEqual(self.api.answers[-1][1], "Удалено")
        self.assertEqual(len(self.storage.routes(OWNER)), 1)
        self.assertNotIn("Стамбул", self.api.last["text"])

    def test_empty_list(self):
        self.send(message("/list"))
        self.assertIn("ни за чем не следите", self.api.last["text"])

    def test_check_now(self):
        self.storage.add_route(make_route(chat_id=OWNER, max_price=60000))
        self.send(message(MENU_CHECK))
        self.assertIn("58 802 ₸", self.api.last["text"])

        self.send(message(MENU_CHECK))
        self.assertIn("Новых или подешевевших билетов нет", self.api.last["text"])


class AccessTest(BotTestCase):
    def test_stranger_is_refused(self):
        self.send(message("/start", chat_id=999))
        self.assertIn("личный бот", self.api.last["text"])
        self.send(callback("add", chat_id=999))
        self.assertEqual(self.api.answers[-1][1], "Это личный бот.")
        self.assertEqual(len(self.api.sent), 1)

    def test_public_bot_serves_everyone(self):
        self.bot.public = True
        self.send(message(MENU_ADD, chat_id=999))
        self.assertIn("Откуда летим?", self.api.last["text"])

    def test_errors_do_not_stop_the_bot(self):
        self.source.error = RuntimeError("boom")
        with self.assertLogs("ticket_alert.bot", "ERROR"):
            self.send(message(MENU_ADD), callback("o:ALA"), callback("d:IST"), callback("m:2026-12"))
        self.assertEqual(self.api.answers[-1], ("cb", None))


class HelpersTest(unittest.TestCase):
    def test_parse_amount(self):
        for text, expected in {"60000": 60000, "60 000 ₸": 60000, "60к": 60000, "45.5": 455, "0": None, "много": None}.items():
            with self.subTest(text):
                self.assertEqual(parse_amount(text), expected)

    def test_budget_options(self):
        self.assertEqual(budget_options(58802), [(10, 52000), (20, 47000), (30, 41000)])
        self.assertEqual(budget_options(15050), [(10, 13500), (20, 12000), (30, 10500)])


if __name__ == "__main__":
    unittest.main()
