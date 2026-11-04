from uuid import UUID
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_trip_service
from app.schemas.trip import TripCreate, TripDetailResponse, TripResponse, TripUpdate
from app.services.trip_service import TripService

router = APIRouter(prefix="/trips", tags=["Trips"])


@router.post("/", response_model=TripResponse)
async def create_trip(
    trip: TripCreate,
    trip_service: TripService = Depends(get_trip_service),
):
    created_trip = await trip_service.create_trip(trip.model_dump())
    return {
        "trip_id": str(created_trip.id),
        "message": "Trip created successfully",
        "trip": trip.model_dump(),
    }


@router.get("/", response_model=list[TripDetailResponse])
async def list_trips(
    trip_service: TripService = Depends(get_trip_service),
):
    return await trip_service.get_all_trips()


@router.get("/{trip_id}", response_model=TripDetailResponse)
async def get_trip(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
):
    return await trip_service.get_trip(trip_id)


@router.put("/{trip_id}", response_model=TripDetailResponse)
async def update_trip(
    trip_id: UUID,
    trip: TripUpdate,
    trip_service: TripService = Depends(get_trip_service),
):
    return await trip_service.update_trip(trip_id, trip.model_dump())


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
) -> None:
    await trip_service.delete_trip(trip_id)
