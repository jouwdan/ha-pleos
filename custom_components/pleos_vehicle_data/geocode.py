"""Optional reverse geocoding for Pleos vehicle locations."""

import logging
import time
from urllib.parse import urlparse

from nominatim_reverse_client import GeocodingError, NominatimClient, reverse_url

LOGGER = logging.getLogger(__name__)
USER_AGENT = "HomeAssistant-PleosVehicleData (https://github.com/jouwdan/ha-pleos)"
CACHE_TTL_SECONDS = 48 * 60 * 60
MAX_CACHE_ENTRIES = 256


class ReverseGeocoder:
    """Resolve WGS84 vehicle coordinates using a user-selected service."""

    def __init__(self, session, service_url="", api_key=""):
        self.session = session
        self.service_url = self._reverse_url(service_url)
        self.api_key = api_key.strip() if isinstance(api_key, str) else ""
        self.client = NominatimClient(session, self.service_url, user_agent=USER_AGENT, api_key=self.api_key)
        self.cache = {}
        self.last_status = None
        self.last_attribution = None

    _reverse_url = staticmethod(reverse_url)

    @staticmethod
    def cache_key(point):
        return round(point["latitude"], 4), round(point["longitude"], 4)

    async def apply(self, car):
        now = time.monotonic()
        self.cache = {key: cached for key, cached in self.cache.items()
                      if now - cached["cached_at"] < CACHE_TTL_SECONDS}
        reading = {
            "name": "Address", "value": None, "domain": "sensor", "source": "locations",
            "attributes": {"geocoder": urlparse(self.service_url).hostname if self.service_url else None},
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
        cached = self.cache.get(key)
        if cached is None:
            address = await self._lookup(*key)
            # Cache successful responses only. Failures retry at a later poll.
            if address:
                cached = {"address": address, "cached_at": now, "attribution": self.last_attribution}
                self.cache[key] = cached
                if len(self.cache) > MAX_CACHE_ENTRIES:
                    del self.cache[next(iter(self.cache))]
            else:
                cached = None
        if cached:
            reading["value"] = cached["address"][:255]
            if len(cached["address"]) > 255:
                reading["attributes"]["full_address"] = cached["address"]
            reading["attributes"]["geocoding_status"] = "ok"
            if cached.get("attribution"):
                reading["attributes"]["attribution"] = cached["attribution"]
        else:
            reading["attributes"]["geocoding_status"] = self.last_status or "lookup_failed"

    async def _lookup(self, latitude, longitude):
        self.last_status = None
        self.last_attribution = None
        try:
            address = await self.client.reverse(latitude, longitude)
        except GeocodingError as error:
            self.last_status = error.code
            LOGGER.debug("Reverse geocoding failed: %s", error.code)
            return None
        self.last_attribution = address.attribution
        self.last_status = "ok"
        return address.display_name
