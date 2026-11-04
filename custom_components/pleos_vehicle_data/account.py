"""Stable account namespaces, independent of replaceable API credentials."""

import hashlib
from collections.abc import Iterable
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .data import vehicle_id
from .models import PleosConfigEntry


def account_id(entry: PleosConfigEntry) -> str:
    # Existing installations keep their device IDs, statistics and archive directory.
    return cast(str, entry.data.get("account_id", entry.data["brand"]))


def credential_id(brand: str, client_id: str) -> str:
    return brand + "_" + hashlib.sha256(client_id.strip().encode()).hexdigest()


def has_known_vehicle(hass: HomeAssistant, entry: PleosConfigEntry, vins: Iterable[str]) -> bool:
    """Check vehicle continuity using registry evidence, including unloaded entries."""
    registered = {
        identifier for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, identifier in device.identifiers if domain == DOMAIN
    }
    return any(vehicle_id(account_id(entry), vin) in registered for vin in vins)
