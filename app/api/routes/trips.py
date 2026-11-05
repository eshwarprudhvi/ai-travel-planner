from uuid import UUID
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_location_service, get_trip_service
from app.schemas.trip import (
    OriginConfirmRequest,
    OriginRejectResponse,
    OriginResponse,
    OriginUpdateConfirmResponse,
    OriginUpdateRequest,
    OriginUpdateResponse,
    TripCreate,
    TripDetailResponse,
    TripResponse,
    TripUpdate,
)
from app.services.location_service import LocationResolutionService
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
    return await trip_service.get_all_trip_details()


@router.get("/{trip_id}", response_model=TripDetailResponse)
async def get_trip(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
):
    return await trip_service.get_trip_detail(trip_id)


@router.put("/{trip_id}", response_model=TripDetailResponse)
async def update_trip(
    trip_id: UUID,
    trip: TripUpdate,
    trip_service: TripService = Depends(get_trip_service),
):
    return await trip_service.update_trip(
        trip_id,
        trip.model_dump(exclude_unset=True),
        fields_set=trip.model_fields_set,
    )


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
) -> None:
    await trip_service.delete_trip(trip_id)


@router.put("/{trip_id}/origin", response_model=OriginUpdateResponse)
async def update_origin(
    trip_id: UUID,
    request: OriginUpdateRequest,
    trip_service: TripService = Depends(get_trip_service),
    location_service: LocationResolutionService = Depends(get_location_service),
):
    """Resolve a new origin without immediately replacing the existing confirmed origin."""
    return await trip_service.request_origin_update(
        trip_id=trip_id,
        origin_text=request.origin_text,
        location_service=location_service,
    )


@router.post("/{trip_id}/origin/confirm", response_model=OriginResponse)
async def confirm_origin(
    trip_id: UUID,
    request: OriginConfirmRequest,
    trip_service: TripService = Depends(get_trip_service),
    location_service: LocationResolutionService = Depends(get_location_service),
):
    return await trip_service.confirm_origin(trip_id, request, location_service)


@router.post("/{trip_id}/origin/confirm-update", response_model=OriginUpdateConfirmResponse)
async def confirm_origin_update(
    trip_id: UUID,
    request: OriginConfirmRequest,
    trip_service: TripService = Depends(get_trip_service),
):
    """Confirm and atomically replace the trip origin from a pending origin update."""
    return await trip_service.confirm_origin_update(trip_id, request)


@router.post("/{trip_id}/origin/reject-update", response_model=OriginRejectResponse)
async def reject_origin_update(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
):
    """Reject pending origin update and preserve existing confirmed origin."""
    return await trip_service.reject_origin_update(trip_id)


@router.get("/{trip_id}/origin", response_model=OriginResponse)
async def get_origin(
    trip_id: UUID,
    trip_service: TripService = Depends(get_trip_service),
    location_service: LocationResolutionService = Depends(get_location_service),
):
    return await trip_service.get_origin(trip_id, location_service)
