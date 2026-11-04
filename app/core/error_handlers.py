import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


def error_response(status_code: int, message: str, details: Any = None) -> JSONResponse:
    """The single error shape every failure uses: {success, message, details}."""
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "message": message, "details": details},
    )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.warning(
        "Application error: %s %s -> %s %s",
        request.method, request.url.path, exc.status_code, exc.message,
    )
    return error_response(exc.status_code, exc.message, exc.details)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Keep only field, message and type. Pydantic's "input" is left out on purpose:
    # it echoes whatever the user sent, which may be sensitive.
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"] if part != "body"),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    logger.info(
        "Validation failed: %s %s -> fields %s",
        request.method, request.url.path, [d["field"] for d in details],
    )
    return error_response(422, "Request validation failed", details)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # Framework errors such as unknown routes (404) or wrong method (405).
    logger.info(
        "HTTP error: %s %s -> %s", request.method, request.url.path, exc.status_code
    )
    return error_response(exc.status_code, str(exc.detail))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Full stack trace goes to the logs only, never to the client.
    logger.exception("Unhandled error: %s %s", request.method, request.url.path)
    details = {"error": type(exc).__name__} if settings.DEBUG else None
    return error_response(500, "Internal server error", details)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
