from fastapi import APIRouter, Depends

from app.api.dependencies import get_location_service
from app.schemas.location import LocationResolveRequest, LocationResolveResponse
from app.services.location_service import LocationResolutionService

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.post("/resolve", response_model=LocationResolveResponse)
async def resolve_location(
    request: LocationResolveRequest,
    service: LocationResolutionService = Depends(get_location_service),
):
    """Find candidate places for a name. Nothing is saved or confirmed.

    Call this when the user submits a search, not on every keystroke:
    autocomplete is not allowed by the Nominatim usage policy.
    """
    return await service.resolve(request.query)
