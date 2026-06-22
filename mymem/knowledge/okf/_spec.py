"""
OKF v0.1 spec constants + conformance predicate (ADR-016).

The spec requires exactly one frontmatter field — `type` — and reserves two
filenames. Everything else is recommended/optional; consumers must preserve
unknown keys and tolerate broken links. This module is the single source of
truth for those rules.
"""
from __future__ import annotations

from typing import Any

# The only required frontmatter field.
REQUIRED_FIELD = "type"

# Recommended fields, in spec priority order.
RECOMMENDED_FIELDS = ("title", "description", "resource", "tags", "timestamp")

# Filenames with defined meaning — never used for concept documents.
RESERVED_FILES = ("index.md", "log.md")


def has_valid_type(frontmatter: dict[str, Any]) -> bool:
    """A concept document conforms iff it carries a non-empty string `type`."""
    value = frontmatter.get(REQUIRED_FIELD)
    return isinstance(value, str) and bool(value.strip())


def concept_id(rel_path: str) -> str:
    """OKF concept identity = the bundle-relative file path with `.md` removed.

    `tables/orders.md` -> `tables/orders`. Backslashes are normalized to `/`.
    """
    p = rel_path.replace("\\", "/")
    return p[:-3] if p.endswith(".md") else p
