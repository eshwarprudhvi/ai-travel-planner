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
    assert response.json() == {"id": trip_id, **trip_payload()}


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
    updated = trip_payload(destination="Manali", budget="25000.00", travelers=3)

    response = await client.put(f"/api/v1/trips/{trip_id}", json=updated)

    assert response.status_code == 200
    assert response.json() == {"id": trip_id, **updated}

    # The change must be saved, not just echoed back.
    saved = (await client.get(f"/api/v1/trips/{trip_id}")).json()
    assert saved["destination"] == "Manali"
    assert saved["budget"] == "25000.00"


async def test_update_does_not_create_duplicate(client: AsyncClient):
    trip_id = await create_trip(client)

    await client.put(f"/api/v1/trips/{trip_id}", json=trip_payload(destination="Kerala"))

    trips = (await client.get("/api/v1/trips/")).json()
    assert len(trips) == 1


async def test_update_nonexistent_trip_returns_404(client: AsyncClient):
    response = await client.put(f"/api/v1/trips/{MISSING_ID}", json=trip_payload())

    assert response.status_code == 404


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
