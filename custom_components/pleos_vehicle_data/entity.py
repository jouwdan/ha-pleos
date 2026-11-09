"""Shared vehicle identity and dynamic discovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from homeassistant.const import EntityCategory
from homeassistant.core import Event, callback
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRANDS, DOMAIN
from .coordinator import PleosCoordinator
from .data import identifier, reported_age
from .economy import ECONOMY_KEY
from .migration import migrate_to_binary_sensor
from .models import PleosConfigEntry, Reading, VehicleState
from .names import DISABLED_DIAGNOSTICS, ENTITY_NAMES, NUMBERED_RANGE_NAMES, entity_translation_key


def discover(
    entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback, domain: str,
    factory: Callable[[PleosCoordinator, str, str, Reading], VehicleEntity],
) -> None:
    coordinator = entry.runtime_data
    registry = entity_registry.async_get(coordinator.hass)
    seen: set[tuple[str, str]] = set()

    def add_new() -> None:
        entities = []
        for car_id, car in coordinator.data["cars"].items():
            items: dict[str, Reading] = {"location": {"name": "Location", "source": "locations"}} if domain == "device_tracker" and car["location"] else car["readings"] if domain != "device_tracker" else {}
            for key, spec in items.items():
                if (domain == "device_tracker" or spec["domain"] == domain) and (car_id, key) not in seen:
                    if domain == "binary_sensor":
                        migrate_to_binary_sensor(coordinator.hass, entry, car_id + "_" + key)
                    if entity_translation_key(domain, key) is None:
                        registered_id = registry.async_get_entity_id(domain, DOMAIN, car_id + "_" + key)
                        registered = registry.async_get(registered_id) if registered_id else None
                        if registered is None or registered.config_entry_id != entry.entry_id:
                            continue
                    seen.add((car_id, key))
                    entities.append(factory(coordinator, car_id, key, spec))
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))

    @callback
    def device_removed(event: Event[device_registry.EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] != "remove" or event.data["device"]["config_entry_id"] != entry.entry_id:
            return
        removed = {car_id for namespace, car_id in event.data["device"]["identifiers"] if namespace == DOMAIN}
        seen.difference_update({item for item in seen if item[0] in removed})

    entry.async_on_unload(coordinator.hass.bus.async_listen(device_registry.EVENT_DEVICE_REGISTRY_UPDATED, device_removed))


class VehicleEntity(CoordinatorEntity[PleosCoordinator]):
    _attr_has_entity_name = True
    platform_domain: str  # Set by each platform's entity class.

    def __init__(self, coordinator: PleosCoordinator, car_id: str, key: str, spec: Reading) -> None:
        super().__init__(coordinator)
        self.car_id, self.key, self.spec = car_id, key, spec
        self._attr_unique_id = car_id + "_" + key
        if translation_key := entity_translation_key(self.platform_domain, key):
            self._attr_translation_key = translation_key
            if translation_key in NUMBERED_RANGE_NAMES:
                self._attr_translation_placeholders = {"index": key.rsplit("_", 1)[1]}
        else:
            # Only registered legacy readings reach this path. Translate the label,
            # leaving their stable technical field identifier uninterpreted.
            self._attr_translation_key = "legacy_reading"
            self._attr_translation_placeholders = {"field": key}
        vehicle = coordinator.data["cars"][car_id]["vehicle"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, car_id)},
            name=vehicle.get("nickname") or vehicle.get("sellname") or BRANDS[coordinator.brand] + " vehicle",
            manufacturer=BRANDS[coordinator.brand], model=vehicle.get("sellname") or vehicle.get("name") or "Vehicle",
        )
        category = spec.get("entity_category")
        self._attr_entity_category = EntityCategory(category) if category else None
        # HA applies this only to first registration; existing registry choices win.
        self._attr_entity_registry_enabled_default = key not in DISABLED_DIAGNOSTICS

    @property
    def suggested_object_id(self) -> str:
        # New entity IDs always come from the English name, whatever the system language.
        return ENTITY_NAMES[self.platform_domain].get(self.key, self.spec["name"])

    @property
    def car(self) -> VehicleState | None:
        return self.coordinator.data["cars"].get(self.car_id)

    @property
    def reading(self) -> Reading | None:
        car = self.car
        if not car or (reading := car["readings"].get(self.key)) is None:
            return None
        attrs = reading.get("attributes", {})
        stamp = attrs.get("reported_at", car["sources"].get(self.spec["source"]))
        if not self.historical and (
            attrs.get("last_known") or reported_age(stamp) is None
        ):
            # Preserve the cached reading and its provenance without presenting it as current.
            return reading | {"value": None}
        return reading

    @property
    def historical(self) -> bool:
        return (
            self.key == ECONOMY_KEY
            or self.spec.get("historical", False)
            or self.spec.get("entity_category") == "diagnostic"
            and self.spec.get("device_class") != "battery"
        )

    @property
    def reported_at(self) -> str | None:
        car = self.car
        if car is None:
            return None
        return cast(str | None, (self.reading or {}).get("attributes", {}).get(
            "reported_at", car["sources"].get(self.spec["source"]),
        ))

    @property
    def available(self) -> bool:
        car = self.car
        if not super().available or not car:
            return False
        if self.historical:
            return True
        source = car["readings"].get(identifier(self.spec["source"]) + "_api", {})
        return source.get("value") not in ("error", "disabled")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.car:
            return None
        attrs: dict[str, Any] = {"source": self.spec["source"]}
        if not self.spec.get("historical"):
            attrs["reported_at"] = self.reported_at
            age = reported_age(attrs["reported_at"])
            if age is not None:
                attrs["data_age_minutes"] = round(age / 60, 1)
                attrs["data_stale"] = age > self.coordinator.options["stale_minutes"] * 60
        reading = self.reading
        attrs |= reading.get("attributes", {}) if reading else {}
        if self.coordinator.using_last_data:
            attrs.update(last_known=True, data_stale=True)
        return attrs
