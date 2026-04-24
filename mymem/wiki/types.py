"""
Core data types for the wiki layer.

All types are frozen dataclasses — immutable by design.
The LLM pipeline always creates new objects; it never mutates existing ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Tag domain taxonomy
# ---------------------------------------------------------------------------

class TagDomain(str, Enum):
    SPIRITUAL = "spiritual"
    TECH      = "tech"
    FINANCE   = "finance"
    HEALTH    = "health"
    REMINDER  = "reminder"
    RESEARCH  = "research"
    PERSONAL  = "personal"
    CREATIVE  = "creative"
    BUSINESS  = "business"
    MISC      = "misc"

    @classmethod
    def values(cls) -> list[str]:
        return [d.value for d in cls]


# ---------------------------------------------------------------------------
# Wiki page
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


@dataclass(frozen=True)
class WikiPage:
    """
    Represents a single wiki page on disk.

    `path` is the absolute or project-relative path to the .md file.
    `body` is the full markdown content (excluding frontmatter).
    """

    title:   str
    body:    str
    path:    Path
    tags:    tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    domain:  TagDomain       = TagDomain.MISC
    created: date            = field(default_factory=date.today)
    updated: date            = field(default_factory=date.today)

    def __post_init__(self) -> None:
        # Coerce list → tuple so the dataclass stays hashable/frozen
        object.__setattr__(self, "tags",    tuple(self.tags))
        object.__setattr__(self, "sources", tuple(self.sources))

    def wikilinks(self) -> list[str]:
        """Extract all [[Target]] link targets from the body."""
        return _WIKILINK_RE.findall(self.body)

    @property
    def slug(self) -> str:
        """File stem derived from title."""
        return self.title.lower().replace(" ", "-")

    def with_updated(self, **changes: object) -> "WikiPage":
        """Return a new WikiPage with fields replaced — never mutates self."""
        current = {
            "title":   self.title,
            "body":    self.body,
            "path":    self.path,
            "tags":    list(self.tags),
            "sources": list(self.sources),
            "domain":  self.domain,
            "created": self.created,
            "updated": date.today(),
        }
        current.update(changes)
        return WikiPage(**current)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Index entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndexEntry:
    """One line in index.md representing a wiki page."""

    title:        str
    path:         Path
    summary:      str
    category:     str           = "misc"
    source_count: int           = 0
    domain:       TagDomain     = TagDomain.MISC
    tags:         tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", tuple(self.tags))


# ---------------------------------------------------------------------------
# Log entry
# ---------------------------------------------------------------------------

class LogOperation(str, Enum):
    INGEST     = "ingest"
    QUERY      = "query"
    LINT       = "lint"
    INTROSPECT = "introspect"


@dataclass(frozen=True)
class LogEntry:
    """One entry in log.md."""

    operation:      LogOperation
    description:    str
    affected_pages: tuple[str, ...] = field(default_factory=tuple)
    timestamp:      datetime        = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "affected_pages", tuple(self.affected_pages))

    def header(self) -> str:
        """
        Produces the parseable header line.

        Format: ## [YYYY-MM-DD HH:MM] operation | description
        grep "^## [" log.md  →  lists all entries
        """
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M")
        return f"## [{ts}] {self.operation.value} | {self.description}"
