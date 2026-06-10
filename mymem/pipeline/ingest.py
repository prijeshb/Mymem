"""
Ingest pipeline — raw source → wiki pages via LLM.

Flow:
    1. Security scan (block on HIGH-severity secrets)
    2. Read source text (file or URL)
    3. Router selects model; if too long → ChunkSplitter splits it
    4. LLM extracts key ideas per chunk
    5. LLM writes/updates wiki pages
    6. index.md updated
    7. curiosity event logged
    8. log.md appended
"""

from __future__ import annotations

import asyncio
from collections import Counter
import textwrap
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Awaitable

from pydantic import BaseModel, Field as PydanticField, ValidationError

from mymem.observability.logger import get_logger, set_run_id
from mymem.pipeline.readers import (
    read_source as _read_source,
    _is_youtube_url,
    _html_to_text,
    _extract_video_id,
    _read_youtube,
    _format_duration,
    _format_chapters,
    _build_youtube_context,
    _fetch_youtube_metadata,
    _YT_AVAILABLE,
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    trafilatura,
)
from mymem.pipeline.router import ModelRouter
from mymem.pipeline.splitter import ChunkSplitter, merge_prompt, merge_system_prompt

splitter = ChunkSplitter(max_tokens=1024)
from mymem.security.sanitize import sanitize_for_prompt
from mymem.security.scanner import has_high_severity_secret
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import read_page, slug_to_path, write_page
from mymem.wiki.tags import domain_from_str, normalize_tags
from mymem.wiki.types import IndexEntry, LogEntry, LogOperation, TagDomain, WikiPage

log = get_logger(__name__)


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
# Idea schema
# ---------------------------------------------------------------------------

class IdeaSchema(BaseModel):
    """Canonical shape for every extracted idea — pipeline and reference extractor."""
    title: str
    summary: str
    why_it_matters: str = ""
    evidence: list[str] = PydanticField(default_factory=list)
    chunk_id: int = 0
    importance: int = PydanticField(default=3, ge=1, le=5)
    main_thesis: bool = False
    tags: list[str] = PydanticField(default_factory=list)
    domain: str = "misc"


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

# New canonical extraction system prompt — no max_concepts ceiling.
_EXTRACT_SYSTEM = """\
You are a knowledge curator. Extract the globally important ideas from this source chunk.

Return only valid JSON array:
[
  {
    "title": "3-8 word searchable concept title",
    "summary": "2-3 sentence explanation grounded only in the source",
    "why_it_matters": "Why this is worth preserving in a personal wiki",
    "evidence": ["short source-grounded quote or paraphrase"],
    "chunk_id": 0,
    "importance": 3,
    "main_thesis": false,
    "tags": ["lowercase"],
    "domain": "tech|research|business|personal|creative|finance|health|spiritual|reminder|misc"
  }
]

Rules:
- Do not infer facts not present in the source.
- Prefer distinct concepts over overlapping variants.
- Include both central thesis and non-obvious supporting ideas.
- Do not include generic background knowledge unless the source uses it as a key idea.
"""

_MERGE_SYSTEM = """\
You are a knowledge curator merging extracted concepts from multiple document chunks.

You will receive a JSON array of candidate ideas, each with a recurrence_count showing
how many chunks mentioned this concept. Deduplicate by concept identity (not wording),
preserve the evidence from the best-scored duplicate, and return a clean final list.

Return only a valid JSON array using the same schema as the input.
"""

_VERIFY_SYSTEM = """\
You are reviewing an extraction for completeness.

You will receive the source text and the ideas already extracted from it.
List any important source ideas that are MISSING from the extraction.
Use the same JSON schema. If nothing is missing, return an empty JSON array [].
"""

_EXTRACT_SYSTEM_TMPL = """\
You are a knowledge curator. Given a source document, extract the key ideas,
concepts, and facts that are worth preserving in a personal wiki.
Output a JSON array of objects, each with:
  "title": short page title (3-6 words)
  "summary": 2-3 sentences covering the core insight, key facts or numbers, and why it matters
  "tags": list of lowercase tags
  "domain": one of spiritual|tech|finance|health|reminder|research|personal|creative|business|misc
Do not include more than {max_concepts} ideas. Output only valid JSON.
"""

_SOURCE_TYPE_HINTS: dict[str, str] = {
    "article":    "a written article or blog post",
    "paper":      "an academic or research paper",
    "repo":       "a code repository or technical project",
    "dataset":    "a data file or dataset",
    "image":      "an image or visual document",
    "youtube":    "a YouTube video (transcript with title, description, and chapter markers)",
    "podcast":    "a podcast episode or show notes",
    "tweet":      "a tweet or Twitter/X thread",
    "webpage":    "a general web page",
    "book":       "a book or long-form text",
    "newsletter": "an email newsletter",
    "note":       "a personal note or journal entry",
}

_COMPILE_SYSTEM = """\
You are a wiki author. Given source material and a concept to document,
write a wiki page in markdown with YAML frontmatter.

Frontmatter fields required: title, domain, tags, sources.
Do NOT include created or updated — those are set by the system.
Body: use ## headings, include [[wikilinks]] to related concepts.
Output only the markdown — no commentary.
"""


def _extract_prompt(
    source_text: str,
    source_name: str,
    source_type: str = "article",
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> str:
    hint = _SOURCE_TYPE_HINTS.get(source_type, f"a {source_type}")
    section_note = f" (part {chunk_index} of {total_chunks})" if total_chunks > 1 else ""
    header = f"Source: {source_name}{section_note}\nType: {hint}\n"
    return f"{header}\n---\n{source_text}\n---"


def _compile_prompt(idea_title: str, idea_summary: str, source_text: str, domain: str) -> str:
    preview = textwrap.shorten(source_text, width=6000, placeholder="...")
    return (
        f"Write a wiki page for: {idea_title}\n"
        f"Summary hint: {idea_summary}\n"
        f"Domain: {domain}\n\n"
        f"Source material:\n{preview}"
    )


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
        if is_update:
            try:
                existing_created = read_page(page_path).created
            except Exception:
                pass

        page = WikiPage(
            title=idea_title,
            body=page_body,
            path=page_path,
            tags=idea_tags,  # type: ignore[arg-type]
            sources=[source_name],
            domain=idea_domain,
            created=existing_created,
        )
        write_page(page)
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

    return result


async def _rag_index_pdf(source: str, *, db_path: Path | None) -> int:
    """Index a local PDF into the RAG vector store. Returns chunk count (0 on failure)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_pdf

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_pdf(
            Path(source),
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info("RAG index: already indexed", source=source, reason=rag_result.skip_reason)
        elif rag_result.ok:
            log.info("RAG index: complete", source=source, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source, error=rag_result.error)
        return rag_result.chunk_count if rag_result.ok else 0
    except Exception as exc:
        log.warning("RAG indexing raised unexpectedly", source=source, error=str(exc))
        return 0


async def _rag_index_wiki(page_path: Path, *, db_path: Path | None) -> None:
    """Index a wiki page into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_wiki_page

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        result = await ingest_wiki_page(
            page_path,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
            force=True,
        )
        if result.ok:
            log.info("Wiki RAG indexed", path=str(page_path), chunks=result.chunk_count)
        elif not result.skipped:
            log.warning("Wiki RAG index failed", path=str(page_path), error=result.error)
    except Exception as exc:
        log.warning("Wiki RAG indexing raised unexpectedly", path=str(page_path), error=str(exc))


async def _rag_index_text(source_name: str, text: str, *, db_path: Path | None) -> None:
    """Index raw text into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_text_chunks

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_text_chunks(
            text,
            source_id=source_name,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info("RAG index: already indexed", source=source_name, reason=rag_result.skip_reason)
        elif rag_result.ok:
            log.info("RAG index: complete", source=source_name, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source_name, error=rag_result.error)
    except Exception as exc:
        log.warning("RAG text indexing raised unexpectedly — continuing", source=source_name, error=str(exc))


def _record_ingest_analytics(
    *,
    db_path: Path,
    source_type: str,
    source: str,
    source_text: str,
    all_ideas: list[dict[str, object]],
    result: "IngestResult",
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
        except Exception:
            pass

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ideas(raw: str) -> list[dict[str, object]]:
    """Parse LLM JSON output into a list of idea dicts."""
    import json, re
    cleaned = raw.strip()
    # Strip <think>…</think> reasoning blocks (emitted by thinking models)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return [d for d in v if isinstance(d, dict)]
    except (json.JSONDecodeError, ValueError):
        log.debug("_parse_ideas: JSON parse failed", raw_preview=raw[:200])
    return []


def _idea_text(idea: dict[str, object]) -> str:
    return f"{idea.get('title', '')} {idea.get('summary', '')}".strip()


def _rank_extracted_ideas(
    ideas: list[dict[str, object]],
    *,
    max_concepts: int,
    duplicate_threshold: float = 0.55,
) -> list[dict[str, object]]:
    """
    Deduplicate and rank chunk-extracted ideas.

    Repeated ideas across chunks are evidence of document-wide coverage, so they
    rise above one-off ideas while single-chunk documents keep their original order.
    """
    if max_concepts <= 0:
        return []
    if not ideas:
        return []

    from mymem.evals.metrics import rouge1_f1

    groups: list[dict[str, object]] = []
    for index, idea in enumerate(ideas):
        text = _idea_text(idea)
        matched_group: dict[str, object] | None = None
        for group in groups:
            if rouge1_f1(text, str(group["text"])) >= duplicate_threshold:
                matched_group = group
                break

        if matched_group is None:
            groups.append({
                "first_index": index,
                "count": 1,
                "text": text,
                "ideas": [dict(idea)],
            })
            continue

        matched_group["count"] = int(matched_group["count"]) + 1
        group_ideas = matched_group["ideas"]
        if isinstance(group_ideas, list):
            group_ideas.append(dict(idea))

    selected: list[dict[str, object]] = []
    ranked = sorted(
        groups,
        key=lambda group: (-int(group["count"]), int(group["first_index"])),
    )
    for group in ranked[:max_concepts]:
        group_ideas = group["ideas"]
        if not isinstance(group_ideas, list):
            continue

        representative = max(
            group_ideas,
            key=lambda item: len(str(item.get("summary", ""))),
        )
        merged = dict(representative)
        tags: list[str] = []
        domains: list[str] = []
        for item in group_ideas:
            for tag in item.get("tags") or []:
                tag_text = str(tag)
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)
            domain = str(item.get("domain", ""))
            if domain:
                domains.append(domain)
        if tags:
            merged["tags"] = tags
        if domains:
            merged["domain"] = Counter(domains).most_common(1)[0][0]
        selected.append(merged)

    return selected


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block if the LLM accidentally included it."""
    import re
    return re.sub(r"^---\n.*?\n---\n?", "", text, flags=re.DOTALL).lstrip()


# ---------------------------------------------------------------------------
# Map / Merge / Verify pipeline (new extraction path)
# ---------------------------------------------------------------------------

def _parse_and_validate_ideas(
    raw: str,
    *,
    chunk_id: int | None = None,
) -> list[dict[str, object]]:
    """Parse JSON from LLM output, validate each item against IdeaSchema.
    If chunk_id is given, overrides whatever the LLM returned for that field.
    """
    import json, re
    cleaned = raw.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
        if not isinstance(data, list):
            return []
    except (json.JSONDecodeError, ValueError):
        log.debug("_parse_and_validate_ideas: JSON parse failed", raw_preview=raw[:200])
        return []

    validated: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if chunk_id is not None:
            item = {**item, "chunk_id": chunk_id}
        try:
            validated.append(IdeaSchema.model_validate(item).model_dump())
        except ValidationError:
            log.debug("Idea failed schema validation, skipping", item=item)
    return validated


async def _extract_chunk_ideas(
    chunk: str,
    chunk_id: int,
    *,
    router: "ModelRouter",
    source_name: str = "",
    source_type: str = "article",
) -> list[dict[str, object]]:
    """Map stage: extract ideas from a single chunk with schema validation."""
    hint = _SOURCE_TYPE_HINTS.get(source_type, f"a {source_type}")
    prompt = (
        f"Source: {source_name} (chunk {chunk_id})\n"
        f"Type: {hint}\n\n"
        f"---\n{chunk}\n---"
    )
    raw = await router.call(prompt, task="compile", system=_EXTRACT_SYSTEM)
    ideas = _parse_and_validate_ideas(raw, chunk_id=chunk_id)
    log.info("Chunk extracted", chunk_id=chunk_id, ideas=len(ideas))
    return ideas


def _evidence_quality(idea: dict[str, object]) -> float:
    """0.0–1.0 based on number of evidence items (capped at 3)."""
    evidence = idea.get("evidence") or []
    if not isinstance(evidence, list):
        return 0.0
    return min(len(evidence), 3) / 3.0


def _recurrence_score(
    idea: dict[str, object],
    all_chunks: list[list[dict[str, object]]],
) -> int:
    """Count how many chunks contain an idea with overlapping title."""
    from mymem.evals.metrics import rouge1_f1
    title = str(idea.get("title", ""))
    return sum(
        1 for chunk in all_chunks
        if any(rouge1_f1(title, str(c.get("title", ""))) >= 0.4 for c in chunk)
    )


async def _merge_ideas(
    chunk_idea_lists: list[list[dict[str, object]]],
    *,
    router: "ModelRouter",
) -> list[dict[str, object]]:
    """Merge stage: score by recurrence × importance × evidence_quality, then LLM merge."""
    import json as _json
    all_ideas: list[dict[str, object]] = [
        idea for chunk in chunk_idea_lists for idea in chunk
    ]
    if not all_ideas:
        return []

    # Score and annotate each candidate
    scored: list[dict[str, object]] = []
    for idea in all_ideas:
        rec = _recurrence_score(idea, chunk_idea_lists)
        imp = float(idea.get("importance", 3))
        evq = _evidence_quality(idea)
        scored.append({**idea, "recurrence_count": rec, "_score": rec * imp * max(evq, 0.1)})

    ranked = sorted(scored, key=lambda x: -float(x.get("_score", 0)))
    # Strip internal score field before sending to LLM
    candidates = [{k: v for k, v in r.items() if k != "_score"} for r in ranked]

    prompt = (
        f"Merge and deduplicate these extracted ideas.\n\n"
        f"{_json.dumps(candidates, indent=2)}"
    )
    raw = await router.call(prompt, task="merge", system=_MERGE_SYSTEM)
    merged = _parse_and_validate_ideas(raw)
    if not merged:
        # Fallback: return ranked candidates without the recurrence_count field
        merged = [{k: v for k, v in c.items() if k != "recurrence_count"} for c in candidates[:10]]
        merged = _parse_and_validate_ideas(_json.dumps(merged))
    log.info("Merge complete", input=len(all_ideas), output=len(merged))
    return merged


async def _verify_ideas(
    source_text: str,
    merged_ideas: list[dict[str, object]],
    *,
    router: "ModelRouter",
) -> list[dict[str, object]]:
    """Verify stage: one 'what's missing?' LLM turn. Appends new ideas, capped at 1."""
    import json as _json
    if not merged_ideas:
        return merged_ideas

    preview = source_text[:8000]
    prompt = (
        f"Source:\n---\n{preview}\n---\n\n"
        f"Extracted ideas so far:\n{_json.dumps(merged_ideas, indent=2)}\n\n"
        "List any important source ideas that are missing from the extraction above. "
        "Use the same JSON schema. If nothing is missing, return an empty JSON array []."
    )
    raw = await router.call(prompt, task="compile", system=_VERIFY_SYSTEM)
    new_ideas = _parse_and_validate_ideas(raw)

    if not new_ideas:
        return merged_ideas

    # Dedup: skip any new idea whose title already exists in merged_ideas
    existing_titles = {str(i.get("title", "")).lower() for i in merged_ideas}
    appended = [i for i in new_ideas if str(i.get("title", "")).lower() not in existing_titles]
    log.info("Verify complete", new_ideas=len(appended))
    return merged_ideas + appended


async def _extract_ideas_map_reduce(
    source_text: str,
    source_name: str,
    source_type: str,
    *,
    router: "ModelRouter",
) -> list[dict[str, object]]:
    """Map → Merge → Verify pipeline. Always chunks, even short sources."""
    chunks = splitter.split(source_text)
    log.info("Map stage", source=source_name, chunks=len(chunks))

    chunk_idea_lists = []
    for i, chunk in enumerate(chunks):
        ideas = await _extract_chunk_ideas(
            chunk, chunk_id=i,
            router=router,
            source_name=source_name,
            source_type=source_type,
        )
        chunk_idea_lists.append(ideas)

    merged = await _merge_ideas(chunk_idea_lists, router=router)
    if not merged:
        return []

    final = await _verify_ideas(source_text, merged, router=router)
    return final


async def _eval_extraction_background(
    *,
    source_name: str,
    source_type: str,
    source_text: str,
    pipeline_ideas: list[dict[str, object]],
    router: "ModelRouter",
    db_path: Path,
) -> None:
    """
    Fire-and-forget background task: run reference LLM extraction and score consensus.
    Skips silently if no API key is configured for the reference provider.
    Never raises — all errors are logged and swallowed.
    """
    try:
        from mymem.config import get_settings
        from mymem.evals.extraction_consensus import (
            GROQ_DEFAULT_MODEL,
            NVIDIA_DEFAULT_MODEL,
            OPENROUTER_DEFAULT_MODEL,
            run_extraction_consensus,
        )
        from mymem.evals.store import save_extraction_consensus
        from mymem.pipeline.llm import complete

        settings = get_settings()
        provider = settings.eval_reference_provider

        if provider == "groq":
            api_key = settings.groq_api_key
            ref_model = GROQ_DEFAULT_MODEL
        elif provider == "gemini":
            api_key = settings.gemini_api_key
            ref_model = "gemini-2.0-flash"
        elif provider == "nvidia":
            api_key = settings.nvidia_api_key
            ref_model = NVIDIA_DEFAULT_MODEL
        elif provider == "openrouter":
            api_key = settings.openrouter_api_key
            ref_model = OPENROUTER_DEFAULT_MODEL
        else:
            api_key = None
            ref_model = ""

        if not api_key:
            log.debug(
                "Extraction eval skipped — no API key for reference provider",
                provider=provider,
            )
            return

        async def _llm_fn(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return await complete(
                prompt,
                model=model,
                provider=provider,
                system=system,
                max_tokens=max_tokens,
                groq_api_key=api_key if provider == "groq" else "",
                gemini_api_key=api_key if provider == "gemini" else "",
                nvidia_api_key=api_key if provider == "nvidia" else "",
                openrouter_api_key=api_key if provider == "openrouter" else "",
            )

        pipeline_model = router.task_router.model_for("compile") if hasattr(router, "task_router") else "unknown"

        result = await run_extraction_consensus(
            source_id=source_name,
            source_type=source_type,
            source_text=source_text,
            pipeline_ideas=[dict(i) for i in pipeline_ideas],
            pipeline_model=pipeline_model,
            reference_model=ref_model,
            llm_fn=_llm_fn,
        )

        evals_db = db_path.parent / "evals.db"
        save_extraction_consensus(evals_db, result)
        log.info(
            "Extraction consensus eval complete",
            source=source_name,
            grade=result.grade,
            consensus_score=result.consensus_score,
            gaps=list(result.gaps),
        )
    except Exception as exc:
        log.warning(
            "Extraction consensus eval failed (background)",
            source=source_name,
            error=str(exc),
        )
