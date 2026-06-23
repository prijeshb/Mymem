"""Wire payloads returned by the MCP tools/resources (ADR-017).

Frozen dataclasses with `as_dict()` so the server layer can hand plain JSON-able
dicts to FastMCP regardless of its serializer. `ConceptPayload` is an OKF v0.1
concept (frontmatter + body) — the same shape `mymem export okf` emits (ADR-016).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConceptStub:
    """A lightweight search/list result — never carries the page body."""

    title: str
    slug: str
    domain: str
    description: str
    score: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "slug": self.slug,
            "domain": self.domain,
            "description": self.description,
            "score": self.score,
        }


@dataclass(frozen=True)
class ConceptPayload:
    """A full page as an OKF concept (frontmatter + body, links as OKF md links)."""

    uri: str
    frontmatter: dict[str, object]
    body: str

    def as_dict(self) -> dict[str, object]:
        return {"uri": self.uri, "frontmatter": dict(self.frontmatter), "body": self.body}


@dataclass(frozen=True)
class AskResult:
    """A synthesized answer with wiki-page citations."""

    question: str
    answer: str
    citations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"question": self.question, "answer": self.answer, "citations": list(self.citations)}


@dataclass(frozen=True)
class GapItem:
    """A referenced-but-unwritten concept (ADR-008 D12), surfaced to peers."""

    concept: str
    inbound_refs: int

    def as_dict(self) -> dict[str, object]:
        return {"concept": self.concept, "inbound_refs": self.inbound_refs}
