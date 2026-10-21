import os
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path

from ticket_alert.dates import describe_departure, is_expired, parse_departure, upcoming_months
from ticket_alert.places import Places

from tests.helpers import FakeResponse, FakeUrlopen

CITIES = [
    {"code": "IST", "name": "Стамбул", "country_code": "TR", "has_flightable_airport": True,
     "name_translations": {"en": "Istanbul"}},
    {"code": "NQZ", "name": "Нур-Султан", "country_code": "KZ", "has_flightable_airport": True,
     "name_translations": {"en": "Astana"}},
    {"code": "SPX", "name": "Санкт-Петербург", "country_code": "US", "has_flightable_airport": True},
    {"code": "LED", "name": "Санкт-Петербург", "country_code": "RU", "has_flightable_airport": True},
    {"code": "XXX", "name": "Стамбульск", "country_code": "RU", "has_flightable_airport": False},
]
COUNTRIES = [{"code": "TR", "name": "Турция"}, {"code": "KZ", "name": "Казахстан"}, {"code": "RU", "name": "Россия"}]


class PlacesTest(unittest.TestCase):
    def setUp(self):
        self.places = Places.from_json(CITIES, COUNTRIES)

    def test_names_and_labels(self):
        self.assertEqual(self.places.name("IST"), "Стамбул")
        self.assertEqual(self.places.get("IST").label, "Стамбул, Турция")
        self.assertEqual(self.places.name("ZZZ"), "ZZZ")
        self.assertEqual(self.places.name("ALA"), "Алматы")  # из встроенного списка

    def test_resolve_by_name_code_and_alias(self):
        self.assertEqual(self.places.resolve("стамбул").code, "IST")
        self.assertEqual(self.places.resolve("Istanbul").code, "IST")
        self.assertEqual(self.places.resolve("ist").code, "IST")
        self.assertEqual(self.places.resolve("Астана").code, "NQZ")  # встроенное название как синоним
        self.assertIsNone(self.places.resolve("Санкт-Петербург"))  # два города — нужно уточнить

    def test_search_prefix_and_skips_cities_without_airport(self):
        codes = [p.code for p in self.places.search("Стамб")]
        self.assertEqual(codes, ["IST"])
        self.assertEqual({p.code for p in self.places.search("санкт-петербург")}, {"LED", "SPX"})
        self.assertEqual(self.places.search("Атлантида"), [])

    def test_load_downloads_once_then_uses_cache(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name)
        urlopen = FakeUrlopen(FakeResponse(CITIES), FakeResponse(COUNTRIES))

        places = Places.load(cache, urlopen=urlopen)
        self.assertEqual(places.get("IST").label, "Стамбул, Турция")
        self.assertEqual(len(urlopen.requests), 2)

        offline = FakeUrlopen(urllib.error.URLError("no network"))
        self.assertEqual(Places.load(cache, urlopen=offline).name("IST"), "Стамбул")
        self.assertEqual(len(offline.requests), 0)

        # Устаревший кэш пробуем обновить, а без сети работаем со старым.
        old = os.path.getmtime(cache / "cities.json") - 40 * 24 * 3600
        os.utime(cache / "cities.json", (old, old))
        self.assertEqual(Places.load(cache, urlopen=offline).name("IST"), "Стамбул")
        self.assertEqual(len(offline.requests), 1)

    def test_load_without_network_and_cache_uses_builtin_list(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        places = Places.load(Path(tmp.name), urlopen=FakeUrlopen(urllib.error.URLError("offline")))
        self.assertEqual(places.resolve("алматы").code, "ALA")
        self.assertEqual(places.resolve("Стамбул").code, "IST")


class DatesTest(unittest.TestCase):
    today = date(2026, 10, 22)

    def test_upcoming_months_cross_the_year(self):
        self.assertEqual(
            upcoming_months(self.today, 4), ["2026-10", "2026-11", "2026-12", "2027-01"]
        )

    def test_describe(self):
        self.assertEqual(describe_departure("2026-12"), "декабрь 2026")
        self.assertEqual(describe_departure("2026-12-05"), "05.12.2026")

    def test_parse_departure(self):
        cases = {
            "05.12": "2026-12-05",
            "5/12/2026": "2026-12-05",
            "05.01": "2027-01-05",  # январь уже прошёл — значит, следующий год
            "2026-12-31": "2026-12-31",
            "декабрь": "2026-12",
            "Январь": "2027-01",
            "март 2027": "2027-03",
            "2026-11": "2026-11",
            "01.10.2026": None,  # в прошлом
            "31.02": None,
            "завтра": None,
            "2026-13": None,
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(parse_departure(text, self.today), expected)

    def test_is_expired(self):
        self.assertFalse(is_expired("2026-10", self.today))
        self.assertTrue(is_expired("2026-09", self.today))
        self.assertTrue(is_expired("2026-10-21", self.today))
        self.assertFalse(is_expired("2026-10-22", self.today))


if __name__ == "__main__":
    unittest.main()
