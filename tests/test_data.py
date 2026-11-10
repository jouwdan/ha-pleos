"""Snapshot parsing, field specs and warnings, without Home Assistant."""

import unittest

from custom_components.pleos_vehicle_data.const import BRANDS
from custom_components.pleos_vehicle_data.data import (TRIP_FIELDS, Rejections, Spec, scale_pressure, snapshot_spec, TRIP_NAMES, location, readings, vehicle_id)

from support import CAR, stamp


class SnapshotTests(unittest.TestCase):
    def test_documented_on_off_fields_are_binary_and_reject_other_states(self):
        for value in ("on", "off", None, "invalid", "unknown", "unexpected", True, False, 0, 1):
            payloads = {
                "driving": {"ignition": {"status": value, "ignitionDetails": [
                    {"type": kind, "status": value} for kind in ("ign1", "ign3", "acc")]}, "sleepMode": value},
                "status": {"climateControl": {"status": value}, "windshield": {"defrost": value},
                           "steeringWheelHeat": value},
                "freezer": {"freezer": {"status": value}},
            }
            for endpoint, payload in payloads.items():
                with self.subTest(endpoint=endpoint, value=value):
                    rejections = Rejections(endpoint)
                    parsed = readings(endpoint, payload, "EV", rejections)
                    count = {"driving": 5, "status": 3, "freezer": 1}[endpoint]
                    if value in ("on", "off"):
                        self.assertEqual(len(parsed), count)
                        for reading in parsed.values():
                            self.assertEqual((reading["domain"], reading["value"]),
                                             ("binary_sensor", value.upper()))
                            self.assertNotIn("state_class", reading)
                            self.assertNotIn("unit_of_measurement", reading)
                    else:
                        self.assertEqual(parsed, {})
                        self.assertEqual(rejections.count, 0 if value in (None, "invalid") else count)
                        self.assertTrue(all(example["reason"] == "expected on or off" for example in rejections.examples))

    def test_multistate_and_undocumented_on_off_text_remain_sensors(self):
        for value in ("off", "low", "middle", "high"):
            payload = {"seats": [{"left": {"heat": value, "ventilation": value}}],
                       "climateControl": {"mode": "off"}, "newField": "on", "doors": [{"left": "locked"}]}
            parsed = readings("status", payload, "EV")
            self.assertTrue(all(reading["domain"] == "sensor" for reading in parsed.values()))
            self.assertEqual(parsed["status_seats_row1_left_heat"]["value"], value)
            self.assertEqual(parsed["status_seats_row1_left_ventilation"]["value"], value)
            self.assertEqual(parsed["status_newfield"]["value"], "on")
        self.assertEqual(readings("other", {"ignition": {"status": "on"}}, "EV")["other_ignition_status"]["domain"], "sensor")

    def test_range_mile_aliases(self):
        for unit in ("mile", "miles"):
            with self.subTest(unit=unit):
                sample = {"distanceToEmpties": [{"type": "HEV", "value": "257", "unit": unit}]}
                reading = readings("powertrains", sample, "HV")["powertrains_distancetoempties_hev"]
                self.assertEqual((reading["value"], reading["unit_of_measurement"]), (257, "mi"))

    def test_repeated_range_types_keep_separate_slots_and_units(self):
        sample = {"distanceToEmpties": [{"type": "PHEV", "value": "400", "unit": "km"},
                                      {"type": "PHEV", "value": "50", "unit": "mile"},
                                      {"type": "EV", "value": "100", "unit": "km"}]}
        parsed = readings("powertrains", sample, "PE")
        self.assertEqual(parsed["powertrains_distancetoempties_phev"]["value"], 400)
        second = parsed["powertrains_distancetoempties_phev_2"]
        self.assertEqual((second["value"], second["unit_of_measurement"], second["name"]),
                         (50, "mi", "Plug-in hybrid range 2"))
        sample["distanceToEmpties"][0]["value"] = "-"
        parsed = readings("powertrains", sample, "PE")
        self.assertNotIn("powertrains_distancetoempties_phev", parsed)
        self.assertEqual(parsed["powertrains_distancetoempties_phev_2"]["value"], 50)
        self.assertEqual(parsed["powertrains_distancetoempties_ev"]["value"], 100)

    def test_ev_hybrid_and_combustion(self):
        sample = {"engine": {"fuelLevel": 0}, "distanceToEmpties": [{"type": "EV", "value": "106", "unit": "km"}]}
        self.assertNotIn("powertrains_engine_fuellevel", readings("powertrains", sample, "EV"))
        for kind in ("GN", "HV", "PE", "UNKNOWN"):
            self.assertEqual(readings("powertrains", sample, kind)["powertrains_engine_fuellevel"]["value"], 0)
        self.assertEqual(readings("powertrains", sample, "EV")["powertrains_distancetoempties_ev"]["value"], 106)

    def test_absent_and_invalid_values_are_omitted(self):
        self.assertEqual(readings("status", {"outsideTemperature": {"unit": "C"}, "hood": None, "trunk": "invalid"}, "EV"), {})
        self.assertEqual(readings("batteries", {"charge": {"stateOfCharge": 101, "remainTime": 65536, "batteryCapacity": float("nan")}}, "EV"), {})
        self.assertEqual(readings("batteries", {"charge": {"remainTime": 65535}}, "EV")["batteries_charge_remaintime"]["value"], 65535)

    def test_charge_time_is_unknown_while_unplugged(self):
        unplugged = readings("batteries", {"charge": {"plugin": "disconnected", "remainTime": 95}}, "EV")
        self.assertIsNone(unplugged["batteries_charge_remaintime"]["value"])
        self.assertEqual(unplugged["batteries_charge_remaintime"]["attributes"], {"raw_value": 95})
        plugged = readings("batteries", {"charge": {"plugin": "connected", "remainTime": 95}}, "EV")
        self.assertEqual(plugged["batteries_charge_remaintime"]["value"], 95)
        self.assertNotIn("attributes", plugged["batteries_charge_remaintime"])
        # Without a valid plug state the estimate is kept as reported.
        for charge in ({"remainTime": 95}, {"plugin": "maybe", "remainTime": 95}):
            self.assertEqual(readings("batteries", {"charge": charge}, "EV")["batteries_charge_remaintime"]["value"], 95)

    def test_zero_false_and_tyre_units(self):
        sample = readings("batteries", {"charge": {"stateOfCharge": 0, "charging": False, "plugin": "disconnected"}}, "EV")
        self.assertEqual(sample["batteries_charge_stateofcharge"]["value"], 0)
        self.assertEqual(sample["batteries_charge_charging"]["value"], "OFF")
        self.assertEqual(sample["batteries_charge_plugin"]["value"], "OFF")
        for unit, raw, expected in (("psi", 52, 52), ("kpa", 72, 360), ("bar", 36, 3.6)):
            values = readings("status", {"tire": [{"left": raw, "right": 255, "unit": unit}]}, "EV")
            self.assertEqual(values["status_tire_row1_left"]["value"], expected)
            self.assertNotIn("status_tire_row1_right", values)

    def test_location_validation_and_zero_coordinates(self):
        for point in ({"latitude": 0, "longitude": 0, "type": 1}, {"latitude": 100, "longitude": 0, "type": 0}, {"latitude": True, "longitude": 0, "type": 0}):
            self.assertIsNone(location({"location": point, "timestamp": stamp()}))
        self.assertEqual(location({"location": {"latitude": 0, "longitude": 0, "type": 0}, "timestamp": stamp()})["latitude"], 0)

    def test_vehicle_identity_is_stable_and_does_not_contain_vin(self):
        for brand in BRANDS:
            self.assertEqual(vehicle_id(brand, CAR["vin"]), vehicle_id(brand, CAR["vin"]))
            self.assertNotIn(CAR["vin"], vehicle_id(brand, CAR["vin"]))

    def test_freezer_and_all_location_fields(self):
        freezer = readings("freezer", {"freezer": {"status": "on", "temperature": -20}}, "EV")
        self.assertEqual(freezer["freezer_freezer_temperature"]["unit_of_measurement"], "°C")
        self.assertNotIn("freezer_freezer_temperature", readings("freezer", {"freezer": {"temperature": 63}}, "EV"))
        values = readings("locations", {"location": {"latitude": 0, "longitude": 0, "altitude": -1, "heading": 359, "speed": {"value": 10, "unit": "kph"}}}, "EV")
        self.assertEqual(len(values), 5)
        self.assertEqual(values["locations_location_speed"]["device_class"], "speed")

    def test_readable_labels_preserve_data_keys(self):
        values = readings("status", {"doors": [{"left": "locked"}],
                                    "seats": [{"right": {"heat": "off"}}],
                                    "climateControl": {"speed": 2, "temperature": {"value": 20, "unit": "C"}}}, "EV")
        expected = {"status_doors_row1_left": "Front left door",
                    "status_seats_row1_right_heat": "Front right seat heating",
                    "status_climatecontrol_speed": "Cabin fan speed",
                    "status_climatecontrol_temperature": "Cabin target temperature"}
        self.assertEqual({key: value["name"] for key, value in values.items()}, expected)
        self.assertEqual(set(TRIP_NAMES), {field for fields in TRIP_FIELDS.values() for field in fields})


class SpecTests(unittest.TestCase):
    def test_check_validates_range_units_type_and_converts(self):
        tyre = Spec(0, 254, units=("psi", "kpa", "bar"), convert=scale_pressure)
        self.assertEqual((tyre.check(72, "kpa"), tyre.check(36, "bar"), tyre.check(35, "psi")), (360, 3.6, 35))
        for value, unit in ((255, "psi"), (30, "furlong"), (True, "psi"), ("30", "psi"), (float("inf"), "psi")):
            self.assertIsNone(tyre.check(value, unit))
        self.assertEqual(Spec(1, 10, numeric_text=True).check("4", None), 4.0)
        self.assertIsNone(Spec(1, 10, numeric_text=True).check("nan", None))
        self.assertIsNone(Spec(-1, 8, integer=True).check(2.5, None))
        enum = Spec(0, 2, integer=True, options=("a", "b", "c"))
        self.assertEqual((enum.check(1, None), enum.metadata()), ("b", {"device_class": "enum", "options": ["a", "b", "c"]}))
        self.assertEqual(Spec(0, 1, unit="%", device_class="battery").metadata(), {"unit_of_measurement": "%", "device_class": "battery", "state_class": "measurement"})

    def test_snapshot_spec_scopes_by_endpoint_and_path(self):
        self.assertEqual(snapshot_spec("status", ("tire", "row1", "left")).device_class, "pressure")
        self.assertIsNone(snapshot_spec("driving", ("tire", "row1", "left")))
        self.assertEqual(snapshot_spec("batteries", ("charge", "targetStateOfCharge", "quick")).unit, "%")
        self.assertEqual(snapshot_spec("powertrains", ("engine", "fuelLevel")).device_class, None)
        self.assertIsNone(snapshot_spec("status", ("hood",)))


class WarningListTests(unittest.TestCase):
    def test_warnings_keep_full_list_and_count(self):
        many = [f"warning{index:03d}" for index in range(40)]
        reading = readings("status", {"warnings": many}, "EV")["status_warnings"]
        self.assertEqual(len(reading["value"]), 255)
        self.assertEqual(reading["attributes"], {"warnings": many, "warning_count": 40})
        empty = readings("status", {"warnings": []}, "EV")["status_warnings"]
        self.assertEqual((empty["value"], empty["attributes"]), ("None", {"warnings": [], "warning_count": 0}))
        self.assertNotIn("status_warnings", readings("status", {"warnings": ["ok", 1]}, "EV"))


if __name__ == "__main__":
    unittest.main()
