import logging
from collections.abc import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# One engine for the whole app. It owns the connection pool.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,  # print every SQL statement while developing
    pool_pre_ping=True,  # check a pooled connection is alive before using it
    hide_parameters=not settings.DEBUG,  # keep user data out of error messages in production
)

# A factory that creates a new session each time it is called.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep loaded objects usable after commit
)

# Dedicated engine and session factory for tests (points to travel_planner_test)
test_engine = create_async_engine(
    settings.TEST_DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    hide_parameters=not settings.DEBUG,
)

TestAsyncSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError as exc:
            # Log the error type only: SQLAlchemy messages can contain SQL and parameter values.
            # The full traceback is logged once by the 500 handler.
            logger.error("Database error, rolling back: %s", type(exc).__name__)
            await session.rollback()
            raise
