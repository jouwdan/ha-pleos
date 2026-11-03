"""The refresh service."""

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from pleos_client import ApiError

from custom_components.pleos_vehicle_data.const import DOMAIN, SERVICE_REFRESH
from custom_components.pleos_vehicle_data.services import async_setup_services

from support import CAR, HomeAssistantTestCase, stamp


class ServiceTests(HomeAssistantTestCase):
    def setup_service(self, state=ConfigEntryState.LOADED):
        async_setup_services(self.hass)
        self.entry.runtime_data = self.coordinator
        self.entry._async_set_state(self.hass, state, None)

    async def call(self, entry_id):
        await self.hass.services.async_call(
            DOMAIN, SERVICE_REFRESH, {"config_entry_id": entry_id}, blocking=True
        )

    async def test_service_is_registered(self):
        self.setup_service()
        self.assertTrue(self.hass.services.has_service(DOMAIN, SERVICE_REFRESH))

    async def test_refresh_updates_the_coordinator(self):
        self.setup_service()
        with patch.object(self.coordinator, "async_refresh_for_service", new_callable=AsyncMock) as refresh:
            await self.call(self.entry.entry_id)
        refresh.assert_awaited_once_with()

    async def test_concurrent_requests_batch_account_polls(self):
        self.setup_service()
        self.client.vehicles = AsyncMock(return_value=[])
        await asyncio.gather(*(self.call(self.entry.entry_id) for _ in range(5)))
        self.client.vehicles.assert_awaited_once_with()
        self.assertEqual(self.coordinator.data, {"status": "no_vehicles", "cars": {}})

    async def test_refresh_reports_account_failure(self):
        self.setup_service()
        self.client.vehicles = AsyncMock(side_effect=ApiError(503, "unavailable"))
        with self.assertRaises(HomeAssistantError) as raised:
            await self.call(self.entry.entry_id)
        self.assertEqual(raised.exception.translation_key, "refresh_failed")
        self.assertFalse(self.coordinator.last_update_success)

    async def test_refresh_reports_partial_endpoint_failure(self):
        self.setup_service()
        self.client.vehicles = AsyncMock(return_value=[CAR])

        def response(path):
            if path.endswith("/batteries"):
                raise ApiError(503, "unavailable")
            return {"timestamp": stamp(), "odometer": {"value": 123, "unit": "km"}}

        self.client.get = AsyncMock(side_effect=response)
        with self.assertRaises(HomeAssistantError) as raised:
            await self.call(self.entry.entry_id)
        self.assertEqual(raised.exception.translation_key, "refresh_failed")
        self.assertTrue(self.coordinator.last_update_success)
        car = next(iter(self.coordinator.data["cars"].values()))
        self.assertEqual(car["readings"]["driving_odometer"]["value"], 123)

    async def test_deferred_refresh_uses_its_own_outcome(self):
        self.setup_service()
        self.coordinator._debounced_refresh.cooldown = 0.01
        self.client.vehicles = AsyncMock(return_value=[])
        await self.call(self.entry.entry_id)
        self.client.vehicles.side_effect = ApiError(503, "unavailable")
        with self.assertRaises(HomeAssistantError):
            await asyncio.wait_for(self.call(self.entry.entry_id), 1)
        self.assertEqual(self.client.vehicles.await_count, 2)
        self.client.vehicles.side_effect = None
        await asyncio.wait_for(self.call(self.entry.entry_id), 1)
        self.assertEqual(self.client.vehicles.await_count, 3)

    async def test_request_joins_inflight_poll_and_cancellation_is_isolated(self):
        self.setup_service()
        started, release = asyncio.Event(), asyncio.Event()

        async def vehicles():
            started.set()
            await release.wait()
            return []

        self.client.vehicles = AsyncMock(side_effect=vehicles)
        first = asyncio.create_task(self.coordinator.async_refresh_for_service())
        await started.wait()
        second = asyncio.create_task(self.coordinator.async_refresh_for_service())
        await asyncio.sleep(0)
        self.assertEqual(len(self.coordinator._refresh_waiters), 2)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(second.done())
        release.set()
        await asyncio.wait_for(second, 1)
        self.client.vehicles.assert_awaited_once_with()
        self.assertEqual(self.coordinator.data["cars"], {})
        self.assertFalse(self.coordinator._refresh_waiters)

    async def test_unload_releases_a_deferred_refresh(self):
        self.setup_service()
        self.client.vehicles = AsyncMock(return_value=[])
        await self.call(self.entry.entry_id)
        pending = asyncio.create_task(self.coordinator.async_refresh_for_service())
        await asyncio.sleep(0)
        await self.coordinator.async_shutdown()
        with self.assertRaises(HomeAssistantError) as raised:
            await asyncio.wait_for(pending, 1)
        self.assertEqual(raised.exception.translation_key, "refresh_cancelled")
        self.assertFalse(self.coordinator._refresh_waiters)
        with self.assertRaises(HomeAssistantError) as raised:
            await self.coordinator.async_refresh_for_service()
        self.assertEqual(raised.exception.translation_key, "refresh_cancelled")

    async def test_unknown_entry_is_rejected(self):
        self.setup_service()
        with self.assertRaises(ServiceValidationError) as raised:
            await self.call("missing")
        self.assertEqual(raised.exception.translation_key, "entry_not_found")

    async def test_unloaded_entry_is_rejected(self):
        self.setup_service(ConfigEntryState.NOT_LOADED)
        with self.assertRaises(ServiceValidationError) as raised:
            await self.call(self.entry.entry_id)
        self.assertEqual(raised.exception.translation_key, "entry_not_loaded")

    async def test_other_domain_entry_is_rejected(self):
        self.setup_service()
        other = type(self.entry)(
            data={},
            domain="other",
            title="Other",
            unique_id="other",
            version=1,
            minor_version=1,
            source="user",
            options={},
            discovery_keys={},
            subentries_data=[],
        )
        self.hass.config_entries._entries[other.entry_id] = other
        with self.assertRaises(ServiceValidationError) as raised:
            await self.call(other.entry_id)
        self.assertEqual(raised.exception.translation_key, "entry_not_found")

    async def test_entry_id_is_required(self):
        self.setup_service()
        with self.assertRaises(Exception):
            await self.hass.services.async_call(
                DOMAIN, SERVICE_REFRESH, {}, blocking=True
            )
