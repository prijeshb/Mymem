"""
3-tier entity resolution — ground extracted entities against the catalog (ADR-007).

Ported from Graphiti's deterministic-first, LLM-last pipeline:

  Tier 1  exact canonical/alias match (free)
  Tier 2  fuzzy name match, rapidfuzz token_sort + punctuation-stripping
          processor; borderline candidates optionally scored by embedding
          cosine (embed_fn injected — skipped gracefully when unavailable)
  Tier 3  ONE batched LLM-judge call for surviving borderlines (router
          injected — skipped gracefully; unresolved borderlines become new)

Dependency Inversion: embed_fn and router are injected abstractions; tests
use fakes, production wires the Ollama embedder and the model router.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, utils

from mymem.graph.extractor import ExtractedEntity
from mymem.graph.store import Entity, find_entity, list_entities
from mymem.observability.logger import get_logger
from mymem.pipeline.router import ModelRouter

log = get_logger(__name__)

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

FUZZY_ACCEPT = 92.0       # token_sort_ratio: auto-accept as same entity
FUZZY_BORDERLINE = 70.0   # below this: definitely a new entity
COSINE_ACCEPT = 0.85      # embedding similarity auto-accept for borderlines

_JUDGE_SYSTEM = """\
You decide whether candidate names refer to existing entities in a knowledge wiki.

For each item you get a NAME (newly extracted) and a CANDIDATE (existing entity).
Answer with ONLY a JSON array:
  [{"name": "<the name>", "match": "<candidate canonical>" or null}]

Rules:
- "match" must be EXACTLY the candidate canonical given, or null.
- Same real-world thing under different surface forms → match.
- Different things with similar names → null. When unsure → null.
"""


@dataclass(frozen=True)
class Resolution:
    entity_id: int | None    # matched existing entity, or None → create new
    tier: str                # "exact" | "fuzzy" | "embedding" | "llm" | "new"
    score: float

    @staticmethod
    def new() -> Resolution:
        return Resolution(entity_id=None, tier="new", score=0.0)


def _pair_score(name: str, candidate: str, name_tokens: int) -> float:
    """token_sort_ratio, upgraded to 100 when *name* (>= 2 tokens) is a full
    token-subset of *candidate* — the short-form wiki link pattern
    ('Transactional Outbox' for 'Transactional Outbox Pattern'). Single-token
    names are excluded: 'AI' is a subset of half the catalog."""
    score = fuzz.token_sort_ratio(name, candidate, processor=utils.default_process)
    if name_tokens >= 2:
        subset = fuzz.token_set_ratio(name, candidate, processor=utils.default_process)
        if subset == 100.0:
            return 100.0
    return float(score)


def _best_fuzzy(name: str, catalog: list[Entity]) -> tuple[Entity | None, float]:
    """Best fuzzy score across all canonicals and aliases."""
    name_tokens = len(utils.default_process(name).split())
    best: Entity | None = None
    best_score = 0.0
    for entity in catalog:
        for candidate in (entity.canonical, *entity.aliases):
            score = _pair_score(name, candidate, name_tokens)
            if score > best_score:
                best, best_score = entity, score
    return best, best_score


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _parse_judge_output(raw: str) -> dict[str, str | None]:
    """Parse the judge's JSON array into {name_lower: match_or_None}."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.debug("entity judge: JSON parse failed", raw_preview=raw[:200])
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, str | None] = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            match = item.get("match")
            out[item["name"].lower()] = match if isinstance(match, str) else None
    return out


async def _judge_borderlines(
    router: ModelRouter,
    pairs: list[tuple[ExtractedEntity, Entity]],
) -> dict[str, str | None]:
    """One batched LLM call deciding match/new for every borderline pair."""
    lines = [
        f"- NAME: {ent.name} ({ent.type}; {ent.description or 'no description'})\n"
        f"  CANDIDATE: {cand.canonical} ({cand.type}; {cand.description or 'no description'})"
        for ent, cand in pairs
    ]
    raw = await router.call("\n".join(lines), task="classify", system=_JUDGE_SYSTEM)
    return _parse_judge_output(raw)


async def resolve_entities(
    db_path: Path,
    extracted: list[ExtractedEntity],
    *,
    embed_fn: EmbedFn | None = None,
    router: ModelRouter | None = None,
) -> list[Resolution]:
    """Resolve each extracted entity against the stored catalog.

    Returns one Resolution per input, in input order. Degrades gracefully:
    without embed_fn the embedding tier is skipped; without router the
    judge tier is skipped and borderlines resolve as new.
    """
    if not extracted:
        return []

    catalog = list_entities(db_path, limit=10_000)
    results: dict[int, Resolution] = {}
    borderline: dict[int, Entity] = {}   # input index -> best candidate

    for i, ent in enumerate(extracted):
        exact = find_entity(db_path, ent.name)
        if exact is not None:
            results[i] = Resolution(entity_id=exact.id, tier="exact", score=1.0)
            continue
        candidate, score = _best_fuzzy(ent.name, catalog)
        if candidate is not None and score >= FUZZY_ACCEPT:
            results[i] = Resolution(entity_id=candidate.id, tier="fuzzy", score=score / 100)
        elif candidate is not None and score >= FUZZY_BORDERLINE:
            borderline[i] = candidate
        else:
            results[i] = Resolution.new()

    # Tier 2b — embedding cosine on borderlines (one batched embed call)
    if borderline and embed_fn is not None:
        indices = list(borderline)
        names = [extracted[i].name for i in indices]
        candidates = [borderline[i].canonical for i in indices]
        vectors = await embed_fn(names + candidates)
        n = len(indices)
        for j, i in enumerate(indices):
            cos = _cosine(vectors[j], vectors[n + j])
            if cos >= COSINE_ACCEPT:
                results[i] = Resolution(entity_id=borderline[i].id, tier="embedding", score=cos)
        borderline = {i: c for i, c in borderline.items() if i not in results}

    # Tier 3 — batched LLM judge
    if borderline and router is not None:
        pairs = [(extracted[i], borderline[i]) for i in borderline]
        decisions = await _judge_borderlines(router, pairs)
        for i, candidate in borderline.items():
            match = decisions.get(extracted[i].name.lower())
            # Guard: the judge may only confirm the candidate we offered
            if match is not None and match.lower() == candidate.canonical.lower():
                results[i] = Resolution(entity_id=candidate.id, tier="llm", score=0.75)

    # Whatever survived every tier is a new entity
    for i in range(len(extracted)):
        if i not in results:
            results[i] = Resolution.new()

    return [results[i] for i in range(len(extracted))]
