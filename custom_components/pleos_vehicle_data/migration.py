"""Carry readings that became binary sensors over from their old sensor entities.

0.7.0 and 0.8.0 turned on/off and open/closed readings, such as ignition, bonnet and the
"(last trip)" doors, from sensors into binary sensors with the same unique ID. Home Assistant
registers those as new entities, so customisations, history and references stayed with an old
sensor that is left unavailable.

Before such a binary sensor is registered for the first time, this moves the old sensor's
registry settings and its recorded history to it and removes the old entry. The new entity ID
keeps the old object ID: sensor.ev6_ignition becomes binary_sensor.ev6_ignition. Nothing is
merged when the binary sensor already exists, because both entities then have their own
settings and history.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance

from .const import DOMAIN
from .models import PleosConfigEntry

LOGGER = logging.getLogger(__name__)
# Options other integrations keep per entity, such as voice assistant exposure, move along;
# sensor display options do not apply to a binary sensor.
SENSOR_OPTIONS = ("sensor", "sensor.private")


def migrate_to_binary_sensor(hass: HomeAssistant, entry: PleosConfigEntry, unique_id: str) -> None:
    registry = er.async_get(hass)
    if registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id):
        return
    old_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    old = registry.async_get(old_id) if old_id else None
    if old is None or old.config_entry_id != entry.entry_id:
        return
    object_id = old.entity_id.split(".", 1)[1]
    # The registry adds a suffix if the old object ID is already taken in this domain.
    new = registry.async_get_or_create(
        "binary_sensor", DOMAIN, unique_id, config_entry=entry, suggested_object_id=object_id,
        disabled_by=old.disabled_by, hidden_by=old.hidden_by,
    )
    registry.async_update_entity(
        new.entity_id, aliases=list(old.aliases), area_id=old.area_id, categories=dict(old.categories),
        device_id=old.device_id, icon=old.icon, labels=set(old.labels), name=old.name,
    )
    for domain, options in old.options.items():
        if domain not in SENSOR_OPTIONS:
            registry.async_update_entity_options(new.entity_id, domain, dict(options))
    if "recorder" in hass.config.components:
        # Queued before the old entry is removed and before the new entity records a state, so
        # the recorder renames the old history first. It keeps both apart if the new ID has history.
        get_instance(hass).async_update_states_metadata(old.entity_id, new.entity_id)
    registry.async_remove(old.entity_id)
    LOGGER.info("Moved %s with its settings and history to %s", old.entity_id, new.entity_id)
