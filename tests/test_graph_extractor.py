"""
Tests for mymem/graph/extractor.py — typed entity extraction with span grounding.

All LLM calls injected via ModelRouter(llm_fn=fake) — never a real provider.
"""
from __future__ import annotations

import json

import pytest

from mymem.graph.extractor import ExtractedEntity, extract_entities
from mymem.pipeline.router import ModelRouter

SOURCE = (
    "Sarah Chen leads the platform team at Acme Corp. "
    "Her team built MyMem, a personal wiki system that uses "
    "Retrieval-Augmented Generation to answer questions from markdown notes."
)


def _router_returning(payload: object) -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return json.dumps(payload) if not isinstance(payload, str) else payload

    return ModelRouter(llm_fn=fake_llm)


GOOD = [
    {"name": "Sarah Chen", "type": "person", "description": "Platform team lead",
     "span": "Sarah Chen leads the platform team"},
    {"name": "MyMem", "type": "project", "description": "Personal wiki system",
     "span": "built MyMem, a personal wiki system"},
    {"name": "Acme Corp", "type": "organization", "description": "",
     "span": "platform team at Acme Corp"},
]


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_extracts_valid_entities(self) -> None:
        out = await extract_entities(SOURCE, router=_router_returning(GOOD))
        assert [e.name for e in out] == ["Sarah Chen", "MyMem", "Acme Corp"]
        assert out[0].type == "person"
        assert isinstance(out[0], ExtractedEntity)

    @pytest.mark.asyncio
    async def test_code_fenced_json_parsed(self) -> None:
        fenced = "```json\n" + json.dumps(GOOD) + "\n```"
        out = await extract_entities(SOURCE, router=_router_returning(fenced))
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_think_block_stripped(self) -> None:
        raw = "<think>reasoning here</think>" + json.dumps(GOOD)
        out = await extract_entities(SOURCE, router=_router_returning(raw))
        assert len(out) == 3


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_type_item_skipped_not_fatal(self) -> None:
        payload = GOOD + [{"name": "Thing", "type": "alien", "span": "MyMem"}]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_type_normalized_to_lowercase(self) -> None:
        payload = [{"name": "Sarah Chen", "type": "Person", "span": "Sarah Chen leads"}]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert out[0].type == "person"

    @pytest.mark.asyncio
    async def test_missing_name_skipped(self) -> None:
        payload = [{"type": "person", "span": "Sarah Chen"}] + GOOD[:1]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self) -> None:
        out = await extract_entities(SOURCE, router=_router_returning("not json at all"))
        assert out == []

    @pytest.mark.asyncio
    async def test_empty_source_returns_empty_without_llm(self) -> None:
        called = False

        async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            nonlocal called
            called = True
            return "[]"

        out = await extract_entities("   ", router=ModelRouter(llm_fn=fake_llm))
        assert out == []
        assert called is False


class TestSpanGrounding:
    @pytest.mark.asyncio
    async def test_hallucinated_entity_filtered(self) -> None:
        payload = GOOD + [
            {"name": "Quantum Blockchain", "type": "concept",
             "span": "quantum blockchain synergy layer"}
        ]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert all(e.name != "Quantum Blockchain" for e in out)

    @pytest.mark.asyncio
    async def test_paraphrased_name_grounded_by_span_passes(self) -> None:
        # Name not verbatim in source, but span is — grounding via span
        payload = [{"name": "Retrieval Augmented Generation (RAG)", "type": "concept",
                    "span": "Retrieval-Augmented Generation to answer questions"}]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert len(out) == 1


class TestDedupAndCaps:
    @pytest.mark.asyncio
    async def test_duplicate_names_deduped_case_insensitive(self) -> None:
        payload = [
            {"name": "MyMem", "type": "project", "span": "built MyMem"},
            {"name": "mymem", "type": "concept", "span": "built MyMem"},
        ]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert len(out) == 1
        assert out[0].type == "project"  # first occurrence wins

    @pytest.mark.asyncio
    async def test_max_entities_cap(self) -> None:
        out = await extract_entities(
            SOURCE, router=_router_returning(GOOD), max_entities=2
        )
        assert len(out) == 2


def test_extracted_entity_immutable() -> None:
    e = ExtractedEntity(name="X", type="concept", description="", span="")
    with pytest.raises(AttributeError):
        e.name = "Y"  # type: ignore[misc]


class TestEdgeBranches:
    @pytest.mark.asyncio
    async def test_json_object_not_array_returns_empty(self) -> None:
        out = await extract_entities(SOURCE, router=_router_returning({"oops": "dict"}))
        assert out == []

    @pytest.mark.asyncio
    async def test_ungrounded_without_span_filtered(self) -> None:
        payload = [{"name": "Quantum Blockchain", "type": "concept"}]  # no span at all
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert out == []

    @pytest.mark.asyncio
    async def test_whitespace_only_name_skipped(self) -> None:
        payload = [{"name": "   ", "type": "concept", "span": "MyMem"}]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert out == []

    @pytest.mark.asyncio
    async def test_name_absent_but_span_verbatim_passes(self) -> None:
        payload = [{"name": "Knowledge Synthesis Engine", "type": "system",
                    "span": "built MyMem, a personal wiki system"}]
        out = await extract_entities(SOURCE, router=_router_returning(payload))
        assert len(out) == 1
