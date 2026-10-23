# Upgrading

[Back to setup guide](../README.md)

Read the notes for each version you are crossing, including the native entity sections when upgrading from 0.6.0 or earlier. Restart Home Assistant after updating.

## Odometer setup in 0.10.2

New accounts now see an **Odometer** step that makes the existing per-car unit-label correction easier to find. It is off by default. This is for kilometres incorrectly labelled as miles by Pleos; Home Assistant still handles normal km/miles display conversion. See [odometer setup](configuration.md#odometer-setup).

Existing accounts keep their settings and do not need setup again. The correction remains available under **Configure → Vehicle status**, and replacing an API key preserves it. Entity IDs, recorded history and odometer calculations are unchanged. Restart Home Assistant after updating.

## Retained readings and tyre units in 0.10.1

**Tyre pressures** now default to **psi**, including existing sensors and last-trip pressures. Explicit entity unit choices are preserved. For **bar**, use **Unit of measurement** in the entity's settings. Home Assistant converts the displayed number along with the unit. Check numeric thresholds in automations that assumed the previous default unit. Entity IDs stay the same and existing recorded history is not rewritten.

Snapshot tyre pressures and the last completed **Estimated EV consumption** are now saved across reloads and restarts. Missing parked-car pressures retain their last valid readings; charging or an incomplete new consumption window retains the previous completed estimate. Retained values keep their original reporting time and are marked `last_known: true`, `data_stale: true`. A fresh valid reading or completed estimate replaces them. Calculation reset rules and EV-only eligibility are unchanged.

An estimate still present in the previous version's saved observation window can be restored. Values already discarded by an earlier version cannot be recovered from history; the integration needs a new valid tyre reading or completed consumption window before it can retain one. Restart Home Assistant after updating. See [tyre pressures](entities.md#tyre-pressures) and [estimated EV consumption](entities.md#estimated-ev-consumption) for details.

## Optional address sensor in 0.10.0

Reverse geocoding is off by default, so updating does not send coordinates to another service. To add the **Address** sensor, enable **Reverse geocode vehicle location** in the integration's options and configure a Nominatim-compatible service URL and optional API key. Location data must also be enabled. See [configuration](configuration.md#options) and [the Address sensor](entities.md#optional-address-sensor) for privacy, provider and caching details.

Existing entity IDs and recorded history are unchanged. Restart Home Assistant after updating.

## Vehicle entity refinements in 0.9.2

**Key battery warning** now displays Low / Normal using Home Assistant's battery binary-sensor class. Its entity ID and raw `on` / `off` states stay the same.

On pure EVs, obsolete **Low fuel warning** and **Engine oil warning** entities are disabled by the integration when it loads. Their registry entries, customisations and recorded history are preserved; entries already disabled by the user remain so. They are no longer created for new EVs. Other vehicle types and the complete **Warnings** list are unchanged.

When **Battery level** is available, **Traction battery level** moves into Diagnostics to avoid two primary charge readings. It returns to the main sensor section if Battery level is missing or invalid. Both entity IDs, values and history remain unchanged, so existing dashboards and automations continue to work. Restart Home Assistant after updating.

## EV economy and upgrading to 0.9.0

**Battery capacity** keeps its entity ID but now displays **kWh** with the `energy_storage` device class. Its previous unitless number was the API's kJ value: `256320` becomes `71.2 kWh`. Remove any existing template division by 3600 to avoid converting twice, and update numeric thresholds that used the raw number. Raw API diagnostics and archives remain unchanged. Existing recorded history/statistics are not rewritten; older unitless values should not be compared directly with the new kWh values. Capacity remains without a state class, so this change does not create consumption totals.

**Estimated EV consumption** is a new sensor for pure EVs with suitable data. It defaults to mi/kWh for Home Assistant installations using miles, or kWh/100 km for metric distance. An explicit unit choice in the entity settings overrides the default. No dashboard or automation references are moved.

The sensor appears after a usable matched report and starts unknown until at least 10 km and a 5-percentage-point battery drop have been observed. Charging and invalid data reset the observation window. See [estimated EV consumption](entities.md#estimated-ev-consumption) for requirements, reset rules and accuracy limits. No past economy is backfilled; this starts collecting after the update. Restart Home Assistant after installation.

## Native entities and upgrading to 0.8.0

**Bonnet**, **Boot** and **Sunroof** are now open/closed binary sensors. Bonnet and boot use Home Assistant's `opening` device class; sunroof uses `window`. Update references from `sensor.<car>_bonnet`, `sensor.<car>_boot` and `sensor.<car>_sunroof` to their new `binary_sensor.*` IDs after updating and restarting Home Assistant. As with the 0.7.0 conversions, reapply customisations to the new entities; old entries and history are not automatically deleted or moved. Their raw states are now `on` (open) and `off` (closed).

Doors, seat heating/ventilation levels, climate mode and detailed charging status are **enum sensors** with predefined states and translated display labels. Their entity IDs stay the same. Door states retain `open`, `closed` and `locked`; seat levels retain `off`, `low`, `middle` and `high`; climate mode retains `manual`, `auto`, `off` and `error`. Unknown values become unavailable instead of being interpreted as off or closed.

**Charging status raw states change in 0.8.0.** Update state comparisons in automations, scripts and templates:

| Previous raw state | New raw state |
| --- | --- |
| `notCharging` | `not_charging` |
| `charging` | `charging` |
| `fastCharging` | `fast_charging` |
| `v2lOperating` | `v2l_operating` |
| `v2lStop` | `v2l_stop` |
| `v2xOperating` | `v2x_operating` |
| `reservedCharging` | `reserved_charging` |
| `wirelessCharging` | `wireless_charging` |

The 17 [documented warning indicators](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-status) each get a `problem` binary sensor, including **Low fuel warning**, **Key battery warning**, **Washer fluid warning**, **Brake fluid warning**, **Engine oil warning**, tyre-pressure warnings and lamp warnings. They show `on` when that warning is reported and `off` when absent from a valid warning list. An empty list clears them all. Missing, invalid or malformed warning data, or a failed status-endpoint request, makes them unavailable. A transient failure of the initial account request still retains the previous whole poll with `last_known: true` and `data_stale: true`, as described in [Data freshness and retained readings](entities.md#data-freshness-and-retained-readings). These indicate what the last vehicle report says, not an independent health check.

The existing **Warnings** sensor still keeps the full list and count, including unrecognised codes. Warning sensors appear only once a valid warning list has been received.

**AC charge limit** and **DC charge limit** are ordinary percentage sensors, not battery-level sensors. This prevents Home Assistant's device-page battery indicator from selecting a charge target such as 100%. Actual **Battery level** and **Traction battery level** retain the battery device class. Charge-limit IDs, values, history and retention during transient failures are preserved.

**Cabin fan speed** keeps its entity ID but becomes an enum: numeric `-1` becomes `auto`, `0` becomes `off`, and `1`–`8` become `level_1`–`level_8`. Update numeric state comparisons. Display labels are Auto, Off and Level 1–8.

Six **(last trip)** readings also move from `sensor.*` to `binary_sensor.*`: **Ignition**, **Driver door**, **Front passenger door**, **Rear left door**, **Rear right door** and **Low fuel warning**. Their suffix remains `_last_trip`; for example, `sensor.<car>_driver_door_last_trip` becomes `binary_sensor.<car>_driver_door_last_trip`. Doors use `on` for open and `off` for closed; ignition and the warning use `on` for active. Reapply customisations and update references as for bonnet/boot/sunroof. These are historical samples, not current vehicle status.

Last-trip **Gear**, **Seat belts**, **Speed limit assist settings**, **Exterior light setting**, **Speed unit** and **Tyre pressure unit** become enums while keeping their IDs. Gear raw states remain `p`, `r`, `n`, `d`, displayed as Park, Reverse, Neutral, Drive. Seat belts retain `belted`/`unbelted`; an unfastened belt does not establish occupancy or danger. Light-setting raw states change: `lightSwOff` → `off`, `tailSwOn` → `tail_lights`, `headLampLowSwOn` → `headlamps`, `autoLightSwOn` → `auto`. The **Trip** event's text attributes and raw trip events retain their original API values.

See the [full entity mapping](entities.md#native-entity-types) for every sensor type.

Only current measurements generate measurement statistics. Charge targets, cabin temperature targets, range/time estimates, capacity, coordinates, data age and delayed **(last trip)** readings no longer generate new measurement statistics. Their ordinary state history remains; existing statistics are not deleted or rewritten. Heading uses angular statistics so 359° and 1° average around north. Odometer uses `total` because lifetime mileage does not periodically reset. The separate [trip statistics](trips.md#trip-statistics) continue to record distance, driving time and speed against the hour driven.

These remain read-only entities. The API does not support operating locks, windows, heating or fans, so they are not exposed as controllable switches, covers, locks, fans or climate entities.

## On/off readings and upgrading to 0.7.0

Ignition, ignition power, high-voltage system power, accessory power, sleep mode, climate control, windscreen defrost, steering wheel heating and freezer operation are native binary sensors. They report `on` or `off`, allowing Home Assistant to colour active dashboard icons. Missing, invalid or unexpected values do not become `off`. These readings reflect the latest cloud report; ignition can be on while the vehicle is parked.

**Upgrading from 0.6.0 or earlier:** these nine readings move from `sensor.*` to `binary_sensor.*`. After updating and restarting Home Assistant, find their new entity IDs on the vehicle's device page and update dashboard cards, automations and scripts that use the old IDs. For example, `sensor.ev6_ignition` becomes `binary_sensor.ev6_ignition` with the default names. In a picture-elements `state-icon`, use the new entity and `state_color: true`.

Home Assistant registers these as new entities. Custom entity IDs, names, icons, areas and disabled/hidden settings on the old sensors are not copied; reapply the settings you need. Old sensor entries may remain unavailable and can be removed after you update their references. Existing history is not moved to the new entities. The [example dashboard](../examples/dashboard.yaml) uses the new IDs.

The 0.7.0 release left other text readings unchanged; 0.8.0 extends those conversions.
