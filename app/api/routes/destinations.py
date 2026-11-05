from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_destination_service, get_location_service
from app.schemas.destination import (
    DestinationAddRequest,
    DestinationConfirmRequest,
    DestinationPreferences,
    DestinationReorderRequest,
    DestinationReplaceRequest,
    DestinationResolveRequest,
    DestinationResponse,
    DestinationUpdateRequest,
    PendingDestinationResponse,
)
from app.services.destination_service import DestinationService
from app.services.location_service import LocationResolutionService

router = APIRouter(prefix="/trips/{trip_id}/destinations", tags=["Destinations"])


@router.get("", response_model=list[DestinationResponse])
async def list_destinations(
    trip_id: UUID,
    include_history: bool = Query(False, description="Also return replaced and removed destinations"),
    service: DestinationService = Depends(get_destination_service),
):
    """Active destinations in sequence order (history after them when requested)."""
    return await service.list_destinations(trip_id, include_history)


@router.post("", response_model=DestinationResponse, status_code=status.HTTP_201_CREATED)
async def add_destination(
    trip_id: UUID,
    request: DestinationAddRequest,
    service: DestinationService = Depends(get_destination_service),
):
    """Add a destination from a location that already exists on the server."""
    return await service.add_existing_location(
        trip_id,
        request.location_id,
        destination_type=request.destination_type,
        planned_duration_days=request.planned_duration_days,
        preferences=request.preferences.model_dump(),
    )


@router.post("/resolve", response_model=PendingDestinationResponse, status_code=status.HTTP_201_CREATED)
async def resolve_destination(
    trip_id: UUID,
    request: DestinationResolveRequest,
    service: DestinationService = Depends(get_destination_service),
    location_service: LocationResolutionService = Depends(get_location_service),
):
    """Look up a place by name. Creates a pending item; nothing is added until it's confirmed."""
    return await service.resolve_destination(
        trip_id,
        request.query,
        location_service,
        destination_type=request.destination_type,
        planned_duration_days=request.planned_duration_days,
        preferences=request.preferences.model_dump(),
    )


@router.post(
    "/pending/{pending_id}/confirm",
    response_model=DestinationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_destination(
    trip_id: UUID,
    pending_id: UUID,
    request: DestinationConfirmRequest,
    service: DestinationService = Depends(get_destination_service),
):
    """Confirm one stored candidate. Adds the destination, or completes a replacement."""
    return await service.confirm_pending(trip_id, pending_id, request.provider_name, request.provider_place_id)


@router.delete("/pending/{pending_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_pending_destination(
    trip_id: UUID,
    pending_id: UUID,
    service: DestinationService = Depends(get_destination_service),
) -> None:
    await service.cancel_pending(trip_id, pending_id)


@router.put("/order", response_model=list[DestinationResponse])
async def reorder_destinations(
    trip_id: UUID,
    request: DestinationReorderRequest,
    service: DestinationService = Depends(get_destination_service),
):
    """Send every active destination ID in the new order."""
    return await service.reorder(trip_id, request.destination_ids)


@router.patch("/{destination_id}", response_model=DestinationResponse)
async def update_destination(
    trip_id: UUID,
    destination_id: UUID,
    request: DestinationUpdateRequest,
    service: DestinationService = Depends(get_destination_service),
):
    """Change type (e.g. promote optional -> primary) or planned duration."""
    return await service.update_destination(trip_id, destination_id, request.model_dump(exclude_unset=True))


@router.put("/{destination_id}/preferences", response_model=DestinationResponse)
async def replace_destination_preferences(
    trip_id: UUID,
    destination_id: UUID,
    request: DestinationPreferences,
    service: DestinationService = Depends(get_destination_service),
):
    """Replace this destination's preferences (send the full set)."""
    return await service.update_destination(trip_id, destination_id, {"preferences": request.model_dump()})


@router.post(
    "/{destination_id}/replace",
    response_model=PendingDestinationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def replace_destination(
    trip_id: UUID,
    destination_id: UUID,
    request: DestinationReplaceRequest,
    service: DestinationService = Depends(get_destination_service),
    location_service: LocationResolutionService = Depends(get_location_service),
):
    """Start replacing a destination. The old one stays active until the new one is confirmed."""
    return await service.start_replacement(
        trip_id,
        destination_id,
        request.query,
        location_service,
        planned_duration_days=request.planned_duration_days,
        preferences=request.preferences.model_dump(),
    )


@router.delete("/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_destination(
    trip_id: UUID,
    destination_id: UUID,
    service: DestinationService = Depends(get_destination_service),
) -> None:
    """Soft-delete: kept as history; the shared location is never deleted."""
    await service.remove(trip_id, destination_id)
