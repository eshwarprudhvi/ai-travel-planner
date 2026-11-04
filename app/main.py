from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.health import router as health_router
from app.api.routes.trips import router as trips_router
from app.core.config import settings
from app.core.exceptions import AppException

app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered travel planning system",
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
)


@app.exception_handler(AppException)
async def app_exception_handler(
    request: Request,
    exc: AppException,
):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.message,
        },
    )


app.include_router(health_router)
app.include_router(trips_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
