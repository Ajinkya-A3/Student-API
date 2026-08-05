from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.api.v1.students import router as student_router
from app.config import settings
from app.exceptions import register_exception_handlers
from app.logger import logger
from app.logger import setup_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan events.
    """

    setup_logger()

    logger.info(
        "application_starting",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        debug=settings.DEBUG,
    )

    yield

    logger.info(
        "application_shutdown",
        app=settings.APP_NAME,
    )


app = FastAPI(
    title=settings.APP_NAME,
    description="Student CRUD REST API built with FastAPI.",
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Register global exception handlers
register_exception_handlers(app)

# Register API routers
app.include_router(health_router)
app.include_router(student_router)


@app.get(
    "/",
    tags=["Root"],
)
def root():
    """
    Root endpoint.
    """

    logger.debug("root_endpoint_called")

    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "status": "running",
    }