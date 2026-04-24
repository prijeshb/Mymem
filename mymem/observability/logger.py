"""
Structured logger — Rich (dev) or JSON (prod), with correlation IDs.

Usage:
    from mymem.observability.logger import get_logger

    log = get_logger(__name__)
    log.info("Compiling article", file="raw/foo.md", model="gemma3:12b")

In prod (log_format=json) each line is a JSON object:
    {"ts": "2026-04-03T10:00:00Z", "level": "INFO", "logger": "mymem.compile",
     "msg": "Compiling article", "file": "raw/foo.md", "model": "gemma3:12b",
     "run_id": "abc123"}
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

# ---------------------------------------------------------------------------
# Correlation ID — propagated through a pipeline run
# ---------------------------------------------------------------------------

_run_id: ContextVar[str] = ContextVar("run_id", default="")


def set_run_id(run_id: str | None = None) -> str:
    """Set (or generate) a run ID for the current pipeline execution."""
    rid = run_id or uuid.uuid4().hex[:8]
    _run_id.set(rid)
    return rid


def get_run_id() -> str:
    return _run_id.get() or "—"


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """One JSON object per log line — machine-parseable, grep-friendly."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": get_run_id(),
        }
        # Attach any extra kwargs passed to log.*()
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                payload[key] = val

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Setup — called once at startup from cli.py / app.py
# ---------------------------------------------------------------------------

_configured = False


def configure_logging(
    level: str = "INFO",
    fmt: str = "rich",
    log_file: Path | None = None,
) -> None:
    """
    Configure the root mymem logger. Safe to call multiple times (idempotent).

    Args:
        level:    DEBUG | INFO | WARNING | ERROR
        fmt:      "rich" for coloured console (dev), "json" for structured (prod)
        log_file: Optional path to write all logs (always JSON in file).
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("mymem")
    root.setLevel(level)
    root.propagate = False

    # Console handler
    if fmt == "rich":
        console_handler = RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
        console_handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(_JSONFormatter())

    root.addHandler(console_handler)

    # File handler — always JSON so logs are parseable
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_JSONFormatter())
        root.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_logger(name: str) -> "ContextLogger":
    """Return a logger that auto-injects run_id into every record."""
    return ContextLogger(logging.getLogger(name))


class ContextLogger:
    """
    Thin wrapper around stdlib Logger that injects run_id and supports
    keyword-argument extras natively:

        log.info("step done", duration_ms=42, tokens=500)
    """

    def __init__(self, inner: logging.Logger) -> None:
        self._inner = inner

    def _log(self, level: int, msg: str, exc_info: bool = False, **kwargs: Any) -> None:
        extra = {"run_id": get_run_id(), **kwargs}
        # Append key=value pairs to the message so they appear in Rich console output.
        # JSON formatter still receives them as structured fields via extra=.
        if kwargs:
            pairs = "  ".join(f"{k}={v}" for k, v in kwargs.items())
            display_msg = f"{msg}  {pairs}"
        else:
            display_msg = msg
        self._inner.log(level, display_msg, extra=extra, exc_info=exc_info, stacklevel=3)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, exc_info: bool = False, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, exc_info=exc_info, **kwargs)

    def error(self, msg: str, exc_info: bool = False, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, exc_info=exc_info, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        extra = {"run_id": get_run_id(), **kwargs}
        self._inner.exception(msg, extra=extra, stacklevel=2)
