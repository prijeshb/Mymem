"""
OKF bundle conformance check (ADR-016).

A bundle conforms if every non-reserved `.md` file has parseable YAML frontmatter
containing a non-empty `type`. Everything else is guidance the spec says consumers
must tolerate, so it is not enforced here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mymem.knowledge.okf._spec import RESERVED_FILES, has_valid_type

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


@dataclass(frozen=True)
class ConformanceReport:
    conformant: bool
    total: int                          # non-reserved concept files checked
    violations: tuple[str, ...] = field(default_factory=tuple)  # rel paths failing


def _frontmatter(raw: str) -> dict[str, Any]:
    m = _FM_RE.match(raw)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def check_bundle(bundle_dir: Path) -> ConformanceReport:
    """Validate every non-reserved `.md` in *bundle_dir* (recursively)."""
    violations: list[str] = []
    total = 0
    for md in sorted(bundle_dir.rglob("*.md")):
        if md.name in RESERVED_FILES:
            continue
        total += 1
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        if not has_valid_type(fm):
            violations.append(md.relative_to(bundle_dir).as_posix())
    return ConformanceReport(
        conformant=not violations,
        total=total,
        violations=tuple(violations),
    )
