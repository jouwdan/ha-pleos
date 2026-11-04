"""Retire user polling intervals while retaining account settings and identity."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pleos_vehicle_data import async_migrate_entry
from custom_components.pleos_vehicle_data.const import DOMAIN
from support import OLD


@pytest.mark.parametrize("minor_version", [0, 1])
async def test_setup_migrates_only_polling_options(
    hass: HomeAssistant, pleos_api: dict[str, AsyncMock], minor_version: int,
) -> None:
    """The public setup path preserves credentials, identity and every remaining option."""
    preferences = {
        "stale_minutes": 180,
        "location": False,
        "reverse_geocode": True,
        "geocoder_url": "https://geo.example",
        "geocoder_key": "fixture-geocoder-key",
        "odometer_km_vehicles": ["kia_fixture_car"],
        "trip_events": True,
        "archive": False,
        "unrelated_saved_setting": "keep",
    }
    data = OLD | {"account_id": "kia_fixture_account"}
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, minor_version=minor_version, unique_id="fixture-identity",
        data=data, options=preferences | {"poll_minutes": 5, "trip_poll_minutes": 1440},
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id
    pleos_api["vehicles"].return_value = []

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.entry_id == original_entry_id
    assert entry.unique_id == "fixture-identity"
    # The successful first poll learns vehicle evidence without altering existing data.
    assert entry.data == data | {"known_vehicle_ids": []}
    assert entry.options == preferences
    assert entry.runtime_data.update_interval == timedelta(minutes=15)
    assert entry.runtime_data.trips.interval == timedelta(minutes=60)
    assert hass.services.has_service(DOMAIN, "refresh")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options == preferences


async def test_migration_without_custom_intervals_is_idempotent(hass: HomeAssistant) -> None:
    """Accounts using defaults gain only the minor version, without saved defaults."""
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=1, data=OLD, options={})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.options == {}
    assert entry.minor_version == 2
    with patch.object(hass.config_entries, "async_update_entry") as update:
        assert await async_migrate_entry(hass, entry)
    update.assert_not_called()


@pytest.mark.parametrize(("version", "minor_version"), [(0, 1), (2, 1), (1, 3)])
async def test_unsupported_version_is_not_modified(
    hass: HomeAssistant, version: int, minor_version: int,
) -> None:
    """Unknown configuration formats cannot be silently downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=version, minor_version=minor_version, data=OLD,
        options={"poll_minutes": 5, "trip_poll_minutes": 10, "location": False},
    )
    entry.add_to_hass(hass)
    before = dict(entry.options)
    with patch.object(hass.config_entries, "async_update_entry") as update:
        assert not await async_migrate_entry(hass, entry)
    update.assert_not_called()
    assert entry.options == before
    assert (entry.version, entry.minor_version) == (version, minor_version)
