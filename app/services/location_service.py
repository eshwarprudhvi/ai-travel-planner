import logging
from dataclasses import dataclass
from enum import Enum

from app.core.exceptions import AppException
from app.integrations.geocoding import (
    GeocodingCandidate,
    GeocodingProvider,
    GeocodingProviderError,
)

logger = logging.getLogger(__name__)


class ResolutionStatus(str, Enum):
    NO_MATCH = "no_match"
    SINGLE_MATCH = "single_match"
    MULTIPLE_MATCHES = "multiple_matches"


@dataclass(frozen=True)
class LocationResolution:
    query: str
    status: ResolutionStatus
    candidates: list[GeocodingCandidate]
    attribution: str


class LocationResolutionService:
    """Turns a place name into candidate locations. Knows nothing about HTTP or Nominatim.

    Candidates are suggestions only: nothing is saved and nothing is confirmed here,
    even when there is exactly one match. The user must pick (Task 3).
    """

    def __init__(self, provider: GeocodingProvider, *, enabled: bool, max_results: int):
        self._provider = provider
        self._enabled = enabled
        self._max_results = max_results

    async def resolve(self, query: str) -> LocationResolution:
        if not self._enabled:
            raise AppException("Location search is currently disabled", status_code=503)

        normalized = " ".join(query.split())

        try:
            candidates = await self._provider.search(normalized, limit=self._max_results)
        except GeocodingProviderError as exc:
            # Log the reason, but not the query: place names typed by users can be personal.
            logger.warning(
                "Geocoding failed: provider=%s reason=%s retryable=%s",
                self._provider.name, exc, exc.retryable,
            )
            raise AppException("Location search is temporarily unavailable", status_code=503) from exc

        candidates = _deduplicate(candidates)
        logger.info("Location resolved: provider=%s candidates=%d", self._provider.name, len(candidates))

        return LocationResolution(
            query=normalized,
            status=_status_for(len(candidates)),
            candidates=candidates,
            attribution=self._provider.attribution,
        )


def find_candidate(
    candidates: list[GeocodingCandidate], provider_name: str, provider_place_id: str
) -> GeocodingCandidate | None:
    """Return the candidate with this provider identity, or None. Shared by origin and destination confirmation."""
    for candidate in candidates:
        if candidate.provider_name == provider_name and candidate.provider_place_id == provider_place_id:
            return candidate
    return None


def _deduplicate(candidates: list[GeocodingCandidate]) -> list[GeocodingCandidate]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for candidate in candidates:
        key = (candidate.provider_name, candidate.provider_place_id)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _status_for(count: int) -> ResolutionStatus:
    if count == 0:
        return ResolutionStatus.NO_MATCH
    if count == 1:
        return ResolutionStatus.SINGLE_MATCH
    return ResolutionStatus.MULTIPLE_MATCHES
