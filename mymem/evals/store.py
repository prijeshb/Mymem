"""
Persist eval run results to data/evals.db (SQLite).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mymem.evals.extraction_consensus import ExtractionConsensusResult

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

_CREATE_CONSENSUS = """
CREATE TABLE IF NOT EXISTS extraction_consensus (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at           TEXT    NOT NULL,
    source_id        TEXT    NOT NULL,
    source_type      TEXT    NOT NULL,
    pipeline_model   TEXT    NOT NULL,
    reference_model  TEXT    NOT NULL,
    consensus_score  REAL    NOT NULL,
    thesis_captured  INTEGER NOT NULL,
    grade            TEXT    NOT NULL,
    gaps_json        TEXT    NOT NULL,
    false_pos_json   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ec_score ON extraction_consensus(consensus_score);
"""


def _ensure(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for stmt in _CREATE.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)


def _ensure_consensus(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for stmt in _CREATE_CONSENSUS.strip().split(";"):
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


def save_extraction_consensus(db_path: Path, result: "ExtractionConsensusResult") -> None:
    """Persist an ExtractionConsensusResult to the extraction_consensus table."""
    _ensure_consensus(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO extraction_consensus
               (run_at, source_id, source_type, pipeline_model, reference_model,
                consensus_score, thesis_captured, grade, gaps_json, false_pos_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                result.source_id,
                result.source_type,
                result.pipeline_model,
                result.reference_model,
                result.consensus_score,
                int(result.thesis_captured),
                result.grade,
                json.dumps(list(result.gaps)),
                json.dumps(list(result.false_positives)),
            ),
        )


def recent_consensus_runs(
    db_path: Path,
    limit: int = 20,
    order: str = "recent_first",
) -> list[dict]:
    """
    Return recent extraction consensus runs.

    order: "recent_first" (default) | "worst_first" (lowest consensus_score first)
    """
    try:
        _ensure_consensus(db_path)
        order_clause = (
            "consensus_score ASC, id DESC"
            if order == "worst_first"
            else "id DESC"
        )
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM extraction_consensus ORDER BY {order_clause} LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["gaps"] = json.loads(d.pop("gaps_json", "[]"))
            d["false_positives"] = json.loads(d.pop("false_pos_json", "[]"))
            d["thesis_captured"] = bool(d["thesis_captured"])
            result.append(d)
        return result
    except Exception:
        return []


def history_by_type(db_path: Path, limit_per_type: int = 30) -> dict[str, list[dict]]:
    """
    Return up to `limit_per_type` historical runs for each eval_type,
    oldest-first, keyed by eval_type.

    Useful for trend charts in the dashboard.
    """
    try:
        _ensure(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, run_at, eval_type, summary
                   FROM eval_runs
                   ORDER BY eval_type, id ASC"""
            ).fetchall()
    except Exception:
        return {}

    by_type: dict[str, list[dict]] = {}
    for row in rows:
        et = row["eval_type"]
        entry = {"run_at": row["run_at"], "id": row["id"]}
        try:
            entry.update(json.loads(row["summary"]))
        except Exception:
            pass
        by_type.setdefault(et, []).append(entry)

    return {k: v[-limit_per_type:] for k, v in by_type.items()}
