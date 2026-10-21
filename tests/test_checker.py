import unittest
from datetime import date, datetime

from ticket_alert.checker import check_route, find_deals, format_message, run_once, Deal
from ticket_alert.notifiers import NotifyError
from ticket_alert.storage import Storage
from ticket_alert.travelpayouts import SourceError

from tests.helpers import FakeNotifier, FakeSource, make_offer, make_route


class FindDealsTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.route = make_route(max_price=10000)

    def test_keeps_only_offers_within_budget_sorted_by_price(self):
        offers = [make_offer(9000, day=1), make_offer(12000, day=2), make_offer(10000, day=3)]
        deals = find_deals(self.route, offers, self.storage)
        self.assertEqual([d.offer.price for d in deals], [9000, 10000])

    def test_picks_cheapest_offer_per_date(self):
        offers = [make_offer(9500, day=1), make_offer(8000, day=1, flight_number="777")]
        deals = find_deals(self.route, offers, self.storage)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].offer.flight_number, "777")

    def test_direct_only_skips_flights_with_transfers(self):
        route = make_route(direct_only=True)
        offers = [
            make_offer(5000, day=1, transfers=1),
            make_offer(6000, day=2),
            make_offer(4000, day=3, return_transfers=1),
        ]
        deals = find_deals(route, offers, self.storage)
        self.assertEqual([d.offer.price for d in deals], [6000])

    def test_skips_already_notified_price_and_reports_drops(self):
        self.storage.remember(self.route.key, [make_offer(9000, day=1), make_offer(9000, day=2)])
        offers = [make_offer(9000, day=1), make_offer(8500, day=2)]
        deals = find_deals(self.route, offers, self.storage)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].offer.price, 8500)
        self.assertEqual(deals[0].previous_price, 9000)


class CheckRouteTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.route = make_route()

    def test_sends_once_and_remembers(self):
        source = FakeSource([make_offer(7000)])
        notifier = FakeNotifier()

        check_route(self.route, source, self.storage, notifier, "rub")
        check_route(self.route, source, self.storage, notifier, "rub")

        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("7 000 ₽", notifier.messages[0])

    def test_dry_run_does_not_remember(self):
        source = FakeSource([make_offer(7000)])
        notifier = FakeNotifier()

        check_route(self.route, source, self.storage, notifier, "rub", dry_run=True)
        check_route(self.route, source, self.storage, notifier, "rub", dry_run=True)

        self.assertEqual(len(notifier.messages), 2)

    def test_failed_send_is_retried_next_time(self):
        source = FakeSource([make_offer(7000)])
        with self.assertRaises(NotifyError):
            check_route(self.route, source, self.storage, FakeNotifier(NotifyError("down")), "rub")

        notifier = FakeNotifier()
        check_route(self.route, source, self.storage, notifier, "rub")
        self.assertEqual(len(notifier.messages), 1)


class RunOnceTest(unittest.TestCase):
    def test_continues_after_route_error(self):
        storage = Storage(":memory:")
        notifier = FakeNotifier()

        class Source:
            def search(self, route):
                if route.destination == "IST":
                    raise SourceError("boom")
                return [make_offer(5000)]

        routes = [make_route(), make_route(name="Москва → Анталья", destination="AYT")]
        summary = run_once(routes, Source(), storage, notifier, "rub", today=date(2026, 11, 1))

        self.assertEqual(summary.failed_routes, 1)
        self.assertEqual(summary.deals, 1)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("Анталья", notifier.messages[0])

    def test_prunes_past_departures(self):
        storage = Storage(":memory:")
        route = make_route()
        storage.remember(route.key, [make_offer(7000, day=1), make_offer(7000, day=20)])

        run_once([route], FakeSource(), storage, FakeNotifier(), "rub", today=date(2026, 12, 10))

        self.assertIsNone(storage.last_price(route.key, make_offer(7000, day=1).key))
        self.assertEqual(storage.last_price(route.key, make_offer(7000, day=20).key), 7000)


class FormatMessageTest(unittest.TestCase):
    def test_escapes_html_and_shows_details(self):
        route = make_route(name="Москва <→> Стамбул & назад")
        deal = Deal(make_offer(8000, transfers=1), previous_price=9000)

        text = format_message(route, [deal], "rub")

        self.assertIn("Москва &lt;→&gt; Стамбул &amp; назад", text)
        self.assertIn("<b>8 000 ₽</b> (было 9 000 ₽)", text)
        self.assertIn("1 пересадка", text)
        self.assertIn("05.12 10:15", text)
        self.assertIn('<a href="https://www.aviasales.ru/search/MOW0512IST1">купить</a>', text)

    def test_round_trip_shows_both_legs(self):
        offer = make_offer(
            8000,
            return_at=datetime.fromisoformat("2026-12-12T18:00:00+03:00"),
            return_transfers=2,
        )
        text = format_message(make_route(return_date="2026-12"), [Deal(offer, None)], "rub")
        self.assertIn("05.12 10:15 → 12.12", text)
        self.assertIn("туда прямой, обратно 2 пересадки", text)

    def test_limits_number_of_items(self):
        deals = [Deal(make_offer(1000 + i, day=i + 1), None) for i in range(13)]
        text = format_message(make_route(), deals, "rub", limit=10)
        self.assertEqual(text.count("• "), 10)
        self.assertIn("…и ещё вариантов: 3", text)


if __name__ == "__main__":
    unittest.main()
