"""Supported entity names and translations for documented vehicle fields.

strings.json and translations/en.json carry the same names; a test keeps them in step.
Existing undocumented entities retain their generated names for compatibility.
"""

import re

from .const import ENDPOINT_NAMES, ENDPOINTS, SNAPSHOT_ENDPOINTS, TRIP_ENDPOINTS
from .data import NAMES, ON_OFF_KEYS, OPENING_CLASSES, TRIP_BINARY, TRIP_FIELDS, TRIP_NAMES, WARNING_NAMES, identifier, label

# Documented snapshot binary readings.
BINARY_KEYS = (("batteries_charge_charging", "batteries_charge_plugin", "driving_transmission_parkingposition")
               + ON_OFF_KEYS + tuple(OPENING_CLASSES) + tuple("status_warning_" + code.lower() for code in WARNING_NAMES))
NUMBERED_RANGE_NAMES = {key + "_numbered": name + " {index}" for key, name in NAMES.items()
                        if key.startswith("powertrains_distancetoempties_")}
DISABLED_DIAGNOSTICS = frozenset(
    {identifier(endpoint) + "_api" for endpoint in ENDPOINTS}
    | {identifier(endpoint) + suffix for endpoint in SNAPSHOT_ENDPOINTS
       for suffix in ("_reported_at", "_data_age")}
    | {"locations_location_type", "speed_location_speedunit", "status_data_tire_pressureunit"}
)


def _sensor_names():
    names = {key: name for key, name in NAMES.items() if key not in BINARY_KEYS}
    names["connection"] = "Connection"
    names["last_successful_poll"] = "Last successful poll"
    names["archive"] = "Archive"
    names["estimated_ev_consumption"] = "Estimated EV consumption"
    names["vehicle_information"] = "Vehicle information"
    names["location_address"] = "Address"
    for endpoint in ENDPOINTS:
        names[identifier(endpoint) + "_api"] = ENDPOINT_NAMES[endpoint] + " API"
    for endpoint in SNAPSHOT_ENDPOINTS:
        names[identifier(endpoint) + "_reported_at"] = ENDPOINT_NAMES[endpoint] + " last reported"
        names[identifier(endpoint) + "_data_age"] = ENDPOINT_NAMES[endpoint] + " data age"
    for endpoint in TRIP_ENDPOINTS:
        prefix = identifier(endpoint)
        names[prefix + "_trip_start"] = label((ENDPOINT_NAMES[endpoint], "start"))
        names[prefix + "_trip_duration"] = label((ENDPOINT_NAMES[endpoint], "duration"))
        for field in TRIP_FIELDS[endpoint]:
            key = prefix + "_" + identifier(field)
            if key not in TRIP_BINARY:
                names[key] = TRIP_NAMES[field] + " (last trip)"
    names["driving_data_trip_distance"] = "Trip distance (last trip)"
    return names | NUMBERED_RANGE_NAMES


ENTITY_NAMES = {
    "sensor": _sensor_names(),
    "binary_sensor": {key: NAMES[key] for key in BINARY_KEYS} | {
        identifier(endpoint) + "_" + identifier(field): TRIP_NAMES[field] + " (last trip)"
        for endpoint, fields in TRIP_FIELDS.items() for field in fields
        if identifier(endpoint) + "_" + identifier(field) in TRIP_BINARY},
    "device_tracker": {"location": "Location"},
    "event": {"trip": "Trip"},
}


def entity_translation_key(platform: str, key: str) -> str | None:
    """Identify supported fields, including repeated ranges with stable slot IDs."""
    if key in ENTITY_NAMES[platform] and key not in NUMBERED_RANGE_NAMES:
        return key
    base, _, index = key.rpartition("_")
    numbered = base + "_numbered"
    if platform == "sensor" and numbered in NUMBERED_RANGE_NAMES and re.fullmatch(r"[2-9]|[1-9][0-9]+", index):
        return numbered
    return None
