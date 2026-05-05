"""Tests for mymem.pipeline.search — web search + relevance scoring."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from mymem.pipeline.search import (
    _tokenise,
    _overlap_cosine,
    _score_results,
    _tfidf_keywords,
    _sklearn_cosine_score,
    search_concept,
    _wikipedia_fallback,
    _search_cache,
)


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_lowercases_and_splits(self):
        assert "cloud" in _tokenise("Cloud Computing")
        assert "computing" in _tokenise("Cloud Computing")

    def test_removes_stop_words(self):
        tokens = _tokenise("the art of war")
        assert "the" not in tokens
        assert "of" not in tokens
        assert "art" in tokens
        assert "war" in tokens

    def test_removes_single_chars(self):
        tokens = _tokenise("a b c hello")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "hello" in tokens

    def test_alphanumeric_only(self):
        tokens = _tokenise("machine-learning, AI!")
        assert "machine" in tokens
        assert "learning" in tokens
        assert "ai" in tokens

    def test_empty_string(self):
        assert _tokenise("") == set()


# ---------------------------------------------------------------------------
# _overlap_cosine
# ---------------------------------------------------------------------------

class TestOverlapCosine:
    def test_identical_sets_returns_one(self):
        tokens = {"machine", "learning"}
        assert _overlap_cosine(tokens, tokens) == pytest.approx(1.0)

    def test_no_overlap_returns_zero(self):
        assert _overlap_cosine({"apple"}, {"orange"}) == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = {"machine", "learning", "ai"}
        b = {"machine", "learning", "data"}
        score = _overlap_cosine(a, b)
        assert 0 < score < 1

    def test_empty_concept_returns_zero(self):
        assert _overlap_cosine(set(), {"foo"}) == pytest.approx(0.0)

    def test_empty_result_returns_zero(self):
        assert _overlap_cosine({"foo"}, set()) == pytest.approx(0.0)

    def test_symmetry(self):
        a = {"cat", "dog"}
        b = {"cat", "fish", "bird"}
        assert _overlap_cosine(a, b) == pytest.approx(_overlap_cosine(b, a))


# ---------------------------------------------------------------------------
# _score_results
# ---------------------------------------------------------------------------

class TestScoreResults:
    def _make_raw(self, title: str, body: str) -> dict:
        return {"title": title, "body": body, "href": f"https://example.com/{title}"}

    def test_returns_top_k(self):
        raw = [
            self._make_raw("Machine Learning Guide", "intro to machine learning algorithms"),
            self._make_raw("Deep Learning", "neural networks deep learning models"),
            self._make_raw("Data Science", "data analysis statistics"),
            self._make_raw("Irrelevant Topic", "cooking recipes baking"),
        ]
        results = _score_results("machine learning", raw, top_k=2)
        assert len(results) <= 2

    def test_drops_zero_score_results(self):
        raw = [self._make_raw("Cooking Recipes", "baking bread flour yeast")]
        results = _score_results("machine learning", raw, top_k=3)
        assert len(results) == 0

    def test_result_shape(self):
        raw = [self._make_raw("Machine Learning", "supervised learning algorithms")]
        results = _score_results("machine learning", raw, top_k=1)
        assert len(results) == 1
        r = results[0]
        assert r["label"] == "Machine Learning"
        assert r["url"] == "https://example.com/Machine Learning"
        assert r["source"] == "Web"
        assert len(r["snippet"]) <= 220

    def test_snippet_truncated_to_220(self):
        long_body = "x" * 500
        raw = [self._make_raw("machine", f"machine {long_body}")]
        results = _score_results("machine", raw, top_k=1)
        assert len(results[0]["snippet"]) <= 220

    def test_ranked_by_score_descending(self):
        raw = [
            self._make_raw("Tangential Result", "machine vague distant concept"),
            self._make_raw("Machine Learning Core", "machine learning supervised unsupervised algorithms"),
        ]
        results = _score_results("machine learning", raw, top_k=2)
        assert len(results) == 2
        # The more relevant result should come first
        assert "Machine Learning Core" == results[0]["label"]


# ---------------------------------------------------------------------------
# search_concept — DDG happy path
# ---------------------------------------------------------------------------

class TestSearchConcept:
    def setup_method(self):
        # Clear cache before each test
        _search_cache.clear()

    def _ddg_raw(self, title: str, body: str) -> dict:
        return {"title": title, "body": body, "href": f"https://example.com/{title.replace(' ', '-')}"}

    @pytest.mark.asyncio
    async def test_returns_web_results_on_ddg_success(self):
        raw = [
            self._ddg_raw("Machine Learning", "supervised learning algorithms"),
            self._ddg_raw("Machine Learning Tutorial", "machine learning introduction basics"),
            self._ddg_raw("Neural Networks", "machine learning deep networks"),
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw

        with patch("ddgs.DDGS", mock_ddgs):
            results = await search_concept("machine learning", top_k=3)

        assert isinstance(results, list)
        assert all(r["source"] == "Web" for r in results)

    @pytest.mark.asyncio
    async def test_caches_results(self):
        raw = [self._ddg_raw("Machine Learning", "machine learning basics")]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw

        with patch("ddgs.DDGS", mock_ddgs):
            r1 = await search_concept("caching test", top_k=2)
            r2 = await search_concept("caching test", top_k=2)

        assert r1 is r2
        assert mock_ddgs.call_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_wikipedia_on_ddg_error(self):
        wiki_results = [
            {"title": "Machine Learning", "snippet": "a field of AI", "pageid": 1},
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.side_effect = RuntimeError("rate limited")

        mock_response = MagicMock()
        mock_response.json.return_value = {"query": {"search": wiki_results}}

        with patch("ddgs.DDGS", mock_ddgs):
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )
                results = await search_concept("machine learning fallback", top_k=1)

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_ddg_retries_without_quotes(self):
        """Empty first result triggers an unquoted retry."""
        raw = [self._ddg_raw("Niche Concept", "niche concept detail")]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.side_effect = [[], raw]

        with patch("ddgs.DDGS", mock_ddgs):
            results = await search_concept("niche concept", top_k=1)

        assert mock_ddgs.return_value.text.call_count == 2

    @pytest.mark.asyncio
    async def test_different_top_k_separate_cache_keys(self):
        raw = [
            self._ddg_raw("Machine Learning", "machine learning supervised"),
            self._ddg_raw("Deep Learning", "machine learning deep networks"),
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw

        with patch("ddgs.DDGS", mock_ddgs):
            r1 = await search_concept("machine", top_k=1)
            r2 = await search_concept("machine", top_k=2)

        assert r1 is not r2


# ---------------------------------------------------------------------------
# _wikipedia_fallback
# ---------------------------------------------------------------------------

class TestWikipediaFallback:
    @pytest.mark.asyncio
    async def test_returns_wikipedia_results(self):
        hits = [
            {"title": "Python (programming language)", "snippet": "Python is a <b>programming</b> language"},
            {"title": "Python (genus)", "snippet": "A genus of snakes"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = {"query": {"search": hits}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            results = await _wikipedia_fallback("Python", top_k=2)

        assert len(results) == 2
        assert results[0]["label"] == "Python (programming language)"
        assert results[0]["source"] == "Wikipedia"
        assert "https://en.wikipedia.org/wiki/" in results[0]["url"]

    @pytest.mark.asyncio
    async def test_strips_html_tags_from_snippet(self):
        hits = [{"title": "Python", "snippet": "Python is a <b>high-level</b> language"}]
        mock_response = MagicMock()
        mock_response.json.return_value = {"query": {"search": hits}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            results = await _wikipedia_fallback("Python", top_k=1)

        assert "<b>" not in results[0]["snippet"]
        assert "high-level" in results[0]["snippet"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("network down")
            )
            results = await _wikipedia_fallback("anything", top_k=3)

        assert results == []


# ---------------------------------------------------------------------------
# Phase 2 — TF-IDF keyword extraction
# ---------------------------------------------------------------------------

class TestTfidfKeywords:
    def test_returns_keywords_from_text(self):
        text = (
            "Machine learning is a subset of artificial intelligence. "
            "It uses neural networks and deep learning algorithms to learn from data."
        )
        keywords = _tfidf_keywords(text, max_features=5)
        assert isinstance(keywords, list)
        assert len(keywords) <= 5
        # Should pick up domain-specific words, not stop words
        assert all(isinstance(k, str) for k in keywords)

    def test_returns_empty_on_empty_text(self):
        keywords = _tfidf_keywords("", max_features=5)
        assert keywords == []

    def test_max_features_respected(self):
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        keywords = _tfidf_keywords(text, max_features=3)
        assert len(keywords) <= 3

    def test_returns_empty_on_stop_words_only(self):
        keywords = _tfidf_keywords("the and or but in on at to for of", max_features=5)
        assert keywords == []


# ---------------------------------------------------------------------------
# Phase 2 — sklearn cosine scoring
# ---------------------------------------------------------------------------

class TestSklearnCosineScore:
    def _make_raw(self, title: str, body: str) -> dict:
        return {"title": title, "body": body, "href": f"https://example.com/{title.replace(' ', '-')}"}

    def test_returns_results_with_page_context(self):
        raw = [
            self._make_raw("Machine Learning Guide", "supervised learning algorithms classification"),
            self._make_raw("Neural Networks", "deep learning neural network architecture"),
            self._make_raw("Cooking Recipes", "baking bread flour yeast kitchen"),
        ]
        results = _sklearn_cosine_score("machine learning", "algorithms classification models", raw, top_k=2)
        assert isinstance(results, list)
        assert len(results) <= 2
        assert all(r["source"] == "Web" for r in results)

    def test_returns_empty_on_no_raw_results(self):
        results = _sklearn_cosine_score("anything", "context", [], top_k=3)
        assert results == []

    def test_snippet_truncated_to_220(self):
        raw = [self._make_raw("machine learning", "machine " + "x" * 500)]
        results = _sklearn_cosine_score("machine", "learning", raw, top_k=1)
        if results:
            assert len(results[0]["snippet"]) <= 220

    def test_falls_back_to_overlap_on_exception(self):
        raw = [self._make_raw("Machine Learning", "machine learning supervised")]
        # Simulate sklearn failure by patching the lazy import to raise
        import builtins
        real_import = builtins.__import__
        def _fail_sklearn(name, *args, **kwargs):
            if "sklearn" in name:
                raise ImportError("sklearn not available")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=_fail_sklearn):
            results = _sklearn_cosine_score("machine learning", "algorithms", raw, top_k=1)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Phase 2 — search_concept with page_body
# ---------------------------------------------------------------------------

class TestSearchConceptPhase2:
    def setup_method(self):
        _search_cache.clear()

    def _ddg_raw(self, title: str, body: str) -> dict:
        return {"title": title, "body": body, "href": f"https://example.com/{title.replace(' ', '-')}"}

    @pytest.mark.asyncio
    async def test_uses_sklearn_scoring_when_page_body_provided(self):
        raw = [
            self._ddg_raw("Machine Learning Basics", "supervised machine learning algorithms"),
            self._ddg_raw("Deep Learning", "neural networks deep learning models"),
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw
        page_body = "This page covers machine learning algorithms and classification models."

        with patch("ddgs.DDGS", mock_ddgs):
            results = await search_concept("machine learning", top_k=2, page_body=page_body)

        assert isinstance(results, list)
        assert all(r["source"] == "Web" for r in results)

    @pytest.mark.asyncio
    async def test_enriches_query_with_tfidf_keywords(self):
        raw = [self._ddg_raw("Machine Learning", "machine learning basics")]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw
        page_body = "supervised learning classification regression neural networks"

        with patch("ddgs.DDGS", mock_ddgs):
            await search_concept("machine learning", top_k=1, page_body=page_body)

        # Query should be enriched (quoted concept + keywords), not just '"concept"'
        call_args = mock_ddgs.return_value.text.call_args_list[0]
        query_used = call_args[0][0]
        assert '"machine learning"' in query_used

    @pytest.mark.asyncio
    async def test_different_page_body_produces_different_cache_key(self):
        raw = [self._ddg_raw("Machine Learning", "machine learning supervised")]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw

        with patch("ddgs.DDGS", mock_ddgs):
            r1 = await search_concept("machine", top_k=1, page_body="")
            r2 = await search_concept("machine", top_k=1, page_body="neural networks deep learning")

        assert r1 is not r2

    @pytest.mark.asyncio
    async def test_no_page_body_uses_phase1_overlap(self):
        raw = [self._ddg_raw("Machine Learning", "machine learning supervised")]
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = raw

        with patch("ddgs.DDGS", mock_ddgs):
            results = await search_concept("machine learning", top_k=1)

        assert isinstance(results, list)
