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
                make_offer(15000, day=2, destination="CIT"),  # внутренний рейс — не показываем
                make_offer(22000, day=7, destination="TAS"),
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
        self.assertIn(("🌍 Куда угодно за границу", "d:*"), self.api.buttons())
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
        self.assertIn("Сейчас самый дешёвый билет: <b>22 000 ₸</b>, Ташкент, 07.12.", self.api.last["text"])

        self.send(message("30к"))
        self.assertIn("Алматы → за границу, декабрь 2026, до 30 000 ₸", self.api.last["text"])

        self.send(callback("s:direct"))
        route = self.storage.routes(OWNER)[0]
        self.assertIsNone(route.destination)
        self.assertTrue(route.direct_only)
        # Сразу после сохранения приходит подходящий билет: Ташкент прямым рейсом за 22 000.
        alert = self.api.last["text"]
        self.assertIn("🌍", alert)
        self.assertIn("Ташкент — <b>22 000 ₸</b>", alert)
        self.assertNotIn("Шымкент", alert)
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
        self.assertIn("Из Алматы за границу", self.api.last["text"])

        self.send(callback("m:2026-12"))
        text = self.api.last["text"]
        self.assertIn("Самые дешёвые билеты за границу из Алматы, декабрь 2026", text)
        self.assertLess(text.index("Ташкент"), text.index("Дубай"))
        self.assertNotIn("Шымкент", text)
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
        self.assertIn("2. Алматы → за границу", text)
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


BROTHER = 555


def from_brother(update):
    update["message"]["from"] = {"first_name": "Арман", "username": "arman"}
    return update


class AccessTest(BotTestCase):
    def messages_to(self, chat_id):
        return [m["text"] for m in self.api.sent if m["chat_id"] == chat_id]

    def test_stranger_asks_owner_for_access(self):
        self.send(from_brother(message("/start", chat_id=BROTHER)))

        self.assertIn("отправил владельцу запрос", self.messages_to(BROTHER)[-1])
        request = self.api.last
        self.assertEqual(request["chat_id"], OWNER)
        self.assertIn("Арман (@arman)", request["text"])
        self.assertIn(f"ID {BROTHER}", request["text"])
        self.assertEqual(self.api.buttons(), [("✅ Разрешить", f"acc:{BROTHER}"), ("✖ Отклонить", f"rej:{BROTHER}")])

        # Повторные сообщения не засыпают владельца запросами.
        self.send(from_brother(message("привет", chat_id=BROTHER)))
        self.assertIn("Запрос уже у владельца", self.messages_to(BROTHER)[-1])
        self.assertEqual(len(self.messages_to(OWNER)), 1)

    def test_owner_allows_and_brother_can_use_the_bot(self):
        self.send(from_brother(message("/start", chat_id=BROTHER)), callback(f"acc:{BROTHER}"))

        self.assertIn("теперь может пользоваться ботом", self.messages_to(OWNER)[-1])
        self.assertIn("Доступ открыт", self.messages_to(BROTHER)[-1])

        self.send(message(MENU_ADD, chat_id=BROTHER), callback("o:NQZ", chat_id=BROTHER))
        self.send(callback("d:IST", chat_id=BROTHER), callback("m:2026-12", chat_id=BROTHER))
        self.send(callback("b:50000", chat_id=BROTHER), callback("s:all", chat_id=BROTHER))
        self.assertEqual(len(self.storage.routes(BROTHER)), 1)
        self.assertEqual(self.storage.routes(OWNER), [])

    def test_owner_rejects(self):
        self.send(from_brother(message("/start", chat_id=BROTHER)), callback(f"rej:{BROTHER}"))
        self.assertIn("Отклонено", self.messages_to(OWNER)[-1])
        self.send(message("/start", chat_id=BROTHER))
        self.assertIn("не открыл вам доступ", self.messages_to(BROTHER)[-1])

    def test_only_owner_decides(self):
        self.storage.set_user(BROTHER, "Арман", "allowed")
        self.send(callback("acc:777", chat_id=BROTHER))
        self.assertEqual(self.api.answers[-1][1], "Это может только владелец бота")
        self.assertIsNone(self.storage.user_status(777))

    def test_users_list_and_revoke(self):
        self.storage.set_user(BROTHER, "Арман (@arman)", "allowed")
        self.storage.add_route(make_route(chat_id=BROTHER))

        self.send(message("/users"))
        self.assertIn("Арман (@arman) — направлений: 1", self.api.last["text"])

        self.send(callback(f"rm:{BROTHER}"))
        self.assertEqual(self.api.answers[-1][1], "Доступ закрыт: Арман (@arman)")
        self.assertIsNone(self.storage.user_status(BROTHER))
        self.assertEqual(self.storage.routes(BROTHER), [])
        self.assertIn("никто не пользуется", self.api.last["text"])

        self.send(message("/start", chat_id=BROTHER))  # может попросить доступ снова
        self.assertIn("отправил владельцу запрос", self.messages_to(BROTHER)[-1])

    def test_users_command_is_for_owner_only(self):
        self.storage.set_user(BROTHER, "Арман", "allowed")
        self.send(message("/users", chat_id=BROTHER))
        self.assertNotIn("Кто ещё пользуется", self.api.last["text"])

    def test_stranger_buttons_do_nothing(self):
        self.send(callback("add", chat_id=999))
        self.assertEqual(self.api.answers[-1][1], "Это личный бот.")
        self.assertEqual(self.api.sent, [])

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
