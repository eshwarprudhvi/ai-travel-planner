from decimal import Decimal
from uuid import UUID
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
    Trip,
    TripLocation,
    TripLocationRole,
)
from app.repositories.pending_origin_store import default_pending_origin_store

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def make_candidate(
    place_name: str,
    place_id: str,
    provider_name: str = "fake",
    latitude: str = "12.971599",
    longitude: str = "77.594566",
    location_type: LocationType = LocationType.CITY,
) -> GeocodingCandidate:
    return GeocodingCandidate(
        place_name=place_name,
        display_name=f"{place_name}, Karnataka, India",
        city=place_name,
        state="Karnataka",
        country="India",
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        location_type=location_type,
        provider_name=provider_name,
        provider_place_id=place_id,
    )


class MultiQueryFakeProvider:
    name = "fake"
    attribution = "Fake provider data"

    def __init__(self, mapping: dict[str, list[GeocodingCandidate]] | None = None):
        self.mapping = mapping or {}

    async def search(self, query: str, limit: int) -> list[GeocodingCandidate]:
        return self.mapping.get(query.casefold(), [])[:limit]


@pytest.fixture(autouse=True)
def clean_pending_store():
    default_pending_origin_store.clear()
    yield
    default_pending_origin_store.clear()


@pytest.fixture
def use_multi_provider():
    def install(provider: MultiQueryFakeProvider) -> MultiQueryFakeProvider:
        app.dependency_overrides[get_geocoding_provider] = lambda: provider
        return provider

    yield install
    app.dependency_overrides.pop(get_geocoding_provider, None)


async def create_trip(client: AsyncClient, origin: str = "Hyderabad") -> str:
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


# ---------- 1. Successful Resolution & Keeping Confirmed Origin ----------

async def test_origin_update_resolves_and_keeps_confirmed_origin(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Create trip and confirm origin as Hyderabad
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    assert confirm_res.status_code == 200

    # 2. User initiates origin update to Bangalore
    update_res = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )

    assert update_res.status_code == 200
    body = update_res.json()
    assert body["trip_id"] == trip_id
    assert body["pending_origin_text"] == "Bangalore"
    assert body["status"] == "resolved"
    assert body["requires_confirmation"] is True
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["place_name"] == "Bangalore"
    assert body["candidates"][0]["provider_place_id"] == "blr:1"

    # Confirmed origin reported in response remains Hyderabad
    assert body["confirmed_origin"] is not None
    assert body["confirmed_origin"]["place_name"] == "Hyderabad"
    assert body["confirmed_origin"]["provider_place_id"] == "hyd:1"

    # 3. Verify in PostgreSQL that the confirmed TripLocation record STILL points to Hyderabad
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()

    confirmed_location = (
        await db_session.execute(
            select(Location).where(Location.id == tl.location_id)
        )
    ).scalar_one()

    assert confirmed_location.place_name == "Hyderabad"

    # 4. Verify pending storage has Bangalore recorded
    pending = default_pending_origin_store.get(trip_id)
    assert pending is not None
    assert pending.origin_text == "Bangalore"
    assert len(pending.candidates) == 1
    assert pending.candidates[0].place_name == "Bangalore"


# ---------- 2. No Matches Found ----------

async def test_origin_update_with_no_matches(client: AsyncClient, use_multi_provider):
    use_multi_provider(MultiQueryFakeProvider({}))
    trip_id = await create_trip(client, origin="Hyderabad")

    response = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "NonExistentPlace"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trip_id"] == trip_id
    assert body["pending_origin_text"] == "NonExistentPlace"
    assert body["status"] == "no_match"
    assert body["candidates"] == []

    # Pending store tracks the attempt
    pending = default_pending_origin_store.get(trip_id)
    assert pending is not None
    assert pending.origin_text == "NonExistentPlace"
    assert pending.candidates == []


# ---------- 3. Nonexistent Trip Returns 404 ----------

async def test_origin_update_nonexistent_trip_returns_404(client: AsyncClient, use_multi_provider):
    use_multi_provider(MultiQueryFakeProvider({}))
    response = await client.put(
        f"/api/v1/trips/{MISSING_ID}/origin",
        json={"origin_text": "Bangalore"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Trip not found"


# ---------- 4. Validation Rules ----------

@pytest.mark.parametrize("invalid_text", ["", " ", "   a   "])
async def test_origin_update_rejects_invalid_origin_text(
    client: AsyncClient, use_multi_provider, invalid_text
):
    use_multi_provider(MultiQueryFakeProvider({}))
    trip_id = await create_trip(client, origin="Hyderabad")

    response = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": invalid_text},
    )

    assert response.status_code == 422


# ---------- 5. GET /origin reflects pending state while keeping confirmed intact ----------

async def test_get_origin_shows_pending_origin_alongside_confirmed(
    client: AsyncClient, use_multi_provider
):
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # Initiate update
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )

    # GET /origin should report confirmed origin as Hyderabad, and indicate pending_origin_text as Bangalore
    get_res = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["origin"] == "Hyderabad"
    assert body["pending_origin"] == "Bangalore"
    assert body["location"]["place_name"] == "Hyderabad"
    assert body["pending_origin_text"] == "Bangalore"


# ---------- 6. Task 4.2 Scenario 1 — Confirmed Origin Replacement ----------

async def test_confirm_origin_update_scenario_1_replaces_origin_safely(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Create a trip with Hyderabad as its confirmed origin
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_hyd = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1", "confirmed": True},
    )
    assert confirm_hyd.status_code == 200

    # 2. Requests an origin update to Bangalore
    update_res = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert update_res.status_code == 200

    # Verify state before confirmation
    get_before = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert get_before.status_code == 200
    assert get_before.json()["origin"] == "Hyderabad"
    assert get_before.json()["pending_origin"] == "Bangalore"
    assert get_before.json()["status"] == "awaiting_confirmation"

    # 3. Selects a valid Bangalore candidate & 4. Confirms the update
    confirm_update_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={
            "provider_name": "fake",
            "provider_place_id": "blr:1",
            "confirmed": True,
        },
    )
    assert confirm_update_res.status_code == 200
    confirm_body = confirm_update_res.json()
    assert confirm_body["origin"] == "Bangalore"
    assert confirm_body["pending_origin"] is None
    assert confirm_body["status"] == "confirmed"
    assert confirm_body["location"]["place_name"] == "Bangalore"
    assert confirm_body["location"]["provider_place_id"] == "blr:1"

    # 5. Verifies the trip now references Bangalore
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()

    new_location = (
        await db_session.execute(
            select(Location).where(Location.id == tl.location_id)
        )
    ).scalar_one()
    assert new_location.place_name == "Bangalore"

    trip_record = (
        await db_session.execute(
            select(Trip).where(Trip.id == trip_id)
        )
    ).scalar_one()
    assert trip_record.origin == "Bangalore"

    # 6. Verifies Hyderabad's canonical location record still exists
    hyd_loc = (
        await db_session.execute(
            select(Location).where(Location.place_name == "Hyderabad")
        )
    ).scalar_one_or_none()
    assert hyd_loc is not None

    all_locs = (await db_session.execute(select(Location))).scalars().all()
    assert len(all_locs) == 2
    assert {loc.place_name for loc in all_locs} == {"Hyderabad", "Bangalore"}

    # 7. Verifies the pending update is cleared
    pending = default_pending_origin_store.get(trip_id)
    assert pending is None

    # Verify GET /origin after confirmation
    get_after = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert get_after.status_code == 200
    assert get_after.json()["origin"] == "Bangalore"
    assert get_after.json()["pending_origin"] is None
    assert get_after.json()["status"] == "confirmed"
    assert get_after.json()["location"]["place_name"] == "Bangalore"



# ---------- 7. Task 4.2 Scenario 2 — User Rejects the New Origin ----------

async def test_reject_origin_update_preserves_confirmed_origin_and_clears_pending(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Create trip and confirm Hyderabad
    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1", "confirmed": True},
    )

    # 2. Request origin update to Bangalore
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert default_pending_origin_store.get(trip_id) is not None

    # 3. User rejects the Bangalore update
    reject_res = await client.post(f"/api/v1/trips/{trip_id}/origin/reject-update")
    assert reject_res.status_code == 200
    reject_body = reject_res.json()
    assert reject_body["trip_id"] == trip_id
    assert reject_body["status"] == "confirmed"
    assert reject_body["origin"] == "Hyderabad"
    assert reject_body["pending_origin"] is None
    assert "rejected" in reject_body["message"].lower()

    # 4. Pending update is cleared
    assert default_pending_origin_store.get(trip_id) is None

    # 5. Hyderabad's database association remains unchanged
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()

    confirmed_location = (
        await db_session.execute(
            select(Location).where(Location.id == tl.location_id)
        )
    ).scalar_one()
    assert confirmed_location.place_name == "Hyderabad"

    trip_record = (
        await db_session.execute(
            select(Trip).where(Trip.id == trip_id)
        )
    ).scalar_one()
    assert trip_record.origin == "Hyderabad"

    # 6. No new Bangalore location association is created
    trip_locations = (
        await db_session.execute(
            select(TripLocation).where(TripLocation.trip_id == trip_id)
        )
    ).scalars().all()
    assert len(trip_locations) == 1

    # 7. GET /origin reflects Hyderabad confirmed and no pending origin
    get_res = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert get_res.status_code == 200
    assert get_res.json()["origin"] == "Hyderabad"
    assert get_res.json()["pending_origin"] is None
    assert get_res.json()["status"] == "confirmed"


async def test_reject_origin_update_when_no_pending_update_exists(client: AsyncClient, use_multi_provider):
    use_multi_provider(MultiQueryFakeProvider({}))
    trip_id = await create_trip(client, origin="Hyderabad")

    # Attempt to reject when no pending update was ever requested
    response = await client.post(f"/api/v1/trips/{trip_id}/origin/reject-update")
    assert response.status_code == 400
    assert "no pending origin update" in response.json()["message"].lower()


async def test_reject_origin_update_nonexistent_trip_returns_404(client: AsyncClient, use_multi_provider):
    use_multi_provider(MultiQueryFakeProvider({}))
    response = await client.post(f"/api/v1/trips/{MISSING_ID}/origin/reject-update")
    assert response.status_code == 404
    assert response.json()["message"] == "Trip not found"


# ---------- 8. Task 4.2 Scenario 3 — Duplicate Confirmation (Idempotency) ----------

async def test_confirm_origin_update_scenario_3_idempotency_duplicate_calls(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    Scenario 3:
    1. Hyderabad is the confirmed origin.
    2. User requests Bangalore.
    3. User confirms Bangalore.
    4. The confirmation request is accidentally submitted again.

    Both requests should produce the same final state:
    - Confirmed origin: Bangalore.
    - One origin association for the trip.
    - No duplicate canonical location.
    - No duplicate provider reference.
    - Repeated call does not fail merely because pending update was already cleared.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Establish trip with Hyderabad confirmed
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_init = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    assert confirm_init.status_code == 200

    # 2. User requests Bangalore
    update_res = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert update_res.status_code == 200

    confirm_payload = {
        "provider_name": "fake",
        "provider_place_id": "blr:1",
        "confirmed": True,
    }

    # 3. User confirms Bangalore (First confirmation)
    first_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert first_res.status_code == 200
    first_body = first_res.json()
    assert first_body["status"] == "confirmed"
    assert first_body["origin"] == "Bangalore"
    first_location_id = first_body["location"]["id"]

    # Pending update should now be cleared
    assert default_pending_origin_store.get(trip_id) is None

    # 4. Confirmation request is submitted again (Duplicate call)
    second_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert second_res.status_code == 200
    second_body = second_res.json()
    assert second_body["status"] == "confirmed"
    assert second_body["origin"] == "Bangalore"
    assert second_body["location"]["id"] == first_location_id
    assert second_body["location"]["place_name"] == "Bangalore"
    assert second_body["location"]["provider_name"] == "fake"
    assert second_body["location"]["provider_place_id"] == "blr:1"

    # 5. Database safeguards verification:
    # Exactly 1 origin association exists for this trip
    trip_locations = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalars().all()
    assert len(trip_locations) == 1
    assert trip_locations[0].location_id == UUID(first_location_id)

    # Exactly 1 canonical Location for Bangalore
    blr_locations = (
        await db_session.execute(
            select(Location).where(Location.place_name == "Bangalore")
        )
    ).scalars().all()
    assert len(blr_locations) == 1

    # Exactly 1 LocationProviderRef for blr:1
    refs = (
        await db_session.execute(
            select(LocationProviderRef).where(
                LocationProviderRef.provider_name == "fake",
                LocationProviderRef.provider_place_id == "blr:1",
            )
        )
    ).scalars().all()
    assert len(refs) == 1

    # Total TripLocation records for this trip across any role is 1
    all_tl = (
        await db_session.execute(
            select(TripLocation).where(TripLocation.trip_id == trip_id)
        )
    ).scalars().all()
    assert len(all_tl) == 1


# ---------- 9. Task 4.2 Scenario 4 — Invalid Candidate Submission ----------

@pytest.mark.parametrize(
    "bad_provider, bad_place_id",
    [
        ("fake", "unknown_place_999"),       # Unknown candidate ID / place ID
        ("unregistered_provider", "blr:1"),   # Incorrect provider name
        ("fake", "blr:999"),                 # Incorrect provider place ID
        ("fake", "chennai:1"),               # Candidate from another resolution / not part of Bangalore update
    ],
)
async def test_confirm_origin_update_scenario_4_rejects_invalid_candidates(
    client: AsyncClient,
    db_session: AsyncSession,
    use_multi_provider,
    bad_provider: str,
    bad_place_id: str,
):
    """
    Scenario 4:
    1. Confirmed origin: Hyderabad.
    2. Pending origin: Bangalore.
    3. User submits a candidate ID / provider that is not part of the Bangalore resolution.
    4. System rejects with 400 Bad Request.
    5. Existing origin remains Hyderabad.
    6. Pending update for Bangalore remains preserved.
    7. No new Location or TripLocation records created.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")
    chn_cand = make_candidate("Chennai", "chennai:1", latitude="13.082680", longitude="80.270718")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
            "chennai": [chn_cand],
        })
    )

    # 1. Establish trip with Hyderabad confirmed
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_init = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    assert confirm_init.status_code == 200

    # 2. User requests Bangalore update
    update_res = await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert update_res.status_code == 200

    # Capture location count before invalid attempt
    locs_before = (await db_session.execute(select(Location))).scalars().all()
    loc_count_before = len(locs_before)

    # 3. User submits an invalid candidate
    res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={
            "provider_name": bad_provider,
            "provider_place_id": bad_place_id,
            "confirmed": True,
        },
    )

    # Error handling verification
    assert res.status_code == 400
    res_body = res.json()
    assert res_body["success"] is False
    assert res_body["message"] == "Selected location is not part of the pending origin update."

    # 4. State preservation verification:
    # A) Existing confirmed origin unchanged in database
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    confirmed_location = (
        await db_session.execute(
            select(Location).where(Location.id == tl.location_id)
        )
    ).scalar_one()
    assert confirmed_location.place_name == "Hyderabad"

    # Trip model record origin string unchanged
    trip_record = (
        await db_session.execute(select(Trip).where(Trip.id == trip_id))
    ).scalar_one()
    assert trip_record.origin == "Hyderabad"

    # B) Pending update preserved in pending store
    pending = default_pending_origin_store.get(trip_id)
    assert pending is not None
    assert pending.origin_text == "Bangalore"
    assert len(pending.candidates) == 1
    assert pending.candidates[0].provider_place_id == "blr:1"

    # C) No new canonical location records created
    locs_after = (await db_session.execute(select(Location))).scalars().all()
    assert len(locs_after) == loc_count_before

    # D) No new TripLocation associations created (still exactly 1)
    trip_locations = (
        await db_session.execute(
            select(TripLocation).where(TripLocation.trip_id == trip_id)
        )
    ).scalars().all()
    assert len(trip_locations) == 1

    # E) GET /origin reflects confirmed origin Hyderabad and pending Bangalore
    get_res = await client.get(f"/api/v1/trips/{trip_id}/origin")
    assert get_res.status_code == 200
    get_body = get_res.json()
    assert get_body["status"] == "awaiting_confirmation"
    assert get_body["origin"] == "Hyderabad"
    assert get_body["pending_origin"] == "Bangalore"
    assert get_body["location"]["place_name"] == "Hyderabad"


async def test_confirm_origin_update_scenario_4_candidate_from_another_trip_pending_update(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    Verify that a candidate generated for Trip B cannot be confirmed for Trip A.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")
    del_cand = make_candidate("Delhi", "del:1", latitude="28.613939", longitude="77.209021")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
            "delhi": [del_cand],
        })
    )

    # Trip A: confirmed Hyderabad, pending Bangalore
    trip_a = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_a}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    await client.put(
        f"/api/v1/trips/{trip_a}/origin",
        json={"origin_text": "Bangalore"},
    )

    # Trip B: confirmed Hyderabad, pending Delhi
    trip_b = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_b}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    await client.put(
        f"/api/v1/trips/{trip_b}/origin",
        json={"origin_text": "Delhi"},
    )

    # Attempt to confirm Trip B's candidate (del:1) on Trip A
    res = await client.post(
        f"/api/v1/trips/{trip_a}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "del:1", "confirmed": True},
    )
    assert res.status_code == 400
    assert res.json()["message"] == "Selected location is not part of the pending origin update."

    # Trip A pending remains Bangalore
    pending_a = default_pending_origin_store.get(trip_a)
    assert pending_a is not None
    assert pending_a.origin_text == "Bangalore"


# ---------- 10. Task 4.2 Scenario 5 — Pending Origin Does Not Exist ----------

async def test_confirm_origin_update_without_pending_update_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    1. Confirmed origin: Hyderabad.
    2. No pending origin update exists.
    3. User sends a request to confirm Bangalore.
    4. System rejects with 409 Conflict.
    5. Hyderabad remains unchanged; no records created.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # Confirmed origin: Hyderabad
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_init = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    assert confirm_init.status_code == 200

    # Ensure no pending update exists
    assert default_pending_origin_store.get(trip_id) is None

    # Capture location count before
    loc_count_before = len((await db_session.execute(select(Location))).scalars().all())

    # User attempts to confirm Bangalore without requesting an update first
    res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert res.status_code == 409
    res_body = res.json()
    assert res_body["success"] is False
    assert res_body["message"] == "No pending origin update exists for this trip."

    # State preservation: Confirmed origin is still Hyderabad
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc = (await db_session.execute(select(Location).where(Location.id == tl.location_id))).scalar_one()
    assert loc.place_name == "Hyderabad"

    # No new locations created
    loc_count_after = len((await db_session.execute(select(Location))).scalars().all())
    assert loc_count_after == loc_count_before


async def test_confirm_origin_update_after_rejecting_pending_update_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    1. Confirmed origin: Hyderabad.
    2. User requests Bangalore update.
    3. User rejects the pending Bangalore update (which clears the pending store).
    4. User subsequently tries to confirm Bangalore.
    5. System rejects with 409 Conflict.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # Request update to Bangalore
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert default_pending_origin_store.get(trip_id) is not None

    # Reject pending update
    reject_res = await client.post(f"/api/v1/trips/{trip_id}/origin/reject-update")
    assert reject_res.status_code == 200
    assert default_pending_origin_store.get(trip_id) is None

    # Now attempt to confirm Bangalore
    confirm_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert confirm_res.status_code == 409
    assert confirm_res.json()["message"] == "No pending origin update exists for this trip."


async def test_confirm_origin_update_after_successful_replacement_different_candidate_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    Confirming the same candidate repeatedly is an idempotent replay (200 OK from Scenario 3).
    However, attempting to confirm a DIFFERENT candidate once the pending update has been cleared
    must return 409 Conflict.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")
    del_cand = make_candidate("Delhi", "del:1", latitude="28.613939", longitude="77.209021")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
            "delhi": [del_cand],
        })
    )

    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # Request update to Bangalore
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )

    # Confirm Bangalore successfully
    first_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert first_res.status_code == 200

    # Replaying Bangalore is idempotent (200 OK)
    replay_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert replay_res.status_code == 200

    # Submitting a different candidate (e.g. Delhi) when pending has been cleared must return 409
    diff_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "del:1", "confirmed": True},
    )
    assert diff_res.status_code == 409
    assert diff_res.json()["message"] == "No pending origin update exists for this trip."


async def test_confirm_origin_update_without_existing_origin_rejects_if_no_pending_update(
    client: AsyncClient, use_multi_provider
):
    """
    If a trip has no confirmed origin at all and no pending origin update exists,
    calling confirm-update must return 409 Conflict.
    """
    use_multi_provider(MultiQueryFakeProvider({}))
    trip_id = await create_trip(client, origin="Hyderabad")
    # Do not confirm Hyderabad, and do not put a pending update

    res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "hyd:1", "confirmed": True},
    )
    assert res.status_code == 409
    assert res.json()["message"] == "No pending origin update exists for this trip."


# ---------- 11. Task 4.2 Scenario 6 — Database Failure During Origin Replacement ----------

async def test_confirm_origin_update_db_failure_preserves_pending_and_rolls_back(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider, monkeypatch
):
    """
    Scenario 6:
    1. Confirmed origin: Hyderabad.
    2. Pending origin: Bangalore.
    3. User confirms Bangalore.
    4. Database fails during replace_origin (e.g. during commit or operation).
    5. Operation rolls back.
    6. Hyderabad remains confirmed origin.
    7. Bangalore remains pending in the store so user can retry.
    8. User retries confirmation after recovery and succeeds.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Establish trip with Hyderabad confirmed
    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # 2. User requests Bangalore update
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert default_pending_origin_store.get(trip_id) is not None

    confirm_payload = {
        "provider_name": "fake",
        "provider_place_id": "blr:1",
        "confirmed": True,
    }

    # 3. Simulate database commit failure during replace_origin
    from app.repositories.trip_repository import TripRepository
    original_replace_origin = TripRepository.replace_origin

    async def failing_replace_origin(self, trip, candidate):
        # Force a database failure before completion
        raise Exception("Simulated database failure during replace_origin")

    monkeypatch.setattr(TripRepository, "replace_origin", failing_replace_origin)

    # 4. User attempts confirmation -> should fail with 500 without leaking db internals
    fail_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert fail_res.status_code == 500
    assert fail_res.json()["success"] is False
    assert fail_res.json()["message"] == "Failed to update trip origin due to a server error."

    # 5. Pending update for Bangalore MUST remain in store so user can retry
    pending = default_pending_origin_store.get(trip_id)
    assert pending is not None
    assert pending.origin_text == "Bangalore"
    assert pending.candidates[0].provider_place_id == "blr:1"

    # 6. Database state: Hyderabad remains the confirmed origin
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc = (await db_session.execute(select(Location).where(Location.id == tl.location_id))).scalar_one()
    assert loc.place_name == "Hyderabad"

    trip_record = (await db_session.execute(select(Trip).where(Trip.id == trip_id))).scalar_one()
    assert trip_record.origin == "Hyderabad"

    # 7. Recovery: restore normal database operation and retry confirmation
    monkeypatch.undo()

    retry_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "confirmed"
    assert retry_res.json()["origin"] == "Bangalore"

    # 8. Pending store cleared after successful recovery
    assert default_pending_origin_store.get(trip_id) is None

    # 9. Database confirmed origin is now Bangalore
    db_session.expire_all()
    tl_updated = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc_updated = (await db_session.execute(select(Location).where(Location.id == tl_updated.location_id))).scalar_one()
    assert loc_updated.place_name == "Bangalore"




# ---------- 10. Task 4.2 Scenario 5 — Pending Origin Does Not Exist ----------

async def test_confirm_origin_update_without_pending_update_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    1. Confirmed origin: Hyderabad.
    2. No pending origin update exists.
    3. User sends a request to confirm Bangalore.
    4. System rejects with 409 Conflict.
    5. Hyderabad remains unchanged; no records created.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # Confirmed origin: Hyderabad
    trip_id = await create_trip(client, origin="Hyderabad")
    confirm_init = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )
    assert confirm_init.status_code == 200

    # Ensure no pending update exists
    assert default_pending_origin_store.get(trip_id) is None

    # Capture location count before
    loc_count_before = len((await db_session.execute(select(Location))).scalars().all())

    # User attempts to confirm Bangalore without requesting an update first
    res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert res.status_code == 409
    res_body = res.json()
    assert res_body["success"] is False
    assert res_body["message"] == "No pending origin update exists for this trip."

    # State preservation: Confirmed origin is still Hyderabad
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc = (await db_session.execute(select(Location).where(Location.id == tl.location_id))).scalar_one()
    assert loc.place_name == "Hyderabad"

    # No new locations created
    loc_count_after = len((await db_session.execute(select(Location))).scalars().all())
    assert loc_count_after == loc_count_before


async def test_confirm_origin_update_after_rejecting_pending_update_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    1. Confirmed origin: Hyderabad.
    2. User requests Bangalore update.
    3. User rejects the pending Bangalore update (which clears the pending store).
    4. User subsequently tries to confirm Bangalore.
    5. System rejects with 409 Conflict.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # Request update to Bangalore
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert default_pending_origin_store.get(trip_id) is not None

    # Reject pending update
    reject_res = await client.post(f"/api/v1/trips/{trip_id}/origin/reject-update")
    assert reject_res.status_code == 200
    assert default_pending_origin_store.get(trip_id) is None

    # Now attempt to confirm Bangalore
    confirm_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert confirm_res.status_code == 409
    assert confirm_res.json()["message"] == "No pending origin update exists for this trip."


async def test_confirm_origin_update_after_successful_replacement_different_candidate_returns_409(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider
):
    """
    Confirming the same candidate repeatedly is an idempotent replay (200 OK from Scenario 3).
    However, attempting to confirm a DIFFERENT candidate once the pending update has been cleared
    must return 409 Conflict.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")
    del_cand = make_candidate("Delhi", "del:1", latitude="28.613939", longitude="77.209021")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
            "delhi": [del_cand],
        })
    )

    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # Request update to Bangalore
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )

    # Confirm Bangalore successfully
    first_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert first_res.status_code == 200

    # Replaying Bangalore is idempotent (200 OK)
    replay_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "blr:1", "confirmed": True},
    )
    assert replay_res.status_code == 200

    # Submitting a different candidate (e.g. Delhi) when pending has been cleared must return 409
    diff_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "del:1", "confirmed": True},
    )
    assert diff_res.status_code == 409
    assert diff_res.json()["message"] == "No pending origin update exists for this trip."


async def test_confirm_origin_update_without_existing_origin_rejects_if_no_pending_update(
    client: AsyncClient, use_multi_provider
):
    """
    If a trip has no confirmed origin at all and no pending origin update exists,
    calling confirm-update must return 409 Conflict.
    """
    use_multi_provider(MultiQueryFakeProvider({}))
    trip_id = await create_trip(client, origin="Hyderabad")
    # Do not confirm Hyderabad, and do not put a pending update

    res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json={"provider_name": "fake", "provider_place_id": "hyd:1", "confirmed": True},
    )
    assert res.status_code == 409
    assert res.json()["message"] == "No pending origin update exists for this trip."


# ---------- 11. Task 4.2 Scenario 6 — Database Failure During Origin Replacement ----------

async def test_confirm_origin_update_db_failure_preserves_pending_and_rolls_back(
    client: AsyncClient, db_session: AsyncSession, use_multi_provider, monkeypatch
):
    """
    Scenario 6:
    1. Confirmed origin: Hyderabad.
    2. Pending origin: Bangalore.
    3. User confirms Bangalore.
    4. Database fails during replace_origin (e.g. during commit or operation).
    5. Operation rolls back.
    6. Hyderabad remains confirmed origin.
    7. Bangalore remains pending in the store so user can retry.
    8. User retries confirmation after recovery and succeeds.
    """
    hyd_cand = make_candidate("Hyderabad", "hyd:1", latitude="17.385044", longitude="78.486671")
    blr_cand = make_candidate("Bangalore", "blr:1", latitude="12.971599", longitude="77.594566")

    use_multi_provider(
        MultiQueryFakeProvider({
            "hyderabad": [hyd_cand],
            "bangalore": [blr_cand],
        })
    )

    # 1. Establish trip with Hyderabad confirmed
    trip_id = await create_trip(client, origin="Hyderabad")
    await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm",
        json={"provider_name": "fake", "provider_place_id": "hyd:1"},
    )

    # 2. User requests Bangalore update
    await client.put(
        f"/api/v1/trips/{trip_id}/origin",
        json={"origin_text": "Bangalore"},
    )
    assert default_pending_origin_store.get(trip_id) is not None

    confirm_payload = {
        "provider_name": "fake",
        "provider_place_id": "blr:1",
        "confirmed": True,
    }

    # 3. Simulate database commit failure during replace_origin
    from app.repositories.trip_repository import TripRepository
    original_replace_origin = TripRepository.replace_origin

    async def failing_replace_origin(self, trip, candidate):
        # Force a database failure before completion
        raise Exception("Simulated database failure during replace_origin")

    monkeypatch.setattr(TripRepository, "replace_origin", failing_replace_origin)

    # 4. User attempts confirmation -> should fail with 500 without leaking db internals
    fail_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert fail_res.status_code == 500
    assert fail_res.json()["success"] is False
    assert fail_res.json()["message"] == "Failed to update trip origin due to a server error."

    # 5. Pending update for Bangalore MUST remain in store so user can retry
    pending = default_pending_origin_store.get(trip_id)
    assert pending is not None
    assert pending.origin_text == "Bangalore"
    assert pending.candidates[0].provider_place_id == "blr:1"

    # 6. Database state: Hyderabad remains the confirmed origin
    tl = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc = (await db_session.execute(select(Location).where(Location.id == tl.location_id))).scalar_one()
    assert loc.place_name == "Hyderabad"

    trip_record = (await db_session.execute(select(Trip).where(Trip.id == trip_id))).scalar_one()
    assert trip_record.origin == "Hyderabad"

    # 7. Recovery: restore normal database operation and retry confirmation
    monkeypatch.undo()

    retry_res = await client.post(
        f"/api/v1/trips/{trip_id}/origin/confirm-update",
        json=confirm_payload,
    )
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "confirmed"
    assert retry_res.json()["origin"] == "Bangalore"

    # 8. Pending store cleared after successful recovery
    assert default_pending_origin_store.get(trip_id) is None

    # 9. Database confirmed origin is now Bangalore
    db_session.expire_all()
    tl_updated = (
        await db_session.execute(
            select(TripLocation).where(
                TripLocation.trip_id == trip_id,
                TripLocation.role == TripLocationRole.ORIGIN,
            )
        )
    ).scalar_one()
    loc_updated = (await db_session.execute(select(Location).where(Location.id == tl_updated.location_id))).scalar_one()
    assert loc_updated.place_name == "Bangalore"



