"""Structured JSON logging for the whole application.

Stdlib ``logging`` with a JSON formatter, not structlog. Everything in this
codebase — SQLAlchemy, uvicorn, httpx — already logs through stdlib, so a
stdlib formatter captures all of it with one configuration. structlog would
render its own events as JSON and leave every library log line in a different
shape, which is the opposite of the point.

One handler on the root logger, writing to stderr. No file handlers: the
process is expected to run somewhere that captures its output streams.

stderr, not stdout, because stdout is the data channel for the CLIs: the eval
runner and adversarial suite print their ``--json`` reports there and CI pipes
them into files it then parses. A log line on stdout corrupts that document.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import Any

from backend.config import settings

# LogRecord attributes that are structure, not payload. Anything on a record
# outside this set arrived via `extra=` and belongs in the JSON output.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_configured = False


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Structured fields passed as logger.info("...", extra={...}).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _safe(value)

        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    """Keep a value JSON-encodable without letting the formatter raise."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def configure_logging(level: str | None = None, *, force: bool = False) -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: repeated calls are ignored unless ``force`` is set, so an
    import-time call cannot stack duplicate handlers onto the root logger and
    double every line.
    """
    global _configured
    if _configured and not force:
        return

    resolved = (level or settings.LOG_LEVEL or "INFO").upper()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    # uvicorn installs its own handlers; drop them so its lines go through
    # this formatter instead of appearing twice in two different shapes.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # SQLAlchemy's engine logger is verbose at INFO and echoes full SQL.
    logging.getLogger("sqlalchemy.engine").setLevel("INFO" if resolved == "DEBUG" else "WARNING")

    _configured = True
