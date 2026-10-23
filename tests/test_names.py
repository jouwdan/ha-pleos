"""Entity name translations match the names the integration generates."""

import json
import logging
import pathlib
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.pleos_vehicle_data.binary_sensor import ReadingBinarySensor
from custom_components.pleos_vehicle_data.const import DOMAIN, ENDPOINTS, TRIP_ENDPOINTS
from custom_components.pleos_vehicle_data.data import TRIP_FIELDS, readings, trip_readings, vehicle_id
from custom_components.pleos_vehicle_data.device_tracker import VehicleTracker
from custom_components.pleos_vehicle_data.names import ENTITY_NAMES
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor, ReadingSensor

from support import CAR, HomeAssistantTestCase, stamp

COMPONENT = pathlib.Path(__file__).parents[1] / "custom_components" / "pleos_vehicle_data"

# One documented payload per snapshot endpoint, covering every named field.
SNAPSHOTS = {
    "batteries": {"charge": {"plugin": "connected", "status": "charging", "charging": True, "stateOfCharge": 40,
                             "batteryCapacity": 77000, "targetStateOfCharge": {"standard": 80, "quick": 90},
                             "remainTime": 10}},
    "powertrains": {"engine": {"fuelLevel": 30}, "batteries": [{"type": "main", "remainRatio": 50}],
                    "distanceToEmpties": [{"type": kind, "value": 100, "unit": "km"}
                                          for kind in ("ICE", "EV", "HEV", "PHEV", "FCEV")]},
    "driving": {"ignition": {"status": "off", "ignitionDetails": [{"type": kind, "status": "off"}
                                                                  for kind in ("ign1", "ign3", "acc")]},
                "sleepMode": "on", "odometer": {"value": 1000, "unit": "km"}, "transmission": {"parkingPosition": True}},
    "status": {"doors": [{"left": "closed", "right": "closed"}] * 2, "hood": "closed", "trunk": "closed",
               "sunroof": "closed", "tire": [{"left": 36, "right": 36, "unit": "psi"}] * 2, "warnings": ["a"],
               "climateControl": {"mode": "auto", "status": "on", "speed": 3, "temperature": {"value": 21, "unit": "C"}},
               "windshield": {"defrost": "off"}, "steeringWheelHeat": "off",
               "seats": [{side: {"heat": "off", "ventilation": "off"} for side in ("left", "right")}] * 2,
               "outsideTemperature": {"value": 15, "unit": "C"}},
    "locations": {"location": {"latitude": 1, "longitude": 1, "altitude": 1, "heading": 1, "type": 0,
                               "speed": {"value": 1, "unit": "kph"}}},
    "freezer": {"freezer": {"status": "on", "temperature": -20}},
}


def trip_payload(endpoint):
    trip = {"triggerStartTime": stamp(30), "tripLength": 1000}
    for field, spec in TRIP_FIELDS[endpoint].items():
        node = trip
        *parents, leaf = field.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        units = {"location.speedUnit": "kph", "tire.pressureUnit": "psi",
                 "isla.optionUsm": "assist", "isla.optionUsm2": "information",
                 "seatbelt.assistance": "belted", "seatbelt.driver": "unbelted",
                 "transmission.gear": "p", "lamp.setting": "autoLightSwOn",
                 **{f"doors.open{side}": "open" for side in ("Assistance", "Driver", "RearLeft", "RearRight")}}
        node[leaf] = [units.get(field, "on") if spec is None else spec.low, units.get(field, "on") if spec is None else spec.low]
    if endpoint == "driving/data":
        trip["odometer"]["value"] = [10, 12]
    return {"trips": [trip]}


class TranslationFileTests(unittest.TestCase):
    def test_strings_and_english_translation_carry_exactly_the_entity_names(self):
        for path in (COMPONENT / "strings.json", COMPONENT / "translations" / "en.json"):
            entity = json.loads(path.read_text())["entity"]
            with self.subTest(file=path.name):
                self.assertEqual({platform: {key: value["name"] for key, value in names.items()}
                                  for platform, names in entity.items()}, ENTITY_NAMES)
        self.assertIn("state", json.loads((COMPONENT / "strings.json").read_text())["entity"]["sensor"]["connection"])

    def test_translation_keys_are_valid(self):
        for names in ENTITY_NAMES.values():
            for key in names:
                self.assertRegex(key, r"^[a-z0-9_-]+$")

    def test_every_documented_reading_has_its_generated_name_as_translation(self):
        produced = {}
        for endpoint, payload in SNAPSHOTS.items():
            produced |= readings(endpoint, payload, "HV")
        for endpoint in TRIP_ENDPOINTS:
            produced |= trip_readings(endpoint, trip_payload(endpoint))[0]
        translated = {key for names in ENTITY_NAMES.values() for key in names}
        for key, reading in produced.items():
            with self.subTest(key=key):
                self.assertIn(key, ENTITY_NAMES[reading["domain"]], "documented field without a translation")
                self.assertEqual(ENTITY_NAMES[reading["domain"]][key], reading["name"])
        # Every translation (apart from coordinator-built entities) is produced by some documented field.
        coordinator_built = {"connection", "last_successful_poll", "archive", "vehicle_information", "trip", "estimated_ev_consumption", "location_address"} | {
            key for key in translated if key.endswith(("_api", "_reported_at", "_data_age"))}
        self.assertEqual(translated - set(produced) - coordinator_built - {"location"}, set())


class EntityNameTests(HomeAssistantTestCase):
    async def test_ignition_binary_entity_registers_on_upgrade_and_tracks_states(self):
        await entity_registry.async_load(self.hass, load_empty=True)
        registry = entity_registry.async_get(self.hass)
        car_id = vehicle_id("kia", CAR["vin"])
        key = "driving_ignition_status"
        legacy = registry.async_get_or_create(
            "sensor", DOMAIN, car_id + "_" + key, config_entry=self.entry,
            suggested_object_id="custom_ignition",
        )
        payload = {"timestamp": stamp(), "ignition": {"status": "on"}}
        self.client.vehicles = AsyncMock(return_value=[CAR | {"nickname": "Van"}])
        self.client.get = AsyncMock(side_effect=lambda path: payload if path.endswith("/driving") else None)
        await self.coordinator.async_refresh()
        reading = self.coordinator.data["cars"][car_id]["readings"][key]
        self.assertEqual(reading["domain"], "binary_sensor")
        binary = ReadingBinarySensor(self.coordinator, car_id, key, reading)
        self.assertEqual(binary.translation_key, key)
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="binary_sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        try:
            await platform.async_add_entities([binary])
            entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, car_id + "_" + key)
            self.assertEqual(entity_id, "binary_sensor.van_ignition")
            self.assertEqual(self.hass.states.get(entity_id).state, "on")
            # Domain changes create a new entity; keep the old registry entry/history for the user.
            self.assertEqual(registry.async_get(legacy.entity_id), legacy)
            for raw, expected in (("off", "off"), ("invalid", "unavailable"), (None, "unavailable"),
                                  ("unexpected", "unavailable"), ("on", "on")):
                with self.subTest(raw=raw):
                    payload["ignition"]["status"] = raw
                    await self.coordinator.async_refresh()
                    self.assertEqual(self.hass.states.get(entity_id).state, expected)
        finally:
            await platform.async_reset()

    async def test_entities_use_translation_keys_and_keep_names_for_undocumented_fields(self):
        payload = {"timestamp": stamp(), "charge": {"stateOfCharge": 50, "charging": False, "newField": 3},
                   "location": {"type": 0, "latitude": 0, "longitude": 0}}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(return_value=payload)
        self.coordinator.data = await self.coordinator._async_update_data()
        car_id = vehicle_id("kia", CAR["vin"])
        car = self.coordinator.data["cars"][car_id]
        for key, reading in car["readings"].items():
            if key in ENTITY_NAMES[reading["domain"]]:
                with self.subTest(key=key):
                    self.assertEqual(ENTITY_NAMES[reading["domain"]][key], reading["name"])
        for endpoint in ENDPOINTS:
            self.assertIn(endpoint.replace("/", "_") + "_api", ENTITY_NAMES["sensor"])

        documented = ReadingSensor(self.coordinator, car_id, "batteries_charge_stateofcharge",
                                   car["readings"]["batteries_charge_stateofcharge"])
        self.assertEqual(documented.translation_key, "batteries_charge_stateofcharge")
        self.assertFalse(hasattr(documented, "_attr_name"))
        binary = ReadingBinarySensor(self.coordinator, car_id, "batteries_charge_charging",
                                     car["readings"]["batteries_charge_charging"])
        self.assertEqual(binary.translation_key, "batteries_charge_charging")
        undocumented = ReadingSensor(self.coordinator, car_id, "batteries_charge_newfield",
                                     car["readings"]["batteries_charge_newfield"])
        self.assertIsNone(undocumented.translation_key)
        self.assertEqual(undocumented.name, "Charge new field")
        tracker = VehicleTracker(self.coordinator, car_id, "location", {"name": "Location", "source": "locations"})
        self.assertEqual(tracker.translation_key, "location")
        self.assertEqual(ConnectionSensor(self.coordinator).translation_key, "connection")

    async def test_entity_ids_stay_english_on_a_translated_system(self):
        """A German system with a German translation shows German names but creates English entity IDs."""
        self.hass.config.language = "de"
        await entity_registry.async_load(self.hass, load_empty=True)
        self.client.vehicles = AsyncMock(return_value=[CAR | {"nickname": "Van"}])
        self.client.get = AsyncMock(return_value={"timestamp": stamp(), "charge": {"stateOfCharge": 50}})
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        car_id = vehicle_id("kia", CAR["vin"])
        car = self.coordinator.data["cars"][car_id]
        german = {f"component.{DOMAIN}.entity.sensor.{key}.name": name for key, name in (
            ("batteries_charge_stateofcharge", "Batteriestand"), ("connection", "Verbindung"))}

        async def translations(hass, language, category, integrations):
            return german if language == "de" and category == "entity" else {}

        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="sensor",
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                  entity_namespace=None)
        platform.config_entry = self.entry
        with patch("homeassistant.helpers.translation.async_get_translations", side_effect=translations):
            await platform.platform_data.async_load_translations()
            await platform.async_add_entities([
                ReadingSensor(self.coordinator, car_id, "batteries_charge_stateofcharge",
                              car["readings"]["batteries_charge_stateofcharge"]),
                ConnectionSensor(self.coordinator),
            ])
        registry = entity_registry.async_get(self.hass)
        level = registry.async_get_entity_id("sensor", DOMAIN, car_id + "_batteries_charge_stateofcharge")
        connection = registry.async_get_entity_id("sensor", DOMAIN, "kia_api_status")
        self.assertEqual((level, connection), ("sensor.van_battery_level", "sensor.kia_api_connection"))
        self.assertEqual(self.hass.states.get(level).name, "Van Batteriestand")
        await platform.async_reset()

if __name__ == "__main__":
    unittest.main()
