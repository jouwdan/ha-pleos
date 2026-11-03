"""GPS tracking only when the API supplies valid WGS84 coordinates."""

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VehicleEntity, discover
from .models import PleosConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    discover(entry, async_add_entities, "device_tracker", VehicleTracker)


class VehicleTracker(VehicleEntity, TrackerEntity):
    platform_domain = "device_tracker"
    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        return self.car["location"]["latitude"] if self.car and self.car["location"] else None

    @property
    def longitude(self):
        return self.car["location"]["longitude"] if self.car and self.car["location"] else None
