"""API error types + handlers (uniform JSON error envelope)."""


from fastapi import Request
from fastapi.responses import JSONResponse


class ServiceException(Exception):
    """Base service error with an HTTP status code."""

    status_code = 500

    def __init__(self, message: str, error_code: str = "SERVICE_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class NotFoundException(ServiceException):
    status_code = 404

    def __init__(self, message: str):
        super().__init__(message, error_code="NOT_FOUND")


def _envelope(request: Request, exc: ServiceException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "type": exc.error_code,
            "path": str(request.url.path),
        },
    )


async def service_exception_handler(request: Request, exc: ServiceException) -> JSONResponse:
    return _envelope(request, exc)


async def not_found_exception_handler(request: Request, exc: NotFoundException) -> JSONResponse:
    return _envelope(request, exc)
