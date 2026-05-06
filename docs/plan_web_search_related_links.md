# Plan: Backend Web Search for Related Links

## What was asked
Backend searches the web for each related concept extracted from a wiki page.
Results are ranked by relevance to the concept. Top 3 returned to frontend via SSE.

---

## Phase 1 — implemented

### Search provider
`duckduckgo-search` Python package (free, no API key, ~10M downloads/month).
Query: quoted concept title e.g. `"Cloud Computing"`.

### Relevance scoring (word-overlap cosine)
```
tokens_concept = set(concept.lower().split())
tokens_result  = set((title + " " + snippet).lower().split())
score = |intersection| / sqrt(|concept| × |result|)
```
Results with score=0 dropped. Top 3 by score returned.

### Files changed
| File | Change |
|------|--------|
| `mymem/pipeline/search.py` | New — DDG search + overlap scoring + Wikipedia fallback |
| `mymem/web/routes/api.py` | Replace `_fetch_wikipedia_results` with `search_concept` |
| `pyproject.toml` | Add `duckduckgo-search>=6.0` |

### What does NOT change
- Frontend, SSE contract, popover UI — all unchanged
- `web_links` shape: `[{ label, url, snippet, source }]`

---

## Phase 2 — planned

### Richer search query
Extract top-N keywords from the wiki page body using TF-IDF:
```python
from sklearn.feature_extraction.text import TfidfVectorizer
vectorizer = TfidfVectorizer(stop_words='english', max_features=10)
keywords = vectorizer.fit([page.body]).get_feature_names_out()
query = f'"{concept}" {" ".join(keywords[:5])}'
```

### Proper cosine similarity
```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

query_vec  = vectorizer.transform([concept + " " + page_keywords])
result_vec = vectorizer.transform([r['title'] + " " + r['body'] for r in results])
scores = cosine_similarity(query_vec, result_vec).flatten()
```

### Additional dep
`scikit-learn>=1.4`

---

## Risks
| Risk | Mitigation |
|------|-----------|
| DDG rate limits | In-process cache; Wikipedia API fallback |
| DDG breaks | Pinned version; fallback chain |
| Slow (~1–2s per concept) | Already async SSE — skeleton shows while loading |

---

## Phase 3 — planned

BM25 re-scoring of DDG results + Ollama embedding similarity + cross-encoder final rerank,
fused via Reciprocal Rank Fusion (RRF).

### Design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| BM25 target | Score DDG result set (~9 docs) | DDG already handles recall; we only need ranking precision on this small corpus |
| Embedding store | In-memory per request | DDG results are ephemeral; `_search_cache` amortises Ollama latency; SQLite adds I/O with no benefit |
| Embedding model | Ollama `nomic-embed-text` | Already configured in `config.yaml`; no extra API key |
| Cross-encoder | `cross-encoder/ms-marco-MiniLM-L-6-v2` via `sentence-transformers` | Purpose-built for ranking; ~30 MB; ~50 ms/query; more calibrated than Ollama LLM zero-shot reranker |
| Fusion strategy | RRF(BM25, embed) → cross-encoder final rerank | Avoids cascading failures; each ranker contributes independently |

### Scoring formula

```
# Step 1 — RRF over BM25 rank and embedding cosine rank
rrf_score(doc) = 1 / (60 + rank_bm25(doc)) + 1 / (60 + rank_embed(doc))

# Step 2 — cross-encoder score for top-5 RRF candidates
ce_score(doc) = CrossEncoder("ms-marco-MiniLM-L-6-v2").predict(query, doc_text)

# Step 3 — final blend
final_score(doc) = 0.6 × rrf_score(doc) + 0.4 × ce_score(doc)
```

k=60 is the standard RRF constant (empirically stable across many retrieval benchmarks).

### New functions in `mymem/pipeline/search.py`

```python
async def _get_embeddings(texts: list[str]) -> list[list[float]]:
    """Fetch embeddings via Ollama nomic-embed-text. Returns [] on failure."""

def _bm25_score(
    query: str,
    raw: list[dict],
) -> dict[str, float]:
    """
    BM25 score each DDG result against query.
    Returns {href: normalised_score}.
    Uses rank-bm25 (already in deps).
    Tokenises title + body of each result.
    """

def _cosine_scores(
    query_vec: list[float],
    doc_vecs: list[list[float]],
) -> list[float]:
    """Dot-product cosine similarity (vectors are L2-normalised by nomic-embed-text)."""

def _rrf_fuse(
    ranked_lists: list[list[str]],   # each list is hrefs ordered by one ranker
    k: int = 60,
) -> dict[str, float]:
    """Standard Reciprocal Rank Fusion over N ranked lists."""

def _cross_encoder_score(
    query: str,
    candidates: list[dict],          # DDG result dicts
) -> dict[str, float]:
    """
    Score (query, doc_text) pairs with ms-marco-MiniLM-L-6-v2.
    Returns {href: ce_score}.
    Lazy-loads model; cached as module-level singleton.
    """

async def _phase3_rank(
    concept: str,
    page_keywords: str,
    raw: list[dict],
    top_k: int,
) -> list[WebResult]:
    """
    Orchestrate Phase 3:
      1. BM25 rank raw results
      2. Embed query + results → cosine rank
      3. RRF fuse BM25 + embed ranks
      4. Cross-encoder rerank top-5 RRF candidates
      5. Blend RRF + CE scores → final ranking
    Falls back to _sklearn_cosine_score on any failure.
    """
```

### Changes to `search_concept()`

Add one parameter; Phase 3 is opt-in so existing callers are unaffected:

```python
async def search_concept(
    concept: str,
    top_k: int = 3,
    page_body: str = "",
    use_rerank: bool = True,         # NEW — enables Phase 3 when True and sklearn available
) -> list[WebResult]:
    ...
    # Routing logic:
    #   page_body + use_rerank → _phase3_rank()   (Phase 3)
    #   page_body only         → _sklearn_cosine_score()  (Phase 2)
    #   neither                → _score_results()          (Phase 1)
```

### New dependency

```toml
"sentence-transformers>=3.1",   # cross-encoder/ms-marco-MiniLM-L-6-v2
```

`rank-bm25` is already present. `ollama` client already present.

### Files changed

| File | Change |
|------|--------|
| `mymem/pipeline/search.py` | Add `_get_embeddings`, `_bm25_score`, `_cosine_scores`, `_rrf_fuse`, `_cross_encoder_score`, `_phase3_rank`; update `search_concept` |
| `pyproject.toml` | Add `sentence-transformers>=3.1` |
| `tests/test_search.py` | Phase 3 test classes for each new function |

Frontend, SSE contract, and API endpoint are **unchanged** — `page_slug` is already wired.

### Latency budget

| Stage | Cold | Cached hit |
|-------|------|-----------|
| DDG fetch | ~800 ms | <1 ms (cache) |
| BM25 scoring | ~5 ms | — |
| Ollama embed (query + 9 docs) | ~200 ms | — |
| Cross-encoder (top-5) | ~50 ms | — |
| **Total cold** | **~1 055 ms** | **<1 ms** |

SSE streaming means the UI skeleton appears immediately; results fill in as each concept resolves.
First cold request per concept is the only expensive path.

### Risks

| Risk | Mitigation |
|------|-----------|
| Ollama not running | `_get_embeddings` returns `[]`; pipeline falls back to Phase 2 |
| `sentence-transformers` not installed | `_cross_encoder_score` returns uniform scores; RRF result used as-is |
| MiniLM model download (~30 MB) | One-time; cached in `~/.cache/huggingface/hub/`; no issue for local workflows |
| BM25 on tiny corpus (<3 results) | Degenerate scores; RRF naturally handles this (equal BM25 rank contribution) |
| RRF weight tuning | Start with k=60, weights 0.6/0.4; adjust based on observed result quality |
