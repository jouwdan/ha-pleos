"""Last valid ranges and tyre pressures, including their original reporting time."""

import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .data import finite_number, reported_age

LOGGER = logging.getLogger(__name__)
RANGE_PREFIX = "powertrains_distancetoempties_"
TYRE_PREFIX = "status_tire_"


def range_store(hass, entry_id):
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.ranges")


class RangeCache:
    def __init__(self, hass, entry_id):
        self.store = range_store(hass, entry_id)
        self.values = None
        self.dirty = False

    async def apply(self, cars):
        if self.values is None:
            try:
                stored = await self.store.async_load()
            except (HomeAssistantError, OSError):
                LOGGER.warning("Could not load saved ranges and tyre pressures; keeping new values only in memory until reload")
                # A failed read must not overwrite a cache we could not inspect.
                self.store.make_read_only()
                stored = None
            self.values = {}
            for car_id, entries in (stored.items() if isinstance(stored, dict) else ()):
                if not isinstance(entries, dict):
                    continue
                for key, saved in entries.items():
                    tyre = isinstance(key, str) and key.startswith(TYRE_PREFIX)
                    limits = {"psi": 254, "kPa": 1270, "bar": 25.4} if tyre else dict.fromkeys(("km", "mi", "m", "ft"), 4095)
                    if (not isinstance(key, str) or not key.startswith((RANGE_PREFIX, TYRE_PREFIX)) or not isinstance(saved, dict)
                            or not finite_number(saved.get("value"))
                            or not isinstance(saved.get("unit"), str)
                            or not (0 if tyre else 1) <= saved["value"] <= limits.get(saved.get("unit"), -1)
                            or reported_age(saved.get("reported_at")) is None
                            or not isinstance(saved.get("name"), str) or len(saved["name"]) > 255):
                        continue
                    self.values.setdefault(car_id, {})[key] = saved

        kept = {}
        for car_id, car in cars.items():
            saved = dict(self.values.get(car_id, {}))
            current = set()
            for key, reading in car["readings"].items():
                if not key.startswith((RANGE_PREFIX, TYRE_PREFIX)):
                    continue
                stamp = car["sources"].get(reading["source"])
                if reported_age(stamp) is not None:
                    saved[key] = {"value": reading["value"], "unit": reading["unit_of_measurement"],
                                  "name": reading["name"], "reported_at": stamp}
                    reading.setdefault("attributes", {})["last_known"] = False
                    current.add(key)
            for key, reading in saved.items():
                if key not in current:
                    tyre = key.startswith(TYRE_PREFIX)
                    car["readings"][key] = {
                        "value": reading["value"], "unit_of_measurement": reading["unit"], "name": reading["name"],
                        "domain": "sensor", "device_class": "pressure" if tyre else "distance",
                        "source": "status" if tyre else "powertrains", "state_class": "measurement" if tyre else None,
                        "attributes": {"reported_at": reading["reported_at"], "last_known": True, "data_stale": True},
                    }
            if saved:
                kept[car_id] = saved
        self.dirty |= kept != self.values
        self.values = kept
        if self.dirty:
            try:
                await self.store.async_save(kept)
            except OSError:
                LOGGER.warning("Could not save ranges and tyre pressures; they may be lost after restart")
            else:
                self.dirty = False
