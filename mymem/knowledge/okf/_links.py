"""
Link conversion between MyMem wikilinks and OKF markdown links (ADR-016).

Export: `[[Concept Title]]` -> `[Concept Title](/concept-title.md)` (absolute-from-
root, the OKF-recommended form). Import: `[text](/path/to/file.md)` -> `[[text]]`.
OKF tolerates broken links, so an unresolved wikilink still emits a link (to its
slugified path) and is reported, never dropped.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from mymem.wiki.types import slugify

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
# Markdown link whose target is a local .md file (optionally absolute-from-root).
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((/?[^)]+?\.md)\)")


def wikilinks_to_markdown(
    body: str, resolve: Callable[[str], str | None]
) -> tuple[str, list[str]]:
    """Rewrite `[[Title]]` to `[Title](/slug.md)`.

    `resolve(title)` returns the slug of the matching page, or None if no page
    matches (a broken link). Broken links still emit `/slugify(title).md` (OKF
    consumers tolerate them) and are collected in the returned unresolved list.
    """
    unresolved: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        title = m.group(1).strip()
        slug = resolve(title)
        if slug is None:
            unresolved.append(title)
            slug = slugify(title)
        return f"[{title}](/{slug}.md)"

    return _WIKILINK_RE.sub(_repl, body), unresolved


def flatten_wikilinks(text: str) -> str:
    """Replace `[[Title]]` with plain `Title` — for plain-text fields (e.g. OKF
    `description`), which should not carry link syntax. Leaves all other text intact.
    """
    return _WIKILINK_RE.sub(lambda m: m.group(1).strip(), text)


def markdown_links_to_wikilinks(body: str) -> str:
    """Rewrite local `[text](/path/file.md)` markdown links back to `[[text]]`.

    Only links targeting a `.md` file are converted; external/http links and
    non-md targets are left untouched.
    """

    def _repl(m: re.Match[str]) -> str:
        text = m.group(1).strip()
        return f"[[{text}]]"

    return _MD_LINK_RE.sub(_repl, body)
