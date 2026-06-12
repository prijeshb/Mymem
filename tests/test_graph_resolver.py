"""
Tests for mymem/graph/resolver.py — 3-tier entity resolution (Graphiti pattern).

Tier 1 exact/alias → tier 2 fuzzy + optional embedding → tier 3 batched LLM judge.
Every external dependency injected: embed_fn fake, ModelRouter(llm_fn=fake).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mymem.graph.extractor import ExtractedEntity
from mymem.graph.resolver import Resolution, resolve_entities
from mymem.graph.store import add_alias, init_db, upsert_entity
from mymem.pipeline.router import ModelRouter


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "graph.db"
    init_db(p)
    return p


def _ent(name: str, type: str = "concept") -> ExtractedEntity:
    return ExtractedEntity(name=name, type=type, description="", span="")


def _judge_router(mapping: dict[str, str | None]) -> tuple[ModelRouter, list[str]]:
    """Router whose fake LLM answers the dedup-judge prompt; records calls."""
    calls: list[str] = []

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        calls.append(prompt)
        return json.dumps([{"name": k, "match": v} for k, v in mapping.items()])

    return ModelRouter(llm_fn=fake_llm), calls


# ---------------------------------------------------------------------------
# Tier 1 — exact / alias
# ---------------------------------------------------------------------------

class TestTier1Exact:
    @pytest.mark.asyncio
    async def test_exact_canonical_match(self, db: Path) -> None:
        e = upsert_entity(db, "Sarah Chen", type="person")
        [r] = await resolve_entities(db, [_ent("Sarah Chen", "person")])
        assert r == Resolution(entity_id=e.id, tier="exact", score=1.0)

    @pytest.mark.asyncio
    async def test_exact_is_case_insensitive(self, db: Path) -> None:
        e = upsert_entity(db, "MyMem", type="project")
        [r] = await resolve_entities(db, [_ent("mymem", "project")])
        assert r.entity_id == e.id and r.tier == "exact"

    @pytest.mark.asyncio
    async def test_alias_match(self, db: Path) -> None:
        e = upsert_entity(db, "Large Language Models", type="concept")
        add_alias(db, e.id, "LLM")
        [r] = await resolve_entities(db, [_ent("LLM")])
        assert r.entity_id == e.id and r.tier == "exact"


# ---------------------------------------------------------------------------
# Tier 2 — fuzzy
# ---------------------------------------------------------------------------

class TestTier2Fuzzy:
    @pytest.mark.asyncio
    async def test_near_identical_name_fuzzy_accepted(self, db: Path) -> None:
        e = upsert_entity(db, "Retrieval-Augmented Generation", type="concept")
        [r] = await resolve_entities(db, [_ent("Retrieval Augmented Generation")])
        assert r.entity_id == e.id and r.tier == "fuzzy"

    @pytest.mark.asyncio
    async def test_unrelated_name_is_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        [r] = await resolve_entities(db, [_ent("Quantum Computing")])
        assert r == Resolution(entity_id=None, tier="new", score=0.0)

    @pytest.mark.asyncio
    async def test_empty_catalog_everything_new(self, db: Path) -> None:
        [r] = await resolve_entities(db, [_ent("Anything")])
        assert r.tier == "new"


# ---------------------------------------------------------------------------
# Tier 2b — optional embedding scoring of borderline candidates
# ---------------------------------------------------------------------------

class TestEmbeddingTier:
    @pytest.mark.asyncio
    async def test_borderline_accepted_by_high_cosine(self, db: Path) -> None:
        e = upsert_entity(db, "Sarah Chen", type="person")

        async def embed_fn(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]   # identical vectors → cosine 1.0

        # "S. Chen" vs "Sarah Chen": fuzzy borderline (not auto-accept)
        [r] = await resolve_entities(db, [_ent("S. Chen", "person")], embed_fn=embed_fn)
        assert r.entity_id == e.id and r.tier == "embedding"

    @pytest.mark.asyncio
    async def test_borderline_low_cosine_no_router_is_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")

        async def embed_fn(texts: list[str]) -> list[list[float]]:
            # First half (names) orthogonal to second half (candidates)
            return [[1.0, 0.0]] + [[0.0, 1.0]] * (len(texts) - 1)

        [r] = await resolve_entities(db, [_ent("S. Chen", "person")], embed_fn=embed_fn)
        assert r.tier == "new"


# ---------------------------------------------------------------------------
# Tier 3 — batched LLM judge
# ---------------------------------------------------------------------------

class TestTier3Judge:
    @pytest.mark.asyncio
    async def test_judge_confirms_match(self, db: Path) -> None:
        e = upsert_entity(db, "Sarah Chen", type="person")
        router, _ = _judge_router({"S. Chen": "Sarah Chen"})
        [r] = await resolve_entities(db, [_ent("S. Chen", "person")], router=router)
        assert r.entity_id == e.id and r.tier == "llm"

    @pytest.mark.asyncio
    async def test_judge_rejects_match(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        router, _ = _judge_router({"S. Chen": None})
        [r] = await resolve_entities(db, [_ent("S. Chen", "person")], router=router)
        assert r.tier == "new"

    @pytest.mark.asyncio
    async def test_judge_inventing_unknown_match_treated_as_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        router, _ = _judge_router({"S. Chen": "Nonexistent Person"})
        [r] = await resolve_entities(db, [_ent("S. Chen", "person")], router=router)
        assert r.tier == "new"

    @pytest.mark.asyncio
    async def test_judge_is_one_batched_call(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        upsert_entity(db, "Model Router", type="system")
        router, calls = _judge_router({"S. Chen": "Sarah Chen", "ModelRoute": "Model Router"})
        out = await resolve_entities(
            db, [_ent("S. Chen", "person"), _ent("ModelRoute", "system")], router=router
        )
        assert len(calls) == 1
        assert all(r.tier == "llm" for r in out)

    @pytest.mark.asyncio
    async def test_judge_garbage_output_degrades_to_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")

        async def garbage(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return "I cannot answer that."

        [r] = await resolve_entities(
            db, [_ent("S. Chen", "person")], router=ModelRouter(llm_fn=garbage)
        )
        assert r.tier == "new"


# ---------------------------------------------------------------------------
# Degradation + ordering
# ---------------------------------------------------------------------------

class TestDegradationAndOrder:
    @pytest.mark.asyncio
    async def test_borderline_without_embedder_or_router_is_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        [r] = await resolve_entities(db, [_ent("S. Chen", "person")])
        assert r.tier == "new"

    @pytest.mark.asyncio
    async def test_empty_input(self, db: Path) -> None:
        assert await resolve_entities(db, []) == []

    @pytest.mark.asyncio
    async def test_results_preserve_input_order(self, db: Path) -> None:
        a = upsert_entity(db, "Alpha", type="concept")
        b = upsert_entity(db, "Beta", type="concept")
        out = await resolve_entities(db, [_ent("Beta"), _ent("New Thing"), _ent("Alpha")])
        assert [r.entity_id for r in out] == [b.id, None, a.id]

    @pytest.mark.asyncio
    async def test_no_llm_call_when_nothing_borderline(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")
        router, calls = _judge_router({})
        await resolve_entities(db, [_ent("Sarah Chen", "person")], router=router)
        assert calls == []


def test_resolution_immutable() -> None:
    r = Resolution(entity_id=None, tier="new", score=0.0)
    with pytest.raises(AttributeError):
        r.tier = "exact"  # type: ignore[misc]


class TestJudgeParsing:
    @pytest.mark.asyncio
    async def test_judge_json_object_not_array_degrades_to_new(self, db: Path) -> None:
        upsert_entity(db, "Sarah Chen", type="person")

        async def obj_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return json.dumps({"name": "S. Chen", "match": "Sarah Chen"})

        [r] = await resolve_entities(
            db, [_ent("S. Chen", "person")], router=ModelRouter(llm_fn=obj_llm)
        )
        assert r.tier == "new"
