import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.location import Location


class LocationProviderRef(Base):
    """Links one of our Locations to a geocoding provider's own place ID.

    Lets several providers (e.g. Open-Meteo, Google) point at the same Location,
    and lets us find an existing Location again from a provider result.
    """

    __tablename__ = "location_provider_refs"
    __table_args__ = (
        UniqueConstraint(
            "provider_name",
            "provider_place_id",
            name="uq_location_provider_refs_provider_place",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # A provider reference means nothing without its location.
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    location: Mapped["Location"] = relationship(back_populates="provider_refs")
