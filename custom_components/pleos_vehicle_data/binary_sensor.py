"""Boolean vehicle readings."""

from typing import cast

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PleosCoordinator
from .entity import VehicleEntity, discover
from .models import PleosConfigEntry, Reading

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    discover(entry, async_add_entities, "binary_sensor", ReadingBinarySensor)


class ReadingBinarySensor(VehicleEntity, BinarySensorEntity):
    platform_domain = "binary_sensor"
    def __init__(self, coordinator: PleosCoordinator, car_id: str, key: str, spec: Reading) -> None:
        super().__init__(coordinator, car_id, key, spec)
        self._attr_device_class = cast(BinarySensorDeviceClass | None, spec.get("device_class"))

    @property
    def is_on(self) -> bool | None:
        reading = self.reading
        value = reading["value"] if reading else None
        return value == "ON" if value is not None else None
