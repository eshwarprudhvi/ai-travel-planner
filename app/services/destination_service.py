import logging
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.integrations.geocoding.base import GeocodingCandidate
from app.models import (
    DestinationType,
    LocationType,
    PendingDestination,
    PendingDestinationStatus,
    Trip,
    TripLocation,
    TripLocationRole,
    TripLocationStatus,
)
from app.repositories.destination_repository import DestinationRepository
from app.repositories.location_repository import LocationRepository
from app.services.location_service import LocationResolutionService, find_candidate

logger = logging.getLogger(__name__)


def candidate_to_json(candidate: GeocodingCandidate) -> dict:
    data = asdict(candidate)
    data["latitude"] = str(candidate.latitude)  # str keeps the exact decimal value
    data["longitude"] = str(candidate.longitude)
    data["location_type"] = candidate.location_type.value
    return data


def candidate_from_json(data: dict) -> GeocodingCandidate:
    return GeocodingCandidate(
        **{
            **data,
            "latitude": Decimal(data["latitude"]),
            "longitude": Decimal(data["longitude"]),
            "location_type": LocationType(data["location_type"]),
        }
    )


class DestinationService:
    """Business rules for a trip's destinations.

    Every public method that changes data is one transaction: it either commits
    everything or rolls everything back (see _commit).
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = DestinationRepository(db)
        self.locations = LocationRepository(db)

    # ---------- helpers ----------

    async def _lock_trip(self, trip_id: UUID):
        trip = await self.repository.lock_trip(trip_id)
        if trip is None:
            logger.warning("Trip not found for destination operation: trip_id=%s", trip_id)
            raise AppException("Trip not found", status_code=404)
        return trip

    async def _get_active(self, trip_id: UUID, destination_id: UUID) -> TripLocation:
        destination = await self.repository.get_destination(trip_id, destination_id)
        if destination is None:
            raise AppException("Destination not found", status_code=404)
        if destination.status != TripLocationStatus.CONFIRMED:
            raise AppException(
                f"Destination is {destination.status.value} and can no longer be changed",
                status_code=409,
            )
        return destination

    async def _ensure_not_duplicate(self, trip_id: UUID, location_id: UUID) -> None:
        if await self.repository.find_active_by_location(trip_id, location_id) is not None:
            raise AppException("This place is already a destination of the trip", status_code=409)

    async def _run(self, operation):
        """Run `operation` and commit as ONE transaction; on any failure roll back all of it.

        IntegrityError can come from a flush inside the operation or from the commit
        (e.g. a concurrent request created the same destination first): both become 409.
        """
        try:
            result = await operation()
            await self.db.commit()
            return result
        except IntegrityError as exc:
            await self.db.rollback()
            logger.warning("Destination change conflicted: %s", type(exc.orig).__name__)
            raise AppException("The destination change conflicts with existing data", status_code=409) from exc
        except Exception:
            await self.db.rollback()
            raise

    async def _reload(self, trip_id: UUID, destination_id: UUID) -> TripLocation:
        destination = await self.repository.get_destination(trip_id, destination_id)
        if destination is None:  # only possible if deleted concurrently
            raise AppException("Destination not found", status_code=404)
        return destination

    # ---------- reads ----------

    async def list_destinations(self, trip_id: UUID, include_history: bool = False) -> list[TripLocation]:
        await self._ensure_trip_exists(trip_id)
        return await self.repository.list_destinations(trip_id, include_history)

    async def _ensure_trip_exists(self, trip_id: UUID) -> None:
        if await self.db.get(Trip, trip_id) is None:
            raise AppException("Trip not found", status_code=404)

    # ---------- add ----------

    async def resolve_destination(
        self,
        trip_id: UUID,
        query: str,
        location_service: LocationResolutionService,
        *,
        destination_type: DestinationType,
        planned_duration_days: int | None,
        preferences: dict,
        replaces: TripLocation | None = None,
    ) -> PendingDestination:
        """Geocode `query` and save the candidates as a pending item. Nothing is confirmed."""
        await self._ensure_trip_exists(trip_id)
        # Call the provider outside any write transaction (it may be slow).
        resolution = await location_service.resolve(query)

        async def operation():
            pending = PendingDestination(
                trip_id=trip_id,
                query=resolution.query,
                candidates=[candidate_to_json(c) for c in resolution.candidates],
                destination_type=destination_type,
                planned_duration_days=planned_duration_days,
                preferences=preferences,
                replaces_trip_location_id=replaces.id if replaces else None,
            )
            return await self.repository.add_pending(pending)

        pending = await self._run(operation)
        logger.info(
            "Pending destination created: trip_id=%s pending_id=%s candidates=%d replaces=%s",
            trip_id, pending.id, len(resolution.candidates), pending.replaces_trip_location_id,
        )
        return pending

    async def start_replacement(
        self,
        trip_id: UUID,
        destination_id: UUID,
        query: str,
        location_service: LocationResolutionService,
        *,
        planned_duration_days: int | None,
        preferences: dict,
    ) -> PendingDestination:
        old = await self._get_active(trip_id, destination_id)
        return await self.resolve_destination(
            trip_id,
            query,
            location_service,
            destination_type=old.destination_type,
            planned_duration_days=planned_duration_days if planned_duration_days is not None else old.planned_duration_days,
            preferences=preferences,
            replaces=old,
        )

    async def confirm_pending(
        self, trip_id: UUID, pending_id: UUID, provider_name: str, provider_place_id: str
    ) -> TripLocation:
        """Turn the user's chosen candidate into an active destination (or a replacement)."""

        async def operation():
            await self._lock_trip(trip_id)
            pending = await self.repository.get_pending(trip_id, pending_id)
            if pending is None:
                raise AppException("Pending destination not found", status_code=404)
            if pending.status != PendingDestinationStatus.PENDING:
                raise AppException(f"Pending destination is already {pending.status.value}", status_code=409)

            # Only candidates the server itself stored for THIS pending item are accepted.
            stored = [candidate_from_json(c) for c in pending.candidates]
            candidate = find_candidate(stored, provider_name, provider_place_id)
            if candidate is None:
                logger.warning(
                    "Destination candidate rejected: trip_id=%s pending_id=%s", trip_id, pending_id
                )
                raise AppException(
                    "Selected candidate is not part of this pending destination", status_code=400
                )

            location = await self.locations.get_or_create_from_candidate(candidate)

            if pending.replaces_trip_location_id is not None:
                destination = await self._replace(trip_id, pending, location.id)
            else:
                await self._ensure_not_duplicate(trip_id, location.id)
                destination = await self.repository.add(
                    TripLocation(
                        trip_id=trip_id,
                        location_id=location.id,
                        role=TripLocationRole.DESTINATION,
                        sequence=await self.repository.next_sequence(trip_id),
                        destination_type=pending.destination_type,
                        planned_duration_days=pending.planned_duration_days,
                        preferences=pending.preferences,
                    )
                )
            pending.status = PendingDestinationStatus.CONFIRMED
            return destination.id

        destination_id = await self._run(operation)
        logger.info(
            "Destination confirmed: trip_id=%s pending_id=%s destination_id=%s",
            trip_id, pending_id, destination_id,
        )
        return await self._reload(trip_id, destination_id)

    async def _replace(self, trip_id: UUID, pending: PendingDestination, location_id: UUID) -> TripLocation:
        old = await self._get_active(trip_id, pending.replaces_trip_location_id)
        if old.location_id == location_id:
            raise AppException("The new destination is the same place as the old one", status_code=409)
        await self._ensure_not_duplicate(trip_id, location_id)

        # Retire the old link first so its position is free in the partial unique index.
        old.status = TripLocationStatus.REPLACED
        await self.db.flush()

        new = await self.repository.add(
            TripLocation(
                trip_id=trip_id,
                location_id=location_id,
                role=TripLocationRole.DESTINATION,
                sequence=old.sequence,
                destination_type=pending.destination_type,
                planned_duration_days=pending.planned_duration_days,
                preferences=pending.preferences,
            )
        )
        old.replaced_by_id = new.id
        return new

    async def add_existing_location(
        self,
        trip_id: UUID,
        location_id: UUID,
        *,
        destination_type: DestinationType,
        planned_duration_days: int | None,
        preferences: dict,
    ) -> TripLocation:
        """Add a destination from a Location already stored (and therefore validated) on the server."""

        async def operation():
            await self._lock_trip(trip_id)
            if await self.locations.get_by_id(location_id) is None:
                raise AppException("Location not found", status_code=404)
            await self._ensure_not_duplicate(trip_id, location_id)
            destination = await self.repository.add(
                TripLocation(
                    trip_id=trip_id,
                    location_id=location_id,
                    role=TripLocationRole.DESTINATION,
                    sequence=await self.repository.next_sequence(trip_id),
                    destination_type=destination_type,
                    planned_duration_days=planned_duration_days,
                    preferences=preferences,
                )
            )
            return destination.id

        destination_id = await self._run(operation)
        logger.info("Destination added from location: trip_id=%s destination_id=%s", trip_id, destination_id)
        return await self._reload(trip_id, destination_id)

    async def cancel_pending(self, trip_id: UUID, pending_id: UUID) -> None:
        async def operation():
            await self._lock_trip(trip_id)
            pending = await self.repository.get_pending(trip_id, pending_id)
            if pending is None:
                raise AppException("Pending destination not found", status_code=404)
            if pending.status != PendingDestinationStatus.PENDING:
                raise AppException(f"Pending destination is already {pending.status.value}", status_code=409)
            pending.status = PendingDestinationStatus.CANCELLED

        await self._run(operation)
        logger.info("Pending destination cancelled: trip_id=%s pending_id=%s", trip_id, pending_id)

    # ---------- update ----------

    async def update_destination(self, trip_id: UUID, destination_id: UUID, changes: dict) -> TripLocation:
        """Change trip-specific fields only. The canonical Location is never touched."""
        allowed = {"destination_type", "planned_duration_days", "preferences"}

        async def operation():
            await self._lock_trip(trip_id)
            destination = await self._get_active(trip_id, destination_id)
            for field, value in changes.items():
                if field in allowed:
                    setattr(destination, field, value)

        await self._run(operation)
        logger.info("Destination updated: trip_id=%s destination_id=%s fields=%s", trip_id, destination_id, sorted(changes))
        return await self._reload(trip_id, destination_id)

    # ---------- reorder ----------

    async def reorder(self, trip_id: UUID, destination_ids: list[UUID]) -> list[TripLocation]:
        async def operation():
            await self._lock_trip(trip_id)
            active = await self.repository.list_active(trip_id)
            by_id = {d.id: d for d in active}

            if len(set(destination_ids)) != len(destination_ids):
                raise AppException("destination_ids must not contain duplicates", status_code=400)
            unknown = [str(i) for i in destination_ids if i not in by_id]
            if unknown:
                raise AppException(
                    "Some destinations do not belong to this trip or are not active",
                    status_code=400,
                    details={"unknown_ids": unknown},
                )
            if len(destination_ids) != len(active):
                raise AppException(
                    "The new order must list every active destination exactly once",
                    status_code=400,
                    details={"expected": len(active), "received": len(destination_ids)},
                )
            await self.repository.apply_order([by_id[i] for i in destination_ids])

        await self._run(operation)
        logger.info("Destinations reordered: trip_id=%s count=%d", trip_id, len(destination_ids))
        return await self.repository.list_active(trip_id)

    # ---------- remove ----------

    async def remove(self, trip_id: UUID, destination_id: UUID) -> None:
        """Soft-delete: the link is kept as history; the Location and its provider refs stay."""

        async def operation():
            await self._lock_trip(trip_id)
            destination = await self._get_active(trip_id, destination_id)
            destination.status = TripLocationStatus.REMOVED
            await self.db.flush()
            # Close the gap: remaining active destinations become 0..n-1 again.
            await self.repository.apply_order(await self.repository.list_active(trip_id))

        await self._run(operation)
        logger.info("Destination removed: trip_id=%s destination_id=%s", trip_id, destination_id)
