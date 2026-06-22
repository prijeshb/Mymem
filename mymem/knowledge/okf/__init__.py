"""
Open Knowledge Format (OKF v0.1) interop — export the wiki to / import it from a
spec-conformant OKF bundle (ADR-016).

OKF is Google Cloud's open spec for the "LLM wiki" pattern: a directory of markdown
files with YAML frontmatter, one file per concept, markdown links forming a graph.
MyMem's wiki already matches it closely, so this is an adapter, not a storage change.

  _spec   — OKF constants + conformance predicate
  _map    — frontmatter field mapping (domain<->type, dates<->timestamp, extensions)
  _links  — [[wikilink]] <-> OKF markdown-link conversion
  exporter   — wiki -> OKF bundle (export_okf)
  conformance — validate a bundle against the spec
"""
from __future__ import annotations
