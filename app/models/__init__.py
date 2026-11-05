# Importing every model here registers all tables on Base.metadata.
# Alembic (migrations/env.py) and the test setup import this package,
# so a new model only needs to be added to this file.
from app.models.base import Base
from app.models.location import Location, LocationType
from app.models.location_provider_ref import LocationProviderRef
from app.models.pending_destination import PendingDestination, PendingDestinationStatus
from app.models.trip import Trip
from app.models.trip_location import (
    DestinationType,
    TripLocation,
    TripLocationRole,
    TripLocationStatus,
)

__all__ = [
    "Base",
    "DestinationType",
    "Location",
    "LocationProviderRef",
    "LocationType",
    "PendingDestination",
    "PendingDestinationStatus",
    "Trip",
    "TripLocation",
    "TripLocationRole",
    "TripLocationStatus",
]
