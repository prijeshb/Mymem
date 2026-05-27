"""
4-factor confidence scoring + lifecycle state machine for wiki pages.

Based on Karpathy's LLM-wiki pattern.

Lifecycle: DRAFT → REVIEWED → VERIFIED → STALE → ARCHIVED
Confidence factors:
  source_count     — how many sources back this page (>= 3 = full credit)
  recency          — exponential decay since last update (half-life ~46 days)
  cross_references — wikilink count (>= 5 = full credit)
  tag_coverage     — has non-misc domain + at least one tag
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

from mymem.wiki.types import TagDomain, WikiPage


class LifecycleState(str, Enum):
    DRAFT    = "draft"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    STALE    = "stale"
    ARCHIVED = "archived"


@dataclass
class ConfidenceScore:
    source_count: float
    recency: float
    cross_references: float
    tag_coverage: float
    overall: float
    state: LifecycleState

    def grade(self) -> str:
        if self.overall >= 0.75:
            return "VERIFIED"
        if self.overall >= 0.5:
            return "REVIEWED"
        return "DRAFT"


def score_confidence(page: WikiPage, stale_days: int = 90) -> ConfidenceScore:
    src_score = min(len(page.sources) / 3.0, 1.0)

    days_old = (date.today() - page.updated).days
    recency = math.exp(-0.015 * max(days_old, 0))  # half-life ≈ 46 days

    links = len(page.wikilinks())
    cross_ref = min(links / 5.0, 1.0)

    tag_ok = (
        1.0
        if page.tags and page.domain != TagDomain.MISC
        else (0.5 if page.tags or page.domain != TagDomain.MISC else 0.1)
    )

    overall = (src_score + recency + cross_ref + tag_ok) / 4.0

    if page.archived:
        state = LifecycleState.ARCHIVED
    elif days_old > stale_days:
        state = LifecycleState.STALE
    elif overall >= 0.75:
        state = LifecycleState.VERIFIED
    elif overall >= 0.5:
        state = LifecycleState.REVIEWED
    else:
        state = LifecycleState.DRAFT

    return ConfidenceScore(
        source_count=round(src_score, 2),
        recency=round(recency, 2),
        cross_references=round(cross_ref, 2),
        tag_coverage=round(tag_ok, 2),
        overall=round(overall, 2),
        state=state,
    )
