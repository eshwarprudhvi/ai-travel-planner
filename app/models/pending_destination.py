import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.trip_location import DestinationType, values_enum


class PendingDestinationStatus(str, enum.Enum):
    PENDING = "pending"  # candidates shown, waiting for the user's choice
    CONFIRMED = "confirmed"  # the user picked a candidate; a TripLocation was created
    CANCELLED = "cancelled"


class PendingDestination(Base):
    """A server-side snapshot of geocoding candidates waiting for user confirmation.

    Confirmation is only accepted for a candidate stored here, for this trip.
    That is how the server avoids trusting client-sent coordinates or place IDs,
    and why the provider isn't called again at confirm time.
    """

    __tablename__ = "pending_destinations"
    __table_args__ = (
        CheckConstraint(
            "planned_duration_days IS NULL OR planned_duration_days > 0",
            name="ck_pending_destinations_duration_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    # List of GeocodingCandidate dicts (coordinates stored as strings to stay exact).
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[PendingDestinationStatus] = mapped_column(
        values_enum(PendingDestinationStatus, "pending_destination_status"),
        nullable=False,
        default=PendingDestinationStatus.PENDING,
        server_default=PendingDestinationStatus.PENDING.value,
    )
    # What the destination will look like once confirmed.
    destination_type: Mapped[DestinationType] = mapped_column(
        values_enum(DestinationType, "destination_type"), nullable=False
    )
    planned_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Set when this pending item replaces an existing destination (the "replace" flow).
    replaces_trip_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_locations.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
