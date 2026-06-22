"""
Tests for mymem/graph/backfill.py — wiki migration into the entity graph.

Tier 1 (seed_from_wiki): structural, zero LLM, idempotent/repair semantics.
Tier 2 (classify_entities): batched LLM via injected ModelRouter(llm_fn=fake).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mymem.graph.backfill import (
    SEED_SOURCE_BROKEN,
    SEED_SOURCE_LINKED,
    classify_entities,
    rekey_graph_page_ids,
    seed_from_wiki,
)
from mymem.graph.store import (
    add_mention,
    find_entity,
    init_db,
    mentions_for_page,
    stats,
    upsert_entity,
)
from mymem.pipeline.router import ModelRouter
from mymem.wiki.page import read_page, write_page
from mymem.wiki.types import TagDomain, WikiPage


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "graph.db"
    init_db(p)
    return p


@pytest.fixture()
def wiki_dir(tmp_path: Path) -> Path:
    d = tmp_path / "wiki"
    d.mkdir()
    return d


def _page(wiki_dir: Path, title: str, body: str = "") -> WikiPage:
    slug = title.lower().replace(" ", "-")
    page = WikiPage(
        title=title,
        body=body or f"# {title}\n\nContent.",
        path=wiki_dir / f"{slug}.md",
        tags=("test",),
        domain=TagDomain.TECH,
    )
    write_page(page)
    # write_page mints a stable id; re-read so callers get the id the graph keys on.
    return read_page(page.path)


# ---------------------------------------------------------------------------
# Tier 1 — seed_from_wiki
# ---------------------------------------------------------------------------

class TestSeedFromWiki:
    @pytest.mark.asyncio
    async def test_pages_become_entities_with_page_id(self, db: Path, wiki_dir: Path) -> None:
        p = _page(wiki_dir, "Transactional Outbox Pattern")
        report = await seed_from_wiki(db, wiki_dir)
        assert report.pages == 1
        e = find_entity(db, "Transactional Outbox Pattern")
        assert e is not None and e.page_id == p.id

    @pytest.mark.asyncio
    async def test_wikilink_to_existing_page_becomes_mention(
        self, db: Path, wiki_dir: Path
    ) -> None:
        _page(wiki_dir, "Target Page")
        src = _page(wiki_dir, "Source Page", body="See [[Target Page]] for details.")
        report = await seed_from_wiki(db, wiki_dir)
        assert report.linked_mentions == 1
        ms = mentions_for_page(db, src.id)
        assert len(ms) == 1 and ms[0].source_id == SEED_SOURCE_LINKED
        target = find_entity(db, "Target Page")
        assert target is not None and ms[0].entity_id == target.id

    @pytest.mark.asyncio
    async def test_broken_wikilink_becomes_pageless_entity(
        self, db: Path, wiki_dir: Path
    ) -> None:
        src = _page(wiki_dir, "Source Page", body="Uses [[JWT Tokens]] heavily.")
        report = await seed_from_wiki(db, wiki_dir)
        assert report.broken_link_entities == 1
        e = find_entity(db, "JWT Tokens")
        assert e is not None and e.page_id is None
        ms = mentions_for_page(db, src.id)
        assert ms[0].source_id == SEED_SOURCE_BROKEN

    @pytest.mark.asyncio
    async def test_short_form_link_resolves_to_full_title_page(
        self, db: Path, wiki_dir: Path
    ) -> None:
        _page(wiki_dir, "Transactional Outbox Pattern")
        other = _page(wiki_dir, "Other Page", body="Apply [[Transactional Outbox]] here.")
        await seed_from_wiki(db, wiki_dir)
        # Subset match — no new entity created for the short form
        full = find_entity(db, "Transactional Outbox Pattern")
        assert full is not None
        ms = mentions_for_page(db, other.id)
        assert ms[0].entity_id == full.id
        assert stats(db).total_entities == 2  # the two pages, nothing else

    @pytest.mark.asyncio
    async def test_idempotent_rerun_no_duplicates(self, db: Path, wiki_dir: Path) -> None:
        _page(wiki_dir, "Target Page")
        _page(wiki_dir, "Source Page", body="See [[Target Page]] and [[Ghost Page]].")
        first = await seed_from_wiki(db, wiki_dir)
        second = await seed_from_wiki(db, wiki_dir)
        assert first.total_mentions == second.total_mentions
        assert stats(db).total_mentions == second.total_mentions
        assert stats(db).total_entities == 3  # 2 pages + 1 broken link

    @pytest.mark.asyncio
    async def test_empty_wiki_returns_zero_report(self, db: Path, wiki_dir: Path) -> None:
        report = await seed_from_wiki(db, wiki_dir)
        assert report.pages == 0
        assert report.total_mentions == 0

    @pytest.mark.asyncio
    async def test_preserves_non_tier1_mentions_on_rerun(
        self, db: Path, wiki_dir: Path
    ) -> None:
        # A mention written by ingest (different source_id) must survive re-seed
        from mymem.graph.store import add_mention

        _page(wiki_dir, "Target Page")
        e = upsert_entity(db, "Ingested Thing", entity_type="concept")
        add_mention(db, e.id, "target-page", source_id="ingest")
        await seed_from_wiki(db, wiki_dir)
        ms = [m for m in mentions_for_page(db, "target-page") if m.source_id == "ingest"]
        assert len(ms) == 1


# ---------------------------------------------------------------------------
# Tier 2 — classify_entities
# ---------------------------------------------------------------------------

def _classify_router(answers: list[dict[str, object]]) -> tuple[ModelRouter, list[str]]:
    calls: list[str] = []

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        calls.append(prompt)
        return json.dumps(answers)

    return ModelRouter(llm_fn=fake_llm), calls


class TestClassifyEntities:
    @pytest.mark.asyncio
    async def test_assigns_type_and_aliases(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", entity_type="concept")  # default type → candidate
        router, _ = _classify_router(
            [{"name": "Sarah Chen", "type": "person", "aliases": ["S. Chen"]}]
        )
        report = await classify_entities(db, router=router)
        assert report.classified == 1
        e = find_entity(db, "Sarah Chen")
        assert e is not None and e.type == "person" and e.aliases == ("S. Chen",)

    @pytest.mark.asyncio
    async def test_skips_non_default_types(self, db: Path) -> None:
        upsert_entity(db, "Already Typed", entity_type="system")
        router, calls = _classify_router([])
        report = await classify_entities(db, router=router)
        assert report.classified == 0
        assert calls == []  # nothing to classify → no LLM call

    @pytest.mark.asyncio
    async def test_invalid_type_in_answer_ignored(self, db: Path) -> None:
        upsert_entity(db, "Thing", entity_type="concept")
        router, _ = _classify_router([{"name": "Thing", "type": "alien", "aliases": []}])
        report = await classify_entities(db, router=router)
        assert report.classified == 0
        e = find_entity(db, "Thing")
        assert e is not None and e.type == "concept"  # unchanged

    @pytest.mark.asyncio
    async def test_unknown_name_in_answer_ignored(self, db: Path) -> None:
        upsert_entity(db, "Thing", entity_type="concept")
        router, _ = _classify_router([{"name": "Imaginary", "type": "person", "aliases": []}])
        report = await classify_entities(db, router=router)
        assert report.classified == 0

    @pytest.mark.asyncio
    async def test_batching_respects_batch_size(self, db: Path) -> None:
        for i in range(5):
            upsert_entity(db, f"Entity {i}", entity_type="concept")
        router, calls = _classify_router([])
        await classify_entities(db, router=router, batch_size=2)
        assert len(calls) == 3  # 5 entities / batch of 2 → 3 calls

    @pytest.mark.asyncio
    async def test_garbage_llm_output_is_not_fatal(self, db: Path) -> None:
        upsert_entity(db, "Thing", entity_type="concept")

        async def garbage(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return "no json here"

        report = await classify_entities(db, router=ModelRouter(llm_fn=garbage))
        assert report.classified == 0

    @pytest.mark.asyncio
    async def test_limit_caps_candidates(self, db: Path) -> None:
        for i in range(5):
            upsert_entity(db, f"Entity {i}", entity_type="concept")
        router, calls = _classify_router([])
        report = await classify_entities(db, router=router, limit=2, batch_size=10)
        assert report.candidates == 2


# ---------------------------------------------------------------------------
# Resolution precision — opt-in embedding/judge tiers (Part A)
# ---------------------------------------------------------------------------

class TestSeedResolutionTiers:
    @pytest.mark.asyncio
    async def test_semantic_tier_resolves_borderline_to_existing_page(
        self, tmp_path: Path, wiki_dir: Path
    ) -> None:
        # A wikilink worded differently from the page title: fuzzy ~72 (borderline),
        # so deterministic-only seed records it BROKEN; the embedding tier links it.
        _page(wiki_dir, "Durable State Machine Implementation")
        _page(wiki_dir, "Workflow Notes", body="See [[Durable State Machines]] here.")

        det_db = tmp_path / "det.db"
        init_db(det_db)
        det = await seed_from_wiki(det_db, wiki_dir)  # deterministic-only

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]  # identical vectors → cosine 1.0

        sem_db = tmp_path / "sem.db"
        init_db(sem_db)
        sem = await seed_from_wiki(sem_db, wiki_dir, embed_fn=fake_embed)

        assert det.linked_mentions == 0
        assert det.broken_link_entities == 1
        # With the embedding tier the borderline resolves to the real page.
        assert sem.linked_mentions == 1
        assert sem.broken_link_entities == 0

    @pytest.mark.asyncio
    async def test_default_seed_is_deterministic_only(
        self, db: Path, wiki_dir: Path
    ) -> None:
        # No embed_fn/router → today's behavior preserved (borderline stays broken).
        _page(wiki_dir, "Durable State Machine Implementation")
        _page(wiki_dir, "Workflow Notes", body="See [[Durable State Machines]].")
        report = await seed_from_wiki(db, wiki_dir)
        assert report.broken_link_entities == 1
        assert report.linked_mentions == 0


# ---------------------------------------------------------------------------
# rekey_graph_page_ids — slug → stable id value migration (ADR-014 D4)
# ---------------------------------------------------------------------------

class TestRekeyGraphPageIds:
    def test_converts_slug_anchors_to_ids(self, db: Path, wiki_dir: Path) -> None:
        p = _page(wiki_dir, "Vector Search")
        slug = p.path.stem
        # Legacy state: entity + mention keyed by slug (pre-rekey value).
        e = upsert_entity(db, "Vector Search", entity_type="concept", page_id=slug)
        add_mention(db, e.id, slug, source_id="ingest")

        report = rekey_graph_page_ids(db, wiki_dir)

        assert report.entities_rekeyed == 1
        assert report.mentions_rekeyed == 1
        assert report.unresolved == 0
        got = find_entity(db, "Vector Search")
        assert got is not None and got.page_id == p.id
        assert len(mentions_for_page(db, p.id)) == 1
        assert mentions_for_page(db, slug) == []  # old slug key no longer matches

    def test_idempotent_rerun_counts_zero(self, db: Path, wiki_dir: Path) -> None:
        p = _page(wiki_dir, "Vector Search")
        e = upsert_entity(db, "Vector Search", entity_type="concept", page_id=p.path.stem)
        add_mention(db, e.id, p.path.stem, source_id="ingest")
        rekey_graph_page_ids(db, wiki_dir)
        second = rekey_graph_page_ids(db, wiki_dir)
        assert second.entities_rekeyed == 0
        assert second.mentions_rekeyed == 0
        assert second.unresolved == 0  # already-id anchors are not counted unresolved

    def test_unresolved_slug_left_untouched(self, db: Path, wiki_dir: Path) -> None:
        # An anchor whose page no longer exists in the wiki → unresolved, never deleted.
        upsert_entity(db, "Ghost", entity_type="concept", page_id="ghost-page")
        report = rekey_graph_page_ids(db, wiki_dir)
        assert report.unresolved == 1
        assert report.entities_rekeyed == 0
        got = find_entity(db, "Ghost")
        assert got is not None and got.page_id == "ghost-page"
