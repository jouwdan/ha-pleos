"""Persist validated vehicle evidence independently of platform setup and API keys."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pleos_client import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import account_id, has_known_vehicle, vehicle_ids
from custom_components.pleos_vehicle_data.const import DOMAIN
from support import CAR, NEW, OLD


async def test_completed_setup_retains_evidence_when_first_refresh_fails(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], device_registry: dr.DeviceRegistry,
) -> None:
    """A completed flow keeps its VIN evidence when no device was ever created."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    pleos_api["vehicles"].side_effect = ApiError(0, "connection_failed")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"odometer_km_vehicles": []})
    entry = result["result"]
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()
    assert not device_registry.devices
    assert entry.data["known_vehicle_ids"] == vehicle_ids(account_id(entry), [CAR["vin"]])

    pleos_api["vehicles"].side_effect = None
    duplicate = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": "kia"},
    )
    assert duplicate["reason"] == "vehicle_already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch.object(hass.config_entries, "async_schedule_reload"):
        renewal = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        renewal = await hass.config_entries.flow.async_configure(renewal["flow_id"], NEW)
    assert renewal["reason"] == "reconfigure_successful"
    assert entry.data["client_id"] == NEW["client_id"]


async def test_legacy_account_learns_evidence_and_retains_it_while_unloaded(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    original = dict(pleos_entry.data)
    assert "known_vehicle_ids" not in original
    pleos_api["vehicles"].return_value = [CAR, CAR | {"vin": "SECOND-CAR"}, CAR]
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    assert pleos_entry.data == original | {
        "known_vehicle_ids": vehicle_ids(account_id(pleos_entry), [CAR["vin"], "SECOND-CAR"]),
    }
    await hass.async_block_till_done()
    options, unique_id = pleos_entry.options, pleos_entry.unique_id
    assert await hass.config_entries.async_unload(pleos_entry.entry_id)
    assert has_known_vehicle(hass, pleos_entry, [CAR["vin"]])
    assert pleos_entry.options == options
    assert pleos_entry.unique_id == unique_id


@pytest.mark.parametrize("error", [
    ApiError(0, "connection_failed"), ApiError(429, "rate_limited"), ApiError(503, "server_error"),
    ApiError(200, "invalid_vehicle_list"), AuthError(),
])
async def test_failed_vehicle_list_never_erases_saved_evidence(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry, error: Exception,
) -> None:
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    original = dict(pleos_entry.data)
    pleos_api["vehicles"].side_effect = error
    await pleos_entry.runtime_data.async_refresh()
    assert pleos_entry.data == original
    assert has_known_vehicle(hass, pleos_entry, [CAR["vin"]])


async def test_successful_list_is_saved_even_when_a_vehicle_endpoint_fails(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    pleos_api["get"].side_effect = AuthError()
    assert not await hass.config_entries.async_setup(pleos_entry.entry_id)
    assert pleos_entry.state is ConfigEntryState.SETUP_ERROR
    assert pleos_entry.data["known_vehicle_ids"] == vehicle_ids(account_id(pleos_entry), [CAR["vin"]])


async def test_empty_success_clears_metadata_but_device_removal_releases_transfer(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry, hass_ws_client,
) -> None:
    assert await async_setup_component(hass, "config", {})
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    known = pleos_entry.data["known_vehicle_ids"][0]
    device = registry.async_get_device_by_identifier((DOMAIN, known), pleos_entry.entry_id)
    pleos_api["vehicles"].return_value = []
    await pleos_entry.runtime_data.async_refresh()
    assert pleos_entry.data["known_vehicle_ids"] == []
    assert has_known_vehicle(hass, pleos_entry, [CAR["vin"]])
    pleos_api["vehicles"].return_value = [CAR]
    blocked = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": "kia"},
    )
    assert blocked["reason"] == "vehicle_already_configured"
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "config/device_registry/remove", "device_id": device.id})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert not has_known_vehicle(hass, pleos_entry, [CAR["vin"]])
    allowed = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": "kia"},
    )
    assert allowed["type"] is FlowResultType.FORM
    assert allowed["step_id"] == "odometer"


async def test_poll_for_replaced_credentials_cannot_overwrite_new_evidence(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    coordinator = pleos_entry.runtime_data
    started, resume = asyncio.Event(), asyncio.Event()

    async def old_response() -> list[dict]:
        started.set()
        await resume.wait()
        return []

    pleos_api["vehicles"].side_effect = old_response
    poll = hass.async_create_task(coordinator.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 2)
        replacement = dict(pleos_entry.data) | NEW | {
            "known_vehicle_ids": vehicle_ids(account_id(pleos_entry), ["NEW-ACCOUNT-CAR"]),
        }
        hass.config_entries.async_update_entry(pleos_entry, data=replacement)
    finally:
        resume.set()
        await poll
    assert pleos_entry.data == replacement


async def test_unchanged_vehicle_list_does_not_write_config_entry_again(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    pleos_api["vehicles"].return_value = [CAR, CAR]
    with patch.object(hass.config_entries, "async_update_entry", wraps=hass.config_entries.async_update_entry) as update:
        await pleos_entry.runtime_data.async_refresh()
    update.assert_not_called()


@pytest.mark.parametrize("invalid", ["not-a-list", [None, 123, "foreign_vehicle"]])
async def test_invalid_persisted_evidence_does_not_match_vehicles(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, device_registry: dr.DeviceRegistry, invalid: object,
) -> None:
    hass.config_entries.async_update_entry(pleos_entry, data=dict(pleos_entry.data) | {"known_vehicle_ids": invalid})
    assert not has_known_vehicle(hass, pleos_entry, [CAR["vin"]])
