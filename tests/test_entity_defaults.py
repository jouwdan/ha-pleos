"""Supported discovery and first-registration defaults through real HA platforms."""

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import account_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR, stamp


async def test_new_diagnostics_are_disabled_and_existing_choices_survive_reload(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
) -> None:
    registry = er.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    existing = {}
    for unique_id, disabled_by in (
        (car_id + "_batteries_api", None),
        (car_id + "_status_api", er.RegistryEntryDisabler.USER),
        (car_id + "_locations_location_type", er.RegistryEntryDisabler.INTEGRATION),
        (account_id(pleos_entry) + "_last_successful_poll", None),
    ):
        registered = registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=pleos_entry,
            suggested_object_id="existing_" + str(len(existing)), disabled_by=disabled_by,
        )
        existing[unique_id] = registry.async_update_entity(
            registered.entity_id, name="My diagnostic", icon="mdi:car", labels={"my-label"},
        )
    for _ in range(2):
        assert await hass.config_entries.async_setup(pleos_entry.entry_id)
        await hass.async_block_till_done()
        for unique_id, before in existing.items():
            after = registry.async_get(before.entity_id)
            assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) == before.entity_id
            assert (after.disabled_by, after.name, after.icon, after.labels) == (
                before.disabled_by, before.name, before.icon, before.labels,
            )
            assert (hass.states.get(before.entity_id) is not None) == (before.disabled_by is None)
        for key in ("driving_api", "driving_reported_at", "driving_data_age"):
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, car_id + "_" + key)
            assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert hass.states.get(entity_id) is None
        for unique_id in (car_id + "_batteries_charge_stateofcharge", car_id + "_vehicle_information",
                          account_id(pleos_entry) + "_api_status"):
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            assert registry.async_get(entity_id).disabled_by is None
            assert hass.states.get(entity_id) is not None
        assert await hass.config_entries.async_unload(pleos_entry.entry_id)
        await hass.async_block_till_done()


async def test_unsupported_fields_only_restore_entities_owned_by_this_entry(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
) -> None:
    registry = er.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    existing = registry.async_get_or_create(
        "sensor", DOMAIN, car_id + "_batteries_charge_existingfield", config_entry=pleos_entry,
        suggested_object_id="my_existing_field",
    )
    registry.async_update_entity(existing.entity_id, name="My existing reading")
    foreign = MockConfigEntry(domain=DOMAIN, title="Another account")
    foreign.add_to_hass(hass)
    foreign_entity = registry.async_get_or_create(
        "sensor", DOMAIN, car_id + "_batteries_charge_foreignfield", config_entry=foreign,
    )
    payload = {"timestamp": stamp(), "charge": {
        "stateOfCharge": 50, "existingField": 1, "newField": 2, "foreignField": 3,
    }}
    pleos_api["get"].side_effect = lambda path: payload if path.endswith("/batteries") else None
    assert await hass.config_entries.async_setup(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(existing.entity_id).state == "1"
    assert hass.states.get(existing.entity_id).name.endswith("My existing reading")
    assert registry.async_get_entity_id("sensor", DOMAIN, car_id + "_batteries_charge_newfield") is None
    assert registry.async_get(foreign_entity.entity_id).config_entry_id == foreign.entry_id
    assert hass.states.get(foreign_entity.entity_id) is None
    last_poll = registry.async_get_entity_id("sensor", DOMAIN, account_id(pleos_entry) + "_last_successful_poll")
    assert registry.async_get(last_poll).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(last_poll) is None
    # Unknown values remain in the API data; only automatic entity creation is restricted.
    assert pleos_entry.runtime_data.data["cars"][car_id]["readings"]["batteries_charge_newfield"]["value"] == 2

    payload["charge"].update(existingField=4, batteryCapacity=256320, anotherField=True)
    await pleos_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(existing.entity_id).state == "4"
    capacity = registry.async_get_entity_id("sensor", DOMAIN, car_id + "_batteries_charge_batterycapacity")
    assert float(hass.states.get(capacity).state) == 71.2
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, car_id + "_batteries_charge_anotherfield") is None
    assert await hass.config_entries.async_reload(pleos_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(existing.entity_id).name == "My existing reading"
    assert hass.states.get(existing.entity_id).state == "4"
