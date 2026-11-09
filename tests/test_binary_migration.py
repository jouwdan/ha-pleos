"""Readings that became binary sensors keep their old sensor's settings and history."""

from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util
from pleos_client import PleosClient
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.pleos_vehicle_data.account import account_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR, stamp

PAYLOADS = {
    "driving": {"ignition": {"status": "on"}},
    "status": {"hood": "closed"},
}


def serve(payloads=PAYLOADS):
    timestamp = stamp()
    return (
        patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])),
        patch.object(PleosClient, "get", AsyncMock(
            side_effect=lambda path: payloads[name] | {"timestamp": timestamp}
            if (name := path.rsplit("/", 1)[-1]) in payloads else None)),
    )


def old_sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str, object_id: str, **settings) -> er.RegistryEntry:
    registry = er.async_get(hass)
    car_id = vehicle_id(account_id(entry), CAR["vin"])
    created = registry.async_get_or_create(
        "sensor", DOMAIN, car_id + "_" + key, config_entry=entry, suggested_object_id=object_id)
    return registry.async_update_entity(created.entity_id, **settings) if settings else created


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    vehicles, get = serve()
    with vehicles, get:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_settings_and_history_move_to_the_binary_sensor(
    recorder_mock: Recorder, hass: HomeAssistant, pleos_entry: MockConfigEntry, enable_custom_integrations: None,
) -> None:
    registry = er.async_get(hass)
    garage = ar.async_get(hass).async_create("Garage")
    ignition = old_sensor(hass, pleos_entry, "driving_ignition_status", "my_ignition",
                          name="Engine", icon="mdi:engine", area_id=garage.id, labels={"car"},
                          aliases=["motor"], categories={"helpers": "car"})
    registry.async_update_entity_options(ignition.entity_id, "conversation", {"should_expose": True})
    registry.async_update_entity_options(ignition.entity_id, "sensor", {"display_precision": 1})
    bonnet = old_sensor(hass, pleos_entry, "status_hood", "bonnet", hidden_by=er.RegistryEntryHider.USER)
    start = dt_util.utcnow()
    for state in ("off", "on", "off"):
        hass.states.async_set(ignition.entity_id, state)
    hass.states.async_set(bonnet.entity_id, "closed")
    await async_wait_recording_done(hass)

    await setup(hass, pleos_entry)
    await async_wait_recording_done(hass)

    assert registry.async_get(ignition.entity_id) is None
    assert registry.async_get(bonnet.entity_id) is None
    moved = registry.async_get("binary_sensor.my_ignition")
    assert moved.unique_id == ignition.unique_id
    assert (moved.name, moved.icon, moved.area_id, moved.labels, moved.aliases, moved.categories) == (
        "Engine", "mdi:engine", garage.id, {"car"}, ["motor"], {"helpers": "car"})
    assert moved.options.get("conversation") == {"should_expose": True}
    assert "sensor" not in moved.options
    assert hass.states.get("binary_sensor.my_ignition").state == "on"
    assert registry.async_get("binary_sensor.bonnet").hidden_by is er.RegistryEntryHider.USER

    history = await recorder_mock.async_add_executor_job(
        lambda: get_significant_states(hass, start, None, ["binary_sensor.my_ignition", ignition.entity_id],
                                       significant_changes_only=False))
    assert [state.state for state in history["binary_sensor.my_ignition"]] == ["off", "on", "off", "on"]
    assert ignition.entity_id not in history

    # A reload finds the moved entity registered and changes nothing more.
    entries = {entry.entity_id for entry in er.async_entries_for_config_entry(registry, pleos_entry.entry_id)}
    vehicles, get = serve()
    with vehicles, get:
        assert await hass.config_entries.async_reload(pleos_entry.entry_id)
        await hass.async_block_till_done()
    assert {entry.entity_id for entry in er.async_entries_for_config_entry(registry, pleos_entry.entry_id)} == entries


async def test_existing_binary_sensors_and_other_entries_are_left_alone(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, enable_custom_integrations: None,
) -> None:
    registry = er.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    # Upgraded before this migration existed: both entities have their own settings and history.
    ignition = old_sensor(hass, pleos_entry, "driving_ignition_status", "old_ignition", name="Old")
    upgraded = registry.async_get_or_create("binary_sensor", DOMAIN, ignition.unique_id, config_entry=pleos_entry,
                                            suggested_object_id="new_ignition")
    # The same unique ID under another entry is not this account's sensor.
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create("sensor", DOMAIN, car_id + "_status_hood", config_entry=other,
                                           suggested_object_id="foreign_bonnet")

    await setup(hass, pleos_entry)

    assert registry.async_get(ignition.entity_id).name == "Old"
    assert registry.async_get(upgraded.entity_id).name is None
    assert hass.states.get(upgraded.entity_id).state == "on"
    assert registry.async_get(foreign.entity_id) == foreign
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, car_id + "_status_hood") == "binary_sensor.example_ev_bonnet"


async def test_taken_object_id_gets_a_suffix_without_recorder(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, enable_custom_integrations: None,
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create("binary_sensor", "other", "x", suggested_object_id="bonnet")
    bonnet = old_sensor(hass, pleos_entry, "status_hood", "bonnet", disabled_by=er.RegistryEntryDisabler.USER)

    await setup(hass, pleos_entry)

    assert registry.async_get(bonnet.entity_id) is None
    moved = registry.async_get("binary_sensor.bonnet_2")
    assert moved.unique_id == bonnet.unique_id
    assert moved.disabled_by is er.RegistryEntryDisabler.USER
