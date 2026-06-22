"""
Import an OKF v0.1 bundle into the wiki (ADR-016) — the direct inverse of export.

Each OKF concept file maps straight back to a WikiPage (frontmatter via
`from_okf_frontmatter`, OKF markdown links back to `[[wikilinks]]`), preserving the
MyMem `id` extension key so a MyMem-origin bundle round-trips identity-stable. This
is deliberately NOT routed through the LLM ingest pipeline: that would re-derive
content and lose identity, defeating the round-trip ship gate (PRD G4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mymem.knowledge.okf._links import markdown_links_to_wikilinks
from mymem.knowledge.okf._map import from_okf_frontmatter
from mymem.knowledge.okf._spec import RESERVED_FILES, has_valid_type
from mymem.observability.logger import get_logger
from mymem.wiki.page import write_page
from mymem.wiki.types import WikiPage, slugify

log = get_logger(__name__)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


@dataclass(frozen=True)
class ImportReport:
    concepts: int   # non-reserved .md files seen
    written: int    # pages written
    skipped: int    # files skipped (no valid type, or page exists w/o overwrite)


def _split(raw: str) -> tuple[dict[str, Any], str]:
    m = _FM_RE.match(raw)
    if not m:
        return {}, raw
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(fm, dict):
        return {}, raw
    return fm, raw[m.end():]


def import_okf(bundle_dir: Path, wiki_dir: Path, *, overwrite: bool = False) -> ImportReport:
    """Import every conformant concept file in *bundle_dir* into *wiki_dir*.

    Files without a valid `type` are skipped (not OKF concepts). Existing pages are
    skipped unless *overwrite* is True. Pages are written with stamp_updated=False so
    the bundle's `timestamp` is preserved rather than reset to today.
    """
    wiki_dir.mkdir(parents=True, exist_ok=True)
    concepts = 0
    written = 0
    skipped = 0

    for md in sorted(bundle_dir.rglob("*.md")):
        if md.name in RESERVED_FILES:
            continue
        fm, body = _split(md.read_text(encoding="utf-8"))
        if not has_valid_type(fm):
            skipped += 1
            continue
        concepts += 1

        kwargs = from_okf_frontmatter(fm)
        title = kwargs["title"]
        page_path = wiki_dir / f"{slugify(title)}.md"
        if page_path.exists() and not overwrite:
            skipped += 1
            continue

        page = WikiPage(
            title=title,
            body=markdown_links_to_wikilinks(body).strip("\n"),
            path=page_path,
            tags=kwargs["tags"],
            sources=kwargs["sources"],
            domain=kwargs["domain"],
            created=kwargs["created"],
            updated=kwargs["updated"],
            archived=kwargs["archived"],
            id=kwargs["id"],
        )
        write_page(page, stamp_updated=False)  # preserve the bundle's timestamp
        written += 1

    log.info(
        "OKF import complete",
        bundle=str(bundle_dir), concepts=concepts, written=written, skipped=skipped,
    )
    return ImportReport(concepts=concepts, written=written, skipped=skipped)
