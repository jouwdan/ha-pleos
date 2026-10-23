"""Estimate EV consumption from matching battery and odometer reports."""

import logging
from datetime import datetime

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import DistanceConverter

from .const import DOMAIN
from .data import finite_number, reported_age

LOGGER = logging.getLogger(__name__)
ECONOMY_KEY = "estimated_ev_consumption"
MIN_DISTANCE_KM = 10
MIN_SOC_DROP = 5
MAX_GAP_SECONDS = 24 * 60 * 60


def economy_store(hass, entry_id):
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.economy")


def valid_sample(sample):
    return (isinstance(sample, dict) and reported_age(sample.get("reported_at")) is not None
            and all(finite_number(sample.get(key)) for key in ("km", "soc", "capacity"))
            and 0 <= sample["km"] <= 16777214 * 1.609344 and 0 <= sample["soc"] <= 100
            and 0 < sample["capacity"] <= 1000)


def sample_time(sample):
    return datetime.fromisoformat(sample["reported_at"].replace("Z", "+00:00"))


def valid_window(window):
    if not isinstance(window, dict) or not all(valid_sample(window.get(key)) for key in ("start", "last")):
        return False
    start, last = window["start"], window["last"]
    return (sample_time(start) <= sample_time(last) and start["capacity"] == last["capacity"]
            and start["km"] <= last["km"] and start["soc"] >= last["soc"])


def window_reading(window):
    start, last = window["start"], window["last"]
    distance, drop = last["km"] - start["km"], start["soc"] - last["soc"]
    energy = last["capacity"] * drop / 100
    ready = distance >= MIN_DISTANCE_KM and drop >= MIN_SOC_DROP
    return {
        "name": "Estimated EV consumption", "domain": "sensor", "source": "batteries",
        "device_class": "energy_distance", "unit_of_measurement": "kWh/100km",
        "state_class": "measurement", "value": energy * 100 / distance if ready else None,
        "attributes": {"estimated": True, "reported_at": last["reported_at"], "last_known": False,
                       "window_started_at": start["reported_at"], "distance_km": round(distance, 3),
                       "energy_used_kwh": round(energy, 3), "battery_capacity_kwh": last["capacity"],
                       "state_of_charge_drop": round(drop, 3)},
    }


def snapshot(car):
    """Never combine retained values or reports from different vehicle timestamps."""
    readings = car["readings"]
    for key, expected in (("batteries_charge_charging", "OFF"), ("batteries_charge_plugin", "OFF"),
                          ("batteries_charge_status", "not_charging")):
        if readings.get(key, {}).get("value") != expected:
            return None
    keys = ("driving_odometer", "batteries_charge_stateofcharge", "batteries_charge_batterycapacity")
    if any(key not in readings or readings[key].get("attributes", {}).get("last_known") for key in keys):
        return None
    battery_time, driving_time = (car["sources"].get(key) for key in ("batteries", "driving"))
    if reported_age(battery_time) is None or reported_age(driving_time) is None:
        return None
    if datetime.fromisoformat(battery_time.replace("Z", "+00:00")) != datetime.fromisoformat(driving_time.replace("Z", "+00:00")):
        return None
    odometer = readings["driving_odometer"]
    sample = {"reported_at": battery_time,
              "km": DistanceConverter.convert(odometer["value"], odometer["unit_of_measurement"], "km"),
              "soc": readings["batteries_charge_stateofcharge"]["value"],
              "capacity": readings["batteries_charge_batterycapacity"]["value"]}
    return sample if valid_sample(sample) else None


class EVEconomy:
    def __init__(self, hass, entry_id):
        self.store = economy_store(hass, entry_id)
        self.windows = None
        self.estimates = {}
        self.dirty = False

    async def apply(self, cars, stale_minutes):
        if self.windows is None:
            try:
                stored = await self.store.async_load()
            except (HomeAssistantError, OSError):
                LOGGER.warning("Could not load EV consumption; starting new estimates without overwriting saved data")
                self.store.make_read_only()
                stored = None
            self.windows = {}
            for car_id, window in (stored.items() if isinstance(stored, dict) else ()):
                if not isinstance(window, dict):
                    continue
                if valid_window(window):
                    self.windows[car_id] = {key: window[key] for key in ("start", "last")}
                # Older stores contain only the active window; recover its estimate if complete.
                estimate = window.get("estimate", window)
                if valid_window(estimate) and window_reading(estimate)["value"] is not None:
                    self.estimates[car_id] = {key: estimate[key] for key in ("start", "last")}

        kept = {}
        estimates = {car_id: value for car_id, value in self.estimates.items()
                     if car_id in cars and cars[car_id]["vehicle"].get("type") == "EV"}
        for car_id, car in cars.items():
            if car["vehicle"].get("type") != "EV" or (current := snapshot(car)) is None:
                continue
            window = self.windows.get(car_id)
            if window and sample_time(current) < sample_time(window["last"]):
                kept[car_id] = window
                continue
            if not window or current != window["last"]:
                # Repeated parked reports can keep an estimate, but stale new reports cannot build one.
                if reported_age(current["reported_at"]) > stale_minutes * 60:
                    continue
                if window:
                    last = window["last"]
                    elapsed = (sample_time(current) - sample_time(last)).total_seconds()
                    distance = current["km"] - last["km"]
                    if (elapsed <= 0 or elapsed > MAX_GAP_SECONDS or distance < 0
                            or distance > 250 * elapsed / 3600 or current["soc"] > last["soc"]
                            or current["capacity"] != last["capacity"]
                            or (distance == 0 and current["soc"] != last["soc"])):
                        window = None
                window = {"start": window["start"] if window else current, "last": current}
            kept[car_id] = window
            reading = car["readings"][ECONOMY_KEY] = window_reading(window)
            if reading["value"] is not None:
                estimates[car_id] = {key: dict(window[key]) for key in ("start", "last")}
        for car_id, estimate in estimates.items():
            if cars[car_id]["readings"].get(ECONOMY_KEY, {}).get("value") is None:
                reading = window_reading(estimate)
                reading["attributes"].update(last_known=True, data_stale=True)
                cars[car_id]["readings"][ECONOMY_KEY] = reading
        self.dirty |= kept != self.windows or estimates != self.estimates
        self.windows = kept
        self.estimates = estimates
        if self.dirty:
            saved = {car_id: kept.get(car_id, {}) | ({"estimate": estimates[car_id]} if car_id in estimates else {})
                     for car_id in kept.keys() | estimates.keys()}
            try:
                await self.store.async_save(saved)
            except OSError:
                LOGGER.warning("Could not save EV consumption; estimates may restart after reload")
            else:
                self.dirty = False
