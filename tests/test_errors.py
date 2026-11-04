import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.infrastructure.database import get_db
from app.main import app
from app.services.trip_service import TripService

ERROR_KEYS = {"success", "message", "details"}


@pytest.fixture
async def crashing_client(monkeypatch, client: AsyncClient):
    """A client whose list endpoint raises an unexpected error.

    raise_app_exceptions=False makes the client return the 500 response
    instead of re-raising the exception inside the test.
    """

    async def boom(self):
        raise RuntimeError("internal detail: table xyz, password=hunter2")

    monkeypatch.setattr(TripService, "get_all_trips", boom)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_404_uses_standard_error_shape(client: AsyncClient):
    response = await client.get("/api/v1/trips/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json() == {"success": False, "message": "Trip not found", "details": None}


async def test_unknown_route_uses_standard_error_shape(client: AsyncClient):
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert set(response.json()) == ERROR_KEYS
    assert response.json()["success"] is False


async def test_422_lists_field_errors(client: AsyncClient):
    response = await client.post("/api/v1/trips/", json={"origin": "H", "budget": "-1"})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["message"] == "Request validation failed"
    fields = {error["field"] for error in body["details"]}
    assert {"origin", "budget", "destination", "start_date", "end_date"} <= fields
    assert all(set(error) == {"field", "message", "type"} for error in body["details"])


async def test_422_does_not_echo_user_input(client: AsyncClient):
    too_long_origin = "SECRETVALUE" * 10  # 110 chars, fails max_length=100

    response = await client.post("/api/v1/trips/", json={"origin": too_long_origin})

    assert response.status_code == 422
    assert "SECRETVALUE" not in response.text


async def test_500_hides_internals_in_production(crashing_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)

    response = await crashing_client.get("/api/v1/trips/")

    assert response.status_code == 500
    assert response.json() == {"success": False, "message": "Internal server error", "details": None}
    assert "hunter2" not in response.text


async def test_500_shows_only_error_type_in_debug(crashing_client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)

    response = await crashing_client.get("/api/v1/trips/")

    assert response.status_code == 500
    assert response.json()["details"] == {"error": "RuntimeError"}
    assert "hunter2" not in response.text


async def test_500_is_logged_with_stack_trace(crashing_client: AsyncClient, caplog):
    with caplog.at_level(logging.ERROR, logger="app.core.error_handlers"):
        await crashing_client.get("/api/v1/trips/")

    record = next(r for r in caplog.records if r.name == "app.core.error_handlers")
    assert "GET /api/v1/trips/" in record.getMessage()
    assert record.exc_info is not None  # stack trace attached


async def test_not_found_is_logged_with_trip_id(client: AsyncClient, caplog):
    missing = "00000000-0000-0000-0000-000000000000"
    with caplog.at_level(logging.WARNING, logger="app.services.trip_service"):
        await client.delete(f"/api/v1/trips/{missing}")

    assert any(missing in r.getMessage() for r in caplog.records)


async def test_get_db_rolls_back_and_logs_on_database_error(caplog):
    generator = get_db()
    session = await anext(generator)

    with caplog.at_level(logging.ERROR, logger="app.infrastructure.database"):
        with pytest.raises(OperationalError):
            await generator.athrow(OperationalError("SELECT 1", {"pw": "hunter2"}, Exception("down")))

    assert "OperationalError" in caplog.text
    assert "hunter2" not in caplog.text
    assert not session.in_transaction()


async def test_unversioned_paths_no_longer_exist(client: AsyncClient):
    for path in ("/trips/", "/health"):
        response = await client.get(path)
        assert response.status_code == 404
        assert set(response.json()) == ERROR_KEYS
