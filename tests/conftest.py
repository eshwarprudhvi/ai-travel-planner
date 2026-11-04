from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database import TestAsyncSessionLocal, get_db, test_engine
from app.main import app
from app.models.base import Base
from app.models.trip import Trip  # noqa: F401  (registers the trips table on Base.metadata)

TEST_DB_NAME = "travel_planner_test"

# Safety net: refuse to run if the test URL ever points at another database,
# because the fixtures below drop every table.
assert settings.TEST_DATABASE_URL.database == TEST_DB_NAME, "Tests must use travel_planner_test"
assert settings.POSTGRES_DB != TEST_DB_NAME, "Dev and test databases must be different"


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def create_test_tables() -> AsyncGenerator[None, None]:
    """Create all tables once before the test run and drop them at the end."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # start clean even if a previous run crashed
        await conn.run_sync(Base.metadata.create_all)
    await test_engine.dispose()

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """One session per test. Data is wiped afterwards so tests stay independent."""
    async with TestAsyncSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))

    # Each test runs in its own event loop; pooled connections can't be reused
    # across loops, so close them all before the next test.
    await test_engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client whose requests use the test database session."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
