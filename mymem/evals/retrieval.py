"""
Retrieval quality evaluation — BM25-based (no embedder required).

Metrics:
  precision@k — fraction of test cases where expected page is in top-k
  MRR         — mean reciprocal rank
  UDCG        — LLM-oriented positional discount (arxiv 2510.21440)

Test cases are generated automatically from the wiki corpus (self-supervised):
  - Query is derived from each page's title (slug → natural question)
  - Expected slug is the page itself
  - No hand-curated YAML needed — always reflects current wiki state

Optionally, a YAML override can be passed for pinned regression cases.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mymem.evals.metrics import bm25_score
from mymem.wiki.page import list_pages
from mymem.wiki.types import WikiPage

_DEFAULT_CASES_PATH = Path("tests/eval_cases/retrieval.yaml")


@dataclass
class RetrievalCase:
    query: str
    expected_slug: str
    domain: str = ""


@dataclass
class CaseResult:
    query: str
    expected_slug: str
    rank: int | None        # None = not in top-k
    top_k_slugs: list[str] = field(default_factory=list)

    @property
    def hit(self) -> bool:
        return self.rank is not None


@dataclass
class RetrievalReport:
    k: int
    total_cases: int
    hits: int
    precision_at_k: float
    mrr: float
    udcg: float
    mode: str = "self-supervised"   # "self-supervised" | "yaml"
    results: list[CaseResult] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.precision_at_k >= 0.7:
            return "PASS"
        if self.precision_at_k >= 0.5:
            return "WARN"
        return "FAIL"


def _udcg(ranks: list[int | None], k: int) -> float:
    """
    UDCG: uniform-discount DCG for LLM context.
    contribution of rank r = 1 / log2(r + 1).
    """
    if not ranks:
        return 0.0
    total = sum(1.0 / math.log2(r + 1) for r in ranks if r is not None)
    max_possible = sum(1.0 / math.log2(i + 2) for i in range(min(len(ranks), k)))
    return round(total / max_possible, 3) if max_possible > 0.0 else 0.0


def _slug_to_query(slug: str) -> str:
    """Convert a slug like 'transformer-architecture-fundamentals' → 'What is transformer architecture fundamentals?'"""
    phrase = slug.replace("-", " ")
    return f"What is {phrase}?"


def generate_self_supervised_cases(
    pages: list[WikiPage],
    n: int = 20,
    seed: int = 42,
) -> list[RetrievalCase]:
    """
    Sample N pages and derive a query from each title.
    The page itself is the ground-truth answer — no manual labelling needed.
    Excludes archived pages and stubs (body < 200 chars).
    """
    candidates = [
        p for p in pages
        if not p.archived and len(p.body or "") >= 200
    ]
    rng = random.Random(seed)
    sample = rng.sample(candidates, min(n, len(candidates)))
    return [
        RetrievalCase(
            query=_slug_to_query(p.slug),
            expected_slug=p.slug,
            domain=p.domain.value if p.domain else "",
        )
        for p in sample
    ]


_MAX_CASES_FILE_BYTES = 512 * 1024  # 512 KB — no legitimate cases file is larger


def load_cases(path: Path = _DEFAULT_CASES_PATH) -> list[RetrievalCase]:
    """Load pinned cases from YAML (optional override). Returns [] if file absent."""
    if not path.exists():
        return []
    if path.stat().st_size > _MAX_CASES_FILE_BYTES:
        raise ValueError(f"Cases file too large ({path.stat().st_size} bytes); max {_MAX_CASES_FILE_BYTES}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    if not isinstance(raw, list):
        raise ValueError(f"Cases YAML must be a list, got {type(raw).__name__}")
    return [
        RetrievalCase(
            query=str(item["query"]),
            expected_slug=str(item["expected_slug"]),
            domain=str(item.get("domain", "")),
        )
        for item in raw
        if "query" in item and "expected_slug" in item
    ]


def run_bm25_eval(
    cases: list[RetrievalCase],
    wiki_dir: Path,
    k: int = 5,
    mode: str = "self-supervised",
) -> RetrievalReport:
    """Evaluate retrieval using BM25 against wiki page bodies."""
    pages: list[WikiPage] = list_pages(wiki_dir)
    corpus: list[tuple[str, str]] = [(p.slug, p.body) for p in pages]

    results: list[CaseResult] = []
    for case in cases:
        scored = sorted(
            [(slug, bm25_score(case.query, body)) for slug, body in corpus],
            key=lambda x: x[1],
            reverse=True,
        )
        top_k = [slug for slug, _ in scored[:k]]
        rank: int | None = None
        for i, slug in enumerate(top_k, 1):
            if slug == case.expected_slug:
                rank = i
                break
        results.append(CaseResult(
            query=case.query,
            expected_slug=case.expected_slug,
            rank=rank,
            top_k_slugs=top_k,
        ))

    hits = sum(1 for r in results if r.hit)
    ranks = [r.rank for r in results]
    mrr = sum(1.0 / r for r in ranks if r is not None) / len(ranks) if ranks else 0.0

    return RetrievalReport(
        k=k,
        total_cases=len(cases),
        hits=hits,
        precision_at_k=round(hits / len(cases), 3) if cases else 0.0,
        mrr=round(mrr, 3),
        udcg=_udcg(ranks, k),
        mode=mode,
        results=results,
    )
