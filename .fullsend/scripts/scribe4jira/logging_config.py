"""Structured logging configuration using Python standard library."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

try:
    from pythonjsonlogger import jsonlogger
except ImportError:
    jsonlogger = None  # type: ignore[assignment,misc]


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string.

        record: LogRecord to format

        returns: JSON-formatted log record string
        """
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class StructuredAdapter(logging.LoggerAdapter):
    """Adapter that makes it easy to add structured fields to log records."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, dict[str, Any]]:
        """Process log call to add structured fields.

        msg: Log message
        kwargs: Keyword arguments including optional 'extra' dict

        returns: Tuple of (message, updated kwargs)
        """
        extra = kwargs.get("extra", {})
        if extra:
            kwargs["extra"] = {"extra_fields": extra}
        return msg, kwargs


def setup_logging(level: str = "INFO", use_json: bool | None = None) -> None:
    """Configure root logger with structured logging support.

    level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    use_json: If True, use JSON format. If None, check SCRIBE_LOG_FORMAT env var
    """
    if use_json is None:
        use_json = os.environ.get("SCRIBE_LOG_FORMAT", "").lower() == "json"

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    formatter: logging.Formatter
    if use_json:
        if jsonlogger is not None:
            formatter = jsonlogger.JsonFormatter(
                "%(timestamp)s %(level)s %(name)s %(message)s",
                rename_fields={"levelname": "level", "name": "logger"},
                timestamp=True,
            )
        else:
            formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> StructuredAdapter:
    """Get a structured logger for the given module name.

        name: Logger name (typically __name__)

    returns: StructuredAdapter logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("user_login", extra={"user_id": "123", "ip": "192.168.1.1"})
    """
    base_logger = logging.getLogger(name)
    return StructuredAdapter(base_logger, {})
