"""Boolean vehicle readings."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VehicleEntity, discover
from .models import PleosConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    registry = entity_registry.async_get(hass)
    for car_id, car in entry.runtime_data.data["cars"].items():
        if car["vehicle"].get("type") != "EV":
            continue
        for key in ("status_warning_lowfuel", "status_warning_engineoil"):
            entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, car_id + "_" + key)
            # Disable obsolete warnings on upgrade without deleting IDs, customisations or history.
            if entity_id and registry.async_get(entity_id).disabled_by is None:
                registry.async_update_entity(entity_id, disabled_by=entity_registry.RegistryEntryDisabler.INTEGRATION)
    discover(entry, async_add_entities, "binary_sensor", ReadingBinarySensor)


class ReadingBinarySensor(VehicleEntity, BinarySensorEntity):
    platform_domain = "binary_sensor"
    def __init__(self, coordinator, car_id, key, spec):
        super().__init__(coordinator, car_id, key, spec)
        self._attr_device_class = spec.get("device_class")

    @property
    def is_on(self):
        return self.reading["value"] == "ON" if self.reading else None
