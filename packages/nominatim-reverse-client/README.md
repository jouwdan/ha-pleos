# nominatim-reverse-client

An asynchronous Python client for reverse lookup with an explicitly selected Nominatim-compatible provider. It has no Home Assistant dependency and does not select a public provider, cache results, round coordinates, or start background requests.

Requires Python 3.11 or later and a caller-owned `aiohttp.ClientSession`.

Once the release gate below is complete, install the published distribution with `python -m pip install nominatim-reverse-client`.

## Usage

```python
import aiohttp

from nominatim_reverse_client import GeocodingError, NominatimClient


async def lookup(service_url: str, latitude: float, longitude: float):
    async with aiohttp.ClientSession() as session:
        client = NominatimClient(
            session,
            service_url,
            user_agent="MyApplication (https://example.com/contact)",
        )
        try:
            address = await client.reverse(latitude, longitude)
        except GeocodingError as error:
            # error.code contains no provider URL, API key or coordinates.
            return error.code
        return address
```

`reverse()` returns `Address(display_name, attribution)`. The display name is stripped of surrounding whitespace but is otherwise unchanged. Attribution is taken from `attribution`, `licence`, or `license` in the provider's response. Display the attribution and follow the provider's usage and caching terms.

Pass a base URL or the provider's `/reverse` endpoint. `reverse_url(value)` returns its normalized endpoint, or an empty string when it is missing or invalid. URLs must use HTTP or HTTPS and cannot contain login credentials, query parameters, or fragments. HTTPS protects coordinates and API keys in transit; HTTP is available for providers operated on a trusted local network.

If the selected provider requires a key, pass `api_key="..."`; it is sent using the `key` query parameter. Every lookup also sends `lat`, `lon`, `format=json`, and `addressdetails=1`. Only valid numeric WGS84 coordinates are accepted. Requests use a ten-second timeout and never follow redirects or retry automatically. The client never closes the caller's session.

The caller is responsible for obtaining consent before sending coordinates, choosing a provider, respecting its request limits, caching or rounding as appropriate, and protecting addresses in local history. This client logs nothing. Exceptions contain a code only; provider responses and underlying request exceptions are not included in their displayed traceback.

## Errors

`GeocodingError.code` is one of:

- `service_url_missing`: no valid explicit endpoint was supplied.
- `invalid_coordinates`: coordinates are missing, nonnumeric, nonfinite, or outside WGS84 bounds.
- `http_<status>`: the provider returned a non-200 status, including redirects and rate limits.
- `timeout` or `connection_error`: the request could not complete.
- `invalid_json`, `invalid_response`, or `no_display_name`: the provider response could not supply an address.

Cancellation propagates to the caller. Provider errors are not retried; callers decide when another request is appropriate.

## Development

From this package directory:

```sh
python -m pip install -e '.[dev]'
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
python -m build
python -m twine check dist/*
```

Tests use synthetic responses and never contact a provider. The public `publish-geocoder.yml` workflow validates Python 3.11 and 3.14 and builds both a source distribution and wheel.

## Release gate

The distribution is maintained in this public repository independently of the Home Assistant integration and `pleos-client`. A package release must be published before an integration release pins it.

Before the first publication, a maintainer must register or reserve the `nominatim-reverse-client` project on PyPI and configure a trusted publisher for repository `jouwdan/ha-pleos`, workflow `publish-geocoder.yml`, and environment `pypi-nominatim-reverse-client`. Configure that GitHub environment with required maintainer approval. This account setup cannot be provided by repository code.

After checks and review, tag the approved commit `nominatim-reverse-client-v<version>` matching this package's `pyproject.toml`, then push that tag. The workflow checks the tag/version match, builds the distributions in public CI, and uses OIDC to publish those same artifacts after environment approval. Ordinary integration tags, branch pushes, pull requests, and manual workflow runs cannot publish this distribution. No PyPI token is stored in the repository.

Do not create a GitHub Release for the package tag: GitHub Releases in this repository belong to the Home Assistant integration and are consumed by HACS. Package provenance is the namespaced Git tag and its public build/publish workflow run; package changes are recorded in `CHANGELOG.md`.

Verify the published wheel and source distribution can be installed before publishing the dependent integration release. Provider credentials or live addresses are not required for release validation.

Licensed under the MIT license.
