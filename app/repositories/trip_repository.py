from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.geocoding.base import GeocodingCandidate
from app.repositories.location_repository import LocationRepository
from app.models import (
    Location,
    Trip,
    TripLocation,
    TripLocationRole,
    TripLocationStatus,
)


class TripRepository:
    """Database access for trips and trip locations. Receives its session from outside (see get_db)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, trip_data: dict) -> Trip:
        trip = Trip(**trip_data)
        self.db.add(trip)
        await self.db.commit()
        await self.db.refresh(trip)
        return trip

    async def get_by_id(self, trip_id: UUID | str) -> Trip | None:
        statement = select(Trip).where(Trip.id == trip_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def update(self, trip_id: UUID | str, trip_data: dict) -> Trip | None:
        trip = await self.get_by_id(trip_id)
        if trip is None:
            return None

        for field, value in trip_data.items():
            if field == "id":
                continue  # the primary key must never change
            setattr(trip, field, value)

        await self.db.commit()
        await self.db.refresh(trip)
        return trip

    async def delete(self, trip_id: UUID | str) -> bool:
        trip = await self.get_by_id(trip_id)
        if trip is None:
            return False

        await self.db.delete(trip)
        await self.db.commit()
        return True

    async def get_all(self) -> list[Trip]:
        statement = select(Trip).order_by(Trip.start_date, Trip.id)
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_active_destinations(self, trip_id: UUID | str) -> list[TripLocation]:
        stmt = (
            select(TripLocation)
            .options(selectinload(TripLocation.location))
            .where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.DESTINATION,
                TripLocation.status == TripLocationStatus.CONFIRMED,
            )
            .order_by(TripLocation.sequence)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_active_destinations_for_trips(
        self, trip_ids: list[UUID]
    ) -> dict[UUID, list[TripLocation]]:
        """Bulk query for active destinations across multiple trips (avoids N+1 queries)."""
        if not trip_ids:
            return {}
        stmt = (
            select(TripLocation)
            .options(selectinload(TripLocation.location))
            .where(
                TripLocation.trip_id.in_(trip_ids),
                TripLocation.role == TripLocationRole.DESTINATION,
                TripLocation.status == TripLocationStatus.CONFIRMED,
            )
            .order_by(TripLocation.trip_id, TripLocation.sequence)
        )
        result = await self.db.execute(stmt)
        grouped: dict[UUID, list[TripLocation]] = {t_id: [] for t_id in trip_ids}
        for item in result.scalars().all():
            grouped[item.trip_id].append(item)
        return grouped

    async def get_origin_trip_location(self, trip_id: UUID | str) -> TripLocation | None:
        stmt = (
            select(TripLocation)
            .options(
                selectinload(TripLocation.location).selectinload(Location.provider_refs)
            )
            .where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def persist_origin(
        self, trip: Trip, candidate: GeocodingCandidate
    ) -> TripLocation:
        existing_trip_location = await self.get_origin_trip_location(trip.id)
        if existing_trip_location:
            for ref in existing_trip_location.location.provider_refs:
                if (
                    ref.provider_name == candidate.provider_name
                    and ref.provider_place_id == candidate.provider_place_id
                ):
                    return existing_trip_location

        location = await LocationRepository(self.db).get_or_create_from_candidate(candidate)

        if existing_trip_location:
            existing_trip_location.location = location
        else:
            trip_location = TripLocation(
                trip_id=trip.id,
                location=location,
                role=TripLocationRole.ORIGIN,
                sequence=0,
            )
            self.db.add(trip_location)

        await self.db.commit()
        loaded = await self.get_origin_trip_location(trip.id)
        assert loaded is not None
        return loaded

    async def replace_origin(
        self, trip: Trip, candidate: GeocodingCandidate
    ) -> TripLocation:
        """Atomically replaces the trip's origin association with the new canonical location.
        Does not delete the previous canonical location so other trips can continue referencing it."""
        try:
            location = await LocationRepository(self.db).get_or_create_from_candidate(candidate)

            trip_location = await self.get_origin_trip_location(trip.id)
            if trip_location:
                trip_location.location = location
            else:
                trip_location = TripLocation(
                    trip_id=trip.id,
                    location=location,
                    role=TripLocationRole.ORIGIN,
                    sequence=0,
                )
                self.db.add(trip_location)

            trip.origin = candidate.place_name
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        loaded = await self.get_origin_trip_location(trip.id)
        assert loaded is not None
        return loaded
