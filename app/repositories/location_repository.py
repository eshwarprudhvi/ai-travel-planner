from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.integrations.geocoding.base import GeocodingCandidate
from app.models import Location, LocationProviderRef


class LocationRepository:
    """Canonical (shared) locations. Shared by origin and destination flows.

    Never commits: the caller owns the transaction.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, location_id) -> Location | None:
        return await self.db.get(Location, location_id)

    async def find_by_provider_ref(self, provider_name: str, provider_place_id: str) -> Location | None:
        stmt = (
            select(Location)
            .join(Location.provider_refs)
            .options(selectinload(Location.provider_refs))
            .where(
                LocationProviderRef.provider_name == provider_name,
                LocationProviderRef.provider_place_id == provider_place_id,
            )
        )
        result = await self.db.execute(stmt)
        # The unique constraint on (provider_name, provider_place_id) guarantees 0 or 1 row.
        return result.scalar_one_or_none()

    async def get_or_create_from_candidate(self, candidate: GeocodingCandidate) -> Location:
        """Reuse the Location for this provider place, or create it with its provider ref.

        Matching is by provider reference only. Equal coordinates are NOT treated as the
        same place (a hotel and a station can share a point), so nothing is merged by guess.
        """
        location = await self.find_by_provider_ref(candidate.provider_name, candidate.provider_place_id)
        if location is not None:
            return location

        if not candidate.country:
            # Never invent data: the country column is required and the provider gave none.
            raise AppException(
                "The selected place has no country information; please choose another candidate",
                status_code=422,
            )

        location = Location(
            place_name=candidate.place_name,
            city=candidate.city,
            state=candidate.state,
            country=candidate.country,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            location_type=candidate.location_type,
        )
        location.provider_refs.append(
            LocationProviderRef(
                provider_name=candidate.provider_name,
                provider_place_id=candidate.provider_place_id,
            )
        )
        self.db.add(location)
        await self.db.flush()
        return location
