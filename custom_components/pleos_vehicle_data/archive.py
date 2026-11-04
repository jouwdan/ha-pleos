"""Optional archive of raw API payloads for export and troubleshooting.

Records are appended as JSON lines to gzip files, one file per UTC day:
<config>/pleos_vehicle_data/archive/<account>/<YYYY-MM>/<YYYY-MM-DD>.jsonl.gz

Unchanged responses are not written again: a snapshot or vehicle list is archived
when its content differs from the last archived copy, and each trip is archived once
per distinct content. Nothing is ever deleted.
"""

import gzip
import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import DOMAIN, TRIP_ENDPOINTS
from .data import reported_age

LOGGER = logging.getLogger(__name__)
SCHEMA = 1
# Trip contents are remembered this long; the API never returns trips older than its query window.
TRIP_HASH_DAYS = 10


class ArchiveHashes(TypedDict):
    snapshots: dict[str, str]
    trips: dict[str, dict[str, str]]
    vehicles: NotRequired[str]


class ArchiveRecord(TypedDict, total=False):
    schema: int
    kind: str
    fetched_at: str
    endpoint: str
    vehicle_id: str
    query: dict[str, str]
    api_metadata: dict[str, Any]
    data: Any
    trip: Any
    response: dict[str, Any]


class ArchiveStatus(TypedDict):
    state: str
    last_written_at: str | None
    records_last_poll: int
    records_written: int
    last_error: str | None


def archive_store(hass: HomeAssistant, entry_id: str) -> Store[ArchiveHashes]:
    """Content hashes of what was last archived, so restarts do not write duplicates."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.archive")


@callback
def async_clear_archive_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Clear only this account's archive issue after recovery or explicit cleanup."""
    ir.async_delete_issue(hass, DOMAIN, f"archive_write_failed_{entry_id}")


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_records(directory: Path, records: list[ArchiveRecord]) -> None:
    """Append records to their day files. Runs in the executor."""
    by_day: dict[str, list[ArchiveRecord]] = {}
    for record in records:
        by_day.setdefault(record["fetched_at"][:10], []).append(record)
    for day, day_records in by_day.items():
        path = directory / day[:7] / f"{day}.jsonl.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Appending adds a gzip member; gzip readers, zcat and zgrep read the file as one stream.
        with gzip.open(path, "at", encoding="utf-8") as file:
            for record in day_records:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


class ResponseArchive:
    def __init__(self, hass: HomeAssistant, entry_id: str, account: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.directory = Path(hass.config.path(DOMAIN, "archive", account))
        self.store = archive_store(hass, entry_id)
        # {"snapshots": {"<car>/<endpoint>": hash}, "trips": {"<car>/<endpoint>": {start: hash}}, "vehicles": hash}
        self.hashes: ArchiveHashes | None = None
        self.pending: list[ArchiveRecord] = []
        self.staged: list[tuple[str, str | None, str | None, str]] = []  # Hash updates applied once their records are written.
        self.status: ArchiveStatus = {"state": "ok", "last_written_at": None, "records_last_poll": 0,
                       "records_written": 0, "last_error": None}

    async def load(self) -> None:
        if self.hashes is None:
            stored = await self.store.async_load()
            self.hashes = cast(ArchiveHashes, stored if isinstance(stored, dict) else {})
            self.hashes.setdefault("snapshots", {})
            self.hashes.setdefault("trips", {})

    def begin(self) -> None:
        self.pending, self.staged = [], []

    def _record(
        self, kind: str, fetched_at: datetime, endpoint: str, car_id: str | None = None,
        query: dict[str, str] | None = None, api_metadata: dict[str, Any] | None = None, **content: Any,
    ) -> None:
        record: ArchiveRecord = {"schema": SCHEMA, "kind": kind, "fetched_at": fetched_at.isoformat(), "endpoint": endpoint}
        if car_id:
            record["vehicle_id"] = car_id
        if query:
            record["query"] = query
        if api_metadata:
            record["api_metadata"] = api_metadata
        self.pending.append(cast(ArchiveRecord, record | content))

    def add_vehicles(self, fetched_at: datetime, vehicles: list[dict[str, Any]], api_metadata: dict[str, Any]) -> None:
        digest = content_hash(vehicles)
        if digest != cast(ArchiveHashes, self.hashes).get("vehicles"):
            self._record("vehicles", fetched_at, "vehicles/consent", api_metadata=api_metadata, data=vehicles)
            self.staged.append(("vehicles", None, None, digest))

    def add_response(
        self, car_id: str, endpoint: str, fetched_at: datetime, query: dict[str, str] | None,
        api_metadata: dict[str, Any] | None, data: Any,
    ) -> None:
        hashes = cast(ArchiveHashes, self.hashes)
        key = f"{car_id}/{endpoint}"
        if endpoint not in TRIP_ENDPOINTS:
            digest = content_hash(data)
            if digest != hashes["snapshots"].get(key):
                self._record("snapshot", fetched_at, endpoint, car_id, None, api_metadata, data=data)
                self.staged.append(("snapshots", key, None, digest))
            return
        trips = data.get("trips") if isinstance(data, dict) else None
        if not isinstance(trips, list):
            # Keep an unexpected trip response whole rather than lose it.
            digest = content_hash(data)
            if digest != hashes["trips"].get(key, {}).get("response"):
                self._record("trip_response", fetched_at, endpoint, car_id, query, api_metadata, data=data)
                self.staged.append(("trips", key, "response", digest))
            return
        # Fields next to "trips" are kept with each trip so every record stands on its own.
        context = {name: value for name, value in data.items() if name != "trips"}
        known = hashes["trips"].get(key, {})
        for trip in trips:
            digest = content_hash(trip)
            start = trip.get("triggerStartTime") if isinstance(trip, dict) else None
            slot = start if isinstance(start, str) else digest
            if known.get(slot) != digest:
                extra = {"response": context} if context else {}
                self._record("trip", fetched_at, endpoint, car_id, query, api_metadata, trip=trip, **extra)
                self.staged.append(("trips", key, slot, digest))

    async def flush(self) -> None:
        """Write this poll's records; remember them only once they are on disk."""
        hashes = cast(ArchiveHashes, self.hashes)
        records, self.pending = self.pending, []
        self.status["records_last_poll"] = len(records)
        if records:
            try:
                await self.hass.async_add_executor_job(write_records, self.directory, records)
            except OSError as error:
                # Unwritten records are found again on the next poll, because their hashes were not stored.
                self.status.update({"state": "error", "last_error": f"{type(error).__name__}: {error.strerror or error}"})
                LOGGER.warning("Could not write the Pleos response archive in %s: %s", self.directory, error)
                ir.async_create_issue(
                    self.hass, DOMAIN, f"archive_write_failed_{self.entry_id}",
                    is_fixable=False, is_persistent=True, severity=ir.IssueSeverity.WARNING,
                    translation_key="archive_write_failed",
                    translation_placeholders={"directory": str(self.directory)},
                    learn_more_url="https://github.com/jouwdan/ha-pleos/blob/main/docs/troubleshooting.md#archive-storage-problems",
                )
                self.staged = []
                return
            async_clear_archive_issue(self.hass, self.entry_id)
            self.status.update({"state": "ok", "last_error": None,
                                "records_written": self.status["records_written"] + len(records),
                                "last_written_at": records[-1]["fetched_at"]})
        changed = bool(self.staged)
        for group, key, slot, digest in self.staged:
            if group == "vehicles":
                hashes["vehicles"] = digest
            elif group == "snapshots":
                hashes["snapshots"][cast(str, key)] = digest
            else:
                hashes["trips"].setdefault(cast(str, key), {})[cast(str, slot)] = digest
        self.staged = []
        changed |= self._prune_trip_hashes()
        if changed:
            await self.store.async_save(hashes)

    def _prune_trip_hashes(self) -> bool:
        hashes = cast(ArchiveHashes, self.hashes)
        limit = timedelta(days=TRIP_HASH_DAYS).total_seconds()
        changed = False
        for key in list(hashes["trips"]):
            slots = hashes["trips"][key]
            for slot in list(slots):
                # Slots named by a start time expire; content-hash slots of trips without a start are kept.
                if slot != "response" and (age := reported_age(slot)) is not None and age > limit:
                    del slots[slot]
                    changed = True
            if not slots:
                del hashes["trips"][key]
                changed = True
        return changed
