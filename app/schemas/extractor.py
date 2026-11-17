"""Schemas for AI trip parameter extraction."""

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.trip import TripCreate


class ExtractedPreferences(BaseModel):
    """Specific preferences and constraints extracted from user query."""

    preferred_areas: list[str] = Field(
        default_factory=list,
        description="Specific neighborhoods, regions, or landmarks the user wants to see.",
    )
    preferred_activities: list[str] = Field(
        default_factory=list,
        description="Types of activities, cuisines, or experiences desired (e.g. food tour, museums).",
    )
    avoid: list[str] = Field(
        default_factory=list,
        description="Things the user wants to avoid (e.g. crowded areas, long walks, nightlife).",
    )


class TripExtractionResult(BaseModel):
    """Raw structured fields extracted from the user's natural language input."""

    origin: Optional[str] = Field(
        default=None,
        description="Departure city, state, or airport. None if not mentioned.",
    )
    destination: Optional[str] = Field(
        default=None,
        description="Target destination city, region, or country. None if not mentioned.",
    )
    start_date: Optional[date] = Field(
        default=None,
        description="Trip departure date in YYYY-MM-DD format.",
    )
    end_date: Optional[date] = Field(
        default=None,
        description="Trip return/end date in YYYY-MM-DD format.",
    )
    duration_days: Optional[int] = Field(
        default=None,
        description="Total duration in days (e.g. 5 for a 5-day trip).",
    )
    budget: Optional[Decimal] = Field(
        default=None,
        description="Total estimated budget number (excluding currency symbol).",
    )
    currency: Optional[str] = Field(
        default="USD",
        description="Three-letter currency code (e.g. USD, EUR, JPY).",
    )
    travelers: int = Field(
        default=1,
        description="Number of people traveling.",
    )
    preferences: ExtractedPreferences = Field(
        default_factory=ExtractedPreferences,
        description="Preferences including preferred areas, activities, and things to avoid.",
    )
    summary: Optional[str] = Field(
        default=None,
        description="A concise one-line summary of the extracted trip intent.",
    )


class TripExtractionRequest(BaseModel):
    """Incoming request to extract trip parameters from user query."""

    text: str = Field(
        min_length=3,
        max_length=4000,
        description="Natural language trip description or travel prompt.",
        examples=[
            "I want to visit Tokyo for 5 days from New York starting 2026-11-20 with a budget of 3000 USD."
        ],
    )
    reference_date: Optional[date] = Field(
        default=None,
        description="Optional reference date for resolving relative dates like 'next Monday' or 'in two weeks'. Defaults to today.",
    )


class TripExtractionResponse(BaseModel):
    """Response containing extracted trip data and validation status."""

    extracted: TripExtractionResult
    is_complete: bool = Field(
        description="True if all required fields to create a trip (origin, destination, dates, budget) are present."
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of required fields missing from user input (e.g. ['origin', 'budget']).",
    )
    trip_create_payload: Optional[TripCreate] = Field(
        default=None,
        description="Pre-populated TripCreate schema if complete and valid, ready for trip creation.",
    )

