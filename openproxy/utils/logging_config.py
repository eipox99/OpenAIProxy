from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from typing import Any

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)
LOG_FILE = os.path.join(LOG_DIR, "openaiproxy.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5


class JsonFormatter(logging.Formatter):
    """Output log records as JSON lines for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            obj["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra") and record.extra:
            obj["extra"] = record.extra
        return json.dumps(obj, default=str)


def setup_logging() -> logging.Logger:
    """Configure application-wide logging.

    Writes structured JSON to stdout (for Docker) and
    rotating text logs to a file inside the data volume.
    """
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    # Avoid duplicate handlers when reload is active (uvicorn --reload)
    if root.handlers:
        return root

    # --- Stdout handler (JSON, for Docker log collection) ---
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(LOG_LEVEL)
    stdout_handler.setFormatter(JsonFormatter())
    root.addHandler(stdout_handler)

    # --- File handler (rotating text, for persistent diagnostics) ---
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(LOG_LEVEL)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    root.info("Logging initialised", extra={"log_file": LOG_FILE, "log_level": LOG_LEVEL})
    return root
