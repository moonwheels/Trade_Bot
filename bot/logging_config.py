"""Logging configuration helpers."""

from __future__ import annotations

import logging
import logging.config
from pathlib import Path

from .exceptions import ConfigurationError

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | corr=%(correlation_id)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class CorrelationIdFilter(logging.Filter):
    """Ensure every log record has a correlation identifier field."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure application logging."""
    normalized_level = level.strip().upper()
    numeric_level = getattr(logging, normalized_level, None)
    if not isinstance(numeric_level, int):
        raise ConfigurationError(
            f"Invalid log level '{level}'. Expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["correlation_id"],
            "level": normalized_level,
        }
    }
    root_handlers = ["console"]

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filters": ["correlation_id"],
            "filename": str(log_path),
            "maxBytes": 1_048_576,
            "backupCount": 3,
            "encoding": "utf-8",
            "level": normalized_level,
        }
        root_handlers.append("file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "correlation_id": {
                    "()": "bot.logging_config.CorrelationIdFilter",
                }
            },
            "formatters": {
                "standard": {
                    "format": LOG_FORMAT,
                    "datefmt": DATE_FORMAT,
                }
            },
            "handlers": handlers,
            "root": {
                "handlers": root_handlers,
                "level": normalized_level,
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger using the shared application configuration."""
    return logging.getLogger(name)
