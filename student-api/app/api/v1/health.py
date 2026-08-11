from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import logger


router = APIRouter(
    prefix="/api/v1",
    tags=["Health"],
)


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
)
def health():
    """
    Liveness Probe.

    Indicates whether the application process is running.
    """

    logger.debug("health_check")

    return {
        "status": "healthy",
    }


@router.get(
    "/ready",
    status_code=status.HTTP_200_OK,
)
def readiness(
    db: Session = Depends(get_db),
):
    """
    Readiness Probe.

    Indicates whether the application is ready
    to serve requests.
    """

    try:

        db.execute(text("SELECT 1"))

        logger.debug("readiness_check_success")

        return {
            "status": "ready",
            "database": "connected",
        }

    except SQLAlchemyError as exc:

        logger.error(
            "database_unavailable",
            error=str(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable.",
        )
