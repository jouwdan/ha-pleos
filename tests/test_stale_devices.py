"""Only an explicit user action can remove a confirmed departed vehicle."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pleos_client import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data import async_remove_config_entry_device
from custom_components.pleos_vehicle_data.account import account_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR


@pytest.mark.parametrize("reload_entry", [False, True])
async def test_user_can_remove_departed_vehicle_through_home_assistant(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
    hass_ws_client, reload_entry: bool,
) -> None:
    """A valid empty list permits deletion, also when the car left while unloaded."""
    assert await async_setup_component(hass, "config", {})
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    vehicle = registry.async_get_device_by_identifier((DOMAIN, car_id), pleos_entry.entry_id)
    account = registry.async_get_device_by_identifier(
        (DOMAIN, account_id(pleos_entry) + "_api"), pleos_entry.entry_id,
    )
    assert vehicle and account
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "config/device_registry/remove", "device_id": vehicle.id})
    assert not (await client.receive_json())["success"]
    assert registry.async_get(vehicle.id)

    pleos_api["vehicles"].return_value = []
    if reload_entry:
        assert await hass.config_entries.async_reload(pleos_entry.entry_id)
    else:
        await pleos_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    # A successful omission makes removal possible; it never deletes user entities automatically.
    assert registry.async_get(vehicle.id)
    assert er.async_entries_for_device(entities, vehicle.id)
    await client.send_json({"id": 2, "type": "config/device_registry/remove", "device_id": vehicle.id})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert registry.async_get(vehicle.id) is None
    assert not er.async_entries_for_device(entities, vehicle.id)
    assert registry.async_get(account.id)
    assert not await async_remove_config_entry_device(hass, pleos_entry, account)


@pytest.mark.parametrize("error", [
    ApiError(0, "connection_failed"), ApiError(429, "rate_limited"),
    ApiError(500, "server_error"), ApiError(200, "invalid_vehicle_list"), AuthError(),
])
async def test_failed_list_never_authorizes_stale_device_removal(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock], error: Exception,
) -> None:
    """Neither a retained last response nor an earlier omission is deletion evidence after failure."""
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    vehicle = registry.async_get_device_by_identifier((DOMAIN, car_id), pleos_entry.entry_id)
    pleos_api["vehicles"].return_value = []
    await pleos_entry.runtime_data.async_refresh()
    assert await async_remove_config_entry_device(hass, pleos_entry, vehicle)
    pleos_api["vehicles"].side_effect = error
    await pleos_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert not await async_remove_config_entry_device(hass, pleos_entry, vehicle)
    assert registry.async_get(vehicle.id)


async def test_unloaded_unknown_and_other_account_devices_are_protected(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
) -> None:
    registry = dr.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    vehicle = registry.async_get_or_create(config_entry_id=pleos_entry.entry_id, identifiers={(DOMAIN, car_id)})
    assert not await async_remove_config_entry_device(hass, pleos_entry, vehicle)
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    other = MockConfigEntry(domain=DOMAIN, data={"brand": "kia", "account_id": "kia_other"})
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create(config_entry_id=other.entry_id, identifiers={(DOMAIN, "kia_other_car")})
    assert not await async_remove_config_entry_device(hass, pleos_entry, foreign)
    for identifiers in ({("another_domain", "fixture")}, {(DOMAIN, "kia_other_car")}):
        unknown = registry.async_get_or_create(config_entry_id=pleos_entry.entry_id, identifiers=identifiers)
        assert not await async_remove_config_entry_device(hass, pleos_entry, unknown)
    pleos_entry.runtime_data.data = None
    assert not await async_remove_config_entry_device(hass, pleos_entry, vehicle)
