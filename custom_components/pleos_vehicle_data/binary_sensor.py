"""Boolean vehicle readings."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VehicleEntity, discover
from .models import PleosConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    discover(entry, async_add_entities, "binary_sensor", ReadingBinarySensor)


class ReadingBinarySensor(VehicleEntity, BinarySensorEntity):
    platform_domain = "binary_sensor"
    def __init__(self, coordinator, car_id, key, spec):
        super().__init__(coordinator, car_id, key, spec)
        self._attr_device_class = spec.get("device_class")

    @property
    def is_on(self):
        value = self.reading["value"] if self.reading else None
        return value == "ON" if value is not None else None
