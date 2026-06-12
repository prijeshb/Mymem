"""
Typed entity extraction from source text (ADR-007).

LLM emits JSON entities with a verbatim source span; a mechanical
span-grounding filter (rapidfuzz, zero LLM cost) drops hallucinated
entities whose name AND span both fail to match the source.

All LLM calls go through the injected ModelRouter (Dependency Inversion —
tests inject ModelRouter(llm_fn=fake), same as the ingest pipeline).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError
from rapidfuzz import fuzz

from mymem.graph.store import ENTITY_TYPES
from mymem.observability.logger import get_logger
from mymem.pipeline.router import ModelRouter

log = get_logger(__name__)

# Mechanical hallucination gate: name or span must fuzzy-match the source
GROUNDING_THRESHOLD = 80     # rapidfuzz partial_ratio, 0-100
_SOURCE_CHAR_LIMIT = 8000    # callers map-reduce longer sources (same as ingest)

_ENTITY_SYSTEM = """\
You extract named entities from a document for a personal knowledge wiki.

Return ONLY a JSON array. Each element:
  {"name": "...", "type": "...", "description": "...", "span": "..."}

Rules:
- type MUST be one of: person, project, system, organization, concept
- name: the canonical surface form as used in the document
- description: one short sentence, from the document only
- span: a SHORT VERBATIM quote from the document where the entity appears
- Extract only entities central to the document. No generic terms.
- No markdown, no commentary — the JSON array only.
"""


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    type: str
    description: str
    span: str


class _EntitySchema(BaseModel):
    name: str
    type: str
    description: str = ""
    span: str = ""


def _parse_json_array(raw: str) -> list[dict[str, object]]:
    """Strip think-blocks/code fences and parse a JSON array of objects."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.debug("entity extraction: JSON parse failed", raw_preview=raw[:200])
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _is_grounded(entity: _EntitySchema, source_lower: str) -> bool:
    name_score = fuzz.partial_ratio(entity.name.lower(), source_lower)
    if name_score >= GROUNDING_THRESHOLD:
        return True
    if entity.span:
        span_score = fuzz.partial_ratio(entity.span.lower(), source_lower)
        if span_score >= GROUNDING_THRESHOLD:
            return True
    return False


async def extract_entities(
    source_text: str,
    *,
    router: ModelRouter,
    max_entities: int = 20,
) -> list[ExtractedEntity]:
    """Extract typed, span-grounded entities from *source_text*.

    Invalid items are skipped (never fatal); hallucinated entities — name and
    span both absent from the source — are filtered; duplicates merged
    case-insensitively (first occurrence wins).
    """
    text = source_text.strip()
    if not text:
        return []

    raw = await router.call(
        text[:_SOURCE_CHAR_LIMIT],
        task="classify",
        system=_ENTITY_SYSTEM,
    )

    source_lower = text[:_SOURCE_CHAR_LIMIT].lower()
    seen: set[str] = set()
    out: list[ExtractedEntity] = []

    for item in _parse_json_array(raw):
        try:
            parsed = _EntitySchema(**{str(k): v for k, v in item.items()})  # type: ignore[arg-type]
        except ValidationError:
            log.debug("entity extraction: item failed schema", item=str(item)[:100])
            continue

        normalized_type = parsed.type.strip().lower()
        if normalized_type not in ENTITY_TYPES:
            log.debug("entity extraction: invalid type skipped", type=parsed.type)
            continue
        name = " ".join(parsed.name.split())
        if not name:
            continue

        key = name.lower()
        if key in seen:
            continue
        if not _is_grounded(parsed, source_lower):
            log.info("entity extraction: ungrounded entity filtered", name=name)
            continue

        seen.add(key)
        out.append(ExtractedEntity(
            name=name,
            type=normalized_type,
            description=parsed.description.strip(),
            span=parsed.span.strip(),
        ))
        if len(out) >= max_entities:
            break

    return out
