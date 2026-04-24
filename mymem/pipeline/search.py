"""
Web search + relevance scoring for related wiki concepts.

Phase 1: DuckDuckGo text search, word-overlap cosine for ranking.
Phase 2 (planned): TF-IDF keyword extraction from page body + sklearn cosine similarity.
"""

from __future__ import annotations

import math
import re
from typing import TypedDict

import logging

log = logging.getLogger(__name__)

# In-process cache: concept → ranked results
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


async def search_concept(concept: str, top_k: int = 3) -> list[WebResult]:
    """
    Search DuckDuckGo for a concept, rank by overlap cosine, return top_k.
    Falls back to Wikipedia API on error.
    Results are cached in-process for the session lifetime.
    """
    cache_key = f"{concept}:{top_k}"
    if cache_key in _search_cache:
        return _search_cache[cache_key]

    try:
        from ddgs import DDGS
        raw = list(DDGS().text(f'"{concept}"', max_results=top_k * 3))
        if not raw:
            # retry without quotes for niche concepts
            raw = list(DDGS().text(concept, max_results=top_k * 3))
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
