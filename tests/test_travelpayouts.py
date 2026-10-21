import unittest
import urllib.error
from io import BytesIO

from ticket_alert.travelpayouts import (
    SourceError,
    TravelpayoutsClient,
    build_params,
    parse_offers,
    partner_link,
)

from tests.helpers import FakeResponse, FakeUrlopen, make_route

SAMPLE = {
    "success": True,
    "currency": "kzt",
    "data": [
        {
            "origin": "ALA",
            "destination": "IST",
            "origin_airport": "ALA",
            "destination_airport": "SAW",
            "price": 58802,
            "airline": "PC",
            "flight_number": "101",
            "departure_at": "2026-12-04T05:45:00+05:00",
            "transfers": 0,
            "return_transfers": 0,
            "duration": 420,
            "link": "/search/ALA0412IST1?t=PC17963631001796378400000375ALASAW",
        },
        {"price": "not a number", "destination": "IST", "departure_at": "2026-12-06T10:15:00+05:00"},
        {"price": 1000, "departure_at": "2026-12-06T10:15:00+05:00"},  # без города назначения
    ],
}


class TravelpayoutsTest(unittest.TestCase):
    def test_params_for_one_city(self):
        params = build_params(make_route(direct_only=True), "kzt", "kz")
        self.assertEqual(params["destination"], "IST")
        self.assertEqual(params["one_way"], "true")
        self.assertEqual(params["direct"], "true")
        self.assertEqual(params["unique"], "false")
        self.assertEqual(params["market"], "kz")

    def test_params_for_anywhere(self):
        params = build_params(make_route(destination=None), "kzt", "kz")
        self.assertNotIn("destination", params)
        self.assertEqual(params["unique"], "true")
        self.assertEqual(params["origin"], "ALA")

    def test_parse_skips_broken_rows_and_builds_link(self):
        offers = parse_offers(SAMPLE, "https://www.aviasales.kz")
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual((offer.origin, offer.destination, offer.price), ("ALA", "IST", 58802))
        self.assertEqual(offer.link, "https://www.aviasales.kz/search/ALA0412IST1?t=PC17963631001796378400000375ALASAW")

    def test_partner_link(self):
        site = "https://www.aviasales.kz"
        self.assertEqual(partner_link(site, "/search/ALA0512IST1"), site + "/search/ALA0512IST1")
        self.assertEqual(
            partner_link(site, "/search/ALA0512IST1", "123456"), site + "/search/ALA0512IST1?marker=123456"
        )
        self.assertEqual(
            partner_link(site, "/search/ALA0512IST1?t=PC1", "123456.bot"),
            site + "/search/ALA0512IST1?t=PC1&marker=123456.bot",
        )

    def test_parse_error_payload(self):
        with self.assertRaises(SourceError):
            parse_offers({"success": False, "error": "bad params"})

    def test_search_sends_token_in_header_handles_gzip_and_adds_marker(self):
        urlopen = FakeUrlopen(FakeResponse(SAMPLE, gzipped=True))
        client = TravelpayoutsClient("secret", marker="123456", urlopen=urlopen)

        offers = client.search(make_route())

        request = urlopen.requests[0]
        self.assertEqual(request.get_header("X-access-token"), "secret")
        self.assertNotIn("secret", request.full_url)
        self.assertIn("origin=ALA", request.full_url)
        self.assertTrue(offers[0].link.startswith("https://www.aviasales.kz/search/"))
        self.assertTrue(offers[0].link.endswith("&marker=123456"))

    def test_search_results_are_cached_for_a_while(self):
        now = [0.0]
        urlopen = FakeUrlopen(FakeResponse(SAMPLE), FakeResponse(SAMPLE))
        client = TravelpayoutsClient("t", urlopen=urlopen, cache_seconds=600, clock=lambda: now[0])

        client.search(make_route())
        now[0] = 300
        client.search(make_route())
        self.assertEqual(len(urlopen.requests), 1)
        now[0] = 700
        client.search(make_route())
        self.assertEqual(len(urlopen.requests), 2)

    def test_search_unauthorized(self):
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO(b""))
        client = TravelpayoutsClient("bad", urlopen=FakeUrlopen(error))
        with self.assertRaisesRegex(SourceError, "токен"):
            client.search(make_route())


if __name__ == "__main__":
    unittest.main()
