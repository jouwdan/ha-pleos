"""Optional reverse geocoding for Pleos vehicle locations."""

import logging
import time
from urllib.parse import urlparse

from aiohttp import ClientError, ContentTypeError

LOGGER = logging.getLogger(__name__)
USER_AGENT = "HomeAssistant-PleosVehicleData/0.9.2"
# Conservative provider-neutral request/response cache lifetime. This also stays
# within LocationIQ Free's documented 48-hour request/response caching limit.
CACHE_TTL_SECONDS = 48 * 60 * 60


class ReverseGeocoder:
    """Resolve WGS84 vehicle coordinates using a user-selected service."""

    def __init__(self, session, service_url="", api_key=""):
        self.session = session
        self.service_url = self._reverse_url(service_url)
        self.api_key = api_key.strip() if isinstance(api_key, str) else ""
        self.cache = {}
        self.last_status = None
        self.last_attribution = None

    @staticmethod
    def _reverse_url(url):
        if not isinstance(url, str) or not url.strip():
            return ""
        url = url.strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return url if parsed.path.rstrip("/").endswith("/reverse") else url + "/reverse"

    @staticmethod
    def cache_key(point):
        return round(point["latitude"], 4), round(point["longitude"], 4)

    async def apply(self, car):
        reading = {
            "name": "Address", "value": None, "domain": "sensor", "source": "locations",
            "attributes": {"geocoder": urlparse(self.service_url).netloc if self.service_url else None},
        }
        car["readings"]["location_address"] = reading
        if not self.service_url:
            reading["attributes"]["geocoding_status"] = "service_url_missing"
            return
        point = car.get("location")
        if not point:
            reading["attributes"]["geocoding_status"] = "location_missing"
            return
        key = self.cache_key(point)
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached is None or now - cached["cached_at"] >= CACHE_TTL_SECONDS:
            address = await self._lookup(*key)
            # Cache successful responses only. Failures retry at a later poll.
            if address:
                cached = {"address": address, "cached_at": now, "attribution": self.last_attribution}
                self.cache[key] = cached
            else:
                cached = None
        if cached:
            reading["value"] = cached["address"]
            reading["attributes"]["geocoding_status"] = "ok"
            if cached.get("attribution"):
                reading["attributes"]["attribution"] = cached["attribution"]
        else:
            reading["attributes"]["geocoding_status"] = self.last_status or "lookup_failed"

    async def _lookup(self, latitude, longitude):
        # Some Nominatim-compatible providers (including LocationIQ v1) default
        # to XML, so request JSON explicitly.
        params = {"lat": latitude, "lon": longitude, "format": "json", "addressdetails": 1}
        if self.api_key:
            params["key"] = self.api_key
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self.last_status = None
        self.last_attribution = None
        try:
            async with self.session.get(self.service_url, params=params, headers=headers, timeout=10) as response:
                if response.status != 200:
                    self.last_status = f"http_{response.status}"
                    LOGGER.debug("Reverse geocoding returned HTTP %s", response.status)
                    return None
                try:
                    data = await response.json()
                except (ContentTypeError, ValueError):
                    self.last_status = "invalid_json"
                    LOGGER.debug("Reverse geocoding returned a non-JSON response")
                    return None
        except TimeoutError:
            self.last_status = "timeout"
            LOGGER.debug("Reverse geocoding timed out")
            return None
        except ClientError as error:
            self.last_status = "connection_error"
            LOGGER.debug("Reverse geocoding failed: %s", error)
            return None
        if not isinstance(data, dict):
            self.last_status = "invalid_response"
            return None
        display_name = data.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            self.last_status = "no_display_name"
            return None
        attribution = data.get("attribution") or data.get("licence") or data.get("license")
        if isinstance(attribution, str) and attribution.strip():
            self.last_attribution = attribution.strip()
        self.last_status = "ok"
        return display_name.strip()
