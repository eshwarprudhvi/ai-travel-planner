from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.location import LocationType
from app.schemas.destination import DestinationResponse
from app.schemas.location import LocationCandidateResponse


class TripBase(BaseModel):
    """Editable trip fields and their validation, shared by create and update."""

    origin: str = Field(min_length=2, max_length=100)
    destination: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Optional legacy destination string; destinations should be confirmed via /destinations.",
    )
    start_date: date
    end_date: date
    budget: Decimal = Field(gt=0, max_digits=12, decimal_places=2)  # matches Numeric(12, 2)
    travelers: int = Field(default=1, ge=1)
    preferences: Optional[str] = None

    @field_validator("destination", mode="before")
    @classmethod
    def validate_destination_optional(cls, v):
        if v is None:
            return None
        cleaned = " ".join(v.split()) if isinstance(v, str) else v
        return cleaned or None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        return self


class TripCreate(TripBase):
    pass


class TripUpdate(BaseModel):
    """Editable trip fields for PUT /trips/{trip_id}.
    
    Destination cannot be mutated through this endpoint.
    All fields are optional to support partial updates without overwriting with None.
    """

    model_config = ConfigDict(extra="forbid")

    origin: Optional[str] = Field(default=None, min_length=2, max_length=100)
    destination: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    travelers: Optional[int] = Field(default=None, ge=1)
    preferences: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date is not None and self.end_date is not None:
            if self.end_date <= self.start_date:
                raise ValueError("End date must be after start date")
            if self.start_date < date.today():
                raise ValueError("Start date cannot be in the past")
        return self


class TripResponse(BaseModel):
    trip_id: str
    message: str
    trip: dict


class TripDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin: str
    destination: Optional[str] = Field(
        default=None,
        description="Legacy summary; derived from active destinations when present, or fallback to stored string.",
    )
    start_date: date
    end_date: date
    budget: Decimal
    travelers: int
    preferences: Optional[str] = None
    destinations: list[DestinationResponse] = Field(
        default_factory=list,
        description="Active confirmed destinations in sequence order.",
    )


class OriginStatus(str, Enum):
    MISSING = "missing"
    EXTRACTED = "extracted"
    RESOLVED = "resolved"
    CONFIRMED = "confirmed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class OriginConfirmRequest(BaseModel):
    provider_name: str = Field(default="nominatim", max_length=50)
    provider_place_id: str | None = Field(default=None, max_length=255)
    candidate_id: str | None = Field(default=None, max_length=255)
    confirmed: bool = Field(default=True, description="Explicit user confirmation")

    @model_validator(mode="after")
    def validate_identifier(self):
        if not self.provider_place_id and not self.candidate_id:
            raise ValueError("Either provider_place_id or candidate_id must be provided")
        if not self.provider_place_id:
            self.provider_place_id = self.candidate_id
        return self


class ConfirmedLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    place_name: str
    display_name: str | None = None
    city: str | None = None
    state: str | None = None
    country: str
    latitude: Decimal
    longitude: Decimal
    location_type: LocationType
    provider_name: str | None = None
    provider_place_id: str | None = None


class OriginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: uuid.UUID
    status: str
    origin_text: str | None = None
    origin: str | None = None
    pending_origin: str | None = None
    location: ConfirmedLocationResponse | None = None
    candidates: list[LocationCandidateResponse] | None = None
    pending_origin_text: str | None = None


class OriginUpdateRequest(BaseModel):
    origin_text: str = Field(min_length=2, max_length=100, examples=["Bangalore"])

    @field_validator("origin_text")
    @classmethod
    def validate_origin_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Origin text must contain at least 2 non-space characters")
        return cleaned


class OriginUpdateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: uuid.UUID
    pending_origin_text: str
    status: str
    requires_confirmation: bool = True
    candidates: list[LocationCandidateResponse]
    confirmed_origin: ConfirmedLocationResponse | None = None


class OriginUpdateConfirmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: uuid.UUID
    origin: str
    pending_origin: str | None = None
    status: str = "confirmed"
    location: ConfirmedLocationResponse | None = None


class OriginRejectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: uuid.UUID
    status: str = "confirmed"
    origin: str
    pending_origin: str | None = None
    message: str = "Origin update rejected. Existing origin preserved."
    location: ConfirmedLocationResponse | None = None
