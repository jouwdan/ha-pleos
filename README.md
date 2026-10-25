# Pleos Vehicle Data

[![Validate](https://github.com/jouwdan/ha-pleos/actions/workflows/validate.yml/badge.svg)](https://github.com/jouwdan/ha-pleos/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/jouwdan/ha-pleos)](https://github.com/jouwdan/ha-pleos/releases/latest)
[![Available in HACS](https://img.shields.io/badge/HACS-Default-41BDF5)](https://www.hacs.xyz/)

Bring your Hyundai, Kia or Genesis vehicle into Home Assistant using the official Pleos **personal Vehicle Data API**. Each car gets its own device, with entities you can use in dashboards and automations. Multiple accounts are supported.

This is a **read-only cloud integration for Europe**. It reads the data your car has already uploaded; it cannot lock the car, start climate control or charging, or wake it for a fresh update.

## What it does

- Shows battery and charging status, fuel level, range and odometer readings.
- Reports doors, ignition, climate settings, tyre pressures and warnings using native Home Assistant sensors and binary sensors.
- Adds GPS tracking, trip summaries and driving statistics.
- Estimates cumulative [EV consumption since install](docs/entities.md#estimated-ev-consumption) in kWh/100 km or mi/kWh from valid battery and odometer reports.
- Shows when data was reported, so you can tell how recent it is.

Available data varies by vehicle. Status is checked every **15 minutes** and trips every **60 minutes** by default; the car uploads on its own schedule, so these are not live readings.

## Before you start

You need:

- **Home Assistant 2026.10 or later.**
- A supported European Hyundai, Kia or Genesis vehicle with active connected services. See [supported vehicles](docs/supported-vehicles.md).
- The vehicle's **primary user** account to generate a personal API key. A shared or secondary app user cannot authorise access, even if they can see the car in the app.

## Install with HACS

1. [Install HACS](https://www.hacs.dev/docs/use/download/download/) if you do not already have it.
2. Open **HACS** and search for **Pleos Vehicle Data**.
3. Open the result, choose **Download**, then restart Home Assistant.

Prefer to install manually? Follow [Install without HACS](docs/configuration.md#install-without-hacs).

## Set up your account

1. Sign into [Pleos Playground](https://pleos.ai/) and open the account menu → **My Vehicle Data API**. This menu is available to users in Europe.
2. Select your manufacturer, accept the terms, choose **Get API Key**, and sign into the primary user's manufacturer account. Copy the **Client ID** and **Client Secret**. Use the personal API key; you do not need a business project or API-access approval. [Official key-generation guide](https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/request-my-vehicle-api-key).
3. In Home Assistant, open **Settings → Devices & services → Add integration → Pleos Vehicle Data**. Select the same manufacturer and enter both credentials.
4. On **Odometer**, select only cars whose kilometre reading is incorrectly labelled as miles by Pleos. Leave empty if the units are correct or you are unsure. Home Assistant handles your normal km/miles display preference. [Odometer setup](docs/configuration.md#odometer-setup).
5. Open your vehicle's device page and add its entities to your dashboards or automations. Repeat setup to add another account.

Keep your Client Secret private. Personal keys need manual renewal when they expire; use the account entry's **⋮ → Reconfigure** menu to replace an existing key. See [key renewal](docs/troubleshooting.md#expired-or-replaced-api-keys).

## After setup

The defaults work without further configuration. To change polling, disable location, or enable raw-data archiving, open **Configure** on the account entry. See [all options](docs/configuration.md#options).

For a ready-made dashboard with charging, range, doors, a map and trip graphs, follow the [example dashboard instructions](docs/configuration.md#example-dashboard).

Readings may stay visible when old or temporarily unavailable from Pleos. Check their reporting time and `data_stale` attribute before using them in automations that need current data. [How data freshness works](docs/entities.md#data-freshness-and-retained-readings).

**Updating an existing installation?** Read the [upgrade guide](docs/upgrading.md) for capacity-unit, entity-ID and state changes.

## Help and reference

| I want to… | Guide |
| --- | --- |
| Fix setup, missing vehicles or expired keys | [Troubleshooting](docs/troubleshooting.md) |
| Install manually, change options or refresh on demand | [Configuration](docs/configuration.md) |
| Understand sensors, units and retained readings | [Entity reference](docs/entities.md) |
| Use trip events, statistics or raw archives | [Trip guide](docs/trips.md) |
| Compare this with Kia Uvo / Hyundai Bluelink | [kia_uvo comparison](docs/kia-uvo-comparison.md) |

For a bug or feature request, [open an issue](https://github.com/jouwdan/ha-pleos/issues). Include your Home Assistant and integration versions, vehicle model, and what happened. If sharing [diagnostics](docs/troubleshooting.md#diagnostics), review them for personal information first. Never post your API credentials.
