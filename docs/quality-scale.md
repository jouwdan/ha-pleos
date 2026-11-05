# Home Assistant quality checklist

[Back to setup guide](../README.md)

The integration aims to meet **Home Assistant's Gold quality standards**. This is its readiness checklist, checked against the [54 official rules](https://contributors.home-assistant.io/core/quality-scale/rules/) on **4 November 2026**. It is not an awarded Home Assistant tier. A tier requires every applicable rule in that tier and every tier below it.

[`quality_scale.yaml`](../custom_components/pleos_vehicle_data/quality_scale.yaml) follows the [official status format](https://contributors.home-assistant.io/core/quality-scale/quality-scale-file/): **done** has implementation evidence, **todo** has remaining work, and **exempt** has a specific reason. The statuses below match that file. Documentation rules remain **todo** for formal Core readiness even where the linked local user guide is complete: publication on home-assistant.io is still pending. Passing Hassfest for a custom integration does not validate this checklist or award a tier; its Core quality-scale checks are skipped for custom integrations.

## Bronze

| Rule | Status | Evidence or remaining work |
| --- | --- | --- |
| `action-setup` | done | [`async_setup`](../custom_components/pleos_vehicle_data/__init__.py) registers the refresh action once; [service tests](../tests/test_services.py). |
| `appropriate-polling` | done | [Managed intervals](configuration.md#polling-cadence): 15-minute snapshots and 60-minute trips; [constants](../custom_components/pleos_vehicle_data/const.py) and [migration tests](../tests/test_options_migration.py). |
| `brands` | todo | [Local custom-integration icons](../custom_components/pleos_vehicle_data/brand/) exist; submission to home-assistant/brands is pending. |
| `common-modules` | done | Shared [coordinator](../custom_components/pleos_vehicle_data/coordinator.py) and [entity](../custom_components/pleos_vehicle_data/entity.py) modules. |
| `config-flow` | done | [UI setup and options](../custom_components/pleos_vehicle_data/config_flow.py), translated field help, credentials in entry data and preferences in options. |
| `config-flow-test-coverage` | done | [Flow tests](../tests/test_config_flow.py), [real flow-manager tests](../tests/test_flow_manager.py), and the [CI 100% gate](../.github/workflows/validate.yml). |
| `dependency-transparency` | done | Published, open-source [pleos-client](https://github.com/jouwdan/pleos-client) and [nominatim-reverse-client](https://github.com/jouwdan/nominatim-reverse-client), each built and released by public CI. |
| `docs-actions` | todo | Local [refresh action guide](configuration.md#refresh-on-demand) complete; Core documentation publication pending. |
| `docs-conditions` | exempt | No custom condition platform. Examples use Home Assistant's built-in conditions. |
| `docs-high-level-description` | todo | [README](../README.md) explains Pleos and read-only cloud access; Core documentation publication pending. |
| `docs-installation-instructions` | todo | [Prerequisites and setup](../README.md#before-you-start), [manual installation](configuration.md#manual-installation); Core installation documentation pending. |
| `docs-removal-instructions` | todo | [Removal guide](configuration.md#removing-the-integration), including retained archives; Core documentation publication pending. |
| `docs-triggers` | exempt | No custom trigger platform. [Trip events](trips.md#trip-event-entity) and [examples](examples.md) use built-in triggers. |
| `entity-event-setup` | done | [Coordinator entities](../custom_components/pleos_vehicle_data/entity.py) subscribe through HA's lifecycle; platform listeners use entry unload callbacks; [lifecycle tests](../tests/test_lifecycle.py). |
| `entity-unique-id` | done | Account-scoped vehicle and reading identifiers; [account](../tests/test_accounts.py) and [registry compatibility tests](../tests/test_entity_defaults.py). |
| `has-entity-name` | done | Shared vehicle, account-status and Trip entities set `has_entity_name`; [name tests](../tests/test_names.py). |
| `runtime-data` | done | Typed [`PleosConfigEntry`](../custom_components/pleos_vehicle_data/models.py) stores its coordinator in `entry.runtime_data`. |
| `test-before-configure` | done | [`validate`](../custom_components/pleos_vehicle_data/config_flow.py) calls Get Vehicles before creating or updating an entry; empty accounts remain valid. |
| `test-before-setup` | done | [Setup](../custom_components/pleos_vehicle_data/__init__.py) awaits the coordinator's first refresh; authentication and connection failures use HA exceptions. |
| `unique-config-entry` | todo | Duplicate keys and [vehicle overlap](../tests/test_account_guardrails.py) are rejected using registered devices and [saved evidence](../tests/test_account_evidence.py), including failed initial setup. Empty or completely changed fleets still cannot establish account identity. |

## Silver

| Rule | Status | Evidence or remaining work |
| --- | --- | --- |
| `action-exceptions` | done | [Refresh](../custom_components/pleos_vehicle_data/services.py) and [coordinator](../custom_components/pleos_vehicle_data/coordinator.py) raise validation/HA errors for invalid, unloaded, failed or cancelled requests; [tests](../tests/test_services.py). |
| `config-entry-unloading` | done | [Unload](../custom_components/pleos_vehicle_data/__init__.py) stops platforms/coordinator and removes listeners; [lifecycle](../tests/test_lifecycle.py) and [retention](../tests/test_account_retention.py) tests. |
| `docs-configuration-parameters` | todo | [All options, defaults and ranges](configuration.md#options) documented locally; Core publication pending. |
| `docs-installation-parameters` | todo | [Brand and credentials](../README.md#set-up-your-account), [odometer selection](configuration.md#odometer-setup) documented locally; Core publication pending. |
| `entity-unavailable` | done | [Current/historical semantics](entities.md#data-freshness-and-retained-readings); [endpoint isolation tests](../tests/test_failure_isolation.py). |
| `integration-owner` | done | `@jouwdan` is the [manifest codeowner](../custom_components/pleos_vehicle_data/manifest.json). |
| `log-when-unavailable` | done | Coordinator account transitions and per-endpoint failure/recovery logs; [coordinator tests](../tests/test_coordinator.py). |
| `parallel-updates` | done | All four platforms declare `PARALLEL_UPDATES = 0`; the coordinator owns polling. |
| `reauthentication-flow` | todo | [Renewal](../custom_components/pleos_vehicle_data/config_flow.py) preserves IDs, checks vehicle continuity and requires explicit same-account confirmation when continuity is absent. This does not prove account identity. |
| `test-coverage` | done | [CI](../.github/workflows/validate.yml) requires more than 95% for every integration module and 100% for config flow; [recorded validation](development.md#completed-checks). |

## Gold

| Rule | Status | Evidence or remaining work |
| --- | --- | --- |
| `devices` | done | Account API and vehicle devices include manufacturer/model; [shared device info](../custom_components/pleos_vehicle_data/entity.py) and [account tests](../tests/test_accounts.py). |
| `diagnostics` | done | [Account/device diagnostics](../custom_components/pleos_vehicle_data/diagnostics.py) redact credentials, VINs, nicknames, locations and provider settings; [tests](../tests/test_diagnostics.py). |
| `discovery` | exempt | The personal cloud API offers no network/bus discovery mechanism. Account credentials must be supplied. |
| `discovery-update-info` | exempt | No discoverable per-vehicle IP address or network endpoint exists to update. |
| `docs-data-update` | todo | [Polling](configuration.md#polling-cadence), [freshness](entities.md#data-freshness-and-retained-readings), [delayed trips](trips.md) documented locally; Core publication pending. |
| `docs-examples` | todo | [Copyable guarded automations](examples.md) and [dashboard](configuration.md#example-dashboard) available locally; Core publication pending. |
| `docs-known-limitations` | todo | [Limitations and privacy](troubleshooting.md#limitations-and-privacy), [estimated consumption](entities.md#estimated-ev-consumption); Core publication pending. |
| `docs-supported-devices` | todo | [Tested vehicle versus documented compatibility](supported-vehicles.md), European/primary-user restrictions in setup; Core publication pending. |
| `docs-supported-functions` | todo | [Entity reference](entities.md) and [trip guide](trips.md); Core publication pending. |
| `docs-troubleshooting` | todo | [Key renewal, missing vehicles and diagnostics](troubleshooting.md) documented locally; Core publication pending. |
| `docs-use-cases` | todo | [README](../README.md#what-it-does), [dashboard](configuration.md#example-dashboard), [battery and trip notifications](examples.md); Core publication pending. |
| `dynamic-devices` | done | [Discovery listeners](../custom_components/pleos_vehicle_data/entity.py) add cars after setup, including a previously removed car that returns; [tests](../tests/test_stale_devices.py). |
| `entity-category` | done | API, reporting, vehicle-information and unit diagnostics have categories; [entity reference](entities.md#native-entity-types) and [tests](../tests/test_entity_defaults.py). |
| `entity-device-class` | done | Supported readings use native classes where meaningful; [native state tests](../tests/test_native_states.py), [unit tests](../tests/test_units.py). |
| `entity-disabled-by-default` | done | [Noisy diagnostics](../custom_components/pleos_vehicle_data/names.py) default off only on first registration; [tests](../tests/test_entity_defaults.py) preserve user choices. |
| `entity-translations` | done | [Supported fields](../custom_components/pleos_vehicle_data/names.py), numbered ranges and existing undocumented readings use translation keys. [English/German platform tests](../tests/test_legacy_translations.py) verify legacy labels and preserved registry choices. |
| `exception-translations` | done | Action and coordinator errors use [translation keys](../custom_components/pleos_vehicle_data/strings.json), including authentication and account-refresh failures; [tests](../tests/test_coordinator.py). |
| `icon-translations` | done | Non-device-class icons are in [`icons.json`](../custom_components/pleos_vehicle_data/icons.json). |
| `reconfiguration-flow` | todo | [Replacement-key flow](../custom_components/pleos_vehicle_data/config_flow.py) checks vehicle overlap and requires explicit confirmation without continuity; same-account proof remains unresolved. |
| `repair-issues` | done | Account-specific [archive write repairs](../custom_components/pleos_vehicle_data/archive.py) clear on successful write, opt-out or account removal; [archive tests](../tests/test_archive.py), [repair lifecycle tests](../tests/test_archive_repairs.py) and [upgrade guide](upgrading.md#account-checks-and-actionable-repairs-in-0140). |
| `stale-devices` | done | [Guarded manual removal](configuration.md#removing-a-vehicle-that-left-the-account), [callback](../custom_components/pleos_vehicle_data/__init__.py) and [failure/removal/rediscovery tests](../tests/test_stale_devices.py). |

## Platinum

| Rule | Status | Evidence or remaining work |
| --- | --- | --- |
| `async-dependency` | done | Both published clients use async `aiohttp` requests; [dependency sources](#bronze). Archive disk work uses the executor. |
| `inject-websession` | done | [Setup](../custom_components/pleos_vehicle_data/__init__.py) and [config flow](../custom_components/pleos_vehicle_data/config_flow.py) supply HA's client session; [geocoder](../custom_components/pleos_vehicle_data/geocode.py) receives that session too. |
| `strict-typing` | todo | All 21 integration modules pass [strict mypy](../pyproject.toml) in the [HA CI matrix](../.github/workflows/validate.yml); both libraries ship `py.typed`. Formal Core `.strict-typing` registration is pending. |

## Compatibility and remaining Core work

Existing entities for undocumented API fields use a translated **Legacy reading ({field})** label with their technical field identifier. They retain registry identities, custom names and user choices. The template stays outside the supported discovery catalog, so new undocumented fields remain in diagnostic payloads and optional raw archives instead of creating entities. Labels update when a field is returned; absent registry-only entries are left intact. This closes the local untranslated-name path without inventing meaning for undocumented fields. Official tier recognition still requires Core review.

The saved account namespace preserves IDs and storage through key replacement. It is not proof of the Pleos account behind a new key. [Guardrails](configuration.md#multiple-accounts) reject keys whose vehicles overlap another entry's registered devices or saved vehicle evidence, including unloaded entries and new entries whose initial setup failed. Saved evidence follows the latest successful vehicle-list response; a failed request does not erase it. Replacement keys with no vehicle in common require the user to check an initially unchecked same-account confirmation before any credentials change. Empty accounts and older failed entries may still have no evidence, and vehicle membership can change completely. Neither a vehicle-list hash nor overlapping VINs establishes account identity. The library already exposes the authorized VINs; moving the comparison into it would not provide a provider-issued identity. Complete duplicate-account prevention and wrong-account rejection still need a verified upstream identity contract or agreement from HA maintainers on an acceptable alternative.

Core submission also needs official brand assets, home-assistant.io documentation, Core typing registration and maintainer review. The [new-integration guidance](https://contributors.home-assistant.io/core/integrations/create/) asks for a small initial submission; the full custom integration's four platforms and optional features should not be copied into one first Core PR. No manifest tier is claimed here.
