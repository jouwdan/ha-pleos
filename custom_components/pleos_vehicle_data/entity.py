"""Shared vehicle identity and dynamic discovery."""

from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRANDS, DOMAIN
from .data import identifier, reported_age
from .economy import ECONOMY_KEY
from .names import ENTITY_NAMES


def discover(entry, async_add_entities, domain, factory):
    coordinator = entry.runtime_data
    seen = set()

    def add_new():
        entities = []
        for car_id, car in coordinator.data["cars"].items():
            items = {"location": {"name": "Location", "source": "locations"}} if domain == "device_tracker" and car["location"] else car["readings"] if domain != "device_tracker" else {}
            for key, spec in items.items():
                if (domain == "device_tracker" or spec["domain"] == domain) and (car_id, key) not in seen:
                    seen.add((car_id, key))
                    entities.append(factory(coordinator, car_id, key, spec))
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class VehicleEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    platform_domain = None  # Set by each platform's entity class.

    def __init__(self, coordinator, car_id, key, spec):
        super().__init__(coordinator)
        self.car_id, self.key, self.spec = car_id, key, spec
        self._attr_unique_id = car_id + "_" + key
        if key in ENTITY_NAMES[self.platform_domain]:
            self._attr_translation_key = key
        else:
            # Undocumented fields keep the English name generated from their path.
            self._attr_name = spec["name"]
        vehicle = coordinator.data["cars"][car_id]["vehicle"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, car_id)},
            name=vehicle.get("nickname") or vehicle.get("sellname") or BRANDS[coordinator.brand] + " vehicle",
            manufacturer=BRANDS[coordinator.brand], model=vehicle.get("sellname") or vehicle.get("name") or "Vehicle",
        )
        self._attr_entity_category = EntityCategory(spec["entity_category"]) if spec.get("entity_category") else None

    @property
    def suggested_object_id(self):
        # New entity IDs always come from the English name, whatever the system language.
        return ENTITY_NAMES[self.platform_domain].get(self.key, self.spec["name"])

    @property
    def car(self):
        return self.coordinator.data["cars"].get(self.car_id)

    @property
    def reading(self):
        if not self.car or (reading := self.car["readings"].get(self.key)) is None:
            return None
        attrs = reading.get("attributes", {})
        stamp = attrs.get("reported_at", self.car["sources"].get(self.spec["source"]))
        if not self.historical and (
            attrs.get("last_known") or reported_age(stamp) is None
        ):
            # Preserve the cached reading and its provenance without presenting it as current.
            return reading | {"value": None}
        return reading

    @property
    def historical(self):
        return (
            self.key == ECONOMY_KEY
            or self.spec.get("historical")
            or self.spec.get("entity_category") == "diagnostic"
            and self.spec.get("device_class") != "battery"
        )

    @property
    def reported_at(self):
        return (self.reading or {}).get("attributes", {}).get("reported_at", self.car["sources"].get(self.spec["source"]))

    @property
    def available(self):
        if not super().available or not self.car:
            return False
        if self.historical:
            return True
        source = self.car["readings"].get(identifier(self.spec["source"]) + "_api", {})
        return source.get("value") not in ("error", "disabled")

    @property
    def extra_state_attributes(self):
        if not self.car:
            return None
        attrs = {"source": self.spec["source"]}
        if not self.spec.get("historical"):
            attrs["reported_at"] = self.reported_at
            age = reported_age(attrs["reported_at"])
            if age is not None:
                attrs["data_age_minutes"] = round(age / 60, 1)
                attrs["data_stale"] = age > self.coordinator.options["stale_minutes"] * 60
        attrs |= self.reading.get("attributes", {}) if self.reading else {}
        if self.coordinator.using_last_data:
            attrs.update(last_known=True, data_stale=True)
        return attrs
