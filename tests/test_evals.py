"""
Tests for the eval framework.
No LLM calls, no embedder, no file system beyond tmp_path.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from mymem.evals.metrics import bm25_score, duplicate_rate, rouge1_f1, tfidf_cosine
from mymem.evals.hope import HopeScore, aggregate_hope, score_chunk, score_chunks
from mymem.evals.chunking import AblationRow, chunk_size_ablation, efficiency_report, optimal_max_tokens
from mymem.evals.ingest_quality import PageScore, WikiQualityReport, score_page, wiki_quality_report
from mymem.evals.confidence import ConfidenceScore, LifecycleState, score_confidence
from mymem.evals.retrieval import CaseResult, RetrievalCase, RetrievalReport, _udcg, load_cases, run_bm25_eval
from mymem.evals.store import latest_runs, save_run
from mymem.wiki.page import write_page
from mymem.wiki.types import TagDomain, WikiPage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(
    title: str = "Test Page",
    body: str = "This is a [[Related Concept]] and another [[Linked Page]].\n\n## Section\n\nMore content here.",
    tags: tuple = ("ml", "ai"),
    domain: TagDomain = TagDomain.TECH,
    sources: tuple = ("source.md",),
    days_old: int = 0,
    path: Path | None = None,
) -> WikiPage:
    updated = date.today() - timedelta(days=days_old)
    return WikiPage(
        title=title,
        body=body,
        path=path or Path(f"wiki/{title.lower().replace(' ', '-')}.md"),
        tags=tags,
        domain=domain,
        sources=sources,
        updated=updated,
        created=updated,
    )


# ---------------------------------------------------------------------------
# metrics.py
# ---------------------------------------------------------------------------

class TestTfidfCosine:
    def test_identical(self):
        assert tfidf_cosine("hello world", "hello world") == pytest.approx(1.0)

    def test_disjoint(self):
        assert tfidf_cosine("apple banana", "orange grape") == pytest.approx(0.0)

    def test_partial_overlap(self):
        score = tfidf_cosine("the cat sat on the mat", "the cat is on the floor")
        assert 0.0 < score < 1.0

    def test_empty_string(self):
        assert tfidf_cosine("", "hello") == 0.0
        assert tfidf_cosine("hello", "") == 0.0


class TestBm25Score:
    def test_relevant_document(self):
        score = bm25_score("machine learning", "machine learning is a subset of artificial intelligence")
        assert score > 0.0

    def test_irrelevant_document(self):
        score = bm25_score("quantum physics", "the cat sat on the mat")
        assert score == pytest.approx(0.0)

    def test_empty_query(self):
        assert bm25_score("", "some document") == 0.0


class TestRouge1F1:
    def test_perfect_match(self):
        assert rouge1_f1("cat sat mat", "cat sat mat") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert rouge1_f1("abc def", "xyz uvw") == pytest.approx(0.0)

    def test_partial(self):
        score = rouge1_f1("the cat sat on the mat", "the cat")
        assert 0.0 < score < 1.0


class TestDuplicateRate:
    def test_no_duplicates(self):
        texts = ["machine learning is great", "stoic philosophy teaches virtue", "compound interest grows wealth"]
        assert duplicate_rate(texts) == pytest.approx(0.0)

    def test_all_duplicates(self):
        texts = ["machine learning", "machine learning", "machine learning"]
        assert duplicate_rate(texts) == pytest.approx(1.0)

    def test_single_text(self):
        assert duplicate_rate(["only one"]) == 0.0


# ---------------------------------------------------------------------------
# hope.py
# ---------------------------------------------------------------------------

class TestHopeScore:
    def test_good_chunk_passes(self):
        chunk = (
            "Machine learning is a branch of artificial intelligence. "
            "It enables systems to learn from data automatically. "
            "Deep learning uses neural networks with many layers."
        )
        score = score_chunk(chunk, max_tokens=1024)
        assert isinstance(score, HopeScore)
        assert score.bi == pytest.approx(1.0)   # ends with period
        assert score.sc >= 0.0                  # within [0, 1] — short chunk may be below min
        assert 0.0 <= score.overall <= 1.0

    def test_dangling_pronoun_lowers_dcc(self):
        chunk = "they said it was good. The results were positive."
        score = score_chunk(chunk)
        assert score.dcc < 1.0

    def test_short_chunk_lowers_sc(self):
        chunk = "Too short."
        score = score_chunk(chunk, max_tokens=1024)
        assert score.sc < 1.0

    def test_ends_mid_sentence_lowers_bi(self):
        chunk = "Machine learning is a field that"
        score = score_chunk(chunk)
        assert score.bi < 0.5

    def test_aggregate_hope(self):
        chunks = ["First chunk ends here.", "Second chunk also ends here."]
        scores = score_chunks(chunks)
        agg = aggregate_hope(scores)
        assert 0.0 <= agg.overall <= 1.0

    def test_grade_values(self):
        high = HopeScore(rc=0.9, icc=0.9, dcc=0.9, bi=0.9, sc=0.9)
        assert high.grade() == "PASS"
        low = HopeScore(rc=0.2, icc=0.2, dcc=0.2, bi=0.2, sc=0.2)
        assert low.grade() == "FAIL"


# ---------------------------------------------------------------------------
# chunking.py
# ---------------------------------------------------------------------------

class TestChunkSizeAblation:
    _SAMPLE = " ".join(["word"] * 3000)  # ~3000 tokens of text

    def test_returns_row_per_size(self):
        rows = chunk_size_ablation(self._SAMPLE, sizes=[256, 512, 1024])
        assert len(rows) == 3
        assert all(isinstance(r, AblationRow) for r in rows)

    def test_larger_max_tokens_fewer_chunks(self):
        rows = chunk_size_ablation(self._SAMPLE, sizes=[256, 1024])
        chunks_256 = rows[0].chunk_count
        chunks_1024 = rows[1].chunk_count
        assert chunks_1024 <= chunks_256

    def test_short_text_single_chunk(self):
        rows = chunk_size_ablation("Short text.", sizes=[1024])
        assert rows[0].chunk_count == 1
        assert rows[0].recommendation in ("OPTIMAL", "GOOD", "OK")


def test_optimal_max_tokens():
    # caps at 1024 regardless of how large the context window is
    assert optimal_max_tokens(8_000) == 1024
    assert optimal_max_tokens(128_000) == 1024
    assert optimal_max_tokens(200_000) == 1024
    # very small windows floor at 512
    assert optimal_max_tokens(1_000) == 512


def test_efficiency_report_missing_db(tmp_path):
    groups = efficiency_report(tmp_path / "nonexistent.db")
    assert groups == []


# ---------------------------------------------------------------------------
# ingest_quality.py
# ---------------------------------------------------------------------------

class TestScorePage:
    def test_rich_page_scores_high(self):
        page = _make_page(
            body=(
                "## Introduction\n\nThis covers [[Attention]] and [[Transformers]].\n\n"
                "## Details\n\nMore content here with [[Self Attention]] concepts.\n\n"
                "## Conclusion\n\nSee also [[Multi Head Attention]] for more.\n"
            ) * 3,
            tags=("ml", "attention", "transformers"),
        )
        s = score_page(page)
        assert s.richness_score >= 5.0
        assert not s.is_stub
        assert not s.no_wikilinks

    def test_stub_detected(self):
        page = _make_page(body="Short.", tags=())
        s = score_page(page)
        assert s.is_stub
        assert s.no_tags

    def test_no_wikilinks_flagged(self):
        page = _make_page(body="## Section\n\nContent with no links at all. " * 20)
        s = score_page(page)
        assert s.no_wikilinks


def test_wiki_quality_report_empty(tmp_path):
    (tmp_path / "index.md").write_text("")
    report = wiki_quality_report(tmp_path)
    assert report.total_pages == 0
    assert report.mean_richness == 0.0


def test_wiki_quality_report_with_pages(tmp_path):
    for i in range(3):
        page = _make_page(
            title=f"Page {i}",
            body=f"## Section\n\nContent [[Link A]] and [[Link B]].\n" * 5,
            path=tmp_path / f"page-{i}.md",
        )
        write_page(page)
    report = wiki_quality_report(tmp_path)
    assert report.total_pages == 3
    assert report.mean_richness > 0


# ---------------------------------------------------------------------------
# confidence.py
# ---------------------------------------------------------------------------

class TestScoreConfidence:
    def test_well_sourced_recent_linked(self):
        page = _make_page(
            sources=("a.md", "b.md", "c.md"),
            body="[[Link1]] [[Link2]] [[Link3]] [[Link4]] [[Link5]]",
            tags=("ml",),
            domain=TagDomain.TECH,
            days_old=0,
        )
        cs = score_confidence(page)
        assert cs.overall >= 0.7
        assert cs.state in (LifecycleState.VERIFIED, LifecycleState.REVIEWED)

    def test_stale_page(self):
        page = _make_page(days_old=100)
        cs = score_confidence(page, stale_days=90)
        assert cs.state == LifecycleState.STALE

    def test_no_sources_lowers_score(self):
        page = _make_page(sources=())
        cs = score_confidence(page)
        assert cs.source_count == 0.0

    def test_archived_page(self):
        page = WikiPage(
            title="Archived", body="old content", archived=True,
            path=Path("wiki/archived.md"),
        )
        cs = score_confidence(page)
        assert cs.state == LifecycleState.ARCHIVED


# ---------------------------------------------------------------------------
# retrieval.py
# ---------------------------------------------------------------------------

class TestUdcg:
    def test_hit_at_rank_1_is_max(self):
        score = _udcg([1], k=5)
        assert score == pytest.approx(1.0)

    def test_no_hits_is_zero(self):
        assert _udcg([None, None], k=5) == 0.0

    def test_lower_rank_lowers_score(self):
        assert _udcg([1], k=5) > _udcg([3], k=5)


def test_load_cases_missing_file(tmp_path):
    cases = load_cases(tmp_path / "nonexistent.yaml")
    assert cases == []


def test_load_cases(tmp_path):
    yaml_path = tmp_path / "cases.yaml"
    yaml_path.write_text(
        "- query: What is attention?\n  expected_slug: attention\n  domain: tech\n"
    )
    cases = load_cases(yaml_path)
    assert len(cases) == 1
    assert cases[0].query == "What is attention?"
    assert cases[0].expected_slug == "attention"


def test_bm25_eval_hit(tmp_path):
    page = _make_page(
        title="Attention Mechanism",
        body="The attention mechanism allows the model to focus on relevant parts "
             "of the input. It computes query key value dot products.",
        path=tmp_path / "attention-mechanism.md",
    )
    write_page(page)

    cases = [RetrievalCase(query="How does attention work?", expected_slug="attention-mechanism")]
    report = run_bm25_eval(cases, wiki_dir=tmp_path, k=5)

    assert report.total_cases == 1
    assert isinstance(report.precision_at_k, float)
    assert 0.0 <= report.mrr <= 1.0
    assert 0.0 <= report.udcg <= 1.0


def test_bm25_eval_no_cases(tmp_path):
    report = run_bm25_eval([], wiki_dir=tmp_path, k=5)
    assert report.total_cases == 0
    assert report.precision_at_k == 0.0


# ---------------------------------------------------------------------------
# store.py
# ---------------------------------------------------------------------------

class TestStore:
    def test_save_and_retrieve(self, tmp_path):
        db = tmp_path / "evals.db"
        save_run(db, "wiki_quality", {"mean_richness": 4.5, "total_pages": 10})
        runs = latest_runs(db, limit=5)
        assert len(runs) == 1
        assert runs[0]["eval_type"] == "wiki_quality"
        assert runs[0]["summary"]["mean_richness"] == 4.5

    def test_multiple_runs(self, tmp_path):
        db = tmp_path / "evals.db"
        save_run(db, "chunking", {"recommended": 512})
        save_run(db, "retrieval", {"precision_at_k": 0.8})
        save_run(db, "wiki_quality", {"mean_richness": 5.0})
        runs = latest_runs(db)
        assert len(runs) == 3

    def test_missing_db_returns_empty(self, tmp_path):
        runs = latest_runs(tmp_path / "nonexistent.db")
        assert runs == []


# ---------------------------------------------------------------------------
# Grade properties — wiki quality + chunking (mirror RetrievalReport.grade)
# ---------------------------------------------------------------------------

class TestWikiQualityGrade:
    def _report(self, total=10, stubs=0, richness=9.0):
        return WikiQualityReport(
            total_pages=total, mean_richness=richness, median_richness=richness,
            stub_count=stubs, no_wikilinks_count=0, no_tags_count=0,
        )

    def test_healthy_wiki_passes(self):
        assert self._report(total=10, stubs=0, richness=9.0).grade == "PASS"

    def test_some_stubs_warns(self):
        # 15% stubs, decent richness → WARN
        assert self._report(total=20, stubs=3, richness=5.0).grade == "WARN"

    def test_many_stubs_fails(self):
        # 30% stubs → FAIL
        assert self._report(total=10, stubs=3, richness=5.0).grade == "FAIL"

    def test_low_richness_fails(self):
        assert self._report(total=10, stubs=0, richness=2.0).grade == "FAIL"

    def test_empty_wiki_warns(self):
        # No pages = nothing to grade — WARN, not FAIL
        assert self._report(total=0, stubs=0, richness=0.0).grade == "WARN"


class TestChunkingGrade:
    def test_ablation_with_recommendation_passes(self):
        from mymem.evals.chunking import ChunkingReport
        rows = chunk_size_ablation("word " * 500)
        report = ChunkingReport(ablation=rows, recommended_max_tokens=1024)
        assert report.grade == "PASS"

    def test_empty_ablation_warns(self):
        from mymem.evals.chunking import ChunkingReport
        report = ChunkingReport(ablation=[], recommended_max_tokens=1024)
        assert report.grade == "WARN"

    def test_no_recommendation_warns(self):
        from mymem.evals.chunking import ChunkingReport
        rows = chunk_size_ablation("word " * 500)
        report = ChunkingReport(ablation=rows, recommended_max_tokens=0)
        assert report.grade == "WARN"
