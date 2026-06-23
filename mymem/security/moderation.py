"""Lexicon-based adult/toxicity moderation (ADR-018).

A dependency-free heuristic: count matches of curated adult/toxicity indicator terms
and derive a score + confidence. This is a pragmatic v1 (high precision on explicit
content, not a real classifier). The clean upgrade path is a local model (e.g.
detoxify) behind the same ``classify_content`` interface — see ADR-018. The built-in
lexicon is intentionally compact and clinical; extend banned topics via the
``security.denylist_terms`` denylist rather than bloating this list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Compact, representative seed lexicons (word-boundary, case-insensitive).
_ADULT_TERMS: frozenset[str] = frozenset({
    "porn", "pornographic", "xxx", "nsfw", "explicit sex", "hardcore",
    "nude", "nudes", "erotic", "erotica", "fetish",
})
_TOXIC_TERMS: frozenset[str] = frozenset({
    "kill yourself", "kys", "i hate you", "go die",
    "slur", "racist", "bigot", "nazi",
})
# Terms that are severe enough that a single hit is high-confidence.
_SEVERE: frozenset[str] = frozenset({"porn", "pornographic", "xxx", "kill yourself", "kys"})

_HIGH_CONF_HITS = 3  # this many indicators (any category) => high confidence


def _matcher(terms: frozenset[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


_ADULT_RE = _matcher(_ADULT_TERMS)
_TOXIC_RE = _matcher(_TOXIC_TERMS)
_SEVERE_RE = _matcher(_SEVERE)


@dataclass(frozen=True)
class ModerationResult:
    flagged: bool
    score: float                              # 0.0 – 1.0
    categories: tuple[str, ...] = field(default_factory=tuple)
    high_confidence: bool = False


def classify_content(text: str) -> ModerationResult:
    """Heuristically score *text* for adult/toxic content."""
    adult = {m.group(0).lower() for m in _ADULT_RE.finditer(text)}
    toxic = {m.group(0).lower() for m in _TOXIC_RE.finditer(text)}
    total = len(adult) + len(toxic)
    if total == 0:
        return ModerationResult(flagged=False, score=0.0)

    categories: list[str] = []
    if adult:
        categories.append("adult")
    if toxic:
        categories.append("toxicity")

    severe = bool(_SEVERE_RE.search(text))
    high_confidence = severe or total >= _HIGH_CONF_HITS
    score = 1.0 if high_confidence else min(0.9, 0.3 + 0.2 * total)
    return ModerationResult(
        flagged=True,
        score=round(score, 2),
        categories=tuple(categories),
        high_confidence=high_confidence,
    )
