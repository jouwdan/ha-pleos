"""Translate legacy labels through HA without changing registry identity or choices."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, translation
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data.account import account_id
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from support import CAR, stamp


@pytest.mark.parametrize("legacy_namespace", [False, True])
@pytest.mark.parametrize("language", ["en", "de"])
async def test_legacy_labels_translate_without_changing_registry_choices(
    hass: HomeAssistant, pleos_entry: MockConfigEntry, pleos_api: dict[str, AsyncMock],
    legacy_namespace: bool, language: str,
) -> None:
    """Both old namespaces retain every entity and choice through setup and reload."""
    if legacy_namespace:
        hass.config_entries.async_update_entry(
            pleos_entry, data={key: value for key, value in pleos_entry.data.items() if key != "account_id"},
        )
    hass.config.language = language
    label = "Legacy reading" if language == "en" else "Alte Messung"
    registry = er.async_get(hass)
    car_id = vehicle_id(account_id(pleos_entry), CAR["vin"])
    payload = {"timestamp": stamp(), "charge": {"newNumber": 1, "newFlag": True}}
    existing = {}
    for platform in ("sensor", "binary_sensor"):
        for choice, disabled, hidden, name in (
            ("plain", None, None, None),
            ("renamed", None, er.RegistryEntryHider.USER, "My chosen label"),
            ("userdisabled", er.RegistryEntryDisabler.USER, er.RegistryEntryHider.USER, None),
            ("integrationdisabled", er.RegistryEntryDisabler.INTEGRATION, er.RegistryEntryHider.INTEGRATION, None),
        ):
            field = platform + "_" + choice
            key = "batteries_charge_" + field
            payload["charge"][field] = {"value": 20, "unit": "C"} if platform == "sensor" else True
            before = registry.async_get_or_create(
                platform, DOMAIN, car_id + "_" + key, config_entry=pleos_entry,
                suggested_object_id="preserved_legacy_" + field, has_entity_name=True,
                original_name="Old generated English label", disabled_by=disabled, hidden_by=hidden,
            )
            registry.async_update_entity(before.entity_id, name=name, icon="mdi:car", labels={"my-label"})
            if platform == "sensor":
                registry.async_update_entity_options(before.entity_id, "sensor", {
                    "unit_of_measurement": "°F", "suggested_display_precision": 1, "display_precision": 2,
                })
            existing[key] = registry.async_get(before.entity_id)

    original_translations = translation.async_get_translations

    async def translated_names(hass, language, category, integrations=None, config_flow=None):
        result = await original_translations(hass, language, category, integrations, config_flow)
        if language == "de" and category == "entity":
            result |= {f"component.{DOMAIN}.entity.{platform}.legacy_reading.name": "Alte Messung ({field})"
                       for platform in ("sensor", "binary_sensor")}
        return result

    pleos_api["get"].side_effect = lambda path: payload if path.endswith("/batteries") else None
    with patch("homeassistant.helpers.translation.async_get_translations", side_effect=translated_names):
        for reload in (False, True):
            if reload:
                assert await hass.config_entries.async_reload(pleos_entry.entry_id)
            else:
                assert await hass.config_entries.async_setup(pleos_entry.entry_id)
            await hass.async_block_till_done()
            for key, before in existing.items():
                after = registry.async_get(before.entity_id)
                assert (after.id, after.entity_id, after.unique_id, after.config_entry_id) == (
                    before.id, before.entity_id, before.unique_id, before.config_entry_id,
                )
                assert (after.name, after.disabled_by, after.hidden_by, after.options, after.icon, after.labels) == (
                    before.name, before.disabled_by, before.hidden_by, before.options, before.icon, before.labels,
                )
                assert after.translation_key == "legacy_reading"
                state = hass.states.get(before.entity_id)
                if before.disabled_by is not None:
                    assert state is None
                    continue
                assert state is not None
                assert state.name.endswith(before.name or f"{label} ({key})")
                assert "Old generated English label" not in state.name
                if before.domain == "sensor":
                    assert float(state.state) == 68
                    assert state.attributes["unit_of_measurement"] == "°F"
                else:
                    assert state.state == "on"
            assert registry.async_get_entity_id("sensor", DOMAIN, car_id + "_batteries_charge_newnumber") is None
            assert registry.async_get_entity_id("binary_sensor", DOMAIN, car_id + "_batteries_charge_newflag") is None
