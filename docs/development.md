# Development and release checks

The standards work starts from `v0.11.0` / `b61b450`, rather than the older 0.6.0 checkout used for the initial review. The unmodified 0.11.0 baseline passed 239 tests on Home Assistant 2026.10.1 with 98% rounded aggregate branch coverage and 100% config-flow coverage. The baseline's weaker modules were setup/unload, device tracking and statistics.

## Reproduce validation

Use Python 3.14 and an isolated environment. Install `requirements-test.txt` for the declared minimum Home Assistant version (2026.10.0), or `requirements-test-current.txt` for the current CI target (2026.10.4). The workflow checks both versions, HACS and Hassfest. Local validation also runs on 2026.10.1. The existing unit tests are collected by pytest. Mypy checks the integration with `strict = true`; `follow_imports = "silent"` retains dependency signatures while limiting diagnostics to the integration. Official HA containers install Core as an editable source tree, so CI sets `MYPYPATH=/usr/src/homeassistant` to check the actual HA APIs.

Install both published libraries at the versions pinned in `custom_components/pleos_vehicle_data/manifest.json`. Their source, tests and release workflows live in [`pleos-client`](https://github.com/jouwdan/pleos-client) and [`nominatim-reverse-client`](https://github.com/jouwdan/nominatim-reverse-client). Then run:

```sh
python -m ruff check .
python -m mypy
python -m coverage run -m pytest -q
python -m coverage report
python -m coverage report --include='*/config_flow.py' --fail-under=100
```

The workflow also checks every integration module against a threshold above 95%, so a high total cannot hide an untested platform. Tests disable network access; synthetic requests never contact vehicles or geocoding providers. Each library separately runs its own tests, lint/type checks, wheel/source builds and distribution checks.

### Completed checks

The 0.13.0 candidate passes 291 tests and 610 subtests on Home Assistant 2026.10.1, with 98.79% branch coverage, 100% config-flow coverage and every module above 95%. Strict mypy passes all 21 integration modules; Ruff passes. Regression tests cover polling migration, supported/legacy entities, first-registration defaults, guarded device removal and rediscovery after a removed vehicle returns without a reload.

Development checkpoints were tested independently on Home Assistant 2026.10.1. They were combined into the [0.12.0 integration release](https://github.com/jouwdan/ha-pleos/releases/tag/v0.12.0):

| Checkpoint | Tests | Branch coverage |
| --- | --- | --- |
| Validation foundation | 260 tests + 598 subtests | 98.70% |
| Protocol boundary | 260 tests + 598 subtests | 98.75% |
| Complete release candidate | 270 tests + 598 subtests | 98.70% |

All three have 100% config-flow coverage and every integration module above 95%. Ruff passes. Hassfest reports zero invalid integrations. Independent review reproduced a deferred-refresh unload leak, then confirmed its regression test passes after the fix.

Both library wheels pass tests on Python 3.11 and 3.14: `pleos-client` has 31 tests and 99% branch coverage; `nominatim-reverse-client` has 13 tests and 100% branch coverage. Strict mypy, Ruff, source/wheel builds, Twine checks and fresh-wheel installations passed locally and in hosted CI. Pleos 0.2.0 and geocoder 0.1.0 were published on PyPI before 0.12.0; fresh installations and the complete [published-dependency validation](https://github.com/jouwdan/ha-pleos/actions/runs/41643712111) passed.

The 0.12.0 live pilot passed restart, account polling, manual refresh and config-entry reload checks, preserving entity identities, preferences and saved values. The 2026-11-04 real-drive follow-up also passed: mileage and all four tyre caches updated, a completed trip arrived once, and driving-time statistics were imported. Another config-entry reload preserved trip/statistics stores and all 88 identities/preferences, with no Pleos/geocoder warnings or errors. Charging and natural outage recovery remain untested.

## Upgrade invariants

| Data | Upgrade contract |
| --- | --- |
| Account namespace | Keep `data.account_id`; legacy entries retain their manufacturer namespace. Credential replacement must not regenerate it. |
| Devices/entities | Keep unique IDs, registry IDs, names, user enablement and unit preferences. |
| Calibration/privacy | Preserve odometer correction, location/reverse-geocoding opt-in, provider URL/key and archive settings. |
| `*.ranges` storage v1 | Retain per-vehicle ranges and tyre pressures; changing displayed availability must not erase the cache. |
| `*.economy` storage v1 | Retain cumulative totals and observation/counting checkpoints; restart and repeated reports must not double count. |
| `*.trips`, `*.trip_entity`, `*.trip_window` v1 | Retain trip deduplication and query progress across reloads and credential replacement. |
| `*.statistics` v1 | Keep the ledger and existing recorder statistic IDs; do not reimport already-counted trips. |
| `*.archive` v1 and raw files | Keep account folder names and deduplication metadata. Unload must not delete files; explicit entry removal keeps raw archive files. |

Public lifecycle tests cover both legacy and current account namespaces. Dedicated tests cover retained values, economy checkpoints, user units, calibration, trip deduplication and failure recovery. Entry removal is intentionally destructive to its derived stores and imported statistics, as described in the user documentation.

## Standards work status

| Finding | Stage and evidence |
| --- | --- |
| HA lifecycle / same-flow recovery | Stage 2: `test_lifecycle.py`, `test_flow_manager.py`, existing config-flow tests; preserve the odometer setup step. |
| Module coverage / lint / typed entry | Stage 2: CI matrix, per-module coverage, Ruff, `models.PleosConfigEntry`. All 21 integration modules now pass strict mypy, including normalized readings, coordinator data, storage records and platform callbacks. |
| Field help / removal docs / concurrency | Stage 2: English translations, configuration/removal guide, all four platform declarations. |
| Pleos protocol boundary | Stage 3: client 0.2.0 owns URL/query construction and range fallback; integration tests preserve UTC queries and response metadata. |
| Geocoder protocol boundary | Stage 3: independent `nominatim-reverse-client`; HA retains privacy choices, caching and state formatting. |
| Availability / refresh failures / logging | Stage 4: current versus historical state semantics, real debouncer and entry-unload tests, endpoint transition logs. |
| Polling options | 0.13.0: managed 15-minute snapshots / 60-minute trips; minor-version migration removes only retired interval settings. |
| Account identity | Investigation only: the current client exposes vehicles and tokens, not a validated stable account identifier. Duplicate checks remain key-based. No namespace migration or vehicle-list hash. |
| Default entities / translations | 0.13.0: supported field catalog, translated numbered ranges, first-registration diagnostic defaults; existing user choices preserved. |
| Stale devices | 0.13.0: explicit device removal only after a successful list omits the vehicle; active, failed, unloaded and foreign-account cases are protected. |
| Official tier / Core submission | Later stage: no official tier is claimed for this custom integration. Prepare the minimal Core submission separately. |

## Library ownership

Both libraries are maintained and released in their own repositories. The geocoder's `publish-geocoder.yml` workflow belongs to `jouwdan/nominatim-reverse-client`; its PyPI trusted publisher uses the protected `pypi-nominatim-reverse-client` environment. Release tags there are `v<version>`, matching `pyproject.toml`. See the [library release instructions](https://github.com/jouwdan/nominatim-reverse-client#releases).

The integration pins geocoder 0.1.1, which updates repository metadata after extraction without changing client behavior. The historical 0.12.0 integration release continues to pin geocoder 0.1.0.

## Preparing an integration release

Every integration validation run installs both manifest-pinned libraries from PyPI in fresh Home Assistant containers. There is no local package build or fallback when a published dependency is missing. Library releases must therefore be published and verified before updating the integration's pins.

Release checklist:

1. Review any library changes and require passing library CI. Publish those changes from their own repositories, then verify the PyPI wheels, source distributions and fresh installations.
2. Update the integration's dependency pins as needed and require passing hosted CI, including HA 2026.10.0/2026.10.4, HACS and Hassfest. Merge the reviewed integration code without publishing it yet.
3. Test a clean installation and upgrade, preserving the invariants listed here. Pilot the candidate through a drive, charging, parked reports, restart, manual refresh and recovery; record any untested scenarios. Automated synthetic responses do not replace this pilot.
4. Update the integration version and [upgrade notes](upgrading.md), then publish the tested commit with a matching `v<version>` tag and GitHub Release. GitHub Releases in this repository belong to the integration and are consumed by HACS. The existing repository layout needs no custom ZIP.

For a merged candidate, validation can also be started with:

```sh
gh workflow run validate.yml --ref main
```

No tag or publication is implied by the manifest version or these notes.
