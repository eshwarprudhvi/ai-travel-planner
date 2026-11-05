from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_geocoding_provider
from app.integrations.geocoding import GeocodingCandidate
from app.main import app
from app.models import (
    Location,
    LocationProviderRef,
    LocationType,
    PendingDestination,
    TripLocation,
    TripLocationRole,
)
from app.repositories.destination_repository import DestinationRepository

MISSING_ID = "00000000-0000-0000-0000-000000000000"

PLACES = {
    "Goa": ("relation:1", "15.299326", "74.123996"),
    "Gokarna": ("node:2", "14.550000", "74.316700"),
    "Murudeshwar": ("node:3", "14.094300", "74.484700"),
    "Kerala": ("relation:4", "10.352874", "76.512039"),
}


def candidate(name: str) -> GeocodingCandidate:
    place_id, lat, lon = PLACES[name]
    return GeocodingCandidate(
        place_name=name,
        display_name=f"{name}, India",
        city=None,
        state=name,
        country="India",
        latitude=Decimal(lat),
        longitude=Decimal(lon),
        location_type=LocationType.CITY,
        provider_name="fake",
        provider_place_id=place_id,
    )


class FakeProvider:
    """Returns the matching place for a query; counts calls so tests can check them."""

    name = "fake"
    attribution = "Fake provider data"

    def __init__(self):
        self.calls = 0

    async def search(self, query: str, limit: int) -> list[GeocodingCandidate]:
        self.calls += 1
        return [candidate(query)] if query in PLACES else []


@pytest.fixture
def provider():
    fake = FakeProvider()
    app.dependency_overrides[get_geocoding_provider] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_geocoding_provider, None)


async def create_trip(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/trips/",
        json={
            "origin": "Hyderabad",
            "destination": "Goa",
            "start_date": "2027-01-10",
            "end_date": "2027-01-20",
            "budget": "50000.00",
            "travelers": 2,
        },
    )
    assert response.status_code == 200
    return response.json()["trip_id"]


def url(trip_id: str, suffix: str = "") -> str:
    return f"/api/v1/trips/{trip_id}/destinations{suffix}"


async def resolve(client: AsyncClient, trip_id: str, query: str, **extra) -> dict:
    response = await client.post(url(trip_id, "/resolve"), json={"query": query, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def confirm(client: AsyncClient, trip_id: str, pending: dict, index: int = 0):
    chosen = pending["candidates"][index]
    return await client.post(
        url(trip_id, f"/pending/{pending['id']}/confirm"),
        json={
            "provider_name": chosen["provider_name"],
            "provider_place_id": chosen["provider_place_id"],
            "confirmed": True,
        },
    )


async def add(client: AsyncClient, trip_id: str, query: str, **extra) -> dict:
    pending = await resolve(client, trip_id, query, **extra)
    response = await confirm(client, trip_id, pending)
    assert response.status_code == 201, response.text
    return response.json()


async def names(client: AsyncClient, trip_id: str) -> list[str]:
    response = await client.get(url(trip_id))
    assert response.status_code == 200
    return [d["location"]["place_name"] for d in response.json()]


async def sequences(client: AsyncClient, trip_id: str) -> list[int]:
    return [d["sequence"] for d in (await client.get(url(trip_id))).json()]


# ---------- Basic operations ----------


async def test_add_one_destination(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    body = await add(client, trip_id, "Goa", planned_duration_days=3)

    assert body["trip_id"] == trip_id
    assert body["sequence"] == 0
    assert body["destination_type"] == "primary"
    assert body["status"] == "confirmed"
    assert body["planned_duration_days"] == 3
    assert body["location"]["place_name"] == "Goa"
    assert body["location"]["latitude"] == "15.299326"
    # Provider internals are not part of the destination response.
    assert "provider_place_id" not in body["location"]


async def test_add_multiple_destinations_in_sequence_order(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    for name in ("Goa", "Gokarna", "Murudeshwar"):
        await add(client, trip_id, name)

    assert await names(client, trip_id) == ["Goa", "Gokarna", "Murudeshwar"]
    assert await sequences(client, trip_id) == [0, 1, 2]


async def test_add_optional_destination(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    await add(client, trip_id, "Goa")
    body = await add(client, trip_id, "Murudeshwar", destination_type="optional")
    assert body["destination_type"] == "optional"

    # Promote optional -> primary
    response = await client.patch(url(trip_id, f"/{body['id']}"), json={"destination_type": "primary"})
    assert response.status_code == 200
    assert response.json()["destination_type"] == "primary"


async def test_add_from_existing_location_reuses_it(client: AsyncClient, db_session: AsyncSession, provider):
    trip_a = await create_trip(client)
    goa = await add(client, trip_a, "Goa")
    trip_b = await create_trip(client)

    response = await client.post(url(trip_b), json={"location_id": goa["location"]["id"]})
    assert response.status_code == 201
    assert response.json()["location"]["id"] == goa["location"]["id"]
    assert len((await db_session.execute(select(Location))).scalars().all()) == 1

    missing = await client.post(url(trip_b), json={"location_id": MISSING_ID})
    assert missing.status_code == 404


async def test_update_duration_does_not_touch_location(client: AsyncClient, db_session: AsyncSession, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa", planned_duration_days=2)

    response = await client.patch(url(trip_id, f"/{goa['id']}"), json={"planned_duration_days": 4})
    assert response.status_code == 200
    assert response.json()["planned_duration_days"] == 4

    cleared = await client.patch(url(trip_id, f"/{goa['id']}"), json={"planned_duration_days": None})
    assert cleared.json()["planned_duration_days"] is None

    invalid = await client.patch(url(trip_id, f"/{goa['id']}"), json={"planned_duration_days": 0})
    assert invalid.status_code == 422

    location = (await db_session.execute(select(Location))).scalar_one()
    assert location.place_name == "Goa"


async def test_update_preferences(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa", preferences={"preferred_areas": ["South Goa"]})
    assert goa["preferences"]["preferred_areas"] == ["South Goa"]

    response = await client.put(
        url(trip_id, f"/{goa['id']}/preferences"),
        json={
            "preferred_areas": ["North Goa", "north goa"],
            "preferred_activities": ["beaches", "nightlife"],
            "avoid": ["Calangute"],
        },
    )
    assert response.status_code == 200
    assert response.json()["preferences"] == {
        "preferred_areas": ["North Goa"],  # case-insensitive duplicate dropped
        "preferred_activities": ["beaches", "nightlife"],
        "avoid": ["Calangute"],
    }

    unknown_key = await client.put(url(trip_id, f"/{goa['id']}/preferences"), json={"budget": ["x"]})
    assert unknown_key.status_code == 422


async def test_list_for_missing_trip_returns_404(client: AsyncClient, provider):
    response = await client.get(url(MISSING_ID))
    assert response.status_code == 404
    assert response.json()["message"] == "Trip not found"


# ---------- Confirmation ----------


async def test_confirm_valid_candidate_persists_location_and_ref(
    client: AsyncClient, db_session: AsyncSession, provider
):
    trip_id = await create_trip(client)
    pending = await resolve(client, trip_id, "Goa")
    assert pending["status"] == "pending"
    assert pending["requires_confirmation"] is True

    response = await confirm(client, trip_id, pending)
    assert response.status_code == 201

    ref = (await db_session.execute(select(LocationProviderRef))).scalar_one()
    assert ref.provider_place_id == "relation:1"
    # Confirming uses the stored candidates: the provider was called once (at resolve).
    assert provider.calls == 1

    again = await confirm(client, trip_id, pending)
    assert again.status_code == 409  # a pending item can be confirmed only once


async def test_confirm_requires_explicit_true(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    pending = await resolve(client, trip_id, "Goa")
    chosen = pending["candidates"][0]
    base = {"provider_name": chosen["provider_name"], "provider_place_id": chosen["provider_place_id"]}

    missing = await client.post(url(trip_id, f"/pending/{pending['id']}/confirm"), json=base)
    false = await client.post(url(trip_id, f"/pending/{pending['id']}/confirm"), json={**base, "confirmed": False})
    assert missing.status_code == 422
    assert false.status_code == 422


async def test_reject_invalid_candidate(client: AsyncClient, db_session: AsyncSession, provider):
    trip_id = await create_trip(client)
    pending = await resolve(client, trip_id, "Goa")

    response = await client.post(
        url(trip_id, f"/pending/{pending['id']}/confirm"),
        json={"provider_name": "fake", "provider_place_id": "node:999", "confirmed": True},
    )
    assert response.status_code == 400
    assert (await db_session.execute(select(TripLocation))).scalars().all() == []
    assert (await db_session.execute(select(Location))).scalars().all() == []


async def test_reject_pending_from_another_trip(client: AsyncClient, db_session: AsyncSession, provider):
    trip_a = await create_trip(client)
    trip_b = await create_trip(client)
    pending_b = await resolve(client, trip_b, "Goa")

    response = await confirm(client, trip_a, pending_b)
    assert response.status_code == 404
    assert response.json()["message"] == "Pending destination not found"
    assert (await db_session.execute(select(TripLocation))).scalars().all() == []


async def test_unresolved_destination_is_not_treated_as_confirmed(
    client: AsyncClient, db_session: AsyncSession, provider
):
    trip_id = await create_trip(client)
    pending = await resolve(client, trip_id, "Goa")
    no_match = await resolve(client, trip_id, "Atlantis")
    assert no_match["candidates"] == []

    assert (await client.get(url(trip_id))).json() == []
    assert (await db_session.execute(select(TripLocation))).scalars().all() == []

    cancel = await client.delete(url(trip_id, f"/pending/{pending['id']}"))
    assert cancel.status_code == 204
    after_cancel = await confirm(client, trip_id, pending)
    assert after_cancel.status_code == 409


async def test_duplicate_destination_rejected(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    await add(client, trip_id, "Goa")
    pending = await resolve(client, trip_id, "Goa")
    response = await confirm(client, trip_id, pending)
    assert response.status_code == 409


# ---------- Reordering ----------


async def test_reorder_destinations(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa")
    gokarna = await add(client, trip_id, "Gokarna")
    murud = await add(client, trip_id, "Murudeshwar", destination_type="optional")

    response = await client.put(
        url(trip_id, "/order"), json={"destination_ids": [goa["id"], murud["id"], gokarna["id"]]}
    )
    assert response.status_code == 200
    body = response.json()
    assert [d["location"]["place_name"] for d in body] == ["Goa", "Murudeshwar", "Gokarna"]
    assert [d["sequence"] for d in body] == [0, 1, 2]
    # Identity, type and status are preserved
    assert body[1]["id"] == murud["id"]
    assert body[1]["destination_type"] == "optional"
    assert all(d["status"] == "confirmed" for d in body)


async def test_reorder_rejects_duplicate_ids(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa")
    await add(client, trip_id, "Gokarna")

    response = await client.put(url(trip_id, "/order"), json={"destination_ids": [goa["id"], goa["id"]]})
    assert response.status_code == 422
    assert await names(client, trip_id) == ["Goa", "Gokarna"]


async def test_reorder_rejects_destinations_of_another_trip(client: AsyncClient, provider):
    trip_a = await create_trip(client)
    trip_b = await create_trip(client)
    goa = await add(client, trip_a, "Goa")
    other = await add(client, trip_b, "Gokarna")

    response = await client.put(url(trip_a, "/order"), json={"destination_ids": [other["id"]]})
    assert response.status_code == 400
    assert response.json()["details"]["unknown_ids"] == [other["id"]]

    incomplete = await add(client, trip_a, "Murudeshwar")
    missing_one = await client.put(url(trip_a, "/order"), json={"destination_ids": [incomplete["id"]]})
    assert missing_one.status_code == 400
    assert await names(client, trip_a) == ["Goa", "Murudeshwar"]
    assert goa["sequence"] == 0


async def test_sequences_stay_consistent_after_many_changes(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    ids = [(await add(client, trip_id, n))["id"] for n in ("Goa", "Gokarna", "Murudeshwar")]

    for order in ([ids[2], ids[0], ids[1]], [ids[1], ids[2], ids[0]], ids):
        response = await client.put(url(trip_id, "/order"), json={"destination_ids": order})
        assert response.status_code == 200
        assert [d["id"] for d in response.json()] == order
        assert await sequences(client, trip_id) == [0, 1, 2]


# ---------- Removal ----------


async def test_remove_destination_keeps_location_and_normalizes(
    client: AsyncClient, db_session: AsyncSession, provider
):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa")
    await add(client, trip_id, "Gokarna")
    await add(client, trip_id, "Murudeshwar")

    response = await client.delete(url(trip_id, f"/{goa['id']}"))
    assert response.status_code == 204

    assert await names(client, trip_id) == ["Gokarna", "Murudeshwar"]
    assert await sequences(client, trip_id) == [0, 1]

    # Canonical location and its provider ref survive
    location = await db_session.get(Location, goa["location"]["id"])
    assert location is not None
    refs = (await db_session.execute(select(LocationProviderRef))).scalars().all()
    assert len(refs) == 3

    # History is kept
    history = (await client.get(url(trip_id), params={"include_history": True})).json()
    assert history[-1]["id"] == goa["id"]
    assert history[-1]["status"] == "removed"

    # Removed destinations can't be changed or removed twice
    assert (await client.delete(url(trip_id, f"/{goa['id']}"))).status_code == 409
    # ...but the same place can be added again
    await add(client, trip_id, "Goa")
    assert await names(client, trip_id) == ["Gokarna", "Murudeshwar", "Goa"]


async def test_remove_does_not_affect_other_trips(client: AsyncClient, provider):
    trip_a = await create_trip(client)
    trip_b = await create_trip(client)
    goa_a = await add(client, trip_a, "Goa")
    goa_b = await add(client, trip_b, "Goa")
    assert goa_a["location"]["id"] == goa_b["location"]["id"]

    await client.delete(url(trip_a, f"/{goa_a['id']}"))

    assert await names(client, trip_a) == []
    remaining = (await client.get(url(trip_b))).json()
    assert [d["id"] for d in remaining] == [goa_b["id"]]
    assert remaining[0]["status"] == "confirmed"


# ---------- Replacement ----------


async def test_replace_destination(client: AsyncClient, db_session: AsyncSession, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa", planned_duration_days=3, preferences={"preferred_areas": ["North Goa"]})
    gokarna = await add(client, trip_id, "Gokarna")

    pending = (await client.post(url(trip_id, f"/{goa['id']}/replace"), json={"query": "Kerala"})).json()
    assert pending["replaces_destination_id"] == goa["id"]
    # Until confirmed, Goa is still the active destination
    assert await names(client, trip_id) == ["Goa", "Gokarna"]

    response = await confirm(client, trip_id, pending)
    assert response.status_code == 201
    kerala = response.json()
    assert kerala["location"]["place_name"] == "Kerala"
    assert kerala["sequence"] == 0  # took Goa's position
    assert kerala["planned_duration_days"] == 3  # inherited
    assert kerala["preferences"]["preferred_areas"] == []  # not copied from Goa

    assert await names(client, trip_id) == ["Kerala", "Gokarna"]

    # Old association kept as history and linked to its successor
    history = (await client.get(url(trip_id), params={"include_history": True})).json()
    old = next(d for d in history if d["id"] == goa["id"])
    assert old["status"] == "replaced"
    assert old["replaced_by_id"] == kerala["id"]
    assert old["preferences"]["preferred_areas"] == ["North Goa"]
    assert await db_session.get(Location, goa["location"]["id"]) is not None
    assert gokarna["id"] in [d["id"] for d in history]


async def test_replace_rejects_existing_destination(client: AsyncClient, provider):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa")
    await add(client, trip_id, "Gokarna")

    pending = (await client.post(url(trip_id, f"/{goa['id']}/replace"), json={"query": "Gokarna"})).json()
    response = await confirm(client, trip_id, pending)
    assert response.status_code == 409
    assert await names(client, trip_id) == ["Goa", "Gokarna"]


async def test_replace_failure_rolls_back_everything(
    client: AsyncClient, db_session: AsyncSession, provider, monkeypatch
):
    trip_id = await create_trip(client)
    goa = await add(client, trip_id, "Goa")
    pending = (await client.post(url(trip_id, f"/{goa['id']}/replace"), json={"query": "Kerala"})).json()

    async def failing_add(self, trip_location):
        # Simulate the DB rejecting the new row after Goa was already marked "replaced".
        raise IntegrityError("INSERT trip_locations", {}, Exception("simulated failure"))

    monkeypatch.setattr(DestinationRepository, "add", failing_add)
    response = await confirm(client, trip_id, pending)
    assert response.status_code == 409
    monkeypatch.undo()

    # Goa is still the only, active destination; Kerala was not created; pending is still open
    current = (await client.get(url(trip_id), params={"include_history": True})).json()
    assert [(d["id"], d["status"]) for d in current] == [(goa["id"], "confirmed")]
    assert (await db_session.execute(select(Location).where(Location.place_name == "Kerala"))).scalar_one_or_none() is None
    stored = await db_session.get(PendingDestination, pending["id"])
    await db_session.refresh(stored)
    assert stored.status.value == "pending"

    # And the replacement can still be completed afterwards
    retry = await confirm(client, trip_id, pending)
    assert retry.status_code == 201


async def test_origin_rows_are_not_listed_as_destinations(
    client: AsyncClient, db_session: AsyncSession, provider
):
    trip_id = await create_trip(client)
    await add(client, trip_id, "Goa")
    roles = [tl.role for tl in (await db_session.execute(select(TripLocation))).scalars().all()]
    assert roles == [TripLocationRole.DESTINATION]


# ---------- Task 4: End-to-End Destination Workflow & Compatibility ----------

async def test_end_to_end_destination_lifecycle_workflow(
    client: AsyncClient, db_session: AsyncSession, provider
):
    """
    Task 4 End-to-End Workflow:
    1. Create a trip without a destination.
    2. Resolve and confirm Goa.
    3. Add Gokarna.
    4. Add Murudeshwar as an optional destination.
    5. Reorder the destinations.
    6. Update planned duration and preferences.
    7. Replace Goa with Kerala.
    8. Remove Gokarna.
    9. Retrieve the trip and verify structured destinations and legacy summary.
    10. Verify removed and replaced destinations remain in historical records with correct statuses.
    11. Verify contiguous sequence numbers, replaced_by_id linkage, and rollback safety.
    """
    # 1. Create a trip without a destination
    create_payload = {
        "origin": "Hyderabad",
        "start_date": "2027-01-10",
        "end_date": "2027-01-25",
        "budget": "60000.00",
        "travelers": 2,
    }
    trip_res = await client.post("/api/v1/trips/", json=create_payload)
    assert trip_res.status_code == 200
    trip_id = trip_res.json()["trip_id"]

    init_detail = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert init_detail["destination"] is None
    assert init_detail["destinations"] == []

    # 2. Resolve and confirm Goa (Sequence 0, Primary)
    goa_pending = await resolve(client, trip_id, "Goa", destination_type="primary", planned_duration_days=4)
    goa_confirm_res = await confirm(client, trip_id, goa_pending)
    assert goa_confirm_res.status_code == 201
    goa_id = goa_confirm_res.json()["id"]

    # 3. Add Gokarna (Sequence 1, Primary)
    gokarna_pending = await resolve(client, trip_id, "Gokarna", destination_type="primary", planned_duration_days=3)
    gokarna_confirm_res = await confirm(client, trip_id, gokarna_pending)
    assert gokarna_confirm_res.status_code == 201
    gokarna_id = gokarna_confirm_res.json()["id"]

    # 4. Add Murudeshwar as an optional destination (Sequence 2, Optional)
    muru_pending = await resolve(
        client,
        trip_id,
        "Murudeshwar",
        destination_type="optional",
        planned_duration_days=2,
        preferences={"preferred_areas": ["Temple Beach"], "preferred_activities": ["Scuba diving"], "avoid": []},
    )
    muru_confirm_res = await confirm(client, trip_id, muru_pending)
    assert muru_confirm_res.status_code == 201
    muru_id = muru_confirm_res.json()["id"]

    # Check order: Goa (0), Gokarna (1), Murudeshwar (2, optional)
    dests_step4 = (await client.get(url(trip_id))).json()
    assert [d["location"]["place_name"] for d in dests_step4] == ["Goa", "Gokarna", "Murudeshwar"]
    assert [d["destination_type"] for d in dests_step4] == ["primary", "primary", "optional"]
    assert [d["sequence"] for d in dests_step4] == [0, 1, 2]

    # 5. Reorder the destinations: Murudeshwar (0), Goa (1), Gokarna (2)
    reorder_res = await client.put(
        url(trip_id, "/order"),
        json={"destination_ids": [muru_id, goa_id, gokarna_id]},
    )
    assert reorder_res.status_code == 200
    reordered_dests = reorder_res.json()
    assert [d["location"]["place_name"] for d in reordered_dests] == ["Murudeshwar", "Goa", "Gokarna"]
    assert [d["sequence"] for d in reordered_dests] == [0, 1, 2]

    # 6. Update planned duration and preferences on Murudeshwar
    update_res = await client.patch(
        url(trip_id, f"/{muru_id}"),
        json={"planned_duration_days": 3},
    )
    assert update_res.status_code == 200
    assert update_res.json()["planned_duration_days"] == 3

    # Update preferences on Murudeshwar
    pref_res = await client.put(
        url(trip_id, f"/{muru_id}/preferences"),
        json={
            "preferred_areas": ["Temple Beach", "Statue Park"],
            "preferred_activities": ["Sightseeing"],
            "avoid": ["Crowded spots"],
        },
    )
    assert pref_res.status_code == 200
    assert pref_res.json()["preferences"]["preferred_areas"] == ["Temple Beach", "Statue Park"]

    # 7. Replace Goa with Kerala (Goa is currently at sequence 1)
    replace_pending_res = await client.post(
        url(trip_id, f"/{goa_id}/replace"),
        json={"query": "Kerala", "planned_duration_days": 5},
    )
    assert replace_pending_res.status_code == 201
    replace_pending = replace_pending_res.json()

    replace_confirm_res = await confirm(client, trip_id, replace_pending)
    assert replace_confirm_res.status_code == 201
    kerala_id = replace_confirm_res.json()["id"]

    # Kerala takes Goa's sequence (1) and type (primary)
    kerala_dest = replace_confirm_res.json()
    assert kerala_dest["location"]["place_name"] == "Kerala"
    assert kerala_dest["sequence"] == 1
    assert kerala_dest["destination_type"] == "primary"
    assert kerala_dest["planned_duration_days"] == 5

    # 8. Remove Gokarna (currently at sequence 2)
    remove_res = await client.delete(url(trip_id, f"/{gokarna_id}"))
    assert remove_res.status_code == 204

    # 9. Retrieve the trip and verify structured destinations and legacy summary
    trip_detail_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert trip_detail_res.status_code == 200
    trip_detail = trip_detail_res.json()

    # Active destinations: Murudeshwar (0, optional), Kerala (1, primary)
    assert len(trip_detail["destinations"]) == 2
    assert trip_detail["destination"] == "Murudeshwar, Kerala"
    assert trip_detail["destinations"][0]["id"] == muru_id
    assert trip_detail["destinations"][0]["sequence"] == 0
    assert trip_detail["destinations"][0]["destination_type"] == "optional"
    assert trip_detail["destinations"][0]["location"]["place_name"] == "Murudeshwar"
    assert trip_detail["destinations"][1]["id"] == kerala_id
    assert trip_detail["destinations"][1]["sequence"] == 1
    assert trip_detail["destinations"][1]["destination_type"] == "primary"
    assert trip_detail["destinations"][1]["location"]["place_name"] == "Kerala"

    # Also check GET /trips/
    all_trips_res = await client.get("/api/v1/trips/")
    assert all_trips_res.status_code == 200
    matching = next(t for t in all_trips_res.json() if t["id"] == trip_id)
    assert matching["destination"] == "Murudeshwar, Kerala"
    assert len(matching["destinations"]) == 2

    # 10. Verify historical records: Goa (replaced), Gokarna (removed)
    history_res = await client.get(url(trip_id), params={"include_history": True})
    assert history_res.status_code == 200
    history = history_res.json()
    assert len(history) == 4

    by_id = {d["id"]: d for d in history}

    # Goa: status=replaced, replaced_by_id points to Kerala
    assert by_id[goa_id]["status"] == "replaced"
    assert by_id[goa_id]["replaced_by_id"] == kerala_id

    # Gokarna: status=removed
    assert by_id[gokarna_id]["status"] == "removed"

    # Murudeshwar & Kerala: status=confirmed
    assert by_id[muru_id]["status"] == "confirmed"
    assert by_id[kerala_id]["status"] == "confirmed"

    # 11. Contiguous sequences check for active destinations
    active_seqs = [d["sequence"] for d in [by_id[muru_id], by_id[kerala_id]]]
    assert active_seqs == [0, 1]

