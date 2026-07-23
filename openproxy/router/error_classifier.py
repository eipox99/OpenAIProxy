from __future__ import annotations

from enum import Enum

import httpx


class ErrorType(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"


class ClassifiedError(Exception):
    """An error raised during a proxy request with a classified error type."""

    def __init__(self, error_type: ErrorType, message: str, status_code: int | None = None):
        self.error_type = error_type
        self.status_code = status_code
        super().__init__(message)


def classify_http_status(status_code: int) -> ErrorType:
    """Classify an HTTP response status code."""
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    if status_code == 401 or status_code == 403:
        return ErrorType.AUTH_ERROR
    if status_code == 400 or status_code == 422:
        return ErrorType.BAD_REQUEST
    if 500 <= status_code < 600:
        return ErrorType.SERVER_ERROR
    return ErrorType.UNKNOWN


def is_retryable(error_type: ErrorType) -> bool:
    """Return True if the error type should trigger a failover to the next provider."""
    return error_type in (
        ErrorType.RATE_LIMIT,
        ErrorType.AUTH_ERROR,  # Different providers may have valid keys
        ErrorType.SERVER_ERROR,
        ErrorType.TIMEOUT,
        ErrorType.NETWORK_ERROR,
    )


def classify_exception(exc: Exception) -> ClassifiedError:
    """Classify an httpx exception into a ClassifiedError."""
    if isinstance(exc, httpx.TimeoutException):
        return ClassifiedError(ErrorType.TIMEOUT, str(exc))
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.NetworkError)):
        return ClassifiedError(ErrorType.NETWORK_ERROR, str(exc))
    return ClassifiedError(ErrorType.UNKNOWN, str(exc))


def classify_response(response: httpx.Response) -> ClassifiedError | None:
    """Classify a non-2xx httpx Response. Returns None if the status is 2xx."""
    if response.is_success:
        return None
    error_type = classify_http_status(response.status_code)

    # Upgrade BAD_REQUEST to SERVER_ERROR if the body contains rate-limit
    # or resource-exhaustion keywords — some providers (e.g. Nvidia NIM)
    # return 400 instead of 429 for "ResourceExhausted".
    if error_type == ErrorType.BAD_REQUEST:
        body_lower = response.text[:1000].lower()
        if any(
            kw in body_lower
            for kw in ("resourc", "rate_limit", "rate limit", "too many", "exhausted", "capacity")
        ):
            error_type = ErrorType.RATE_LIMIT

    return ClassifiedError(error_type, response.text[:500], status_code=response.status_code)
