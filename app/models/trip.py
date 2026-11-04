import uuid
from datetime import date
from decimal import Decimal
from sqlalchemy import Date, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    origin: Mapped[str] = mapped_column(String(100), nullable=False)
    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    budget: Mapped[Decimal] =mapped_column(Numeric(12, 2), nullable=False)
    travelers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preferences: Mapped[str | None] = mapped_column(Text, nullable=True)

