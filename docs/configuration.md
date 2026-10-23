# Installation and configuration

[Back to setup guide](../README.md)

## Install without HACS

1. Download the source ZIP from the [latest release](https://github.com/jouwdan/ha-pleos/releases/latest).
2. Extract it and copy `custom_components/pleos_vehicle_data` into your Home Assistant configuration directory's `custom_components` folder. The result must be `<config>/custom_components/pleos_vehicle_data/manifest.json`.
3. Restart Home Assistant and complete [account setup](../README.md#set-up-your-account). For updates, replace that integration directory with the new release and restart.

## Multiple accounts

Each account has separate devices, readings, options, trip history and archive storage. Additional entries are named **Kia 2**, **Kia 3**, and so on; you can rename them in Home Assistant. Adding the same API key twice is rejected. To replace an existing account's credentials, use **⋮ → Reconfigure** on that entry instead of adding it again. Different keys are treated as separate accounts, even if they grant access to the same car.

Account reconfiguration preserves entity and device IDs, statistics and archive paths. Replacing credentials keeps those identities for both existing and newly added accounts.

## Options

All settings are in one place: **Settings → Devices & services → Pleos Vehicle Data → Configure** on an account entry. The dialog has three sections, **Vehicle status**, **Trips** and **Storage**. Changes reload the integration. Everything not listed here works without configuration, including the **Trip** event entity and the trip statistics.

| Section | Option | Default | Range | Effect |
| --- | --- | --- | --- | --- |
| Vehicle status | **Polling interval** | 15 min | 5–1440 min | How often status data is requested. The car uploads on its own schedule, so polling more often does not make data newer. |
| Vehicle status | **Stale-data threshold** | 60 min | 5–10080 min | Readings older than this get `data_stale: true`. They stay available. |
| Vehicle status | **Enable location data and GPS tracking** | On | | Requests the location endpoint and creates the GPS tracker. Off: neither. |
| Vehicle status | **Reverse geocode vehicle location** | Off | | Resolves valid GPS coordinates to a human-readable **Address** sensor. Requires location data and a configured Nominatim-compatible service. |
| Vehicle status | **Reverse geocoding service URL** | Empty | | User-selected Nominatim-compatible endpoint. A base URL or `/reverse` endpoint is accepted. Put credentials in the separate key field; URLs with login details, query parameters or fragments are rejected. Redirects are not followed. No coordinates are sent when empty. |
| Vehicle status | **Reverse geocoding API key / token** | Empty | | Optional provider credential. For compatible Nominatim-style services it is sent as the `key` parameter. It is sent only to the configured service and redacted from diagnostics. |
| Vehicle status | **Correct odometer kilometres labelled as miles** | No cars selected | Per car | Treats a selected car's snapshot odometer value as kilometres when the API labels it miles. Use only for a confirmed unit-label error; see [Units](entities.md#units). |
| Trips | **Trip data interval** | 60 min | 5–1440 min | How often the four trip endpoints are requested. Failed trip requests retry at the next status poll. |
| Trips | **Fire an event for each new trip** | Off | | Fires `pleos_vehicle_data_trip` with every sample; see [Trip events](trips.md#trip-events). The **Trip** event entity works regardless of this option. |
| Storage | **Archive raw vehicle data** | Off | | Keeps raw responses in compressed files; see [Response archive](trips.md#response-archive). |

Reverse-geocoding providers have their own usage, caching and attribution terms. The integration keeps at most 256 successful results in memory, expires them after 48 hours and removes expired entries on the next geocoding poll. The Address sensor exposes provider attribution when the API returns it. Check the terms of the service you configure.

Pleos account credentials are not options: replace them with **⋮ → Reconfigure**; see [Expired or replaced API keys](troubleshooting.md#expired-or-replaced-api-keys).

## Refresh on demand

Use the **Pleos Vehicle Data: Refresh** action (`pleos_vehicle_data.refresh`) in Home Assistant's action editor or an automation, and select the **Account** to refresh. It requests another poll of that account without waiting for its normal polling interval. Repeated requests are batched, and trip polling intervals and API rate-limit backoff still apply.

This fetches data already available in Pleos. It cannot wake the car or make it upload new readings, so the returned values may be unchanged.

## Example dashboard

[`examples/dashboard.yaml`](../examples/dashboard.yaml) uses built-in cards for charge, range, doors, climate, a 48-hour map, trip summaries, 7-day graphs and data age.

1. Replace `my_car_` with your car's entity-ID prefix and `hyundai_api_` with `kia_api_` or `genesis_api_` if needed. Check the full IDs against your entity list: older installations and customised IDs may also have different suffixes. Update these in both cards and markdown templates; some templates assemble IDs from a prefix and suffix.
2. In Home Assistant, add a dashboard, open it, then choose **⋮ → Edit → ⋮ → Raw configuration editor** and paste the file.

Missing tiles, gauges, map and badges are hidden outside edit mode. Trip cards fill in as valid samples arrive; graphs need recorded history. Missing trip distance is shown as unavailable. Summary tables label API units separately from converted sensor states. The VIN is masked to its last six characters.
