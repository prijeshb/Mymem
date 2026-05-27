"""
HOPE — Holistic Passage Evaluation (arxiv 2505.02171).

Five chunk-level metrics, all computable without an LLM:
  RC  — References Completeness: unresolved pronouns / dangling references
  ICC — Intrachunk Cohesion: average pairwise sentence similarity
  DCC — Discourse Coherence: chunk starts as a self-contained thought
  BI  — Block Integrity: chunk ends at a sentence boundary
  SC  — Size Compliance: chunk is within acceptable token bounds
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from mymem.evals.metrics import tfidf_cosine, tokenize

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_DANGLING = frozenset(
    "he she it they this these those which such who whom whose".split()
)
_CONTINUATION = frozenset(
    "and but however therefore thus hence moreover furthermore nevertheless".split()
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if len(s.strip()) > 15]


@dataclass
class HopeScore:
    rc: float   # References Completeness   [0-1]
    icc: float  # Intrachunk Cohesion       [0-1]
    dcc: float  # Discourse Coherence       [0-1]
    bi: float   # Block Integrity           [0-1]
    sc: float   # Size Compliance           [0-1]

    @property
    def overall(self) -> float:
        return (self.rc + self.icc + self.dcc + self.bi + self.sc) / 5.0

    def grade(self) -> str:
        s = self.overall
        if s >= 0.75:
            return "PASS"
        if s >= 0.55:
            return "WARN"
        return "FAIL"


def _rc(chunk: str) -> float:
    """References Completeness: penalise sentences starting with unresolved pronouns."""
    sents = _sentences(chunk)
    if len(sents) < 2:
        return 1.0
    interior = sents[1:]
    unresolved = sum(
        1 for s in interior
        if tokenize(s)[:1] and tokenize(s)[0] in _DANGLING
    )
    return 1.0 - unresolved / len(interior)


def _icc(chunk: str) -> float:
    """Intrachunk Cohesion: mean pairwise sentence TF-IDF cosine similarity."""
    sents = _sentences(chunk)
    if len(sents) < 2:
        return 1.0
    sims: list[float] = []
    for i, a in enumerate(sents):
        for b in sents[i + 1 :]:
            sims.append(tfidf_cosine(a, b))
    return sum(sims) / len(sims) if sims else 1.0


def _dcc(chunk: str) -> float:
    """Discourse Coherence: chunk starts as a self-contained thought."""
    first = chunk.lstrip().split("\n")[0].strip()
    if not first:
        return 0.0
    first_tokens = tokenize(first)
    if not first_tokens:
        return 0.5
    if first_tokens[0] in _CONTINUATION or first_tokens[0] in _DANGLING:
        return 0.2
    return 1.0 if first[0].isupper() else 0.6


def _bi(chunk: str) -> float:
    """Block Integrity: chunk ends at a complete sentence boundary."""
    stripped = chunk.rstrip()
    if not stripped:
        return 0.0
    if stripped[-1] in ".!?":
        return 1.0
    if stripped.endswith(("\n", ":")):
        return 0.7
    return 0.2


def _sc(chunk: str, min_tokens: int = 30, max_tokens: int = 1024) -> float:
    """Size Compliance: chunk is within [min_tokens, max_tokens]."""
    n = len(tokenize(chunk))
    if n < min_tokens:
        return max(0.0, n / min_tokens)
    if n > max_tokens:
        return max(0.0, 1.0 - (n - max_tokens) / max_tokens)
    return 1.0


def score_chunk(chunk: str, max_tokens: int = 1024) -> HopeScore:
    """Compute all five HOPE metrics for a single chunk."""
    return HopeScore(
        rc=round(_rc(chunk), 3),
        icc=round(_icc(chunk), 3),
        dcc=round(_dcc(chunk), 3),
        bi=round(_bi(chunk), 3),
        sc=round(_sc(chunk, max_tokens=max_tokens), 3),
    )


def score_chunks(chunks: list[str], max_tokens: int = 1024) -> list[HopeScore]:
    return [score_chunk(c, max_tokens=max_tokens) for c in chunks]


def aggregate_hope(scores: list[HopeScore]) -> HopeScore:
    """Mean HOPE score across a list of chunks."""
    if not scores:
        return HopeScore(0.0, 0.0, 0.0, 0.0, 0.0)
    n = len(scores)
    return HopeScore(
        rc=round(sum(s.rc for s in scores) / n, 3),
        icc=round(sum(s.icc for s in scores) / n, 3),
        dcc=round(sum(s.dcc for s in scores) / n, 3),
        bi=round(sum(s.bi for s in scores) / n, 3),
        sc=round(sum(s.sc for s in scores) / n, 3),
    )
