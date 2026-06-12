"""
Wiki migration into the entity graph (ADR-007, PRD Phase 1.5).

Tier 1 — seed_from_wiki(): structural, zero LLM, repair semantics.
  Page titles become entities; [[wikilinks]] become mentions, resolved
  through the deterministic resolver tiers; broken links become pageless
  entities. Re-running wipes and rebuilds ONLY tier-1 mentions, so
  ingest-derived mentions survive — the command doubles as graph repair.

Tier 2 — classify_entities(): batched LLM classify (router-injected).
  Default-typed entities get a real type and 0-3 proposed aliases.

Facade over store + resolver: callers (CLI, API) depend on these two
functions, never on the underlying steps.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from mymem.graph.extractor import ExtractedEntity
from mymem.graph.resolver import resolve_entities
from mymem.graph.store import (
    ENTITY_TYPES,
    add_alias,
    add_mention,
    delete_mentions_by_source,
    find_entity,
    get_entity,
    list_entities,
    update_entity_type,
    upsert_entity,
)
from mymem.observability.logger import get_logger
from mymem.pipeline.router import ModelRouter
from mymem.wiki.page import list_pages

log = get_logger(__name__)

SEED_SOURCE_LINKED = "tier1-wikilink"
SEED_SOURCE_BROKEN = "tier1-broken-link"
_DEFAULT_TYPE = "concept"

_CLASSIFY_SYSTEM = """\
You classify entities from a personal knowledge wiki and propose aliases.

For each entity name, answer with ONLY a JSON array:
  [{"name": "<exact name given>", "type": "<type>", "aliases": ["...", ...]}]

Rules:
- type MUST be one of: person, project, system, organization, concept
- aliases: 0-3 common alternative surface forms (abbreviations, full forms);
  never repeat the name itself; empty list when none are obvious
- "name" must be EXACTLY as given.
"""


@dataclass(frozen=True)
class SeedReport:
    pages: int
    page_entities: int
    linked_mentions: int          # mentions pointing at entities that have pages
    broken_link_entities: int     # new pageless entities created this run
    total_mentions: int


@dataclass(frozen=True)
class ClassifyReport:
    candidates: int
    classified: int


async def seed_from_wiki(db_path: Path, wiki_dir: Path) -> SeedReport:
    """Tier-1 structural seed. Idempotent — see module docstring."""
    pages = list_pages(wiki_dir)

    delete_mentions_by_source(db_path, (SEED_SOURCE_LINKED, SEED_SOURCE_BROKEN))

    page_entities = 0
    for page in pages:
        upsert_entity(
            db_path, page.title, entity_type=_DEFAULT_TYPE, page_slug=page.path.stem
        )
        page_entities += 1

    linked = 0
    broken_created = 0
    total = 0
    for page in pages:
        slug = page.path.stem
        targets = page.wikilinks()
        if not targets:
            continue
        extracted = [
            ExtractedEntity(name=t, type=_DEFAULT_TYPE, description="", span="")
            for t in targets
        ]
        resolutions = await resolve_entities(db_path, extracted)
        for target, resolution in zip(targets, resolutions, strict=True):
            if resolution.entity_id is not None:
                entity = get_entity(db_path, resolution.entity_id)
                has_page = entity is not None and entity.page_slug is not None
                add_mention(
                    db_path,
                    resolution.entity_id,
                    slug,
                    source_id=SEED_SOURCE_LINKED if has_page else SEED_SOURCE_BROKEN,
                )
                if has_page:
                    linked += 1
            else:
                created = upsert_entity(db_path, target, entity_type=_DEFAULT_TYPE)
                add_mention(db_path, created.id, slug, source_id=SEED_SOURCE_BROKEN)
                broken_created += 1
            total += 1

    log.info(
        "Tier-1 graph seed complete",
        pages=len(pages), linked=linked, broken=broken_created, mentions=total,
    )
    return SeedReport(
        pages=len(pages),
        page_entities=page_entities,
        linked_mentions=linked,
        broken_link_entities=broken_created,
        total_mentions=total,
    )


def _parse_classify_output(raw: str) -> list[dict[str, object]]:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log.debug("classify backfill: JSON parse failed", raw_preview=raw[:200])
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def classify_entities(
    db_path: Path,
    *,
    router: ModelRouter,
    batch_size: int = 20,
    limit: int = 0,
) -> ClassifyReport:
    """Tier-2 backfill: type + alias proposals for default-typed entities.

    One LLM call per batch; malformed answers, unknown names, and invalid
    types are skipped — a bad batch never aborts the run.
    """
    candidates = [
        e for e in list_entities(db_path, limit=10_000) if e.type == _DEFAULT_TYPE
    ]
    if limit > 0:
        candidates = candidates[:limit]
    if not candidates:
        return ClassifyReport(candidates=0, classified=0)

    candidate_names = {e.canonical.lower() for e in candidates}
    classified = 0

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        prompt = "\n".join(
            f"- {e.canonical}" + (f" ({e.description})" if e.description else "")
            for e in batch
        )
        raw = await router.call(prompt, task="classify", system=_CLASSIFY_SYSTEM)

        for item in _parse_classify_output(raw):
            name = str(item.get("name", "")).strip()
            entity_type = str(item.get("type", "")).strip().lower()
            if name.lower() not in candidate_names:
                continue
            if entity_type not in ENTITY_TYPES:
                log.debug("classify backfill: invalid type skipped", type=entity_type)
                continue
            entity = find_entity(db_path, name)
            if entity is None:  # pragma: no cover — guarded by candidate_names
                continue
            update_entity_type(db_path, entity.id, entity_type)
            aliases = item.get("aliases", [])
            if isinstance(aliases, list):
                for alias in aliases[:3]:
                    alias_str = str(alias).strip()
                    if alias_str and alias_str.lower() != entity.canonical.lower():
                        add_alias(db_path, entity.id, alias_str)
            classified += 1

    log.info(
        "Tier-2 classify backfill complete",
        candidates=len(candidates), classified=classified,
    )
    return ClassifyReport(candidates=len(candidates), classified=classified)
