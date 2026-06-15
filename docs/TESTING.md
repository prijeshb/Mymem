# MyMem — Test Coverage Reference

Last updated: 2026-06-14 (branch V1-0008). Suite: **~724 tests** across 27 files.
Run: `pytest` · coverage: `pytest --cov=mymem --cov-report=term-missing`.

## Testing philosophy (how to read this doc)

Every test here aims to exercise the **real system path**, mocking only the
outermost boundary that can't run deterministically in CI:

- **Network** is mocked at the `httpx`/SDK boundary only. The reader chain,
  router, parsing, and assembly logic all run for real.
- **LLM calls** are injected (`llm_fn`) or patched at `complete()` — never is a
  real Ollama/Anthropic/NVIDIA call required (project rule).
- **Filesystem** uses `tmp_path`; **DBs** use real SQLite on temp files.
- We avoid self-referential snapshot assertions. Where a value is pinned (e.g.
  the syndication token), it is anchored to a value **empirically validated
  against the live API**, so the test guards regressions rather than tautology.

"Diagnostic" tests (e.g. `test_ollama.py`) hit real local services and
**skip** automatically when the service/config isn't applicable.

---

## Full-suite map

| File | Tests | Covers |
|------|-------|--------|
| `test_ingest.py` | 77 | Ingest pipeline: source reading dispatch, idea parsing, frontmatter strip, ranking, PDF RAG path |
| `test_web.py` | 75 | FastAPI routes (`/api/*`) via `TestClient` — query/pages/stats/graph/ingest/upload/archive |
| `test_extraction_consensus.py` | 52 | Dual-LLM extraction consensus eval, cosine matching, grading |
| `test_evals.py` | 51 | Eval framework (chunking, wiki quality, retrieval, RAGAS-lite) |
| `test_graph_store.py` | 42 | Entity graph SQLite store: upsert/find/mentions/aliases |
| **`test_social_readers.py`** | **36** | **X/Twitter syndication + nitter, Reddit `.json`, X Article handling (this branch)** |
| `test_search.py` | 36 | DDG + Wikipedia fallback + TF-IDF web search |
| **`test_router.py`** | **36** | **Token/cost/context estimation, ModelRouter, fallback chains incl. free-tier swap (this branch)** |
| `test_wiki.py` | 33 | Wiki page/index/log read-write, slugify, archived field |
| `test_ingest_map_reduce.py` | 27 | Map→Merge→Verify extraction across chunks |
| `test_wiki_chunker.py` | 26 | Markdown header + parent-child chunking |
| `test_security.py` | 25 | Secret scanner, prompt-injection sanitize, validation |
| `test_tags.py` | 23 | Domain/tag normalization, curiosity tag store |
| `test_graph_resolver.py` | 23 | 3-tier entity resolution (exact/fuzzy/LLM) |
| `test_rag_ingest.py` | 19 | PDF/wiki/text RAG indexing orchestration |
| `test_ingest_analytics.py` | 19 | Ingest quality analytics records |
| `test_graph_extractor.py` | 17 | Typed entity extraction from source text |
| `test_rag_store.py` | 16 | sqlite-vec chunk store + delete_source |
| `test_rag_parser.py` | 16 | PDF text extraction + sliding-window chunking |
| `test_lint.py` | 14 | Wiki lint (orphans, broken links, stubs) — pure Python, 100% |
| `test_graph_backfill.py` | 14 | Graph backfill/migration CLI logic |
| `test_query.py` | 13 | Hybrid wiki keyword + RAG vector retrieval |
| `test_observability.py` | 13 | Logger, tracer, health |
| `test_introspect.py` | 12 | Daily summary + curiosity recommendations |
| `test_ollama.py` | 4 | **Diagnostic**: Ollama reachability + configured-model availability (provider-aware) |
| `test_graph_cli.py` | 4 | `mymem graph` CLI commands |
| `test_ingest_graph.py` | 3 | Ingest → graph extraction hook |

---

## Detailed coverage — files changed on V1-0008

### `tests/test_social_readers.py` (36)

Covers `mymem/pipeline/social_readers.py`. **Real:** URL parsing, token math,
JSON→text assembly, the full reader chain, dispatch, and fallback. **Mocked:**
only `httpx.AsyncClient` (the network), via `install_fake_httpx(route)` which
routes by URL so dispatch and fallback order are observable.

| Class | What it verifies |
|-------|------------------|
| `TestUrlDetection` | Twitter/Reddit host detection; tweet-ID extraction from `status/`, `i/web/status/`, query-stringed and `fxtwitter`/`nitter` forms; `None` when no ID |
| `TestSyndicationToken` | Token is base-36 alnum, no `0`/`.`; deterministic; varies by ID; **golden value `5b2tggf6ely89cmn9daemi` validated against the live syndication API** (regression guard) |
| `TestBuildTweetText` | Author + full untruncated body + quoted tweet + image alt-text; empty alt skipped; empty text → `""`; reply `parent` rendered; **X Article** title/preview + `t.co` expansion; article-only (empty tweet text) still produced |
| `TestBuildRedditText` | Post (title/selftext/author) + top comments; non-comment kinds (`more`) skipped; empty payloads → `""`; `.json` URL construction |
| `TestChainOrdering` | Tweet/Reddit readers claim their URLs **before** `WebSourceReader`; plain article URLs still go to `WebSourceReader` |
| `TestTweetIntegration` | `read_source` dispatches a tweet to syndication; falls back to nitter on syndication failure; raises actionable "paste the thread text" error when all fail |
| `TestTweetErrorPaths` | A raised network error on syndication still falls through to nitter; missing tweet ID raises `ValueError` |
| `TestRedditIntegration` | Dispatch to `.json` API; **403 block** (observed live) and **429 rate-limit** raise actionable errors; network error raises `RuntimeError` |

Honest caveat: the Reddit JSON fixture mirrors Reddit's documented `.json`
shape; it could not be live-verified because Reddit returns **403** to
non-browser IPs (that 403 path *is* tested). The Twitter syndication fixture
shape was confirmed against the live endpoint.

### `tests/test_router.py` → `TestFreeTierFallbackChain` (6 of 36)

Covers the cross-provider rate-limit fallback added this branch. **Real:**
`DefaultModelRegistry`, `FreeTierFallbackChain`, and the actual `ModelRouter.call`
fallback loop. **Mocked:** only `complete()` (the provider SDK call).

| Test | What it verifies |
|------|------------------|
| `test_preferred_is_first_and_chain_crosses_providers` | Preferred model first; the **2nd attempt is a different provider** (Groq) so a same-account NVIDIA 429 can't stall the pipeline |
| `test_ollama_floor_always_last` | Local Ollama is always the last resort even with no cloud keys |
| `test_groq_excluded_without_key` | Groq models dropped when no Groq key |
| `test_openrouter_gated_on_key` | OpenRouter models present iff the key is configured |
| `test_preferred_not_duplicated_when_already_in_chain` | No duplicate attempts when the preferred model is itself a chain member |
| `test_router_swaps_provider_on_rate_limit` | **End-to-end swap**: a 429 on NVIDIA makes the router retry on Groq and return its result — asserts the actual call sequence (`nvidia` then `groq`) |

### `tests/test_ollama.py` → `test_configured_models_available` (fixed)

Now **provider-aware**: resolves each configured model's provider via the
registry and only requires a local `ollama pull` for **Ollama-provider** models.
Cloud-provider models are validated by API key, not by pull, so the test
**skips** cleanly when the active config uses a cloud provider (e.g. NVIDIA) —
instead of falsely failing.

---

## Known gaps / not covered

- No live integration test against real NVIDIA/Groq/Reddit endpoints (by design —
  they're non-deterministic and rate-limited). Live behavior was manually
  verified during development and pinned via golden values where possible.
- X Article **full body** is not fetchable without auth; only the title +
  truncated preview path is covered (the structural limit, not a test gap).
