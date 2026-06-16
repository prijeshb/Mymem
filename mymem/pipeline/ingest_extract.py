"""
Ingest extraction — the Map / Merge / Verify idea pipeline (split out of ingest.py).

Turns raw source text into a ranked list of atomic ideas:
  Map    — extract ideas per chunk (`_extract_chunk_ideas`), grounding each source_span
  Merge  — dedupe + score by recurrence × importance × evidence (`_merge_ideas`)
  Verify — one "what's missing?" pass (`_verify_ideas`)

Pure of any wiki/claims/RAG I/O — the router is injected so it mocks cleanly in tests.
"""
from __future__ import annotations

import textwrap
from collections import Counter
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError
from pydantic import Field as PydanticField

from mymem.observability.logger import get_logger
from mymem.pipeline.splitter import ChunkSplitter

if TYPE_CHECKING:
    from mymem.pipeline.router import ModelRouter

log = get_logger(__name__)

splitter = ChunkSplitter(max_tokens=1024)


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
    # Verbatim quote from the source grounding this idea (ADR-011 D2). Blanked by
    # _ground_idea_spans when not actually found in the source (anti-hallucination).
    source_span: str = ""


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
    "source_span": "a short quote copied VERBATIM (exact characters) from the source that grounds this idea",
    "chunk_id": 0,
    "importance": 3,
    "main_thesis": false,
    "tags": ["lowercase"],
    "domain": "tech|research|business|personal|creative|finance|health|spiritual|reminder|misc"
  }
]

Rules:
- Do not infer facts not present in the source.
- "source_span" MUST be copied verbatim (exact substring) from the source, never paraphrased.
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
# Parsing
# ---------------------------------------------------------------------------

def _parse_ideas(raw: str) -> list[dict[str, object]]:
    """Parse LLM JSON output into a list of idea dicts."""
    import json
    import re
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


def _parse_and_validate_ideas(
    raw: str,
    *,
    chunk_id: int | None = None,
) -> list[dict[str, object]]:
    """Parse JSON from LLM output, validate each item against IdeaSchema.
    If chunk_id is given, overrides whatever the LLM returned for that field.
    """
    import json
    import re
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


# ---------------------------------------------------------------------------
# Source-span grounding (ADR-011 Phase 1)
# ---------------------------------------------------------------------------

def _ground_span(span: str, source: str, *, min_ratio: float = 90.0) -> str:
    """
    Return *span* if it is grounded in *source* (verbatim or near-verbatim), else "".

    Mechanical anti-hallucination check (ADR-011 D2). A whitespace/case-normalized
    substring match accepts exact and lightly-reformatted quotes; rapidfuzz
    partial_ratio then catches minor OCR/transcription drift. An ungrounded span is
    dropped (blanked) — the idea itself is kept, so recall never regresses.
    """
    span = span.strip()
    if not span:
        return ""
    norm_span = " ".join(span.split()).lower()
    norm_source = " ".join(source.split()).lower()
    if norm_span in norm_source:
        return span
    from rapidfuzz import fuzz
    if fuzz.partial_ratio(norm_span, norm_source) >= min_ratio:
        return span
    return ""


def _ground_idea_spans(
    ideas: list[dict[str, object]], source: str
) -> list[dict[str, object]]:
    """Return new idea dicts with each `source_span` validated against *source*."""
    return [
        {**idea, "source_span": _ground_span(str(idea.get("source_span", "")), source)}
        for idea in ideas
    ]


def _preserve_spans(
    merged: list[dict[str, object]], candidates: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Refill `source_span` the merge LLM dropped, from the pre-merge candidates (ADR-015 D3).

    The map stage already grounded each candidate's span against its chunk; the merge LLM
    often omits it. For each merged idea missing a span, copy the first *grounded* span from
    a candidate with the same title (case-insensitive). Existing merged spans are kept.
    Immutable — returns new dicts.
    """
    by_title: dict[str, str] = {}
    for cand in candidates:
        title = str(cand.get("title", "")).strip().lower()
        span = str(cand.get("source_span", "")).strip()
        if title and span and title not in by_title:
            by_title[title] = span

    out: list[dict[str, object]] = []
    for idea in merged:
        if str(idea.get("source_span", "")).strip():
            out.append({**idea})
            continue
        recovered = by_title.get(str(idea.get("title", "")).strip().lower(), "")
        out.append({**idea, "source_span": recovered})
    return out


# ---------------------------------------------------------------------------
# Map / Merge / Verify pipeline
# ---------------------------------------------------------------------------

async def _extract_chunk_ideas(
    chunk: str,
    chunk_id: int,
    *,
    router: ModelRouter,
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
    ideas = _ground_idea_spans(ideas, chunk)  # drop hallucinated spans (ADR-011 D2)
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
    router: ModelRouter,
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
    # The merge LLM frequently drops source_span; recover it from the grounded candidates.
    merged = _preserve_spans(merged, candidates)
    log.info("Merge complete", input=len(all_ideas), output=len(merged))
    return merged


async def _verify_ideas(
    source_text: str,
    merged_ideas: list[dict[str, object]],
    *,
    router: ModelRouter,
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
    router: ModelRouter,
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
