"""
Index manager — maintains index.md as a catalog of all wiki pages.

index.md format:
    # Wiki Index

    ## Concepts
    - [Title](path.md) — summary (N sources)

    ## Papers
    - [Title](path.md) — summary (1 source)

Updated atomically on every ingest. The LLM reads this first when answering
queries to find relevant pages without scanning every file.
"""

from __future__ import annotations

import re
from pathlib import Path

from mymem.wiki.tags import domain_from_str
from mymem.wiki.types import IndexEntry, TagDomain


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_CATEGORY_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_ENTRY_RE    = re.compile(
    r"^\s*-\s+\[([^\]]+)\]\(([^)]+)\)\s+—\s+(.+?)(?:\s+\((\d+)\s+sources?\))?$"
)


def _parse_entries(text: str) -> list[IndexEntry]:
    entries: list[IndexEntry] = []
    current_category = "misc"

    for line in text.splitlines():
        cat_m = _CATEGORY_RE.match(line)
        if cat_m:
            current_category = cat_m.group(1).strip().lower()
            continue

        entry_m = _ENTRY_RE.match(line)
        if entry_m:
            title, path_str, summary, src_count = entry_m.groups()
            entries.append(
                IndexEntry(
                    title=title.strip(),
                    path=Path(path_str.strip()),
                    summary=summary.strip(),
                    category=current_category,
                    source_count=int(src_count) if src_count else 0,
                    domain=domain_from_str(current_category),
                )
            )

    return entries


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_entries(entries: list[IndexEntry]) -> str:
    # Group by category, preserving insertion order within each group
    groups: dict[str, list[IndexEntry]] = {}
    for e in entries:
        groups.setdefault(e.category, []).append(e)

    lines = ["# Wiki Index", ""]
    for category, group in sorted(groups.items()):
        lines.append(f"## {category.title()}")
        lines.append("")
        for e in group:
            src = f" ({e.source_count} {'source' if e.source_count == 1 else 'sources'})"
            lines.append(f"- [{e.title}]({e.path}) — {e.summary}{src}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# IndexManager
# ---------------------------------------------------------------------------

class IndexManager:
    """
    Load, update, and persist index.md.

    All mutating methods return nothing — they update the in-memory list
    and call save() immediately so the file is always in sync.
    """

    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self._entries: list[IndexEntry] = []
        if index_path.exists():
            self._entries = _parse_entries(index_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> list[IndexEntry]:
        """Return a snapshot of the current entries (immutable copies)."""
        return list(self._entries)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, entries: list[IndexEntry]) -> None:
        """Overwrite index.md with the given entries list."""
        self._entries = list(entries)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(_render_entries(self._entries), encoding="utf-8")

    def upsert(self, entry: IndexEntry) -> None:
        """
        Add a new entry or replace the existing one with the same title.
        Preserves order — updates happen in-place.
        """
        for i, existing in enumerate(self._entries):
            if existing.title == entry.title:
                self._entries[i] = entry
                self.save(self._entries)
                return
        self._entries.append(entry)
        self.save(self._entries)

    def remove(self, title: str) -> None:
        """Remove the entry with the given title. No-op if not found."""
        self._entries = [e for e in self._entries if e.title != title]
        self.save(self._entries)

    def find(self, title: str) -> IndexEntry | None:
        """Return the entry with the given title, or None."""
        for e in self._entries:
            if e.title == title:
                return e
        return None

    def by_domain(self, domain: TagDomain) -> list[IndexEntry]:
        """Return all entries matching a domain."""
        return [e for e in self._entries if e.domain == domain]

    def search(self, query: str, top_k: int = 5) -> list[IndexEntry]:
        """
        Simple keyword search over titles and summaries.
        Returns up to top_k entries ranked by match count.
        """
        q_words = query.lower().split()

        def score(e: IndexEntry) -> int:
            text = (e.title + " " + e.summary).lower()
            return sum(1 for w in q_words if w in text)

        ranked = sorted(self._entries, key=score, reverse=True)
        return [e for e in ranked if score(e) > 0][:top_k]
