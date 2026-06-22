"""
OKF <-> WikiPage frontmatter mapping (ADR-016).

Export maps MyMem's frontmatter onto OKF's recommended fields and preserves
MyMem-specific fields as extension keys (OKF consumers must keep unknown keys),
so a MyMem-origin bundle round-trips losslessly. Import reverses it, defaulting
gracefully for bundles that didn't come from MyMem.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from mymem.wiki.tags import domain_from_str, normalize_tags
from mymem.wiki.types import TagDomain, WikiPage


def _to_iso8601(d: date) -> str:
    """Widen a date to an ISO-8601 datetime (midnight UTC) for OKF `timestamp`."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC).isoformat()


def _parse_timestamp(value: object) -> date:
    """Parse an OKF `timestamp` (ISO datetime or date) back to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
    return date.today()


def to_okf_frontmatter(page: WikiPage, *, description: str = "") -> dict[str, Any]:
    """Build OKF frontmatter for a wiki page.

    `type` (required) = the page domain. `description` is supplied by the caller
    (index summary / first paragraph). MyMem identity fields ride along as
    extension keys so the bundle re-imports losslessly.
    """
    fm: dict[str, Any] = {
        "type": page.domain.value,
        "title": page.title,
    }
    if description.strip():
        fm["description"] = description.strip()
    if page.sources:
        fm["resource"] = page.sources[0]
    fm["tags"] = list(page.tags)
    fm["timestamp"] = _to_iso8601(page.updated)

    # --- extension keys (preserved verbatim by OKF consumers) ---
    if page.id:
        fm["id"] = page.id
    fm["domain"] = page.domain.value
    if page.sources:
        fm["sources"] = list(page.sources)
    fm["created"] = page.created.isoformat()
    if page.archived:
        fm["archived"] = True
    return fm


def from_okf_frontmatter(fm: dict[str, Any]) -> dict[str, Any]:
    """Map OKF frontmatter back to WikiPage constructor kwargs.

    Prefers MyMem extension keys when present (lossless round-trip); otherwise
    derives sensible defaults. An unknown `type` becomes domain `misc` and is
    kept as a tag so the information isn't lost. Returns a kwargs dict (no path).
    """
    okf_type = str(fm.get("type", "")).strip()
    # domain: prefer the extension key, else the type if it's a known domain.
    domain_src = str(fm.get("domain") or okf_type or "misc")
    domain = domain_from_str(domain_src)

    raw_tags = [str(t) for t in fm["tags"]] if isinstance(fm.get("tags"), list) else []
    # Keep an unmapped type as a tag so it survives the round trip.
    if okf_type and domain == TagDomain.MISC and okf_type.lower() != "misc":
        raw_tags = [*raw_tags, okf_type]

    sources: list[str] = []
    if isinstance(fm.get("sources"), list):
        sources = [str(s) for s in fm["sources"]]
    elif fm.get("resource"):
        sources = [str(fm["resource"])]

    updated = _parse_timestamp(fm.get("timestamp"))
    created = date.today()
    if fm.get("created"):
        try:
            created = date.fromisoformat(str(fm["created"])[:10])
        except ValueError:
            created = updated

    return {
        "title": str(fm.get("title", "")).strip() or "Untitled",
        "tags": normalize_tags(raw_tags),
        "sources": sources,
        "domain": domain,
        "created": created,
        "updated": updated,
        "archived": bool(fm.get("archived", False)),
        "id": str(fm.get("id", "")),
    }
