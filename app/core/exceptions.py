from typing import Any


class AppException(Exception):
    """An expected application error that should reach the client as-is (e.g. 404)."""

    def __init__(self, message: str, status_code: int = 400, details: Any = None):
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)
