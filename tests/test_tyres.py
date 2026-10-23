"""Tyre pressures retain their native readings and respect HA display-unit choices."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform
from pleos_client import ApiError

from custom_components.pleos_vehicle_data import async_remove_entry
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.ranges import range_store
from custom_components.pleos_vehicle_data.sensor import ReadingSensor, async_setup_entry
from support import CAR, HomeAssistantTestCase, stamp

CAR_ID = vehicle_id("kia", CAR["vin"])
KEY = "status_tire_row1_left"


class TyreTests(HomeAssistantTestCase):
    async def poll(self, tyres=None, reported_at=None):
        payload = {"timestamp": reported_at or stamp(), "tire": tyres}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if path.endswith("/status") else None)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        return payload

    def sensor(self, key=KEY):
        spec = self.coordinator.data["cars"][CAR_ID]["readings"][key]
        return ReadingSensor(self.coordinator, CAR_ID, key, spec)

    async def register(self, previous_unit=None, explicit_unit=None):
        self.entry.runtime_data = self.coordinator
        await entity_registry.async_load(self.hass, load_empty=True)
        if previous_unit:
            registry = entity_registry.async_get(self.hass)
            registered = registry.async_get_or_create(
                "sensor", DOMAIN, CAR_ID + "_" + KEY, config_entry=self.entry,
                original_device_class="pressure", unit_of_measurement=previous_unit)
            registry.async_update_entity_options(registered.entity_id, "sensor.private",
                                                 {"suggested_unit_of_measurement": previous_unit})
            if explicit_unit:
                registry.async_update_entity_options(registered.entity_id, "sensor",
                                                     {"unit_of_measurement": explicit_unit})
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        self.addAsyncCleanup(platform.async_reset)
        entities = []
        await async_setup_entry(self.hass, self.entry, entities.extend)
        await platform.async_add_entities(entities)
        return next(entity for entity in entities if getattr(entity, "key", None) == KEY)

    async def test_parked_readings_survive_restart_and_remain_discoverable(self):
        reported = stamp(30)
        await self.poll([{"left": 60, "right": 61, "unit": "kpa"},
                         {"left": 62, "right": 63, "unit": "kpa"}], reported)
        await self.poll()
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        await self.poll()
        sensor = await self.register()
        state = self.hass.states.get(sensor.entity_id)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 300)
        self.assertEqual(sensor.native_unit_of_measurement, "kPa")
        self.assertEqual(state.attributes["unit_of_measurement"], "psi")
        self.assertAlmostEqual(float(state.state), 43.5113, places=3)
        self.assertEqual(state.attributes["reported_at"], reported)
        self.assertTrue(state.attributes["last_known"])
        self.assertTrue(state.attributes["data_stale"])
        self.assertEqual(self.sensor("status_tire_row2_right").native_value, 315)
        self.assertEqual(sensor.state_class, "measurement")

    async def test_missing_invalid_and_undated_pressures_keep_original_timestamp(self):
        reported = stamp(30)
        await self.poll([{"left": 44, "unit": "psi"}], reported)
        sensor = self.sensor()
        for value in (None, "-", 255, -1, True, float("inf")):
            await self.poll([{"left": value, "unit": "psi"}])
            self.assertTrue(sensor.available)
            self.assertEqual(sensor.native_value, 44)
            self.assertEqual(sensor.extra_state_attributes["reported_at"], reported)
            self.assertTrue(sensor.extra_state_attributes["last_known"])
        for timestamp in ("invalid", stamp(-60)):
            await self.poll([{"left": 50, "unit": "psi"}], timestamp)
            self.assertEqual(sensor.native_value, 44)
        self.client.get = AsyncMock(side_effect=ApiError(503, "fixture failure"))
        self.coordinator.data = await self.coordinator._async_update_data()
        self.assertEqual(sensor.native_value, 44)
        self.assertEqual(sensor.car["readings"]["status_api"]["value"], "error")
        self.assertTrue(sensor.extra_state_attributes["data_stale"])
        # A valid zero pressure is not mistaken for missing data.
        await self.poll([{"left": 0, "unit": "psi"}])
        self.assertEqual(sensor.native_value, 0)
        self.assertFalse(sensor.extra_state_attributes["last_known"])

    async def test_psi_default_converts_native_units_and_bar_override_survives_retention(self):
        await self.poll([{"left": 60, "unit": "kpa"}])
        sensor = await self.register()
        for unit, value, psi in (("kpa", 60, 43.5113), ("bar", 30, 43.5113), ("psi", 44, 44)):
            payload = await self.poll([{"left": value, "unit": unit}])
            sensor.async_write_ha_state()
            state = self.hass.states.get(sensor.entity_id)
            self.assertEqual(state.attributes["unit_of_measurement"], "psi")
            self.assertAlmostEqual(float(state.state), psi, places=3)
            self.assertEqual(payload["tire"][0]["left"], value)
        entity_registry.async_get(self.hass).async_update_entity_options(
            sensor.entity_id, "sensor", {"unit_of_measurement": "bar"})
        await self.hass.async_block_till_done()
        await self.poll()
        sensor.async_write_ha_state()
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.attributes["unit_of_measurement"], "bar")
        self.assertAlmostEqual(float(state.state), 3.0337, places=3)
        await self.poll([{"left": 32, "unit": "bar"}])
        sensor.async_write_ha_state()
        self.assertEqual(float(self.hass.states.get(sensor.entity_id).state), 3.2)
        self.assertFalse(sensor.extra_state_attributes["last_known"])
        self.assertFalse(sensor.extra_state_attributes["data_stale"])

    async def test_cache_is_isolated_pruned_and_removed_with_account(self):
        await self.poll([{"left": 44, "unit": "psi"}])
        self.client.vehicles.return_value = [CAR | {"vin": "SYNTHETIC-CAR-002"}]
        self.client.get = AsyncMock(return_value=None)
        result = await self.coordinator._async_update_data()
        self.assertNotIn(KEY, next(iter(result["cars"].values()))["readings"])
        self.assertEqual(await range_store(self.hass, self.entry.entry_id).async_load(), {})
        await self.poll([{"left": 44, "unit": "psi"}])
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await range_store(self.hass, self.entry.entry_id).async_load())

    async def test_existing_default_changes_to_psi(self):
        await self.poll([{"left": 60, "unit": "kpa"}])
        sensor = await self.register(previous_unit="kPa")
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.attributes["unit_of_measurement"], "psi")
        self.assertAlmostEqual(float(state.state), 43.5113, places=3)

    async def test_existing_explicit_bar_choice_is_preserved(self):
        await self.poll([{"left": 60, "unit": "kpa"}])
        sensor = await self.register(previous_unit="kPa", explicit_unit="bar")
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.attributes["unit_of_measurement"], "bar")
        self.assertEqual(float(state.state), 3)

    async def test_invalid_saved_pressures_are_ignored(self):
        for value, unit in ((True, "psi"), (-1, "psi"), (255, "psi"), (1271, "kPa"),
                            (25.5, "bar"), (44, "bad"), (44, []), (float("inf"), "psi")):
            with self.subTest(value=value, unit=unit):
                await self.coordinator.async_shutdown()
                await range_store(self.hass, self.entry.entry_id).async_save({CAR_ID: {
                    KEY: {"value": value, "unit": unit, "name": "Front left tyre pressure", "reported_at": stamp()}}})
                self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
                await self.poll()
                self.assertNotIn(KEY, self.coordinator.data["cars"][CAR_ID]["readings"])
