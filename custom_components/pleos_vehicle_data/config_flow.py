"""Guided setup and replacement of manually renewed personal API keys."""

from uuid import uuid4
from collections.abc import Mapping
from typing import Any, Self

import probatio as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode,
    TextSelector, TextSelectorConfig, TextSelectorType,
)

from .account import account_id, credential_id, has_known_vehicle, vehicle_ids
from pleos_client import ApiError, AuthError, PleosClient
from .const import BRANDS, DEFAULT_OPTIONS, DOMAIN
from .models import PleosConfigEntry
from .data import vehicle_id

KEY_FIELDS = {
    vol.Required("client_id"): TextSelector(),
    vol.Required("client_secret"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
}
PLEOS_URL = "https://pleos.ai/"
KEY_GUIDE_URL = "https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/request-my-vehicle-api-key"


class PleosConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2
    _validated_vehicles: frozenset[str] = frozenset()
    _setup_data: dict[str, Any]
    _replacement_entry: PleosConfigEntry
    _replacement_data: dict[str, str]
    _previous_credentials: tuple[str, str]

    async def validate(self, brand: str, user_input: dict[str, Any]) -> str | None:
        self._validated_vehicles = frozenset()
        credentials = {key: user_input[key].strip() for key in ("client_id", "client_secret")}
        if not all(credentials.values()):
            return "invalid_auth"
        try:
            self._vehicles = await PleosClient(async_get_clientsession(self.hass), brand, **credentials).vehicles()
        except AuthError:
            return "invalid_auth"
        except ApiError as error:
            return "rate_limited" if error.status == 429 else "cannot_connect"
        # Transient vehicle fingerprints also protect concurrent flows before devices exist.
        self._validated_vehicles = frozenset(vehicle_id(brand, car["vin"]) for car in self._vehicles)
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input:
            brand = user_input["brand"]
            if self.key_configured(brand, user_input["client_id"]):
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(credential_id(brand, user_input["client_id"]))
            self._abort_if_unique_id_configured()
            if error := await self.validate(brand, user_input):
                errors["base"] = error
            else:
                self._setup_data = {key: value.strip() for key, value in user_input.items()}
                self._setup_data["account_id"] = brand + "_" + uuid4().hex
                self._setup_data["known_vehicle_ids"] = vehicle_ids(
                    self._setup_data["account_id"], (car["vin"] for car in self._vehicles),
                )
                return await self.async_step_odometer()
        schema = vol.Schema({
            vol.Required("brand"): SelectSelector(SelectSelectorConfig(
                options=[SelectOptionDict(value=key, label=name) for key, name in BRANDS.items()],
                mode=SelectSelectorMode.DROPDOWN,
            )), **KEY_FIELDS,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors,
                                    description_placeholders={"pleos_url": PLEOS_URL, "key_guide_url": KEY_GUIDE_URL})

    async def async_step_odometer(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._abort_if_unique_id_configured()
        if reason := self.vehicle_conflict(self._setup_data["brand"]):
            return self.async_abort(reason=reason)
        if user_input is None and self._vehicles:
            vehicles = {
                vehicle_id(self._setup_data["account_id"], car["vin"]):
                    f"{car.get('nickname') or car.get('sellname') or car.get('name') or 'Vehicle'} ({car['vin'][-6:]})"
                for car in self._vehicles
            }
            return self.async_show_form(step_id="odometer", data_schema=vol.Schema({
                vol.Required("odometer_km_vehicles", default=[]): odometer_selector(vehicles),
            }))
        titles = {entry.title for entry in self._async_current_entries()}
        brand = self._setup_data["brand"]
        title, number = BRANDS[brand], 1
        while title in titles:
            number += 1
            title = f"{BRANDS[brand]} {number}"
        return self.async_create_entry(title=title, data=self._setup_data,
                                       options=user_input or {"odometer_km_vehicles": []})

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.replace_key("reauth_confirm", self._get_reauth_entry(), user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.replace_key("reconfigure", self._get_reconfigure_entry(), user_input)

    async def replace_key(
        self, step: str, entry: PleosConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input:
            self._previous_credentials = (entry.data["client_id"], entry.data["client_secret"])
            if self.key_configured(entry.data["brand"], user_input["client_id"], entry.entry_id):
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(credential_id(entry.data["brand"], user_input["client_id"]))
            if error := await self.validate(entry.data["brand"], user_input):
                errors["base"] = error
            else:
                if self.key_configured(entry.data["brand"], user_input["client_id"], entry.entry_id):
                    return self.async_abort(reason="already_configured")
                if reason := self.vehicle_conflict(entry.data["brand"], entry.entry_id):
                    return self.async_abort(reason=reason)
                self._replacement_entry = entry
                self._replacement_data = {key: user_input[key].strip() for key in ("client_id", "client_secret")}
                if has_known_vehicle(self.hass, entry, (car["vin"] for car in self._vehicles)):
                    return self.finish_replacement()
                return await self.async_step_confirm_account()
        return self.async_show_form(step_id=step, data_schema=vol.Schema(KEY_FIELDS), errors=errors,
                                    description_placeholders={"brand": BRANDS[entry.data["brand"]], "pleos_url": PLEOS_URL})

    async def async_step_confirm_account(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input and user_input["confirm_same_account"]:
            return self.finish_replacement()
        return self.async_show_form(
            step_id="confirm_account",
            data_schema=vol.Schema({vol.Required("confirm_same_account", default=False): BooleanSelector()}),
            errors={"base": "confirmation_required"} if user_input is not None else {},
            description_placeholders={"brand": BRANDS[self._replacement_entry.data["brand"]],
                                      "title": self._replacement_entry.title},
        )

    def finish_replacement(self) -> ConfigFlowResult:
        entry = self._replacement_entry
        if (entry.data["client_id"], entry.data["client_secret"]) != self._previous_credentials:
            return self.async_abort(reason="credentials_changed")
        if self.key_configured(entry.data["brand"], self._replacement_data["client_id"], entry.entry_id):
            return self.async_abort(reason="already_configured")
        if reason := self.vehicle_conflict(entry.data["brand"], entry.entry_id):
            return self.async_abort(reason=reason)
        return self.async_update_reload_and_abort(
            entry, unique_id=credential_id(entry.data["brand"], self._replacement_data["client_id"]),
            data_updates=self._replacement_data | {
                "known_vehicle_ids": vehicle_ids(account_id(entry), (car["vin"] for car in self._vehicles)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: PleosConfigEntry) -> "PleosOptionsFlow":
        return PleosOptionsFlow()

    def key_configured(self, brand: str, client_id: str, exclude: str | None = None) -> bool:
        return any(entry.entry_id != exclude and entry.data.get("brand") == brand
                   and entry.data.get("client_id") == client_id.strip() for entry in self._async_current_entries())

    def vehicle_conflict(self, brand: str, exclude: str | None = None) -> str | None:
        if any(entry.entry_id != exclude and entry.data.get("brand") == brand
               and has_known_vehicle(self.hass, entry, (car["vin"] for car in self._vehicles))
               for entry in self._async_current_entries()):
            return "vehicle_already_configured"
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return "already_in_progress"
        return None

    def is_matching(self, other_flow: Self) -> bool:
        return bool(self._validated_vehicles & other_flow._validated_vehicles)


# Options shown together in the form; entry options stay flat.
OPTION_SECTIONS = {"polling": ("stale_minutes", "location", "reverse_geocode", "geocoder_url", "geocoder_key", "odometer_km_vehicles"),
                   "trips": ("trip_events",),
                   "storage": ("archive",)}


def minutes(maximum: float) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=5, max=maximum, step=1, mode=NumberSelectorMode.BOX,
                                               unit_of_measurement="min"))


def odometer_selector(vehicles: Mapping[str, str]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(
        options=[SelectOptionDict(value=car_id, label=name) for car_id, name in sorted(vehicles.items())],
        multiple=True, mode=SelectSelectorMode.DROPDOWN,
    ))


class PleosOptionsFlow(config_entries.OptionsFlowWithReload):
    config_entry: PleosConfigEntry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={
                key: value for fields in user_input.values() for key, value in fields.items()})
        options: dict[str, Any] = DEFAULT_OPTIONS | dict(self.config_entry.options)
        # Retain saved selections when a car is temporarily missing or the entry is unloaded.
        vehicles = {car_id: car_id for car_id in options["odometer_km_vehicles"]}
        for device in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.config_entry.entry_id):
            for domain, car_id in device.identifiers:
                if domain == DOMAIN and car_id != account_id(self.config_entry) + "_api":
                    name = device.name_by_user or device.name or car_id
                    vehicles[car_id] = f"{name} ({car_id[-6:]})"
        selectors = {"stale_minutes": minutes(10080), "location": BooleanSelector(), "reverse_geocode": BooleanSelector(),
                     "geocoder_url": TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                     "geocoder_key": TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                     "trip_events": BooleanSelector(),
                     "archive": BooleanSelector(),
                     "odometer_km_vehicles": odometer_selector(vehicles)}
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(name): section(vol.Schema({
                (vol.Optional if key in ("geocoder_url", "geocoder_key") else vol.Required)(
                    key, default=options[key]): selectors[key] for key in keys
            }), {"collapsed": False})
            for name, keys in OPTION_SECTIONS.items()
        }))
