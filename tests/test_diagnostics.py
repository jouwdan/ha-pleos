"""The diagnostics download contains the integration's state without personal data."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.diagnostics import (async_get_config_entry_diagnostics,
                                                             async_get_device_diagnostics)

from support import CAR, OLD, HomeAssistantTestCase, stamp

VIN = CAR["vin"]


class DiagnosticsTests(HomeAssistantTestCase):
    async def populate_everything(self):
        car = CAR | {"nickname": "Family van", "modelYear": 2026}
        self.client.vehicles = AsyncMock(return_value=[car])
        self.client.response_meta = {f"/v1/vehicles/{VIN}/batteries": {"requestId": "req-1", "note": f"car {VIN}"}}

        def get(path):
            if "/locations" in path:
                return {"timestamp": stamp(), "location": {"type": 0, "latitude": 48.137, "longitude": 11.575,
                                                            "altitude": 520}}
            if "/batteries" in path:
                return {"timestamp": stamp(), "charge": {"stateOfCharge": 64}, "echo": VIN}
            if "/speed?" in path:
                return {"trips": [{"triggerStartTime": stamp(30), "location": {"speed": [40], "speedUnit": ["kph"]}}]}
            return None
        self.client.get = AsyncMock(side_effect=get)
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        self.entry.runtime_data = self.coordinator

    async def test_entry_diagnostics_cover_state_and_hide_personal_data(self):
        await self.populate_everything()
        diagnostics = await async_get_config_entry_diagnostics(self.hass, self.entry)
        text = json.dumps(diagnostics)  # Must be serialisable as it is.
        for secret in (VIN, OLD["client_id"], OLD["client_secret"], "Family van", "48.137", "11.575"):
            self.assertNotIn(secret, text)
        self.assertEqual(diagnostics["entry"]["data"]["brand"], "kia")
        self.assertEqual(diagnostics["coordinator"]["status"], "connected")
        self.assertIn("max_query_days", diagnostics["trips"])
        self.assertEqual(diagnostics["archive"]["enabled"], True)
        self.assertEqual(diagnostics["archive"]["status"]["state"], "ok")
        car = diagnostics["cars"][vehicle_id("kia", VIN)]
        self.assertEqual(car["readings"]["batteries_charge_stateofcharge"]["value"], 64)
        self.assertEqual(car["readings"]["locations_location_latitude"]["value"], "**REDACTED**")
        self.assertEqual(car["readings"]["locations_location_altitude"]["value"], 520)
        self.assertEqual(car["readings"]["vehicle_information"]["attributes"]["model_year"], 2026)
        self.assertEqual(car["readings"]["batteries_api"]["attributes"]["data"]["echo"], "**REDACTED**")
        self.assertEqual(diagnostics["coordinator"]["api_metadata"][0]["note"], "car **REDACTED**")

    async def test_device_diagnostics_return_one_car_or_the_account(self):
        await self.populate_everything()
        car_id = vehicle_id("kia", VIN)
        car = await async_get_device_diagnostics(self.hass, self.entry, SimpleNamespace(identifiers={(DOMAIN, car_id)}))
        self.assertEqual(car["vehicle_id"], car_id)
        self.assertNotIn(VIN, json.dumps(car))
        account = await async_get_device_diagnostics(self.hass, self.entry, SimpleNamespace(identifiers={(DOMAIN, "kia_api")}))
        self.assertNotIn("cars", account)
        self.assertIn("coordinator", account)

    async def test_diagnostics_before_the_first_successful_poll(self):
        self.entry.runtime_data = self.coordinator
        diagnostics = await async_get_config_entry_diagnostics(self.hass, self.entry)
        json.dumps(diagnostics)
        self.assertEqual(diagnostics["cars"], {})

    async def test_diagnostics_without_scheduled_polling(self):
        self.entry.runtime_data = self.coordinator
        self.coordinator.update_interval = None
        diagnostics = await async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertIsNone(diagnostics["coordinator"]["update_interval_minutes"])

    async def test_diagnostics_redact_secrets_embedded_in_text_and_keys(self):
        await self.populate_everything()
        self.client.token = "fixture-bearer-token"
        secrets = (CAR["vin"], "Family van", OLD["client_id"], OLD["client_secret"], self.client.token)
        self.client.response_meta["echo"] = {"message": " ".join(secrets), OLD["client_secret"]: "echo"}
        self.coordinator.last_exception = RuntimeError(" ".join(secrets))
        car_id = vehicle_id("kia", CAR["vin"])
        original = self.coordinator.data["cars"][car_id]["vehicle"].copy()
        device = SimpleNamespace(identifiers={(DOMAIN, car_id)})
        for diagnostics in (await async_get_config_entry_diagnostics(self.hass, self.entry),
                            await async_get_device_diagnostics(self.hass, self.entry, device)):
            for secret in secrets:
                self.assertNotIn(secret, json.dumps(diagnostics))
        self.assertEqual(self.coordinator.data["cars"][car_id]["vehicle"], original)

    async def test_diagnostics_redact_trip_coordinates_summaries_and_rejected_values(self):
        await self.populate_everything()
        car_id = vehicle_id("kia", VIN)
        readings = self.coordinator.data["cars"][car_id]["readings"]
        readings["speed_location_latitude"] = {"value": 48.137, "attributes": {"trip_mean": 48.137}}
        readings["speed_api"]["attributes"]["rejected_examples"] = [
            {"path": "location.latitude", "value": 48.137, "reason": "unexpected type"}]
        diagnostics = await async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertNotIn("48.137", json.dumps(diagnostics))
        self.assertEqual(readings["speed_location_latitude"]["value"], 48.137)

    async def test_statistics_status_shows_hours_and_pending_imports(self):
        await self.populate_everything()
        trip = {"triggerStartTime": stamp(120), "tripLength": 600000, "odometer": {"value": [100, 112.5]}}
        car_id = vehicle_id("kia", VIN)
        statistics = self.coordinator.statistics
        statistics.add(car_id, "driving/data", trip, "Family van")
        # No recorder in this test: the hour waits for import and diagnostics say so.
        await statistics.flush(True)
        state = (await async_get_config_entry_diagnostics(self.hass, self.entry))["statistics"]
        self.assertFalse(state["recorder_loaded"])
        car = state[car_id]
        # The fixture's speed trip and this driving trip.
        self.assertEqual((car["hours"], car["distance_km"], car["recent_trips"]), (2, 12.5, 2))
        self.assertEqual(car["pending_from"], car["first_hour"])
        self.assertIn(f"pleos_vehicle_data:{car_id}_distance", car["statistic_ids"])
        self.assertNotIn("Family van", json.dumps(state))
