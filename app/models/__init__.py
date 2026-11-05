# Importing every model here registers all tables on Base.metadata.
# Alembic (migrations/env.py) and the test setup import this package,
# so a new model only needs to be added to this file.
from app.models.base import Base
from app.models.location import Location, LocationType
from app.models.location_provider_ref import LocationProviderRef
from app.models.trip import Trip
from app.models.trip_location import TripLocation, TripLocationRole

__all__ = [
    "Base",
    "Location",
    "LocationProviderRef",
    "LocationType",
    "Trip",
    "TripLocation",
    "TripLocationRole",
]
