"""Database-level tests for the location tables: constraints, cascades and relationships."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    DestinationType,
    Location,
    LocationProviderRef,
    LocationType,
    Trip,
    TripLocation,
    TripLocationRole,
)


def make_trip() -> Trip:
    return Trip(
        origin="Hyderabad",
        destination="Goa",
        start_date=date(2027, 1, 10),
        end_date=date(2027, 1, 14),
        budget=Decimal("20000.00"),
    )


def make_location(**overrides) -> Location:
    values = dict(
        place_name="Hyderabad",
        city="Hyderabad",
        state="Telangana",
        country="India",
        latitude=Decimal("17.385044"),
        longitude=Decimal("78.486671"),
        location_type=LocationType.CITY,
    )
    values.update(overrides)
    return Location(**values)


async def save(db: AsyncSession, *objects) -> None:
    db.add_all(objects)
    await db.commit()


async def assert_rejected(db: AsyncSession, *objects) -> None:
    db.add_all(objects)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_location_stores_enum_value_and_timestamps(db_session: AsyncSession):
    location = make_location(location_type=LocationType.RAILWAY_STATION)
    await save(db_session, location)

    raw = (await db_session.execute(
        text("SELECT location_type::text, created_at, updated_at FROM locations WHERE id = :id"),
        {"id": location.id},
    )).one()

    assert raw[0] == "railway_station"  # the value, not the Python name RAILWAY_STATION
    assert raw.created_at is not None and raw.updated_at is not None


async def test_coordinates_are_exact_decimals(db_session: AsyncSession):
    location = make_location()
    await save(db_session, location)
    await db_session.refresh(location)

    assert location.latitude == Decimal("17.385044")


@pytest.mark.parametrize("field, value", [("latitude", "90.5"), ("longitude", "-180.1")])
async def test_out_of_range_coordinates_are_rejected(db_session: AsyncSession, field, value):
    await assert_rejected(db_session, make_location(**{field: Decimal(value)}))


async def test_same_provider_place_id_cannot_be_stored_twice(db_session: AsyncSession):
    location = make_location()
    await save(db_session, location, LocationProviderRef(
        location=location, provider_name="open-meteo", provider_place_id="1269843",
    ))

    await assert_rejected(db_session, LocationProviderRef(
        location_id=location.id, provider_name="open-meteo", provider_place_id="1269843",
    ))


async def test_same_place_id_from_different_providers_is_allowed(db_session: AsyncSession):
    location = make_location()
    await save(
        db_session,
        location,
        LocationProviderRef(location=location, provider_name="open-meteo", provider_place_id="42"),
        LocationProviderRef(location=location, provider_name="google", provider_place_id="42"),
    )

    loaded = (await db_session.execute(
        select(Location).options(selectinload(Location.provider_refs)).where(Location.id == location.id)
    )).scalar_one()
    assert {ref.provider_name for ref in loaded.provider_refs} == {"open-meteo", "google"}


async def test_trip_cannot_have_two_origins_with_same_sequence(db_session: AsyncSession):
    trip, hyd, goa = make_trip(), make_location(), make_location(place_name="Goa")
    await save(db_session, trip, hyd, goa, TripLocation(
        trip=trip, location=hyd, role=TripLocationRole.ORIGIN, sequence=0,
    ))

    await assert_rejected(db_session, TripLocation(
        trip_id=trip.id, location_id=goa.id, role=TripLocationRole.ORIGIN, sequence=0,
    ))


async def test_origin_and_destination_can_share_a_sequence(db_session: AsyncSession):
    trip, hyd, goa = make_trip(), make_location(), make_location(place_name="Goa")
    await save(
        db_session, trip, hyd, goa,
        TripLocation(trip=trip, location=hyd, role=TripLocationRole.ORIGIN, sequence=0),
        TripLocation(trip=trip, location=goa, role=TripLocationRole.DESTINATION, sequence=0,
                     destination_type=DestinationType.PRIMARY),
    )


async def test_one_location_can_be_reused_by_many_trips(db_session: AsyncSession):
    hyd, trip_a, trip_b = make_location(), make_trip(), make_trip()
    await save(
        db_session, hyd, trip_a, trip_b,
        TripLocation(trip=trip_a, location=hyd, role=TripLocationRole.ORIGIN),
        TripLocation(trip=trip_b, location=hyd, role=TripLocationRole.ORIGIN),
    )

    locations = (await db_session.execute(select(Location))).scalars().all()
    assert len(locations) == 1


async def test_negative_sequence_is_rejected(db_session: AsyncSession):
    trip, hyd = make_trip(), make_location()
    await save(db_session, trip, hyd)

    await assert_rejected(db_session, TripLocation(
        trip_id=trip.id, location_id=hyd.id, role=TripLocationRole.ORIGIN, sequence=-1,
    ))


async def test_deleting_trip_removes_links_but_keeps_shared_location(db_session: AsyncSession):
    trip, hyd = make_trip(), make_location()
    await save(db_session, trip, hyd, TripLocation(trip=trip, location=hyd, role=TripLocationRole.ORIGIN))

    await db_session.execute(text("DELETE FROM trips WHERE id = :id"), {"id": trip.id})
    await db_session.commit()

    links = (await db_session.execute(text("SELECT count(*) FROM trip_locations"))).scalar_one()
    locations = (await db_session.execute(text("SELECT count(*) FROM locations"))).scalar_one()
    assert (links, locations) == (0, 1)


async def test_location_in_use_cannot_be_deleted(db_session: AsyncSession):
    trip, hyd = make_trip(), make_location()
    await save(db_session, trip, hyd, TripLocation(trip=trip, location=hyd, role=TripLocationRole.ORIGIN))

    with pytest.raises(IntegrityError):
        await db_session.execute(text("DELETE FROM locations WHERE id = :id"), {"id": hyd.id})
        await db_session.commit()
    await db_session.rollback()


async def test_deleting_location_removes_its_provider_refs(db_session: AsyncSession):
    hyd = make_location()
    await save(db_session, hyd, LocationProviderRef(location=hyd, provider_name="open-meteo", provider_place_id="1"))

    await db_session.execute(text("DELETE FROM locations WHERE id = :id"), {"id": hyd.id})
    await db_session.commit()

    refs = (await db_session.execute(text("SELECT count(*) FROM location_provider_refs"))).scalar_one()
    assert refs == 0
