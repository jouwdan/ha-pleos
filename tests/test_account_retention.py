"""Keep the last vehicle state, with honest freshness, during account outages."""

from copy import deepcopy
import time
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import ConfigEntryNotReady

from pleos_client import ApiError, AuthError
from custom_components.pleos_vehicle_data.binary_sensor import ReadingBinarySensor
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.device_tracker import VehicleTracker
from custom_components.pleos_vehicle_data.diagnostics import _coordinator_state
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor, LastSuccessfulPollSensor, ReadingSensor

from support import CAR, HomeAssistantTestCase, stamp

CAR_ID = vehicle_id("kia", CAR["vin"])
BATTERY = "batteries_charge_stateofcharge"
RANGE = "powertrains_distancetoempties_ev"


class AccountRetentionTests(HomeAssistantTestCase):
    async def seed(self):
        reported = stamp(5)
        self.payloads = {
            "batteries": {"charge": {"stateOfCharge": 60, "charging": True}},
            "powertrains": {"distanceToEmpties": [{"type": "EV", "value": 100, "unit": "mile"}]},
            "driving": {"odometer": {"value": 1500, "unit": "km"}, "ignition": {"status": "on"}},
            "status": {"doors": [{"left": "locked"}]},
            "locations": {"location": {"latitude": 0, "longitude": 0, "type": 0}},
        }
        self.payloads = {key: value | {"timestamp": reported} for key, value in self.payloads.items()}
        self.payloads["speed"] = {"trips": [{"triggerStartTime": stamp(20), "tripLength": 60000,
                                            "location": {"speed": [10, 20], "speedUnit": ["kph", "kph"]}}]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: self.payloads.get(path.split("?")[0].rsplit("/", 1)[-1]))
        await self.coordinator.async_refresh()
        car = self.coordinator.data["cars"][CAR_ID]
        self.entities = {
            key: (ReadingBinarySensor if spec["domain"] == "binary_sensor" else ReadingSensor)(
                self.coordinator, CAR_ID, key, spec)
            for key, spec in car["readings"].items()
        }
        self.entities["location"] = VehicleTracker(
            self.coordinator, CAR_ID, "location", {"name": "Location", "source": "locations"})
        return reported

    async def fail_poll(self, error):
        self.client.vehicles.side_effect = error
        self.client.retry_at = time.monotonic() + 300 if getattr(error, "status", None) == 429 else 0
        await self.coordinator.async_refresh()

    async def test_outages_hide_states_without_discarding_cached_data(self):
        reported = await self.seed()
        previous = self.coordinator.data
        original = deepcopy(previous)
        poll = LastSuccessfulPollSensor(self.coordinator).native_value
        archive = deepcopy(self.coordinator.archive.status)
        statistics = deepcopy(self.coordinator.statistics.ledger)
        trips = deepcopy(self.coordinator.trip_queue)
        updates = Mock()
        self.addCleanup(self.coordinator.async_add_listener(updates))
        self.client.get.reset_mock()
        for status in (0, 503, 429):
            with self.subTest(status=status):
                await self.fail_poll(ApiError(status, "fixture_failure"))
                self.assertFalse(self.coordinator.last_update_success)
                self.assertTrue(_coordinator_state(self.coordinator)["using_last_data"])
                self.assertIs(self.coordinator.data, previous)
                for key, entity in self.entities.items():
                    self.assertFalse(entity.available, key)
                    self.assertTrue(entity.extra_state_attributes["last_known"], key)
                    self.assertTrue(entity.extra_state_attributes["data_stale"], key)
                battery = self.entities[BATTERY]
                self.assertEqual(battery.native_value, 60)
                self.assertEqual(battery.extra_state_attributes["reported_at"], reported)
                self.assertGreaterEqual(battery.extra_state_attributes["data_age_minutes"], 5)
                self.assertEqual(self.entities[RANGE].native_unit_of_measurement, "mi")
                self.assertTrue(self.entities["batteries_charge_charging"].is_on)
                self.assertEqual((self.entities["location"].latitude, self.entities["location"].longitude), (0, 0))
                self.assertEqual(ConnectionSensor(self.coordinator).native_value,
                                 "rate_limited" if status == 429 else "api_unavailable")
                self.assertEqual(LastSuccessfulPollSensor(self.coordinator).native_value, poll)
        self.assertEqual(updates.call_count, 1)
        self.client.get.assert_not_awaited()
        self.assertEqual(previous, original)
        self.assertEqual(self.coordinator.archive.status, archive)
        self.assertEqual(self.coordinator.statistics.ledger, statistics)
        self.assertEqual(self.coordinator.trip_queue, trips)
        self.client.vehicles.side_effect = None
        self.client.retry_at = 0
        fresh = stamp()
        self.payloads["batteries"] = {"timestamp": fresh, "charge": {"stateOfCharge": 70, "charging": False}}
        self.payloads["powertrains"] = {"timestamp": fresh, "distanceToEmpties": [{"type": "EV", "value": 150, "unit": "km"}]}
        await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.using_last_data)
        self.assertTrue(battery.available)
        self.assertEqual(battery.native_value, 70)
        self.assertEqual(battery.extra_state_attributes["reported_at"], fresh)
        self.assertFalse(battery.extra_state_attributes.get("last_known", False))
        self.assertFalse(battery.extra_state_attributes["data_stale"])
        self.assertFalse(self.entities["batteries_charge_charging"].is_on)
        self.assertEqual((self.entities[RANGE].native_value, self.entities[RANGE].native_unit_of_measurement), (150, "km"))
        self.assertFalse(self.entities[RANGE].extra_state_attributes["last_known"])
        self.assertEqual(ConnectionSensor(self.coordinator).native_value, "connected")
        self.assertGreater(LastSuccessfulPollSensor(self.coordinator).native_value, poll)

    async def test_permission_auth_invalid_response_and_unexpected_failures_end_retention(self):
        failures = [ApiError(status, "fixture_failure") for status in (400, 401, 403, 404, 200)]
        failures += [AuthError(), RuntimeError("fixture_failure")]
        with patch("homeassistant.config_entries.ConfigEntry.async_start_reauth_if_available"):
            for failure in failures:
                with self.subTest(failure=repr(failure)):
                    await self.seed()
                    battery = self.entities[BATTERY]
                    available = []
                    unsubscribe = self.coordinator.async_add_listener(lambda: available.append(battery.available))
                    try:
                        await self.fail_poll(ApiError(0, "connection_failed"))
                        await self.fail_poll(failure)
                        self.assertFalse(self.coordinator.using_last_data)
                        self.assertFalse(battery.available)
                        # A later transport failure must not revive readings after access was rejected.
                        await self.fail_poll(ApiError(0, "connection_failed"))
                        self.assertFalse(battery.available)
                        self.assertEqual(available, [False])
                    finally:
                        unsubscribe()

    async def test_startup_or_reload_failure_does_not_invent_cached_state(self):
        await self.seed()
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.client.vehicles.side_effect = ApiError(0, "connection_failed")
        with self.assertRaises(ConfigEntryNotReady):
            await self.coordinator.async_config_entry_first_refresh()
        self.assertIsNone(self.coordinator.data)
        self.assertFalse(self.coordinator.using_last_data)
        self.assertIsNone(self.coordinator.last_successful_poll)

    async def test_successful_missing_or_invalid_reading_does_not_retain_old_value(self):
        for payload in (None, {"timestamp": stamp(), "charge": {"stateOfCharge": 101}}):
            with self.subTest(payload=payload):
                await self.seed()
                await self.fail_poll(ApiError(0, "connection_failed"))
                self.client.vehicles.side_effect = None
                self.payloads["batteries"] = payload
                await self.coordinator.async_refresh()
                self.assertFalse(self.coordinator.using_last_data)
                self.assertTrue(self.entities[BATTERY].available)
                self.assertIsNone(self.entities[BATTERY].native_value)

    async def test_removed_vehicle_is_not_revived_by_subsequent_failure(self):
        await self.seed()
        await self.fail_poll(ApiError(0, "connection_failed"))
        self.client.vehicles.side_effect = None
        self.client.vehicles.return_value = []
        await self.coordinator.async_refresh()
        for entity in self.entities.values():
            self.assertFalse(entity.available)
        self.assertEqual(await self.coordinator.ranges.store.async_load(), {})
        await self.fail_poll(ApiError(0, "connection_failed"))
        for entity in self.entities.values():
            self.assertFalse(entity.available)
