from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_geocoding_provider
from app.integrations.geocoding import GeocodingCandidate
from app.main import app
from app.models import (
    Location,
    LocationProviderRef,
    LocationType,
    TripLocation,
    TripLocationRole,
)

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def make_candidate(
    place_name: str = "Hyderabad",
    place_id: str = "relation:1",
    provider_name: str = "fake",
    **overrides,
) -> GeocodingCandidate:
    data = dict(
        place_name=place_name,
        display_name=f"{place_name}, Telangana, India",
        city="Hyderabad",
        state="Telangana",
        country="India",
        latitude=Decimal("17.385044"),
        longitude=Decimal("78.486671"),
        location_type=LocationType.CITY,
        provider_name=provider_name,
        provider_place_id=place_id,
    )
    data.update(overrides)
    return GeocodingCandidate(**data)


class FakeProvider:
    name = "fake"
    attribution = "Fake provider data"

    def __init__(self, candidates: list[GeocodingCandidate] | None = None):
        self.candidates = candidates or []

    async def search(self, query: str, limit: int) -> list[GeocodingCandidate]:
        return self.candidates[:limit]


@pytest.fixture
def use_provider():
    def install(provider: FakeProvider) -> FakeProvider:
        app.dependency_overrides[get_geocoding_provider] = lambda: provider
        return provider

    yield install
    app.dependency_overrides.pop(get_geocoding_provider, None)


async def create_test_trip(client: AsyncClient, origin: str = "Hyderabad") -> str:
    payload = {
        "origin": origin,
        "destination": "Goa",
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
    }
    response = await client.post("/api/v1/trips/", json=payload)
    assert response.status_code == 200
    return response.json()["trip_id"]


# ---------- 1. Confirmation & Persistence ----------

async def test_confirm_origin_success(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    response = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={
            "provider_name": "fake",
            "provider_place_id": "rel:123",
            "confirmed": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trip_id"] == trip_id
    assert body["status"] == "confirmed"
    assert body["origin_text"] == "Hyderabad"
    loc = body["location"]
    assert loc["place_name"] == "Hyderabad"
    assert loc["country"] == "India"
    assert loc["latitude"] == "17.385044"
    assert loc["longitude"] == "78.486671"
    assert loc["location_type"] == "city"
    assert loc["provider_name"] == "fake"
    assert loc["provider_place_id"] == "rel:123"

    # Verify persistence in database
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    assert tl.sequence == 0

    saved_loc = (
        await db_session.execute(
            select(Location).where(Location.id == tl.location_id)
        )
    ).scalar_one()
    assert saved_loc.place_name == "Hyderabad"

    ref = (
        await db_session.execute(
            select(LocationProviderRef).where(
                LocationProviderRef.location_id == saved_loc.id
            )
        )
    ).scalar_one()
    assert ref.provider_name == "fake"
    assert ref.provider_place_id == "rel:123"


async def test_confirm_origin_with_candidate_id_alias(client: AsyncClient, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    response = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"candidate_id": "rel:123", "provider_name": "fake"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


# ---------- 2. Get Origin (Confirmed & Unconfirmed) ----------

async def test_get_origin_confirmed(client: AsyncClient, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    # Confirm origin
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:123"},
    )

    # Retrieve confirmed origin
    response = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["location"]["place_name"] == "Hyderabad"
    assert body["location"]["latitude"] == "17.385044"


async def test_get_origin_unconfirmed_returns_resolved_status(client: AsyncClient, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    response = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["location"] is None
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["place_name"] == "Hyderabad"


async def test_get_origin_unconfirmed_with_no_matches_returns_extracted(client: AsyncClient, use_provider):
    use_provider(FakeProvider([]))
    trip_id = await create_test_trip(client, origin="NowhereLand")

    response = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "extracted"
    assert body["location"] is None


# ---------- 3. Validation & Business Rules ----------

async def test_confirm_origin_requires_explicit_confirmation(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    response = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={
            "provider_name": "fake",
            "provider_place_id": "rel:123",
            "confirmed": False,
        },
    )

    assert response.status_code == 400
    assert "confirmation is required" in response.json()["message"].lower()

    # Verify no origin persisted
    tl = (
        await db_session.execute(
            select(TripLocation).where(TripLocation.trip_id == trip_id)
        )
    ).scalar_one_or_none()
    assert tl is None


async def test_confirm_origin_rejects_candidate_not_in_resolution_results(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    # Send a candidate ID that does not exist in resolution results
    response = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={
            "provider_name": "fake",
            "provider_place_id": "unrelated:999",
            "confirmed": True,
        },
    )

    assert response.status_code == 400
    assert "does not belong" in response.json()["message"]

    # Verify no origin persisted
    tl = (
        await db_session.execute(
            select(TripLocation).where(TripLocation.trip_id == trip_id)
        )
    ).scalar_one_or_none()
    assert tl is None


async def test_confirm_origin_nonexistent_trip_returns_404(client: AsyncClient, use_provider):
    use_provider(FakeProvider([]))
    response = await client.post(
        f"/api/v1/trips/{MISSING_ID}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:1"},
    )
    assert response.status_code == 404
    assert response.json()["message"] == "Trip not found"


async def test_get_origin_nonexistent_trip_returns_404(client: AsyncClient, use_provider):
    use_provider(FakeProvider([]))
    response = await client.get(f"/api/v1/trips/{MISSING_ID}/origin")
    assert response.status_code == 404
    assert response.json()["message"] == "Trip not found"


# ---------- 4. Idempotency & Reuse ----------

async def test_confirm_origin_repeated_requests_are_idempotent(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))
    trip_id = await create_test_trip(client, origin="Hyderabad")

    # Call 1
    res1 = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:123"},
    )
    assert res1.status_code == 200

    # Call 2 (repeated)
    res2 = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:123"},
    )
    assert res2.status_code == 200
    assert res2.json()["location"]["id"] == res1.json()["location"]["id"]

    # Database has exactly 1 TripLocation
    tls = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalars().all()
    assert len(tls) == 1


async def test_confirm_origin_reuses_canonical_location_across_trips(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand = make_candidate("Hyderabad", "rel:123")
    use_provider(FakeProvider([cand]))

    trip_a = await create_test_trip(client, origin="Hyderabad")
    trip_b = await create_test_trip(client, origin="Hyderabad")

    res_a = await client.post(
        f"/api/v1/trips/{trip_a}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:123"},
    )
    res_b = await client.post(
        f"/api/v1/trips/{trip_b}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:123"},
    )

    assert res_a.status_code == 200
    assert res_b.status_code == 200
    assert res_a.json()["location"]["id"] == res_b.json()["location"]["id"]

    # Check only 1 location is in database
    locs = (await db_session.execute(select(Location))).scalars().all()
    assert len(locs) == 1


async def test_confirm_origin_updates_without_duplicate_origin_association(client: AsyncClient, db_session: AsyncSession, use_provider):
    cand1 = make_candidate("Hyderabad City", "rel:1")
    cand2 = make_candidate("Hyderabad Airport", "rel:2", location_type=LocationType.AIRPORT, latitude=Decimal("17.2403"))
    use_provider(FakeProvider([cand1, cand2]))

    trip_id = await create_test_trip(client, origin="Hyderabad")

    # Confirm candidate 1
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:1"},
    )

    # Change mind and confirm candidate 2
    res2 = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "rel:2"},
    )
    assert res2.status_code == 200
    assert res2.json()["location"]["provider_place_id"] == "rel:2"

    # Only 1 TripLocation exists for this trip
    tls = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalars().all()
    assert len(tls) == 1
    assert tls[0].location.place_name == "Hyderabad Airport"
