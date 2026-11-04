"""Trip state per account: the fetch interval, reused results and announced trips."""

from datetime import date, datetime, timedelta
from typing import Any, TypedDict, cast

from homeassistant.core import HomeAssistant

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import DOMAIN, TRIP_CATCH_UP_DAYS, TRIP_ENDPOINTS, TRIP_EVENT, TRIP_MEMORY_DAYS
from .data import new_trips, prune_seen
from .models import Reading, Trip, VehicleState

type TripStarts = dict[str, dict[str, list[str]]]
type TripWindow = dict[str, dict[str, str]]
type QueryRange = dict[str, str]
type PendingTrips = list[tuple[str, str, Trip]]


class CachedEndpoint(TypedDict):
    start: str | None
    readings: dict[str, Reading]


class CachedTrips(TypedDict):
    fetched_at: datetime
    endpoints: dict[str, CachedEndpoint]


def trip_store(hass: HomeAssistant, entry_id: str) -> Store[TripStarts]:
    """Trip start times already announced, per car and endpoint, kept across restarts."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.trips")


def fired_store(hass: HomeAssistant, entry_id: str) -> Store[TripStarts]:
    """Trip start times already fired on the Trip event entity, per car and endpoint, kept across restarts."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.trip_entity")


def window_store(hass: HomeAssistant, entry_id: str) -> Store[TripWindow]:
    """The last UTC day each car's trip endpoints were fetched up to, kept across restarts."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.trip_window")


class TripTracker:
    def __init__(self, hass: HomeAssistant, entry_id: str, interval_minutes: float) -> None:
        self.hass, self.entry_id = hass, entry_id
        self.interval = timedelta(minutes=interval_minutes)
        self.store = trip_store(hass, entry_id)
        # Announced trip starts: {car_id: {endpoint: [start, ...]}}, loaded on first use.
        self.seen: TripStarts | None = None
        # Last trip results per car: {car_id: {"fetched_at": datetime, "endpoints": {endpoint: {...}}}}.
        self.cache: dict[str, CachedTrips] = {}
        self.window: TripWindow | None = None  # {car_id: {endpoint: "YYYYMMDD"}}: last day fetched successfully.
        self.window_store = window_store(hass, entry_id)
        self.covered_this_poll: TripWindow = {}
        # Longest range queried; lowered to two days if the API refuses longer ranges.
        self.max_days = TRIP_CATCH_UP_DAYS
        self.fired: TripStarts | None = None  # {car_id: {endpoint: [start, ...]}}: trips fired on the Trip event entity.
        self.fired_store = fired_store(hass, entry_id)

    async def load_fired(self) -> None:
        if self.fired is None:
            stored = await self.fired_store.async_load()
            self.fired = stored if isinstance(stored, dict) else {}

    def unfired(self, car_id: str, endpoint: str, data: Any) -> list[Trip]:
        """Trips in `data` not fired on the Trip event entity yet, oldest first. Call `load_fired` first."""
        return new_trips(data, set(cast(TripStarts, self.fired).get(car_id, {}).get(endpoint, ())))

    async def mark_fired(self, items: PendingTrips) -> None:
        """Remember fired trips ([(car_id, endpoint, trip)]) and forget old ones."""
        fired = cast(TripStarts, self.fired)
        for car_id, endpoint, trip in items:
            fired.setdefault(car_id, {}).setdefault(endpoint, []).append(trip["triggerStartTime"])
        max_age = TRIP_MEMORY_DAYS * 86400
        pruned = {car_id: {endpoint: kept for endpoint, starts in endpoints.items()
                           if (kept := prune_seen(starts, max_age))}
                  for car_id, endpoints in fired.items()}
        pruned = {car_id: endpoints for car_id, endpoints in pruned.items() if endpoints}
        if items or pruned != self.fired:
            self.fired = pruned
            await self.fired_store.async_save(pruned)

    async def load_window(self) -> None:
        if self.window is None:
            stored = await self.window_store.async_load()
            self.window = stored if isinstance(stored, dict) else {}
        self.covered_this_poll = {}

    def query_range(self, car_id: str, endpoint: str, now: datetime, days: int | None = None) -> QueryRange:
        """Yesterday and today, reaching back to the last day fetched after downtime."""
        today = now.date()
        earliest = today - timedelta(days=(days or self.max_days) - 1)
        start = min(today - timedelta(days=1), earliest if days else self._last_day(car_id, endpoint) or earliest)
        return {"startDate": max(start, earliest).strftime("%Y%m%d"), "endDate": today.strftime("%Y%m%d"), "zoneId": "UTC"}

    def _last_day(self, car_id: str, endpoint: str) -> date | None:
        stamp = cast(str, cast(TripWindow, self.window).get(car_id, {}).get(endpoint))
        try:
            return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        except (TypeError, ValueError):
            return None

    def covered(self, car_id: str, endpoint: str, query: QueryRange) -> None:
        """Note a successful trip query; it is kept once the whole poll succeeded."""
        self.covered_this_poll.setdefault(car_id, {})[endpoint] = query["endDate"]

    async def save_window(self) -> None:
        if not self.covered_this_poll:
            return
        for car_id, endpoints in self.covered_this_poll.items():
            cast(TripWindow, self.window).setdefault(car_id, {}).update(endpoints)
        self.covered_this_poll = {}
        await self.window_store.async_save(cast(TripWindow, self.window))

    def due(self, car_id: str, now: datetime) -> bool:
        """Trip data arrives with hours of delay, so trips are fetched less often than snapshots."""
        cached = self.cache.get(car_id)
        return cached is None or now - cached["fetched_at"] >= self.interval

    def reuse(self, car_id: str, endpoint: str, car: VehicleState) -> None:
        """Copy the last fetched results of one trip endpoint into this poll's car data."""
        if (cached := self.cache.get(car_id)) and (reused := cached["endpoints"].get(endpoint)):
            car["readings"].update(reused["readings"])
            car["sources"][endpoint] = reused["start"]

    def remember(self, car_id: str, now: datetime, car: VehicleState) -> None:
        self.cache[car_id] = {"fetched_at": now, "endpoints": {
            endpoint: {"start": car["sources"].get(endpoint),
                       "readings": {key: reading for key, reading in car["readings"].items()
                                    if reading.get("source") == endpoint}}
            for endpoint in TRIP_ENDPOINTS}}

    async def load(self) -> TripStarts:
        if self.seen is None:
            stored = await self.store.async_load()
            self.seen = stored if isinstance(stored, dict) else {}
        return self.seen

    def unseen(self, car_id: str, endpoint: str, data: Any) -> list[Trip]:
        """Trips in `data` not announced yet. Call `load` first."""
        return new_trips(data, set(cast(TripStarts, self.seen).get(car_id, {}).get(endpoint, ())))

    async def announce(self, brand: str, pending: PendingTrips) -> None:
        """Fire one event per new trip, oldest first, then remember them."""
        seen = await self.load()
        devices = dr.async_get(self.hass) if pending else None
        for car_id, endpoint, trip in pending:
            device = cast(dr.DeviceRegistry, devices).async_get_device_by_identifier((DOMAIN, car_id), self.entry_id)
            self.hass.bus.async_fire(TRIP_EVENT, {
                "vehicle_id": car_id, "device_id": device.id if device else None, "brand": brand,
                "endpoint": endpoint, "trip_start": trip["triggerStartTime"],
                "trip_length_ms": trip.get("tripLength"), "trip": trip,
            })
            seen.setdefault(car_id, {}).setdefault(endpoint, []).append(trip["triggerStartTime"])
        max_age = TRIP_MEMORY_DAYS * 86400
        pruned = {car_id: {endpoint: kept for endpoint, starts in endpoints.items()
                           if (kept := prune_seen(starts, max_age))}
                  for car_id, endpoints in seen.items()}
        pruned = {car_id: endpoints for car_id, endpoints in pruned.items() if endpoints}
        if pending or pruned != seen:
            self.seen = pruned
            await self.store.async_save(pruned)
