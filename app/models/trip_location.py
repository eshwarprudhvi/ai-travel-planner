import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.location import Location
    from app.models.trip import Trip


class TripLocationRole(str, enum.Enum):
    ORIGIN = "origin"
    DESTINATION = "destination"


class TripLocation(Base):
    """Connects a trip to a location in a given role.

    `sequence` orders several locations with the same role,
    e.g. a multi-city trip: destination 0 = Goa, destination 1 = Gokarna.
    """

    __tablename__ = "trip_locations"
    __table_args__ = (
        UniqueConstraint("trip_id", "role", "sequence", name="uq_trip_locations_trip_role_sequence"),
        CheckConstraint("sequence >= 0", name="ck_trip_locations_sequence_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # No separate index: the unique constraint above starts with trip_id,
    # so PostgreSQL can already use it for "WHERE trip_id = ...".
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # Deleting a trip removes its location links (not the shared locations).
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # A location used by any trip cannot be deleted.
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[TripLocationRole] = mapped_column(
        Enum(
            TripLocationRole,
            name="trip_location_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # One-directional on purpose: Trip is left unchanged (task requirement).
    trip: Mapped["Trip"] = relationship()
    location: Mapped["Location"] = relationship(back_populates="trip_locations")
