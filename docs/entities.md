# Entities and vehicle data

[Back to setup guide](../README.md)

## Entities and devices

Each account entry creates an **API device** (for example *Kia API* or *Kia 2 API*) and one device per car.

| Device | Entity | What it shows |
| --- | --- | --- |
| API device | **Connection** | `connected`, `no_vehicles` (the key works but grants no car; see [No vehicles](troubleshooting.md#no-vehicles)), `key_required` (replace the personal key), `api_unavailable` (Pleos could not be reached) or `rate_limited` (Pleos asked to slow down; requests pause until it allows them again) |
| API device | **Archive** | Only when the archive is enabled: `ok` or `error`, with the folder, last write and last error |
| API device | **Last successful poll** | When Home Assistant last completed this account's poll without request errors. This is the fetch completion time, not when the car uploaded its data. |
| Car | Readings | One sensor or binary sensor per value the API returns, for example **Battery level**, **Odometer**, **Front left door** |
| Car | **(last trip)** sensors | The latest trip's final sample per trip field, with [trip summaries](trips.md#trip-readings) |
| Car | **Trip** | Event entity firing once per trip; see [Trip event entity](trips.md#trip-event-entity) |
| Car | **Location** | GPS tracker, when location is enabled and the car reports valid WGS84 coordinates |
| Car | **Vehicle information** | Diagnostic: powertrain type, with VIN, model and every other Get Vehicles field as attributes |
| Car | ***Endpoint* API** | Diagnostic, one per endpoint: `available`, `no_data`, `error` or `disabled`, with the raw response, query and rejected values (see [API coverage](#api-coverage)) |
| Car | ***Endpoint* last reported**, ***Endpoint* data age** | Diagnostic, per status endpoint: when the car last reported that data and how old it is |

**Last successful poll** advances only when the vehicle list and all endpoints requested in that poll finish without errors. Disabled endpoints, trip data reused between scheduled trip polls, and an API `no_data` result do not count as failures. Authentication, connection, rate-limit and individual endpoint errors leave the timestamp unchanged. It remains visible during failures and starts unknown after setup/reload until a poll succeeds. Check each car's **last reported** and **data age** sensors for the age of the actual vehicle readings; a successful poll can return old data.

## Native entity types

| Readings | Home Assistant representation |
| --- | --- |
| Actual charge percentages | `battery` measurement sensors, % |
| AC/DC charge limits; fuel level | Percentage sensors; target icons for charge limits and fuel-pump icon for fuel |
| Plug connection; charging | `plug` and `battery_charging` binary sensors |
| Bonnet, boot; sunroof | `opening` and `window` binary sensors |
| Ignition and ignition/accessory power | `power` binary sensors |
| Climate/freezer operation | `running` binary sensors |
| Sleep mode, in park, defrost, steering-wheel heating | On/off binary sensors with matching icons |
| Doors, seat levels, climate mode, fan level, charging status | Enum sensors preserving the documented states |
| Key battery warning | `battery` binary sensor: Low / Normal |
| Other individual warnings | `problem` binary sensors; fuel and engine-oil warning entities omitted for pure EVs |
| Ambient, freezer and oil temperatures; cabin target temperature | `temperature` sensors; the cabin target is a setting, not a measurement |
| Tyre pressures; battery voltage | `pressure` and `voltage` sensors with validated units |
| Odometer | `distance` sensor with `total` state class for lifetime mileage |
| Remaining ranges; altitude | `distance` sensors; ranges are estimates |
| Speed and wheel speeds | `speed` sensors |
| Charge time remaining; trip duration | `duration` sensors |
| GPS location; latitude/longitude; heading | GPS tracker; degree-valued coordinates; angle measurement for heading |
| Coordinate system; trip speed/pressure unit | Diagnostic enums |
| Trip ignition, doors and low-fuel warning | Historical `power`, `door` and `problem` binary sensors |
| Trip gear, belts, ISLA and light settings | Historical enums |
| RPM, acceleration, steering angle and angular rates | Numeric sensors with their documented units; HA has no matching device class for these units |
| Battery capacity | `energy_storage` sensor in kWh, converted from the API’s kJ value |
| Estimated EV consumption | `energy_distance` measurement sensor; kWh/100 km or mi/kWh by default |
| API connection/endpoint/archive status; reporting times | Diagnostic enums and timestamp sensors |
| Vehicle information; data age | Diagnostic text/attributes and minutes |
| Undocumented fields | Plain readings until their meaning and units are documented |

These remain read-only entities. The API does not support operating locks, windows, heating or fans, so they are not exposed as controllable switches, covers, locks, fans or climate entities.

**Battery level** is the primary charge percentage. When it has a valid reported timestamp, the separate **Traction battery level** reading from the powertrain endpoint appears under **Diagnostics**. If Battery level is missing or invalid, Traction battery level returns to the main sensor section. Both keep their own values, source timestamps, entity IDs and history; neither is a 12 V battery reading. Retained readings still follow the freshness rules below.

Pure EVs do not get **Low fuel warning** or **Engine oil warning** entities. Hybrid, combustion and unknown vehicle types keep them; **Brake fluid warning** remains relevant to EVs. The **Warnings** sensor and raw API diagnostics always preserve the complete reported warning list, including unexpected codes. **Key battery warning** displays Low when reported and Normal when absent from a valid list; missing or invalid warning data remains unavailable.

## Optional address sensor

Enable **Reverse geocode vehicle location** and configure a Nominatim-compatible service under the account's options to add an **Address** sensor. GPS/location must also be enabled. This sends rounded vehicle coordinates to your chosen provider; it is off by default and has no default provider.

The sensor uses the location report's timestamp and freshness. Lookup failures produce an unknown address with a `geocoding_status` attribute; missing or invalid location timestamps make it unavailable. Addresses longer than Home Assistant's 255-character state limit are shortened in the state, with the complete text in `full_address`. Provider attribution is shown when returned. Addresses, the configured URL and API keys are redacted from downloaded diagnostics. Address history can still be recorded locally by Home Assistant.

## Estimated EV consumption

Pure electric vehicles (`EV`) gain **Estimated EV consumption** once matching battery and odometer reports are available. No extra API calls or manual capacity setting are needed. Home Assistant defaults to **mi/kWh** when its distance unit is miles, otherwise **kWh/100 km**. The sensor’s settings can override this with **Wh/km**, **km/kWh**, **mi/kWh** or **kWh/100 km**; values convert along with the unit.

The estimate uses the drop in battery percentage, reported battery capacity and distance travelled over an observation window:

`kWh/100 km = battery capacity (kWh) × percentage-point drop ÷ distance (km)`

For example, a 71.2 kWh battery dropping from 80% to 70% over 50 km gives **14.24 kWh/100 km**, or about **4.36 mi/kWh**. The existing **Battery capacity** sensor now converts the API value from kJ to kWh: a PV5 Passenger report of `256320` becomes `71.2 kWh`. This conversion is supported by that vehicle report; Pleos's [capacity field documentation](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-battery-charging-status) currently omits the unit. Raw diagnostics and archives retain the original number.

To reduce percentage-rounding noise, consumption stays **unknown** until the window covers at least **10 km and a 5-percentage-point drop**. This is an average across observed reports, not an instantaneous or exact last-trip measurement. Its attributes include `window_started_at`, `reported_at`, `distance_km`, `energy_used_kwh`, `state_of_charge_drop`, `battery_capacity_kwh` and `estimated: true`.

Only battery and odometer reports with the **same vehicle timestamp** are paired. The car must report disconnected, not charging, with valid capacity, battery level and odometer. New reports older than the configured stale-data threshold are excluded. Missing, mismatched or retained inputs make the sensor unavailable and clear its observation window; fresh matching reports start again. An unchanged parked report can keep the existing estimate, with the normal data-age attributes. Out-of-order reports are ignored. An initial account request failure follows the normal retained-data policy below.

Charging/plug connection, V2L/V2X or unknown charging status clears the window. A battery-level increase, capacity change, odometer decrease or impossible jump, battery loss without distance, or a gap of more than 24 hours between matching reports starts a new window. The window survives normal restarts and is removed with its vehicle or integration entry. After a reset, allow another 10 km and 5-point drop before expecting a number.

This remains an estimate: battery ageing and usable versus reported capacity, rounded percentages, heating while parked, and charging or V2L missed between reports can affect it. It does not measure charger energy, charging losses or regenerated energy. Fuel/hybrid economy is not calculated.

## Data freshness and retained readings

Status endpoints have reporting-time and data-age sensors when the vehicle supplies a valid timestamp. Readings stay visible while the car is parked; check `reported_at`, `data_age_minutes` and `data_stale` for their age. Turning off location in the [options](configuration.md#options) disables both location requests and GPS tracking.

If the initial account request fails with a connection error, rate limit or server error, existing vehicle readings stay visible until a poll succeeds. This includes location, status and diagnostic readings from the last completed poll. They get `last_known: true` and `data_stale: true`, keeping their original values, units and reporting times. The **Connection** sensor still reports the failure, **Last successful poll** does not advance, and downloaded diagnostics show `last_update_success: false` and `using_last_data: true`. Authentication, permission, invalid-response and unexpected failures still make readings unavailable; a successful poll is required before retention can resume.

During connection failures, rate limits and server errors on an individual endpoint, the last valid battery levels, charge limits, fuel level, odometer, temperatures and tyre pressures remain visible with `last_known: true` and `data_stale: true`. Their age and `reported_at` still refer to the original vehicle reading, and endpoint diagnostics continue to show the error. Successful responses replace these values and clear the retention marker. Permission errors, `no_data`, and missing or invalid fields in successful responses do not retain these measurements. Locks, doors, warnings, charging/ignition status, charge time remaining and location are not retained.

These readings are retained only in memory, until a reload or restart; [driving ranges](#driving-ranges) have separate persistent restoration. Retained values can remain indefinitely during an outage, so automations needing current data must check `data_stale`, including before using a retained location, lock or charging state.

Entities use names such as **AC charge limit** and **Cabin fan speed**; historical readings include **(last trip)**. Name changes preserve existing IDs and customised names. Documented fields support Home Assistant translations, with English included. New IDs use the English name regardless of your display language. Undocumented fields use names derived from their API paths.

## Units

Status readings use the unit returned by the API. For example, the [odometer response](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-status) can report `km`, `mile`, `meter` or `feet`; these become Home Assistant's `km`, `mi`, `m` or `ft`. Trip odometer samples are documented in kilometres.

Some vehicles return a kilometre odometer value labelled `mile` ([#6](https://github.com/jouwdan/ha-pleos/issues/6)). If **Driving status API → data.odometer** contains `5177.5` with unit `mile`, but the car shows about `3217` miles, select that car under **Configure → Vehicle status → Correct odometer kilometres labelled as miles**. Home Assistant will interpret the value as `5177.5 km` and display about `3217.15 mi` when miles are selected. Raw API diagnostics and archives stay unchanged; the corrected Odometer entity includes `api_unit_of_measurement: mi`.

Leave this option empty for correctly reported odometers. It applies only to the selected cars' snapshot odometers labelled miles; trip data and other units are unaffected. Clear the selection if Pleos fixes the underlying data. Existing history and statistics are not rewritten. A correction changes the recorded total, so check **Developer tools → Statistics** for affected odometer totals after enabling it.

Home Assistant converts readings for your unit preferences. To display miles, open the odometer entity's settings and set **Unit of measurement** to **mi**. This converts both the number and label: **1,609.344 km = 1,000 mi**. If a reading differs from the car or app, compare its value and unit with `data.odometer` on the **Driving status API** sensor and include both in an issue report.

## Driving ranges

Range readings accept both `mile` and `miles`, alongside `km`, `meter` and `feet`. Home Assistant converts their values for your selected display unit.

When the API returns several ranges with the same type, each has its own sensor. The existing unnumbered sensor shows the first entry of that type; subsequent entries are named **Plug-in hybrid range 2**, **Plug-in hybrid range 3**, and so on. Previously, the last entry overwrote the earlier ones. Numbering follows the API's order, including entries whose values are missing or invalid; it does not identify petrol versus electric range. If the API changes the order, the numbered readings follow that change.

The last valid range and its native unit are saved per vehicle and sensor, including across restarts. If a later response omits it, contains a dash or an invalid value, lacks a valid timestamp, or the powertrain request fails, the saved range stays visible with `last_known: true` and `data_stale: true`. Its `reported_at` and `data_age_minutes` refer to the original reading, not the latest poll. A new valid reading clears `last_known`; the usual stale-data threshold still applies. Raw API diagnostics and archives remain unchanged, and the **Powertrain status API** sensor still reports request errors.

Saved ranges can remain visible indefinitely while the vehicle is still on the account. Automations needing current data should check `data_stale`. Transient account failures retain ranges already loaded in memory; after a reload, the initial account request must succeed before saved ranges can be restored. Authentication and permission failures still make vehicle readings unavailable. Removing a vehicle from the API's vehicle list clears its saved ranges; removing the integration clears this saved data too.

## API coverage

All ten common Vehicle Data API endpoints are queried. Fields appear when the API supplies valid values for the vehicle.

| API | Endpoint suffix | Exposed data |
| --- | --- | --- |
| [Battery charging](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-battery-charging-status) | `batteries` | Plug connection, charging state, charge percentage, capacity, charge targets and remaining charge time |
| [Powertrain status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-powertrain-status) | `powertrains` | Fuel level, battery percentage and remaining range |
| [Location](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-location-info) | `locations` | Coordinates, coordinate system, altitude, heading and speed; GPS tracker for valid WGS84 coordinates |
| [Driving status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-status) | `driving` | Ignition, power states, sleep mode, odometer and parking position |
| [Vehicle status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-status) | `status` | Doors, bonnet, boot, sunroof, tyres, warnings (full list and count as attributes), climate, defrost, steering-wheel heat, seats and outside temperature |
| [Freezer status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-freezer-status) | `freezer` | Operation state and temperature |
| [Powertrain trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-powertrain-data) | `powertrains/data` | Battery voltage, engine oil temperature and RPM samples |
| [Speed trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-speed-data) | `speed` | Dashboard speed, units and four wheel-speed series |
| [Driving trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-data) | `driving/data` | Ignition, mileage, trip distance, acceleration, yaw, ISLA settings, seat belts, steering and gear samples |
| [Vehicle status trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-data) | `status/data` | Door, lamp, tyre-pressure, low-fuel-warning and outside-temperature samples |

Each endpoint has an **API diagnostic sensor** showing `available`, `no_data`, `error` or `disabled`. Permission and unregistered-vehicle errors show `error`; code `4045` means `no_data`. The `data` attribute holds the complete vehicle payload, including fields without a dedicated sensor, and `api_metadata` holds response metadata.

The `rejected_values` and `rejected_examples` attributes explain discarded readings, such as values outside the documented range or in an unknown unit. Empty fields and the API's `invalid` markers are not counted. Debug logs include the field and reason without the raw value. The **Vehicle information** sensor exposes VIN, nickname, model and powertrain details, plus other fields returned by Get Vehicles.

API communication is provided by the independent [`pleos-client`](https://github.com/jouwdan/pleos-client) Python library. Home Assistant installs the pinned dependency automatically. Entities, polling schedules, retained readings, trip history and diagnostics remain in this integration.
