"""OpenStreetMap Nominatim adapter.

All Nominatim-specific knowledge (URL, parameters, response format, type names,
usage-policy rules) stays in this file.

Policy: https://operations.osmfoundation.org/policies/nominatim/
"""

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.integrations.geocoding.base import GeocodingCandidate, GeocodingProviderError
from app.models.location import LocationType

logger = logging.getLogger(__name__)

COORDINATE_PRECISION = Decimal("0.000001")  # matches Numeric(9, 6) in the locations table

# Nominatim "addresstype" -> our LocationType
_ADDRESS_TYPES = {
    "city": LocationType.CITY,
    "town": LocationType.CITY,
    "village": LocationType.CITY,
    "municipality": LocationType.CITY,
    "hamlet": LocationType.CITY,
    "suburb": LocationType.CITY,
    "country": LocationType.REGION,
    "state": LocationType.REGION,
    "region": LocationType.REGION,
    "province": LocationType.REGION,
    "state_district": LocationType.REGION,
    "county": LocationType.REGION,
    "district": LocationType.REGION,
}

# Nominatim (category, type) pairs for transport hubs
_TRANSPORT_TYPES = {
    ("aeroway", "aerodrome"): LocationType.AIRPORT,
    ("aeroway", "terminal"): LocationType.AIRPORT,
    ("railway", "station"): LocationType.RAILWAY_STATION,
    ("railway", "halt"): LocationType.RAILWAY_STATION,
    ("public_transport", "station"): LocationType.RAILWAY_STATION,
    ("amenity", "bus_station"): LocationType.BUS_STATION,
    ("highway", "bus_stop"): LocationType.BUS_STATION,
}


class NominatimGeocodingProvider:
    name = "nominatim"
    attribution = "Data © OpenStreetMap contributors, ODbL 1.0. https://www.openstreetmap.org/copyright"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
        min_interval_seconds: float = 1.0,
        cache_ttl_seconds: int = 86_400,
        max_cache_entries: int = 1_000,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._headers = {"User-Agent": user_agent, "Accept-Language": "en"}
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._cache_ttl = cache_ttl_seconds
        self._max_cache_entries = max_cache_entries

        # Shared by every request in this process: the 1 req/s limit is for the whole app.
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cache: dict[tuple[str, int], tuple[float, list[GeocodingCandidate]]] = {}

    async def search(self, query: str, limit: int) -> list[GeocodingCandidate]:
        cache_key = (query.casefold(), limit)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            logger.debug("Geocoding cache hit")
            return cached[1]

        raw_results = await self._request(query, limit)
        candidates = [c for c in (self._to_candidate(r) for r in raw_results) if c is not None]

        self._store_in_cache(cache_key, candidates)
        return candidates

    async def _request(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
        }
        async with self._rate_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._client.get(
                    f"{self._base_url}/search",
                    params=params,
                    headers=self._headers,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise GeocodingProviderError("Geocoding provider timed out") from exc
            except httpx.HTTPError as exc:
                raise GeocodingProviderError("Could not reach geocoding provider") from exc
            finally:
                self._last_request_at = time.monotonic()

        if response.status_code == 429:
            raise GeocodingProviderError("Geocoding provider rate limit reached")
        if response.status_code == 403:
            # Usually means we are blocked for violating the usage policy. Retrying won't help.
            raise GeocodingProviderError("Geocoding provider refused the request", retryable=False)
        if response.status_code >= 400:
            raise GeocodingProviderError(f"Geocoding provider returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise GeocodingProviderError("Geocoding provider returned invalid JSON") from exc
        if not isinstance(data, list):
            raise GeocodingProviderError("Geocoding provider returned an unexpected format")
        return data

    def _to_candidate(self, raw: dict[str, Any]) -> GeocodingCandidate | None:
        """Map one Nominatim result. Returns None for results we can't use safely."""
        latitude = _parse_coordinate(raw.get("lat"), limit=90)
        longitude = _parse_coordinate(raw.get("lon"), limit=180)
        osm_type, osm_id = raw.get("osm_type"), raw.get("osm_id")
        if latitude is None or longitude is None or not osm_type or osm_id is None:
            # Never invent coordinates or IDs: drop the result instead.
            logger.info("Skipping Nominatim result without usable coordinates or OSM id")
            return None

        address = raw.get("address") or {}
        display_name = raw.get("display_name") or ""
        place_name = raw.get("name") or display_name.split(",")[0].strip()
        if not place_name:
            return None

        return GeocodingCandidate(
            place_name=place_name,
            display_name=display_name or place_name,
            city=address.get("city") or address.get("town") or address.get("village"),
            state=address.get("state"),
            country=address.get("country"),
            latitude=latitude,
            longitude=longitude,
            location_type=_location_type(raw),
            provider_name=self.name,
            # osm_type + osm_id identifies the OSM object itself. Nominatim's own
            # "place_id" can change between database imports, so it is not stored.
            provider_place_id=f"{osm_type}:{osm_id}",
        )

    def _store_in_cache(self, key: tuple[str, int], candidates: list[GeocodingCandidate]) -> None:
        if len(self._cache) >= self._max_cache_entries:
            self._cache.pop(next(iter(self._cache)))  # drop the oldest entry
        self._cache[key] = (time.monotonic() + self._cache_ttl, candidates)


def _parse_coordinate(value: Any, *, limit: int) -> Decimal | None:
    try:
        number = Decimal(str(value)).quantize(COORDINATE_PRECISION)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or abs(number) > limit:
        return None
    return number


def _location_type(raw: dict[str, Any]) -> LocationType:
    transport = _TRANSPORT_TYPES.get((raw.get("category"), raw.get("type")))
    if transport:
        return transport
    return _ADDRESS_TYPES.get(raw.get("addresstype"), LocationType.ADDRESS)
