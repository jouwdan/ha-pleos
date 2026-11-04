"""Trip events and the trip polling interval."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry

from custom_components.pleos_vehicle_data import async_remove_entry
from pleos_client import ApiError, AuthError
from custom_components.pleos_vehicle_data.const import DOMAIN, TRIP_CATCH_UP_DAYS, TRIP_MEMORY_DAYS
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.trips import window_store

from support import CAR, HomeAssistantTestCase, stamp


class TripPollingTests(HomeAssistantTestCase):
    async def test_new_trips_fire_one_event_each_across_polls_and_restarts(self):
        events = self.capture_trip_events()
        first = {"triggerStartTime": stamp(240), "tripLength": 2000,
                 "location": {"speed": [10, 20], "speedUnit": ["kph", "kph"]}}
        second = {"triggerStartTime": stamp(120), "tripLength": 1000, "wheel": {"speedFrontLeft": [5]}}
        self.serve_trips(second, first)
        await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual([event.data["trip_start"] for event in events], [first["triggerStartTime"], second["triggerStartTime"]])
        self.assertEqual(events[0].data["trip"], first)
        self.assertEqual(events[0].data["trip_length_ms"], 2000)
        self.assertEqual(events[0].data["endpoint"], "speed")
        self.assertEqual(events[0].data["vehicle_id"], vehicle_id("kia", CAR["vin"]))
        self.assertEqual(events[0].data["brand"], "kia")
        self.assertNotIn(CAR["vin"], repr(events[0].data))

        await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(len(events), 2)

        # A restarted coordinator reads the stored starts instead of announcing the same trips again.
        restarted = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(restarted.async_shutdown)
        await restarted._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(len(events), 2)

        third = {"triggerStartTime": stamp(10), "tripLength": 500}
        self.serve_trips(first, second, third)
        restarted.trips.cache.clear()
        await restarted._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual([event.data["trip_start"] for event in events[2:]], [third["triggerStartTime"]])

    async def test_trip_event_carries_device_id_when_registered(self):
        events = self.capture_trip_events()
        car_id = vehicle_id("kia", CAR["vin"])
        device = device_registry.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={(DOMAIN, car_id)})
        self.serve_trips({"triggerStartTime": stamp(30)})
        await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(events[0].data["device_id"], device.id)

    async def test_failed_poll_does_not_mark_trips_seen(self):
        events = self.capture_trip_events()
        trip = {"triggerStartTime": stamp(30)}
        payload = {"trips": [trip]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if "/speed?" in path
                                    else (_ for _ in ()).throw(AuthError("expired")) if "/status/data?" in path else None)
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(events, [])
        self.serve_trips(trip)
        await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(len(events), 1)

    async def test_same_start_on_another_endpoint_is_a_separate_event(self):
        events = self.capture_trip_events()
        start = stamp(30)
        payload = {"trips": [{"triggerStartTime": start}]}
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: payload if "/speed?" in path or "/driving/data?" in path else None)
        await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(sorted(event.data["endpoint"] for event in events), ["driving/data", "speed"])

    async def test_removing_entry_forgets_announced_trips(self):
        self.serve_trips({"triggerStartTime": stamp(30)})
        await self.coordinator._async_update_data()
        self.assertTrue(await self.coordinator.trips.store.async_load())
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await self.coordinator.trips.store.async_load())

    async def test_trip_endpoints_use_their_own_interval_and_keep_readings(self):
        trip = {"triggerStartTime": stamp(30), "location": {"speed": [42], "speedUnit": ["kph"]}}
        self.serve_trips(trip)
        await self.coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 10)

        # Within the trip interval only the six snapshot endpoints are queried.
        self.client.get.reset_mock()
        result = await self.coordinator._async_update_data()
        queried = [call.args[0] for call in self.client.get.await_args_list]
        self.assertEqual(len(queried), 6)
        self.assertFalse(any("?" in path for path in queried))
        car = next(iter(result["cars"].values()))
        self.assertEqual(car["readings"]["speed_location_speed"]["value"], 42)
        self.assertEqual(car["readings"]["speed_api"]["value"], "available")
        self.assertEqual(car["sources"]["speed"], trip["triggerStartTime"])
        self.assertEqual(car["readings"]["driving_data_api"]["value"], "no_data")

        # Once the interval has passed, trips are fetched again.
        for cached in self.coordinator.trips.cache.values():
            cached["fetched_at"] -= timedelta(minutes=60)
        self.client.get.reset_mock()
        await self.coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 10)

    async def test_managed_interval_ignores_legacy_options_and_new_vehicles_are_fetched_at_once(self):
        self.hass.config_entries.async_update_entry(self.entry, options={"poll_minutes": 5, "trip_poll_minutes": 5})
        coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(coordinator.async_shutdown)
        self.assertEqual(coordinator.update_interval, timedelta(minutes=15))
        self.assertEqual(coordinator.trips.interval, timedelta(minutes=60))
        self.serve_trips()
        await coordinator._async_update_data()
        for cached in coordinator.trips.cache.values():
            cached["fetched_at"] -= timedelta(minutes=5)
        other = CAR | {"vin": "SYNTHETIC-CAR-002"}
        self.client.vehicles = AsyncMock(return_value=[CAR, other])
        self.client.get.reset_mock()
        await coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 16)
        self.client.get.reset_mock()
        await coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 12)

    async def test_rate_limited_poll_does_not_refresh_trip_cache(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: (_ for _ in ()).throw(ApiError(429, "429"))
                                    if "/batteries" in path else None)
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.trips.cache, {})
        self.client.get = AsyncMock(return_value=None)
        await self.coordinator._async_update_data()
        self.assertEqual(self.client.get.await_count, 10)

    async def test_trip_events_can_be_disabled(self):
        events = self.capture_trip_events()
        self.hass.config_entries.async_update_entry(self.entry, options={"trip_events": False})
        coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(coordinator.async_shutdown)
        self.serve_trips({"triggerStartTime": stamp(30), "location": {"speed": [10], "speedUnit": ["kph"]}})
        result = await coordinator._async_update_data()
        await self.hass.async_block_till_done()
        self.assertEqual(events, [])
        self.assertIn("speed_location_speed", next(iter(result["cars"].values()))["readings"])
        self.assertIsNone(await coordinator.trips.store.async_load())


    async def test_failed_second_vehicle_does_not_commit_first_vehicles_trip_cache(self):
        self.client.vehicles = AsyncMock(return_value=[CAR, CAR | {"vin": "SYNTHETIC-CAR-002"}])
        for failure in (AuthError(), ApiError(429, "429")):
            with self.subTest(error=type(failure).__name__):
                self.client.get = AsyncMock(side_effect=[*([None] * 10), failure])
                if isinstance(failure, AuthError):
                    with self.assertRaises(ConfigEntryAuthFailed):
                        await self.coordinator._async_update_data()
                else:
                    self.assertEqual((await self.coordinator._async_update_data())["status"], "rate_limited")
                self.assertEqual(self.coordinator.trips.cache, {})

    async def test_trip_errors_retry_next_poll_but_no_data_uses_trip_interval(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        for error in (ApiError(500, "unknown"), ApiError(0, "connection_failed"), ApiError(404, "4045")):
            with self.subTest(code=error.code):
                self.coordinator.trips.cache.clear()
                def get(path):
                    if "/speed?" in path:
                        raise error
                    return None
                self.client.get = AsyncMock(side_effect=get)
                await self.coordinator._async_update_data()
                self.client.get = AsyncMock(return_value=None)
                await self.coordinator._async_update_data()
                self.assertEqual(self.client.get.await_count, 6 if error.code == "4045" else 10)

    async def test_trip_cache_drops_removed_vehicles(self):
        self.serve_trips()
        await self.coordinator._async_update_data()
        self.client.vehicles.return_value = []
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.trips.cache, {})

class TripCatchUpTests(HomeAssistantTestCase):
    def serve(self, side_effect=None):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=side_effect or (lambda path: None))

    def trip_starts(self):
        """startDate of each trip query in the last poll, by endpoint."""
        return {path.split(CAR["vin"] + "/", 1)[1].split("?")[0]: parse_qs(path.split("?", 1)[1])["startDate"][0]
                for path in (call.args[0] for call in self.client.get.await_args_list) if "?" in path}

    def day(self, offset):
        return (datetime.now(timezone.utc).date() - timedelta(days=offset)).strftime("%Y%m%d")

    async def poll(self, coordinator=None):
        coordinator = coordinator or self.coordinator
        coordinator.trips.cache.clear()
        self.client.get.reset_mock()
        return await coordinator._async_update_data()

    async def test_first_poll_reaches_back_a_week_then_yesterday_and_today(self):
        self.serve()
        await self.poll()
        self.assertEqual(set(self.trip_starts().values()), {self.day(TRIP_CATCH_UP_DAYS - 1)})
        self.assertEqual(len(self.trip_starts()), 4)
        await self.poll()
        self.assertEqual(set(self.trip_starts().values()), {self.day(1)})

    async def test_after_downtime_queries_start_at_the_last_day_fetched(self):
        car_id = vehicle_id("kia", CAR["vin"])
        await window_store(self.hass, self.entry.entry_id).async_save(
            {car_id: {"speed": self.day(4), "driving/data": self.day(30)}})
        self.serve()
        await self.poll()
        starts = self.trip_starts()
        self.assertEqual(starts["speed"], self.day(4))
        self.assertEqual(starts["driving/data"], self.day(TRIP_CATCH_UP_DAYS - 1))
        self.assertEqual(starts["powertrains/data"], self.day(TRIP_CATCH_UP_DAYS - 1))
        stored = await window_store(self.hass, self.entry.entry_id).async_load()
        self.assertEqual(set(stored[car_id].values()), {self.day(0)})

    async def test_no_data_counts_as_fetched_and_failures_do_not(self):
        car_id = vehicle_id("kia", CAR["vin"])

        def get(path):
            if "/speed?" in path:
                raise ApiError(404, "4045")
            if "/status/data?" in path:
                raise ApiError(500, "unknown")
            return None
        self.serve(get)
        await self.poll()
        window = self.coordinator.trips.window[car_id]
        self.assertEqual(window["speed"], self.day(0))
        self.assertNotIn("status/data", window)

    async def test_failed_poll_keeps_the_previous_window(self):
        def get(path):
            if "/status/data?" in path:
                raise AuthError("expired")
            return None
        self.serve(get)
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.poll()
        self.assertEqual(self.coordinator.trips.window, {})
        self.assertIsNone(await window_store(self.hass, self.entry.entry_id).async_load())

    async def test_refused_long_range_falls_back_to_two_days(self):
        def get(path):
            if "?" in path and parse_qs(path.split("?", 1)[1])["startDate"][0] < self.day(1):
                raise ApiError(400, "4000")
            return {"trips": []} if "?" in path else None
        self.serve(get)
        await self.poll()
        self.assertEqual(self.coordinator.trips.max_days, 2)
        result = await self.poll()
        self.assertEqual(set(self.trip_starts().values()), {self.day(1)})
        car = next(iter(result["cars"].values()))
        self.assertEqual(car["readings"]["speed_api"]["value"], "no_data")
        self.assertEqual(car["readings"]["speed_api"]["attributes"]["query"]["startDate"], self.day(1))

    async def test_other_failures_do_not_shorten_the_range(self):
        for error in (ApiError(0, "connection_failed"), ApiError(500, "unknown"), ApiError(400, "4000")):
            with self.subTest(status=error.status):
                self.coordinator.trips.max_days = TRIP_CATCH_UP_DAYS
                # A 400 that also fails on two days is not about the range.
                self.serve(lambda path, error=error: (_ for _ in ()).throw(error) if "?" in path else None)
                await self.poll()
                self.assertEqual(self.coordinator.trips.max_days, TRIP_CATCH_UP_DAYS)

    async def test_removing_the_entry_forgets_the_window(self):
        self.serve()
        await self.poll()
        self.assertIsNotNone(await window_store(self.hass, self.entry.entry_id).async_load())
        await async_remove_entry(self.hass, self.entry)
        self.assertIsNone(await window_store(self.hass, self.entry.entry_id).async_load())

    async def test_trips_from_the_whole_window_are_announced_once(self):
        self.assertGreater(TRIP_MEMORY_DAYS, TRIP_CATCH_UP_DAYS)
        events = self.capture_trip_events()
        old = {"triggerStartTime": stamp(60 * 24 * (TRIP_CATCH_UP_DAYS - 1))}
        self.serve_trips(old)
        await self.poll()
        await self.poll()
        await self.hass.async_block_till_done()
        self.assertEqual([event.data["trip_start"] for event in events], [old["triggerStartTime"]])


if __name__ == "__main__":
    unittest.main()
