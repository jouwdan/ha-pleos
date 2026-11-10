"""Values present in a response but not turned into readings are counted and explained."""

import unittest
from unittest.mock import AsyncMock

from homeassistant.helpers.json import json_dumps

from custom_components.pleos_vehicle_data.data import REJECTION_EXAMPLES, Rejections, readings, trip_readings, trip_summary, vehicle_id

from support import CAR, HomeAssistantTestCase, stamp


class SnapshotRejectionTests(unittest.TestCase):
    def rejected(self, endpoint, payload, vehicle_type="HV"):
        rejections = Rejections(endpoint)
        readings(endpoint, payload, vehicle_type, rejections)
        return rejections

    def test_each_kind_of_rejection_has_a_reason(self):
        cases = [
            ("batteries", {"charge": {"stateOfCharge": 101}}, "charge.stateOfCharge", 101, "expected 0 to 100, got 101"),
            ("status", {"tire": [{"left": 30, "unit": "furlong"}]}, "tire.row1.left", 30,
             "expected 0 to 254 in psi/kpa/bar, got unit furlong"),
            ("status", {"climateControl": {"speed": 2.5}}, "climateControl.speed", 2.5,
             "expected -1 to 8 as a whole number, got 2.5"),
            ("batteries", {"charge": {"plugin": "maybe"}}, "charge.plugin", "maybe", "expected connected or disconnected"),
            ("status", {"hood": "x" * 300}, "hood", "x" * 100 + "…", "text longer than 255 characters"),
            ("status", {"odd": {"unit": 5, "value": 1}}, "odd.unit", 5, "unit is not text"),
            ("status", {"warnings": ["a", 1]}, "warnings", ["a", 1], "warnings must all be text"),
            ("status", {"hood": float("inf")}, "hood", "inf", "not a finite number"),
            # The value is the problem here, not the accepted unit it came with.
            ("powertrains", {"distanceToEmpties": [{"type": "EV", "value": "-", "unit": "km"}]},
             "distanceToEmpties.EV", "-", "expected 1 to 4095 in feet/km/meter/mile/miles, got '-'"),
        ]
        for endpoint, payload, path, value, reason in cases:
            with self.subTest(path=path):
                rejections = self.rejected(endpoint, payload)
                self.assertEqual(rejections.count, 1)
                example = rejections.examples[0]
                self.assertEqual((example["path"], example["reason"]), (path, reason))
                if not isinstance(value, list):
                    self.assertEqual(example["value"], value)

    def test_absent_invalid_and_deliberately_skipped_values_are_not_rejections(self):
        payload = {"charge": {"stateOfCharge": None, "status": "invalid"}, "engine": {"fuelLevel": 20},
                   "odd": {"unit": "invalid", "value": 3}}
        self.assertEqual(self.rejected("batteries", payload).count, 0)
        self.assertEqual(self.rejected("powertrains", {"engine": {"fuelLevel": 20}}, "EV").count, 0)

    def test_examples_are_capped_but_all_are_counted(self):
        rejections = self.rejected("batteries", {"charge": {"targetStateOfCharge": {f"t{i}": 200 for i in range(9)}}})
        self.assertEqual((rejections.count, len(rejections.examples)), (9, REJECTION_EXAMPLES))

    def test_debug_logging_omits_rejected_payload_values(self):
        with self.assertLogs("custom_components.pleos_vehicle_data.data", level="DEBUG") as logs:
            Rejections("speed").add("unknown", "fixture-private-value", "unsupported type")
        self.assertNotIn("fixture-private-value", " ".join(logs.output))
        self.assertIn("unsupported type", logs.output[0])

    def test_without_a_collector_nothing_changes(self):
        self.assertEqual(readings("batteries", {"charge": {"stateOfCharge": 101}}, "EV"), {})

    def test_rejected_numbers_remain_serializable_by_home_assistant(self):
        for value in (10**30, 10**400, float("inf"), float("nan")):
            with self.subTest(value=repr(value)[:30]):
                rejections = self.rejected("batteries", {"charge": {"stateOfCharge": value}})
                self.assertEqual(rejections.count, 1)
                self.assertIsInstance(rejections.examples[0]["value"], str)
                self.assertIn("rejected_examples", json_dumps(rejections.attributes()))


class TripRejectionTests(unittest.TestCase):
    def test_missing_final_samples_are_not_rejections_or_previous_values(self):
        for missing in (None, "invalid", "null"):
            with self.subTest(missing=missing):
                trip = {"triggerStartTime": stamp(30),
                        "isla": {"optionUsm": ["warning", missing], "optionUsm2": ["none", missing]},
                        "ignition": {"status": ["on", missing]}, "odometer": {"value": [100, missing]},
                        "undocumented": ["example", missing], "scalar": missing}
                rejections = Rejections("driving/data")
                parsed, _ = trip_readings("driving/data", {"trips": [trip]}, rejections)
                self.assertEqual(rejections.count, 0)
                self.assertEqual(set(parsed), {"driving_data_trip_start"})
                self.assertEqual(trip_summary("driving/data", trip), {"trip_start": trip["triggerStartTime"]})
                self.assertEqual(trip["isla"]["optionUsm"], ["warning", missing])

    def test_unusable_last_samples_are_rejections(self):
        trip = {"triggerStartTime": stamp(30), "engine": {"rpm": [800, 99999]},
                "location": {"speed": [10], "speedUnit": ["furlongs"]}}
        rejections = Rejections("powertrains/data")
        trip_readings("powertrains/data", {"trips": [trip]}, rejections)
        self.assertEqual([(e["path"], e["value"]) for e in rejections.examples], [("engine.rpm", 99999)])
        speed = Rejections("speed")
        trip_readings("speed", {"trips": [trip]}, speed)
        self.assertEqual(speed.examples[0]["reason"], "expected 0 to 254 in kph/mph, got unit furlongs")


class DiagnosticSensorTests(HomeAssistantTestCase):
    async def test_literal_null_trip_samples_preserve_raw_diagnostics(self):
        payload = {"trips": [{"triggerStartTime": stamp(30), "tripLength": 1000,
                              "isla": {"optionUsm": ["warning", "null"], "optionUsm2": ["none", "null"]}}]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if "/driving/data?" in path else None)
        result = await self.coordinator._async_update_data()
        parsed = result["cars"][vehicle_id("kia", CAR["vin"])]["readings"]
        diagnostic = parsed["driving_data_api"]
        self.assertEqual(diagnostic["value"], "available")
        self.assertEqual(diagnostic["attributes"]["rejected_values"], 0)
        self.assertEqual(diagnostic["attributes"]["data"], payload)
        self.assertNotIn("driving_data_isla_optionusm", parsed)
        self.assertNotIn("driving_data_isla_optionusm2", parsed)
        self.assertEqual(parsed["driving_data_trip_duration"]["value"], 1)

    async def test_endpoint_diagnostic_sensor_shows_rejections(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: {"timestamp": stamp(), "charge": {"stateOfCharge": 150}}
                                    if "/batteries" in path else None)
        result = await self.coordinator._async_update_data()
        car = result["cars"][vehicle_id("kia", CAR["vin"])]
        attributes = car["readings"]["batteries_api"]["attributes"]
        self.assertEqual(attributes["rejected_values"], 1)
        self.assertEqual(attributes["rejected_examples"][0]["path"], "charge.stateOfCharge")
        self.assertEqual(car["readings"]["status_api"]["attributes"]["rejected_values"], 0)
        self.assertEqual(car["readings"]["speed_api"]["attributes"]["rejected_values"], 0)


if __name__ == "__main__":
    unittest.main()
