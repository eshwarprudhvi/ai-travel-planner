import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TripBase(BaseModel):
    """Editable trip fields and their validation, shared by create and update."""

    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)  # matches Numeric(12, 2)
    travelers: int = Field(default=1, ge=1)
    preferences: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        return self


class TripCreate(TripBase):
    pass


class TripUpdate(TripBase):
    """Full update (PUT): the client sends every editable field."""

    pass


class TripResponse(BaseModel):
    trip_id: str
    message: str
    trip: dict


class TripDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin: str
    destination: str
    start_date: date
    end_date: date
    budget: Decimal
    travelers: int
    preferences: Optional[str] = None
