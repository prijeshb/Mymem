"""Banned-term / topic denylist (ADR-018).

A simple, fast, case-insensitive, word-boundary matcher over a user-supplied list of
terms (from ``config.yaml`` ``security.denylist_terms``). Multi-word terms are matched
as phrases. Deliberately literal — no regex injection from user terms (they're escaped).
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=32)
def _compile(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    cleaned = [t.strip() for t in terms if t.strip()]
    if not cleaned:
        return None
    # Escape each term (literal match) and wrap in word boundaries; longest first
    # so multi-word phrases win over their substrings.
    alts = "|".join(re.escape(t) for t in sorted(cleaned, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


def check_denylist(text: str, terms: list[str]) -> list[str]:
    """Return the distinct denylisted terms found in *text* (original casing of the
    configured term), preserving config order. Empty list when nothing matches."""
    pattern = _compile(tuple(terms))
    if pattern is None:
        return []
    hits = {m.group(0).lower() for m in pattern.finditer(text)}
    if not hits:
        return []
    return [t for t in terms if t.strip() and t.strip().lower() in hits]
