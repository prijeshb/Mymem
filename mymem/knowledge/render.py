"""
Claims → wiki markdown — render a page's claims as a "Knowledge Claims" section and sync
it into the page body (ADR-011 / ADR-015 D13).

Pure string transforms (no LLM, no I/O) so the compounding ledger — active claims plus the
bi-temporal SUPERSEDE audit trail — shows up directly in the durable markdown (and therefore
in Obsidian exports and the SPA). The section is delimited by HTML-comment markers so it can
be replaced idempotently on every re-ingest without disturbing the surrounding prose.
"""
from __future__ import annotations

import re

from mymem.knowledge.claims import Claim

CLAIMS_START = "<!-- claims:start -->"
CLAIMS_END = "<!-- claims:end -->"

_SECTION_RE = re.compile(
    rf"\s*{re.escape(CLAIMS_START)}.*?{re.escape(CLAIMS_END)}", re.DOTALL
)


def _flatten(text: str) -> str:
    """Collapse whitespace so a claim renders as one clean bullet."""
    return " ".join(text.split())


def render_claims_section(claims: list[Claim]) -> str:
    """Render the marked Knowledge Claims block, or "" when there are no claims.

    Active claims (valid_to is None) are listed with their confidence; retired claims are
    listed struck-through under a Superseded subsection with the date they were retired.
    """
    if not claims:
        return ""

    active = [c for c in claims if c.valid_to is None]
    superseded = [c for c in claims if c.valid_to is not None]

    lines = [CLAIMS_START, "## Knowledge Claims", ""]
    lines += [f"- {_flatten(c.text)} (conf {c.confidence:.1f})" for c in active]

    if superseded:
        lines += ["", "### Superseded", ""]
        lines += [
            f"- ~~{_flatten(c.text)}~~ (retired {c.valid_to})" for c in superseded
        ]

    lines.append(CLAIMS_END)
    return "\n".join(lines)


def _dedup_preserving_order(items: list[str]) -> list[str]:
    """Keep the first occurrence of each item, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def render_page_body(
    title: str,
    claims: list[Claim],
    *,
    see_also: list[str] | None = None,
) -> str:
    """Render a complete wiki page body **from** its claims (ADR-015 D20 / D11 end-state).

    The page becomes a deterministic view of the compounding ledger rather than re-compiled
    LLM prose: an `# {title}` heading, active claims as confidence-tagged bullets, the
    struck-through SUPERSEDE trail, and a preserved `## See Also` wikilink block so the
    knowledge graph survives the switch.

    Returns "" when there are no **active** claims — the caller keeps the existing prose,
    so a down embedder or a superseded-only page is never wiped to an empty body.
    """
    active = [c for c in claims if c.valid_to is None]
    if not active:
        return ""
    superseded = [c for c in claims if c.valid_to is not None]

    lines = [f"# {title}", ""]
    lines += [f"- {_flatten(c.text)} (conf {c.confidence:.1f})" for c in active]

    if superseded:
        lines += ["", "### Superseded", ""]
        lines += [
            f"- ~~{_flatten(c.text)}~~ (retired {c.valid_to})" for c in superseded
        ]

    links = _dedup_preserving_order(see_also or [])
    if links:
        lines += ["", "## See Also", ""]
        lines += [f"- [[{link}]]" for link in links]

    return "\n".join(lines)


def sync_claims_section(body: str, claims: list[Claim]) -> str:
    """Return `body` with its Knowledge Claims section replaced by the one for `claims`.

    Idempotent: removes any existing marked section first, then appends the freshly
    rendered one (or nothing, when there are no claims). Surrounding prose is preserved.
    """
    prose = _SECTION_RE.sub("", body).rstrip()
    section = render_claims_section(claims)
    if not section:
        return prose
    return f"{prose}\n\n{section}" if prose else section
