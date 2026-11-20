import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("pathcite")


class ApiError(Exception):
    """Error returned to the client as JSON {detail, code}."""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        # Log the exception type only: messages/tracebacks must never carry image content to clients.
        logger.error("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "Internal server error", "code": "internal_error"}, status_code=500
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        fields = ", ".join(".".join(str(x) for x in e["loc"][1:]) or "body" for e in exc.errors())
        return JSONResponse(
            {"detail": f"Invalid request: {fields}", "code": "invalid_request"}, status_code=422
        )
