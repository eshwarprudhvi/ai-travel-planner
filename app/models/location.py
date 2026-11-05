import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # imports for type hints only; avoids circular imports at runtime
    from app.models.location_provider_ref import LocationProviderRef
    from app.models.trip_location import TripLocation


class LocationType(str, enum.Enum):
    CITY = "city"
    AIRPORT = "airport"
    RAILWAY_STATION = "railway_station"
    BUS_STATION = "bus_station"
    ADDRESS = "address"
    REGION = "region"


class Location(Base):
    """A real-world place, stored once and reused by any number of trips."""

    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_locations_latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_locations_longitude_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    place_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False)

    # Numeric(9, 6): exact to 6 decimal places (~11 cm), up to ±180.000000.
    # Exact values mean the same coordinates always compare equal (useful for deduplication).
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

    location_type: Mapped[LocationType] = mapped_column(
        Enum(
            LocationType,
            name="location_type",
            # store "railway_station", not the Python member name "RAILWAY_STATION"
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    provider_refs: Mapped[list["LocationProviderRef"]] = relationship(
        back_populates="location",
        cascade="all, delete-orphan",
    )
    trip_locations: Mapped[list["TripLocation"]] = relationship(
        back_populates="location",
    )
