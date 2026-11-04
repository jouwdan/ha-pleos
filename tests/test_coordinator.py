"""Coordinator polling, entity discovery and entity state."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers import device_registry, entity_registry

from pleos_client import ApiError
from custom_components.pleos_vehicle_data.binary_sensor import ReadingBinarySensor
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.device_tracker import VehicleTracker
from custom_components.pleos_vehicle_data.entity import discover
from custom_components.pleos_vehicle_data.sensor import ReadingSensor

from support import CAR, HomeAssistantTestCase, stamp


class CoordinatorTests(HomeAssistantTestCase):
    async def test_stale_missing_and_binary_values(self):
        await self.populate()
        car_id = vehicle_id("kia", CAR["vin"])
        spec = self.coordinator.data["cars"][car_id]["readings"]["batteries_charge_stateofcharge"]
        sensor = ReadingSensor(self.coordinator, car_id, "batteries_charge_stateofcharge", spec)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 50)
        self.assertFalse(sensor.extra_state_attributes["data_stale"])
        self.coordinator.data["cars"][car_id]["sources"]["batteries"] = stamp(4320)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 50)
        self.assertTrue(sensor.extra_state_attributes["data_stale"])
        self.assertGreaterEqual(sensor.extra_state_attributes["data_age_minutes"], 4320)
        diagnostic_spec = self.coordinator.data["cars"][car_id]["readings"]["batteries_reported_at"]
        diagnostic = ReadingSensor(self.coordinator, car_id, "batteries_reported_at", diagnostic_spec)
        self.assertTrue(diagnostic.available)
        self.assertIsNotNone(diagnostic.native_value.tzinfo)
        spec = self.coordinator.data["cars"][car_id]["readings"]["batteries_charge_charging"]
        self.assertFalse(ReadingBinarySensor(self.coordinator, car_id, "batteries_charge_charging", spec).is_on)
        self.assertTrue(ReadingBinarySensor(self.coordinator, car_id, "batteries_charge_charging", spec).available)
        self.coordinator.last_update_success = False
        self.assertFalse(sensor.available)
        self.coordinator.last_update_success = True
        self.coordinator.data["cars"][car_id]["sources"]["batteries"] = None
        self.assertTrue(sensor.available)
        self.assertIsNone(sensor.native_value)
        self.coordinator.data["cars"][car_id]["sources"]["batteries"] = stamp()
        del self.coordinator.data["cars"][car_id]["readings"][sensor.key]
        self.assertTrue(sensor.available)
        self.assertIsNone(sensor.native_value)

    async def test_partial_endpoint_and_network_failure(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=[ApiError(404, "4045"), {"timestamp": stamp(), "engine": {"fuelLevel": 20}}, *([None] * 8)])
        result = await self.coordinator._async_update_data()
        self.assertEqual(len(result["cars"]), 1)
        self.assertTrue(next(iter(result["cars"].values()))["readings"])
        self.coordinator.trips.cache.clear()
        self.client.get = AsyncMock(side_effect=ApiError(0, "connection_failed"))
        result = await self.coordinator._async_update_data()
        car = next(iter(result["cars"].values()))
        self.assertTrue(all(car["readings"][key]["value"] == "error" for key in car["readings"] if key.endswith("_api")))
        self.client.vehicles.side_effect = ApiError(0, "connection_failed")
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()

    async def test_dynamic_discovery_has_no_duplicates(self):
        await self.populate()
        await entity_registry.async_load(self.hass, load_empty=True)
        self.entry.runtime_data = self.coordinator
        added = MagicMock()
        discover(self.entry, added, "sensor", ReadingSensor)
        first = added.call_count
        self.coordinator.async_update_listeners()
        self.assertEqual(added.call_count, first)
        car_id = vehicle_id("kia", CAR["vin"])
        self.coordinator.data["cars"][car_id]["readings"]["new_field"] = {"name": "New", "source": "status", "value": "open", "domain": "sensor"}
        self.coordinator.async_update_listeners()
        self.assertEqual(added.call_count, first)
        self.coordinator.data["cars"][car_id]["readings"]["status_outsidetemperature"] = {
            "name": "Outside temperature", "source": "status", "value": 10, "domain": "sensor",
        }
        self.coordinator.async_update_listeners()
        self.assertEqual(added.call_count, first + 1)

    async def test_native_tracker_and_location_opt_out(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=[None, None, None, None, {
            "timestamp": stamp(), "location": {"type": 0, "latitude": 0, "longitude": 0},
        }, *([None] * 5)])
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        car_id = vehicle_id("kia", CAR["vin"])
        tracker = VehicleTracker(self.coordinator, car_id, "location", {"name": "Location", "source": "locations"})
        self.assertTrue(tracker.available)
        self.assertEqual((tracker.latitude, tracker.longitude), (0, 0))
        self.coordinator.data["cars"][car_id]["sources"]["locations"] = stamp(120)
        self.assertTrue(tracker.available)
        self.assertTrue(tracker.extra_state_attributes["data_stale"])
        self.coordinator.options["location"] = False
        self.coordinator.trips.cache.clear()
        self.client.get = AsyncMock(return_value=None)
        result = await self.coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 9)
        self.assertIsNone(result["cars"][car_id]["location"])

    async def test_diagnostics_register_with_native_entity_category(self):
        await self.populate()
        self.hass.data[device_registry.DATA_REGISTRY] = device_registry.DeviceRegistry(self.hass)
        await device_registry.async_load(self.hass, load_empty=True)
        registry = entity_registry.EntityRegistry(self.hass)
        await registry.async_load(load_empty=True)
        car_id = vehicle_id("kia", CAR["vin"])
        for key, spec in self.coordinator.data["cars"][car_id]["readings"].items():
            if spec["domain"] == "sensor":
                sensor = ReadingSensor(self.coordinator, car_id, key, spec)
                registered = registry.async_get_or_create(
                    "sensor", DOMAIN, sensor.unique_id, config_entry=self.entry,
                    entity_category=sensor.entity_category,
                )
                self.assertEqual(registered.entity_category, sensor.entity_category)

    async def test_all_ten_endpoints_and_date_queries(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(return_value=None)
        result = await self.coordinator._async_update_data()
        paths = [call.args[0].split("/")[-1] for call in self.client.get.await_args_list]
        self.assertEqual(len(paths), 10)
        queried = [call.args[0].split(CAR["vin"] + "/", 1)[1] for call in self.client.get.await_args_list]
        for endpoint in ("batteries", "powertrains", "driving", "status", "locations", "freezer"):
            self.assertIn(endpoint, queried)
        for endpoint in ("powertrains/data", "speed", "driving/data", "status/data"):
            request = next(path for path in queried if path.startswith(endpoint + "?"))
            self.assertIn("startDate=", request)
            self.assertIn("endDate=", request)
            self.assertIn("zoneId=UTC", request)
        car = next(iter(result["cars"].values()))
        self.assertEqual(sum(key.endswith("_api") for key in car["readings"]), 10)

    async def test_full_trip_data_is_retained_and_not_recorded(self):
        payload = {"trips": [{"triggerStartTime": stamp(240), "tripLength": 2000,
                              "location": {"speed": [10, 20], "speedUnit": ["kph", "kph"]}}]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if "/speed?" in path else None)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        car_id = vehicle_id("kia", CAR["vin"])
        car = self.coordinator.data["cars"][car_id]
        source = ReadingSensor(self.coordinator, car_id, "speed_api", car["readings"]["speed_api"])
        self.assertEqual(source.extra_state_attributes["data"], payload)
        self.assertIn("data", source._unrecorded_attributes)
        field = ReadingSensor(self.coordinator, car_id, "speed_location_speed", car["readings"]["speed_location_speed"])
        self.assertTrue(field.available)
        self.assertEqual(field.native_value, 20)
        self.assertNotIn("reported_at", field.extra_state_attributes)

    async def test_optional_endpoint_server_error_does_not_hide_status(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: {"timestamp": stamp(), "charge": {"stateOfCharge": 50}} if path.endswith("/batteries") else None)
        original_get = self.client.get.side_effect
        def response(path):
            if "/status/data?" in path:
                raise ApiError(500, "5000")
            return original_get(path)
        self.client.get.side_effect = response
        result = await self.coordinator._async_update_data()
        car = next(iter(result["cars"].values()))
        self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 50)
        self.assertEqual(car["readings"]["status_data_api"]["value"], "error")

    async def test_optional_permission_error_does_not_hide_status(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        def response(path):
            if path.endswith("/locations"):
                raise ApiError(403, "403")
            return {"timestamp": stamp(), "charge": {"stateOfCharge": 50}} if path.endswith("/batteries") else None
        self.client.get = AsyncMock(side_effect=response)
        result = await self.coordinator._async_update_data()
        car = next(iter(result["cars"].values()))
        self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 50)
        self.assertEqual(car["readings"]["locations_api"]["value"], "error")
        self.assertEqual(car["readings"]["locations_api"]["attributes"]["http_status"], 403)

    async def test_vehicle_information_keeps_every_vehicle_field(self):
        car = CAR | {"nickname": "Van", "name": "SV", "modelYear": 2026, "consentDate": "2026-10-01",
                     "options": {"pv": True}, "api_metadata": "spoof", "colour": None}
        self.client.vehicles = AsyncMock(return_value=[car])
        self.client.get = AsyncMock(return_value=None)
        result = await self.coordinator._async_update_data()
        info = next(iter(result["cars"].values()))["readings"]["vehicle_information"]
        attrs = info["attributes"]
        self.assertEqual(info["value"], "EV")
        self.assertEqual((attrs["vin"], attrs["nickname"], attrs["model_code"], attrs["model"]),
                         (CAR["vin"], "Van", "SV", "Example EV"))
        self.assertEqual((attrs["model_year"], attrs["consent_date"], attrs["options"]), (2026, "2026-10-01", {"pv": True}))
        self.assertIsInstance(attrs["api_metadata"], dict)
        self.assertNotIn("colour", attrs)
        self.assertNotIn("sellname", attrs)


if __name__ == "__main__":
    unittest.main()
