"""Integration constants."""

DOMAIN = "pleos_vehicle_data"
BRANDS = {"hyundai": "Hyundai", "kia": "Kia", "genesis": "Genesis"}
SNAPSHOT_ENDPOINTS = ("batteries", "powertrains", "driving", "status", "locations", "freezer")
TRIP_ENDPOINTS = ("powertrains/data", "speed", "driving/data", "status/data")
ENDPOINTS = SNAPSHOT_ENDPOINTS + TRIP_ENDPOINTS
ENDPOINT_NAMES = {
    "batteries": "Battery charging", "powertrains": "Powertrain status", "driving": "Driving status",
    "status": "Vehicle status", "locations": "Location", "freezer": "Freezer status",
    "powertrains/data": "Powertrain trips", "speed": "Speed trips",
    "driving/data": "Driving trips", "status/data": "Vehicle status trips",
}
DEFAULT_OPTIONS = {"poll_minutes": 15, "stale_minutes": 60, "location": True, "reverse_geocode": False, "geocoder_url": "", "geocoder_key": "", "trip_events": False,
                   "trip_poll_minutes": 60, "archive": False, "odometer_km_vehicles": []}
TRIP_EVENT = DOMAIN + "_trip"
# Trip queries normally cover yesterday and today (UTC); after downtime they reach back to the
# last day fetched, at most this many days including today.
TRIP_CATCH_UP_DAYS = 7
# Announced trips are remembered longer than any query reaches back, so none is announced twice.
TRIP_MEMORY_DAYS = TRIP_CATCH_UP_DAYS + 2
SERVICE_REFRESH = "refresh"
