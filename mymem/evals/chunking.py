"""
Chunking quality evaluation.

- chunk_size_ablation(): simulate different max_tokens on the same text
- efficiency_report(): read ingest_analytics DB, group quality by chunk_count
- optimal_max_tokens(): recommend max_tokens for a model context window
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mymem.evals.hope import HopeScore, aggregate_hope, score_chunks
from mymem.evals.metrics import duplicate_rate
from mymem.pipeline.splitter import ChunkSplitter

_ABLATION_SIZES = [256, 512, 1024, 2048, 4096, 6000]


@dataclass
class AblationRow:
    max_tokens: int
    chunk_count: int
    avg_hope: float
    hope_grade: str
    duplicate_rate: float
    recommendation: str


@dataclass
class EfficiencyGroup:
    chunk_count: int
    n_ingests: int
    avg_concepts: float
    avg_page_chars: float
    avg_wikilinks: float
    avg_duplicate_rate: float


@dataclass
class ChunkingReport:
    ablation: list[AblationRow] = field(default_factory=list)
    efficiency_groups: list[EfficiencyGroup] = field(default_factory=list)
    recommended_max_tokens: int = 1024
    current_max_tokens: int = 1024

    @property
    def grade(self) -> str:
        # Informational eval — PASS when the ablation produced a recommendation,
        # WARN otherwise. Never FAIL: there is no wrong answer, only missing data.
        if self.ablation and self.recommended_max_tokens > 0:
            return "PASS"
        return "WARN"


def chunk_size_ablation(
    text: str,
    sizes: list[int] | None = None,
) -> list[AblationRow]:
    """
    Simulate chunking the same text at different max_tokens values.
    Returns HOPE scores + duplicate rate for each option.
    """
    sizes = sizes or _ABLATION_SIZES
    rows: list[AblationRow] = []
    for max_tokens in sizes:
        splitter = ChunkSplitter(max_tokens=max_tokens)
        chunks = splitter.split(text)
        hope_scores = score_chunks(chunks, max_tokens=max_tokens)
        agg = aggregate_hope(hope_scores)
        dup = duplicate_rate([c[:300] for c in chunks])

        if len(chunks) == 1 and agg.overall >= 0.7:
            rec = "OPTIMAL"
        elif agg.overall >= 0.7 and dup < 0.15:
            rec = "GOOD"
        elif dup >= 0.30:
            rec = "HIGH_DUPLICATION"
        elif agg.overall < 0.5:
            rec = "POOR_QUALITY"
        else:
            rec = "OK"

        rows.append(AblationRow(
            max_tokens=max_tokens,
            chunk_count=len(chunks),
            avg_hope=round(agg.overall, 3),
            hope_grade=agg.grade(),
            duplicate_rate=round(dup, 3),
            recommendation=rec,
        ))
    return rows


def optimal_max_tokens(model_context_window: int) -> int:
    """
    Recommend max_tokens for a model given its context window.
    Targets 512-1024 range (research sweet spot), capped at 40% of context.
    """
    target = min(int(model_context_window * 0.40), 1024)
    return max(target, 512)


def efficiency_report(analytics_db: Path) -> list[EfficiencyGroup]:
    """
    Read ingest_analytics, group by chunk_count, return quality per group.
    Shows whether multi-chunk ingests produce worse pages than single-chunk ones.
    """
    try:
        with sqlite3.connect(analytics_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT
                    chunk_count,
                    COUNT(*)                    AS n,
                    AVG(concepts_extracted)     AS avg_concepts,
                    AVG(avg_page_chars)         AS avg_page_chars,
                    AVG(avg_wikilinks)          AS avg_wikilinks,
                    AVG(COALESCE(idea_duplicate_rate, 0)) AS avg_dup_rate
                FROM ingest_analytics
                GROUP BY chunk_count
                ORDER BY chunk_count
            """).fetchall()
            return [
                EfficiencyGroup(
                    chunk_count=int(r["chunk_count"] or 1),
                    n_ingests=int(r["n"]),
                    avg_concepts=round(float(r["avg_concepts"] or 0), 2),
                    avg_page_chars=round(float(r["avg_page_chars"] or 0), 0),
                    avg_wikilinks=round(float(r["avg_wikilinks"] or 0), 2),
                    avg_duplicate_rate=round(float(r["avg_dup_rate"] or 0), 3),
                )
                for r in rows
            ]
    except Exception:
        return []
