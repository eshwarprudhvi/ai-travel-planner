from fastapi import APIRouter, Depends

from app.api.dependencies import get_trip_service
from app.schemas.trip import TripCreate, TripResponse
from app.services.trip_service import TripService

router = APIRouter(prefix="/trips", tags=["Trips"])


@router.post("/", response_model=TripResponse)
async def create_trip(
    trip: TripCreate,
    trip_service: TripService = Depends(get_trip_service),
):
    return {
        "trip_id": "trip_001",
        **trip_service.create_trip(trip.model_dump()),
    }
