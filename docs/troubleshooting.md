# Troubleshooting and privacy

[Back to setup guide](../README.md)

If these notes do not explain the problem, [open an issue](https://github.com/jouwdan/ha-pleos/issues) with your Home Assistant and integration versions, vehicle model, and what you expected to happen. Never post API credentials.

## No vehicles

If the **Connection** sensor shows `no_vehicles`, Pleos accepted the key but returned no car for it. The integration cannot change this; the cause is on the account or vehicle side. Known causes:

- **Not the primary user.** Only the vehicle's primary user can authorise API access. A key created with a shared or secondary app account grants no car, even if that account sees the car in the app.
- **Key created before the car was added or activated.** Vehicle access is fixed when the key is issued. Generate a new key after the car appears in the manufacturer's app, then replace both credentials with **⋮ → Reconfigure**.
- **Vehicle locked by the manufacturer.** Former company, fleet or demonstration cars can remain restricted in the manufacturer's systems, so the API returns no vehicle even for the primary user. Other Pleos-based integrations show the same. Only the manufacturer's customer support or Pleos can lift this.
- **Region or connected services.** The personal API is only available in Europe, and the car's connected services and data-sharing consent must be active.

The diagnostics download then shows `"status": "no_vehicles"` and no cars. When asking for help, review the diagnostics for personal information before attaching them; see [Diagnostics](#diagnostics).

## Expired or replaced API keys

Personal keys expire and require **manual renewal**. Check the expiry shown in Pleos. The integration renews access tokens automatically, not the personal key. [Official key-management guide](https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/manage-my-vehicle-api-key).

Expired access tokens are replaced automatically and the failed request is retried once. A 403 that persists with a fresh token remains an API permission error; it does not ask you to replace a valid personal key.

When credentials are rejected, polling stops and Home Assistant requests reauthentication. Generate a new personal key and replace **both Client ID and Client Secret** in that request. To replace credentials before expiry, use the account entry's **⋮ → Reconfigure** menu. The existing entry reloads and retains entity IDs for the same vehicles.

Pleos invalidates old access tokens when a key expires, is revoked or is reissued. A newly registered or activated vehicle also needs a new key: vehicle access is fixed at issuance. [Get Vehicles rules](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/for-private-users/get-vehicles).

## Diagnostics

Use **Settings → Devices & services → Pleos Vehicle Data → ⋮ → Download diagnostics** for troubleshooting. It includes options, polling and rate-limit status, trip windows, archive status, readings and raw responses. A download from a car's device page contains only that car. Credentials, VINs, nicknames and coordinates are removed. Review the file before attaching it to an issue, especially if the API adds new fields.

## Limitations and privacy

- Snapshot polling is managed by the integration at **15 minutes**. After **60 minutes**, successfully fetched snapshot readings are marked `data_stale: true` but remain available. The freshness threshold remains configurable; see [Options](configuration.md#options). Age is updated at each poll. Failed account requests make vehicle entities unavailable; an isolated failed endpoint affects its current readings. Missing or invalid current readings in a successful response are unknown. Automations that require recent information should check entity state and `data_stale`.
- Availability varies with vehicle, powertrain, model year, region and connected-service settings. Null or documented invalid values do not become vehicle readings. `no_data` is an API result, not a guarantee of future availability. [Pleos FAQ](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).
- Pleos says trip data is available only **24 hours after consent**, and also depends on connected services, data-collection consent and ePrivacy settings. A `4045` response means no data was returned. [Pleos FAQ](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).
- This is a read-only cloud integration. It does not implement remote vehicle commands, camera access, the in-car SDK, Fleet API or business-user authentication/consent flows. Status snapshots and returned trip samples are not live telemetry.
- A failing optional endpoint, including a timeout, does not hide other endpoint data. Rate limits stop remaining requests and keep readings already fetched in that poll; affected readings may be unavailable until recovery. Account-level failures are reported by the Connection sensor. Requests retry on later polls and respect `Retry-After`. Responses above **16 MiB** are rejected.
- Keys live in Home Assistant's private configuration; access tokens stay in memory. Credentials and VINs are not logged. Raw payloads and VIN attributes are excluded from recorder history, but remain accessible in current entity attributes. Protect configuration files and backups.
