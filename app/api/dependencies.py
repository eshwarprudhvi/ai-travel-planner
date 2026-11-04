from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.services.trip_service import TripService


def get_trip_service(db: AsyncSession = Depends(get_db)) -> TripService:
    return TripService(db)
