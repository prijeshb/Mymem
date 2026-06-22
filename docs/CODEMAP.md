# MyMem — Developer Quick Reference (CODEMAP)

> Single source of truth for "where do I touch X?" before opening any file.
> Keep this updated whenever a module's public API or location changes.

---

## 1. Task-Based Index — Where to Change Things

| Task | Files to touch | Key symbol |
|------|---------------|-----------|
| Add a field to the URL/file/text ingest form | `frontend/src/pages/IngestPage.tsx` → `SharedFields` + state + all 3 submit fns | `SharedFields` props, `postIngest/postUpload/postIngestText` |
| Add a backend ingest param | `mymem/web/routes/api.py` → `IngestRequest` + `IngestTextRequest` + `api_upload` Form() | Lines 95–112, 347–353 |
| Change how ideas are ranked/limited | `mymem/pipeline/ingest_extract.py` → `_rank_extracted_ideas()` | — |
| Change the LLM extraction prompt | `mymem/pipeline/ingest_extract.py` → `_EXTRACT_SYSTEM` (canonical) or `_EXTRACT_SYSTEM_TMPL` (legacy w/ max_concepts) | — |
| Change Map/Merge/Verify extraction | `mymem/pipeline/ingest_extract.py` → `_extract_chunk_ideas`/`_merge_ideas`/`_verify_ideas`/`_extract_ideas_map_reduce` | spans grounded in `_ground_span` |
| Change ADD/MERGE/SUPERSEDE/NOOP decision | `mymem/pipeline/reconcile.py` → `build_decision_prompt`/`parse_decision`/`apply_decision` | parse degrades to safe ADD |
| Change claim retrieval (candidates) | `mymem/knowledge/retrieval.py` → `retrieve_candidates()` (global, vector in) | searches `claim_index` cross-page |
| Change the claim vector index | `mymem/knowledge/claim_index.py` → `init_index`/`index_claim`/`search` (sqlite-vec, cosine) | in claims.db; `mymem claims backfill-index` |
| Change the claims store schema/ops | `mymem/knowledge/claims.py` (`data/claims.db`) | bi-temporal; `page_id` key (ADR-013) |
| Change the wiki "Knowledge Claims" section | `mymem/knowledge/render.py` → `render_claims_section`/`sync_claims_section` | marker-delimited, idempotent |
| Change compounding persistence on ingest | `mymem/pipeline/ingest_claims.py` → `_persist_claims` (+ `_sync_claims_sections`) | naive fallback if embedder down |
| Change the decision-agreement ship gate | `mymem/evals/decision_agreement.py` → `score_decision_agreement`/`_grade` | PASS≥0.80/WARN≥0.60 |
| Add a new LLM provider | `mymem/pipeline/llm.py` → subclass `_OpenAICompatProvider`, add branch in `build_provider()` | Lines 65–272 |
| Add a new source type (ingest reader) | `mymem/pipeline/readers.py` → subclass `SourceReader`, register in `_default_readers()` | Lines 55–200 |
| Add a new API endpoint | `mymem/web/routes/api.py` → `@router.get/post(...)` | Any line after existing endpoints |
| Add a new frontend API call | `frontend/src/lib/api.ts` → new `export async function` | Follow existing pattern |
| Add a new frontend type | `frontend/src/lib/types.ts` → new `export interface` | Any line |
| Change eval grading thresholds | `mymem/evals/extraction_consensus.py` → `_grade()`; `RetrievalReport.grade` / `WikiQualityReport.grade` / `ChunkingReport.grade` properties | per-module |
| Trigger eval suite from UI | `mymem/web/routes/api.py` → `api_evals_run` (POST /api/evals/run); `EvalsPage.tsx` → `startRun` | 409 guard via `app.state.evals_running` |
| Change eval dashboard suite cards | `frontend/src/components/EvalSuiteGrid.tsx` → `SUITES` registry + `metricsFor()` | mirrors runner.py registrations |
| Add eval metric | `mymem/evals/extraction_consensus.py` + `mymem/evals/store.py` (schema migration) | PRAGMA migration pattern |
| Change model defaults / fallback chain | `mymem/pipeline/router/_registry.py`, `mymem/pipeline/router/_chain.py` | `DefaultModelRegistry._seed()`, `OllamaFallbackChain` |
| Add a domain or source type constant | `frontend/src/lib/types.ts` → `ALL_DOMAINS` or `SOURCE_TYPES` | Lines 6–15 |
| Change wiki page format / frontmatter | `mymem/wiki/types.py` → `WikiPage` dataclass | |
| Backfill / resolve stable page ids | `mymem pages backfill-ids` → `mymem/wiki/identity.py`; `mint_id()` in `wiki/types.py` | idempotent (ADR-013); claims/graph join on `page.id` |
| Change RAG chunking | `mymem/rag/wiki_chunker.py` (wiki), `mymem/rag/pdf_parser.py` (PDFs) | |
| Change embedding model or dim | `mymem/rag/embedder.py` → `EMBED_MODEL`, `EMBED_DIM` | Lines 20–21 |
| Add a new eval module | Subclass `mymem/evals/_base.py:Evaluator[T]`, register in `mymem/evals/runner.py` | |
| Add an entity type | `mymem/graph/store.py` → `ENTITY_TYPES` (validated everywhere from this tuple) | |
| Tune entity resolution thresholds | `mymem/graph/resolver.py` → `FUZZY_ACCEPT` / `FUZZY_BORDERLINE` / `COSINE_ACCEPT` | |
| Migrate/repair the entity graph | `mymem graph backfill [--classify] [--semantic] [--judge]` → `mymem/graph/backfill.py:seed_from_wiki` | Tier-1 re-seed idempotent; `--semantic`/`--judge` opt-in resolver tiers |
| Re-key graph anchors slug→id | `mymem graph rekey` → `mymem/graph/backfill.py:rekey_graph_page_ids` (+ `store.init_db` column migration) | ADR-014 D6; idempotent |
| Surface knowledge gaps (referenced-but-unwritten) | `mymem graph gaps` / `GET /api/graph/gaps` → `mymem/graph/gaps.py:knowledge_gaps` | ADR-008 D12; ranks pageless entities by inbound pages |
| Export/import OKF bundles | `mymem export okf` / `mymem import okf` → `mymem/knowledge/okf/exporter.py`,`importer.py` | ADR-016; lossless round-trip; direct map (not LLM pipeline) |
| Change graph-on-ingest behavior | `mymem/pipeline/ingest_background.py` → `_graph_extract_background()` (anchors on `page_id`) | fire-and-forget |
| Change RAG-on-ingest indexing | `mymem/pipeline/ingest_rag.py` → `_rag_index_pdf`/`_rag_index_wiki`/`_rag_index_text` | best-effort, never blocks |
| Change a background eval on ingest | `mymem/pipeline/ingest_background.py` → `_eval_extraction_background`/`_eval_decision_agreement_background`; reference LLM via `_build_reference_llm` | fire-and-forget |

---

## 2. Module Map — One Line Each

### Backend (`mymem/`)

```
config.py                   Settings (pydantic-settings): provider, models, paths, pipeline.*
cli.py                      Typer CLI: ingest/query/lint/serve/tags + graph (backfill/rekey/gaps/stats) + export/import okf + pages/claims

pipeline/
  llm.py                    LLMProvider ABC + concrete providers + build_provider() factory + complete() facade
  readers.py                SourceReader ABC + YoutubeSourceReader/WebSourceReader/PdfSourceReader/LocalFileSourceReader + SourceReaderChain
  router/
    _router.py              ModelRouter + ConfigTaskRouter + router_from_settings()
    _credentials.py         ProviderCredentials ABC + KeyMapCredentials (Strategy for API keys)
    _registry.py            DefaultModelRegistry — all known model specs with context windows + costs
    _chain.py               OllamaFallbackChain — fallback order when a model is unavailable
    _cost.py                SessionCostTracker — tracks $ spent per session
    _types.py               Interfaces: IModelRegistry, ITaskRouter, IFallbackChain, ICostTracker
    _utils.py               estimate_tokens(), fits_context(), estimate_cost()
  splitter.py               ChunkSplitter — split long docs for models with limited context
  ingest.py                 ingest_source() orchestrator + IngestResult + analytics; re-exports the split modules
  ingest_extract.py         Map/Merge/Verify idea extraction + prompts + IdeaSchema + span grounding (ADR-011 P1)
  ingest_rag.py             _rag_index_pdf/_rag_index_wiki/_rag_index_text — best-effort RAG indexing on ingest
  ingest_claims.py          _persist_claims (compounding) + _sync_claims_sections + _build_claim_embedder
  ingest_background.py      _graph_extract_background + _eval_*_background + _build_reference_llm (fire-and-forget)
  reconcile.py              ADD/MERGE/SUPERSEDE/NOOP decision core (ADR-011 P3): prompt/parse/apply_decision
  compounding.py            reconcile_source_claims() — embed→retrieve→decide→apply + maintains claim_index; backfill_claim_index()
  query.py                  query_wiki() — search wiki + RAG + LLM synthesis → SSE stream
  lint.py                   lint_wiki() — orphan/broken-link/stub detection (pure Python, no LLM)
  introspect.py             introspect() — daily summary, research suggestions, curiosity recs

knowledge/                  Compounding ledger (ADR-011/015) — data/claims.db
  claims.py                 bi-temporal claims store: add/get/supersede/corroborate/replace_source_claims
  claim_index.py            sqlite-vec cosine index over claims (global cross-page retrieval, D19)
  retrieval.py              retrieve_candidates() — global active claims via claim_index (vector in)
  render.py                 render_claims_section()/sync_claims_section() + render_page_body() (body-from-claims, default-on)
  okf/                      OKF v0.1 interchange (ADR-016) — export/import the wiki as an Open Knowledge Format bundle
    _spec.py                required `type`, reserved files, conformance predicate, concept_id()
    _map.py                 to_okf_frontmatter()/from_okf_frontmatter() — domain↔type, date↔timestamp, extension keys
    _links.py               wikilinks_to_markdown()/markdown_links_to_wikilinks()
    exporter.py             export_okf() — wiki → conformant bundle + index.md/log.md → ExportReport
    conformance.py          check_bundle() — every non-reserved .md has a non-empty `type`
    importer.py             import_okf() — direct lossless inverse → WikiPage (preserves ULID id)

graph/                      Entity layer (ADR-007/008) — data/graph.db
  store.py                  entities/aliases/mentions repo (anchored on stable page_id) + slug→id migration + delete_page() + stats()
  extractor.py              extract_entities() — typed JSON extraction + rapidfuzz span grounding
  resolver.py               resolve_entities() — 3-tier: exact/alias → fuzzy+cosine → batched LLM judge
  backfill.py               seed_from_wiki(embed_fn/router opt-in) + classify_entities() + rekey_graph_page_ids() (ADR-014 D6)
  gaps.py                   knowledge_gaps()/gap_count() — rank referenced-but-unwritten concepts (ADR-008 D12)

wiki/
  types.py                  WikiPage (incl. stable `id`), IndexEntry, LogEntry, LogOperation, TagDomain, slugify(), mint_id()
  identity.py               build_page_id_index() / resolve_to_id() / backfill_page_ids() — stable page id (ADR-013)
  page.py                   read_page(), write_page(stamp_updated=), list_pages(), list_archived_pages()
  index.py                  IndexManager — load/save/upsert/remove index.md entries
  log.py                    WikiLog — append-only log.md
  tags.py                   domain_from_str(), normalize_tags()

rag/
  embedder.py               Embedder ABC + OllamaEmbedder + embed_texts() + embed_query() (backward-compat)
  store.py                  RagStore — sqlite-vec chunk store: upsert/search/delete_source()
  ingest.py                 ingest_wiki_page(), ingest_pdf() — chunk + embed + store
  wiki_chunker.py           Markdown/header + parent-child chunking for wiki pages
  pdf_parser.py             pypdf + sliding-window chunking for PDFs

evals/
  _base.py                  Evaluator[T] Generic ABC (Template Method) + RunContext
  extraction_consensus.py   run_extraction_consensus() — dual-LLM re-extract + cosine matching
  decision_agreement.py     run_decision_agreement() — held-out judge vs pipeline ADD/MERGE/etc. (ship gate)
  runner.py                 EvalConfig, EvalReport, run_evals() — orchestrates all eval modules
  report.py                 render_eval_report() — Rich terminal tables
  store.py                  save_*/load_*/recent_* — eval results in data/evals.db (separate from mymem.db)
  chunking.py / retrieval.py / ragas_lite.py / ingest_quality.py  — other eval modules

observability/
  logger.py                 get_logger(), set_run_id() — structlog structured logging
  tracer.py                 trace_llm() context manager — records LLM calls to SQLite
  ingest_analytics.py       YouTube ingest quality tracking

security/
  scanner.py                has_high_severity_secret() — blocks ingestion of secrets
  sanitize.py               sanitize_for_prompt(), sanitize_query() — prompt injection scrubbing
  validate.py               Strict Pydantic models for API boundary validation

web/
  app.py                    FastAPI factory — mounts routes, serves frontend/dist/, sets app.state.*
  routes/api.py             ALL JSON API endpoints — every /api/* route lives here
  routes/logs.py            GET /api/log + GET /api/heatmap (extracted from api.py)
  routes/pages.py           LEGACY Jinja2 fallback (only used when frontend/dist/ missing)
```

### Frontend (`frontend/src/`)

```
main.tsx                    Router setup (react-router-dom v6)
app.tsx                     Root: Navbar + theme toggle + outlet

pages/
  DashboardPage.tsx         3-col layout: domain sidebar | chat thread | wiki panel
  SearchPage.tsx            Streaming Q&A with SSE, domain filter
  WikiPage.tsx              Markdown render, scroll-spy TOC, graph toggle, backlinks
  GraphPage.tsx             D3 force-directed wikilink network
  IngestPage.tsx            3 tabs: URL | Upload | Paste — all share SharedFields component
  IntrospectPage.tsx        Daily summary, quiz generator, knowledge digest, curiosity trends
  EvalsPage.tsx             Suite summary grid + run trigger + extraction consensus table
  NotFoundPage.tsx          404

components/
  Navbar.tsx                Gradient logo, nav links, search
  WikiSidePane.tsx          TOC + backlinks + related concepts sidebar for WikiPage
  DomainBadge.tsx           Colored domain pill (reused everywhere)
  Heatmap.tsx               16-week GitHub-style activity heatmap
  ClaudeLoader.tsx          Animated loading indicator
  ErrorBanner.tsx           Dismissible error message
  ThemeToggle.tsx           Dark/light mode button
  EvalSuiteGrid.tsx         Eval suite cards: grade badge, metrics, staleness, never-run state

lib/
  api.ts                    All fetch() wrappers for /api/* endpoints
  types.ts                  All shared TypeScript interfaces and constants
  sse.js                    SSE streaming helper for /api/query
```

---

## 3. Key Function Signatures

### `ingest_source()` — `mymem/pipeline/ingest.py` (orchestrator)
```python
async def ingest_source(
    source: str,               # file path or URL
    *,
    wiki_dir: Path,
    index_path: Path,
    log_path: Path,
    router: ModelRouter,
    source_type: str = "article",
    tags: list[str] | None = None,
    domain: str = "",
    title_hint: str | None = None,
    max_concepts: int = 3,     # limits ideas kept after ranking
    db_path: Path | None = None,
) -> IngestResult
```

### `IngestResult` — `mymem/pipeline/ingest.py`
```python
@dataclass
class IngestResult:
    source_path:   str
    pages_written: list[str] = []
    pages_updated: list[str] = []
    chunk_count:   int = 1
    skipped:       bool = False
    skip_reason:   str = ""
    rag_only:      bool = False
    rag_chunks:    int = 0
```

### API Request Models — `mymem/web/routes/api.py:95`
```python
class IngestRequest(BaseModel):
    source: str
    source_type: str = "article"
    tags: list[str] = []
    domain: str = ""
    max_concepts: int | None = None   # None → server config default

class IngestTextRequest(BaseModel):
    text: str
    source_type: str = "article"
    tags: list[str] = []
    domain: str = ""
    title: str = ""
    max_concepts: int | None = None
```

### Upload endpoint — `mymem/web/routes/api.py:347` (multipart Form, not Pydantic)
```python
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    source_type: str = Form("article"),
    domain: str = Form(""),
    tags: str = Form(""),           # comma-separated string — split manually
    max_concepts: int | None = Form(None),
) -> JSONResponse
```

### Frontend API functions — `frontend/src/lib/api.ts`
```typescript
postIngest({ source, source_type, tags, domain, max_concepts? })
postUpload(file, sourceType, domain, tags[], maxConcepts?)
postIngestText(text, title, sourceType, domain, tags[], maxConcepts?)
fetchPages(domain?, tag?)
fetchPage(slug)
patchPage(slug, { domain, tags })
deletePage(slug)
streamQuery({ question, domain, save })     // async generator → SSEEvent
fetchEvalsExtraction(limit?, order?, grade?) → EvalsExtractionResult
postEvalsRun(llmJudge?) → { started, llm_judge }   // POST /api/evals/run, 409 if active
```

### `ModelRouter.call()` — `mymem/pipeline/router/_router.py:111`
```python
async def call(
    self,
    prompt: str,
    task: str,           # "compile" | "qa" | "lint" | "classify" | "merge" | "introspect"
    *,
    system: str = "",
    max_tokens: int = 4096,
    model_override: str | None = None,
) -> str
```

### `build_provider()` — `mymem/pipeline/llm.py:229`
```python
def build_provider(
    provider: str,           # "ollama" | "anthropic" | "openai" | "groq" | "nvidia" | "openrouter"
    *, anthropic_api_key, openai_api_key, groq_api_key,
       nvidia_api_key, openrouter_api_key,
       ollama_base_url, ollama_timeout,
) -> LLMProvider
```

### `read_source()` — `mymem/pipeline/readers.py` (module-level)
```python
async def read_source(source: str, source_type: str = "article") -> str
# Delegates to: YoutubeSourceReader | WebSourceReader | PdfSourceReader | LocalFileSourceReader
```

### `embed_texts()` / `embed_query()` — `mymem/rag/embedder.py`
```python
async def embed_texts(texts: list[str], *, base_url, model) -> list[list[float]]
async def embed_query(query: str, *, base_url, model) -> list[float]
# Both delegate to OllamaEmbedder — backward-compat facades
```

---

## 4. Data Flow: Frontend Field → API → Pipeline

### POST /api/ingest (JSON body)
```
IngestPage.tsx (state)          api.ts payload          IngestRequest (Pydantic)    ingest_source() param
─────────────────────────────────────────────────────────────────────────────────────────────────────
url                         →   source              →   source             →         source
sourceType                  →   source_type         →   source_type        →         source_type
domain                      →   domain              →   domain             →         domain
tagList()                   →   tags                →   tags               →         tags
maxConcepts                 →   max_concepts        →   max_concepts       →         max_concepts
```

### POST /api/upload (multipart FormData)
```
IngestPage.tsx (state)          FormData key            Form() param in api_upload  ingest_source() param
─────────────────────────────────────────────────────────────────────────────────────────────────────
file                        →   "file"              →   file: UploadFile   →         str(dest_path)
sourceType                  →   "source_type"       →   source_type: str   →         source_type
domain                      →   "domain"            →   domain: str        →         domain
tagList().join(',')         →   "tags"              →   tags: str          →  split  tags
String(maxConcepts)         →   "max_concepts"      →   max_concepts: int  →         max_concepts
```

### POST /api/ingest-text (JSON body)
```
text, textTitle, sourceType, domain, tagList(), maxConcepts
→ { text, title, source_type, domain, tags, max_concepts }
→ IngestTextRequest
→ ingest_source(tmp_path, ..., max_concepts=...)
```

---

## 5. Test Conventions

### Monkeypatching — CRITICAL: patch the module where the code lives, not the importer

| Symbol | Patch target | NOT |
|--------|-------------|-----|
| `_read_youtube`, `_YT_AVAILABLE`, `YouTubeTranscriptApi`, `_fetch_youtube_metadata`, `TranscriptsDisabled`, `NoTranscriptFound`, `trafilatura` | `mymem.pipeline.readers` | ~~`mymem.pipeline.ingest`~~ |
| `embed_texts`, `embed_query` | `mymem.rag.ingest` (the callsite) | ~~`mymem.rag.embedder`~~ |
| `ingest_pdf` | `mymem.rag.ingest` | |
| `ingest_source` | via `ModelRouter(llm_fn=fake_llm)` injection | never patch ingest_source directly |
| `_rag_index_wiki` | `mymem.pipeline.ingest` (caller `ingest_source` lives there, calls it unqualified) | |
| `_build_claim_embedder` | `mymem.pipeline.ingest_claims` (caller `_persist_claims` moved there) | ~~`mymem.pipeline.ingest`~~ |
| `_build_reference_llm` | `mymem.pipeline.ingest_background` (callers `_eval_*_background` moved there) | ~~`mymem.pipeline.ingest`~~ |

> **Rule (ADR-015 D18):** after the ingest split, patch a function where its *caller* looks
> it up. If the caller still lives in `ingest.py` and calls it unqualified, the re-exported
> `mymem.pipeline.ingest.X` still works; if the caller moved to a sibling, patch the sibling.

### Standard test fixtures
```python
# Backend — always use tmp_path for file I/O
wiki_dir = tmp_path / "wiki"; wiki_dir.mkdir()
index_path = tmp_path / "index.md"

# Inject fake LLM via ModelRouter — never call Ollama in tests
router = ModelRouter(llm_fn=async_fake_fn)

# FastAPI routes — TestClient (no real server)
from fastapi.testclient import TestClient
client = TestClient(app)
```

### Coverage requirements
- `mymem/pipeline/lint.py` — 100% (pure Python)
- `mymem/rag/store.py` — 100%
- `mymem/security/` — ≥ 90%
- Everything else — ≥ 80%

---

## 6. Config & Settings

**Source of truth:** `config.yaml` + `.env`

```python
# Access in route handlers:
settings = request.app.state.settings

settings.provider              # "ollama" | "anthropic" | "groq" | ...
settings.models.compile        # model name for ingest
settings.models.qa             # model name for query
settings.pipeline.max_concepts # default ideas per ingest (overridable per-request)
settings.paths.wiki            # Path to wiki/
settings.paths.raw             # Path to raw/
settings.paths.db              # Path to data/mymem.db
settings.ollama.base_url
settings.anthropic_api_key     # str | None (from .env ANTHROPIC_API_KEY)
settings.openrouter_api_key    # str | None (from .env OPENROUTER_API_KEY)
settings.nvidia_api_key        # str | None (from .env NVIDIA_API_KEY)

# In tests — build router directly, don't touch settings:
router = ModelRouter(llm_fn=fake)
```

---

## 7. Adding a Shared Form Field (IngestPage Pattern)

Three-step checklist for any new field across all ingest tabs:

1. **`SharedFields` component** — add to props interface + render the control
2. **`IngestPage` state** — `useState(defaultValue)` at the top
3. **Three `SharedFields` usages** — pass `field={field} setField={setField}` to all three tab panels (url, file, text)
4. **Three submit handlers** — `submitUrl`, `submitFile`, `submitText` — pass to API call
5. **`api.ts`** — add optional param to `postIngest`, `postUpload` (append to FormData), `postIngestText`
6. **`api.py`** — add to `IngestRequest`, `IngestTextRequest`, and `api_upload` Form() params
7. **`ingest_source()`** — add param only if the pipeline needs it; if it's a config-level default, override in the handler

---

## 8. ABC / Interface Map

| ABC | Location | Concretions |
|-----|---------|------------|
| `LLMProvider` | `pipeline/llm.py` | `OllamaProvider`, `AnthropicProvider`, `OpenAIProvider`, `GroqProvider`, `NVIDIAProvider`, `OpenRouterProvider` |
| `_OpenAICompatProvider` | `pipeline/llm.py` | `OpenAIProvider`, `GroqProvider`, `NVIDIAProvider`, `OpenRouterProvider` |
| `SourceReader` | `pipeline/readers.py` | `YoutubeSourceReader`, `WebSourceReader`, `PdfSourceReader`, `LocalFileSourceReader` |
| `ProviderCredentials` | `pipeline/router/_credentials.py` | `KeyMapCredentials` |
| `IModelRegistry` | `pipeline/router/_types.py` | `DefaultModelRegistry` |
| `ITaskRouter` | `pipeline/router/_types.py` | `ConfigTaskRouter` |
| `IFallbackChain` | `pipeline/router/_types.py` | `OllamaFallbackChain` |
| `ICostTracker` | `pipeline/router/_types.py` | `SessionCostTracker` |
| `Embedder` | `rag/embedder.py` | `OllamaEmbedder` |
| `Evaluator[T]` | `evals/_base.py` | `ExtractionConsensusEval` (and future eval classes) |

---

## 9. File Size Quick Reference

| File | Lines (approx) | Split if > |
|------|---------------|-----------|
| `mymem/web/routes/api.py` | ~1060 | Extract new route group to `routes/` |
| `mymem/pipeline/ingest.py` | ~480 | OK — orchestrator only (split ADR-015 D18) |
| `mymem/pipeline/ingest_extract.py` | ~520 | OK (Map/Merge/Verify + prompts) |
| `mymem/pipeline/ingest_background.py` | ~230 | OK |
| `mymem/pipeline/ingest_claims.py` | ~120 | OK |
| `mymem/pipeline/ingest_rag.py` | ~90 | OK |
| `mymem/pipeline/readers.py` | ~260 | OK |
| `mymem/pipeline/llm.py` | ~380 | OK (refactored to Strategy pattern) |
| `frontend/src/pages/IngestPage.tsx` | ~490 | Watch — approaching 500 |
| `frontend/src/lib/api.ts` | ~300 | OK |
| `frontend/src/lib/types.ts` | ~270 | OK |

---

## 10. Running Things

```bash
# Backend
mymem serve --port 7860         # production (serves frontend/dist/)
mymem serve --port 7860 --dev   # dev mode (CORS + reload, but no HMR)

# Frontend (dev with HMR)
cd frontend && npm run dev       # http://localhost:5173 — proxies /api → :7860

# Tests
pytest                                            # all backend tests
pytest tests/test_ingest.py -x                   # single file
pytest --cov=mymem --cov-report=term-missing      # with coverage

# TypeScript check
cd frontend && npx tsc --noEmit
```
