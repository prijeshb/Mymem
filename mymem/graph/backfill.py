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
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mymem.graph.extractor import ExtractedEntity
from mymem.graph.resolver import EmbedFn, resolve_entities
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


@dataclass(frozen=True)
class RekeyReport:
    """Result of a slug→id graph re-key pass (ADR-014 D4)."""
    entities_rekeyed: int
    mentions_rekeyed: int
    unresolved: int   # distinct slug anchors with no matching page id (left as-is)


def rekey_graph_page_ids(db_path: Path, wiki_dir: Path) -> RekeyReport:
    """Convert legacy slug-valued page anchors to stable page ids (ADR-014 D4).

    After the structural column rename (store._migrate_slug_to_id), existing
    entities.page_id / mentions.page_id still hold *slugs*. Resolve each distinct
    slug to its page's stable id via the wiki identity index and rewrite it.

    Idempotent: a value that already is an id (or that resolves to itself) is left
    untouched; a slug with no matching page is left as-is and counted as unresolved
    (a tolerated dangling anchor, never deleted).
    """
    from mymem.wiki.identity import build_page_id_index, resolve_to_id

    index = build_page_id_index(wiki_dir)
    valid_ids = set(index.values())  # already-stable anchors — skip without counting
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    entities_rekeyed = 0
    mentions_rekeyed = 0
    unresolved_keys: set[str] = set()
    try:
        with conn:
            for table in ("entities", "mentions"):
                rows = conn.execute(
                    f"SELECT DISTINCT page_id FROM {table} WHERE page_id IS NOT NULL"  # noqa: S608
                ).fetchall()
                for r in rows:
                    old = r["page_id"]
                    if old in valid_ids:
                        continue  # already a stable id (idempotent re-run)
                    new = resolve_to_id(index, old)
                    if new is None:
                        unresolved_keys.add(old)
                        continue
                    if new == old:
                        continue
                    cur = conn.execute(
                        f"UPDATE {table} SET page_id = ? WHERE page_id = ?",  # noqa: S608
                        (new, old),
                    )
                    if table == "entities":
                        entities_rekeyed += cur.rowcount
                    else:
                        mentions_rekeyed += cur.rowcount
    finally:
        conn.close()

    log.info(
        "Graph slug→id re-key complete",
        entities=entities_rekeyed, mentions=mentions_rekeyed, unresolved=len(unresolved_keys),
    )
    return RekeyReport(
        entities_rekeyed=entities_rekeyed,
        mentions_rekeyed=mentions_rekeyed,
        unresolved=len(unresolved_keys),
    )


async def seed_from_wiki(
    db_path: Path,
    wiki_dir: Path,
    *,
    embed_fn: EmbedFn | None = None,
    router: ModelRouter | None = None,
) -> SeedReport:
    """Tier-1 structural seed. Idempotent — see module docstring.

    Resolution precision (opt-in): pass `embed_fn` to enable the embedding-cosine
    tier and `router` to enable the LLM-judge tier when matching `[[wikilinks]]` to
    existing pages. Both default to None → deterministic-only (exact + fuzzy),
    keeping the seed zero-cost and offline. Supplying them reduces false-broken
    links where a wikilink means an existing page but is worded differently.
    """
    pages = list_pages(wiki_dir)

    delete_mentions_by_source(db_path, (SEED_SOURCE_LINKED, SEED_SOURCE_BROKEN))

    page_entities = 0
    for page in pages:
        # Anchor entities on the page's stable id (ADR-013/014); fall back to the slug
        # only for a (pre-ADR-013) page that has no id yet.
        upsert_entity(
            db_path, page.title, entity_type=_DEFAULT_TYPE, page_id=page.id or page.path.stem
        )
        page_entities += 1

    linked = 0
    broken_created = 0
    total = 0
    for page in pages:
        page_key = page.id or page.path.stem
        targets = page.wikilinks()
        if not targets:
            continue
        extracted = [
            ExtractedEntity(name=t, type=_DEFAULT_TYPE, description="", span="")
            for t in targets
        ]
        resolutions = await resolve_entities(
            db_path, extracted, embed_fn=embed_fn, router=router
        )
        for target, resolution in zip(targets, resolutions, strict=True):
            if resolution.entity_id is not None:
                entity = get_entity(db_path, resolution.entity_id)
                has_page = entity is not None and entity.page_id is not None
                add_mention(
                    db_path,
                    resolution.entity_id,
                    page_key,
                    source_id=SEED_SOURCE_LINKED if has_page else SEED_SOURCE_BROKEN,
                )
                if has_page:
                    linked += 1
            else:
                created = upsert_entity(db_path, target, entity_type=_DEFAULT_TYPE)
                add_mention(db_path, created.id, page_key, source_id=SEED_SOURCE_BROKEN)
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
