"""
Ingest pipeline — raw source → wiki pages via LLM (orchestrator).

Flow:
    1. Security scan (block on HIGH-severity secrets)
    2. Read source text (file or URL)
    3. Extract ideas — Map/Merge/Verify (`ingest_extract`)
    4. Compile + write a wiki page per idea
    5. Compound propositions into the claims ledger + sync wiki sections (`ingest_claims`)
    6. index.md / log.md / curiosity / analytics
    7. Fire-and-forget background work — RAG, graph, evals (`ingest_rag`, `ingest_background`)

This module is the thin orchestrator. The heavy lifting lives in focused siblings:
  ingest_extract.py    — Map/Merge/Verify idea extraction + span grounding
  ingest_rag.py        — RAG indexing helpers
  ingest_claims.py     — claims persistence + wiki "Knowledge Claims" sync
  ingest_background.py — graph extraction + background evals

Names from those modules are re-exported here (see __all__) so existing imports and
test monkeypatch targets keep working.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from mymem.observability.logger import get_logger, set_run_id
from mymem.pipeline.ingest_background import (  # noqa: F401
    _build_reference_llm,
    _eval_decision_agreement_background,
    _eval_extraction_background,
    _graph_extract_background,
)
from mymem.pipeline.ingest_claims import (  # noqa: F401
    _build_claim_embedder,
    _naive_persist,
    _persist_claims,
    _sync_claims_sections,
)

# Extraction pipeline (re-exported for callers/tests)
from mymem.pipeline.ingest_extract import (  # noqa: F401
    _COMPILE_SYSTEM,
    _EXTRACT_SYSTEM,
    IdeaSchema,
    _compile_prompt,
    _extract_chunk_ideas,
    _extract_ideas_map_reduce,
    _extract_prompt,
    _ground_idea_spans,
    _ground_span,
    _idea_text,
    _merge_ideas,
    _parse_and_validate_ideas,
    _parse_ideas,
    _preserve_spans,
    _rank_extracted_ideas,
    _strip_frontmatter,
    _verify_ideas,
    splitter,
)
from mymem.pipeline.ingest_rag import (  # noqa: F401
    _rag_index_pdf,
    _rag_index_text,
    _rag_index_wiki,
)
from mymem.pipeline.readers import (
    _build_youtube_context,
    _extract_video_id,
    _fetch_youtube_metadata,
    _format_chapters,
    _format_duration,
    _html_to_text,
    _is_youtube_url,
    _read_youtube,
)
from mymem.pipeline.readers import (  # re-exported for callers/tests  # noqa: F401
    read_source as _read_source,
)
from mymem.pipeline.router import ModelRouter
from mymem.security.sanitize import sanitize_for_prompt
from mymem.security.scanner import has_high_severity_secret
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import read_page, slug_to_path, write_page
from mymem.wiki.tags import domain_from_str, normalize_tags
from mymem.wiki.types import (
    IndexEntry,
    LogEntry,
    LogOperation,
    TagDomain,
    WikiPage,
    mint_id,
)

if TYPE_CHECKING:
    from mymem.pipeline.compounding import AppliedDecision

log = get_logger(__name__)

__all__ = [
    "IngestResult",
    "ingest_source",
    # re-exported helpers (stable import surface for callers + tests)
    "IdeaSchema",
    "_read_source",
    "_is_youtube_url",
    "_html_to_text",
    "_extract_video_id",
    "_read_youtube",
    "_format_duration",
    "_format_chapters",
    "_build_youtube_context",
    "_fetch_youtube_metadata",
    "_extract_prompt",
    "_compile_prompt",
    "_parse_ideas",
    "_idea_text",
    "_rank_extracted_ideas",
    "_strip_frontmatter",
    "_parse_and_validate_ideas",
    "_ground_span",
    "_ground_idea_spans",
    "_preserve_spans",
    "_extract_chunk_ideas",
    "_merge_ideas",
    "_verify_ideas",
    "_extract_ideas_map_reduce",
    "_rag_index_pdf",
    "_rag_index_wiki",
    "_rag_index_text",
    "_persist_claims",
    "_sync_claims_sections",
    "_build_claim_embedder",
    "_naive_persist",
    "_graph_extract_background",
    "_eval_extraction_background",
    "_eval_decision_agreement_background",
    "_build_reference_llm",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class IngestResult:
    source_path:   str
    pages_written: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    chunk_count:   int = 1
    skipped:       bool = False
    skip_reason:   str = ""
    rag_only:      bool = False
    rag_chunks:    int = 0


# ---------------------------------------------------------------------------
# Core ingest function
# ---------------------------------------------------------------------------

async def ingest_source(
    source: str,
    *,
    wiki_dir: Path,
    index_path: Path,
    log_path: Path,
    router: ModelRouter,
    source_type: str = "article",
    tags: list[str] | None = None,
    domain: str = "",
    title_hint: str | None = None,
    max_concepts: int = 3,
    db_path: Path | None = None,
    body_from_claims: bool = False,
) -> IngestResult:
    """
    Ingest a source document into the wiki.

    Args:
        source:      File path or URL.
        wiki_dir:    Path to the wiki/ directory.
        index_path:  Path to index.md.
        log_path:    Path to log.md.
        router:      ModelRouter instance (inject a mocked one in tests).
        source_type: article | paper | repo | dataset | image
        tags:        Optional tag override list.
        domain:      Optional domain override string.
        title_hint:  Optional page title override.
        body_from_claims: Render each touched page's body FROM its claims instead of
                     appending a section (ADR-015 D20). Opt-in via pipeline config.
    """
    run_id = set_run_id()
    source_name = Path(source).name if not source.startswith("http") else source
    log.info("Ingest started", source=source_name, run_id=run_id,
             source_type=source_type, domain=domain, tags=tags)

    # 1. Read source
    log.debug("Reading source", source=source)
    source_text = await _read_source(source, source_type=source_type)
    log.info("Source read", source=source_name, chars=len(source_text))

    # 2. Security scan
    log.debug("Scanning for secrets", source=source_name)
    if has_high_severity_secret(source_text):
        log.warning("HIGH-severity secret detected — skipping", source=source_name)
        return IngestResult(
            source_path=source,
            skipped=True,
            skip_reason="HIGH-severity secret detected in source — ingestion blocked",
        )

    # 2b. Content safety (ADR-018) — PII redaction, denylist, adult/toxicity moderation.
    # Redacting the source before extraction also keeps PII out of generated pages.
    from mymem.security.content_safety import inspect_content

    try:
        from mymem.config import get_settings
        security_cfg = get_settings().security
    except Exception:  # never let config loading break ingestion
        from mymem.config import SecurityConfig
        security_cfg = SecurityConfig()
    safety = inspect_content(source_text, security_cfg)
    if safety.blocked:
        log.warning("Content safety blocked source", source=source_name,
                    reasons=list(safety.reasons))
        return IngestResult(
            source_path=source,
            skipped=True,
            skip_reason="Content safety blocked: " + "; ".join(safety.reasons),
        )
    if safety.reasons:
        log.info("Content safety applied", source=source_name, reasons=list(safety.reasons))
    source_text = safety.text

    # Sanitize source text before it enters any LLM prompt
    source_text, ingest_risk = sanitize_for_prompt(source_text)
    if ingest_risk.matched_patterns:
        log.warning(
            "Prompt injection patterns detected in source",
            source=source_name,
            risk=ingest_risk.level,
            patterns=ingest_risk.matched_patterns,
        )

    # 3a. Local PDFs — skip LLM extraction entirely; just RAG-index for search
    is_local_pdf = (
        not source.startswith(("http://", "https://"))
        and Path(source).suffix.lower() == ".pdf"
    )
    if is_local_pdf:
        rag_chunks = await _rag_index_pdf(source, db_path=db_path)
        log.info("PDF RAG-indexed (search only, no wiki extraction)",
                 source=source_name, chunks=rag_chunks)
        return IngestResult(
            source_path=source,
            rag_only=True,
            rag_chunks=rag_chunks,
        )

    # 3b. Extract ideas — Map / Merge / Verify pipeline
    log.info("Extracting ideas", source=source_name)
    selected_ideas = await _extract_ideas_map_reduce(
        source_text,
        source_name=source_name,
        source_type=source_type,
        router=router,
    )

    if not selected_ideas:
        log.warning("No ideas extracted — skipping", source=source_name)
        return IngestResult(
            source_path=source, skipped=True,
            skip_reason="LLM returned no extractable ideas",
        )

    log.info("Ideas extracted", source=source_name, total=len(selected_ideas))

    # 4. Compile each idea into a wiki page
    index_mgr  = IndexManager(index_path)
    wiki_log   = WikiLog(log_path)
    result     = IngestResult(source_path=source)
    inferred_domain = domain_from_str(domain) if domain else TagDomain.MISC
    extra_tags = normalize_tags(tags or [])
    # Atomic propositions persisted to claims.db after the loop (ADR-015 Phase 2),
    # anchored to each page's stable id so a later rename never orphans provenance.
    claim_records: list[tuple[str, str, str]] = []  # (page_id, text, source_span)
    touched_pages: list[tuple[Path, str]] = []      # (page_path, page_id) for claims-section sync

    for idx, idea in enumerate(selected_ideas, 1):
        idea_title   = str(idea.get("title", "Untitled"))
        idea_summary = str(idea.get("summary", ""))
        idea_tags    = normalize_tags(
            [str(t) for t in (idea.get("tags") or [])] + extra_tags
        )
        idea_domain = (
            inferred_domain
            if inferred_domain != TagDomain.MISC
            else domain_from_str(str(idea.get("domain", "misc")))
        )

        page_path = slug_to_path(wiki_dir, idea_title)
        is_update = page_path.exists()
        log.info(
            f"Compiling page {idx}/{len(selected_ideas)}",
            title=idea_title, domain=idea_domain.value,
            action="update" if is_update else "create",
        )

        # Compile the wiki page body via LLM
        page_body = await router.call(
            _compile_prompt(idea_title, idea_summary, source_text, idea_domain.value),
            task="compile",
            system=_COMPILE_SYSTEM,
        )
        log.debug("Page compiled", title=idea_title, chars=len(page_body))

        page_body = _strip_frontmatter(page_body)
        existing_created = date.today()
        existing_id = ""
        if is_update:
            try:
                existing = read_page(page_path)
                existing_created = existing.created
                existing_id = existing.id  # preserve stable identity across re-ingest (ADR-013)
            except Exception as exc:
                log.debug("Could not read existing page", page=str(page_path), error=str(exc))

        # Resolve the stable id up-front so claims can be keyed on it (write_page would
        # otherwise mint it internally, leaving it out of reach here).
        page_id = existing_id or mint_id()
        page = WikiPage(
            title=idea_title,
            body=page_body,
            path=page_path,
            tags=idea_tags,  # type: ignore[arg-type]
            sources=[source_name],
            domain=idea_domain,
            created=existing_created,
            id=page_id,
        )
        write_page(page)

        claim_text = (idea_summary or idea_title).strip()
        if claim_text:
            claim_records.append(
                (page_id, claim_text, str(idea.get("source_span", "")).strip())
            )
        touched_pages.append((page_path, page_id))
        log.debug("Page written", path=str(page_path))

        # Async best-effort wiki RAG indexing (fire-and-forget; never blocks ingest)
        if db_path:
            asyncio.ensure_future(_rag_index_wiki(page_path, db_path=db_path))

        # Update index
        index_mgr.upsert(IndexEntry(
            title=idea_title,
            path=page_path.relative_to(wiki_dir) if wiki_dir in page_path.parents else page_path,
            summary=idea_summary,
            category=idea_domain.value,
            source_count=1,
            domain=idea_domain,
            tags=tuple(idea_tags),
        ))

        if is_update:
            result.pages_updated.append(idea_title)
        else:
            result.pages_written.append(idea_title)

    # 4b. Compound atomic propositions into claims.db (ADR-015 Phase 3c). Best-effort:
    # knowledge recording must never fail an ingest.
    applied_decisions: list[AppliedDecision] = []
    if db_path:
        applied_decisions = await _persist_claims(
            db_path, source_name, claim_records, router=router
        )
        # 4c. Surface the resulting claims in the wiki — either as an appended section
        # (ADR-015 D13) or as the whole body when body_from_claims is on (D20 / D11).
        _sync_claims_sections(db_path, touched_pages, body_from_claims=body_from_claims)

    # 5. Log the ingest operation
    all_touched = result.pages_written + result.pages_updated
    wiki_log.append(LogEntry(
        operation=LogOperation.INGEST,
        description=source_name,
        affected_pages=tuple(all_touched),
    ))

    log.info(
        "Ingest complete", source=source_name,
        pages_written=len(result.pages_written),
        pages_updated=len(result.pages_updated),
        cost=f"${router.session_cost:.4f}",
    )

    # Record quality analytics for all ingests
    if db_path:
        _record_ingest_analytics(
            db_path=db_path,
            source_type=source_type,
            source=source,
            source_text=source_text,
            all_ideas=selected_ideas,
            result=result,
            wiki_dir=wiki_dir,
        )

    # Background extraction consensus eval (fire-and-forget, never blocks ingest)
    if db_path and selected_ideas:
        asyncio.ensure_future(
            _eval_extraction_background(
                source_name=source_name,
                source_type=source_type,
                source_text=source_text,
                pipeline_ideas=selected_ideas,
                router=router,
                db_path=db_path,
            )
        )

    # Background decision-agreement eval (ship gate, fire-and-forget — never blocks ingest)
    if db_path and applied_decisions:
        asyncio.ensure_future(
            _eval_decision_agreement_background(
                source_name=source_name,
                applied=applied_decisions,
                router=router,
                db_path=db_path,
            )
        )

    # Background entity graph extraction (fire-and-forget, never blocks ingest)
    if db_path and all_touched:
        asyncio.ensure_future(
            _graph_extract_background(
                source_name=source_name,
                source_text=source_text,
                page_ids=[pid for _, pid in touched_pages],
                router=router,
                db_path=db_path,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _record_ingest_analytics(
    *,
    db_path: Path,
    source_type: str,
    source: str,
    source_text: str,
    all_ideas: list[dict[str, object]],
    result: IngestResult,
    wiki_dir: Path,
) -> None:
    """Measure quality of generated pages and persist analytics record for all source types."""
    from mymem.evals.metrics import duplicate_rate
    from mymem.observability.ingest_analytics import record_ingest

    all_touched = result.pages_written + result.pages_updated
    page_chars: list[int] = []
    page_wikilinks: list[int] = []
    for title in all_touched:
        page_path = slug_to_path(wiki_dir, title)
        try:
            p = read_page(page_path)
            page_chars.append(len(p.body))
            page_wikilinks.append(len(p.wikilinks()))
        except Exception as exc:
            log.debug("Analytics: page unreadable", page=str(page_path), error=str(exc))

    avg_chars = sum(page_chars) / len(page_chars) if page_chars else 0.0
    avg_links = sum(page_wikilinks) / len(page_wikilinks) if page_wikilinks else 0.0

    # Measure duplicate ideas across chunks
    idea_summaries = [str(idea.get("summary", "")) for idea in all_ideas if idea.get("summary")]
    dup_rate = duplicate_rate(idea_summaries) if len(idea_summaries) >= 2 else 0.0

    is_youtube = source_type == "youtube" or _is_youtube_url(source)

    record_ingest(
        db_path,
        source_type=source_type,
        metadata_enriched=is_youtube and "[YouTube Video — ID:" in source_text,
        source_chars=len(source_text),
        concepts_extracted=len(all_ideas),
        pages_written=len(result.pages_written),
        pages_updated=len(result.pages_updated),
        avg_page_chars=avg_chars,
        avg_wikilinks=avg_links,
        chunk_count=result.chunk_count,
        idea_duplicate_rate=round(dup_rate, 3),
    )
