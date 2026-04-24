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
