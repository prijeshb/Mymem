"""Request context for the MCP handlers (ADR-017).

A small immutable bundle of wiki paths + the model router, so every handler is a
pure function of (context, args) — trivially testable without global state.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing the provider stack at module load
    from mymem.config import Settings
    from mymem.pipeline.router import ModelRouter


@dataclass(frozen=True)
class WikiContext:
    wiki_dir: Path
    index_path: Path
    log_path: Path
    graph_db: Path
    rag_db: Path
    router: ModelRouter | None = None
    redact_pii: bool = False   # redact PII in served content (ADR-018, redact-on-serve)


def context_from_settings(
    settings: Settings, *, router: ModelRouter | None = None
) -> WikiContext:
    """Derive a WikiContext from app Settings (mirrors cli._paths)."""
    wiki_dir = Path(settings.paths.wiki)
    db_parent = Path(settings.paths.db).parent
    return WikiContext(
        wiki_dir=wiki_dir,
        index_path=wiki_dir / "index.md",
        log_path=wiki_dir / "log.md",
        graph_db=db_parent / "graph.db",
        rag_db=db_parent / "rag.db",
        router=router,
        redact_pii=settings.security.pii != "off",
    )
