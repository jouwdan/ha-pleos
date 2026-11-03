"""The Trip event entity fires once for every trip, so Home Assistant records each one."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import EVENT_STATE_CHANGED, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.pleos_vehicle_data import async_remove_entry
from pleos_client import ApiError, AuthError
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import trip_summary, vehicle_id
from custom_components.pleos_vehicle_data.event import async_setup_entry
from custom_components.pleos_vehicle_data.trips import fired_store

from support import CAR, HomeAssistantTestCase, stamp

CAR_ID = vehicle_id("kia", CAR["vin"])


def speed_trip(start, *speeds):
    return {"triggerStartTime": start, "tripLength": 60000,
            "location": {"speed": list(speeds), "speedUnit": ["kph"] * len(speeds)}}


def driving_trip(start, *odometer):
    return {"triggerStartTime": start, "tripLength": 60000, "odometer": {"value": list(odometer)}}


class TripSummaryTests(HomeAssistantTestCase):
    def test_summary_is_flat_with_statistics_duration_and_distance(self):
        start = stamp(30)
        self.assertEqual(trip_summary("speed", speed_trip(start, 10, 30, 20)), {
            "trip_start": start, "duration_s": 60.0, "location_speed": 20,
            "location_speed_min": 10, "location_speed_max": 30, "location_speed_mean": 20.0,
            "location_speedunit": "kph"})
        driving = trip_summary("driving/data", driving_trip(start, 100.0, 112.5))
        self.assertEqual((driving["distance_km"], driving["odometer_value"], driving["odometer_value_min"]),
                         (12.5, 112.5, 100.0))


class TripEventTests(HomeAssistantTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await entity_registry.async_load(self.hass, load_empty=True)
        self.fired = []

        @callback
        def record(event):
            state = event.data["new_state"]
            if state is not None and state.entity_id.startswith("event.") and state.attributes.get("event_type"):
                self.fired.append(state)
        self.addCleanup(self.hass.bus.async_listen(EVENT_STATE_CHANGED, record))

    def serve(self, speed=(), driving=(), vehicles=(CAR,)):
        self.client.vehicles = AsyncMock(return_value=list(vehicles))
        payloads = {"/speed?": {"trips": list(speed)}, "/driving/data?": {"trips": list(driving)}}
        self.client.get = AsyncMock(side_effect=lambda path: next(
            (payload for marker, payload in payloads.items() if marker in path), None))

    async def start(self, coordinator=None):
        """First refresh, then the event platform, as Home Assistant does at setup."""
        coordinator = coordinator or self.coordinator
        if getattr(self, "platform", None):
            await self.platform.async_reset()
        coordinator.data = await coordinator._async_update_data()
        coordinator.last_update_success = True
        self.entry.runtime_data = coordinator
        self.platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain="event",
                                       platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                       entity_namespace=None)
        self.platform.config_entry = self.entry
        await async_setup_entry(self.hass, self.entry, self.platform._async_schedule_add_entities)
        await self.hass.async_block_till_done()
        self.addAsyncCleanup(self.platform.async_reset)

    async def poll(self, coordinator=None):
        coordinator = coordinator or self.coordinator
        coordinator.trips.cache.clear()
        await coordinator.async_refresh()
        await self.hass.async_block_till_done()

    def history(self):
        return [(state.attributes["event_type"], state.attributes["trip_start"],
                 state.attributes.get("location_speed", state.attributes.get("distance_km")))
                for state in self.fired]

    async def test_trips_found_at_setup_are_fired_once_oldest_first(self):
        older, newer = stamp(600), stamp(60)
        self.serve(speed=[speed_trip(newer, 50), speed_trip(older, 20)])
        await self.start()
        self.assertEqual(self.history(), [("speed", older, 20), ("speed", newer, 50)])
        state = self.fired[-1]
        self.assertEqual(state.entity_id, "event.example_ev_trip")
        self.assertEqual((state.attributes["location_speed_max"], state.attributes["duration_s"]), (50, 60.0))
        await self.poll()
        self.assertEqual(len(self.fired), 2)

    async def test_several_new_trips_in_one_poll_are_each_fired_in_order(self):
        first = speed_trip(stamp(900), 10)
        self.serve(speed=[first])
        await self.start()
        self.fired.clear()
        night, morning, evening = stamp(600), stamp(300), stamp(30)
        self.serve(speed=[first, speed_trip(evening, 70), speed_trip(night, 30), speed_trip(morning, 50)],
                   driving=[driving_trip(morning, 100, 110)])
        await self.poll()
        self.assertEqual(self.history(), [("speed", night, 30), ("speed", morning, 50), ("driving_data", morning, 10),
                                          ("speed", evening, 70)])
        await self.poll()
        self.assertEqual(len(self.fired), 4)

    async def test_restart_does_not_fire_trips_again(self):
        self.serve(speed=[speed_trip(stamp(300), 10)])
        await self.start()
        restarted = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(restarted.async_shutdown)
        await self.start(restarted)
        await self.poll(restarted)
        # Home Assistant restores the last event after a restart (same event time); nothing new is fired.
        self.assertEqual(len({state.state for state in self.fired}), 1)
        self.assertEqual(len({state.attributes["trip_start"] for state in self.fired}), 1)

    async def test_account_failure_marks_trip_unavailable_without_replaying_events(self):
        self.serve(speed=[speed_trip(stamp(300), 10)])
        await self.start()
        event_time = self.hass.states.get('event.example_ev_trip').state
        self.client.vehicles.side_effect = ApiError(503, 'fixture_failure')
        await self.poll()
        self.assertEqual(self.hass.states.get('event.example_ev_trip').state, 'unavailable')
        self.client.vehicles.side_effect = None
        await self.poll()
        self.assertEqual(self.hass.states.get('event.example_ev_trip').state, event_time)

    async def test_failed_poll_fires_its_trips_on_the_next_successful_one(self):
        self.serve()
        await self.start()
        trip = speed_trip(stamp(60), 40)

        def get(path):
            if "/status/data?" in path:
                raise AuthError("expired")
            return {"trips": [trip]} if "/speed?" in path else None
        self.client.get = AsyncMock(side_effect=get)
        self.coordinator.trips.cache.clear()
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        self.assertEqual(self.fired, [])
        self.serve(speed=[trip])
        await self.poll()
        self.assertEqual(self.history(), [("speed", trip["triggerStartTime"], 40)])

    async def test_a_car_added_later_gets_its_entity_and_trips(self):
        self.serve()
        await self.start()
        other = CAR | {"vin": "SYNTHETIC-CAR-002", "nickname": "Second"}
        trip = speed_trip(stamp(60), 25)
        self.serve(speed=[trip], vehicles=(CAR, other))
        await self.poll()
        self.assertEqual(sorted(state.entity_id for state in self.fired),
                         ["event.example_ev_trip", "event.second_trip"])

    async def test_removing_the_entry_forgets_fired_trips(self):
        self.serve(speed=[speed_trip(stamp(60), 25)])
        await self.start()
        self.assertTrue(await fired_store(self.hass, self.entry.entry_id).async_load())
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await fired_store(self.hass, self.entry.entry_id).async_load())
