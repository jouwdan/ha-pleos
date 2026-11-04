"""Typed configuration, normalized readings and coordinator state."""

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import PleosCoordinator

# API metadata and payloads deliberately retain undocumented JSON fields.
type Trip = dict[str, Any]
type ReadingValue = str | int | float | bool | None


class ReadingMetadata(TypedDict, total=False):
    """Optional Home Assistant metadata attached to a normalized reading."""

    unit_of_measurement: str | None
    device_class: str | None
    state_class: str | None
    options: list[str]
    entity_category: str | None


class Reading(ReadingMetadata, total=False):
    """A reading assembled by normalization and enriched by the coordinator."""

    name: str
    domain: str
    value: ReadingValue
    source: str
    historical: bool
    attributes: dict[str, Any]


class LocationPoint(TypedDict):
    """Coordinates checked for numeric WGS84 bounds before use."""

    latitude: float
    longitude: float
    type: NotRequired[int]


class VehicleState(TypedDict):
    """One vehicle and the readings successfully normalized for this poll."""

    vehicle: dict[str, Any]
    readings: dict[str, Reading]
    sources: dict[str, str | None]
    location: LocationPoint | None


class CoordinatorData(TypedDict):
    """Account result shared by every platform."""

    status: str
    cars: dict[str, VehicleState]


class Options(TypedDict):
    """Integration options after merging saved values with the defaults."""

    stale_minutes: float
    location: bool
    reverse_geocode: bool
    geocoder_url: str
    geocoder_key: str
    trip_events: bool
    archive: bool
    odometer_km_vehicles: list[str]


type PleosConfigEntry = ConfigEntry[PleosCoordinator]
