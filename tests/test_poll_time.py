"""Account polling time is independent of the car's reporting time."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.update_coordinator import UpdateFailed

from pleos_client import ApiError, AuthError
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.sensor import LastSuccessfulPollSensor, ReadingSensor, async_setup_entry

from support import CAR, HomeAssistantTestCase, stamp


class PollTimeTests(HomeAssistantTestCase):
    async def test_poll_completion_is_not_vehicle_reporting_time(self):
        sensor = LastSuccessfulPollSensor(self.coordinator)
        self.assertIsNone(sensor.native_value)
        self.assertTrue(sensor.available)
        reported = stamp(1440)
        started = datetime.now(timezone.utc)
        completed = started + timedelta(seconds=2)
        with patch('custom_components.pleos_vehicle_data.coordinator.datetime', wraps=datetime) as clock:
            clock.now.side_effect = [started, completed]
            await self.populate({'timestamp': reported, 'charge': {'stateOfCharge': 50}})
        self.assertEqual(sensor.native_value, completed)
        car_id = vehicle_id('kia', CAR['vin'])
        car = self.coordinator.data['cars'][car_id]
        reading = ReadingSensor(self.coordinator, car_id, 'batteries_charge_stateofcharge', car['readings']['batteries_charge_stateofcharge'])
        self.assertEqual(reading.extra_state_attributes['reported_at'], reported)
        self.assertTrue(reading.extra_state_attributes['data_stale'])
        self.assertEqual(car['readings']['batteries_reported_at']['value'], reported)

    async def test_failed_requests_do_not_advance_timestamp_and_recovery_does(self):
        await self.populate()
        sensor = LastSuccessfulPollSensor(self.coordinator)
        first = sensor.native_value
        for failure in (ApiError(0, 'connection_failed'), ApiError(429, '429'), ApiError(500, '5000')):
            self.coordinator.trips.cache.clear()
            self.client.get = AsyncMock(side_effect=failure)
            self.coordinator.data = await self.coordinator._async_update_data()
            self.assertEqual(sensor.native_value, first)
        for failure, expected in ((ApiError(0, 'connection_failed'), UpdateFailed), (AuthError(), ConfigEntryAuthFailed)):
            self.client.vehicles.side_effect = failure
            with self.assertRaises(expected):
                await self.coordinator._async_update_data()
            self.coordinator.last_update_success = False
            self.assertTrue(sensor.available)
            self.assertEqual(sensor.native_value, first)
        await self.populate()
        self.assertGreater(sensor.native_value, first)

    async def test_no_data_disabled_and_empty_account_are_successful_polls(self):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=ApiError(404, '4045'))
        self.coordinator.options['location'] = False
        await self.coordinator._async_update_data()
        first = self.coordinator.last_successful_poll
        self.assertIsNotNone(first)
        self.client.vehicles.return_value = []
        await self.coordinator._async_update_data()
        self.assertGreater(self.coordinator.last_successful_poll, first)

    async def test_sensor_is_registered_on_api_device_with_native_timestamp(self):
        await self.populate()
        self.entry.runtime_data = self.coordinator
        await entity_registry.async_load(self.hass, load_empty=True)
        entity_registry.async_get(self.hass).async_get_or_create(
            "sensor", DOMAIN, "kia_last_successful_poll", config_entry=self.entry,
        )
        platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain='sensor',
                                  platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15), entity_namespace=None)
        platform.config_entry = self.entry
        self.addAsyncCleanup(platform.async_reset)
        entities = []
        await async_setup_entry(self.hass, self.entry, entities.extend)
        await platform.async_add_entities(entities)
        sensor = next(entity for entity in entities if isinstance(entity, LastSuccessfulPollSensor))
        state = self.hass.states.get(sensor.entity_id)
        self.assertEqual(datetime.fromisoformat(state.state), self.coordinator.last_successful_poll.replace(microsecond=0))
        self.assertEqual(state.attributes['device_class'], 'timestamp')
        self.assertEqual(sensor.device_info['identifiers'], {(DOMAIN, 'kia_api')})
