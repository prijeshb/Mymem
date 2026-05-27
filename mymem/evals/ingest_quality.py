"""
Wiki page quality scoring — richness, completeness, structural integrity.

Richness score (0–10):
  body_chars / 500     → up to 4 pts  (caps at 2000 chars)
  wikilinks * 0.5      → up to 3 pts  (caps at 6 links)
  sections * 0.5       → up to 2 pts  (caps at 4 headings)
  tags * 0.25          → up to 1 pt   (caps at 4 tags)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mymem.wiki.page import list_pages
from mymem.wiki.types import WikiPage

_HEADING_RE = re.compile(r"^#{1,3}\s", re.MULTILINE)


@dataclass
class PageScore:
    slug: str
    title: str
    domain: str
    body_chars: int
    wikilink_count: int
    section_count: int
    tag_count: int
    source_count: int
    richness_score: float
    is_stub: bool
    no_wikilinks: bool
    no_tags: bool
    no_sections: bool


@dataclass
class WikiQualityReport:
    total_pages: int
    mean_richness: float
    median_richness: float
    stub_count: int
    no_wikilinks_count: int
    no_tags_count: int
    pages: list[PageScore] = field(default_factory=list)

    @property
    def stub_rate(self) -> float:
        return self.stub_count / self.total_pages if self.total_pages else 0.0

    @property
    def no_wikilinks_rate(self) -> float:
        return self.no_wikilinks_count / self.total_pages if self.total_pages else 0.0


def score_page(page: WikiPage) -> PageScore:
    body_chars = len(page.body)
    wikilinks = len(page.wikilinks())
    sections = len(_HEADING_RE.findall(page.body))
    tags = len(page.tags)

    richness = (
        min(body_chars / 500.0, 4.0)
        + min(wikilinks * 0.5, 3.0)
        + min(sections * 0.5, 2.0)
        + min(tags * 0.25, 1.0)
    )

    return PageScore(
        slug=page.slug,
        title=page.title,
        domain=page.domain.value,
        body_chars=body_chars,
        wikilink_count=wikilinks,
        section_count=sections,
        tag_count=tags,
        source_count=len(page.sources),
        richness_score=round(richness, 2),
        is_stub=body_chars < 300,
        no_wikilinks=wikilinks == 0,
        no_tags=tags == 0,
        no_sections=sections == 0,
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def wiki_quality_report(wiki_dir: Path) -> WikiQualityReport:
    pages = list_pages(wiki_dir)
    scores = []
    for page in pages:
        try:
            scores.append(score_page(page))
        except Exception:
            continue

    if not scores:
        return WikiQualityReport(
            total_pages=0, mean_richness=0.0, median_richness=0.0,
            stub_count=0, no_wikilinks_count=0, no_tags_count=0,
        )

    richness_vals = [s.richness_score for s in scores]
    return WikiQualityReport(
        total_pages=len(scores),
        mean_richness=round(sum(richness_vals) / len(richness_vals), 2),
        median_richness=round(_median(richness_vals), 2),
        stub_count=sum(1 for s in scores if s.is_stub),
        no_wikilinks_count=sum(1 for s in scores if s.no_wikilinks),
        no_tags_count=sum(1 for s in scores if s.no_tags),
        pages=sorted(scores, key=lambda s: s.richness_score),
    )
