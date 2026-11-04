import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.trip import Trip
from app.repositories.trip_repository import TripRepository

logger = logging.getLogger(__name__)


class TripService:
    def __init__(self, db: AsyncSession):
        self.repository = TripRepository(db)

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

    async def update_trip(self, trip_id: UUID | str, trip_data: dict) -> Trip:
        trip = await self.repository.update(trip_id, trip_data)
        if trip is None:
            raise self._not_found("update", trip_id)
        logger.info("Trip updated: trip_id=%s fields=%s", trip_id, sorted(trip_data))
        return trip

    async def delete_trip(self, trip_id: UUID | str) -> None:
        deleted = await self.repository.delete(trip_id)
        if not deleted:
            raise self._not_found("delete", trip_id)
        logger.info("Trip deleted: trip_id=%s", trip_id)

    async def get_all_trips(self) -> list[Trip]:
        return await self.repository.get_all()
