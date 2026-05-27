"""
Persist eval run results to data/evals.db (SQLite).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_CREATE = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT    NOT NULL,
    eval_type   TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS idx_er_type ON eval_runs(eval_type, run_at);
"""


def _ensure(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for stmt in _CREATE.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)


def save_run(
    db_path: Path,
    eval_type: str,
    summary: dict,
    details: dict | None = None,
) -> None:
    _ensure(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO eval_runs (run_at, eval_type, summary, details) VALUES (?,?,?,?)",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                eval_type,
                json.dumps(summary, default=str),
                json.dumps(details, default=str) if details is not None else None,
            ),
        )


def latest_runs(db_path: Path, limit: int = 20) -> list[dict]:
    try:
        _ensure(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, run_at, eval_type, summary FROM eval_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["summary"] = json.loads(d["summary"])
                result.append(d)
            return result
    except Exception:
        return []


def latest_summary(db_path: Path) -> dict:
    """Return the most recent summary for each eval_type."""
    runs = latest_runs(db_path, limit=100)
    seen: dict[str, dict] = {}
    for run in runs:
        et = run["eval_type"]
        if et not in seen:
            seen[et] = run
    return seen
