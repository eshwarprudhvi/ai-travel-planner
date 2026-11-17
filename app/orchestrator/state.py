import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum
from operator import add
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.extractor import TripExtractionResponse


class OrchestratorStatus(StrEnum):
    """Execution status of the orchestrator run."""
    INITIALIZED = "initialized"
    IN_PROGRESS = "in_progress"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"


class OrchestratorInput(BaseModel):
    """Input schema passed by callers to initiate or resume the graph.

    Only these 4 fields can be provided by the caller; all other state fields
    are managed internally by the graph nodes.
    """

    original_text: str = Field(
        ...,
        min_length=3,
        max_length=4000,
        description="The user's original natural language prompt.",
    )
    reference_date: date = Field(
        ...,
        description="Reference date used to resolve relative dates deterministically.",
    )
    extraction: TripExtractionResponse = Field(
        ...,
        description="Full extraction result from TripExtractorService.",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Optional identifier of the user who owns this trip.",
    )

    @model_validator(mode="after")
    def validate_input_consistency(self) -> "OrchestratorInput":
        """Enforce consistency rules so invalid data never reaches the graph."""
        # 1. Ensure original_text is not purely whitespace
        if not self.original_text.strip():
            raise ValueError("original_text must not be empty or blank whitespace.")

        # 2. Rule 1 & 2: Completeness consistency check on extraction
        ext = self.extraction
        if ext.is_complete:
            if ext.missing_fields:
                raise ValueError(
                    f"Inconsistent extraction: is_complete is True, but missing_fields is not empty: {ext.missing_fields}"
                )
            if ext.trip_create_payload is None:
                raise ValueError(
                    "Inconsistent extraction: is_complete is True, but trip_create_payload is missing."
                )
        else:
            if not ext.missing_fields:
                raise ValueError(
                    "Inconsistent extraction: is_complete is False, but missing_fields is empty."
                )

        return self


class OrchestratorState(BaseModel):
    """Full state for the travel planning orchestrator graph."""

    # ---------------------------------------------------------
    # Group: Request (provided by caller)
    # ---------------------------------------------------------
    original_text: str = Field(
        ...,
        min_length=3,
        max_length=4000,
        description="Raw natural language prompt from the user.",
    )
    reference_date: date = Field(
        ...,
        description="Reference date used to resolve relative dates like 'next week'.",
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Identifier of the user requesting the trip, if authenticated.",
    )

    # ---------------------------------------------------------
    # Group: Requirements (extractor output & tracking)
    # ---------------------------------------------------------
    extraction: TripExtractionResponse = Field(
        ...,
        description="Structured extraction result containing trip parameters and completeness status.",
    )
    defaulted_fields: list[str] = Field(
        default_factory=list,
        description="List of fields that were filled with default values (e.g. ['currency', 'travelers']).",
    )

    # ---------------------------------------------------------
    # Group: Clarification (handling missing information)
    # ---------------------------------------------------------
    question: Optional[str] = Field(
        default=None,
        description="Clarification question generated for the user when details are missing.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of required fields missing from requirements, exposed when status is needs_input.",
    )
    answers: Annotated[list[str], add] = Field(
        default_factory=list,
        description="Accumulated list of answers provided by the user across turns (appended).",
    )
    clarification_attempts: int = Field(
        default=0,
        description="Number of clarification questions asked so far to prevent infinite loops.",
    )

    # ---------------------------------------------------------
    # Group: Trip (persisted database record & status)
    # ---------------------------------------------------------
    trip_id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID of the created Trip entity in the database once validated.",
    )
    status: OrchestratorStatus = Field(
        default=OrchestratorStatus.INITIALIZED,
        description="Current lifecycle status of this planning execution.",
    )

    # ---------------------------------------------------------
    # Group: Planning (breakdowns computed before parallel search)
    # ---------------------------------------------------------
    nights: int = Field(
        default=0,
        description="Calculated number of nights for the trip.",
    )
    budget_split: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Target budget allocation across categories (e.g. {'hotels': 1500, 'transport': 800, 'activities': 700}).",
    )

    # ---------------------------------------------------------
    # Group: Results (parallel search node outputs)
    # ---------------------------------------------------------
    hotels: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate accommodation options found by hotel search node.",
    )
    transport: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate transit/flight options found by transport search node.",
    )
    food: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidate dining and culinary recommendations found by food search node.",
    )

    # ---------------------------------------------------------
    # Group: Budget (optimization, selection, and loop control)
    # ---------------------------------------------------------
    selection: Optional[dict[str, Any]] = Field(
        default=None,
        description="Selected combination of hotel, transport, and activities chosen to fit the budget.",
    )
    total_cost: Optional[Decimal] = Field(
        default=None,
        description="Calculated total cost of the selected items.",
    )
    within_budget: bool = Field(
        default=False,
        description="True if the selection meets the user's budget constraints.",
    )
    budget_retries: int = Field(
        default=0,
        description="Count of attempts to adjust options when selections exceed the budget.",
    )

    # ---------------------------------------------------------
    # Group: Output (final artifacts delivered to caller)
    # ---------------------------------------------------------
    itinerary: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Day-by-day itinerary schedule produced by the itinerary node.",
    )
    final_plan: Optional[dict[str, Any]] = Field(
        default=None,
        description="Aggregated final plan object containing full summary, bookings, and itinerary.",
    )

    # ---------------------------------------------------------
    # Group: Diagnostics (accumulated logs across all nodes)
    # ---------------------------------------------------------
    warnings: Annotated[list[str], add] = Field(
        default_factory=list,
        description="Non-fatal notices accumulated across nodes (e.g. 'budget slightly exceeded').",
    )
    errors: Annotated[list[str], add] = Field(
        default_factory=list,
        description="Fatal or diagnostic error messages accumulated during graph execution.",
    )


class OrchestratorOutput(BaseModel):
    """Output schema returned to the caller at the end (or pause) of a graph run.

    Only public fields needed by callers/UI are exposed. Every field here
    corresponds directly to a State field of the exact same name.
    """

    status: OrchestratorStatus = Field(
        ...,
        description="Execution status of the run (needs_input, completed, failed, or in_progress).",
    )
    question: Optional[str] = Field(
        default=None,
        description="Clarification question for the user if more input is required.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Fields still missing when status is needs_input.",
    )
    trip_id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID of the persisted trip in the database once completed.",
    )
    final_plan: Optional[dict[str, Any]] = Field(
        default=None,
        description="Generated trip plan details once completed.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings accumulated during the run.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Error messages if the run failed.",
    )


