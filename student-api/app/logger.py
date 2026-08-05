import logging
import sys
from typing import Literal
import structlog

from app.config import settings


class StdoutFilter(logging.Filter):
    """
    Allow only DEBUG, INFO and WARNING records.

    ERROR and CRITICAL are handled by stderr.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def setup_logger() -> None:
    """
    Configure structured JSON logging.

    Streams:
        stdout -> DEBUG, INFO, WARNING
        stderr -> ERROR, CRITICAL

    The minimum log level is controlled by LOG_LEVEL
    from the environment.
    """

    # Convert "DEBUG" -> logging.DEBUG
    log_level = getattr(logging, settings.LOG_LEVEL)

    # -----------------------------
    # stdout handler
    # -----------------------------
    stdout_handler = logging.StreamHandler(sys.stdout)

    stdout_handler.setLevel(logging.NOTSET)

    stdout_handler.addFilter(StdoutFilter())

    # -----------------------------
    # stderr handler
    # -----------------------------
    stderr_handler = logging.StreamHandler(sys.stderr)

    stderr_handler.setLevel(logging.ERROR)

    # -----------------------------
    # Root logger
    # -----------------------------
    root_logger = logging.getLogger()

    root_logger.handlers.clear()

    root_logger.setLevel(log_level)

    root_logger.addHandler(stdout_handler)

    root_logger.addHandler(stderr_handler)

    # -----------------------------
    # Structlog configuration
    # -----------------------------
    structlog.configure(
        processors=[
            # Include context variables (request_id etc.)
            structlog.contextvars.merge_contextvars,

            # ISO-8601 UTC timestamp
            structlog.processors.TimeStamper(
                fmt="iso",
                utc=True,
            ),

            # level
            structlog.stdlib.add_log_level,

            # logger name
            structlog.stdlib.add_logger_name,

            # filename, function and line number
            structlog.processors.CallsiteParameterAdder(
                {
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                }
            ),

            # Render exception traceback
            structlog.processors.format_exc_info,

            # Output JSON
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger(settings.APP_NAME)