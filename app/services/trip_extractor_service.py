"""Trip extractor service using Groq structured outputs with strict=True."""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from groq import AsyncGroq
from pydantic import ValidationError

from app.core.config import settings
from app.integrations.llm.client import get_groq_client
from app.integrations.llm.structured import extract_structured
from app.schemas.extractor import (
    TripExtractionRequest,
    TripExtractionResponse,
    TripExtractionResult,
)
from app.schemas.trip import TripCreate

logger = logging.getLogger(__name__)


class TripExtractorService:
    """Service to parse unstructured user travel descriptions into structured trip parameters."""

    def __init__(self, client: AsyncGroq | None = None) -> None:
        self._client = client

    def _get_client(self) -> AsyncGroq:
        if self._client is not None:
            return self._client
        return get_groq_client()

    async def extract(
        self,
        request: TripExtractionRequest | str,
        *,
        reference_date: date | None = None,
    ) -> TripExtractionResponse:
        """Extract structured trip details from user query text.

        Args:
            request: TripExtractionRequest or raw string query.
            reference_date: Reference date for resolving relative dates (defaults to today).

        Returns:
            TripExtractionResponse with extracted result, completeness flag, and optional TripCreate.
        """
        if isinstance(request, str):
            text = request
            ref_date = reference_date or date.today()
        else:
            text = request.text
            ref_date = request.reference_date or reference_date or date.today()

        client = self._get_client()

        system_prompt = (
            "You are an expert travel assistant. Your job is to extract trip planning information "
            "from the user's natural language request into a strict structured format.\n"
            f"Current reference date: {ref_date.isoformat()} ({ref_date.strftime('%A')}).\n"
            "Guidelines:\n"
            "- Resolve relative dates (e.g. 'next weekend', 'in 2 weeks', 'next month') relative to the reference date.\n"
            "- If duration in days is mentioned, extract it as an integer.\n"
            "- Extract monetary budgets as numeric values without currency symbols; extract currency code separately (e.g. 'USD').\n"
            "- If travelers count is not mentioned, default to 1.\n"
            "- Separate preferences into preferred areas, activities, and things to avoid.\n"
            "- If a field is not specified or cannot be inferred with confidence, leave it as null/empty.\n"
            "- Provide a clear, one-sentence summary of the user's trip intent."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        logger.debug("Calling Groq structured extraction with model %s and strict=True", settings.GROQ_MODEL)
        result: TripExtractionResult = await extract_structured(
            client=client,
            response_model=TripExtractionResult,
            messages=messages,
            schema_name="trip_extraction",
            strict=True,
        )

        # Normalize and calculate derived date/duration values
        start_date = result.start_date
        end_date = result.end_date
        duration_days = result.duration_days

        if start_date and duration_days and not end_date:
            end_date = start_date + timedelta(days=duration_days)
            result.end_date = end_date
        elif start_date and end_date and not duration_days:
            diff = (end_date - start_date).days
            if diff > 0:
                result.duration_days = diff

        # Format preferences string for TripCreate
        prefs_parts: list[str] = []
        if result.preferences.preferred_activities:
            prefs_parts.append("Activities: " + ", ".join(result.preferences.preferred_activities))
        if result.preferences.preferred_areas:
            prefs_parts.append("Areas: " + ", ".join(result.preferences.preferred_areas))
        if result.preferences.avoid:
            prefs_parts.append("Avoid: " + ", ".join(result.preferences.avoid))
        combined_preferences = "; ".join(prefs_parts) if prefs_parts else None

        # Check required fields for TripCreate
        missing_fields: list[str] = []
        if not result.origin:
            missing_fields.append("origin")
        if not result.destination:
            missing_fields.append("destination")
        if not result.start_date:
            missing_fields.append("start_date")
        if not result.end_date:
            missing_fields.append("end_date")
        if result.budget is None or result.budget <= Decimal("0"):
            missing_fields.append("budget")

        # Attempt to build valid TripCreate payload if no required fields are missing
        trip_create: TripCreate | None = None
        if not missing_fields:
            try:
                trip_create = TripCreate(
                    origin=result.origin,  # type: ignore[arg-type]
                    destination=result.destination,
                    start_date=result.start_date,  # type: ignore[arg-type]
                    end_date=result.end_date,  # type: ignore[arg-type]
                    budget=result.budget,  # type: ignore[arg-type]
                    travelers=result.travelers,
                    preferences=combined_preferences,
                )
            except (ValidationError, ValueError) as exc:
                logger.warning("Extracted trip data failed TripCreate validation: %s", exc)
                missing_fields.append(f"validation_error: {exc}")

        is_complete = len(missing_fields) == 0 and trip_create is not None

        return TripExtractionResponse(
            extracted=result,
            is_complete=is_complete,
            missing_fields=missing_fields,
            trip_create_payload=trip_create,
        )

