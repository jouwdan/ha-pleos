"""The lossless response archive."""

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry

from custom_components.pleos_vehicle_data import async_remove_entry
from custom_components.pleos_vehicle_data.archive import TRIP_HASH_DAYS, write_records
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.sensor import ArchiveSensor, async_setup_entry

from support import CAR, HomeAssistantTestCase, stamp


def read_archive(directory):
    records = []
    for path in sorted(Path(directory).rglob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as file:
            records.extend(json.loads(line) for line in file)
    return records


class WriteRecordsTests(unittest.TestCase):
    def test_records_go_to_day_files_and_appends_stay_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_records(directory, [{"fetched_at": "2026-10-14T23:59:00+00:00", "n": 1},
                                      {"fetched_at": "2026-10-15T00:01:00+00:00", "n": 2}])
            write_records(directory, [{"fetched_at": "2026-10-14T23:59:30+00:00", "n": 3}])
            self.assertEqual(sorted(str(p.relative_to(directory)) for p in directory.rglob("*.gz")),
                             ["2026-10/2026-10-14.jsonl.gz", "2026-10/2026-10-15.jsonl.gz"])
            self.assertEqual([record["n"] for record in read_archive(directory)], [1, 3, 2])


class ArchiveTests(HomeAssistantTestCase):
    def serve(self, snapshot=None, trips=None):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.response_meta = {"/v1/vehicles/consent": {"requestId": "r1"}}
        snapshot = snapshot or {"timestamp": "2026-10-14T10:00:00Z", "charge": {"stateOfCharge": 50}}

        def get(path):
            if "/batteries" in path:
                return snapshot
            if "/speed?" in path:
                return trips if trips is not None else {"trips": []}
            return None
        self.client.get = AsyncMock(side_effect=get)

    def records(self, kind=None):
        return [record for record in read_archive(self.coordinator.archive.directory)
                if kind is None or record["kind"] == kind]

    async def test_snapshots_are_written_once_until_they_change(self):
        self.serve()
        await self.coordinator._async_update_data()
        await self.coordinator._async_update_data()
        snapshots = [r for r in self.records("snapshot") if r["endpoint"] == "batteries"]
        self.assertEqual(len(snapshots), 1)
        record = snapshots[0]
        self.assertEqual(record["data"], {"timestamp": "2026-10-14T10:00:00Z", "charge": {"stateOfCharge": 50}})
        self.assertEqual((record["schema"], record["vehicle_id"]), (1, vehicle_id("kia", CAR["vin"])))
        self.assertIn("fetched_at", record)
        # Endpoints that returned nothing are archived once too, so "no data" is part of the record.
        self.assertEqual(len([r for r in self.records("snapshot") if r["endpoint"] == "status"]), 1)
        self.assertEqual(len(self.records("vehicles")), 1)
        self.assertEqual(self.records("vehicles")[0]["data"], [CAR])
        self.assertEqual(self.records("vehicles")[0]["api_metadata"], {"requestId": "r1"})

        self.serve({"timestamp": "2026-10-14T11:00:00Z", "charge": {"stateOfCharge": 49}})
        await self.coordinator._async_update_data()
        self.assertEqual([r["data"]["charge"]["stateOfCharge"] for r in self.records("snapshot")
                          if r["endpoint"] == "batteries"], [50, 49])

    async def test_each_trip_is_written_once_per_content_with_its_context(self):
        first = {"triggerStartTime": stamp(120), "tripLength": 1000, "location": {"speed": [1, 2]}}
        self.serve(trips={"trips": [first], "unitSystem": "metric"})
        await self.coordinator._async_update_data()
        self.coordinator.trips.cache.clear()
        second = {"triggerStartTime": stamp(30), "tripLength": 500}
        self.serve(trips={"trips": [first, second], "unitSystem": "metric"})
        await self.coordinator._async_update_data()
        trips = self.records("trip")
        self.assertEqual([r["trip"] for r in trips], [first, second])
        self.assertEqual(trips[0]["response"], {"unitSystem": "metric"})
        self.assertEqual(trips[0]["endpoint"], "speed")
        self.assertIn("startDate", trips[0]["query"])

        # A trip whose samples were completed later is archived again; the earlier copy stays.
        self.coordinator.trips.cache.clear()
        completed = first | {"location": {"speed": [1, 2, 3]}}
        self.serve(trips={"trips": [completed, second], "unitSystem": "metric"})
        await self.coordinator._async_update_data()
        self.assertEqual([r["trip"] for r in self.records("trip")], [first, second, completed])

    async def test_unexpected_trip_response_is_kept_whole(self):
        self.serve(trips={"unexpected": True})
        await self.coordinator._async_update_data()
        self.assertEqual([r["data"] for r in self.records("trip_response") if r["endpoint"] == "speed"],
                         [{"unexpected": True}])

    async def test_restart_does_not_write_duplicates(self):
        trip = {"triggerStartTime": stamp(60)}
        self.serve(trips={"trips": [trip]})
        await self.coordinator._async_update_data()
        count = len(self.records())
        restarted = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(restarted.async_shutdown)
        await restarted._async_update_data()
        self.assertEqual(len(self.records()), count)

    async def test_failed_write_is_reported_and_retried_on_the_next_poll(self):
        self.serve()
        with patch("custom_components.pleos_vehicle_data.archive.write_records", side_effect=PermissionError(13, "Permission denied")):
            await self.coordinator._async_update_data()
        status = self.coordinator.archive.status
        self.assertEqual((status["state"], status["last_error"]), ("error", "PermissionError: Permission denied"))
        self.assertEqual(self.records(), [])
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.archive.status["state"], "ok")
        self.assertEqual(len([r for r in self.records("snapshot") if r["endpoint"] == "batteries"]), 1)

    async def test_old_trip_hashes_are_pruned_but_files_kept(self):
        old = {"triggerStartTime": stamp(60 * 24 * (TRIP_HASH_DAYS + 1))}
        self.serve(trips={"trips": [old]})
        await self.coordinator._async_update_data()
        self.assertEqual(len(self.records("trip")), 1)
        speed = self.coordinator.archive.hashes["trips"]
        self.assertNotIn(vehicle_id("kia", CAR["vin"]) + "/speed", speed)

    async def test_archive_can_be_disabled(self):
        await entity_registry.async_load(self.hass, load_empty=True)
        self.hass.config_entries.async_update_entry(self.entry, options={"archive": False})
        coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(coordinator.async_shutdown)
        self.serve()
        await coordinator._async_update_data()
        self.assertIsNone(coordinator.archive)
        self.assertFalse(Path(self.hass.config.path("pleos_vehicle_data")).exists())
        self.entry.runtime_data = coordinator
        added = []
        with patch("custom_components.pleos_vehicle_data.sensor.discover"):
            await async_setup_entry(self.hass, self.entry, added.extend)
        self.assertNotIn("ArchiveSensor", [type(entity).__name__ for entity in added])

    async def test_removing_the_entry_keeps_files_and_forgets_hashes(self):
        self.serve()
        await self.coordinator._async_update_data()
        await async_remove_entry(self.hass, self.entry)
        self.assertTrue(self.records())
        self.assertIsNone(await self.coordinator.archive.store.async_load())

    async def test_archive_sensor_reports_status(self):
        self.serve()
        await self.coordinator._async_update_data()
        sensor = ArchiveSensor(self.coordinator)
        self.assertEqual(sensor.native_value, "ok")
        attributes = sensor.extra_state_attributes
        self.assertEqual(attributes["directory"], self.hass.config.path("pleos_vehicle_data", "archive", "kia"))
        self.assertEqual(attributes["records_written_since_start"], len(self.records()))
        self.assertGreater(attributes["records_last_poll"], 0)
        self.assertEqual(datetime.fromisoformat(attributes["last_written_at"]).tzinfo, timezone.utc)
        self.assertEqual(sensor.suggested_object_id, "Archive")


if __name__ == "__main__":
    unittest.main()
