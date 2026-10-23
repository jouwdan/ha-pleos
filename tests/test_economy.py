"""EV economy uses real distance, matching reports and conservative reset boundaries."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pleos_client import ApiError

from custom_components.pleos_vehicle_data import async_remove_entry
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import readings, vehicle_id
from custom_components.pleos_vehicle_data.economy import ECONOMY_KEY, economy_store
from custom_components.pleos_vehicle_data.sensor import ReadingSensor

from support import CAR, HomeAssistantTestCase

CAR_ID = vehicle_id("kia", CAR["vin"])
CAPACITY = "batteries_charge_batterycapacity"


class EconomyTests(HomeAssistantTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.now = datetime.now(timezone.utc)
        await entity_registry.async_load(self.hass, load_empty=True)

    def at(self, minutes):
        return (self.now - timedelta(minutes=minutes)).isoformat()

    async def poll(self, km=1000, soc=80, minutes=55, capacity=256320, unit="km", charge=None,
                   driving_minutes=None, cars=None, fail_driving=False):
        self.battery = {"timestamp": self.at(minutes), "charge": {
            "stateOfCharge": soc, "batteryCapacity": capacity, "plugin": "disconnected",
            "charging": False, "status": "notCharging", **(charge or {})}}
        self.driving = {"timestamp": self.at(minutes if driving_minutes is None else driving_minutes),
                        "odometer": {"value": km, "unit": unit}}
        def serve(path):
            if path.endswith("/batteries"):
                return self.battery
            if path.endswith("/driving"):
                if fail_driving:
                    raise ApiError(500, "fixture failure")
                return self.driving
            return None
        self.client.vehicles = AsyncMock(return_value=cars or [CAR])
        self.client.get = AsyncMock(side_effect=serve)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        return self.coordinator.data["cars"][CAR_ID]["readings"].get(ECONOMY_KEY)

    async def ready(self):
        await self.poll()
        return await self.poll(km=1050, soc=70, minutes=25)

    async def register_sensor(self, key=ECONOMY_KEY):
        spec = self.coordinator.data["cars"][CAR_ID]["readings"][key]
        sensor = ReadingSensor(self.coordinator, CAR_ID, key, spec)
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        await platform.async_add_entities([sensor])
        self.addAsyncCleanup(platform.async_reset)
        return sensor

    async def test_capacity_is_kwh_and_raw_payload_is_preserved(self):
        await self.poll()
        sensor = await self.register_sensor(CAPACITY)
        self.assertEqual((sensor.native_value, sensor.native_unit_of_measurement, sensor.device_class),
                         (71.2, "kWh", "energy_storage"))
        self.assertIsNone(sensor.state_class)
        self.assertEqual(self.battery["charge"]["batteryCapacity"], 256320)
        for value in (True, -1, "256320", float("nan"), float("inf"), None):
            self.assertNotIn(CAPACITY, readings("batteries", {"charge": {"batteryCapacity": value}}, "EV"))
        self.assertNotIn(CAPACITY, readings("batteries", {"charge": {"batteryCapacity": 71.2, "unit": "kWh"}}, "EV"))

    async def test_accumulates_distance_and_soc_instead_of_averaging_poll_ratios(self):
        self.assertIsNone((await self.poll())["value"])
        self.assertIsNone((await self.poll(km=1005, soc=79, minutes=50))["value"])
        self.assertIsNone((await self.poll(km=1020, soc=76, minutes=40))["value"])
        reading = await self.poll(km=1050, soc=70, minutes=25)
        self.assertAlmostEqual(reading["value"], 14.24)
        self.assertEqual(reading["attributes"]["distance_km"], 50)
        self.assertEqual(reading["attributes"]["energy_used_kwh"], 7.12)
        self.assertTrue(reading["attributes"]["estimated"])
        self.assertEqual(reading["attributes"]["window_started_at"], self.at(55))

    async def test_needs_both_distance_and_soc_thresholds(self):
        await self.poll()
        self.assertIsNone((await self.poll(km=1009, soc=70, minutes=45))["value"])
        self.assertIsNotNone((await self.poll(km=1010, soc=70, minutes=40))["value"])

    async def check_display_unit(self, system, unit, expected):
        await self.ready()
        self.hass.config.units = system
        sensor = await self.register_sensor()
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.attributes["unit_of_measurement"], unit)
        self.assertAlmostEqual(float(state.state), expected, places=2)
        entity_registry.async_get(self.hass).async_update_entity_options(
            sensor.entity_id, "sensor", {"unit_of_measurement": "Wh/km"})
        await self.hass.async_block_till_done()
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(state.attributes["unit_of_measurement"], "Wh/km")
        self.assertAlmostEqual(float(state.state), 142.4, places=2)

    async def test_metric_default_and_user_override(self):
        await self.check_display_unit(METRIC_SYSTEM, "kWh/100km", 14.24)

    async def test_miles_default_and_user_override(self):
        await self.check_display_unit(US_CUSTOMARY_SYSTEM, "mi/kWh", 100 / 14.24 / 1.609344)

    async def test_odometer_units_and_user_correction_are_applied_once(self):
        for unit, factor in (("km", 1), ("mile", 1.609344), ("meter", .001), ("feet", .0003048)):
            self.coordinator.economy.windows = {}
            await self.poll(km=1000 / factor, unit=unit)
            result = await self.poll(km=1050 / factor, unit=unit, soc=70, minutes=25)
            self.assertAlmostEqual(result["value"], 14.24)
        self.coordinator.economy.windows = {}
        self.coordinator.options["odometer_km_vehicles"] = [CAR_ID]
        await self.poll(unit="mile")
        result = await self.poll(km=1050, unit="mile", soc=70, minutes=25)
        self.assertAlmostEqual(result["value"], 14.24)

    async def test_charge_plug_v2l_and_unknown_status_clear_window(self):
        for charge in ({"charging": True}, {"plugin": "connected"}, {"status": "fastCharging"},
                       {"status": "v2lOperating"}, {"status": "v2xOperating"},
                       {"charging": None}, {"plugin": "invalid"}, {"status": "invalid"}):
            await self.ready()
            result = await self.poll(km=1050, soc=70, minutes=20, charge=charge)
            self.assertAlmostEqual(result["value"], 14.24)
            self.assertTrue(result["attributes"]["last_known"])
            self.assertTrue(result["attributes"]["data_stale"])
            self.assertEqual(result["attributes"]["reported_at"], self.at(25))
            self.assertEqual(self.coordinator.economy.windows, {})
            result = await self.poll(km=1060, soc=69, minutes=10)
            self.assertAlmostEqual(result["value"], 14.24)
            self.assertTrue(result["attributes"]["last_known"])
            self.coordinator.economy.windows = {}

    async def test_battery_increase_capacity_change_and_stationary_loss_start_new_windows(self):
        for kwargs in ({"soc": 71}, {"capacity": 270000}, {"soc": 69}):
            self.coordinator.economy.windows = {}
            await self.ready()
            result = await self.poll(km=1050, soc=kwargs.get("soc", 70), minutes=20,
                                     capacity=kwargs.get("capacity", 256320))
            self.assertAlmostEqual(result["value"], 14.24)
            self.assertTrue(result["attributes"]["last_known"])
            self.assertEqual(self.coordinator.economy.windows[CAR_ID]["start"]["reported_at"], self.at(20))

    async def test_odometer_reset_impossible_jump_and_revised_same_timestamp_reset(self):
        for km, minutes in ((900, 20), (2000, 20), (1051, 25)):
            self.coordinator.economy.windows = {}
            await self.ready()
            self.assertAlmostEqual((await self.poll(km=km, soc=69, minutes=minutes))["value"], 14.24)
            self.assertEqual(self.coordinator.economy.windows[CAR_ID]["start"]["km"], km)

    async def test_repeated_and_out_of_order_reports_do_not_add_consumption(self):
        original = await self.ready()
        self.coordinator.economy.store.async_save = AsyncMock(wraps=self.coordinator.economy.store.async_save)
        self.assertEqual(await self.poll(km=1050, soc=70, minutes=25), original)
        self.coordinator.economy.store.async_save.assert_not_called()
        retained = await self.poll(km=1030, soc=75, minutes=40)
        self.assertEqual(retained["value"], original["value"])
        self.assertEqual(retained["attributes"]["reported_at"], original["attributes"]["reported_at"])
        self.assertTrue(retained["attributes"]["last_known"])
        self.assertEqual(await self.poll(km=1050, soc=70, minutes=25), original)

    async def test_stale_mismatched_missing_or_retained_inputs_cannot_create_estimate(self):
        for kwargs in ({"driving_minutes": 24}, {"soc": None}, {"capacity": 0},
                       {"capacity": 3600001}, {"fail_driving": True}):
            self.coordinator.economy.windows = {}
            await self.ready()
            settings = {"km": 1060, "soc": 65, "minutes": 15, **kwargs}
            retained = await self.poll(**settings)
            self.assertAlmostEqual(retained["value"], 14.24)
            self.assertTrue(retained["attributes"]["last_known"])
            self.assertEqual(self.coordinator.economy.windows, {})

    async def test_stale_new_reports_cannot_start_or_extend_a_window(self):
        self.assertIsNone(await self.poll(minutes=120))
        self.assertEqual(self.coordinator.economy.windows, {})
        await self.ready()
        self.coordinator.options["stale_minutes"] = 1
        self.assertAlmostEqual((await self.poll(km=1060, soc=65, minutes=10))["value"], 14.24)
        self.assertEqual(self.coordinator.economy.windows, {})

    async def test_repeated_parked_reading_can_remain_visible_but_long_gap_resets(self):
        original = await self.ready()
        self.coordinator.options["stale_minutes"] = 1
        self.assertEqual(await self.poll(km=1050, soc=70, minutes=25), original)
        self.coordinator.options["stale_minutes"] = 60
        window = self.coordinator.economy.windows[CAR_ID]
        window["start"]["reported_at"] = self.at(1600)
        window["last"]["reported_at"] = self.at(1500)
        retained = await self.poll(km=1060, soc=69, minutes=10)
        self.assertAlmostEqual(retained["value"], 14.24)
        self.assertTrue(retained["attributes"]["last_known"])

    async def test_restart_retains_window_and_account_removal_clears_it(self):
        await self.poll()
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        result = await self.poll(km=1050, soc=70, minutes=25)
        self.assertAlmostEqual(result["value"], 14.24)
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await economy_store(self.hass, self.entry.entry_id).async_load())

    async def test_only_evs_and_each_vehicle_has_separate_state(self):
        other = CAR | {"vin": "SYNTHETIC-CAR-002"}
        await self.poll()
        await self.poll(km=1050, soc=70, minutes=25, cars=[CAR, other])
        cars = self.coordinator.data["cars"]
        self.assertAlmostEqual(cars[CAR_ID]["readings"][ECONOMY_KEY]["value"], 14.24)
        self.assertIsNone(cars[vehicle_id("kia", other["vin"])]["readings"][ECONOMY_KEY]["value"])
        for kind in ("PE", "HV", "ICE", "FV", "UNKNOWN"):
            self.assertIsNone(await self.poll(cars=[CAR | {"type": kind}]))
        self.client.vehicles.return_value = []
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.economy.windows, {})
        self.assertEqual(self.coordinator.economy.estimates, {})

    async def test_corrupt_storage_and_save_failure_do_not_break_vehicle_updates(self):
        await economy_store(self.hass, self.entry.entry_id).async_save({CAR_ID: {"start": None}, "bad": []})
        await self.poll()
        self.coordinator.economy.store.async_save = AsyncMock(side_effect=OSError("fixture write failure"))
        with self.assertLogs("custom_components.pleos_vehicle_data.economy", level="WARNING"):
            result = await self.poll(km=1050, soc=70, minutes=25)
        self.assertAlmostEqual(result["value"], 14.24)
        self.assertTrue(self.coordinator.economy.dirty)

    async def test_unreadable_storage_is_not_overwritten(self):
        await self.ready()
        original = await economy_store(self.hass, self.entry.entry_id).async_load()
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.coordinator.economy.store.async_load = AsyncMock(side_effect=HomeAssistantError("fixture read failure"))
        with self.assertLogs("custom_components.pleos_vehicle_data.economy", level="WARNING"):
            result = await self.poll(km=1060, soc=69, minutes=10)
        self.assertIsNone(result["value"])
        self.assertEqual(await economy_store(self.hass, self.entry.entry_id).async_load(), original)

    async def test_completed_estimate_survives_charging_restart_and_new_window(self):
        original = await self.ready()
        await self.poll(km=1050, soc=90, minutes=20, charge={"charging": True})
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        result = await self.poll(km=1050, soc=90, minutes=15, charge={"plugin": "connected"})
        self.assertEqual(result["value"], original["value"])
        self.assertEqual(result["attributes"]["reported_at"], self.at(25))
        sensor = await self.register_sensor()
        self.assertTrue(sensor.available)
        self.assertTrue(self.hass.states.get(sensor.entity_id).attributes["last_known"])
        self.assertTrue(sensor.extra_state_attributes["data_stale"])
        # Retaining a result must not bridge the calculation across a charge.
        result = await self.poll(km=1050, soc=90, minutes=10)
        self.assertEqual(result["value"], original["value"])
        result = await self.poll(km=1070, soc=85, minutes=0)
        self.assertAlmostEqual(result["value"], 17.8)
        self.assertFalse(result["attributes"]["last_known"])
        self.assertEqual(result["attributes"]["window_started_at"], self.at(10))
        sensor.async_write_ha_state()
        self.assertFalse(sensor.extra_state_attributes["data_stale"])
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await economy_store(self.hass, self.entry.entry_id).async_load())

    async def test_legacy_complete_window_restores_estimate_on_missing_inputs(self):
        await self.ready()
        legacy = {CAR_ID: self.coordinator.economy.windows[CAR_ID]}
        await economy_store(self.hass, self.entry.entry_id).async_save(legacy)
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        result = await self.poll(soc=None)
        self.assertAlmostEqual(result["value"], 14.24)
        self.assertTrue(result["attributes"]["last_known"])
        self.assertEqual(self.coordinator.economy.windows, {})

    async def test_invalid_saved_estimates_are_ignored(self):
        sample = {"reported_at": self.at(20), "km": 1000, "soc": 80, "capacity": 71.2}
        for estimate in (None, [], {"start": sample, "last": sample},
                         {"start": sample, "last": sample | {"km": 1050, "soc": 70, "capacity": 72}},
                         {"start": sample, "last": sample | {"km": float("inf"), "soc": 70}},
                         {"start": sample, "last": sample | {"km": 1050, "soc": 70, "reported_at": "invalid"}}):
            with self.subTest(estimate=estimate):
                await self.coordinator.async_shutdown()
                await economy_store(self.hass, self.entry.entry_id).async_save({CAR_ID: {"estimate": estimate}})
                self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
                self.assertIsNone(await self.poll(soc=None))

    async def test_new_vehicle_does_not_inherit_an_estimate(self):
        await self.ready()
        other = CAR | {"vin": "SYNTHETIC-CAR-002"}
        await self.poll(soc=None, cars=[CAR, other])
        other_readings = self.coordinator.data["cars"][vehicle_id("kia", other["vin"])]["readings"]
        self.assertNotIn(ECONOMY_KEY, other_readings)
        self.client.vehicles.return_value = []
        await self.coordinator._async_update_data()
        self.assertEqual(await economy_store(self.hass, self.entry.entry_id).async_load(), {})
