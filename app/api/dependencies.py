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
    # One shared provider for the whole app (created in main.py's lifespan),
    # so its rate limiter and cache apply to every request.
    return request.app.state.geocoding_provider


def get_location_service(
    provider: GeocodingProvider = Depends(get_geocoding_provider),
) -> LocationResolutionService:
    return LocationResolutionService(
        provider,
        enabled=settings.GEOCODING_ENABLED,
        max_results=settings.GEOCODING_MAX_RESULTS,
    )
