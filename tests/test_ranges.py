"""Range retention across missing responses, reloads and native unit conversion."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.pleos_vehicle_data import async_remove_entry
from pleos_client import ApiError
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.entity import discover
from custom_components.pleos_vehicle_data.ranges import range_store
from custom_components.pleos_vehicle_data.sensor import ReadingSensor

from support import CAR, HomeAssistantTestCase, stamp

KEY = "powertrains_distancetoempties_ev"
CAR_ID = vehicle_id("kia", CAR["vin"])


class RangeTests(HomeAssistantTestCase):
    async def poll(self, value="100", unit="mile", reported_at=None):
        payload = {"timestamp": reported_at or stamp(), "distanceToEmpties": [
            {"type": "EV", "value": value, "unit": unit}]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if path.endswith("/powertrains") else None)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        return payload

    def sensor(self, key=KEY, car_id=CAR_ID):
        spec = self.coordinator.data["cars"][car_id]["readings"][key]
        return ReadingSensor(self.coordinator, car_id, key, spec)

    async def test_invalid_range_keeps_original_unit_timestamp_and_marks_stale(self):
        reported = stamp(120)
        payload = await self.poll(reported_at=reported)
        sensor = self.sensor()
        for value in ("-", None, True, "NaN", float("inf"), 0, -1, 4096):
            with self.subTest(value=value):
                await self.poll(value=value, unit="km")
                self.assertTrue(sensor.available)
                self.assertEqual((sensor.native_value, sensor.native_unit_of_measurement), (None, "mi"))
                self.assertEqual(sensor.car["readings"][KEY]["value"], 100)
                self.assertEqual(sensor.extra_state_attributes["reported_at"], reported)
                self.assertGreaterEqual(sensor.extra_state_attributes["data_age_minutes"], 120)
                self.assertTrue(sensor.extra_state_attributes["data_stale"])
                self.assertTrue(sensor.extra_state_attributes["last_known"])
        self.assertEqual(payload["distanceToEmpties"], [{"type": "EV", "value": "100", "unit": "mile"}])
        await self.poll("160", "km")
        self.assertEqual((sensor.native_value, sensor.native_unit_of_measurement), (160, "km"))
        self.assertFalse(sensor.extra_state_attributes["last_known"])
        self.assertFalse(sensor.extra_state_attributes["data_stale"])

    async def test_recent_retained_value_is_stale_and_endpoint_error_remains_visible(self):
        await self.poll()
        sensor = self.sensor()
        self.client.get = AsyncMock(side_effect=ApiError(0, "connection_failed"))
        self.coordinator.data = await self.coordinator._async_update_data()
        self.assertFalse(sensor.available)
        self.assertTrue(sensor.extra_state_attributes["data_stale"])
        self.assertLess(sensor.extra_state_attributes["data_age_minutes"], 1)
        self.assertEqual(sensor.car["readings"]["powertrains_api"]["value"], "error")
        self.coordinator.last_update_success = False
        self.assertFalse(sensor.available)

    async def test_missing_and_bad_timestamp_do_not_replace_saved_range(self):
        await self.poll()
        sensor = self.sensor()
        self.client.get = AsyncMock(return_value=None)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.assertIsNone(sensor.native_value)
        self.assertEqual(sensor.car["readings"][KEY]["value"], 100)
        self.assertTrue(sensor.available)
        for reported in ("invalid", stamp(-60)):
            await self.poll("250", reported_at=reported)
            self.assertIsNone(sensor.native_value)
            self.assertEqual(sensor.car["readings"][KEY]["value"], 100)
            self.assertTrue(sensor.extra_state_attributes["last_known"])

    async def test_restored_ranges_are_discovered_and_convert_once(self):
        reported = stamp(30)
        await self.poll("160.9344", "km", reported)
        # A new coordinator has no in-memory readings; the next response has no range.
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.client.get = AsyncMock(return_value=None)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        self.entry.runtime_data = self.coordinator
        self.hass.config.units = US_CUSTOMARY_SYSTEM
        await entity_registry.async_load(self.hass, load_empty=True)
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        self.addAsyncCleanup(platform.async_reset)
        entities = []
        discover(self.entry, entities.extend, "sensor", ReadingSensor)
        await platform.async_add_entities(entities)
        sensor = next(entity for entity in entities if getattr(entity, "key", None) == KEY)
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual((sensor.native_value, sensor.native_unit_of_measurement), (None, "km"))
        self.assertEqual(state.state, "unknown")
        self.assertEqual(sensor.car["readings"][KEY]["value"], 160.9344)
        self.assertEqual(state.attributes["reported_at"], reported)
        self.assertTrue(state.attributes["data_stale"])

    async def test_duplicate_slots_restore_independently_and_cars_do_not_mix(self):
        cars = [CAR | {"type": "PE"}, CAR | {"vin": "SYNTHETIC-CAR-002"}]
        payload = {"timestamp": stamp(), "distanceToEmpties": [
            {"type": "PHEV", "value": "400", "unit": "km"},
            {"type": "PHEV", "value": "50", "unit": "mile"}]}
        self.client.vehicles = AsyncMock(return_value=cars)
        self.client.get = AsyncMock(side_effect=lambda path: payload if CAR["vin"] in path and path.endswith("/powertrains") else None)
        await self.coordinator._async_update_data()
        payload["distanceToEmpties"][0]["value"] = "-"
        payload["distanceToEmpties"][1]["value"] = "45"
        self.coordinator.data = await self.coordinator._async_update_data()
        first = self.sensor("powertrains_distancetoempties_phev")
        second = self.sensor("powertrains_distancetoempties_phev_2")
        self.assertEqual((first.native_value, second.native_value), (None, 45))
        self.assertEqual(first.car["readings"][first.key]["value"], 400)
        self.assertTrue(first.extra_state_attributes["last_known"])
        self.assertFalse(second.extra_state_attributes["last_known"])
        other = self.coordinator.data["cars"][vehicle_id("kia", cars[1]["vin"])]
        self.assertFalse(any(key.startswith("powertrains_distancetoempties_") for key in other["readings"]))

    async def test_cache_is_pruned_and_removed_with_entry(self):
        await self.poll()
        self.client.vehicles.return_value = []
        await self.coordinator._async_update_data()
        self.assertEqual(await range_store(self.hass, self.entry.entry_id).async_load(), {})
        await self.poll()
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await range_store(self.hass, self.entry.entry_id).async_load())

    async def test_bad_saved_data_is_ignored_and_save_failure_keeps_live_data(self):
        await range_store(self.hass, self.entry.entry_id).async_save({CAR_ID: {
            KEY: {"value": "NaN", "unit": "mi", "name": "EV range", "reported_at": stamp()},
            KEY + "_2": None}, "malformed": []})
        await self.poll(value="-")
        self.assertNotIn(KEY, self.coordinator.data["cars"][CAR_ID]["readings"])
        self.coordinator.ranges.store.async_save = AsyncMock(side_effect=OSError("fixture write error"))
        with self.assertLogs("custom_components.pleos_vehicle_data.ranges", level="WARNING"):
            await self.poll()
        self.assertEqual(self.sensor().native_value, 100)
        self.assertTrue(self.coordinator.ranges.dirty)

    async def test_unreadable_cache_does_not_hide_live_ranges_or_overwrite_disk(self):
        await self.poll()
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.coordinator.ranges.store.async_load = AsyncMock(side_effect=HomeAssistantError("fixture read error"))
        with self.assertLogs("custom_components.pleos_vehicle_data.ranges", level="WARNING"):
            await self.poll("200")
        self.assertEqual(self.sensor().native_value, 200)
        saved = await range_store(self.hass, self.entry.entry_id).async_load()
        self.assertEqual(saved[CAR_ID][KEY]["value"], 100)
