from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trip import Trip


class TripRepository:
    """Database access for trips. Receives its session from outside (see get_db)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, trip_data: dict) -> Trip:
        trip = Trip(**trip_data)
        self.db.add(trip)
        # NOTE: commits its own transaction for now. Move commit to the service
        # once one operation must save several things together (e.g. outbox events).
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
        # Trip has no created_at column yet, so order by start_date (soonest first).
        # Trip.id is a tie-breaker so trips with the same start date keep a stable order.
        statement = select(Trip).order_by(Trip.start_date, Trip.id)
        result = await self.db.execute(statement)
        return list(result.scalars().all())
