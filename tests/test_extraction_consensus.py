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
    _compute_duplicate_rate,
    _compute_evidence_support_rate,
    _grade,
    _match_ideas,
    _match_ideas_semantic,
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
# _compute_evidence_support_rate
# ---------------------------------------------------------------------------

class TestComputeEvidenceSupportRate:
    def test_all_with_evidence(self):
        ideas = [
            {"title": "A", "evidence": ["quote 1"]},
            {"title": "B", "evidence": ["quote 2", "quote 3"]},
        ]
        assert _compute_evidence_support_rate(ideas) == pytest.approx(1.0)

    def test_none_with_evidence(self):
        ideas = [{"title": "A"}, {"title": "B"}]
        assert _compute_evidence_support_rate(ideas) == pytest.approx(0.0)

    def test_partial_evidence(self):
        ideas = [
            {"title": "A", "evidence": ["quote"]},
            {"title": "B", "evidence": []},
            {"title": "C"},
        ]
        rate = _compute_evidence_support_rate(ideas)
        assert rate == pytest.approx(1 / 3, abs=0.001)

    def test_empty_ideas_returns_zero(self):
        assert _compute_evidence_support_rate([]) == pytest.approx(0.0)

    def test_single_idea_with_evidence(self):
        assert _compute_evidence_support_rate([{"title": "A", "evidence": ["x"]}]) == pytest.approx(1.0)

    def test_evidence_not_list_not_counted(self):
        ideas = [{"title": "A", "evidence": "not a list"}]
        assert _compute_evidence_support_rate(ideas) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _compute_duplicate_rate
# ---------------------------------------------------------------------------

IDEAS_WITH_DUPLICATES = [
    {"title": "Transformer Architecture", "summary": "Encoder-decoder model with self-attention replacing recurrence."},
    {"title": "Transformer Model", "summary": "Encoder-decoder model using self-attention instead of recurrence."},
    {"title": "Positional Encoding", "summary": "Sine cosine functions encode token position in sequence."},
]

class TestComputeDuplicateRate:
    def test_identical_ideas_have_high_duplicate_rate(self):
        ideas = [
            {"title": "Same Title", "summary": "Same summary text used for both ideas."},
            {"title": "Same Title", "summary": "Same summary text used for both ideas."},
        ]
        rate = _compute_duplicate_rate(ideas)
        assert rate == pytest.approx(1.0)

    def test_fully_distinct_ideas_have_zero_duplicate_rate(self):
        ideas = [
            {"title": "Transformer Architecture", "summary": "Self-attention based encoder-decoder."},
            {"title": "Gradient Descent", "summary": "Optimization algorithm using partial derivatives."},
        ]
        rate = _compute_duplicate_rate(ideas)
        assert rate == pytest.approx(0.0)

    def test_single_idea_returns_zero(self):
        assert _compute_duplicate_rate([{"title": "A", "summary": "s"}]) == pytest.approx(0.0)

    def test_empty_ideas_returns_zero(self):
        assert _compute_duplicate_rate([]) == pytest.approx(0.0)

    def test_near_duplicate_ideas_detected(self):
        rate = _compute_duplicate_rate(IDEAS_WITH_DUPLICATES)
        # First two ideas are near-duplicates; third is distinct
        # 1 duplicate pair out of 3 total = 0.333
        assert rate > 0.0


# ---------------------------------------------------------------------------
# _grade (with evidence_support_rate third param)
# ---------------------------------------------------------------------------

class TestGrade:
    def test_pass_requires_all_three_conditions(self):
        # All three: score >= 0.67, thesis=True, evidence_support_rate >= 0.80
        assert _grade(0.8, True, evidence_support_rate=0.90) == "PASS"

    def test_pass_fails_if_evidence_support_too_low(self):
        assert _grade(0.8, True, evidence_support_rate=0.70) == "WARN"

    def test_pass_fails_if_no_thesis(self):
        assert _grade(0.8, False, evidence_support_rate=0.90) == "WARN"

    def test_pass_fails_if_score_too_low(self):
        assert _grade(0.60, True, evidence_support_rate=0.90) == "WARN"

    def test_warn_on_score_above_050_no_thesis(self):
        assert _grade(0.55, False, evidence_support_rate=0.90) == "WARN"

    def test_warn_on_thesis_alone(self):
        assert _grade(0.3, True, evidence_support_rate=0.90) == "WARN"

    def test_fail_low_score_no_thesis(self):
        assert _grade(0.3, False, evidence_support_rate=0.90) == "FAIL"

    def test_boundary_067_with_high_evidence(self):
        assert _grade(0.67, True, evidence_support_rate=0.80) == "PASS"
        assert _grade(0.66, True, evidence_support_rate=0.80) == "WARN"

    def test_default_evidence_support_is_one(self):
        # Backward-compatible: default evidence_support_rate=1.0 still passes
        assert _grade(0.8, True) == "PASS"


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

    def test_existing_schema_gets_evidence_and_duplicate_columns(self, tmp_path):
        """DB with only full_result_json but missing the two new columns gets migrated."""
        import sqlite3
        from mymem.evals.store import save_extraction_consensus, recent_consensus_runs

        db = tmp_path / "v1.db"
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
                    false_pos_json TEXT NOT NULL,
                    full_result_json TEXT NOT NULL DEFAULT '{}'
                )"""
            )
            conn.execute(
                """INSERT INTO extraction_consensus
                   (run_at, source_id, source_type, pipeline_model, reference_model,
                    consensus_score, thesis_captured, grade, gaps_json, false_pos_json)
                   VALUES ('2026-06-09T00:00:00+00:00', 'legacy', 'article', 'p', 'r',
                           0.5, 1, 'WARN', '[]', '[]')"""
            )

        # recent_consensus_runs triggers _ensure_consensus which runs the migration
        runs = recent_consensus_runs(db)
        assert runs[0]["source_id"] == "legacy"

        # After migration, can save a new result with the new fields without error
        result = score_consensus(
            source_id="new-article.md",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="llama",
            pipeline_ideas=PIPELINE_IDEAS,
            reference_ideas=REFERENCE_IDEAS_MATCHING,
        )
        save_extraction_consensus(db, result)  # must not raise

        runs = recent_consensus_runs(db, limit=10)
        assert any(r["source_id"] == "new-article.md" for r in runs)

    def test_save_stores_evidence_and_duplicate_rates(self, tmp_path):
        from mymem.evals.store import save_extraction_consensus, recent_consensus_runs

        db = tmp_path / "evals.db"
        ideas_with_evidence = [
            {"title": "A", "summary": "s", "evidence": ["quote 1"]},
            {"title": "B", "summary": "s", "evidence": ["quote 2"]},
        ]
        result = score_consensus(
            source_id="evidence-test.md",
            source_type="article",
            pipeline_model="sonnet",
            reference_model="llama",
            pipeline_ideas=ideas_with_evidence,
            reference_ideas=REFERENCE_IDEAS_MATCHING,
        )
        save_extraction_consensus(db, result)
        runs = recent_consensus_runs(db, limit=5)

        assert runs[0]["evidence_support_rate"] == pytest.approx(1.0)
        assert "duplicate_rate" in runs[0]


# ---------------------------------------------------------------------------
# _match_ideas_semantic (async embedding-cosine matching)
# ---------------------------------------------------------------------------

class TestMatchIdeasSemantic:
    @pytest.mark.asyncio
    async def test_semantic_match_uses_embed_fn(self):
        """High cosine similarity → matched=True at EMBED_MATCH_THRESHOLD=0.78."""
        from mymem.evals.extraction_consensus import EMBED_MATCH_THRESHOLD

        async def mock_embed(texts: list[str]) -> list[list[float]]:
            # Return unit vectors: first two texts get the same direction → cosine=1.0
            # third text gets orthogonal vector → cosine=0.0
            vecs = []
            for t in texts:
                if "Transformer" in t or "Multi-Head" in t:
                    vecs.append([1.0, 0.0, 0.0])
                else:
                    vecs.append([0.0, 1.0, 0.0])
            return vecs

        pipeline = [{"title": "Transformer Architecture", "summary": "Self-attention model."}]
        reference = [{"title": "Transformer Model", "summary": "Encoder-decoder with self-attention."}]

        matches = await _match_ideas_semantic(pipeline, reference, mock_embed)
        assert len(matches) == 1
        assert matches[0].matched  # cosine=1.0 >= 0.78

    @pytest.mark.asyncio
    async def test_semantic_low_similarity_not_matched(self):
        async def mock_embed(texts: list[str]) -> list[list[float]]:
            # Orthogonal: "Transformer" family → axis 0; "Gradient" family → axis 1
            vecs = []
            for t in texts:
                if "Transformer" in t:
                    vecs.append([1.0, 0.0, 0.0])
                else:
                    vecs.append([0.0, 1.0, 0.0])
            return vecs

        pipeline = [{"title": "Transformer", "summary": "Self-attention."}]
        reference = [{"title": "Gradient Descent", "summary": "Optimization method."}]

        matches = await _match_ideas_semantic(pipeline, reference, mock_embed)
        assert len(matches) == 1
        assert not matches[0].matched  # cosine([1,0,0], [0,1,0]) = 0.0 < 0.78

    @pytest.mark.asyncio
    async def test_semantic_fallback_on_embed_failure(self):
        """If embed_fn raises, falls back to ROUGE-1 matching."""
        async def broken_embed(texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedder offline")

        matches = await _match_ideas_semantic(
            PIPELINE_IDEAS[:1], REFERENCE_IDEAS_MATCHING[:1], broken_embed
        )
        assert len(matches) == 1  # fell back to ROUGE-1, still returns a result

    @pytest.mark.asyncio
    async def test_semantic_empty_pipeline_returns_empty(self):
        async def mock_embed(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        matches = await _match_ideas_semantic([], REFERENCE_IDEAS_MATCHING, mock_embed)
        assert matches == []

    @pytest.mark.asyncio
    async def test_run_consensus_with_embed_fn(self):
        """run_extraction_consensus uses semantic matching when embed_fn is provided."""
        from mymem.evals.extraction_consensus import run_extraction_consensus

        async def mock_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return json.dumps(REFERENCE_IDEAS_MATCHING)

        call_log: list[list[str]] = []

        async def mock_embed(texts: list[str]) -> list[list[float]]:
            call_log.append(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        result = await run_extraction_consensus(
            source_id="embed-test",
            source_type="article",
            source_text="The Transformer model uses self-attention.",
            pipeline_ideas=PIPELINE_IDEAS,
            pipeline_model="sonnet",
            reference_model="llama",
            llm_fn=mock_llm,
            embed_fn=mock_embed,
        )
        assert isinstance(result, ExtractionConsensusResult)
        # embed_fn should have been called (pipeline + reference texts)
        assert len(call_log) >= 1
