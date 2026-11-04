from fastapi import APIRouter, FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.trips import router as trips_router
from app.core.config import settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import setup_logging

setup_logging(settings.DEBUG)

app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered travel planning system",
    version=settings.APP_VERSION,
    # No debug=True here: it makes Starlette return an HTML traceback page
    # instead of our JSON 500 handler, which would leak internals.
)

register_error_handlers(app)

# Version 1 of the public API. A breaking change later goes into /api/v2
# while /api/v1 keeps working for existing clients.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(trips_router)

app.include_router(api_v1_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
