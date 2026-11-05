from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.location import LocationType
from app.services.location_service import ResolutionStatus


class LocationResolveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200, examples=["Hyderabad"])

    @field_validator("query")
    @classmethod
    def must_contain_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Query must contain at least 2 non-space characters")
        return cleaned


class LocationCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    place_name: str
    display_name: str
    city: str | None
    state: str | None
    country: str | None
    latitude: Decimal
    longitude: Decimal
    location_type: LocationType
    provider_name: str
    provider_place_id: str


class LocationResolveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str
    status: ResolutionStatus
    # Always True for now: candidates are suggestions, the user must pick one.
    requires_confirmation: bool = True
    candidates: list[LocationCandidateResponse]
    attribution: str
