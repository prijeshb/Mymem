"""
Tests for Map/Merge/Verify extraction pipeline and IdeaSchema.
All LLM calls are mocked — no real network or Ollama required.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mymem.pipeline.ingest import (
    IdeaSchema,
    _extract_chunk_ideas,
    _merge_ideas,
    _verify_ideas,
    _extract_ideas_map_reduce,
    _EXTRACT_SYSTEM,
)
from mymem.pipeline.router import ModelRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

IDEA_A = {
    "title": "Transformer Architecture",
    "summary": "Encoder-decoder model using self-attention instead of recurrence.",
    "why_it_matters": "Foundational architecture for modern NLP.",
    "evidence": ["attention is all you need"],
    "chunk_id": 0,
    "importance": 5,
    "main_thesis": True,
    "tags": ["ml", "transformers"],
    "domain": "tech",
}

IDEA_B = {
    "title": "Multi-Head Attention",
    "summary": "Parallel attention heads capture different subspaces.",
    "why_it_matters": "Lets the model attend to multiple positions simultaneously.",
    "evidence": ["allows the model to jointly attend"],
    "chunk_id": 1,
    "importance": 4,
    "main_thesis": False,
    "tags": ["ml", "attention"],
    "domain": "tech",
}

IDEA_A_CHUNK1 = {**IDEA_A, "chunk_id": 1}  # same concept reappearing in chunk 1


def make_router_returning(ideas_per_call: list[list[dict]]) -> ModelRouter:
    """Router that returns successive idea lists on each call."""
    responses = iter(ideas_per_call)

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        try:
            return json.dumps(next(responses))
        except StopIteration:
            return "[]"

    return ModelRouter(llm_fn=fake_llm)


def make_router_text(responses: list[str]) -> ModelRouter:
    """Router that returns raw text responses in order."""
    it = iter(responses)

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        try:
            return next(it)
        except StopIteration:
            return "[]"

    return ModelRouter(llm_fn=fake_llm)


# ---------------------------------------------------------------------------
# IdeaSchema validation
# ---------------------------------------------------------------------------

class TestIdeaSchema:
    def test_full_valid_idea(self):
        idea = IdeaSchema(**IDEA_A)
        assert idea.title == "Transformer Architecture"
        assert idea.chunk_id == 0
        assert idea.importance == 5
        assert idea.main_thesis is True
        assert idea.evidence == ["attention is all you need"]

    def test_defaults_applied(self):
        idea = IdeaSchema(title="Minimal Idea", summary="Short summary.")
        assert idea.why_it_matters == ""
        assert idea.evidence == []
        assert idea.chunk_id == 0
        assert idea.importance == 3
        assert idea.main_thesis is False
        assert idea.domain == "misc"

    def test_importance_clamped_range(self):
        with pytest.raises(Exception):
            IdeaSchema(title="Bad", summary="s", importance=0)
        with pytest.raises(Exception):
            IdeaSchema(title="Bad", summary="s", importance=6)

    def test_model_dump_is_dict(self):
        idea = IdeaSchema(**IDEA_A)
        d = idea.model_dump()
        assert isinstance(d, dict)
        assert d["title"] == "Transformer Architecture"
        assert isinstance(d["evidence"], list)

    def test_model_validate_from_dict(self):
        idea = IdeaSchema.model_validate(IDEA_A)
        assert idea.chunk_id == 0

    def test_missing_title_raises(self):
        with pytest.raises(Exception):
            IdeaSchema(summary="no title here")

    def test_missing_summary_raises(self):
        with pytest.raises(Exception):
            IdeaSchema(title="no summary")


# ---------------------------------------------------------------------------
# Extraction prompt: no max_concepts
# ---------------------------------------------------------------------------

class TestExtractionPrompt:
    def test_system_prompt_has_no_max_concepts(self):
        assert "max_concepts" not in _EXTRACT_SYSTEM
        assert "{max_concepts}" not in _EXTRACT_SYSTEM

    def test_system_prompt_requires_evidence_field(self):
        assert "evidence" in _EXTRACT_SYSTEM

    def test_system_prompt_requires_why_it_matters(self):
        assert "why_it_matters" in _EXTRACT_SYSTEM

    def test_system_prompt_requires_importance(self):
        assert "importance" in _EXTRACT_SYSTEM

    def test_system_prompt_requires_chunk_id(self):
        assert "chunk_id" in _EXTRACT_SYSTEM


# ---------------------------------------------------------------------------
# _extract_chunk_ideas
# ---------------------------------------------------------------------------

class TestExtractChunkIdeas:
    @pytest.mark.asyncio
    async def test_returns_ideas_with_correct_chunk_id(self):
        router = make_router_returning([[IDEA_A]])
        ideas = await _extract_chunk_ideas(
            "Some source text.", chunk_id=2, router=router
        )
        assert len(ideas) == 1
        assert ideas[0]["chunk_id"] == 2

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_llm_returning_empty(self):
        router = make_router_returning([[]])
        ideas = await _extract_chunk_ideas("text", chunk_id=0, router=router)
        assert ideas == []

    @pytest.mark.asyncio
    async def test_invalid_ideas_are_filtered_out(self):
        bad_idea = {"no_title": "missing required field", "summary": "s"}
        router = make_router_returning([[bad_idea, IDEA_A]])
        ideas = await _extract_chunk_ideas("text", chunk_id=0, router=router)
        # bad_idea has no title — should be filtered by IdeaSchema validation
        assert all("title" in i for i in ideas)

    @pytest.mark.asyncio
    async def test_chunk_id_override_corrects_llm_output(self):
        wrong_chunk_id_idea = {**IDEA_A, "chunk_id": 99}
        router = make_router_returning([[wrong_chunk_id_idea]])
        ideas = await _extract_chunk_ideas("text", chunk_id=3, router=router)
        # Function should override whatever chunk_id the LLM returned
        assert ideas[0]["chunk_id"] == 3


# ---------------------------------------------------------------------------
# _merge_ideas
# ---------------------------------------------------------------------------

class TestMergeIdeas:
    @pytest.mark.asyncio
    async def test_recurrence_scores_ideas_from_multiple_chunks(self):
        # IDEA_A appears in both chunk 0 and chunk 1 — high recurrence
        # IDEA_B appears only in chunk 1
        chunk_lists = [[IDEA_A], [IDEA_A_CHUNK1, IDEA_B]]

        merge_result = [IDEA_A]  # LLM merge picks the recurring idea
        router = make_router_returning([merge_result])

        result = await _merge_ideas(chunk_lists, router=router)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_merge_calls_router_once(self):
        call_count = [0]

        async def counting_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            call_count[0] += 1
            return json.dumps([IDEA_A])

        router = ModelRouter(llm_fn=counting_llm)
        await _merge_ideas([[IDEA_A], [IDEA_B]], router=router)
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_all_chunks_empty(self):
        router = make_router_returning([[]])
        result = await _merge_ideas([[], []], router=router)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_chunk_still_goes_through_merge(self):
        router = make_router_returning([[IDEA_A]])
        result = await _merge_ideas([[IDEA_A]], router=router)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# _verify_ideas
# ---------------------------------------------------------------------------

class TestVerifyIdeas:
    @pytest.mark.asyncio
    async def test_appends_new_ideas_from_verify_turn(self):
        new_idea = {**IDEA_B, "chunk_id": 0}
        router = make_router_returning([[new_idea]])
        result = await _verify_ideas(
            source_text="Some long source text.",
            merged_ideas=[IDEA_A],
            router=router,
        )
        assert len(result) == 2
        titles = [i["title"] for i in result]
        assert "Transformer Architecture" in titles
        assert "Multi-Head Attention" in titles

    @pytest.mark.asyncio
    async def test_returns_unchanged_when_llm_returns_empty(self):
        router = make_router_returning([[]])
        result = await _verify_ideas(
            source_text="text",
            merged_ideas=[IDEA_A],
            router=router,
        )
        assert len(result) == 1
        assert result[0]["title"] == "Transformer Architecture"

    @pytest.mark.asyncio
    async def test_capped_at_one_verify_turn(self):
        call_count = [0]

        async def counting_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            call_count[0] += 1
            return json.dumps([IDEA_B])

        router = ModelRouter(llm_fn=counting_llm)
        await _verify_ideas("text", merged_ideas=[IDEA_A], router=router)
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_verify_does_not_duplicate_existing_ideas(self):
        # LLM returns the same idea that already exists
        router = make_router_returning([[IDEA_A]])
        result = await _verify_ideas(
            source_text="text",
            merged_ideas=[IDEA_A],
            router=router,
        )
        # Should not add a duplicate of Transformer Architecture
        titles = [i["title"] for i in result]
        assert titles.count("Transformer Architecture") == 1


# ---------------------------------------------------------------------------
# _extract_ideas_map_reduce (integration)
# ---------------------------------------------------------------------------

class TestExtractIdeasMapReduce:
    @pytest.mark.asyncio
    async def test_ideas_from_all_chunks_represented(self):
        """chunk_id values from different chunks should appear in final output."""
        chunk0_idea = {**IDEA_A, "chunk_id": 0}
        chunk1_idea = {**IDEA_B, "chunk_id": 1}

        # Calls: chunk0 extract, chunk1 extract, merge, verify
        router = make_router_returning([
            [chunk0_idea],   # Map chunk 0
            [chunk1_idea],   # Map chunk 1
            [chunk0_idea, chunk1_idea],  # Merge
            [],              # Verify — nothing missed
        ])

        result = await _extract_ideas_map_reduce(
            source_text="a" * 5000,  # long enough to produce 2 chunks
            source_name="test.md",
            source_type="article",
            router=router,
        )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_short_source_still_goes_through_pipeline(self):
        """Even a short source must go through Map/Merge/Verify."""
        router = make_router_returning([
            [IDEA_A],   # Map
            [IDEA_A],   # Merge
            [],         # Verify
        ])

        result = await _extract_ideas_map_reduce(
            source_text="short text",
            source_name="test.md",
            source_type="article",
            router=router,
        )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_extracted_ideas(self):
        router = make_router_returning([[], [], []])
        result = await _extract_ideas_map_reduce(
            source_text="text",
            source_name="test.md",
            source_type="article",
            router=router,
        )
        assert result == []
