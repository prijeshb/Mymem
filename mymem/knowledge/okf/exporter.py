"""
Export the wiki to a conformant OKF v0.1 bundle (ADR-016).

Pure transform over `list_pages()`: one concept file per page (frontmatter mapped,
`[[wikilinks]]` rewritten to OKF markdown links), plus a frontmatter-free `index.md`
and an OKF-format `log.md`. Returns an ExportReport and asserts conformance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from mymem.knowledge.okf._links import flatten_wikilinks, wikilinks_to_markdown
from mymem.knowledge.okf._map import to_okf_frontmatter
from mymem.knowledge.okf.conformance import check_bundle
from mymem.observability.logger import get_logger
from mymem.wiki.page import list_pages
from mymem.wiki.types import WikiPage

log = get_logger(__name__)

_HEADING_RE = re.compile(r"^#{1,6}\s")


@dataclass(frozen=True)
class ExportReport:
    out_dir: Path
    pages: int
    links_resolved: int
    links_broken: int
    conformant: bool


def _first_paragraph(body: str, *, limit: int = 200) -> str:
    """First non-heading, non-empty line of the body — used for `description`."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("*-> ").strip()
        if stripped and not _HEADING_RE.match(line.strip()):
            return flatten_wikilinks(stripped)[:limit]
    return ""


def _render_okf_file(fm: dict[str, object], body: str) -> str:
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=True)
    return f"---\n{fm_str}---\n\n{body}\n"


def _render_index(pages: list[WikiPage], descriptions: dict[str, str]) -> str:
    """OKF index.md — directory listing, NO frontmatter (spec)."""
    lines = ["# Wiki", ""]
    for p in sorted(pages, key=lambda x: x.title.lower()):
        desc = descriptions.get(p.slug, "")
        suffix = f" — {desc}" if desc else ""
        lines.append(f"- [{p.title}](/{p.slug}.md){suffix}")
    return "\n".join(lines) + "\n"


def _render_log(page_count: int) -> str:
    """OKF log.md — date-grouped, newest first, `**Action**: description`."""
    today = date.today().isoformat()
    return f"# Log\n\n## {today}\n**Exported**: {page_count} concepts from the MyMem wiki.\n"


def export_okf(wiki_dir: Path, out_dir: Path) -> ExportReport:
    """Write a conformant OKF bundle from the wiki at *wiki_dir* into *out_dir*."""
    pages = list_pages(wiki_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    title_to_slug = {p.title: p.slug for p in pages}

    def resolve(title: str) -> str | None:
        return title_to_slug.get(title)

    links_resolved = 0
    links_broken = 0
    descriptions: dict[str, str] = {}

    for page in pages:
        desc = _first_paragraph(page.body)
        descriptions[page.slug] = desc
        fm = to_okf_frontmatter(page, description=desc)
        new_body, unresolved = wikilinks_to_markdown(page.body, resolve)
        total = len(page.wikilinks())
        links_broken += len(unresolved)
        links_resolved += total - len(unresolved)
        (out_dir / f"{page.slug}.md").write_text(
            _render_okf_file(fm, new_body), encoding="utf-8"
        )

    (out_dir / "index.md").write_text(_render_index(pages, descriptions), encoding="utf-8")
    (out_dir / "log.md").write_text(_render_log(len(pages)), encoding="utf-8")

    report = check_bundle(out_dir)
    log.info(
        "OKF export complete",
        pages=len(pages), resolved=links_resolved, broken=links_broken,
        conformant=report.conformant,
    )
    return ExportReport(
        out_dir=out_dir,
        pages=len(pages),
        links_resolved=links_resolved,
        links_broken=links_broken,
        conformant=report.conformant,
    )
