import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ticket_alert.bot import Bot, serve
from ticket_alert.channel import CHANNEL_KEY, ChannelPoster, ChannelSettings, with_sub_id
from ticket_alert.places import Places

from tests.helpers import FakeSource, FakeTelegram, make_offer, memory_storage

NOON = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
CHANNEL = "@traveleasy_kz"
OWNER = 1


def market(cheap: dict[str, int], typical: int = 64000):
    """Источник цен: в подборке «куда угодно» — дешёвые города, а по датам в каждый город обычно дороже."""

    def offers(route):
        if route.departure != "2026-12" or route.round_trip:
            return []
        if route.destination is None:
            return [make_offer(price, day=4, destination=city) for city, price in cheap.items()] + [
                make_offer(9000, day=2, destination="CIT")  # внутренний рейс — в канал не попадёт
            ]
        if route.destination == "TAS":  # почти без скидки
            return [make_offer(p, day=d, destination="TAS") for p, d in ((30000, 7), (31000, 8), (32000, 9), (33000, 10))]
        price = cheap.get(route.destination, typical)
        return [make_offer(price, day=4, destination=route.destination)] + [
            make_offer(typical + i * 1000, day=d, destination=route.destination) for i, d in enumerate((9, 10, 12, 15))
        ]

    return offers


class ChannelTestCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeTelegram()
        self.storage = memory_storage(self)
        self.source = FakeSource(market({"IST": 40000, "TAS": 30000}))
        self.now = NOON
        self.settings = ChannelSettings(chat=CHANNEL, origins=("ALA",), months_ahead=2)
        self.poster = self.make_poster()

    def make_poster(self, **overrides):
        settings = replace(self.settings, **overrides)
        return ChannelPoster(self.api, self.source, self.storage, Places.builtin(), "kzt", settings, lambda: self.now)

    def posts(self):
        return [m for m in self.api.sent if m["chat_id"] == CHANNEL]


class ChannelPosterTest(ChannelTestCase):
    def test_posts_only_real_deals_abroad_with_buttons(self):
        self.assertEqual(self.poster.run(), 1)

        [post] = self.posts()
        self.assertIn("🔥 <b>Алматы → Стамбул — 40 000 ₸</b>", post["text"])
        self.assertIn("📅 В одну сторону: 04.12 в 05:45", post["text"])
        self.assertIn("−38% к обычной цене (около 65 000 ₸)", post["text"])
        self.assertNotIn("Ташкент", post["text"])  # скидка всего 5%
        buttons = post["markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["text"], "✈️ Купить билет")
        self.assertEqual(buttons[1][0]["url"], "https://t.me/traveleasy_bot")

    def test_same_deal_is_not_repeated_unless_cheaper_or_a_week_later(self):
        self.poster.run()
        self.assertEqual(self.poster.run(), 0)

        self.source.offers = market({"IST": 37000})  # на 7,5% дешевле прошлого поста
        self.assertEqual(self.poster.run(), 1)
        self.assertIn("🏆", self.posts()[-1]["text"])  # ниже всего, что видели раньше

        self.assertEqual(self.poster.run(), 0)
        week_ago = (self.now - timedelta(days=8)).isoformat(timespec="seconds")
        self.storage.conn.execute("UPDATE notified SET notified_at = ? WHERE route_key = ?", (week_ago, CHANNEL_KEY))
        self.assertEqual(self.poster.run(), 1)

    def test_limits_per_run_and_per_day(self):
        self.source.offers = market({"IST": 40000, "DXB": 41000, "TBS": 42000, "BKK": 43000})
        poster = self.make_poster(max_posts_per_run=2, max_posts_per_day=3)
        self.assertEqual(poster.run(), 2)
        self.assertEqual(poster.run(), 1)
        self.assertEqual(poster.run(), 0)
        self.assertEqual(len(self.posts()), 3)
        # Самые большие скидки — первыми.
        self.assertIn("Стамбул", self.posts()[0]["text"])

    def test_quiet_hours(self):
        self.now = NOON.replace(hour=3)
        self.assertEqual(self.poster.run(), 0)
        self.assertEqual(self.source.searches, [])
        self.assertEqual(self.poster.run(force=True), 1)

    def test_sub_id_marks_channel_links(self):
        link = "https://www.aviasales.kz/search/ALA0412IST1?t=PC1&marker=123456"
        self.assertEqual(with_sub_id(link, "channel"), "https://www.aviasales.kz/search/ALA0412IST1?t=PC1&marker=123456.channel")
        self.assertEqual(with_sub_id(link.replace("123456", "123456.bot"), "channel"), with_sub_id(link, "channel"))
        self.assertEqual(with_sub_id("https://x.kz/search?t=1", "channel"), "https://x.kz/search?t=1")
        self.assertEqual(with_sub_id(link, ""), link)

        self.source.offers = lambda r: [replace(o, link=link) for o in market({"IST": 40000})(r)]
        self.poster.run()
        self.assertTrue(self.posts()[0]["markup"]["inline_keyboard"][0][0]["url"].endswith("marker=123456.channel"))


class PostCommandTest(ChannelTestCase):
    def make_bot(self, channel):
        return Bot(self.api, self.storage, self.source, Places.builtin(), "kzt", OWNER, channel=channel)

    def command(self, bot, chat_id=OWNER):
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": chat_id}, "text": "/post"}})
        return self.api.last["text"]

    def test_owner_posts_now(self):
        self.now = NOON.replace(hour=3)  # ночью тоже — это ручной запуск
        self.assertEqual(self.command(self.make_bot(self.poster)), "Опубликовано в канал: 1.")
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("Новых находок", self.command(self.make_bot(self.poster)))

    def test_not_configured(self):
        self.assertIn("Автопостинг не настроен", self.command(self.make_bot(None)))

    def test_bot_is_not_admin_of_the_channel(self):
        self.api.fail_chats.add(CHANNEL)
        self.assertIn("администратор канала", self.command(self.make_bot(self.poster)))

    def test_only_owner(self):
        bot = self.make_bot(self.poster)
        bot.public = True
        self.command(bot, chat_id=999)
        self.assertEqual(self.posts(), [])


class ServeTest(unittest.TestCase):
    def test_jobs_run_on_their_own_schedule(self):
        now = [0.0]
        runs = []

        class Api:
            def call(self, method, params=None, timeout=None):
                return {"username": "bot"}

            def set_commands(self, commands):
                pass

            def get_updates(self, offset, timeout):
                now[0] += timeout
                if now[0] > 3 * 3600:
                    raise KeyboardInterrupt
                return []

        jobs = [(60, lambda: runs.append(("check", now[0]))), (180, lambda: runs.append(("channel", now[0])))]
        with self.assertRaises(KeyboardInterrupt):
            serve(Api(), None, jobs, clock=lambda: now[0], sleep=lambda s: None)

        self.assertEqual([t for name, t in runs if name == "check"], [0, 3600, 7200, 10800])
        self.assertEqual([t for name, t in runs if name == "channel"], [0, 10800])


if __name__ == "__main__":
    unittest.main()
