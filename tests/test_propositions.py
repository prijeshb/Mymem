"""Tests for ADR-011 Phase 1 — verbatim source spans + grounding.

Extraction now emits a `source_span` per idea; `_ground_span` mechanically
verifies it is actually present in the source (anti-hallucination, ADR-011 D2),
blanking hallucinated spans while keeping the idea.
"""

from __future__ import annotations

from mymem.pipeline.ingest import (
    IdeaSchema,
    _ground_idea_spans,
    _ground_span,
    _preserve_spans,
)

# ---------------------------------------------------------------------------
# IdeaSchema field
# ---------------------------------------------------------------------------

class TestIdeaSchemaSpan:
    def test_source_span_defaults_empty(self):
        idea = IdeaSchema(title="T", summary="S")
        assert idea.source_span == ""

    def test_source_span_accepted(self):
        idea = IdeaSchema(title="T", summary="S", source_span="a verbatim quote")
        assert idea.source_span == "a verbatim quote"


# ---------------------------------------------------------------------------
# _ground_span
# ---------------------------------------------------------------------------

SOURCE = (
    "Self-attention lets every token attend to every other token in the sequence. "
    "Multi-head attention runs several attention operations in parallel."
)


class TestGroundSpan:
    def test_exact_substring_kept(self):
        span = "every token attend to every other token"
        assert _ground_span(span, SOURCE) == span

    def test_whitespace_and_case_variant_kept(self):
        # Reformatted whitespace + casing still grounds (normalized match).
        span = "Every   token   attend\nto every other token"
        assert _ground_span(span, SOURCE) == span

    def test_near_verbatim_kept_via_fuzzy(self):
        # One-character drift (typo) still grounds via rapidfuzz partial_ratio.
        span = "Multi-head attention runs several attention operatons in parallel"
        assert _ground_span(span, SOURCE) == span

    def test_hallucinated_span_blanked(self):
        assert _ground_span("transformers were invented in 1995", SOURCE) == ""

    def test_empty_span_blanked(self):
        assert _ground_span("", SOURCE) == ""
        assert _ground_span("   ", SOURCE) == ""


# ---------------------------------------------------------------------------
# _ground_idea_spans
# ---------------------------------------------------------------------------

class TestGroundIdeaSpans:
    def test_blanks_only_ungrounded_spans(self):
        ideas = [
            {"title": "A", "source_span": "every other token in the sequence"},
            {"title": "B", "source_span": "a fact never stated in the source"},
        ]
        out = _ground_idea_spans(ideas, SOURCE)
        assert out[0]["source_span"] == "every other token in the sequence"
        assert out[1]["source_span"] == ""

    def test_is_immutable(self):
        ideas = [{"title": "A", "source_span": "made up"}]
        out = _ground_idea_spans(ideas, SOURCE)
        assert ideas[0]["source_span"] == "made up"   # original untouched
        assert out[0]["source_span"] == ""

    def test_missing_span_key_becomes_empty(self):
        out = _ground_idea_spans([{"title": "A"}], SOURCE)
        assert out[0]["source_span"] == ""
        assert out[0]["title"] == "A"


# ---------------------------------------------------------------------------
# _preserve_spans — recover provenance the merge LLM dropped (ADR-015 D3)
# ---------------------------------------------------------------------------

class TestPreserveSpans:
    def test_recovers_blank_span_from_candidate(self):
        merged = [{"title": "Self-Attention", "source_span": ""}]
        candidates = [{"title": "self-attention", "source_span": "a grounded quote"}]
        out = _preserve_spans(merged, candidates)
        assert out[0]["source_span"] == "a grounded quote"

    def test_keeps_existing_merged_span(self):
        merged = [{"title": "A", "source_span": "merge kept this"}]
        candidates = [{"title": "A", "source_span": "candidate quote"}]
        out = _preserve_spans(merged, candidates)
        assert out[0]["source_span"] == "merge kept this"

    def test_no_candidate_match_stays_blank(self):
        merged = [{"title": "A", "source_span": ""}]
        candidates = [{"title": "B", "source_span": "unrelated"}]
        out = _preserve_spans(merged, candidates)
        assert out[0]["source_span"] == ""

    def test_first_grounded_candidate_wins(self):
        merged = [{"title": "A"}]
        candidates = [
            {"title": "A", "source_span": ""},          # ungrounded — skip
            {"title": "A", "source_span": "best quote"},  # first grounded — use
            {"title": "A", "source_span": "later quote"},
        ]
        out = _preserve_spans(merged, candidates)
        assert out[0]["source_span"] == "best quote"

    def test_is_immutable(self):
        merged = [{"title": "A", "source_span": ""}]
        candidates = [{"title": "A", "source_span": "quote"}]
        out = _preserve_spans(merged, candidates)
        assert merged[0]["source_span"] == ""   # original untouched
        assert out[0]["source_span"] == "quote"
