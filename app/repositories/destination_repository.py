from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    PendingDestination,
    Trip,
    TripLocation,
    TripLocationRole,
    TripLocationStatus,
)

# Sequences are moved this far out of the way during a reorder, so the partial unique
# index on (trip_id, role, sequence) never sees two active rows at the same position.
_REORDER_OFFSET = 1_000_000


class DestinationRepository:
    """Database access for trip destinations. Never commits: the service owns the transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_trip(self, trip_id: UUID) -> Trip | None:
        """SELECT ... FOR UPDATE: serialises concurrent destination changes on one trip,
        so two requests can't pick the same sequence or reorder at the same time."""
        stmt = select(Trip).where(Trip.id == trip_id).with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def _destinations(self, trip_id: UUID):
        return (
            select(TripLocation)
            .options(selectinload(TripLocation.location))
            .where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.DESTINATION,
            )
            # The session keeps objects after commit; refresh them from the DB.
            .execution_options(populate_existing=True)
        )

    async def list_destinations(self, trip_id: UUID, include_history: bool) -> list[TripLocation]:
        stmt = self._destinations(trip_id)
        if not include_history:
            stmt = stmt.where(TripLocation.status == TripLocationStatus.CONFIRMED)
        is_active = case((TripLocation.status == TripLocationStatus.CONFIRMED, 0), else_=1)
        stmt = stmt.order_by(is_active, TripLocation.sequence, TripLocation.created_at)
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_active(self, trip_id: UUID) -> list[TripLocation]:
        return await self.list_destinations(trip_id, include_history=False)

    async def get_destination(self, trip_id: UUID, destination_id: UUID) -> TripLocation | None:
        stmt = self._destinations(trip_id).where(TripLocation.id == destination_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_active_by_location(self, trip_id: UUID, location_id: UUID) -> TripLocation | None:
        stmt = self._destinations(trip_id).where(
            TripLocation.location_id == location_id,
            TripLocation.status == TripLocationStatus.CONFIRMED,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def next_sequence(self, trip_id: UUID) -> int:
        stmt = select(func.coalesce(func.max(TripLocation.sequence) + 1, 0)).where(
            TripLocation.trip_id == trip_id,
            TripLocation.role == TripLocationRole.DESTINATION,
            TripLocation.status == TripLocationStatus.CONFIRMED,
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def add(self, trip_location: TripLocation) -> TripLocation:
        self.db.add(trip_location)
        await self.db.flush()
        return trip_location

    async def apply_order(self, ordered: list[TripLocation]) -> None:
        """Give `ordered` the sequences 0..n-1 without transient unique-index conflicts.

        Phase 1 parks every row at a distinct, far-away sequence; phase 2 sets final values.
        Both happen in the caller's transaction, so other requests never see phase 1.
        """
        for index, trip_location in enumerate(ordered):
            trip_location.sequence = _REORDER_OFFSET + index
        await self.db.flush()
        for index, trip_location in enumerate(ordered):
            trip_location.sequence = index
        await self.db.flush()

    async def add_pending(self, pending: PendingDestination) -> PendingDestination:
        self.db.add(pending)
        await self.db.flush()
        return pending

    async def get_pending(self, trip_id: UUID, pending_id: UUID) -> PendingDestination | None:
        # Filtering by trip_id too: a pending item from another trip is simply "not found".
        stmt = (
            select(PendingDestination)
            .where(PendingDestination.id == pending_id, PendingDestination.trip_id == trip_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
