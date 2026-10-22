"""Reverse geocoding tests."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.pleos_vehicle_data.geocode import ReverseGeocoder


class ReverseGeocoderTest(unittest.IsolatedAsyncioTestCase):
    def test_cache_key_rounds_coordinates(self):
        point = {"latitude": 55.123456, "longitude": 10.654321}
        self.assertEqual(ReverseGeocoder.cache_key(point), (55.1235, 10.6543))

    async def test_apply_adds_address_and_reuses_cache(self):
        geocoder = ReverseGeocoder(MagicMock(), "https://geocoder.example")
        geocoder._lookup = AsyncMock(return_value="Example Street 1, Exampletown")
        car = {"location": {"latitude": 55.123456, "longitude": 10.654321}, "readings": {}}
        await geocoder.apply(car)
        await geocoder.apply(car)
        self.assertEqual(car["readings"]["location_address"]["value"], "Example Street 1, Exampletown")
        geocoder._lookup.assert_awaited_once_with(55.1235, 10.6543)

    async def test_apply_ignores_missing_location(self):
        geocoder = ReverseGeocoder(MagicMock(), "https://geocoder.example")
        geocoder._lookup = AsyncMock()
        await geocoder.apply({"location": None, "readings": {}})
        geocoder._lookup.assert_not_awaited()

    def test_service_url_accepts_base_or_reverse_endpoint(self):
        self.assertEqual(ReverseGeocoder(MagicMock(), "https://geo.example").service_url,
                         "https://geo.example/reverse")
        self.assertEqual(ReverseGeocoder(MagicMock(), "https://geo.example/reverse").service_url,
                         "https://geo.example/reverse")

    async def test_empty_service_url_sends_nothing(self):
        session = MagicMock()
        geocoder = ReverseGeocoder(session)
        await geocoder.apply({"location": {"latitude": 55.1, "longitude": 10.1}, "readings": {}})
        session.get.assert_not_called()

    async def test_api_key_is_sent_as_query_parameter(self):
        geocoder = ReverseGeocoder(MagicMock(), "https://geo.example", "secret-key")
        response = AsyncMock()
        response.status = 200
        response.json.return_value = {"display_name": "Example Street 1"}
        context = AsyncMock()
        context.__aenter__.return_value = response
        context.__aexit__.return_value = False
        geocoder.session.get.return_value = context
        self.assertEqual(await geocoder._lookup(55.1, 10.1), "Example Street 1")
        params = geocoder.session.get.call_args.kwargs["params"]
        self.assertEqual(params["key"], "secret-key")
        self.assertEqual(params["format"], "json")

class ReverseGeocoderCachePolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_attribution_is_exposed(self):
        geocoder = ReverseGeocoder(MagicMock(), "https://geo.example")
        geocoder._lookup = AsyncMock(return_value="Example Street 1")
        geocoder.last_attribution = "Example geocoder attribution"
        car = {"location": {"latitude": 55.1, "longitude": 10.1}, "readings": {}}
        await geocoder.apply(car)
        self.assertEqual(car["readings"]["location_address"]["attributes"]["attribution"],
                         "Example geocoder attribution")

    async def test_failed_lookup_is_not_cached(self):
        geocoder = ReverseGeocoder(MagicMock(), "https://geo.example")
        geocoder._lookup = AsyncMock(side_effect=[None, "Example Street 1"])
        car = {"location": {"latitude": 55.1, "longitude": 10.1}, "readings": {}}
        await geocoder.apply(car)
        await geocoder.apply(car)
        self.assertEqual(geocoder._lookup.await_count, 2)
