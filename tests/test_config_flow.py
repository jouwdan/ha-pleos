"""Setup, reauthentication, reconfiguration and options flows."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import probatio
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv, device_registry as dr

from custom_components.pleos_vehicle_data import async_setup_entry
from custom_components.pleos_vehicle_data.account import account_id, credential_id
from pleos_client import ApiError, AuthError, PleosClient
from custom_components.pleos_vehicle_data.config_flow import PleosConfigFlow, PleosOptionsFlow
from custom_components.pleos_vehicle_data.const import BRANDS, DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor

from support import CAR, HomeAssistantTestCase, NEW, OLD, entry, stamp


class ConfigFlowTests(HomeAssistantTestCase):
    async def test_blank_credentials_do_not_make_requests(self):
        for credentials in (NEW | {"client_id": "  "}, NEW | {"client_secret": "\t"}):
            with self.subTest(credentials=credentials), patch.object(PleosClient, "vehicles", AsyncMock()) as request:
                self.assertEqual(await self.flow().validate("kia", credentials), "invalid_auth")
                request.assert_not_awaited()

    async def test_user_flow_errors_do_not_create_entries(self):
        for failure, expected in ((AuthError(), "invalid_auth"),
                                  (ApiError(0, "connection_failed"), "cannot_connect"),
                                  (ApiError(429, "429"), "rate_limited")):
            with self.subTest(expected=expected):
                flow = self.flow()
                with patch.object(flow, "_abort_if_unique_id_configured"), patch.object(PleosClient, "vehicles", AsyncMock(side_effect=failure)):
                    result = await flow.async_step_user(NEW | {"brand": "kia"})
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["step_id"], "user")
                self.assertEqual(result["errors"], {"base": expected})
                self.assertEqual(self.entry.data, OLD)

    async def test_reauth_and_reconfigure_show_forms_without_requests(self):
        for source, step, entry_method in (("reauth", "reauth_confirm", "_get_reauth_entry"),
                                           ("reconfigure", "reconfigure", "_get_reconfigure_entry")):
            with self.subTest(source=source):
                flow = self.flow(source)
                with patch.object(flow, entry_method, return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock()) as request:
                    result = await flow.async_step_reauth(OLD) if source == "reauth" else await flow.async_step_reconfigure()
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["step_id"], step)
                self.assertEqual(result["errors"], {})
                self.assertEqual(result["description_placeholders"]["brand"], "Kia")
                request.assert_not_awaited()

    async def test_replacement_connection_errors_preserve_credentials(self):
        for source in ("reauth", "reconfigure"):
            for status, expected in ((0, "cannot_connect"), (429, "rate_limited")):
                with self.subTest(source=source, status=status):
                    flow = self.flow(source)
                    entry_method = "_get_reauth_entry" if source == "reauth" else "_get_reconfigure_entry"
                    with patch.object(flow, entry_method, return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(side_effect=ApiError(status, "fixture"))), patch.object(self.hass.config_entries, "async_schedule_reload") as reload:
                        result = await flow.async_step_reauth_confirm(NEW) if source == "reauth" else await flow.async_step_reconfigure(NEW)
                    self.assertEqual(result["errors"], {"base": expected})
                    self.assertEqual(self.entry.data, OLD)
                    reload.assert_not_called()

    async def test_options_flow_factory(self):
        self.assertIsInstance(PleosConfigFlow.async_get_options_flow(self.entry), PleosOptionsFlow)

    async def test_coordinator_stops_polling_and_starts_native_reauth(self):
        await self.populate()
        self.client.vehicles.side_effect = AuthError()
        with patch.object(self.entry, "async_start_reauth_if_available") as reauth, patch.object(self.coordinator, "_schedule_refresh") as schedule:
            await self.coordinator.async_refresh()
            reauth.assert_called_once_with(self.hass)
            schedule.assert_not_called()
        self.assertIsInstance(self.coordinator.last_exception, ConfigEntryAuthFailed)
        self.assertEqual(ConnectionSensor(self.coordinator).native_value, "key_required")

    async def test_reauth_validates_and_preserves_entry(self):
        options = {"odometer_km_vehicles": [vehicle_id("kia", CAR["vin"])]}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        flow = self.flow("reauth")
        with patch.object(flow, "_get_reauth_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])), patch.object(self.hass.config_entries, "async_schedule_reload") as reload:
            result = await flow.async_step_reauth_confirm(NEW)
        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(self.entry.data, OLD | NEW)
        self.assertEqual(self.entry.unique_id, credential_id("kia", NEW["client_id"]))
        self.assertEqual(account_id(self.entry), "kia")
        self.assertEqual(self.entry.options, options)
        reload.assert_called_once_with(self.entry.entry_id)

    async def test_rejected_replacement_keeps_previous_credentials(self):
        flow = self.flow("reauth")
        with patch.object(flow, "_get_reauth_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(side_effect=AuthError())):
            result = await flow.async_step_reauth_confirm(NEW)
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(self.entry.data, OLD)

    async def test_proactive_key_replacement(self):
        options = {"odometer_km_vehicles": [vehicle_id("kia", CAR["vin"])]}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        flow = self.flow("reconfigure")
        with patch.object(flow, "_get_reconfigure_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])), patch.object(self.hass.config_entries, "async_schedule_reload"):
            result = await flow.async_step_reconfigure(NEW)
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(self.entry.data["client_id"], NEW["client_id"])
        self.assertEqual(self.entry.options, options)

    async def test_user_flow_for_all_brands_and_secret_selector(self):
        with patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])):
            for brand in BRANDS:
                flow = self.flow()
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    form = await flow.async_step_user(NEW | {"brand": brand})
                    self.assertEqual(form["step_id"], "odometer")
                    result = await flow.async_step_odometer(form["data_schema"]({}))
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(result["data"]["brand"], brand)
                self.assertEqual(result["options"], {"odometer_km_vehicles": []})
        result = await self.flow().async_step_user()
        selector = next(v for k, v in result["data_schema"].schema.items() if str(k) == "client_secret")
        self.assertEqual(selector.config["type"], "password")

    async def test_setup_correction_applies_only_to_selected_car_on_first_poll(self):
        other = CAR | {"vin": "SYNTHETIC-CAR-002"}
        flow = self.flow()
        with patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR, other])) as request:
            form = await flow.async_step_user(NEW | {"brand": "kia"})
            fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
            choices = fields[0]["selector"]["select"]["options"]
            self.assertEqual({choice["label"] for choice in choices},
                             {"Example EV (AR-001)", "Example EV (AR-002)"})
            selected = next(choice["value"] for choice in choices if choice["label"].endswith("AR-001)"))
            with self.assertRaises(Exception):
                form["data_schema"]({"odometer_km_vehicles": [vehicle_id("another-account", CAR["vin"])]})
            created = await flow.async_step_odometer(form["data_schema"]({"odometer_km_vehicles": [selected]}))
            request.assert_awaited_once()
        new_entry = entry(created["data"])
        self.hass.config_entries._entries[new_entry.entry_id] = new_entry
        self.hass.config_entries.async_update_entry(new_entry, options=created["options"])
        self.assertEqual(selected, vehicle_id(account_id(new_entry), CAR["vin"]))
        self.assertNotIn(CAR["vin"], selected)
        coordinator = PleosCoordinator(self.hass, new_entry, self.client)
        self.addAsyncCleanup(coordinator.async_shutdown)
        self.client.vehicles = AsyncMock(return_value=[CAR, other])
        payload = {"timestamp": stamp(), "odometer": {"value": 5177.5, "unit": "mile"}}
        self.client.get = AsyncMock(side_effect=lambda path: payload if path.endswith("/driving") else None)
        cars = (await coordinator._async_update_data())["cars"]
        for car_id, unit in ((selected, "km"), (vehicle_id(account_id(new_entry), other["vin"]), "mi")):
            readings = cars[car_id]["readings"]
            self.assertEqual(readings["driving_odometer"]["value"], 5177.5)
            self.assertEqual(readings["driving_odometer"]["unit_of_measurement"], unit)
            self.assertEqual(readings["driving_api"]["attributes"]["data"]["odometer"], payload["odometer"])

    async def test_setup_without_vehicles_skips_odometer_step(self):
        with patch.object(PleosClient, "vehicles", AsyncMock(return_value=[])) as request:
            created = await self.flow().async_step_user(NEW | {"brand": "kia"})
        self.assertEqual(created["type"], "create_entry")
        self.assertEqual(created["options"], {"odometer_km_vehicles": []})
        request.assert_awaited_once()

    async def test_options_and_limits(self):
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        form = await flow.async_step_init()
        submitted = form["data_schema"]({"polling": {"poll_minutes": 20, "stale_minutes": 60, "location": False, "reverse_geocode": False, "geocoder_url": "", "geocoder_key": ""},
                                         "trips": {}, "storage": {}})
        self.assertEqual(submitted["polling"]["poll_minutes"], 20)
        # Unchanged fields keep their defaults inside each section.
        self.assertEqual(submitted["trips"], {"trip_poll_minutes": 60, "trip_events": True})
        for invalid in ({"poll_minutes": 1}, {"stale_minutes": 10081}):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                form["data_schema"]({"polling": {"poll_minutes": 15, "stale_minutes": 60, "location": True, "reverse_geocode": False, "geocoder_url": "", "geocoder_key": ""} | invalid,
                                     "trips": {}, "storage": {}})
        self.assertTrue(flow.automatic_reload)
        # The frontend receives two expanded sections with their fields.
        fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
        self.assertEqual([(field["name"], field["type"], field["expanded"]) for field in fields],
                         [("polling", "expandable", True), ("trips", "expandable", True), ("storage", "expandable", True)])
        self.assertEqual([item["name"] for item in fields[0]["schema"]],
                         ["poll_minutes", "stale_minutes", "location", "reverse_geocode", "geocoder_url", "geocoder_key", "odometer_km_vehicles"])

        # Saved options stay flat, so the coordinator and existing entries read them unchanged.
        result = await flow.async_step_init(submitted)
        self.assertEqual(result["data"], {"poll_minutes": 20, "stale_minutes": 60, "location": False, "reverse_geocode": False, "geocoder_url": "", "geocoder_key": "",
                                          "trip_poll_minutes": 60, "trip_events": True, "archive": True,
                                          "odometer_km_vehicles": []})

    async def test_options_form_shows_saved_values(self):
        self.hass.config_entries.async_update_entry(self.entry, options={"poll_minutes": 30, "trip_events": False})
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        defaults = (await flow.async_step_init())["data_schema"]({"polling": {}, "trips": {}, "storage": {}})
        self.assertEqual(defaults, {"polling": {"poll_minutes": 30, "stale_minutes": 60, "location": True, "reverse_geocode": False, "geocoder_url": "", "geocoder_key": "",
                                               "odometer_km_vehicles": []},
                                    "trips": {"trip_poll_minutes": 60, "trip_events": False},
                                    "storage": {"archive": False}})

    async def test_odometer_correction_selects_account_cars_and_preserves_missing_car(self):
        car_id = vehicle_id("kia", CAR["vin"])
        missing_id = vehicle_id("kia", "SYNTHETIC-MISSING-CAR")
        self.hass.config_entries.async_update_entry(self.entry, options={"odometer_km_vehicles": [missing_id]})
        registry = dr.async_get(self.hass)
        device = registry.async_get_or_create(config_entry_id=self.entry.entry_id,
                                              identifiers={(DOMAIN, car_id)}, name="Example EV")
        registry.async_update_device(device.id, name_by_user="Family car")
        registry.async_get_or_create(config_entry_id=self.entry.entry_id,
                                     identifiers={(DOMAIN, "kia_api")}, name="Kia API")
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        form = await flow.async_step_init()
        fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
        field = next(item for item in fields[0]["schema"] if item["name"] == "odometer_km_vehicles")
        choices = field["selector"]["select"]["options"]
        self.assertEqual({item["value"] for item in choices}, {car_id, missing_id})
        self.assertIn({"value": car_id, "label": f"Family car ({car_id[-6:]})"}, choices)
        defaults = form["data_schema"]({"polling": {}, "trips": {}, "storage": {}})
        self.assertEqual(defaults["polling"]["odometer_km_vehicles"], [missing_id])
        with self.assertRaises(Exception):
            form["data_schema"]({"polling": {"odometer_km_vehicles": ["kia_api"]}, "trips": {}, "storage": {}})
        for selected in ([car_id], []):
            submitted = form["data_schema"]({"polling": {"odometer_km_vehicles": selected}, "trips": {}, "storage": {}})
            result = await flow.async_step_init(submitted)
            self.assertEqual(result["data"]["odometer_km_vehicles"], selected)

    async def test_setup_uses_replacement_credentials_and_fresh_token(self):
        self.hass.config_entries.async_update_entry(self.entry, data=OLD | NEW)
        with patch("custom_components.pleos_vehicle_data.async_get_clientsession", return_value=MagicMock()), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[])), patch.object(self.hass.config_entries, "async_forward_entry_setups", AsyncMock()):
            await async_setup_entry(self.hass, self.entry)
        self.assertEqual(self.entry.runtime_data.client.credentials["clientId"], NEW["client_id"])
        self.assertIsInstance(self.entry.runtime_data.client, PleosClient)
        self.assertIsNone(self.entry.runtime_data.client.token)
        await self.entry.runtime_data.async_shutdown()


    async def test_raw_exports_are_opt_in_for_new_entries(self):
        self.hass.config_entries.async_update_entry(self.entry, options={})
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        defaults = (await flow.async_step_init())["data_schema"]({"polling": {}, "trips": {}, "storage": {}})
        self.assertFalse(defaults["trips"]["trip_events"])
        self.assertFalse(defaults["storage"]["archive"])


if __name__ == "__main__":
    unittest.main()
