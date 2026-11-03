"""Nominatim-compatible reverse lookup without logging private request data."""

from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError


class GeocodingError(Exception):
    """A provider failure with a public code and no request or response details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Address:
    """The provider's address and attribution, without Home Assistant state limits."""

    display_name: str
    attribution: str | None = None


def reverse_url(value: str) -> str:
    """Normalize an explicit provider URL, rejecting credentials and query data."""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value.strip().rstrip("/")
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
        ):
            return ""
        parsed.port
    except ValueError:
        return ""
    return value if parsed.path.endswith("/reverse") else value + "/reverse"


class NominatimClient:
    """Read an explicitly selected provider, never following redirects.

    The caller owns the HTTP session, coordinate rounding, caching and polling.
    No default provider is selected and no requests or private values are logged.
    """

    def __init__(
        self, session: ClientSession, service_url: str, *, user_agent: str, api_key: str = ""
    ) -> None:
        self.session = session
        self.service_url = reverse_url(service_url)
        self.user_agent = user_agent
        self.api_key = api_key.strip()

    async def reverse(self, latitude: float, longitude: float) -> Address:
        """Return the address or raise a sanitized provider error."""
        if not self.service_url:
            raise GeocodingError("service_url_missing")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not -limit <= value <= limit
            or not isfinite(value)
            for value, limit in ((latitude, 90), (longitude, 180))
        ):
            raise GeocodingError("invalid_coordinates")
        params: dict[str, str | float | int] = {
            "lat": latitude,
            "lon": longitude,
            "format": "json",
            "addressdetails": 1,
        }
        if self.api_key:
            params["key"] = self.api_key
        try:
            async with self.session.get(
                self.service_url,
                params=params,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise GeocodingError(f"http_{response.status}")
                try:
                    data = await response.json()
                except (ContentTypeError, ValueError):
                    raise GeocodingError("invalid_json") from None
        except TimeoutError:
            raise GeocodingError("timeout") from None
        except ClientError:
            raise GeocodingError("connection_error") from None
        if not isinstance(data, dict):
            raise GeocodingError("invalid_response")
        display_name = data.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise GeocodingError("no_display_name")
        attribution = data.get("attribution") or data.get("licence") or data.get("license")
        return Address(
            display_name.strip(),
            attribution.strip() if isinstance(attribution, str) and attribution.strip() else None,
        )
