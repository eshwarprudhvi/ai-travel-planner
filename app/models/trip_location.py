import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.location import Location
    from app.models.trip import Trip


class TripLocationRole(str, enum.Enum):
    ORIGIN = "origin"
    DESTINATION = "destination"


class TripLocationStatus(str, enum.Enum):
    """Lifecycle of a trip-location link. Only CONFIRMED rows are active.

    Unconfirmed geocoding results never become a TripLocation: they live in
    pending_destinations until the user picks one (see PendingDestination).
    """

    CONFIRMED = "confirmed"
    REPLACED = "replaced"  # kept for history; replaced_by_id points to the successor
    REMOVED = "removed"  # soft-deleted by the user; kept for history


class DestinationType(str, enum.Enum):
    PRIMARY = "primary"
    OPTIONAL = "optional"


def values_enum(enum_cls: type[enum.Enum], name: str, **kwargs) -> Enum:
    """Store enum *values* ("primary"), not Python member names ("PRIMARY")."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
        **kwargs,
    )


class TripLocation(Base):
    """Connects a trip to a location in a given role.

    `sequence` orders several locations with the same role,
    e.g. a multi-city trip: destination 0 = Goa, destination 1 = Gokarna.
    Trip-specific data (type, duration, preferences) lives here, never on Location.
    """

    __tablename__ = "trip_locations"
    __table_args__ = (
        # Partial unique index: only ACTIVE (confirmed) links need unique positions.
        # Historical rows (replaced/removed) keep their old sequence without blocking new ones.
        Index(
            "uq_trip_locations_active_sequence",
            "trip_id",
            "role",
            "sequence",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
        # The same place can't be an active destination of one trip twice.
        Index(
            "uq_trip_locations_active_destination_location",
            "trip_id",
            "location_id",
            unique=True,
            postgresql_where=text("role = 'destination' AND status = 'confirmed'"),
        ),
        CheckConstraint("sequence >= 0", name="ck_trip_locations_sequence_non_negative"),
        CheckConstraint(
            "planned_duration_days IS NULL OR planned_duration_days > 0",
            name="ck_trip_locations_duration_positive",
        ),
        # Destinations always have a type; origins never do.
        CheckConstraint(
            "(role = 'destination') = (destination_type IS NOT NULL)",
            name="ck_trip_locations_destination_type_matches_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Own index: the partial unique indexes above don't cover historical rows.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # Deleting a trip removes its location links (not the shared locations).
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # A location used by any trip cannot be deleted.
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[TripLocationRole] = mapped_column(
        values_enum(TripLocationRole, "trip_location_role"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[TripLocationStatus] = mapped_column(
        values_enum(TripLocationStatus, "trip_location_status"),
        nullable=False,
        default=TripLocationStatus.CONFIRMED,
        server_default=TripLocationStatus.CONFIRMED.value,
    )
    destination_type: Mapped[DestinationType | None] = mapped_column(
        values_enum(DestinationType, "destination_type"), nullable=True
    )
    planned_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # e.g. {"preferred_areas": ["North Goa"], "preferred_activities": [...], "avoid": [...]}.
    # Shape is validated by the DestinationPreferences schema.
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trip_locations.id", ondelete="SET NULL"),
        nullable=True,
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

    # One-directional on purpose: Trip is left unchanged (task requirement).
    trip: Mapped["Trip"] = relationship()
    location: Mapped["Location"] = relationship(back_populates="trip_locations")
