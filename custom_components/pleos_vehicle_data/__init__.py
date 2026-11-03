"""Pleos Vehicle Data native Home Assistant integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from pleos_client import PleosClient
from .archive import archive_store
from .const import DOMAIN
from .coordinator import PleosCoordinator
from .economy import economy_store
from .models import PleosConfigEntry
from .ranges import range_store
from .services import async_setup_services
from .statistics import TripStatistics
from .trips import fired_store, trip_store, window_store

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.EVENT]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PleosConfigEntry) -> bool:
    coordinator = PleosCoordinator(hass, entry, PleosClient(
        async_get_clientsession(hass), entry.data["brand"], entry.data["client_id"], entry.data["client_secret"],
    ))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PleosConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PleosConfigEntry) -> None:
    # Forget derived readings, cached ranges and trip tracking. The archive files themselves are kept.
    await trip_store(hass, entry.entry_id).async_remove()
    await window_store(hass, entry.entry_id).async_remove()
    await fired_store(hass, entry.entry_id).async_remove()
    await TripStatistics.async_clear(hass, entry.entry_id)
    await archive_store(hass, entry.entry_id).async_remove()
    await range_store(hass, entry.entry_id).async_remove()
    await economy_store(hass, entry.entry_id).async_remove()
