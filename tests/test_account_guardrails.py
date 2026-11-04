"""Vehicle continuity guards use registry evidence without claiming account identity."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import account_id, credential_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR, NEW, OLD


def add_vehicle(registry: dr.DeviceRegistry, entry: MockConfigEntry, vin: str = CAR["vin"]) -> None:
    """Register evidence in the entry's existing namespace, without storing the VIN."""
    registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, vehicle_id(account_id(entry), vin))},
    )


def add_account(hass: HomeAssistant, client_id: str = "fixture-other-id") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Other Kia", unique_id=credential_id("kia", client_id),
        data=OLD | {"client_id": client_id, "account_id": "kia_" + client_id},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("loaded", [False, True])
async def test_another_key_cannot_duplicate_registered_vehicle(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry, loaded: bool,
) -> None:
    """An account's saved devices protect it even while it is unloaded."""
    if loaded:
        assert await hass.config_entries.async_setup(pleos_entry.entry_id)
        await hass.async_block_till_done()
    else:
        add_vehicle(device_registry, pleos_entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": "kia"},
    )
    assert result["reason"] == "vehicle_already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [pleos_entry]


@pytest.mark.parametrize(("brand", "vehicles"), [("hyundai", [CAR]), ("kia", [CAR | {"vin": "NEW-CAR"}]), ("kia", [])])
async def test_other_vehicles_brands_and_empty_accounts_remain_supported(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry, brand: str, vehicles: list[dict],
) -> None:
    add_vehicle(device_registry, pleos_entry)
    pleos_api["vehicles"].return_value = vehicles
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": brand},
    )
    if vehicles:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"odometer_km_vehicles": []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == credential_id(brand, NEW["client_id"])
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_setup_rechecks_registered_vehicles_after_calibration(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], device_registry: dr.DeviceRegistry,
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    add_vehicle(device_registry, add_account(hass))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"odometer_km_vehicles": []})
    assert result["reason"] == "vehicle_already_configured"


async def test_setup_overlap_is_blocked_while_first_entry_waits_for_devices(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], device_registry: dr.DeviceRegistry,
) -> None:
    """HA keeps the creating flow alive while its initial setup awaits the API."""
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=OLD)
    setup_started, release_setup = asyncio.Event(), asyncio.Event()

    async def wait_for_setup() -> list[dict]:
        setup_started.set()
        await release_setup.wait()
        return [CAR]

    pleos_api["vehicles"].side_effect = wait_for_setup
    creating = hass.async_create_task(hass.config_entries.flow.async_configure(
        first["flow_id"], {"odometer_km_vehicles": []},
    ))
    try:
        await asyncio.wait_for(setup_started.wait(), 2)
        assert not device_registry.devices
        pleos_api["vehicles"].side_effect = None
        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=NEW | {"brand": "kia"},
        )
        assert second["reason"] == "already_in_progress"
    finally:
        release_setup.set()
        result = await creating
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == credential_id("kia", OLD["client_id"])
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize("source", [SOURCE_REAUTH, SOURCE_RECONFIGURE])
async def test_replacement_rejects_any_vehicle_registered_to_another_entry(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry, source: str,
) -> None:
    add_vehicle(device_registry, pleos_entry, "OWN-CAR")
    add_vehicle(device_registry, add_account(hass))
    original = dict(pleos_entry.data)
    pleos_api["vehicles"].return_value = [CAR, CAR | {"vin": "OWN-CAR"}]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": pleos_entry.entry_id},
        data=original if source == SOURCE_REAUTH else None,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    assert result["reason"] == "vehicle_already_configured"
    assert pleos_entry.data == original


@pytest.mark.parametrize("source", [SOURCE_REAUTH, SOURCE_RECONFIGURE])
@pytest.mark.parametrize("vehicles", [[], [CAR | {"vin": "UNRECOGNIZED-CAR"}]])
async def test_no_overlap_replacement_requires_explicit_confirmation(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry, source: str, vehicles: list[dict],
) -> None:
    add_vehicle(device_registry, pleos_entry)
    original, options = dict(pleos_entry.data), dict(pleos_entry.options)
    pleos_api["vehicles"].return_value = vehicles
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": pleos_entry.entry_id},
        data=original if source == SOURCE_REAUTH else None,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    assert result["step_id"] == "confirm_account"
    assert result["data_schema"]({}) == {"confirm_same_account": False}
    assert pleos_entry.data == original
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "confirmation_required"}
    assert pleos_entry.data == original
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm_same_account": True})
    assert result["reason"] == f"{source}_successful"
    assert pleos_entry.data == original | NEW
    assert pleos_entry.options == options
    assert pleos_entry.unique_id == credential_id("kia", NEW["client_id"])
    reload.assert_called_once_with(pleos_entry.entry_id)


@pytest.mark.parametrize("conflict", ["vehicle", "key", "credentials_changed"])
async def test_confirmation_rechecks_changes_since_validation(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry, conflict: str,
) -> None:
    original = dict(pleos_entry.data)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": pleos_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    assert result["step_id"] == "confirm_account"
    if conflict == "vehicle":
        add_vehicle(device_registry, add_account(hass))
        reason = "vehicle_already_configured"
    elif conflict == "key":
        add_account(hass, NEW["client_id"])
        reason = "already_configured"
    else:
        original |= {"client_id": "changed-during-confirmation"}
        hass.config_entries.async_update_entry(pleos_entry, data=original)
        reason = "credentials_changed"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm_same_account": True})
    assert result["reason"] == reason
    assert pleos_entry.data == original


async def test_parallel_empty_replacements_cannot_overwrite_a_newer_confirmation(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    pleos_api["vehicles"].return_value = []
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": pleos_entry.entry_id},
    )
    first = await hass.config_entries.flow.async_configure(first["flow_id"], NEW)
    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": pleos_entry.entry_id},
    )
    second = await hass.config_entries.flow.async_configure(second["flow_id"], NEW | {"client_id": "another-key"})
    assert first["step_id"] == second["step_id"] == "confirm_account"
    with patch.object(hass.config_entries, "async_schedule_reload"):
        accepted = await hass.config_entries.flow.async_configure(first["flow_id"], {"confirm_same_account": True})
        rejected = await hass.config_entries.flow.async_configure(second["flow_id"], {"confirm_same_account": True})
    assert accepted["reason"] == "reconfigure_successful"
    assert rejected["reason"] == "credentials_changed"
    assert pleos_entry.data["client_id"] == NEW["client_id"]


async def test_replacement_rechecks_duplicate_key_after_validation(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], pleos_entry: MockConfigEntry,
) -> None:
    original = dict(pleos_entry.data)

    async def another_entry_claims_key() -> list[dict]:
        add_account(hass, NEW["client_id"])
        return [CAR]

    pleos_api["vehicles"].side_effect = another_entry_claims_key
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": pleos_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], NEW)
    assert result["reason"] == "already_configured"
    assert pleos_entry.data == original
