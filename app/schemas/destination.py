import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.models.location import LocationType
from app.models.pending_destination import PendingDestinationStatus
from app.models.trip_location import DestinationType, TripLocationStatus
from app.schemas.location import LocationCandidateResponse

PreferenceItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PreferenceList = Annotated[list[PreferenceItem], Field(default_factory=list, max_length=20)]
DurationDays = Annotated[int, Field(ge=1, le=365)]


class _PlaceQuery(BaseModel):
    query: str = Field(min_length=2, max_length=200, examples=["Goa"])

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Query must contain at least 2 non-space characters")
        return cleaned


class DestinationPreferences(BaseModel):
    """Trip-specific wishes for one destination. Stored on TripLocation, never on Location."""

    model_config = ConfigDict(extra="forbid")

    preferred_areas: PreferenceList
    preferred_activities: PreferenceList
    avoid: PreferenceList

    @field_validator("preferred_areas", "preferred_activities", "avoid")
    @classmethod
    def drop_duplicates(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        unique = []
        for value in values:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(value)
        return unique


# ---------- Requests ----------


class DestinationResolveRequest(_PlaceQuery):
    """Step 1 of adding a destination by name: geocode and store candidates as pending."""

    destination_type: DestinationType = DestinationType.PRIMARY
    planned_duration_days: DurationDays | None = None
    preferences: DestinationPreferences = Field(default_factory=DestinationPreferences)


class DestinationReplaceRequest(_PlaceQuery):
    """Start replacing a destination. The type and position come from the old destination."""

    # None = keep the old destination's duration.
    planned_duration_days: DurationDays | None = None
    # Not copied from the old place: "North Goa" means nothing for Kerala.
    preferences: DestinationPreferences = Field(default_factory=DestinationPreferences)


class DestinationAddRequest(BaseModel):
    """Add a destination from a location already stored on the server (no geocoding)."""

    location_id: uuid.UUID
    destination_type: DestinationType = DestinationType.PRIMARY
    planned_duration_days: DurationDays | None = None
    preferences: DestinationPreferences = Field(default_factory=DestinationPreferences)


class DestinationConfirmRequest(BaseModel):
    provider_name: str = Field(max_length=50)
    provider_place_id: str = Field(max_length=255)
    # Required and must be true: leaving it out is NOT consent.
    confirmed: Literal[True]


class DestinationUpdateRequest(BaseModel):
    """PATCH: only the fields sent are changed. Send planned_duration_days: null to clear it."""

    model_config = ConfigDict(extra="forbid")

    destination_type: DestinationType | None = None
    planned_duration_days: DurationDays | None = None

    @model_validator(mode="after")
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if "destination_type" in self.model_fields_set and self.destination_type is None:
            raise ValueError("destination_type cannot be null")
        return self


class DestinationReorderRequest(BaseModel):
    destination_ids: list[uuid.UUID] = Field(min_length=1)

    @field_validator("destination_ids")
    @classmethod
    def no_duplicates(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(values)) != len(values):
            raise ValueError("destination_ids must not contain duplicates")
        return values


# ---------- Responses ----------


class DestinationLocationResponse(BaseModel):
    """Geographic details only; provider internals are not exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    place_name: str
    city: str | None
    state: str | None
    country: str
    latitude: Decimal
    longitude: Decimal
    location_type: LocationType


class DestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trip_id: uuid.UUID
    sequence: int
    destination_type: DestinationType
    planned_duration_days: int | None
    status: TripLocationStatus
    preferences: DestinationPreferences
    replaced_by_id: uuid.UUID | None
    location: DestinationLocationResponse
    created_at: datetime
    updated_at: datetime


class PendingDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trip_id: uuid.UUID
    query: str
    status: PendingDestinationStatus
    destination_type: DestinationType
    planned_duration_days: int | None
    preferences: DestinationPreferences
    replaces_destination_id: uuid.UUID | None = Field(validation_alias="replaces_trip_location_id")
    # Always True: candidates are suggestions until the user confirms one.
    requires_confirmation: bool = True
    candidates: list[LocationCandidateResponse]
