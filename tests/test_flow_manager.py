"""Exercise configuration through Home Assistant's public flow manager."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pleos_client import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import account_id, credential_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR, NEW, OLD


@pytest.mark.parametrize(
    ("failure", "error"),
    [(AuthError(), "invalid_auth"), (ApiError(0, "connection_failed"), "cannot_connect"),
     (ApiError(429, "rate_limited"), "rate_limited")],
)
async def test_user_recovers_in_same_flow(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], failure: Exception, error: str,
) -> None:
    """A failed submission can recover and stores one entry with its unique ID."""
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    pleos_api["vehicles"].side_effect = failure
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], OLD)
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)

    pleos_api["vehicles"].side_effect = None
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], OLD)
    assert result["step_id"] == "odometer"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"odometer_km_vehicles": []})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    created = result["result"]
    assert created.unique_id == credential_id("kia", OLD["client_id"])
    assert {key: created.data[key] for key in OLD} == OLD
    assert hass.config_entries.async_entries(DOMAIN) == [created]


async def test_odometer_setup_applies_before_first_poll(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock],
) -> None:
    """The public setup flow applies selected calibration when platforms first load."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=OLD,
    )
    selector = next(iter(result["data_schema"].schema.values()))
    selected = selector.config["options"][0]["value"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"odometer_km_vehicles": [selected]},
    )
    await hass.async_block_till_done()
    created = result["result"]
    assert selected == vehicle_id(account_id(created), CAR["vin"])
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, selected + "_driving_odometer")
    assert hass.states.get(entity_id).attributes["unit_of_measurement"] == "km"
    assert float(hass.states.get(entity_id).state) == 5177.5


async def test_empty_account_and_duplicate_key(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock],
) -> None:
    """Valid empty accounts skip calibration and cannot be registered twice."""
    pleos_api["vehicles"].return_value = []
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {"odometer_km_vehicles": []}
    duplicate = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    assert duplicate["type"] is FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_duplicate_added_during_odometer_step(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock],
) -> None:
    """Recheck uniqueness after the user spends time on calibration."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    other = MockConfigEntry(
        domain=DOMAIN, unique_id=credential_id("kia", OLD["client_id"]), data=OLD,
    )
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"odometer_km_vehicles": []})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [other]


@pytest.mark.parametrize("source", [SOURCE_REAUTH, SOURCE_RECONFIGURE])
@pytest.mark.parametrize("failure", [AuthError(), ApiError(0, "connection_failed"), ApiError(429, "rate_limited")])
async def test_credential_replacement_recovers_without_changing_identity(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    source: str, failure: Exception,
) -> None:
    """Failed key replacements recover, reload, and preserve account settings."""
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    options = dict(pleos_entry.options) | {"odometer_km_vehicles": [car_id]}
    hass.config_entries.async_update_entry(pleos_entry, options=options)
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    # Initial setup retires only the old polling options; credential replacement keeps the rest.
    options.pop("poll_minutes")
    registry = er.async_get(hass)
    before = {entity.unique_id: entity.entity_id for entity in er.async_entries_for_config_entry(registry, pleos_entry.entry_id)}
    original_data = dict(pleos_entry.data)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": pleos_entry.entry_id},
        data=original_data if source == SOURCE_REAUTH else None,
    )
    pleos_api["vehicles"].side_effect = failure
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    assert result["type"] is FlowResultType.FORM
    assert pleos_entry.data == original_data

    pleos_api["vehicles"].side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    await hass.async_block_till_done()
    assert result["reason"] == f"{source}_successful"
    assert pleos_entry.data == original_data | NEW
    assert pleos_entry.unique_id == credential_id("kia", NEW["client_id"])
    assert pleos_entry.options == options
    assert {entity.unique_id: entity.entity_id for entity in er.async_entries_for_config_entry(registry, pleos_entry.entry_id)} == before
