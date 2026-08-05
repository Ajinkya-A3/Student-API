from fastapi import FastAPI
from fastapi import Request
from fastapi import status
from fastapi.responses import JSONResponse

from app.logger import logger


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers.
    """

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