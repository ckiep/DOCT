import unittest
from dataclasses import replace
from datetime import date, timedelta

from ticket_alert.checker import check_route, deal_key, find_deals, relevant_offers, run_once, typical_price
from ticket_alert.formatting import format_alert, format_cheapest
from ticket_alert.models import Deal
from ticket_alert.telegram import NotifyError
from ticket_alert.travelpayouts import SourceError

from tests.helpers import FakeNotifier, FakeSource, make_offer, make_route, memory_storage, names, PLACES


class FindDealsTest(unittest.TestCase):
    def setUp(self):
        self.storage = memory_storage(self)
        self.route = self.storage.add_route(make_route(max_price=60000))

    def test_keeps_only_offers_within_budget_sorted_by_price(self):
        offers = [make_offer(59000, day=1), make_offer(65000, day=2), make_offer(60000, day=3)]
        deals = find_deals(self.route, offers, self.storage)
        self.assertEqual([d.offer.price for d in deals], [59000, 60000])

    def test_picks_cheapest_offer_per_date(self):
        offers = [make_offer(59500, day=1), make_offer(58000, day=1, flight_number="777")]
        deals = find_deals(self.route, offers, self.storage)
        self.assertEqual([d.offer.flight_number for d in deals], ["777"])

    def test_direct_only_skips_flights_with_transfers(self):
        route = self.storage.add_route(make_route(direct_only=True))
        offers = [make_offer(50000, day=1, transfers=1), make_offer(55000, day=2)]
        self.assertEqual([d.offer.price for d in find_deals(route, offers, self.storage)], [55000])

    def test_skips_already_notified_price_and_reports_drops(self):
        self.storage.remember(
            self.route.key, [("2026-12-01", make_offer(59000, day=1)), ("2026-12-02", make_offer(59000, day=2))]
        )
        deals = find_deals(self.route, [make_offer(59000, day=1), make_offer(58500, day=2)], self.storage)
        self.assertEqual([(d.offer.price, d.previous_price) for d in deals], [(58500, 59000)])

    def test_anywhere_tracks_price_per_city_not_per_date(self):
        route = self.storage.add_route(make_route(destination=None, max_price=30000))
        self.storage.remember(route.key, [("TAS", make_offer(20000, day=1, destination="TAS"))])
        offers = [
            make_offer(21000, day=9, destination="TAS"),  # другая дата, но не дешевле — молчим
            make_offer(25000, day=3, destination="DXB"),
            make_offer(90000, day=3, destination="IST"),
        ]
        deals = find_deals(route, offers, self.storage)
        self.assertEqual([d.offer.destination for d in deals], ["DXB"])


class CheckRouteTest(unittest.TestCase):
    def setUp(self):
        self.storage = memory_storage(self)
        self.route = self.storage.add_route(make_route(chat_id=7))

    def check(self, source, notifier, dry_run=False):
        return check_route(self.route, source, self.storage, notifier, "kzt", PLACES, dry_run)

    def test_sends_to_route_chat_once_and_remembers(self):
        source = FakeSource([make_offer(58802)])
        notifier = FakeNotifier()
        self.check(source, notifier)
        self.check(source, notifier)

        self.assertEqual(len(notifier.messages), 1)
        chat_id, text = notifier.messages[0]
        self.assertEqual(chat_id, 7)
        self.assertIn("58 802 ₸", text)
        self.assertIn("Алматы → Стамбул, декабрь 2026", text)

    def test_anywhere_skips_domestic_flights(self):
        route = self.storage.add_route(make_route(chat_id=7, destination=None, max_price=50000))
        source = FakeSource([make_offer(15000, destination="CIT"), make_offer(40000, destination="TAS")])
        notifier = FakeNotifier()

        deals = check_route(route, source, self.storage, notifier, "kzt", PLACES)

        self.assertEqual([d.offer.destination for d in deals], ["TAS"])
        self.assertNotIn("Шымкент", notifier.messages[0][1])

    def test_dry_run_does_not_remember(self):
        source = FakeSource([make_offer(58802)])
        notifier = FakeNotifier()
        self.check(source, notifier, dry_run=True)
        self.check(source, notifier, dry_run=True)
        self.assertEqual(len(notifier.messages), 2)

    def test_failed_send_is_retried_next_time(self):
        source = FakeSource([make_offer(58802)])
        with self.assertRaises(NotifyError):
            self.check(source, FakeNotifier(NotifyError("down")))
        notifier = FakeNotifier()
        self.check(source, notifier)
        self.assertEqual(len(notifier.messages), 1)


class RunOnceTest(unittest.TestCase):
    def test_checks_all_routes_and_continues_after_error(self):
        storage = memory_storage(self)
        storage.add_route(make_route(chat_id=1))
        storage.add_route(make_route(chat_id=2, destination="DXB"))
        notifier = FakeNotifier()

        def offers(route):
            if route.destination == "IST":
                raise SourceError("boom")
            return [make_offer(50000, destination="DXB")]

        class Source:
            def search(self, route):
                return offers(route)

        summary = run_once(storage, Source(), notifier, "kzt", PLACES, today=date(2026, 11, 1))
        self.assertEqual((summary.deals, summary.failed_routes), (1, 1))
        self.assertEqual([chat for chat, _ in notifier.messages], [2])

    def test_removes_expired_routes_and_tells_the_user(self):
        storage = memory_storage(self)
        storage.add_route(make_route(chat_id=5, departure="2026-10"))
        storage.add_route(make_route(chat_id=5, departure="2026-12"))
        notifier = FakeNotifier()

        run_once(storage, FakeSource(), notifier, "kzt", PLACES, today=date(2026, 11, 2))

        self.assertEqual([r.departure for r in storage.routes()], ["2026-12"])
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Даты прошли", notifier.messages[0][1])

    def test_dry_run_leaves_storage_untouched(self):
        storage = memory_storage(self)
        storage.add_route(make_route(departure="2026-10"))
        run_once(storage, FakeSource(), FakeNotifier(), "kzt", PLACES, dry_run=True, today=date(2026, 11, 2))
        self.assertEqual(len(storage.routes()), 1)


def round_trip(price, day=5, length=7, **overrides):
    offer = make_offer(price, day=day, **overrides)
    return replace(offer, return_at=offer.departure_at + timedelta(days=length))


class SmartTest(unittest.TestCase):
    def setUp(self):
        self.storage = memory_storage(self)

    def test_typical_price_needs_several_dates(self):
        self.assertIsNone(typical_price([make_offer(1000, day=d) for d in (1, 2, 3)]))
        offers = [make_offer(p, day=d) for p, d in ((50000, 1), (40000, 1), (60000, 2), (70000, 3), (90000, 4))]
        self.assertEqual(typical_price(offers), 65000)  # медиана 40 000, 60 000, 70 000, 90 000

    def test_smart_route_alerts_only_well_below_typical(self):
        route = self.storage.add_route(make_route(max_price=0, smart=True))
        offers = [make_offer(51000, day=1), make_offer(52000, day=2)]
        self.assertEqual([d.offer.price for d in find_deals(route, offers, self.storage, typical=60000)], [51000])
        self.assertEqual(find_deals(route, offers, self.storage, typical=None), [])

    def test_record_low_is_marked(self):
        route = self.storage.add_route(make_route(chat_id=7, max_price=60000))
        notifier = FakeNotifier()
        check_route(route, FakeSource([make_offer(58000, day=1)]), self.storage, notifier, "kzt", PLACES)
        self.assertNotIn("🔥", notifier.messages[-1][1])  # первая проверка — сравнивать не с чем

        check_route(route, FakeSource([make_offer(57000, day=2)]), self.storage, notifier, "kzt", PLACES)
        self.assertIn("• 🔥", notifier.messages[-1][1])
        self.assertIn("самая низкая цена с тех пор", notifier.messages[-1][1])

    def test_round_trip_key_includes_return_date(self):
        route = make_route(min_days=5, max_days=9)
        self.assertEqual(deal_key(route, round_trip(1, day=5, length=7)), "2026-12-05|2026-12-12")


class RelevantOffersTest(unittest.TestCase):
    def test_round_trip_length(self):
        route = make_route(min_days=5, max_days=9)
        offers = [round_trip(1, length=3), round_trip(2, length=7), make_offer(3)]
        self.assertEqual([o.price for o in relevant_offers(route, offers, PLACES)], [2])
        self.assertEqual([o.price for o in relevant_offers(make_route(), offers, PLACES)], [3])

    def test_unknown_country_counts_as_abroad(self):
        route = make_route(destination=None)
        offers = [make_offer(1, destination="NQZ"), make_offer(2, destination="ZZZ"), make_offer(3, destination="IST")]
        self.assertEqual([o.destination for o in relevant_offers(route, offers, PLACES)], ["ZZZ", "IST"])

    def test_specific_route_is_not_filtered(self):
        offers = [make_offer(1, destination="NQZ")]
        self.assertEqual(relevant_offers(make_route(destination="NQZ"), offers, PLACES), offers)


class FormattingTest(unittest.TestCase):
    def test_alert_escapes_html_and_shows_details(self):
        route = make_route(id=1)
        deal = Deal(make_offer(58000, transfers=1, airline="<PC>"), previous_price=59000)

        text = format_alert(route, [deal], "kzt", names)

        self.assertIn("✈️ <b>Алматы → Стамбул, декабрь 2026</b>", text)
        self.assertIn("<b>58 000 ₸</b> (было 59 000 ₸)", text)
        self.assertIn("1 пересадка", text)
        self.assertIn("&lt;PC&gt; 101", text)
        self.assertIn('<a href="https://www.aviasales.kz/search/ALA0512IST1">купить</a>', text)

    def test_anywhere_alert_names_the_city(self):
        route = make_route(destination=None, max_price=30000)
        text = format_alert(route, [Deal(make_offer(25000, destination="DXB"), None)], "kzt", names)
        self.assertIn("🌍 <b>Алматы → за границу, декабрь 2026</b>", text)
        self.assertIn("• Дубай — <b>25 000 ₸</b>, 05.12 05:45", text)

    def test_alert_limits_number_of_items(self):
        deals = [Deal(make_offer(1000 + i, day=i + 1), None) for i in range(13)]
        text = format_alert(make_route(), deals, "kzt", names, limit=10)
        self.assertEqual(text.count("• "), 10)
        self.assertIn("…и ещё вариантов: 3", text)

    def test_cheapest_lists_one_ticket_per_city(self):
        offers = [
            make_offer(30000, destination="DXB"),
            make_offer(20000, day=7, destination="TAS"),
            make_offer(25000, day=9, destination="DXB"),
        ]
        text = format_cheapest(make_route(destination=None), offers, "kzt", names)
        self.assertIn("Самые дешёвые билеты за границу из Алматы, декабрь 2026", text)
        self.assertLess(text.index("Ташкент"), text.index("Дубай"))
        self.assertIn("Дубай — <b>25 000 ₸</b>", text)
        self.assertNotIn("30 000", text)

    def test_round_trip_and_smart_texts(self):
        route = make_route(min_days=5, max_days=9, smart=True, max_price=0)
        deal = Deal(round_trip(51000, return_transfers=1), None, typical_price=62000)
        text = format_alert(route, [deal], "kzt", names)
        self.assertIn("Алматы ⇄ Стамбул, декабрь 2026, туда-обратно на 5–9 дней", text)
        self.assertIn("Выгодные цены — обычно на эти даты около 62 000 ₸", text)
        self.assertIn("05.12 05:45 → 12.12 (7 дней) — <b>51 000 ₸</b>, −18% к обычной цене", text)
        self.assertIn("туда прямой, обратно 1 пересадка", text)

    def test_cheapest_without_offers(self):
        self.assertIn("Не нашёл билетов", format_cheapest(make_route(destination=None), [], "kzt", names))


if __name__ == "__main__":
    unittest.main()
