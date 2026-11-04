from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# One engine for the whole app. It owns the connection pool.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,  # print every SQL statement while developing
    pool_pre_ping=True,  # check a pooled connection is alive before using it
)

# A factory that creates a new session each time it is called.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep loaded objects usable after commit
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    async with AsyncSessionLocal() as session:
        yield session
