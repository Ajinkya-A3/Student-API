from fastapi import FastAPI
from fastapi import Request
from fastapi import status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logger import logger


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers.
    """

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """
        Catch request validation failures (422).

        These happen before the route body runs, so without this
        handler they produce no structured log at all.
        """

        logger.warning(
            "request_validation_failed",
            method=request.method,
            path=request.url.path,
            errors=exc.errors(),
        )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Catch any unhandled exception.

        Logs the full stack trace and returns a generic
        500 response without exposing internal details.
        """

        logger.exception(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            client=str(request.client),
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal Server Error",
            },
        )