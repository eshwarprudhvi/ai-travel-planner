from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.locations import router as locations_router
from app.api.routes.trips import router as trips_router
from app.core.config import settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import setup_logging
from app.integrations.geocoding.nominatim import NominatimGeocodingProvider

setup_logging(settings.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: one HTTP client and one geocoding provider for the whole app.
    # Reusing the client keeps connections open; sharing the provider makes its
    # rate limit (1 req/s) and cache apply across all users.
    async with httpx.AsyncClient() as http_client:
        app.state.geocoding_provider = NominatimGeocodingProvider(
            http_client,
            base_url=settings.NOMINATIM_BASE_URL,
            user_agent=settings.NOMINATIM_USER_AGENT,
            timeout_seconds=settings.GEOCODING_TIMEOUT_SECONDS,
            min_interval_seconds=settings.GEOCODING_MIN_INTERVAL_SECONDS,
            cache_ttl_seconds=settings.GEOCODING_CACHE_TTL_SECONDS,
        )
        yield
    # Shutdown: leaving the "async with" closes the HTTP client.


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered travel planning system",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    # No debug=True here: it makes Starlette return an HTML traceback page
    # instead of our JSON 500 handler, which would leak internals.
)

register_error_handlers(app)

# Version 1 of the public API. A breaking change later goes into /api/v2
# while /api/v1 keeps working for existing clients.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(trips_router)
api_v1_router.include_router(locations_router)

app.include_router(api_v1_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
