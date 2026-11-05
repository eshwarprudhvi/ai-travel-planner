from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database import get_db
from app.integrations.geocoding import GeocodingProvider
from app.services.location_service import LocationResolutionService
from app.services.trip_service import TripService


def get_trip_service(db: AsyncSession = Depends(get_db)) -> TripService:
    return TripService(db)


def get_geocoding_provider(request: Request) -> GeocodingProvider:
    # One shared provider for the whole app (created in main.py's lifespan).
    provider = getattr(request.app.state, "geocoding_provider", None)
    if provider is not None:
        return provider

    # Fallback if accessed outside lifespan (e.g. test client without lifespan)
    import httpx
    from app.integrations.geocoding.nominatim import NominatimGeocodingProvider

    if not hasattr(request.app.state, "geocoding_provider"):
        request.app.state.geocoding_provider = NominatimGeocodingProvider(
            httpx.AsyncClient(),
            base_url=settings.NOMINATIM_BASE_URL,
            user_agent=settings.NOMINATIM_USER_AGENT,
            timeout_seconds=settings.GEOCODING_TIMEOUT_SECONDS,
            min_interval_seconds=settings.GEOCODING_MIN_INTERVAL_SECONDS,
            cache_ttl_seconds=settings.GEOCODING_CACHE_TTL_SECONDS,
        )
    return request.app.state.geocoding_provider


def get_location_service(
    provider: GeocodingProvider = Depends(get_geocoding_provider),
) -> LocationResolutionService:
    return LocationResolutionService(
        provider,
        enabled=settings.GEOCODING_ENABLED,
        max_results=settings.GEOCODING_MAX_RESULTS,
    )
