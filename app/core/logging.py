"""Small JSON logger; exception details are deliberately excluded."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def configure_logging() -> None:
    logger = logging.getLogger("app")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
