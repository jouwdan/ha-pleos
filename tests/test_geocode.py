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


class GeocodingSafetyTests(unittest.IsolatedAsyncioTestCase):
    def response(self, session, payload, status=200):
        response = AsyncMock()
        response.status = status
        response.json.return_value = payload
        context = AsyncMock()
        context.__aenter__.return_value = response
        session.get.return_value = context
        return response

    def test_unsafe_and_malformed_urls_are_rejected(self):
        for url in ("ftp://geo.example", "https://", "https://user:password@geo.example",
                    "https://geo.example/reverse?key=secret", "https://geo.example/#secret",
                    "https://[bad", "https://geo.example:invalid", None):
            with self.subTest(url=url):
                self.assertEqual(ReverseGeocoder(MagicMock(), url).service_url, "")

    async def test_redirects_are_not_followed(self):
        session = MagicMock()
        self.response(session, {}, 302)
        geocoder = ReverseGeocoder(session, "https://geo.example", "fixture-key")
        self.assertIsNone(await geocoder._lookup(55.1, 10.1))
        self.assertEqual(geocoder.last_status, "http_302")
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_failures_never_log_request_details(self):
        from aiohttp import ClientError
        session = MagicMock()
        for error, status in ((ClientError("https://geo.example?key=fixture-key&lat=55.123"), "connection_error"),
                              (TimeoutError("private request"), "timeout")):
            with self.subTest(status=status):
                session.get.side_effect = error
                geocoder = ReverseGeocoder(session, "https://geo.example", "fixture-key")
                with self.assertLogs("custom_components.pleos_vehicle_data.geocode", level="DEBUG") as logs:
                    self.assertIsNone(await geocoder._lookup(55.123, 10.1))
                self.assertEqual(geocoder.last_status, status)
                for secret in ("fixture-key", "55.123", "private request", "https://"):
                    self.assertNotIn(secret, " ".join(logs.output))

    async def test_invalid_provider_responses_remain_unknown(self):
        session = MagicMock()
        geocoder = ReverseGeocoder(session, "https://geo.example")
        for payload, status in (([], "invalid_response"), ({}, "no_display_name"),
                                ({"display_name": 123}, "no_display_name"),
                                ({"display_name": "  "}, "no_display_name")):
            with self.subTest(payload=payload):
                self.response(session, payload)
                self.assertIsNone(await geocoder._lookup(55.1, 10.1))
                self.assertEqual(geocoder.last_status, status)
        response = self.response(session, {})
        response.json.side_effect = ValueError("private payload")
        self.assertIsNone(await geocoder._lookup(55.1, 10.1))
        self.assertEqual(geocoder.last_status, "invalid_json")

    async def test_expired_entries_are_removed_even_when_location_is_missing(self):
        from unittest.mock import patch
        from custom_components.pleos_vehicle_data.geocode import CACHE_TTL_SECONDS
        geocoder = ReverseGeocoder(MagicMock(), "https://geo.example")
        geocoder._lookup = AsyncMock(return_value="Example Street")
        car = {"location": {"latitude": 55.1, "longitude": 10.1}, "readings": {}}
        with patch("custom_components.pleos_vehicle_data.geocode.time.monotonic", return_value=0):
            await geocoder.apply(car)
        self.assertEqual(len(geocoder.cache), 1)
        car["location"] = None
        with patch("custom_components.pleos_vehicle_data.geocode.time.monotonic", return_value=CACHE_TTL_SECONDS):
            await geocoder.apply(car)
        self.assertEqual(geocoder.cache, {})
        geocoder._lookup.assert_awaited_once()

    async def test_cache_size_is_bounded(self):
        from unittest.mock import patch
        geocoder = ReverseGeocoder(MagicMock(), "https://geo.example")
        geocoder._lookup = AsyncMock(return_value="Example Street")
        with patch("custom_components.pleos_vehicle_data.geocode.MAX_CACHE_ENTRIES", 2):
            for lat in (1, 2, 3):
                await geocoder.apply({"location": {"latitude": lat, "longitude": 10}, "readings": {}})
        self.assertEqual(set(geocoder.cache), {(2, 10), (3, 10)})


from support import CAR, HomeAssistantTestCase, stamp
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.sensor import ReadingSensor


class GeocodingIntegrationTests(HomeAssistantTestCase):
    async def test_opt_in_and_valid_location_are_required(self):
        self.assertIs(self.coordinator.geocoder.session, self.client.session)
        self.coordinator.geocoder.service_url = "https://geo.example/reverse"
        self.coordinator.geocoder._lookup = AsyncMock(return_value="Example Street")
        car_id = vehicle_id("kia", CAR["vin"])
        self.client.vehicles = AsyncMock(return_value=[CAR])
        payload = {"timestamp": stamp(), "location": {"type": 0, "latitude": 55.1, "longitude": 10.1}}
        self.client.get = AsyncMock(side_effect=lambda path: payload if "/locations" in path else None)
        for location, reverse in ((True, False), (False, True), (False, False)):
            self.coordinator.options.update(location=location, reverse_geocode=reverse)
            car = (await self.coordinator._async_update_data())["cars"][car_id]
            self.assertNotIn("location_address", car["readings"])
        self.coordinator.geocoder._lookup.assert_not_awaited()
        self.coordinator.options.update(location=True, reverse_geocode=True)
        for invalid in ({"type": 1, "latitude": 55.1, "longitude": 10.1},
                        {"type": 0, "latitude": 91, "longitude": 10.1}):
            payload["location"] = invalid
            await self.coordinator._async_update_data()
        payload["location"] = {"type": 0, "latitude": 55.1, "longitude": 10.1}
        payload["timestamp"] = "invalid"
        await self.coordinator._async_update_data()
        self.coordinator.geocoder._lookup.assert_not_awaited()
        payload["timestamp"] = stamp()
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        spec = self.coordinator.data["cars"][car_id]["readings"]["location_address"]
        sensor = ReadingSensor(self.coordinator, car_id, "location_address", spec)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, "Example Street")
        self.assertEqual(sensor.reported_at, payload["timestamp"])
        self.coordinator.geocoder._lookup.assert_awaited_once_with(55.1, 10.1)

    async def test_long_address_is_displayable_and_redacted_in_both_diagnostics(self):
        import json
        import logging
        from datetime import timedelta
        from types import SimpleNamespace
        from homeassistant.helpers import entity_registry
        from homeassistant.helpers.entity_platform import EntityPlatform
        from custom_components.pleos_vehicle_data.const import DOMAIN
        from custom_components.pleos_vehicle_data.diagnostics import (
            async_get_config_entry_diagnostics, async_get_device_diagnostics,
        )
        address = "123 Synthetic Home Street, " * 15
        self.hass.config_entries.async_update_entry(self.entry, options={
            "geocoder_url": "https://fixture-user:fixture-password@geo.example/?key=fixture-key",
            "geocoder_key": "fixture-key",
        })
        await self.populate()
        self.entry.runtime_data = self.coordinator
        car_id = vehicle_id("kia", CAR["vin"])
        car = self.coordinator.data["cars"][car_id]
        car["location"] = {"latitude": 55.1, "longitude": 10.1}
        car["sources"]["locations"] = stamp()
        self.coordinator.geocoder.service_url = "https://geo.example/reverse"
        self.coordinator.geocoder._lookup = AsyncMock(return_value=address)
        await self.coordinator.geocoder.apply(car)
        spec = car["readings"]["location_address"]
        sensor = ReadingSensor(self.coordinator, car_id, "location_address", spec)
        await entity_registry.async_load(self.hass, load_empty=True)
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        await platform.async_add_entities([sensor])
        self.addAsyncCleanup(platform.async_reset)
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.state, address[:255])
        self.assertEqual(state.attributes["full_address"], address)
        for diagnostic in (await async_get_config_entry_diagnostics(self.hass, self.entry),
                           await async_get_device_diagnostics(self.hass, self.entry,
                               SimpleNamespace(identifiers={(DOMAIN, car_id)}))):
            text = json.dumps(diagnostic)
            for secret in ("Synthetic Home Street", "fixture-password", "fixture-key", "55.1", "10.1"):
                self.assertNotIn(secret, text)
        self.assertEqual(sensor.native_value, address[:255])  # Diagnostics must not mutate the reading.

    async def test_provider_failure_does_not_break_vehicle_poll(self):
        from aiohttp import ClientError
        self.coordinator.options["reverse_geocode"] = True
        self.coordinator.geocoder.service_url = "https://geo.example/reverse"
        self.client.session.get.side_effect = ClientError("private request details")
        await self.populate({"timestamp": stamp(), "charge": {"stateOfCharge": 50},
                             "location": {"type": 0, "latitude": 55.1, "longitude": 10.1}})
        car_id = vehicle_id("kia", CAR["vin"])
        car = self.coordinator.data["cars"][car_id]
        reading = car["readings"]["location_address"]
        self.assertEqual(reading["attributes"]["geocoding_status"], "connection_error")
        sensor = ReadingSensor(self.coordinator, car_id, "location_address", reading)
        self.assertTrue(sensor.available)
        self.assertIsNone(sensor.native_value)
        self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 50)
