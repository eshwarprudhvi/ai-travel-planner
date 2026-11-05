"""API and service tests for POST /api/v1/locations/resolve, using a fake provider."""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_geocoding_provider
from app.core.config import settings
from app.integrations.geocoding import GeocodingCandidate, GeocodingProviderError
from app.main import app
from app.models import Location, LocationType

URL = "/api/v1/locations/resolve"


def candidate(name: str, place_id: str, **overrides) -> GeocodingCandidate:
    values = dict(
        place_name=name,
        display_name=f"{name}, India",
        city=name,
        state=None,
        country="India",
        latitude=Decimal("17.385044"),
        longitude=Decimal("78.486671"),
        location_type=LocationType.CITY,
        provider_name="fake",
        provider_place_id=place_id,
    )
    values.update(overrides)
    return GeocodingCandidate(**values)


class FakeProvider:
    name = "fake"
    attribution = "Fake data"

    def __init__(self, results=None, error: Exception | None = None):
        self.results = results or []
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, limit: int):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results[:limit]


@pytest.fixture
def use_provider():
    """Swap the real Nominatim provider for a fake one, for one test."""

    def install(provider: FakeProvider) -> FakeProvider:
        app.dependency_overrides[get_geocoding_provider] = lambda: provider
        return provider

    yield install
    app.dependency_overrides.pop(get_geocoding_provider, None)


async def test_single_match(client: AsyncClient, use_provider):
    use_provider(FakeProvider([candidate("Hyderabad", "relation:1")]))

    response = await client.post(URL, json={"query": "Hyderabad"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "single_match"
    assert body["requires_confirmation"] is True  # even one match is never auto-confirmed
    assert body["attribution"] == "Fake data"
    [result] = body["candidates"]
    assert result == {
        "place_name": "Hyderabad",
        "display_name": "Hyderabad, India",
        "city": "Hyderabad",
        "state": None,
        "country": "India",
        "latitude": "17.385044",
        "longitude": "78.486671",
        "location_type": "city",
        "provider_name": "fake",
        "provider_place_id": "relation:1",
    }


async def test_multiple_matches(client: AsyncClient, use_provider):
    use_provider(FakeProvider([
        candidate("Hyderabad", "relation:1"),
        candidate("Hyderabad", "relation:2", country="Pakistan", latitude=Decimal("25.396"),
                  longitude=Decimal("68.377")),
    ]))

    body = (await client.post(URL, json={"query": "Hyderabad"})).json()

    assert body["status"] == "multiple_matches"
    assert [c["country"] for c in body["candidates"]] == ["India", "Pakistan"]


async def test_no_match_is_success_with_empty_list(client: AsyncClient, use_provider):
    use_provider(FakeProvider([]))

    response = await client.post(URL, json={"query": "Qwertyville"})

    assert response.status_code == 200
    assert response.json()["status"] == "no_match"
    assert response.json()["candidates"] == []


async def test_duplicate_candidates_are_removed(client: AsyncClient, use_provider):
    use_provider(FakeProvider([candidate("Goa", "relation:9"), candidate("Goa", "relation:9")]))

    body = (await client.post(URL, json={"query": "Goa"})).json()

    assert body["status"] == "single_match"
    assert len(body["candidates"]) == 1


async def test_query_whitespace_is_normalized(client: AsyncClient, use_provider):
    provider = use_provider(FakeProvider([]))

    body = (await client.post(URL, json={"query": "  New    Delhi  "})).json()

    assert provider.queries == ["New Delhi"]
    assert body["query"] == "New Delhi"


@pytest.mark.parametrize("query", ["", "a", "   x   ", "x" * 201])
async def test_invalid_query_returns_422(client: AsyncClient, use_provider, query):
    provider = use_provider(FakeProvider([]))

    response = await client.post(URL, json={"query": query})

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert provider.queries == []  # the provider is never called with bad input


async def test_provider_failure_returns_503_standard_error(client: AsyncClient, use_provider):
    use_provider(FakeProvider(error=GeocodingProviderError("Geocoding provider timed out")))

    response = await client.post(URL, json={"query": "Goa"})

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Location search is temporarily unavailable",
        "details": None,
    }


async def test_disabled_switch_returns_503_without_calling_provider(
    client: AsyncClient, use_provider, monkeypatch
):
    provider = use_provider(FakeProvider([candidate("Goa", "relation:9")]))
    monkeypatch.setattr(settings, "GEOCODING_ENABLED", False)

    response = await client.post(URL, json={"query": "Goa"})

    assert response.status_code == 503
    assert response.json()["message"] == "Location search is currently disabled"
    assert provider.queries == []


async def test_resolving_saves_nothing(client: AsyncClient, use_provider, db_session: AsyncSession):
    use_provider(FakeProvider([candidate("Hyderabad", "relation:1")]))

    await client.post(URL, json={"query": "Hyderabad"})

    saved = (await db_session.execute(select(func.count()).select_from(Location))).scalar_one()
    assert saved == 0
