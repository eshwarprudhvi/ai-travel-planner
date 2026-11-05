from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.geocoding.base import GeocodingCandidate
from app.models import (
    Location,
    LocationProviderRef,
    Trip,
    TripLocation,
    TripLocationRole,
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

    async def find_location_by_provider_ref(
        self, provider_name: str, provider_place_id: str
    ) -> Location | None:
        stmt = (
            select(Location)
            .join(Location.provider_refs)
            .options(selectinload(Location.provider_refs))
            .where(
                LocationProviderRef.provider_name == provider_name,
                LocationProviderRef.provider_place_id == provider_place_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def find_location_by_coordinates(
        self, latitude: Decimal, longitude: Decimal
    ) -> Location | None:
        stmt = (
            select(Location)
            .options(selectinload(Location.provider_refs))
            .where(
                Location.latitude == latitude,
                Location.longitude == longitude,
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

        location = await self.find_location_by_provider_ref(
            candidate.provider_name, candidate.provider_place_id
        )
        if not location:
            location = await self.find_location_by_coordinates(
                candidate.latitude, candidate.longitude
            )
            if location:
                ref = LocationProviderRef(
                    location=location,
                    provider_name=candidate.provider_name,
                    provider_place_id=candidate.provider_place_id,
                )
                self.db.add(ref)
            else:
                location = Location(
                    place_name=candidate.place_name,
                    city=candidate.city,
                    state=candidate.state,
                    country=candidate.country or "",
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                    location_type=candidate.location_type,
                )
                ref = LocationProviderRef(
                    location=location,
                    provider_name=candidate.provider_name,
                    provider_place_id=candidate.provider_place_id,
                )
                self.db.add(location)
                self.db.add(ref)

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
            location = await self.find_location_by_provider_ref(
                candidate.provider_name, candidate.provider_place_id
            )
            if not location:
                location = await self.find_location_by_coordinates(
                    candidate.latitude, candidate.longitude
                )
                if location:
                    ref = LocationProviderRef(
                        location=location,
                        provider_name=candidate.provider_name,
                        provider_place_id=candidate.provider_place_id,
                    )
                    self.db.add(ref)
                else:
                    location = Location(
                        place_name=candidate.place_name,
                        city=candidate.city,
                        state=candidate.state,
                        country=candidate.country or "",
                        latitude=candidate.latitude,
                        longitude=candidate.longitude,
                        location_type=candidate.location_type,
                    )
                    ref = LocationProviderRef(
                        location=location,
                        provider_name=candidate.provider_name,
                        provider_place_id=candidate.provider_place_id,
                    )
                    self.db.add(location)
                    self.db.add(ref)

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
