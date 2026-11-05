"""Provider-neutral geocoding contract.

Business logic depends only on this module. Each provider (Nominatim today,
maybe Google or Open-Meteo later) lives in its own adapter that returns
GeocodingCandidate objects, so swapping providers never touches the service.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.models.location import LocationType


@dataclass(frozen=True)
class GeocodingCandidate:
    """One possible match for a query. Not a confirmed location."""

    place_name: str
    display_name: str
    city: str | None
    state: str | None
    country: str | None
    latitude: Decimal  # always from the provider, never invented
    longitude: Decimal
    location_type: LocationType
    provider_name: str
    provider_place_id: str


class GeocodingProviderError(Exception):
    """The provider could not answer (timeout, rate limit, outage, bad response).

    `retryable` tells callers whether trying again later might succeed.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class GeocodingProvider(Protocol):
    name: str
    attribution: str  # text the UI must show next to results (provider licence terms)

    async def search(self, query: str, limit: int) -> list[GeocodingCandidate]:
        """Return up to `limit` candidates. Empty list = no match (not an error)."""
        ...
