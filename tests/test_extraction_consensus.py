"""
Tests for extraction consensus eval.
No live LLM calls — router/llm_fn is always mocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mymem.evals.extraction_consensus import (
    ExtractionConsensusResult,
    IdeaMatch,
    _grade,
    _match_ideas,
    _parse_reference_ideas,
    score_consensus,
)
from mymem.evals.store import latest_runs, save_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PIPELINE_IDEAS = [
    {"title": "Transformer Architecture", "summary": "Encoder-decoder model using self-attention instead of recurrence."},
    {"title": "Multi-Head Attention",     "summary": "Parallel attention heads capture different representation subspaces."},
    {"title": "Positional Encoding",      "summary": "Sine cosine encoding gives the model a sense of token order."},
]

REFERENCE_IDEAS_MATCHING = [
    {"title": "Transformer Model Design",  "summary": "Encoder-decoder architecture built entirely on self-attention mechanisms."},
    {"title": "Multi-Head Attention Layer","summary": "Multiple attention heads run in parallel over different representation subspaces."},
    {"title": "Positional Embeddings",     "summary": "Sine and cosine functions encode position of each token in the sequence."},
]

REFERENCE_IDEAS_DIVERGENT = [
    {"title": "Feed-Forward Sublayers",    "summary": "Position-wise feed-forward network applied after each attention layer."},
    {"title": "Layer Normalisation",       "summary": "Normalisation applied around every sub-layer using residual connections."},
    {"title": "Label Smoothing Technique", "summary": "Regularisation approach that softens one-hot targets during training."},
]

REFERENCE_WITH_THESIS = [
    {"title": "Transformer Architecture", "summary": "Encoder-decoder using only self-attention.", "main_thesis": True},
    {"title": "Multi-Head Attention",     "summary": "Parallel heads over subspaces.", "main_thesis": False},
]


# ---------------------------------------------------------------------------
# _parse_reference_ideas
# ---------------------------------------------------------------------------

class TestParseReferenceIdeas:
    def test_valid_json_array(self):
        raw = json.dumps(PIPELINE_IDEAS)
        ideas = _parse_reference_ideas(raw)
        assert len(ideas) == 3
        assert ideas[0]["title"] == "Transformer Architecture"

    def test_strips_code_fence(self):
        raw = "```json\n" + json.dumps(PIPELINE_IDEAS) + "\n```"
        ideas = _parse_reference_ideas(raw)
        assert len(ideas) == 3

    def test_strips_think_tags(self):
        raw = "<think>reasoning here</think>\n" + json.dumps(PIPELINE_IDEAS)
        ideas = _parse_reference_ideas(raw)
        assert len(ideas) == 3

    def test_empty_returns_empty(self):
        assert _parse_reference_ideas("") == []

    def test_invalid_json_returns_empty(self):
        assert _parse_reference_ideas("not json at all") == []

    def test_nested_dict_with_list_value(self):
        raw = json.dumps({"ideas": PIPELINE_IDEAS})
        ideas = _parse_reference_ideas(raw)
        assert len(ideas) == 3


# ---------------------------------------------------------------------------
# _match_ideas
# ---------------------------------------------------------------------------

class TestMatchIdeas:
    def test_similar_ideas_match(self):
        matches = _match_ideas(PIPELINE_IDEAS, REFERENCE_IDEAS_MATCHING)
        assert all(m.matched for m in matches), f"Expected all matched, got: {matches}"

    def test_divergent_ideas_do_not_match(self):
        matches = _match_ideas(PIPELINE_IDEAS, REFERENCE_IDEAS_DIVERGENT)
        matched_count = sum(1 for m in matches if m.matched)
        assert matched_count == 0

    def test_empty_reference(self):
        matches = _match_ideas(PIPELINE_IDEAS, [])
        assert matches == []

    def test_empty_pipeline(self):
        matches = _match_ideas([], REFERENCE_IDEAS_MATCHING)
        assert matches == []

    def test_match_uses_best_reference(self):
        # pipeline idea should match against the most similar reference, not just first
        pipeline = [{"title": "Positional Encoding", "summary": "Sine cosine token position encoding."}]
        reference = [
            {"title": "Unrelated Topic", "summary": "Something completely different."},
            {"title": "Positional Embeddings", "summary": "Sine cosine functions encode token position in sequence."},
        ]
        matches = _match_ideas(pipeline, reference)
        assert len(matches) == 1
        assert matches[0].matched


# ---------------------------------------------------------------------------
# score_consensus
# ---------------------------------------------------------------------------

class TestScoreConsensus:
    def test_perfect_consensus(self):
        result = score_consensus(
            source_id="test-source",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_MATCHING,
        )
        assert result.consensus_score >= 0.6
        assert result.grade in ("PASS", "WARN")

    def test_zero_consensus(self):
        result = score_consensus(
            source_id="test-source",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_DIVERGENT,
        )
        assert result.consensus_score == pytest.approx(0.0)
        assert result.grade == "FAIL"
        assert len(result.gaps) == 3  # all reference ideas are gaps

    def test_thesis_captured_flag(self):
        result = score_consensus(
            source_id="test",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_WITH_THESIS,
        )
        assert result.thesis_captured is True

    def test_thesis_not_captured(self):
        result = score_consensus(
            source_id="test",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=[{"title": "Minor Detail", "summary": "An unrelated footnote."}],
            reference_ideas=REFERENCE_WITH_THESIS,
        )
        assert result.thesis_captured is False

    def test_gaps_are_unmatched_reference_titles(self):
        result = score_consensus(
            source_id="test",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=[PIPELINE_IDEAS[0]],   # only first pipeline idea
            reference_ideas=REFERENCE_IDEAS_DIVERGENT,
        )
        assert len(result.gaps) == len(REFERENCE_IDEAS_DIVERGENT)

    def test_false_positives_are_unmatched_pipeline_titles(self):
        result = score_consensus(
            source_id="test",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_DIVERGENT,
        )
        assert len(result.false_positives) == len(PIPELINE_IDEAS)

    def test_result_is_frozen(self):
        result = score_consensus(
            source_id="test",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="groq-llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_MATCHING,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.consensus_score = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _grade
# ---------------------------------------------------------------------------

class TestGrade:
    def test_pass_high_score_and_thesis(self):
        assert _grade(consensus_score=0.8, thesis_captured=True) == "PASS"

    def test_warn_high_score_no_thesis(self):
        assert _grade(consensus_score=0.8, thesis_captured=False) == "WARN"

    def test_warn_low_score_with_thesis(self):
        assert _grade(consensus_score=0.4, thesis_captured=True) == "WARN"

    def test_fail_low_score_no_thesis(self):
        assert _grade(consensus_score=0.3, thesis_captured=False) == "FAIL"

    def test_boundary_067(self):
        # exactly at threshold: PASS requires >= 0.67
        assert _grade(consensus_score=0.67, thesis_captured=True) == "PASS"
        assert _grade(consensus_score=0.66, thesis_captured=True) == "WARN"


# ---------------------------------------------------------------------------
# run_extraction_consensus (mocked LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_extraction_consensus_mocked():
    from mymem.evals.extraction_consensus import run_extraction_consensus

    async def mock_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return json.dumps(REFERENCE_IDEAS_MATCHING)

    result = await run_extraction_consensus(
        source_id="mock-article",
        source_type="article",
        source_text="The Transformer model uses self-attention instead of recurrence.",
        pipeline_ideas=PIPELINE_IDEAS,
        pipeline_model="claude-sonnet-4-6",
        reference_model="llama-3.3-70b-versatile",
        llm_fn=mock_llm,
    )
    assert isinstance(result, ExtractionConsensusResult)
    assert result.consensus_score >= 0.5
    assert result.pipeline_model == "claude-sonnet-4-6"
    assert result.reference_model == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
async def test_run_extraction_consensus_llm_failure():
    from mymem.evals.extraction_consensus import run_extraction_consensus

    async def failing_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        raise RuntimeError("API unreachable")

    result = await run_extraction_consensus(
        source_id="fail-source",
        source_type="article",
        source_text="Some source text.",
        pipeline_ideas=PIPELINE_IDEAS,
        pipeline_model="sonnet",
        reference_model="groq",
        llm_fn=failing_llm,
    )
    # should return a zero-score skipped result, never raise
    assert result.grade == "FAIL"
    assert result.consensus_score == 0.0


@pytest.mark.asyncio
async def test_run_extraction_consensus_empty_pipeline():
    from mymem.evals.extraction_consensus import run_extraction_consensus

    async def mock_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return json.dumps(REFERENCE_IDEAS_MATCHING)

    result = await run_extraction_consensus(
        source_id="empty-pipeline",
        source_type="article",
        source_text="Some text.",
        pipeline_ideas=[],
        pipeline_model="sonnet",
        reference_model="groq",
        llm_fn=mock_llm,
    )
    assert result.consensus_score == 0.0
    assert result.grade == "FAIL"


# ---------------------------------------------------------------------------
# store integration
# ---------------------------------------------------------------------------

class TestExtractionConsensusStore:
    def test_save_and_retrieve(self, tmp_path):
        from mymem.evals.store import save_extraction_consensus, recent_consensus_runs

        db = tmp_path / "evals.db"
        result = score_consensus(
            source_id="test-article.md",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_MATCHING,
        )
        save_extraction_consensus(db, result)
        runs = recent_consensus_runs(db, limit=5)
        assert len(runs) == 1
        assert runs[0]["source_id"] == "test-article.md"
        assert runs[0]["grade"] in ("PASS", "WARN", "FAIL")
        assert runs[0]["full_result"]["source_id"] == "test-article.md"
        assert len(runs[0]["full_result"]["pipeline_ideas"]) == len(PIPELINE_IDEAS)

    def test_worst_first_ordering(self, tmp_path):
        from mymem.evals.store import save_extraction_consensus, recent_consensus_runs

        db = tmp_path / "evals.db"
        good = score_consensus("good", "article", "s", "g", PIPELINE_IDEAS, REFERENCE_IDEAS_MATCHING)
        bad  = score_consensus("bad",  "article", "s", "g", PIPELINE_IDEAS, REFERENCE_IDEAS_DIVERGENT)
        save_extraction_consensus(db, good)
        save_extraction_consensus(db, bad)

        runs = recent_consensus_runs(db, limit=10, order="worst_first")
        assert runs[0]["source_id"] == "bad"

    def test_missing_db_returns_empty(self, tmp_path):
        from mymem.evals.store import recent_consensus_runs
        runs = recent_consensus_runs(tmp_path / "nonexistent.db")
        assert runs == []

    def test_existing_schema_gets_full_result_column(self, tmp_path):
        import sqlite3
        from mymem.evals.store import recent_consensus_runs

        db = tmp_path / "old.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE extraction_consensus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    pipeline_model TEXT NOT NULL,
                    reference_model TEXT NOT NULL,
                    consensus_score REAL NOT NULL,
                    thesis_captured INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    gaps_json TEXT NOT NULL,
                    false_pos_json TEXT NOT NULL
                )"""
            )
            conn.execute(
                """INSERT INTO extraction_consensus
                   (run_at, source_id, source_type, pipeline_model, reference_model,
                    consensus_score, thesis_captured, grade, gaps_json, false_pos_json)
                   VALUES ('2026-06-09T00:00:00+00:00', 'old', 'article', 'p', 'r',
                           0.0, 0, 'FAIL', '[]', '[]')"""
            )

        runs = recent_consensus_runs(db)

        assert runs[0]["source_id"] == "old"
        assert runs[0]["full_result"] == {}
