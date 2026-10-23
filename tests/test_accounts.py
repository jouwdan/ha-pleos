"""Account isolation and stable identities through credential replacement."""

import gzip
import json
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import probatio
from homeassistant.helpers import config_validation as cv, device_registry, entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.pleos_vehicle_data.account import account_id, credential_id
from pleos_client import PleosClient
from custom_components.pleos_vehicle_data.config_flow import PleosOptionsFlow
from custom_components.pleos_vehicle_data.const import DOMAIN
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.ranges import range_store
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor, LastSuccessfulPollSensor, async_setup_entry
from custom_components.pleos_vehicle_data.statistics import statistic_id

from support import CAR, HomeAssistantTestCase, NEW, OLD, entry, stamp


class AccountTests(HomeAssistantTestCase):
    async def add_account(self):
        with patch.object(PleosClient, 'vehicles', AsyncMock(return_value=[CAR])):
            flow = self.flow()
            form = await flow.async_step_user(NEW | {'brand': 'kia'})
            created = await flow.async_step_odometer(form['data_schema']({}))
        second = entry(created['data'])
        self.hass.config_entries._entries[second.entry_id] = second
        self.hass.config_entries.async_update_entry(second, title=created['title'],
                                                    unique_id=credential_id('kia', NEW['client_id']),
                                                    options=created['options'] | {'archive': True, 'trip_events': True})
        return second

    async def test_add_same_brand_account_and_reject_duplicate_key_including_legacy(self):
        second = await self.add_account()
        self.assertEqual(second.title, 'Kia 2')
        self.assertNotEqual(account_id(second), account_id(self.entry))
        self.assertNotIn(NEW['client_id'], account_id(second))
        for data in (OLD, dict(second.data)):
            with patch.object(PleosClient, 'vehicles', AsyncMock()) as request:
                result = await self.flow().async_step_user(data | {'client_id': ' ' + data['client_id'] + ' '})
                self.assertEqual(result['reason'], 'already_configured')
                request.assert_not_called()

    async def test_replacement_preserves_namespaces_and_cannot_claim_another_accounts_key(self):
        second = await self.add_account()
        for current in (self.entry, second):
            flow = self.flow('reconfigure')
            before = account_id(current)
            car_id = vehicle_id(before, CAR['vin'])
            archive = PleosCoordinator(self.hass, current, self.client)
            folder = archive.archive.directory
            self.addAsyncCleanup(archive.async_shutdown)
            replacement = {'client_id': 'rotated-' + current.entry_id, 'client_secret': 'fixture-secret'}
            with patch.object(flow, '_get_reconfigure_entry', return_value=current), \
                 patch.object(PleosClient, 'vehicles', AsyncMock(return_value=[CAR])), \
                 patch.object(self.hass.config_entries, 'async_schedule_reload'):
                result = await flow.async_step_reconfigure(replacement)
            self.assertEqual(result['reason'], 'reconfigure_successful')
            after = PleosCoordinator(self.hass, current, self.client)
            self.addAsyncCleanup(after.async_shutdown)
            self.assertEqual(after.account_id, before)
            self.assertEqual(after.archive.directory, folder)
            self.assertEqual(vehicle_id(after.account_id, CAR['vin']), car_id)
            self.assertEqual(current.unique_id, credential_id('kia', replacement['client_id']))
        self.assertEqual(account_id(self.entry), 'kia')
        self.assertEqual(ConnectionSensor(self.coordinator).unique_id, 'kia_api_status')
        flow = self.flow('reconfigure')
        before = dict(second.data)
        with patch.object(flow, '_get_reconfigure_entry', return_value=second), \
             patch.object(PleosClient, 'vehicles', AsyncMock()) as request:
            result = await flow.async_step_reconfigure(dict(self.entry.data))
        self.assertEqual(result['reason'], 'already_configured')
        self.assertEqual(dict(second.data), before)
        request.assert_not_called()

    async def test_same_vehicle_in_two_accounts_has_separate_devices_data_storage_and_statistics(self):
        second = await self.add_account()
        client = PleosClient(MagicMock(), **NEW, brand='kia')
        other = PleosCoordinator(self.hass, second, client)
        self.addAsyncCleanup(other.async_shutdown)
        await entity_registry.async_load(self.hass, load_empty=True)
        all_entities, car_ids = [], []
        for current, coordinator, level in ((self.entry, self.coordinator, 25), (second, other, 75)):
            payload = {'timestamp': stamp(), 'charge': {'stateOfCharge': level},
                       'distanceToEmpties': [{'type': 'EV', 'value': 100 + level, 'unit': 'km'}]}
            coordinator.client.vehicles = AsyncMock(return_value=[CAR])
            coordinator.client.get = AsyncMock(return_value=payload)
            coordinator.data = await coordinator._async_update_data()
            coordinator.last_update_success = True
            car_id = next(iter(coordinator.data['cars']))
            car_ids.append(car_id)
            current.runtime_data = coordinator
            platform = EntityPlatform(hass=self.hass, logger=logging.getLogger(__name__), domain='sensor',
                                      platform_name=DOMAIN, platform=None, scan_interval=timedelta(minutes=15),
                                      entity_namespace=None)
            platform.config_entry = current
            self.addAsyncCleanup(platform.async_reset)
            entities = []
            await async_setup_entry(self.hass, current, entities.extend)
            await platform.async_add_entities(entities)
            all_entities.extend(entities)
            battery = next(e for e in entities if getattr(e, 'key', None) == 'batteries_charge_stateofcharge')
            self.assertEqual(float(self.hass.states.get(battery.entity_id).state), level)
            poll = next(e for e in entities if isinstance(e, LastSuccessfulPollSensor))
            registry_entry = entity_registry.async_get(self.hass).async_get(poll.entity_id)
            api_device = device_registry.async_get(self.hass).async_get(registry_entry.device_id)
            self.assertEqual(api_device.identifiers, {(DOMAIN, account_id(current) + '_api')})
            self.assertEqual(api_device.name, current.title + ' API')
            stored = await range_store(self.hass, current.entry_id).async_load()
            self.assertEqual(set(stored), {car_id})
            records = []
            for path in coordinator.archive.directory.rglob('*.gz'):
                with gzip.open(path, 'rt') as source:
                    records.extend(json.loads(line) for line in source)
            self.assertEqual({r['vehicle_id'] for r in records if 'vehicle_id' in r}, {car_id})
        self.assertEqual(len({e.unique_id for e in all_entities}), len(all_entities))
        self.assertEqual(len({e.entity_id for e in all_entities}), len(all_entities))
        self.assertNotEqual(car_ids[0], car_ids[1])
        self.assertNotEqual(statistic_id(car_ids[0], 'distance'), statistic_id(car_ids[1], 'distance'))
        self.assertNotEqual(self.coordinator.archive.directory, other.archive.directory)
        self.assertEqual(self.coordinator.archive.directory.name, 'kia')
        devices = device_registry.async_get(self.hass)
        self.assertNotEqual(devices.async_get_device_by_identifier((DOMAIN, car_ids[0]), self.entry.entry_id).id,
                            devices.async_get_device_by_identifier((DOMAIN, car_ids[1]), second.entry_id).id)
        # Options list only this account's car, never either API device or another account's car.
        options = PleosOptionsFlow()
        options.hass, options.handler = self.hass, second.entry_id
        form = await options.async_step_init()
        fields = probatio.to_field_list(form['data_schema'], custom_serializer=cv.custom_serializer)
        field = next(item for item in fields[0]['schema'] if item['name'] == 'odometer_km_vehicles')
        self.assertEqual([item['value'] for item in field['selector']['select']['options']], [car_ids[1]])
        # Removing a car from one account cannot alter the other's readings or saved ranges.
        successful = self.coordinator.last_successful_poll
        other.client.vehicles.return_value = []
        other.data = await other._async_update_data()
        self.assertEqual(self.coordinator.last_successful_poll, successful)
        self.assertEqual(self.coordinator.data['cars'][car_ids[0]]['readings']['batteries_charge_stateofcharge']['value'], 25)
        self.assertEqual(await range_store(self.hass, second.entry_id).async_load(), {})
        self.assertEqual(set(await range_store(self.hass, self.entry.entry_id).async_load()), {car_ids[0]})
