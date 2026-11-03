"""Real platform setup, reload and removal through Home Assistant."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pleos_client import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.pleos_vehicle_data.account import account_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.economy import economy_store, window_totals
from custom_components.pleos_vehicle_data.ranges import range_store
from custom_components.pleos_vehicle_data.trips import fired_store
from support import CAR, stamp


async def test_setup_reload_unload_preserve_entities_and_customizations(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
) -> None:
    """All platforms load and reload without duplicating registry entries."""
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert pleos_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "refresh")
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, pleos_entry.entry_id)
    assert {entry.domain for entry in entries} == {"sensor", "binary_sensor", "device_tracker", "event"}
    before = {entry.unique_id: entry.entity_id for entry in entries}
    device_ids = {device.id for device in dr.async_entries_for_config_entry(devices, pleos_entry.entry_id)}
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    odometer = before[car_id + "_driving_odometer"]
    registry.async_update_entity(odometer, name="My mileage")
    registry.async_update_entity_options(odometer, "sensor", {"unit_of_measurement": "mi"})
    tracker = hass.states.get(before[car_id + "_location"])
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.attributes["latitude"] == 53.3
    assert tracker.attributes["longitude"] == -6.2
    options = dict(pleos_entry.options)
    old_coordinator = pleos_entry.runtime_data

    assert await hass.config_entries.async_reload(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert old_coordinator is not pleos_entry.runtime_data
    assert {entry.unique_id: entry.entity_id for entry in er.async_entries_for_config_entry(registry, pleos_entry.entry_id)} == before
    assert {device.id for device in dr.async_entries_for_config_entry(devices, pleos_entry.entry_id)} == device_ids
    assert registry.async_get(odometer).name == "My mileage"
    assert hass.states.get(odometer).attributes["unit_of_measurement"] == "mi"
    assert pleos_entry.options == options
    old_coordinator.async_set_updated_data({"status": "no_vehicles", "cars": {}})
    await hass.async_block_till_done()
    assert hass.states.get(odometer).state not in {"unknown", "unavailable"}
    assert await hass.config_entries.async_unload(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert pleos_entry.state is ConfigEntryState.NOT_LOADED
    assert all(hass.states.get(entity_id).state == "unavailable" for entity_id in before.values())
    pleos_api["vehicles"].reset_mock()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
    await hass.async_block_till_done()
    pleos_api["vehicles"].assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "state"),
    [(ApiError(0, "connection_failed"), ConfigEntryState.SETUP_RETRY),
     (ApiError(429, "rate_limited"), ConfigEntryState.SETUP_RETRY),
     (AuthError(), ConfigEntryState.SETUP_ERROR)],
)
async def test_first_refresh_failures_do_not_create_vehicle_entities(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
    failure: Exception, state: ConfigEntryState,
) -> None:
    """HA receives the correct startup failure and can load the same entry later."""
    pleos_api["vehicles"].side_effect = failure
    assert not await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert pleos_entry.state is state
    assert not er.async_entries_for_config_entry(er.async_get(hass), pleos_entry.entry_id)
    pleos_api["vehicles"].side_effect = None
    assert await hass.config_entries.async_reload(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert pleos_entry.state is ConfigEntryState.LOADED


async def test_remove_entry_cleans_owned_stores_and_registries(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
) -> None:
    """The public removal path deletes caches belonging to the removed account."""
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    await range_store(hass, pleos_entry.entry_id).async_save({"fixture": {}})
    await economy_store(hass, pleos_entry.entry_id).async_save({"fixture": {}})
    assert await hass.config_entries.async_remove(pleos_entry.entry_id) == {"require_restart": False}
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(pleos_entry.entry_id) is None
    assert not er.async_entries_for_config_entry(er.async_get(hass), pleos_entry.entry_id)
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), pleos_entry.entry_id)
    assert await range_store(hass, pleos_entry.entry_id).async_load() is None
    assert await economy_store(hass, pleos_entry.entry_id).async_load() is None


@pytest.mark.parametrize("legacy", [False, True])
async def test_existing_account_storage_survives_setup_and_reload(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock], legacy: bool,
) -> None:
    """Both namespace generations retain ranges, trip checkpoints and economy totals."""
    if legacy:
        hass.config_entries.async_update_entry(
            pleos_entry, data={key: value for key, value in pleos_entry.data.items() if key != "account_id"},
            unique_id="kia",
        )
    namespace = account_id(pleos_entry)
    car_id = vehicle_id(namespace, CAR["vin"])
    window = {
        "start": {"reported_at": stamp(60), "km": 1000, "soc": 80, "capacity": 71.2},
        "last": {"reported_at": stamp(30), "km": 1050, "soc": 70, "capacity": 71.2},
    }
    totals = window_totals(window)
    ranges = {car_id: {"powertrains_distancetoempties_EV": {
        "value": 300, "unit": "km", "name": "Electric range", "reported_at": stamp(30),
    }}}
    fired = {car_id: {"driving/data": [stamp(30)]}}
    await range_store(hass, pleos_entry.entry_id).async_save(ranges)
    await economy_store(hass, pleos_entry.entry_id).async_save({car_id: window | {"estimate": window, "totals": totals}})
    await fired_store(hass, pleos_entry.entry_id).async_save(fired)
    for _ in range(2):
        assert await hass.config_entries.async_setup(pleos_entry.entry_id)
        await hass.async_block_till_done()
        assert account_id(pleos_entry) == namespace
        assert await range_store(hass, pleos_entry.entry_id).async_load() == ranges
        saved = await economy_store(hass, pleos_entry.entry_id).async_load()
        assert saved[car_id]["totals"] == totals
        assert saved[car_id]["estimate"] == window
        assert await fired_store(hass, pleos_entry.entry_id).async_load() == fired
        assert await hass.config_entries.async_unload(pleos_entry.entry_id)


@pytest.mark.parametrize("deferred", [False, True])
async def test_unload_cancels_an_inflight_manual_poll(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
    deferred: bool,
) -> None:
    """An action ends cleanly and its network request cannot outlive its entry."""
    pleos_api["vehicles"].return_value = []
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    if deferred:
        pleos_entry.runtime_data._debounced_refresh.cooldown = 0.01
        await hass.services.async_call(
            DOMAIN, "refresh", {"config_entry_id": pleos_entry.entry_id}, blocking=True,
        )
    started, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_request() -> list[dict]:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return []

    pleos_api["vehicles"].side_effect = slow_request
    action = asyncio.create_task(hass.services.async_call(
        DOMAIN, "refresh", {"config_entry_id": pleos_entry.entry_id}, blocking=True,
    ))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await hass.config_entries.async_unload(pleos_entry.entry_id)
        with pytest.raises(HomeAssistantError) as raised:
            await asyncio.wait_for(action, 1)
        assert raised.value.translation_key == "refresh_cancelled"
        assert cancelled.is_set()
    finally:
        release.set()
        await asyncio.gather(action, return_exceptions=True)
        await hass.async_block_till_done()
