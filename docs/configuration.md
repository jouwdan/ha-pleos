# Installation and configuration

[Back to setup guide](../README.md)

<a id="install-without-hacs"></a>

## Manual installation

1. Download the source ZIP from the [latest release](https://github.com/jouwdan/ha-pleos/releases/latest).
2. Extract it and copy `custom_components/pleos_vehicle_data` into your Home Assistant configuration directory's `custom_components` folder. The result must be `<config>/custom_components/pleos_vehicle_data/manifest.json`.
3. Restart Home Assistant and complete [account setup](../README.md#set-up-your-account). For updates, replace that integration directory with the new release and restart.

## Odometer setup

After validating your API key, setup shows an **Odometer** step for the cars on that account. **Correct odometer kilometres labelled as miles** is off by default. Select only cars whose API odometer value is in kilometres but labelled miles: this makes the displayed distance about 1.61 times too high. Correctly reported kilometres and miles need no correction. Home Assistant's unit preference controls how the distance is displayed.

If unsure, leave the selection empty and compare the odometer with your car's dashboard after setup. You can change the same per-car setting later under **Configure → Freshness and privacy**. It affects new readings only; existing history and statistics are not repaired. Clear it if Pleos fixes the underlying unit label. Accounts with no vehicles skip this step.

## Multiple accounts

Each account has separate devices, readings, options, trip history and archive storage. Additional entries are named **Kia 2**, **Kia 3**, and so on; you can rename them in Home Assistant. Adding the same API key twice is rejected. A key that includes a vehicle already associated with another entry of the same manufacturer is also rejected, even if that entry is unloaded or its first setup failed before devices registered. The check uses registered devices and saved vehicle evidence from successful API responses. To replace an existing account's credentials, use **⋮ → Reconfigure** on that entry instead of adding it again.

Account reconfiguration preserves entity and device IDs, statistics and archive paths. Replacing credentials keeps those identities for both existing and newly added accounts.

Vehicle evidence consists of account-scoped hashes, without a new stored copy of the VIN. It is saved when setup or credential replacement is accepted and updated after successful vehicle-list requests. Temporary API failures leave it intact; a successful empty list clears the saved list. Existing entries learn this evidence on their next successful request. A response from credentials that have since been replaced cannot overwrite it. Registered devices remain protected until you explicitly remove them under the [stale-vehicle removal rules](#removing-a-vehicle-that-left-the-account).

If replacement credentials return no vehicles matching the entry's known vehicles, Home Assistant asks you to explicitly confirm that they belong to the same Pleos and manufacturer account. Check which account you signed into when issuing the key. Leave the confirmation unchecked and cancel if it is a different account; no credentials change until you confirm. A key overlapping another entry is still rejected, regardless of confirmation. If a vehicle has changed accounts, remove its old device after a successful refresh confirms it has left the old account, then configure its new account.

These checks compare individual known vehicles; they cannot prove account identity. Pleos's [token response](https://document.pleos.ai/en/api-reference/vehicle-data-api/authentication-authorization/for-private-users/get-access-token) exposes no documented stable account identifier, and its [vehicle list](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/for-private-users/get-vehicles) changes when vehicles leave or keys are reissued. Empty accounts, accounts whose entire vehicle list has changed, and older failed entries with neither devices nor saved evidence cannot be matched automatically. A VIN identifies a vehicle, not its owner; moving this comparison into `pleos-client` would not establish account identity. Existing duplicate entries are not merged or removed. The [quality checklist](quality-scale.md) keeps formal account-identity compliance open pending a supported identity contract or upstream review of this fallback.

## Options

All settings are in one place: **Settings → Devices & services → Pleos Vehicle Data → Configure** on an account entry. The dialog has three sections, **Freshness and privacy**, **Trips** and **Storage**. Changes reload the integration. Everything not listed here works without configuration, including the **Trip** event entity and the trip statistics.

| Section | Option | Default | Range | Effect |
| --- | --- | --- | --- | --- |
| Freshness and privacy | **Stale-data threshold** | 60 min | 5–10080 min | Readings older than this get `data_stale: true`. They stay available. |
| Freshness and privacy | **Enable location data and GPS tracking** | On | | Requests the location endpoint and creates the GPS tracker. Off: neither. |
| Freshness and privacy | **Reverse geocode vehicle location** | Off | | Resolves valid GPS coordinates to a human-readable **Address** sensor. Requires location data and a configured Nominatim-compatible service. |
| Freshness and privacy | **Reverse geocoding service URL** | Empty | | User-selected Nominatim-compatible endpoint. A base URL or `/reverse` endpoint is accepted. Put credentials in the separate key field; URLs with login details, query parameters or fragments are rejected. Redirects are not followed. No coordinates are sent when empty. |
| Freshness and privacy | **Reverse geocoding API key / token** | Empty | | Optional provider credential. For compatible Nominatim-style services it is sent as the `key` parameter. It is sent only to the configured service and redacted from diagnostics. |
| Freshness and privacy | **Correct odometer kilometres labelled as miles** | No cars selected | Per car | Treats a selected car's snapshot odometer value as kilometres when the API labels it miles. Use only for a confirmed unit-label error; see [Units](entities.md#units). |
| Trips | **Fire an event for each new trip** | Off | | Fires `pleos_vehicle_data_trip` with every sample; see [Trip events](trips.md#trip-events). The **Trip** event entity works regardless of this option. |
| Storage | **Archive raw vehicle data** | Off | | Keeps raw responses in compressed files; see [Response archive](trips.md#response-archive). |

Reverse-geocoding providers have their own usage, caching and attribution terms. The integration keeps at most 256 successful results in memory, expires them after 48 hours and removes expired entries on the next geocoding poll. The Address sensor exposes provider attribution when the API returns it. Check the terms of the service you configure.

Pleos account credentials are not options: replace them with **⋮ → Reconfigure**; see [Expired or replaced API keys](troubleshooting.md#expired-or-replaced-api-keys).

## Polling cadence

The integration requests vehicle snapshots every 15 minutes and completed-trip data every 60 minutes. Vehicles upload on their own schedule and trip data can arrive hours after a drive; these intervals balance useful updates with cloud request volume. Failed trip requests retry at the next snapshot poll. Existing custom interval settings are removed automatically on upgrade; freshness, location, geocoding, calibration, events and archive choices are preserved.

## Refresh on demand

Use the **Pleos Vehicle Data: Refresh** action (`pleos_vehicle_data.refresh`) in Home Assistant's action editor or an automation, and select the **Account** to refresh. It waits for the requested or already running poll to complete. Repeated requests are batched, and trip polling intervals and API rate-limit backoff still apply. A failed account request or requested endpoint raises an action error; healthy endpoints still update. Disabling an endpoint, reusing trips between scheduled polls, and a successful `no_data` response are not failures. Unloading the account before completion also reports an action error.

This fetches data already available in Pleos. It cannot wake the car or make it upload new readings, so the returned values may be unchanged.

## Removing a vehicle that left the account

After a successful account refresh confirms a vehicle is no longer listed by Pleos, open its device page and choose **Delete**. This removes that device and its entities; the account and its other vehicles stay configured. Vehicles are never deleted automatically.

Removal is blocked for vehicles still listed, the account API device, unloaded accounts, and failed or malformed account responses. Retry after the connection recovers. Existing recorder history, imported trip statistics and raw archives follow their existing retention policies.

## Removing the integration

1. Open **Settings → Devices & services → Pleos Vehicle Data**. On the account entry, choose **⋮ → Delete** and confirm. Repeat for other accounts you want to remove.
2. Removing an account deletes its saved ranges and tyre pressures, EV-consumption totals and checkpoints, trip tracking and imported trip statistics. Export anything you need first. Ordinary entity history follows Home Assistant's recorder retention policy.
3. Raw archive files remain in `<config>/pleos_vehicle_data/archive/`. Delete the relevant account folder yourself if you no longer want it; keep folders belonging to accounts you still use.
4. If you no longer use the personal API key elsewhere, revoke it in Pleos Playground's **My Vehicle Data API** key-management page. Also revoke a separate geocoding-provider key if you no longer use it.
5. After removing all accounts, open **Settings → Marketplace**, find **Pleos Vehicle Data** under **Installed**, then choose **⋮ → Uninstall** and confirm. Restart Home Assistant if prompted by the repair. For a manual installation, remove `<config>/custom_components/pleos_vehicle_data` and restart instead.

To replace credentials or temporarily stop polling, use reconfiguration or disable the account; deleting and adding it again creates a new account namespace and does not restore its derived totals.

## Example dashboard

[`examples/dashboard.yaml`](../examples/dashboard.yaml) uses built-in cards for charge, range, doors, climate, a 48-hour map, trip summaries, 7-day graphs and data age.

1. Replace `my_car_` with your car's entity-ID prefix and `hyundai_api_` with `kia_api_` or `genesis_api_` if needed. Check the full IDs against your entity list: older installations and customised IDs may also have different suffixes. Update these in both cards and markdown templates; some templates assemble IDs from a prefix and suffix.
2. In Home Assistant, add a dashboard, open it, then choose **⋮ → Edit → ⋮ → Raw configuration editor** and paste the file.

On a new installation, enable the endpoint API and data-age diagnostic entities used by the dashboard if you want its diagnostic tables and freshness tiles. Existing enabled entities remain enabled.

Missing tiles, gauges, map and badges are hidden outside edit mode. Trip cards fill in as valid samples arrive; graphs need recorded history. Missing trip distance is shown as unavailable. Summary tables label API units separately from converted sensor states. The VIN is masked to its last six characters.
