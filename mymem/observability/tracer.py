"""
LLM call tracer — records latency, token usage, and estimated cost.

Every LLM call goes through trace_llm():
  - Logs the call with timing and token counts
  - Persists to SQLite (llm_traces table)
  - Fires a cost alert if session spend exceeds the configured threshold

Usage:
    from mymem.observability.tracer import trace_llm

    with trace_llm(task="compile", model="gemma3:12b", provider="ollama") as t:
        result = llm.chat(messages)
        t.record(input_tokens=200, output_tokens=800)
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator

from mymem.observability.logger import get_logger, get_run_id

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Cost table (USD per 1M tokens) — update as pricing changes
# ---------------------------------------------------------------------------

# fmt: off
_COST_PER_1M: dict[str, dict[str, float]] = {
    # model_id: {input: $, output: $}
    "claude-sonnet-4-6":            {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":    {"input": 0.25,  "output": 1.25},
    "claude-opus-4-6":              {"input": 15.00, "output": 75.00},
    "gpt-4o":                       {"input": 5.00,  "output": 15.00},
    "gpt-4o-mini":                  {"input": 0.15,  "output": 0.60},
    # Local models — zero cost
    "gemma3:4b":                    {"input": 0.0,   "output": 0.0},
    "gemma3:12b":                   {"input": 0.0,   "output": 0.0},
    "gemma3:27b":                   {"input": 0.0,   "output": 0.0},
    "llama3.2:3b":                  {"input": 0.0,   "output": 0.0},
    "mistral:7b":                   {"input": 0.0,   "output": 0.0},
    "nomic-embed-text":             {"input": 0.0,   "output": 0.0},
}
# fmt: on

_DEFAULT_COST = {"input": 0.0, "output": 0.0}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _COST_PER_1M.get(model, _DEFAULT_COST)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Trace record
# ---------------------------------------------------------------------------

@dataclass
class TraceRecord:
    task: str
    model: str
    provider: str
    run_id: str
    started_at: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    _start: float = field(default_factory=time.perf_counter, repr=False)

    def record(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = estimate_cost(self.model, input_tokens, output_tokens)

    def finish(self, error: str | None = None) -> None:
        self.latency_ms = max((time.perf_counter() - self._start) * 1000, 0.001)
        self.error = error


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

def _ensure_table(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_traces (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT    NOT NULL,
                task          TEXT    NOT NULL,
                model         TEXT    NOT NULL,
                provider      TEXT    NOT NULL,
                started_at    TEXT    NOT NULL,
                latency_ms    REAL    NOT NULL DEFAULT 0,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd      REAL    NOT NULL DEFAULT 0,
                error         TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_run ON llm_traces(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_task ON llm_traces(task)")


def _persist(record: TraceRecord, db_path: Path) -> None:
    try:
        _ensure_table(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO llm_traces
                   (run_id, task, model, provider, started_at,
                    latency_ms, input_tokens, output_tokens, cost_usd, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.run_id, record.task, record.model, record.provider,
                    record.started_at, record.latency_ms, record.input_tokens,
                    record.output_tokens, record.cost_usd, record.error,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to persist LLM trace", error=str(exc))


# ---------------------------------------------------------------------------
# Session cost helper
# ---------------------------------------------------------------------------

def session_cost(db_path: Path, run_id: str) -> float:
    """Return total USD spent in the current run."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT SUM(cost_usd) FROM llm_traces WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row[0] or 0.0
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@contextmanager
def trace_llm(
    task: str,
    model: str,
    provider: str,
    db_path: Path | None = None,
    cost_alert_usd: float = 1.0,
) -> Generator[TraceRecord, None, None]:
    """
    Context manager that times an LLM call, logs it, and persists to DB.

    Args:
        task:           Pipeline step name (compile, qa, lint …)
        model:          Model identifier (e.g. "gemma3:12b")
        provider:       "anthropic" | "ollama" | "openai"
        db_path:        SQLite DB path (skips persist if None)
        cost_alert_usd: Warn when session cost exceeds this threshold
    """
    record = TraceRecord(
        task=task,
        model=model,
        provider=provider,
        run_id=get_run_id(),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )

    log.debug("LLM call started", task=task, model=model, provider=provider)

    error_msg: str | None = None
    try:
        yield record
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        record.finish(error=error_msg)

        if error_msg:
            log.error(
                "LLM call failed",
                task=task, model=model,
                latency_ms=round(record.latency_ms),
                error=error_msg,
            )
        else:
            log.info(
                "LLM call complete",
                task=task, model=model,
                latency_ms=round(record.latency_ms),
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_usd=round(record.cost_usd, 6),
            )

        if db_path:
            _persist(record, db_path)
            total = session_cost(db_path, record.run_id)
            if total >= cost_alert_usd:
                log.warning(
                    "Session cost threshold reached",
                    session_cost_usd=round(total, 4),
                    threshold_usd=cost_alert_usd,
                )
