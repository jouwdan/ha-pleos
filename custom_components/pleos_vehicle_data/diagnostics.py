"""Diagnostics download: what the integration received, parsed and stored, with personal data removed."""

import time
from datetime import datetime

from homeassistant.components.diagnostics import REDACTED, async_redact_data

from .const import DOMAIN
from .statistics import STATISTICS, statistic_id

# Credentials, the VIN, the user's own vehicle name and precise coordinates.
TO_REDACT = {"client_id", "client_secret", "clientId", "clientSecret", "vin", "nickname", "latitude", "longitude", "accessToken", "geocoder_key", "geocoder_url", "full_address"}


def scrub(value, secrets):
    """Redact sensitive keys and known personal values embedded in text."""
    value = async_redact_data(value, TO_REDACT)

    def walk(item):
        if isinstance(item, dict):
            return {walk(key): walk(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(child) for child in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, REDACTED)
            return item
        if isinstance(item, datetime):
            return item.isoformat()
        return item
    return walk(value)


def _coordinator_state(coordinator):
    retry_in = coordinator.client.retry_at - time.monotonic()
    return {
        "last_update_success": coordinator.last_update_success,
        "using_last_data": coordinator.using_last_data,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "status": (coordinator.data or {}).get("status"),
        "update_interval_minutes": coordinator.update_interval.total_seconds() / 60,
        "rate_limited_for_seconds": round(retry_in) if retry_in > 0 else 0,
        "api_metadata": list(coordinator.client.response_meta.values()),
    }


def _trip_state(trips):
    return {
        "interval_minutes": trips.interval.total_seconds() / 60,
        "max_query_days": trips.max_days,
        "fetched_up_to": trips.window,
        "announced_trips": trips.seen,
        "cached_results_fetched_at": {car_id: cached["fetched_at"] for car_id, cached in trips.cache.items()},
    }


def _archive_state(archive):
    if archive is None:
        return {"enabled": False}
    hashes = archive.hashes or {}
    return {"enabled": True, "directory": str(archive.directory), "status": archive.status,
            "remembered_snapshots": len(hashes.get("snapshots", {})),
            "remembered_trips": sum(len(slots) for slots in hashes.get("trips", {}).values())}


def _statistics_state(hass, statistics):
    """Per car: which statistics exist, the hours recorded and hours still waiting for import."""
    ledger = statistics.ledger or {}
    result = {"recorder_loaded": "recorder" in hass.config.components}
    for car_id in sorted(set(ledger.get("hours", {})) | set(ledger.get("trips", {}))):
        hours = sorted(ledger.get("hours", {}).get(car_id, {}))
        result[car_id] = {
            "statistic_ids": [statistic_id(car_id, kind) for kind in STATISTICS],
            "hours": len(hours), "first_hour": hours[0] if hours else None, "last_hour": hours[-1] if hours else None,
            "distance_km": round(sum(values.get("km", 0) for values in ledger["hours"].get(car_id, {}).values()), 3),
            "pending_from": ledger.get("pending", {}).get(car_id),
            "recent_trips": len(ledger.get("trips", {}).get(car_id, {})),
        }
    return result


def _cars(coordinator):
    cars = (coordinator.data or {}).get("cars") or {}
    result = {}
    for car_id, car in cars.items():
        readings = {}
        for key, reading in car.get("readings", {}).items():
            if key.endswith(("_latitude", "_longitude")):
                # Undocumented trip coordinates can also have min/max/mean attributes.
                reading = reading | {"value": REDACTED, "attributes": {}}
            elif key == "location_address":
                reading = reading | {"value": REDACTED}
            elif "rejected_examples" in reading.get("attributes", {}):
                reading = reading | {"attributes": reading["attributes"] | {
                    "rejected_examples": [example | {"value": REDACTED}
                                          for example in reading["attributes"]["rejected_examples"]]}}
            readings[key] = reading
        result[car_id] = car | {"readings": readings}
    return result


def _secrets(entry):
    coordinator = entry.runtime_data
    values = [entry.data.get("client_id"), entry.data.get("client_secret"), entry.options.get("geocoder_key"), coordinator.client.token]
    for car in ((coordinator.data or {}).get("cars") or {}).values():
        values.extend(car.get("vehicle", {}).get(key) for key in ("vin", "nickname"))
        address = car.get("readings", {}).get("location_address", {})
        values.extend((address.get("value"), address.get("attributes", {}).get("full_address")))
    return sorted({value for value in values if isinstance(value, str) and value}, key=len, reverse=True)


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return scrub({
        "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
        "coordinator": _coordinator_state(coordinator),
        "trips": _trip_state(coordinator.trips),
        "archive": _archive_state(coordinator.archive),
        "statistics": _statistics_state(hass, coordinator.statistics),
        "cars": _cars(coordinator),
    }, _secrets(entry))


async def async_get_device_diagnostics(hass, entry, device):
    coordinator = entry.runtime_data
    cars = _cars(coordinator)
    car_id = next((identifier for domain, identifier in device.identifiers if domain == DOMAIN), None)
    if car_id not in cars:
        # The manufacturer's API device: account-level state only.
        data = await async_get_config_entry_diagnostics(hass, entry)
        data.pop("cars")
        return data
    return scrub({"vehicle_id": car_id, "car": cars[car_id]}, _secrets(entry))
