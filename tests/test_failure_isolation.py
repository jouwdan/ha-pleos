"""One optional endpoint must not discard successful vehicle readings."""

import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from pleos_client import ApiError, AuthError, PleosClient
from custom_components.pleos_vehicle_data.const import DOMAIN, ENDPOINTS
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor


class FailureIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = config_entries.ConfigEntries(self.hass, {})
        self.entry = config_entries.ConfigEntry(
            data={"brand": "kia", "client_id": "fixture-id", "client_secret": "fixture-secret"},
            domain=DOMAIN, title="Kia", unique_id="kia", version=1,
            minor_version=1, source="user", options={}, discovery_keys={}, subentries_data=[],
            state=config_entries.ConfigEntryState.SETUP_IN_PROGRESS,
        )
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.client = PleosClient(MagicMock(), "kia", "fixture-id", "fixture-secret")
        self.client.vehicles = AsyncMock(return_value=[{"vin": "SYNTHETIC-CAR-001", "type": "EV"}])
        self.sample = {"timestamp": datetime.now(timezone.utc).isoformat(), "charge": {"stateOfCharge": 50}}
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop()
        self.temp.cleanup()

    async def test_optional_timeout_keeps_successful_data_and_continues(self):
        self.client.get = AsyncMock(side_effect=[self.sample, ApiError(0, "connection_failed"), *([None] * 8)])
        result = await self.coordinator._async_update_data()
        car = next(iter(result["cars"].values()))
        self.assertEqual(result["status"], "connected")
        self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 50)
        self.assertEqual(car["readings"]["powertrains_api"]["value"], "error")
        self.assertEqual(car["readings"]["powertrains_api"]["attributes"]["http_status"], 0)
        self.assertEqual(self.client.get.await_count, len(ENDPOINTS))

    async def test_rate_limit_keeps_partial_results_and_stops_all_vehicle_requests(self):
        self.client.vehicles.return_value.append({"vin": "SYNTHETIC-CAR-002", "type": "EV"})
        self.client.get = AsyncMock(side_effect=[self.sample, ApiError(429, "429")])
        self.coordinator.data = await self.coordinator._async_update_data()
        self.assertEqual(ConnectionSensor(self.coordinator).native_value, "rate_limited")
        first, second = self.coordinator.data["cars"].values()
        self.assertEqual(first["readings"]["batteries_charge_stateofcharge"]["value"], 50)
        self.assertEqual(first["readings"]["powertrains_api"]["attributes"]["http_status"], 429)
        for key, reading in second["readings"].items():
            if key.endswith("_api"):
                self.assertEqual(reading["value"], "error")
                self.assertEqual(reading["attributes"]["http_status"], 429)
                self.assertNotIn("fetched_at", reading["attributes"])
        self.assertEqual(self.client.get.await_count, 2)
        self.client.get = AsyncMock(return_value=None)
        recovered = await self.coordinator._async_update_data()
        self.assertEqual(recovered["status"], "connected")
        self.assertEqual(self.client.get.await_count, len(ENDPOINTS) * 2)

    async def test_rate_limit_respects_location_opt_out(self):
        self.coordinator.options["location"] = False
        self.client.get = AsyncMock(side_effect=ApiError(429, "429"))
        result = await self.coordinator._async_update_data()
        car = next(iter(result["cars"].values()))
        self.assertEqual(car["readings"]["locations_api"]["value"], "disabled")
        self.assertEqual(self.client.get.await_count, 1)

    async def test_permissions_and_unregistered_vehicle_are_errors_not_no_data(self):
        for status, code in ((403, "403"), (400, "5006"), (410, "4101"), (404, "4045")):
            with self.subTest(code=code):
                self.coordinator.trips.cache.clear()
                self.client.get = AsyncMock(side_effect=[self.sample, ApiError(status, code), *([None] * 8)])
                result = await self.coordinator._async_update_data()
                car = next(iter(result["cars"].values()))
                self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 50)
                self.assertEqual(car["readings"]["powertrains_api"]["value"], "no_data" if code == "4045" else "error")
                self.assertEqual(self.client.get.await_count, len(ENDPOINTS))

    async def test_account_transport_and_auth_failures_remain_global(self):
        self.client.get = AsyncMock()
        for failure, expected in ((ApiError(0, "connection_failed"), UpdateFailed),
                                  (ApiError(429, "429"), UpdateFailed),
                                  (AuthError(), ConfigEntryAuthFailed)):
            with self.subTest(error=type(failure).__name__, status=getattr(failure, "status", None)):
                self.client.vehicles.side_effect = failure
                with self.assertRaises(expected):
                    await self.coordinator._async_update_data()
        self.client.get.assert_not_awaited()
