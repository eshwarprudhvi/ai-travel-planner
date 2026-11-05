from httpx import AsyncClient

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def trip_payload(**overrides) -> dict:
    """A valid trip request body. Pass keyword arguments to change fields."""
    payload = {
        "origin": "Hyderabad",
        "destination": "Goa",
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
        "preferences": "budget hotels, local food",
    }
    payload.update(overrides)
    return payload


async def create_trip(client: AsyncClient, **overrides) -> str:
    response = await client.post("/api/v1/trips/", json=trip_payload(**overrides))
    assert response.status_code == 200
    return response.json()["trip_id"]


# ---------- Create ----------

async def test_create_trip_success(client: AsyncClient):
    response = await client.post("/api/v1/trips/", json=trip_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Trip created successfully"
    assert len(body["trip_id"]) == 36  # a UUID string
    assert body["trip"]["destination"] == "Goa"


async def test_create_trip_rejects_end_date_before_start_date(client: AsyncClient):
    response = await client.post("/api/v1/trips/", json=trip_payload(end_date="2027-01-05"))

    assert response.status_code == 422


async def test_create_trip_rejects_non_positive_budget(client: AsyncClient):
    response = await client.post("/api/v1/trips/", json=trip_payload(budget="0"))

    assert response.status_code == 422


# ---------- Read one ----------

async def test_get_trip_returns_saved_trip(client: AsyncClient):
    trip_id = await create_trip(client)

    response = await client.get(f"/api/v1/trips/{trip_id}")

    assert response.status_code == 200
    assert response.json() == {"id": trip_id, "destinations": [], **trip_payload()}


async def test_get_nonexistent_trip_returns_404(client: AsyncClient):
    response = await client.get(f"/api/v1/trips/{MISSING_ID}")

    assert response.status_code == 404
    assert response.json() == {"success": False, "message": "Trip not found", "details": None}


async def test_get_trip_with_invalid_uuid_returns_422(client: AsyncClient):
    response = await client.get("/api/v1/trips/abc-123")

    assert response.status_code == 422


# ---------- List ----------

async def test_list_trips_empty(client: AsyncClient):
    response = await client.get("/api/v1/trips/")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_trips_returns_all_sorted_by_start_date(client: AsyncClient):
    await create_trip(client, destination="Manali", start_date="2027-02-01", end_date="2027-02-05")
    await create_trip(client, destination="Goa", start_date="2027-01-10", end_date="2027-01-14")

    response = await client.get("/api/v1/trips/")

    assert response.status_code == 200
    assert [trip["destination"] for trip in response.json()] == ["Goa", "Manali"]


# ---------- Update ----------

async def test_update_trip_success(client: AsyncClient):
    trip_id = await create_trip(client)
    # Destination omitted: update succeeds for budget, travelers, etc.
    update_data = {"budget": "25000.00", "travelers": 3}

    response = await client.put(f"/api/v1/trips/{trip_id}", json=update_data)

    assert response.status_code == 200
    res_body = response.json()
    assert res_body["id"] == trip_id
    assert res_body["destination"] == "Goa"  # Unchanged legacy destination
    assert res_body["budget"] == "25000.00"
    assert res_body["travelers"] == 3

    # The change must be saved, not just echoed back.
    saved = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert saved["destination"] == "Goa"
    assert saved["budget"] == "25000.00"
    assert saved["travelers"] == 3


async def test_update_does_not_create_duplicate(client: AsyncClient):
    trip_id = await create_trip(client)

    await client.put(f"/api/v1/trips/{trip_id}", json={"budget": "35000.00"})

    trips = (await client.get("/api/v1/trips/")).json()
    assert len(trips) == 1


async def test_update_nonexistent_trip_returns_404(client: AsyncClient):
    response = await client.put(f"/api/v1/trips/{MISSING_ID}", json={"budget": "30000.00"})

    assert response.status_code == 404


# ---------- Task 3: Reject Direct Destination Mutations ----------

async def test_update_trip_rejects_new_destination_value(client: AsyncClient):
    trip_id = await create_trip(client, destination="Goa")

    response = await client.put(f"/api/v1/trips/{trip_id}", json={"destination": "Mumbai"})
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Destination cannot be updated directly through this endpoint" in body["message"]
    assert "/api/v1/trips/{trip_id}/destinations" in body["message"]

    # Verify Goa remained unchanged
    saved = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert saved["destination"] == "Goa"


async def test_update_trip_rejects_same_destination_value(client: AsyncClient):
    trip_id = await create_trip(client, destination="Goa")

    # Rejection applies even if destination value matches existing, keeping ownership rule unambiguous
    response = await client.put(f"/api/v1/trips/{trip_id}", json={"destination": "Goa"})
    assert response.status_code == 400
    assert "Destination cannot be updated directly through this endpoint" in response.json()["message"]


async def test_update_trip_rejects_explicit_null_destination(client: AsyncClient):
    trip_id = await create_trip(client, destination="Goa")

    response = await client.put(f"/api/v1/trips/{trip_id}", json={"destination": None})
    assert response.status_code == 400
    assert "Destination cannot be updated directly through this endpoint" in response.json()["message"]

    # Verify Goa remained unchanged
    saved = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert saved["destination"] == "Goa"


async def test_update_trip_rejects_destination_plus_budget_and_preserves_budget(client: AsyncClient):
    trip_id = await create_trip(client, destination="Goa", budget="20000.00")

    response = await client.put(
        f"/api/v1/trips/{trip_id}",
        json={"destination": "Mumbai", "budget": "30000.00"},
    )
    assert response.status_code == 400

    # Ensure the entire request was rejected and budget was NOT updated
    saved = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert saved["destination"] == "Goa"
    assert saved["budget"] == "20000.00"



# ---------- Delete ----------

async def test_delete_trip_success(client: AsyncClient):
    trip_id = await create_trip(client)

    response = await client.delete(f"/api/v1/trips/{trip_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await client.get(f"/api/v1/trips/{trip_id}")).status_code == 404


async def test_delete_nonexistent_trip_returns_404(client: AsyncClient):
    response = await client.delete(f"/api/v1/trips/{MISSING_ID}")

    assert response.status_code == 404


# ---------- Structured Destinations Serialization (Task 1) ----------

async def test_get_trip_returns_derived_destination_summary_and_structured_destinations(
    client: AsyncClient,
):
    # 1. Create a trip with initial legacy destination
    trip_id = await create_trip(client, destination="Legacy Destination")

    # 2. Before any TripLocation exists: returns empty destinations list and legacy string
    init_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert init_res.status_code == 200
    init_body = init_res.json()
    assert init_body["destination"] == "Legacy Destination"
    assert init_body["destinations"] == []

    # 3. Add first destination: Goa
    res_goa = await client.post(
        f"/api/v1/trips/{trip_id}/destinations/resolve",
        json={"query": "Goa"},
    )
    assert res_goa.status_code == 201
    pending_goa_id = res_goa.json()["id"]

    await client.post(
        f"/api/v1/trips/{trip_id}/destinations/pending/{pending_goa_id}/confirm",
        json={
            "provider_name": "nominatim",
            "provider_place_id": "relation:11251493",
            "confirmed": True,
        },
    )

    # 4. Add second destination: Gokarna
    res_gok = await client.post(
        f"/api/v1/trips/{trip_id}/destinations/resolve",
        json={"query": "Gokarna"},
    )
    assert res_gok.status_code == 201
    pending_gok = res_gok.json()
    pending_gok_id = pending_gok["id"]
    # Pick the candidate where place_name is "Gokarna"
    cand = next(c for c in pending_gok["candidates"] if c["place_name"] == "Gokarna")

    await client.post(
        f"/api/v1/trips/{trip_id}/destinations/pending/{pending_gok_id}/confirm",
        json={
            "provider_name": cand["provider_name"],
            "provider_place_id": cand["provider_place_id"],
            "confirmed": True,
        },
    )

    # 5. GET /trips/{trip_id} now derives destination from active TripLocation records in sequence order
    detail_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert detail_res.status_code == 200
    detail_body = detail_res.json()
    assert detail_body["destination"] == "Goa, Gokarna"
    assert len(detail_body["destinations"]) == 2
    assert detail_body["destinations"][0]["sequence"] == 0
    assert detail_body["destinations"][0]["location"]["place_name"] == "Goa"
    assert detail_body["destinations"][1]["sequence"] == 1
    assert detail_body["destinations"][1]["location"]["place_name"] == "Gokarna"

    # 6. GET /trips/ also reflects derived destination and structured destinations
    list_res = await client.get("/api/v1/trips/")
    assert list_res.status_code == 200
    trips_list = list_res.json()
    assert len(trips_list) == 1
    assert trips_list[0]["destination"] == "Goa, Gokarna"
    assert len(trips_list[0]["destinations"]) == 2


# ---------- Task 2: Optional Destination on Trip Creation ----------

async def test_create_trip_without_destination_field(client: AsyncClient):
    payload = {
        "origin": "Hyderabad",
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
    }
    response = await client.post("/api/v1/trips/", json=payload)
    assert response.status_code == 200
    trip_id = response.json()["trip_id"]

    get_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["destination"] is None
    assert body["destinations"] == []


async def test_create_trip_with_explicit_null_destination(client: AsyncClient):
    payload = {
        "origin": "Hyderabad",
        "destination": None,
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
    }
    response = await client.post("/api/v1/trips/", json=payload)
    assert response.status_code == 200
    trip_id = response.json()["trip_id"]

    get_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["destination"] is None
    assert body["destinations"] == []


async def test_create_trip_with_unverified_destination_string_does_not_create_trip_location(
    client: AsyncClient,
):
    payload = {
        "origin": "Hyderabad",
        "destination": "Goa",
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
    }
    response = await client.post("/api/v1/trips/", json=payload)
    assert response.status_code == 200
    trip_id = response.json()["trip_id"]

    get_res = await client.get(f"/api/v1/trips/{trip_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    # Stored string remains as legacy info; no TripLocation is created automatically
    assert body["destination"] == "Goa"
    assert body["destinations"] == []

    # Dedicated destinations endpoint confirms 0 active destinations
    dests_res = await client.get(f"/api/v1/trips/{trip_id}/destinations")
    assert dests_res.status_code == 200
    assert dests_res.json() == []


async def test_create_trip_without_destination_then_confirm_via_workflow(client: AsyncClient):
    payload = {
        "origin": "Hyderabad",
        "start_date": "2027-01-10",
        "end_date": "2027-01-14",
        "budget": "20000.00",
        "travelers": 2,
    }
    response = await client.post("/api/v1/trips/", json=payload)
    assert response.status_code == 200
    trip_id = response.json()["trip_id"]

    # Confirm Goa through destination workflow
    res_goa = await client.post(
        f"/api/v1/trips/{trip_id}/destinations/resolve",
        json={"query": "Goa"},
    )
    assert res_goa.status_code == 201
    pending_goa_id = res_goa.json()["id"]

    confirm_res = await client.post(
        f"/api/v1/trips/{trip_id}/destinations/pending/{pending_goa_id}/confirm",
        json={
            "provider_name": "nominatim",
            "provider_place_id": "relation:11251493",
            "confirmed": True,
        },
    )
    assert confirm_res.status_code == 201

    # Trip detail now reflects Goa as both derived string and structured destination
    detail = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert detail["destination"] == "Goa"
    assert len(detail["destinations"]) == 1
    assert detail["destinations"][0]["location"]["place_name"] == "Goa"


