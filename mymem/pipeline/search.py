"""
Web search + relevance scoring for related wiki concepts.

Phase 1: DuckDuckGo text search, word-overlap cosine for ranking.
Phase 2: TF-IDF keyword extraction from page body + sklearn cosine similarity.
"""

from __future__ import annotations

import math
import re
from typing import TypedDict

from mymem.observability.logger import get_logger

log = get_logger(__name__)

# In-process cache: (concept, top_k, page_body_hash) → ranked results
_search_cache: dict[str, list["WebResult"]] = {}

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "as", "not", "have", "has", "do", "does",
}


class WebResult(TypedDict):
    label:   str
    url:     str
    snippet: str
    source:  str


# ---------------------------------------------------------------------------
# Phase 1 — word-overlap cosine (no external deps)
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1}


def _overlap_cosine(concept_tokens: set[str], result_tokens: set[str]) -> float:
    """
    Jaccard-cosine approximation:
        |A ∩ B| / sqrt(|A| * |B|)
    Returns 0 when either set is empty.
    """
    if not concept_tokens or not result_tokens:
        return 0.0
    intersection = len(concept_tokens & result_tokens)
    return intersection / math.sqrt(len(concept_tokens) * len(result_tokens))


def _score_results(
    concept: str,
    raw: list[dict],
    top_k: int,
) -> list[WebResult]:
    concept_tokens = _tokenise(concept)
    scored: list[tuple[float, dict]] = []

    for r in raw:
        result_text = f"{r.get('title', '')} {r.get('body', '')}"
        score = _overlap_cosine(concept_tokens, _tokenise(result_text))
        if score > 0:
            scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        WebResult(
            label=r["title"],
            url=r["href"],
            snippet=(r.get("body") or "")[:220],
            source="Web",
        )
        for _, r in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# Phase 2 — TF-IDF + sklearn cosine similarity
# ---------------------------------------------------------------------------

def _tfidf_keywords(text: str, max_features: int = 10) -> list[str]:
    """Extract top keywords from text using TF-IDF. Returns [] if sklearn unavailable."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
        vectorizer = TfidfVectorizer(stop_words="english", max_features=max_features)
        vectorizer.fit([text])
        return list(vectorizer.get_feature_names_out())
    except Exception:
        return []


def _sklearn_cosine_score(
    concept: str,
    page_keywords: str,
    raw: list[dict],
    top_k: int,
) -> list[WebResult]:
    """
    Score DDG results using sklearn TF-IDF cosine similarity.
    Falls back to word-overlap scoring if sklearn is unavailable or corpus is empty.
    """
    if not raw:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
        from sklearn.metrics.pairwise import cosine_similarity as sk_cosine  # type: ignore[import-untyped]

        query_text = f"{concept} {page_keywords}".strip()
        corpus = [query_text] + [
            f"{r.get('title', '')} {r.get('body', '')}" for r in raw
        ]
        vectorizer = TfidfVectorizer(stop_words="english")
        tfidf = vectorizer.fit_transform(corpus)
        scores = sk_cosine(tfidf[0:1], tfidf[1:]).flatten()

        ranked = sorted(zip(scores, raw), key=lambda x: x[0], reverse=True)
        return [
            WebResult(
                label=r["title"],
                url=r["href"],
                snippet=(r.get("body") or "")[:220],
                source="Web",
            )
            for score, r in ranked[:top_k]
            if score > 0
        ]
    except Exception:
        return _score_results(concept, raw, top_k)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_concept(
    concept: str,
    top_k: int = 3,
    page_body: str = "",
) -> list[WebResult]:
    """
    Search DuckDuckGo for a concept and return top_k ranked results.

    When page_body is provided (Phase 2):
      - Extracts TF-IDF keywords from the page body to enrich the query
      - Scores results with sklearn cosine similarity

    Falls back to Wikipedia API on DDG error.
    Results are cached in-process for the session lifetime.
    """
    cache_key = f"{concept}:{top_k}:{hash(page_body)}"
    if cache_key in _search_cache:
        return _search_cache[cache_key]

    keywords: list[str] = []
    if page_body:
        keywords = _tfidf_keywords(page_body)
    page_keywords = " ".join(keywords[:5])

    try:
        from ddgs import DDGS
        query = f'"{concept}" {page_keywords}'.strip() if page_keywords else f'"{concept}"'
        raw = list(DDGS().text(query, max_results=top_k * 3))
        if not raw:
            # retry without quotes for niche concepts
            fallback_query = f"{concept} {page_keywords}".strip()
            raw = list(DDGS().text(fallback_query, max_results=top_k * 3))

        if page_body and keywords:
            results = _sklearn_cosine_score(concept, page_keywords, raw, top_k)
        else:
            results = _score_results(concept, raw, top_k)
    except Exception as exc:
        log.warning("DDG search failed, falling back to Wikipedia", concept=concept, error=str(exc))
        results = await _wikipedia_fallback(concept, top_k)

    if not results:
        results = await _wikipedia_fallback(concept, top_k)

    _search_cache[cache_key] = results
    return results


async def _wikipedia_fallback(concept: str, top_k: int) -> list[WebResult]:
    """Wikipedia search API as a fallback when DDG fails."""
    import re as _re
    try:
        import httpx
        params = {
            "action": "query",
            "list": "search",
            "srsearch": concept,
            "srlimit": str(top_k),
            "format": "json",
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MyMem/1.0; +https://github.com/mymem)"}
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get("https://en.wikipedia.org/w/api.php", params=params, headers=headers)
        hits = resp.json().get("query", {}).get("search", [])
        return [
            WebResult(
                label=h["title"],
                url=f"https://en.wikipedia.org/wiki/{h['title'].replace(' ', '_')}",
                snippet=_re.sub(r"<[^>]+>", "", h.get("snippet", ""))[:220],
                source="Wikipedia",
            )
            for h in hits
        ]
    except Exception:
        return []
