"""
Page identity resolution + migration (ADR-013).

A page's stable identity is its ULID `id` (see `mint_id` / `WikiPage.id`). The
slug and title are mutable display/addressing layered on top. This module builds
the *derived* `title | slug → id` index used to answer "which existing page is
this?" and the idempotent backfill that mints ids for pre-ADR-013 pages.

Frontmatter is the source of truth; the index here is rebuilt from it on demand
(ADR-013 D3). Exact, normalization-insensitive matching only — fuzzy/LLM-backed
resolution and `aliases:` are added when ADR-011 consumes identity (ADR-013 D5).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from mymem.observability.logger import get_logger
from mymem.wiki.page import list_pages, write_page
from mymem.wiki.types import mint_id, slugify

log = get_logger(__name__)


@dataclass(frozen=True)
class BackfillReport:
    """Result of an id backfill pass."""
    total_pages: int
    minted: int
    already_had: int


def _norm(text: str) -> str:
    """Normalize a title/slug to an index key. Reuses slugify so that
    'Self Attention', 'self-attention', and '  SELF  attention ' all collide."""
    return slugify(text)


def build_page_id_index(wiki_dir: Path) -> dict[str, str]:
    """
    Build the derived `normalized(title|slug) → id` map from page frontmatter.

    Pages without an id (legacy / not yet backfilled) are skipped — they have no
    stable identity to resolve to yet. On a normalized-key collision the
    last-listed page wins; disambiguation is deferred (ADR-013 D5).
    """
    index: dict[str, str] = {}
    for page in list_pages(wiki_dir, include_archived=True):
        if not page.id:
            continue
        index[_norm(page.title)] = page.id
        index[_norm(page.path.stem)] = page.id
    return index


def resolve_to_id(index: dict[str, str], text: str) -> str | None:
    """Resolve a title or slug to a stable page id via the derived index."""
    return index.get(_norm(text))


def backfill_page_ids(wiki_dir: Path) -> BackfillReport:
    """
    Mint a stable id for every wiki page that lacks one (ADR-013 migration).

    Idempotent and resumable: pages that already have an id are left untouched,
    so re-running only touches the remainder. Mirrors `mymem graph backfill`.
    """
    pages = list_pages(wiki_dir, include_archived=True)
    minted = 0
    for page in pages:
        if page.id:
            continue
        # Adding an id is not a content edit — keep the page's real `updated` date.
        write_page(dataclasses.replace(page, id=mint_id()), stamp_updated=False)
        minted += 1
    log.info("Page id backfill complete", pages=len(pages), minted=minted)
    return BackfillReport(
        total_pages=len(pages),
        minted=minted,
        already_had=len(pages) - minted,
    )
