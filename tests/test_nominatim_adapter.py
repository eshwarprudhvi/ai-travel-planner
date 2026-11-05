"""Nominatim adapter tests. Uses httpx.MockTransport: no real network calls."""

import time
from decimal import Decimal

import httpx
import pytest

from app.integrations.geocoding import GeocodingProviderError
from app.integrations.geocoding.nominatim import NominatimGeocodingProvider
from app.models.location import LocationType

HYDERABAD = {
    "osm_type": "relation", "osm_id": 7868535,
    "lat": "17.3850440", "lon": "78.4866710",
    "category": "boundary", "type": "administrative", "addresstype": "city",
    "name": "Hyderabad",
    "display_name": "Hyderabad, Telangana, India",
    "address": {"city": "Hyderabad", "state": "Telangana", "country": "India"},
}
HYD_AIRPORT = {
    "osm_type": "way", "osm_id": 123, "lat": "17.2403", "lon": "78.4294",
    "category": "aeroway", "type": "aerodrome", "addresstype": "aeroway",
    "name": "Rajiv Gandhi International Airport",
    "display_name": "Rajiv Gandhi International Airport, Shamshabad, Telangana, India",
    "address": {"town": "Shamshabad", "state": "Telangana", "country": "India"},
}
HYD_STATION = {
    "osm_type": "node", "osm_id": 456, "lat": "17.4337", "lon": "78.5016",
    "category": "railway", "type": "station", "addresstype": "railway",
    "name": "Secunderabad Junction", "display_name": "Secunderabad Junction, Hyderabad, India",
    "address": {"city": "Hyderabad", "country": "India"},
}


def make_provider(handler, **overrides) -> tuple[NominatimGeocodingProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))
    options = dict(
        base_url="https://nominatim.example",
        user_agent="ai-travel-planner-tests/1.0",
        timeout_seconds=2,
        min_interval_seconds=0,
    )
    options.update(overrides)
    return NominatimGeocodingProvider(client, **options), requests


def respond_with(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


async def test_maps_city_result_to_candidate():
    provider, _ = make_provider(respond_with([HYDERABAD]))

    [candidate] = await provider.search("Hyderabad", limit=5)

    assert candidate.place_name == "Hyderabad"
    assert (candidate.city, candidate.state, candidate.country) == ("Hyderabad", "Telangana", "India")
    assert candidate.latitude == Decimal("17.385044")  # rounded to 6 places, exact Decimal
    assert candidate.longitude == Decimal("78.486671")
    assert candidate.location_type == LocationType.CITY
    assert candidate.provider_name == "nominatim"
    assert candidate.provider_place_id == "relation:7868535"


async def test_maps_transport_hubs_to_location_types():
    provider, _ = make_provider(respond_with([HYD_AIRPORT, HYD_STATION]))

    airport, station = await provider.search("Hyderabad transport", limit=5)

    assert airport.location_type == LocationType.AIRPORT
    assert airport.city == "Shamshabad"  # falls back to "town"
    assert station.location_type == LocationType.RAILWAY_STATION


async def test_sends_identifying_user_agent_and_expected_params():
    provider, requests = make_provider(respond_with([]))

    await provider.search("Goa", limit=3)

    request = requests[0]
    assert request.headers["User-Agent"] == "ai-travel-planner-tests/1.0"
    assert request.url.path == "/search"
    assert request.url.params["q"] == "Goa"
    assert request.url.params["format"] == "jsonv2"
    assert request.url.params["limit"] == "3"


async def test_no_results_returns_empty_list():
    provider, _ = make_provider(respond_with([]))

    assert await provider.search("zzzzqqq", limit=5) == []


@pytest.mark.parametrize(
    "broken",
    [
        {**HYDERABAD, "lat": None},
        {**HYDERABAD, "lon": "not-a-number"},
        {**HYDERABAD, "lat": "95.0"},
        {**HYDERABAD, "osm_id": None},
    ],
)
async def test_results_without_valid_coordinates_or_id_are_dropped(broken):
    provider, _ = make_provider(respond_with([broken, HYD_STATION]))

    candidates = await provider.search("Hyderabad", limit=5)

    # The bad result is skipped (never invented or guessed); the good one is kept.
    assert [c.place_name for c in candidates] == ["Secunderabad Junction"]


async def test_timeout_raises_provider_error():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    provider, _ = make_provider(timeout)

    with pytest.raises(GeocodingProviderError, match="timed out") as error:
        await provider.search("Goa", limit=5)
    assert error.value.retryable is True


async def test_connection_failure_raises_provider_error():
    def offline(request):
        raise httpx.ConnectError("no network", request=request)

    provider, _ = make_provider(offline)

    with pytest.raises(GeocodingProviderError, match="Could not reach"):
        await provider.search("Goa", limit=5)


@pytest.mark.parametrize(
    "status, retryable", [(429, True), (500, True), (503, True), (403, False)]
)
async def test_http_errors_raise_provider_error(status, retryable):
    provider, _ = make_provider(respond_with({"error": "x"}, status=status))

    with pytest.raises(GeocodingProviderError) as error:
        await provider.search("Goa", limit=5)
    assert error.value.retryable is retryable


async def test_invalid_json_raises_provider_error():
    provider, _ = make_provider(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(GeocodingProviderError, match="invalid JSON"):
        await provider.search("Goa", limit=5)


async def test_repeated_query_is_served_from_cache():
    provider, requests = make_provider(respond_with([HYDERABAD]))

    first = await provider.search("Hyderabad", limit=5)
    second = await provider.search("  hyderabad".strip().upper(), limit=5)  # case-insensitive

    assert first == second
    assert len(requests) == 1


async def test_errors_are_not_cached():
    responses = iter([httpx.Response(503), httpx.Response(200, json=[HYDERABAD])])
    provider, requests = make_provider(lambda request: next(responses))

    with pytest.raises(GeocodingProviderError):
        await provider.search("Hyderabad", limit=5)
    assert len(await provider.search("Hyderabad", limit=5)) == 1
    assert len(requests) == 2


async def test_requests_are_spaced_by_min_interval():
    provider, _ = make_provider(respond_with([]), min_interval_seconds=0.3)

    started = time.monotonic()
    await provider.search("Goa", limit=5)
    await provider.search("Pune", limit=5)  # different query: not cached, must wait
    elapsed = time.monotonic() - started

    assert elapsed >= 0.3
