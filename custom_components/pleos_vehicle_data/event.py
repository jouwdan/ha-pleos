"""One Trip event entity per car, fired once for every trip the API returns.

The "(last trip)" sensors show the latest trip only. This entity records each trip as an
event, oldest first, with a summary in its attributes, so Home Assistant's history and its
InfluxDB integration hold every trip, including several that arrive in one query.
"""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRANDS, DOMAIN, TRIP_ENDPOINTS
from .data import identifier
from .models import PleosConfigEntry
from .names import ENTITY_NAMES

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    added = set()

    def add_new():
        new = [car_id for car_id in (coordinator.data or {}).get("cars", {}) if car_id not in added]
        added.update(new)
        if new:
            async_add_entities([TripEvent(coordinator, car_id) for car_id in new])

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class TripEvent(CoordinatorEntity, EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "trip"
    _attr_event_types = [identifier(endpoint) for endpoint in TRIP_ENDPOINTS]
    # The entity ID stays English, like every other entity of this integration.
    suggested_object_id = ENTITY_NAMES["event"]["trip"]

    def __init__(self, coordinator, car_id):
        super().__init__(coordinator)
        self.car_id = car_id
        self._attr_unique_id = car_id + "_trip"
        vehicle = coordinator.data["cars"][car_id]["vehicle"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, car_id)},
            name=vehicle.get("nickname") or vehicle.get("sellname") or BRANDS[coordinator.brand] + " vehicle",
            manufacturer=BRANDS[coordinator.brand], model=vehicle.get("sellname") or vehicle.get("name") or "Vehicle",
        )

    @property
    def available(self):
        return super().available and self.car_id in ((self.coordinator.data or {}).get("cars") or {})

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Trips found before this entity existed (first setup, a new car) are fired once it is
        # fully added; state written while it is still being added is not recorded.
        self.hass.async_create_task(self._async_fire_queued(), eager_start=False)

    async def _async_fire_queued(self):
        self._fire_queued()

    def _handle_coordinator_update(self):
        self._fire_queued()
        super()._handle_coordinator_update()

    def _fire_queued(self):
        for event_type, attributes in self.coordinator.trip_queue.pop(self.car_id, []):
            self._trigger_event(event_type, attributes)
            self.async_write_ha_state()
