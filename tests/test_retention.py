"""Keep measurements, with their original age, only during transient endpoint failures."""

from copy import deepcopy
from unittest.mock import AsyncMock

from pleos_client import ApiError
from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.sensor import ReadingSensor

from support import CAR, HomeAssistantTestCase, stamp

CAR_ID = vehicle_id('kia', CAR['vin'])


class RetentionTests(HomeAssistantTestCase):
    async def seed(self):
        reported = stamp(30)
        payloads = {
            'batteries': {'charge': {'stateOfCharge': 60, 'charging': True, 'remainTime': 40,
                                     'targetStateOfCharge': {'standard': 80, 'quick': 100}}},
            'powertrains': {'engine': {'fuelLevel': 0}},
            'driving': {'odometer': {'value': 1500, 'unit': 'mile'}, 'ignition': {'status': 'on'}},
            'status': {'outsideTemperature': {'value': 15, 'unit': 'C'},
                       'tire': [{'left': 36, 'unit': 'psi'}], 'warnings': ['lowFuel'],
                       'doors': [{'left': 'locked'}]},
            'locations': {'location': {'latitude': 53, 'longitude': -6, 'altitude': 20, 'type': 0}},
            'freezer': {'freezer': {'status': 'on', 'temperature': -20}},
        }
        payloads = {key: value | {'timestamp': reported} for key, value in payloads.items()}
        self.client.vehicles = AsyncMock(return_value=[CAR | {'type': 'PE'}])
        self.client.get = AsyncMock(side_effect=lambda path: payloads.get(path.rsplit('/', 1)[-1]))
        self.coordinator.options['odometer_km_vehicles'] = [CAR_ID]
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True
        car = self.coordinator.data['cars'][CAR_ID]
        self.sensors = {key: ReadingSensor(self.coordinator, CAR_ID, key, spec)
                        for key, spec in car['readings'].items() if spec['domain'] == 'sensor'}
        return reported

    async def poll(self, response):
        self.client.get = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
        self.coordinator.data = await self.coordinator._async_update_data()
        return self.coordinator.data['cars'][CAR_ID]

    async def test_transient_failures_retain_numeric_values_and_original_age_only(self):
        reported = await self.seed()
        previous = self.coordinator.data
        original = deepcopy(previous)
        successful_poll = self.coordinator.last_successful_poll
        kept = {'batteries_charge_stateofcharge': 60,
                'batteries_charge_targetstateofcharge_standard': 80, 'batteries_charge_targetstateofcharge_quick': 100,
                'powertrains_engine_fuellevel': 0,
                'driving_odometer': 1500, 'status_outsidetemperature': 15,
                'status_tire_row1_left': 36, 'freezer_freezer_temperature': -20}
        for status in (0, 503, 429):
            with self.subTest(status=status):
                car = await self.poll(ApiError(status, 'fixture_failure'))
                for key, value in kept.items():
                    sensor = self.sensors[key]
                    self.assertFalse(sensor.available, key)
                    self.assertIsNone(sensor.native_value)
                    self.assertEqual(car['readings'][key]['value'], value)
                    self.assertEqual(sensor.extra_state_attributes['reported_at'], reported)
                    self.assertGreaterEqual(sensor.extra_state_attributes['data_age_minutes'], 30)
                    self.assertTrue(sensor.extra_state_attributes['last_known'])
                    self.assertTrue(sensor.extra_state_attributes['data_stale'])
                self.assertEqual(self.sensors['driving_odometer'].native_unit_of_measurement, 'km')
                self.assertEqual(self.sensors['driving_odometer'].extra_state_attributes['api_unit_of_measurement'], 'mi')
                for key in ('batteries_charge_charging', 'batteries_charge_remaintime', 'status_warnings',
                            'status_warning_lowfuel', 'status_doors_row1_left', 'driving_ignition_status',
                            'locations_location_altitude',
                            'freezer_freezer_status', 'batteries_reported_at', 'batteries_data_age'):
                    self.assertNotIn(key, car['readings'])
                self.assertIsNone(car['location'])
                self.assertEqual(car['readings']['batteries_api']['value'], 'error')
                self.assertNotIn('data', car['readings']['batteries_api']['attributes'])
                self.assertEqual(self.coordinator.last_successful_poll, successful_poll)
        # Retention does not mutate the previous response or write cached values as new raw data.
        self.assertEqual(previous, original)
        self.assertEqual(self.coordinator.archive.status['records_last_poll'], 0)

    async def test_recovery_replaces_value_unit_and_clears_stale_marker(self):
        await self.seed()
        await self.poll(ApiError(500, 'fixture_failure'))
        reported = stamp()
        await self.poll({'timestamp': reported, 'odometer': {'value': 1600, 'unit': 'km'}})
        sensor = self.sensors['driving_odometer']
        self.assertEqual((sensor.native_value, sensor.native_unit_of_measurement), (1600, 'km'))
        self.assertEqual(sensor.extra_state_attributes['reported_at'], reported)
        self.assertFalse(sensor.extra_state_attributes.get('last_known', False))
        self.assertFalse(sensor.extra_state_attributes['data_stale'])
        self.assertNotIn('api_unit_of_measurement', sensor.extra_state_attributes)

    async def test_no_retention_for_permissions_no_data_missing_invalid_or_undated_values(self):
        for response in (ApiError(403, '403'), ApiError(404, '4045'), None,
                         {'timestamp': stamp(), 'charge': {'stateOfCharge': 101}}):
            with self.subTest(response=response):
                await self.seed()
                await self.poll(response)
                sensor = self.sensors['batteries_charge_stateofcharge']
                self.assertEqual(sensor.available, not isinstance(response, ApiError) or response.code == '4045')
                self.assertIsNone(sensor.native_value)
        await self.seed()
        self.coordinator.data['cars'][CAR_ID]['sources']['batteries'] = None
        car = await self.poll(ApiError(503, 'fixture_failure'))
        self.assertNotIn('batteries_charge_stateofcharge', car['readings'])

    async def test_measurement_retention_ends_on_reload_and_does_not_cross_vehicles(self):
        await self.seed()
        self.client.vehicles.return_value = [CAR | {'vin': 'SYNTHETIC-CAR-002'}]
        self.client.get = AsyncMock(side_effect=ApiError(503, 'fixture_failure'))
        result = await self.coordinator._async_update_data()
        self.assertNotIn('batteries_charge_stateofcharge', next(iter(result['cars'].values()))['readings'])
        self.client.vehicles.return_value = [CAR]
        await self.coordinator.async_shutdown()
        self.coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        car = await self.poll(ApiError(503, 'fixture_failure'))
        self.assertNotIn('batteries_charge_stateofcharge', car['readings'])
