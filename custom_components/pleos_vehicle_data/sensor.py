"""Readings and account connection status."""

from datetime import datetime
import time
from typing import Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .archive import ResponseArchive
from .const import DOMAIN
from .coordinator import PleosCoordinator
from .entity import VehicleEntity, discover
from .names import ENTITY_NAMES
from .models import PleosConfigEntry, Reading, ReadingValue

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: PleosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    # Refresh HA's saved default for existing tyres without changing explicit user units.
    registry = entity_registry.async_get(hass)
    for registered in entity_registry.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.domain == "sensor" and registered.original_device_class == "pressure":
            options = dict(registered.options.get("sensor.private", {}))
            if options.get("suggested_unit_of_measurement") != "psi":
                registry.async_update_entity_options(registered.entity_id, "sensor.private",
                                                     options | {"suggested_unit_of_measurement": "psi"})
    async_add_entities([ConnectionSensor(coordinator), LastSuccessfulPollSensor(coordinator)]
                       + ([ArchiveSensor(coordinator)] if coordinator.archive else []))
    discover(entry, async_add_entities, "sensor", ReadingSensor)


class ReadingSensor(VehicleEntity, SensorEntity):
    platform_domain = "sensor"
    # Complete trip arrays are available now, without copying them into recorder history.
    _unrecorded_attributes = frozenset({"data", "vin", "rejected_examples"})

    def __init__(self, coordinator: PleosCoordinator, car_id: str, key: str, spec: Reading) -> None:
        super().__init__(coordinator, car_id, key, spec)
        self._attr_native_unit_of_measurement = spec.get("unit_of_measurement")
        self._attr_device_class = cast(SensorDeviceClass | None, spec.get("device_class"))
        self._attr_state_class = cast(SensorStateClass | None, spec.get("state_class"))
        self._attr_options = spec.get("options")
        if spec.get("device_class") == "energy_distance":
            self._attr_suggested_unit_of_measurement = (
                "mi/kWh" if coordinator.hass.config.units.length_unit == "mi" else "kWh/100km"
            )
            self._attr_suggested_display_precision = 2
        if spec.get("device_class") == "pressure":
            self._attr_suggested_unit_of_measurement = "psi"
            self._attr_suggested_display_precision = 1

    @property
    def entity_category(self) -> EntityCategory | None:
        category = (self.reading or self.spec).get("entity_category")
        return EntityCategory(category) if category else None

    @callback
    def _handle_coordinator_update(self) -> None:
        # Battery endpoint availability can change which reading belongs in Diagnostics.
        if self.registry_entry and self.registry_entry.entity_category != self.entity_category:
            entity_registry.async_get(self.hass).async_update_entity(
                self.entity_id, entity_category=self.entity_category,
            )
        super()._handle_coordinator_update()

    @property
    def native_unit_of_measurement(self) -> str | None:
        reading = self.reading
        return reading.get("unit_of_measurement") if reading else self.spec.get("unit_of_measurement")

    @property
    def native_value(self) -> ReadingValue | datetime:
        reading = self.reading
        value = reading["value"] if reading else None
        if value is not None and self.device_class == "timestamp":
            return datetime.fromisoformat(cast(str, value).replace("Z", "+00:00"))
        return value


class ConnectionSensor(CoordinatorEntity[PleosCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "connection"
    _attr_options = ["connected", "no_vehicles", "key_required", "api_unavailable", "rate_limited"]

    # The entity ID stays English, like every other entity of this integration.
    suggested_object_id = ENTITY_NAMES["sensor"]["connection"]

    def __init__(self, coordinator: PleosCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.account_id + "_api_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.account_id + "_api")}, name=coordinator.account_name + " API", manufacturer="Pleos",
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if self.coordinator.last_update_success:
            return self.coordinator.data["status"]
        if isinstance(self.coordinator.last_exception, ConfigEntryAuthFailed):
            return "key_required"
        return "rate_limited" if self.coordinator.client.retry_at > time.monotonic() else "api_unavailable"


class LastSuccessfulPollSensor(CoordinatorEntity[PleosCoordinator], SensorEntity):
    """When this account last completed a poll without request errors."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_successful_poll"
    suggested_object_id = ENTITY_NAMES["sensor"]["last_successful_poll"]

    def __init__(self, coordinator: PleosCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.account_id + "_last_successful_poll"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.account_id + "_api")})

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_successful_poll


class ArchiveSensor(CoordinatorEntity[PleosCoordinator], SensorEntity):
    """Whether this account's response archive is being written."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "archive"
    _attr_options = ["ok", "error"]
    suggested_object_id = ENTITY_NAMES["sensor"]["archive"]

    def __init__(self, coordinator: PleosCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.account_id + "_archive"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.account_id + "_api")})

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        return cast(ResponseArchive, self.coordinator.archive).status["state"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        archive = cast(ResponseArchive, self.coordinator.archive)
        status = archive.status
        return {"directory": str(archive.directory), "last_written_at": status["last_written_at"],
                "records_last_poll": status["records_last_poll"], "records_written_since_start": status["records_written"],
                "last_error": status["last_error"]}
