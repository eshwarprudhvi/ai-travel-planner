from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.trip import Trip
from app.repositories.trip_repository import TripRepository


class TripService:
    def __init__(self, db: AsyncSession):
        self.repository = TripRepository(db)

    async def create_trip(self, trip_data: dict) -> Trip:
        return await self.repository.create(trip_data)

    async def get_trip(self, trip_id: UUID | str) -> Trip:
        trip = await self.repository.get_by_id(trip_id)
        if trip is None:
            raise AppException("Trip not found", status_code=404)
        return trip

    async def update_trip(self, trip_id: UUID | str, trip_data: dict) -> Trip:
        trip = await self.repository.update(trip_id, trip_data)
        if trip is None:
            raise AppException("Trip not found", status_code=404)
        return trip

    async def delete_trip(self, trip_id: UUID | str) -> None:
        deleted = await self.repository.delete(trip_id)
        if not deleted:
            raise AppException("Trip not found", status_code=404)
        # Success: nothing to return. Reaching this line without an exception IS the success result.

    async def get_all_trips(self) -> list[Trip]:
        return await self.repository.get_all()
