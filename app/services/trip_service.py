import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import Location, Trip, TripLocation
from app.repositories.pending_origin_store import (
    PendingOriginStore,
    default_pending_origin_store,
)
from app.repositories.trip_repository import TripRepository
from app.schemas.destination import (
    DestinationLocationResponse,
    DestinationPreferences,
    DestinationResponse,
)
from app.schemas.location import LocationCandidateResponse
from app.schemas.trip import (
    ConfirmedLocationResponse,
    OriginConfirmRequest,
    OriginRejectResponse,
    OriginResponse,
    OriginStatus,
    OriginUpdateConfirmResponse,
    OriginUpdateResponse,
    TripDetailResponse,
)
from app.services.location_service import LocationResolutionService, find_candidate

logger = logging.getLogger(__name__)


def _format_confirmed_location(
    location: Location,
    provider_name: str | None = None,
    provider_place_id: str | None = None,
    display_name: str | None = None,
) -> ConfirmedLocationResponse:
    if not provider_name or not provider_place_id:
        if location.provider_refs:
            ref = location.provider_refs[0]
            provider_name = ref.provider_name
            provider_place_id = ref.provider_place_id

    if not display_name:
        parts = []
        for item in [location.place_name, location.city, location.state, location.country]:
            if item and item not in parts:
                parts.append(item)
        display_name = ", ".join(parts) if parts else location.place_name

    return ConfirmedLocationResponse(
        id=location.id,
        place_name=location.place_name,
        display_name=display_name,
        city=location.city,
        state=location.state,
        country=location.country,
        latitude=location.latitude,
        longitude=location.longitude,
        location_type=location.location_type,
        provider_name=provider_name,
        provider_place_id=provider_place_id,
    )


def _build_trip_detail(trip: Trip, destinations: list[TripLocation]) -> TripDetailResponse:
    dest_responses = [
        DestinationResponse.model_validate(d) for d in destinations
    ]
    if destinations:
        dest_summary = ", ".join(d.location.place_name for d in destinations)
    else:
        dest_summary = trip.destination

    return TripDetailResponse(
        id=trip.id,
        origin=trip.origin,
        destination=dest_summary,
        start_date=trip.start_date,
        end_date=trip.end_date,
        budget=trip.budget,
        travelers=trip.travelers,
        preferences=trip.preferences,
        destinations=dest_responses,
    )


class TripService:
    def __init__(
        self,
        db: AsyncSession,
        pending_origin_store: PendingOriginStore = default_pending_origin_store,
    ):
        self.repository = TripRepository(db)
        self.pending_origin_store = pending_origin_store

    def _not_found(self, action: str, trip_id: UUID | str) -> AppException:
        # Log the ID only, never trip contents (they may hold personal preferences).
        logger.warning("Trip not found during %s: trip_id=%s", action, trip_id)
        return AppException("Trip not found", status_code=404)

    async def create_trip(self, trip_data: dict) -> Trip:
        trip = await self.repository.create(trip_data)
        logger.info("Trip created: trip_id=%s", trip.id)
        return trip

    async def get_trip(self, trip_id: UUID | str) -> Trip:
        trip = await self.repository.get_by_id(trip_id)
        if trip is None:
            raise self._not_found("get", trip_id)
        return trip

    async def get_trip_detail(self, trip_id: UUID | str) -> TripDetailResponse:
        trip = await self.get_trip(trip_id)
        destinations = await self.repository.get_active_destinations(trip.id)
        return _build_trip_detail(trip, destinations)

    async def get_all_trip_details(self) -> list[TripDetailResponse]:
        trips = await self.repository.get_all()
        if not trips:
            return []
        trip_ids = [t.id for t in trips]
        dest_map = await self.repository.get_active_destinations_for_trips(trip_ids)
        return [_build_trip_detail(trip, dest_map.get(trip.id, [])) for trip in trips]

    async def update_trip(
        self, trip_id: UUID | str, trip_data: dict, fields_set: set[str] | None = None
    ) -> TripDetailResponse:
        if fields_set is not None and "destination" in fields_set:
            raise AppException(
                "Destination cannot be updated directly through this endpoint. "
                "Use the dedicated destination workflow at /api/v1/trips/{trip_id}/destinations.",
                status_code=400,
            )

        trip = await self.repository.update(trip_id, trip_data)
        if trip is None:
            raise self._not_found("update", trip_id)
        logger.info("Trip updated: trip_id=%s fields=%s", trip_id, sorted(trip_data))
        destinations = await self.repository.get_active_destinations(trip.id)
        return _build_trip_detail(trip, destinations)

    async def delete_trip(self, trip_id: UUID | str) -> None:
        deleted = await self.repository.delete(trip_id)
        if not deleted:
            raise self._not_found("delete", trip_id)
        logger.info("Trip deleted: trip_id=%s", trip_id)

    async def get_all_trips(self) -> list[Trip]:
        return await self.repository.get_all()

    async def confirm_origin(
        self,
        trip_id: UUID | str,
        request: OriginConfirmRequest,
        location_service: LocationResolutionService,
    ) -> OriginResponse:
        trip = await self.get_trip(trip_id)

        if not request.confirmed:
            logger.info("Confirmation rejected by user: trip_id=%s", trip_id)
            raise AppException("Explicit confirmation is required to confirm the origin", status_code=400)

        if not trip.origin or not trip.origin.strip():
            raise AppException("Trip has no origin text to resolve", status_code=400)

        resolution = await location_service.resolve(trip.origin)
        target_candidate = find_candidate(
            resolution.candidates, request.provider_name, request.provider_place_id
        )

        if not target_candidate:
            logger.warning(
                "Candidate rejected (not in resolution): trip_id=%s provider=%s place_id=%s",
                trip_id,
                request.provider_name,
                request.provider_place_id,
            )
            raise AppException(
                "Selected candidate does not belong to the latest resolution results for this trip",
                status_code=400,
            )

        trip_location = await self.repository.persist_origin(trip, target_candidate)
        self.pending_origin_store.delete(trip.id)

        logger.info(
            "Origin confirmed: trip_id=%s location_id=%s provider_place_id=%s",
            trip_id,
            trip_location.location_id,
            target_candidate.provider_place_id,
        )

        return OriginResponse(
            trip_id=trip.id,
            status=OriginStatus.CONFIRMED.value,
            origin_text=trip.origin,
            origin=trip.origin,
            pending_origin=None,
            location=_format_confirmed_location(
                trip_location.location,
                provider_name=target_candidate.provider_name,
                provider_place_id=target_candidate.provider_place_id,
                display_name=target_candidate.display_name,
            ),
        )

    async def get_origin(
        self,
        trip_id: UUID | str,
        location_service: LocationResolutionService | None = None,
    ) -> OriginResponse:
        trip = await self.get_trip(trip_id)
        trip_location = await self.repository.get_origin_trip_location(trip.id)
        pending = self.pending_origin_store.get(trip.id)
        pending_origin_text = pending.origin_text if pending else None

        if trip_location:
            status = "awaiting_confirmation" if pending_origin_text else OriginStatus.CONFIRMED.value
            return OriginResponse(
                trip_id=trip.id,
                status=status,
                origin_text=trip.origin,
                origin=trip.origin,
                pending_origin=pending_origin_text,
                location=_format_confirmed_location(trip_location.location),
                pending_origin_text=pending_origin_text,
            )

        if not trip.origin or not trip.origin.strip():
            return OriginResponse(
                trip_id=trip.id,
                status=OriginStatus.MISSING.value,
                origin_text=None,
                origin=None,
                pending_origin=pending_origin_text,
                location=None,
                pending_origin_text=pending_origin_text,
            )

        candidates = None
        status = OriginStatus.EXTRACTED.value
        if location_service is not None:
            try:
                resolution = await location_service.resolve(trip.origin)
                if resolution.candidates:
                    candidates = [
                        LocationCandidateResponse.model_validate(c)
                        for c in resolution.candidates
                    ]
                    status = OriginStatus.RESOLVED.value
            except Exception as exc:
                logger.debug("Resolution during get_origin failed, falling back to extracted: %s", exc)

        return OriginResponse(
            trip_id=trip.id,
            status=status,
            origin_text=trip.origin,
            origin=trip.origin,
            pending_origin=pending_origin_text,
            location=None,
            candidates=candidates,
            pending_origin_text=pending_origin_text,
        )

    async def request_origin_update(
        self,
        trip_id: UUID | str,
        origin_text: str,
        location_service: LocationResolutionService,
    ) -> OriginUpdateResponse:
        trip = await self.get_trip(trip_id)

        cleaned_text = " ".join(origin_text.split())

        resolution = await location_service.resolve(cleaned_text)

        self.pending_origin_store.set(
            trip_id=trip.id,
            origin_text=cleaned_text,
            candidates=resolution.candidates,
        )

        logger.info(
            "Pending origin update registered: trip_id=%s new_origin=%s candidates=%d",
            trip.id,
            cleaned_text,
            len(resolution.candidates),
        )

        trip_location = await self.repository.get_origin_trip_location(trip.id)
        confirmed_loc = (
            _format_confirmed_location(trip_location.location)
            if trip_location
            else None
        )

        candidates = [
            LocationCandidateResponse.model_validate(c)
            for c in resolution.candidates
        ]
        status = "resolved" if candidates else "no_match"

        return OriginUpdateResponse(
            trip_id=trip.id,
            pending_origin_text=cleaned_text,
            status=status,
            requires_confirmation=True,
            candidates=candidates,
            confirmed_origin=confirmed_loc,
        )

    async def confirm_origin_update(
        self,
        trip_id: UUID | str,
        request: OriginConfirmRequest,
    ) -> OriginUpdateConfirmResponse:
        trip = await self.get_trip(trip_id)

        # Idempotency safeguard: Check if the trip's origin is already confirmed with the requested candidate
        trip_location = await self.repository.get_origin_trip_location(trip.id)
        if trip_location and trip_location.location:
            is_already_confirmed = any(
                ref.provider_name == request.provider_name
                and ref.provider_place_id == request.provider_place_id
                for ref in trip_location.location.provider_refs
            )
            if is_already_confirmed:
                logger.info(
                    "Origin update already confirmed (idempotent call): trip_id=%s provider=%s place_id=%s",
                    trip.id,
                    request.provider_name,
                    request.provider_place_id,
                )
                # Clear any lingering pending update for this trip
                self.pending_origin_store.delete(trip.id)
                return OriginUpdateConfirmResponse(
                    trip_id=trip.id,
                    origin=trip.origin,
                    pending_origin=None,
                    status="confirmed",
                    location=_format_confirmed_location(
                        trip_location.location,
                        provider_name=request.provider_name,
                        provider_place_id=request.provider_place_id,
                    ),
                )

        pending = self.pending_origin_store.get(trip.id)
        if not pending:
            raise AppException("No pending origin update exists for this trip.", status_code=409)

        if not request.confirmed:
            raise AppException("Explicit confirmation is required to update the origin", status_code=400)

        target_candidate = find_candidate(
            pending.candidates, request.provider_name, request.provider_place_id
        )

        if not target_candidate:
            logger.warning(
                "Invalid candidate submitted for origin update: trip_id=%s provider=%s place_id=%s",
                trip.id,
                request.provider_name,
                request.provider_place_id,
            )
            raise AppException(
                "Selected location is not part of the pending origin update.",
                status_code=400,
            )

        try:
            trip_location = await self.repository.replace_origin(trip, target_candidate)
        except AppException:
            raise
        except Exception as exc:
            logger.exception("Database failure during origin replacement: trip_id=%s error=%s", trip.id, exc)
            raise AppException("Failed to update trip origin due to a server error.", status_code=500)

        # Clear pending store only after successful database commit
        self.pending_origin_store.delete(trip.id)

        logger.info(
            "Origin update confirmed and replaced: trip_id=%s new_location_id=%s place=%s",
            trip.id,
            trip_location.location_id,
            target_candidate.place_name,
        )

        return OriginUpdateConfirmResponse(
            trip_id=trip.id,
            origin=trip.origin,
            pending_origin=None,
            status="confirmed",
            location=_format_confirmed_location(
                trip_location.location,
                provider_name=target_candidate.provider_name,
                provider_place_id=target_candidate.provider_place_id,
                display_name=target_candidate.display_name,
            ),
        )

    async def reject_origin_update(
        self,
        trip_id: UUID | str,
    ) -> OriginRejectResponse:
        trip = await self.get_trip(trip_id)

        pending = self.pending_origin_store.get(trip.id)
        if not pending:
            raise AppException("No pending origin update found for this trip", status_code=400)

        # Clear pending update
        self.pending_origin_store.delete(trip.id)

        # Fetch existing confirmed origin (unchanged)
        trip_location = await self.repository.get_origin_trip_location(trip.id)
        confirmed_loc = (
            _format_confirmed_location(trip_location.location)
            if trip_location
            else None
        )

        logger.info(
            "Origin update rejected: trip_id=%s preserved_origin=%s",
            trip.id,
            trip.origin,
        )

        return OriginRejectResponse(
            trip_id=trip.id,
            status="confirmed" if trip_location else OriginStatus.EXTRACTED.value,
            origin=trip.origin,
            pending_origin=None,
            message="Origin update rejected. Existing origin preserved.",
            location=confirmed_loc,
        )
