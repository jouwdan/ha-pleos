# Development and release checks

The standards work starts from `v0.11.0` / `b61b450`, rather than the older 0.6.0 checkout used for the initial review. The unmodified 0.11.0 baseline passed 239 tests on Home Assistant 2026.10.1 with 98% rounded aggregate branch coverage and 100% config-flow coverage. The baseline's weaker modules were setup/unload, device tracking and statistics.

## Reproduce validation

Use Python 3.14 and an isolated environment. Install `requirements-test.txt` for the declared minimum Home Assistant version (2026.10.0), or `requirements-test-current.txt` for the current CI target (2026.10.4). Both versions passed [hosted validation](https://github.com/jouwdan/ha-pleos/actions/runs/41636070774), including HACS and Hassfest. Local validation also passed on 2026.10.1. The existing unit tests are collected by pytest.

Build/install the separately released `pleos-client` version pinned in the manifest. During release preparation its sibling source checkout can be installed instead. Build/install `packages/nominatim-reverse-client` as its own distribution. Then run:

```sh
python -m ruff check .
python -m coverage run -m pytest -q
python -m coverage report
python -m coverage report --include='*/config_flow.py' --fail-under=100
```

The workflow also checks every integration module against a threshold above 95%, so a high total cannot hide an untested platform. Tests disable network access; synthetic requests never contact vehicles or geocoding providers. Each library separately runs its own tests, lint/type checks, wheel/source builds and distribution checks.

### Completed checks

Development checkpoints were tested independently on Home Assistant 2026.10.1. They are combined into one 0.12.0 integration release:

| Checkpoint | Tests | Branch coverage |
| --- | --- | --- |
| Validation foundation | 260 tests + 598 subtests | 98.70% |
| Protocol boundary | 260 tests + 598 subtests | 98.75% |
| Complete release candidate | 270 tests + 598 subtests | 98.70% |

All three have 100% config-flow coverage and every integration module above 95%. Ruff passes. Hassfest reports zero invalid integrations. Independent review reproduced a deferred-refresh unload leak, then confirmed its regression test passes after the fix.

Both library wheels pass tests on Python 3.11 and 3.14: `pleos-client` has 31 tests and 99% branch coverage; `nominatim-reverse-client` has 13 tests and 100% branch coverage. Strict mypy, Ruff, source/wheel builds, Twine checks and fresh-wheel installations pass locally and in hosted CI. Pleos 0.2.0 is published on PyPI; a fresh installation passed all 31 tests and `pip check`. Geocoder publication and the complete published-dependency validation remain release gates.

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
| Module coverage / lint / typed entry | Stage 2: CI matrix, per-module coverage, Ruff, `models.PleosConfigEntry`. Full strict typing remains later work. |
| Field help / removal docs / concurrency | Stage 2: English translations, configuration/removal guide, all four platform declarations. |
| Pleos protocol boundary | Stage 3: client 0.2.0 owns URL/query construction and range fallback; integration tests preserve UTC queries and response metadata. |
| Geocoder protocol boundary | Stage 3: independent `nominatim-reverse-client`; HA retains privacy choices, caching and state formatting. |
| Availability / refresh failures / logging | Stage 4: current versus historical state semantics, real debouncer and entry-unload tests, endpoint transition logs. |
| Polling options | Later work: intervals remain unchanged in this release. |
| Account identity | Investigation only: the current client exposes vehicles and tokens, not a validated stable account identifier. Duplicate checks remain key-based. No namespace migration or vehicle-list hash. |
| Default entities / dynamic translations / stale devices | Later stage: do not retire entities or delete registry entries during this work. |
| Official tier / Core submission | Later stage: no official tier is claimed for this custom integration. Prepare the minimal Core submission separately. |

## Preparing the single 0.12.0 release

The integration branch `codex/ha-standards-stages-1-4` contains one commit per fix for review, all targeting 0.12.0. The dependency changes were merged in [pleos-client PR #1](https://github.com/jouwdan/pleos-client/pull/1) for [client 0.2.0](https://github.com/jouwdan/pleos-client/releases/tag/v0.2.0). The library releases below are prerequisites, not separate Home Assistant releases.

Normal PR validation builds the in-repository geocoder and installs the pinned `pleos-client` 0.2.0 from PyPI. The manifest pins both public package versions. This lets the HA matrix test the geocoder candidate before publication; a green PR run does **not** prove the geocoder is on PyPI.

For the release check, run **Validate → Run workflow → Release check: install the pinned libraries from PyPI** on the reviewed integration branch. That mode skips the local geocoder build and runs the complete matrix against packages installed from PyPI in fresh containers. Version tags also use this mode. There is no fallback to local wheels when a published dependency is missing.

Release checklist:

1. Review both PRs and require passing hosted CI, including HA 2026.10.0/2026.10.4 and both library Python versions. Merge the reviewed code without publishing the integration yet.
2. **Complete:** `pleos-client` 0.2.0 was published through its approved GitHub Release/OIDC workflow; its PyPI wheel and source distribution are available and its fresh installation passed validation.
3. Configure the geocoder's PyPI trusted publisher and protected environment, then push `nominatim-reverse-client-v0.1.0` at the reviewed package commit. **Do not create a GitHub Release for this package tag**; HACS uses this repository's GitHub Releases for the integration. See the [package instructions](../packages/nominatim-reverse-client/README.md#release-gate).
4. Verify both packages' wheels and source distributions are available from PyPI. Run the published-dependency check above and test a clean installation and upgrade, preserving the invariants listed here.
5. Pilot the complete candidate on a real installation through a drive, charging, parked reports, restart, manual refresh and recovery. Automated synthetic responses do not replace this pilot.
6. Publish the tested integration commit as **v0.12.0**, with the [0.12.0 upgrade notes](upgrading.md#availability-refresh-reliability-and-api-clients-in-0120), once all checks pass. The existing HACS repository layout needs no custom ZIP. The rollback procedure is included in those notes.

For a merged candidate, the published-dependency check can also be started with:

```sh
gh workflow run validate.yml --ref main -f published_dependencies=true
```

No tag or publication is implied by the manifest version or these notes.
