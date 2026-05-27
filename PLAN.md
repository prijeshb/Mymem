# MyMem Implementation Plan

## Requirements

Build the core wiki machinery for `mymem` — a personal LLM-powered knowledge base with:
- **CLI** for ingest / query / lint / introspect from the terminal
- **Web UI** (FastAPI + Jinja2) dashboard for chat, search, browsing, and introspection
- **Multi-LLM routing** with Gemma4 support and token-budget-aware task splitting
- **Tag taxonomy** (spiritual, tech, finance, reminder, etc.) for filtering and curiosity tracking
- **Introspect** — daily summary + curiosity-aware reading suggestions from history

The project already has: config, observability, security modules.

---

## Phase 1: Wiki Core (`mymem/wiki/`)

| File | Purpose |
|------|---------|
| `mymem/wiki/__init__.py` | Package marker |
| `mymem/wiki/types.py` | `WikiPage`, `IndexEntry`, `LogEntry`, `LogOperation`, `TagDomain` frozen dataclasses |
| `mymem/wiki/page.py` | `read_page()`, `write_page()`, `list_pages()` — YAML frontmatter I/O |
| `mymem/wiki/index.py` | `IndexManager` — load/save/upsert/remove entries in `index.md` |
| `mymem/wiki/log.py` | `WikiLog` — append-only `log.md` with `## [date] op | desc` headers |

**Key decisions:**
- `WikiPage` is a frozen dataclass — immutable, never mutated in place
- Frontmatter fields: `title`, `tags`, `sources`, `created`, `updated`, `domain`
- `wikilinks()` method on `WikiPage` extracts `[[Link]]` patterns
- `index.md`: grouped by category, one line per entry with markdown link + summary
- `log.md`: `## [YYYY-MM-DD HH:MM] operation | description` so `grep "^## \["` works

---

## Phase 2: Tag Taxonomy (`mymem/wiki/tags.py`)

Pre-defined tag domains — the "colour coding" system for all wiki pages and queries.

### Domains

| Domain | Examples |
|--------|---------|
| `spiritual` | meditation, stoicism, philosophy, mindfulness, religion |
| `tech` | ml, python, systems, databases, devops, security |
| `finance` | investing, crypto, tax, budgeting, markets |
| `health` | fitness, nutrition, sleep, mental-health |
| `reminder` | todo, follow-up, deadline, action-item |
| `research` | paper, study, hypothesis, experiment |
| `personal` | journal, goals, reflection, relationships |
| `creative` | writing, design, music, art |
| `business` | strategy, product, marketing, ops |
| `misc` | catch-all |

### Features
- Every wiki page has a `domain` field (one of the above) + free-form `tags`
- Domain inferred by LLM at ingest time (can be overridden with `--domain`)
- Curiosity tracker (`data/curiosity.db`) aggregates domain + tag frequency over time
- Web UI sidebar shows domain filter buttons; graph view node colors map to domain

---

## Phase 3: Wiki Directory Setup (`wiki/`)

| File | Purpose |
|------|---------|
| `wiki/index.md` | Home page + wiki catalog |
| `wiki/log.md` | Chronological log |

---

## Phase 4: Multi-LLM Router (`mymem/pipeline/router.py`)

Handles model selection, token budget awareness, and task splitting when context is exhausted.

### Model Registry

| Model | Provider | Context | Best For |
|-------|----------|---------|---------|
| `gemma3:4b` | Ollama | 8k | lint, formatting |
| `gemma3:12b` | Ollama | 8k | compile, QA |
| `gemma4:12b` | Ollama | 128k | long-doc ingest, synthesis |
| `gemma4:27b` | Ollama | 128k | deep reasoning, introspect |
| `claude-haiku-4-5` | Anthropic | 200k | fast classification |
| `claude-sonnet-4-6` | Anthropic | 200k | best quality compile + QA |
| `claude-opus-4-6` | Anthropic | 200k | complex synthesis |

### Task Splitting Strategy

When a source exceeds the model's context budget:

```
Document too long for compile model?
  → ChunkSplitter splits into N overlapping chunks (with 10% overlap)
  → Each chunk compiled by lint/fast model → partial wiki pages
  → Synthesizer (larger model) merges partial pages into final page
  → Log records: "compiled in N chunks via [models]"
```

**Router logic (priority order):**
1. If document fits in configured model → use it directly
2. If document is too long → split into chunks, compile each, merge
3. If configured model is unavailable (Ollama not running) → fallback chain:
   `ollama:gemma4:12b → ollama:gemma3:12b → anthropic:claude-haiku-4-5`
4. Cost guard: if estimated cost > `observability.cost_alert_usd` → warn and ask confirmation

### Files

| File | Purpose |
|------|---------|
| `mymem/pipeline/router.py` | `ModelRouter` — select model, estimate tokens, split tasks |
| `mymem/pipeline/splitter.py` | `ChunkSplitter` — split long docs, merge partial results |
| `mymem/pipeline/llm.py` | Thin async client (Anthropic / Ollama / OpenAI) |

---

## Phase 5: Pipeline (`mymem/pipeline/`)

| File | Purpose |
|------|---------|
| `mymem/pipeline/__init__.py` | Package marker |
| `mymem/pipeline/lint.py` | Pure analysis: detect orphans, broken `[[links]]`, stubs |
| `mymem/pipeline/ingest.py` | `ingest_source()` — raw → router → wiki pages → index + log |
| `mymem/pipeline/query.py` | `query_wiki()` — index → relevant pages → LLM synthesis |
| `mymem/pipeline/introspect.py` | Daily summary + curiosity-driven reading suggestions |

### Introspect Pipeline (`introspect.py`)

The introspect command does three things:

**1. Daily Summary**
- Reads today's `log.md` entries (ingests, queries, lint runs)
- Reads wiki pages touched today
- Asks LLM: "Summarize what was explored today and what was learned"
- Output: `wiki/daily/<YYYY-MM-DD>.md` (can be viewed in Obsidian)

**2. Research Suggestion** (given a topic or question)
- User says: "I'm researching attention mechanisms"
- System: reads curiosity profile → finds related past pages + queries → suggests 3-5 pages to re-read that are likely relevant
- Ranks by: tag overlap + domain match + recency of last access

**3. Curiosity-Driven Recommendations** (ambient, no user input needed)
- Reads curiosity profile (topic/tag frequency over time)
- Looks for pages in the wiki that the user hasn't revisited in > N days
- Looks for topics that spiked recently (new ingests) but lack wiki coverage
- Suggests: "You've been reading a lot about [topic] — you might want to read [X] which connects to your earlier work on [Y]"

### Curiosity Profile (`data/curiosity.db`)

SQLite table tracking user interest signals:

```sql
CREATE TABLE curiosity_events (
    id INTEGER PRIMARY KEY,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT,      -- 'ingest' | 'query' | 'view' | 'daily'
    domain TEXT,          -- tag domain (tech, spiritual, etc.)
    tags TEXT,            -- JSON array of tags
    page_slug TEXT,       -- wiki page involved
    query_text TEXT       -- for query events
);

CREATE TABLE topic_weights (
    domain TEXT,
    tag TEXT,
    weight REAL,          -- exponential decay: recent > old
    last_seen TIMESTAMP,
    PRIMARY KEY (domain, tag)
);
```

Weight decay formula: `weight = Σ exp(-λ * days_ago)` where λ = 0.1 (half-life ≈ 7 days)

---

## Phase 6: CLI (`mymem/cli.py`)

```
mymem ingest <source> [--type article|paper|repo|dataset|image|youtube|podcast|tweet|webpage|book|newsletter|note] [--tags tag1,tag2] [--domain tech]
mymem query "<question>" [--top-k 5] [--save] [--domain spiritual]
mymem lint [--fix-suggestions]
mymem introspect [--topic "attention mechanisms"] [--date 2026-04-08]
mymem serve [--port 7860]
mymem tags                        ← list all domains + tag frequencies
```

- Rich console output with progress indicators
- `--save` on query files the answer back into the wiki as a new page
- `mymem introspect` without args = today's summary + ambient recommendations
- `mymem introspect --topic "X"` = research suggestion mode

---

## Phase 7: Preact Frontend (`frontend/`)

**Status: TODO** — the FastAPI backend and all `/api/*` endpoints are complete.
This phase replaces the Jinja2 templates with a Preact SPA served from `frontend/dist/`.

`mymem/web/app.py` already detects `frontend/dist/` and serves it automatically.
The Jinja2 templates remain as a dev fallback only (no changes to backend required).

### Why Preact

- Jinja2 templates duplicated state, had no component reuse, required full page reloads
- All data is already JSON from `/api/*` — the backend doesn't need to change at all
- Preact is ~3 KB (vs 40 KB React), zero extra dependencies, identical API to React

### Stack

- **Preact** — component model, hooks, JSX
- **Tailwind CSS v4** — utility classes, dark mode via `class` strategy
- **Vite** — dev server (HMR), build tool (`npm run build` → `frontend/dist/`)
- **preact-iso** — client-side routing (no page reloads)
- **D3.js** — force-directed knowledge graph
- **marked.js** — markdown rendering in WikiPage

### Setup

```bash
cd frontend
npm create vite@latest . -- --template preact
npm install -D tailwindcss @tailwindcss/vite
npm install preact-iso d3 marked
```

`vite.config.js`:
```js
import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [preact(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:7860' },
  },
  build: { outDir: '../mymem/web/frontend_dist' },  // served by FastAPI
})
```

### Component Map

| Component | File | Replaces |
|-----------|------|---------|
| App shell + router | `src/app.jsx` | `base.html` |
| Dashboard | `src/pages/Dashboard.jsx` | `dashboard.html` |
| Search / Chat | `src/pages/Search.jsx` | `search.html` |
| Wiki page viewer | `src/pages/WikiPage.jsx` | `wiki_page.html` |
| Knowledge graph | `src/pages/Graph.jsx` | `graph.html` |
| Ingest form | `src/pages/Ingest.jsx` | `ingest.html` |
| Introspect | `src/pages/Introspect.jsx` | `introspect.html` |
| Domain badge pill | `src/components/DomainBadge.jsx` | inline spans |
| Page list item | `src/components/PageCard.jsx` | inline `<a>` tags |
| Activity heatmap | `src/components/Heatmap.jsx` | inline JS in dashboard.html |
| Curiosity bars | `src/components/CuriosityBars.jsx` | inline bars in dashboard.html |
| D3 graph | `src/components/KnowledgeGraph.jsx` | inline D3 in graph.html |
| Scroll-spy TOC | `src/components/ScrollSpyToc.jsx` | inline JS in wiki_page.html |
| Edit meta panel | `src/components/EditMetaPanel.jsx` | `<details>` in wiki_page.html |
| API wrappers | `src/lib/api.js` | scattered fetch calls |
| SSE helper | `src/lib/sse.js` | inline reader in search.html |

### Build Order

1. `frontend/` scaffold — Vite + Preact + Tailwind + preact-iso
2. `src/lib/api.js` — typed wrappers for every `/api/*` endpoint
3. `src/lib/sse.js` — SSE streaming helper (used by Search page)
4. Shared components — `DomainBadge`, `PageCard` (used by Dashboard + Search)
5. `Dashboard.jsx` — stats, heatmap, curiosity bars, page list, quick-ask
6. `Search.jsx` — streaming Q&A, domain filter, citation chips
7. `WikiPage.jsx` — markdown render, scroll-spy TOC, local D3 mini-graph, backlinks, EditMetaPanel
8. `Graph.jsx` — full-screen D3 force-directed graph
9. `Ingest.jsx` — URL / file upload / paste tabs
10. `Introspect.jsx` — daily summary, research suggest input, curiosity trends
11. `App.jsx` — router, nav bar, theme toggle, wrap all pages
12. `npm run build` → verify FastAPI serves `frontend/dist/`
13. Delete Jinja2 `templates/` + `routes/pages.py` once SPA is verified

### API Contract (already stable — do not break)

The Preact frontend consumes these endpoints exactly as documented in `mymem/web/routes/api.py`.
Any changes to API response shapes must be backward-compatible until the SPA is shipped.

---

## Phase 8 (Future): Ontology Graph

Not in the initial build — planned after the wikilink knowledge graph is stable.

Where the wikilink graph shows *navigation connections* (page A links to page B), the
ontology graph captures *semantic typed relationships*:

```
[Concept A] —is-a→        [Concept B]
[Concept A] —part-of→     [Domain X]
[Concept A] —contradicts→ [Concept C]
[Concept A] —supports→    [Claim Y]
[Concept A] —derives-from→ [Source Z]
```

**Relationship types:** `is-a`, `part-of`, `related-to`, `contradicts`, `supports`, `derives-from`

**When building:**
- `mymem/pipeline/ontology.py` — LLM extracts typed relationships at ingest time
- `data/ontology.db` — SQLite: nodes, typed edges
- `GET /ontology` — D3.js graph filterable by relationship type
- `GET /api/ontology` — nodes + typed edges as JSON
- Obsidian: relationships as frontmatter (`is_a: [[Parent Concept]]`)

---

## Phase 9: Tests

| Test file | What it tests | LLM needed? |
|-----------|--------------|-------------|
| `tests/test_wiki.py` | types, page I/O, index CRUD, log append | No |
| `tests/test_tags.py` | domain validation, tag normalization, Dataview snippet | No |
| `tests/test_lint.py` | orphan, broken links, stub detection | No |
| `tests/test_router.py` | model selection, token estimation, chunk splitting | No (pure logic) |
| `tests/test_ingest.py` | ingest flow with mocked LLM + router | Mock only |
| `tests/test_query.py` | query flow with mocked LLM | Mock only |
| `tests/test_introspect.py` | summary + curiosity DB + recommendations | Mock LLM |
| `tests/test_web.py` | all FastAPI routes with TestClient | Mock LLM |

Coverage target: ≥ 80% overall; 100% for `lint.py`, `types.py`, `tags.py`, `router.py`.

---

## Full File Map

```
wiki/
  daily/                          ← introspect daily summaries
  index.md
  log.md
  <slug>.md

frontend/                         ← Phase 7: Preact SPA (TODO)
  src/
    app.jsx                       ← router + nav + theme toggle
    pages/
      Dashboard.jsx
      Search.jsx
      WikiPage.jsx
      Graph.jsx
      Ingest.jsx
      Introspect.jsx
    components/
      DomainBadge.jsx
      PageCard.jsx
      Heatmap.jsx
      CuriosityBars.jsx
      KnowledgeGraph.jsx
      ScrollSpyToc.jsx
      EditMetaPanel.jsx
    lib/
      api.js                      ← fetch wrappers for all /api/* endpoints
      sse.js                      ← SSE streaming helper
  index.html
  vite.config.js
  tailwind.config.js
  package.json
  dist/                           ← built output (gitignored), served by FastAPI

mymem/
  config.py                       ← DONE
  cli.py                          ← DONE
  wiki/
    types.py                      ← DONE
    tags.py                       ← DONE
    page.py                       ← DONE
    index.py                      ← DONE
    log.py                        ← DONE
  pipeline/
    llm.py                        ← DONE
    router.py                     ← DONE
    splitter.py                   ← DONE
    lint.py                       ← DONE
    ingest.py                     ← DONE
    query.py                      ← DONE
    introspect.py                 ← DONE
  web/
    app.py                        ← DONE (SPA-first, Jinja2 fallback)
    routes/
      api.py                      ← DONE (all JSON endpoints)
      pages.py                    ← LEGACY (Jinja2 fallback — delete after Phase 7)
    templates/                    ← LEGACY (delete after Phase 7)
    static/                       ← LEGACY (delete after Phase 7)
  observability/                  ← DONE
  security/                       ← DONE

data/
  mymem.db                        ← LLM traces
  curiosity.db                    ← curiosity events + topic weights

tests/
  test_wiki.py
  test_tags.py
  test_lint.py
  test_router.py
  test_ingest.py
  test_query.py
  test_introspect.py
  test_web.py                     ← tests /api/* routes (unaffected by Phase 7)
```

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Gemma4 token limits vs older Gemma3 | MEDIUM | Router detects model context window from registry; splits accordingly |
| Chunk merge losing cross-chunk context | HIGH | 10% overlap between chunks + merge prompt includes chunk summaries |
| Curiosity weights stale after long gaps | LOW | Exponential decay ensures old signals fade naturally |
| Ollama model not pulled locally | MEDIUM | Router catches connection errors, logs fallback path |
| SSE streaming + FastAPI | LOW | Native `StreamingResponse` in Starlette |

---

## Build Order

### Phases 1–6: DONE

All Python backend code is complete and functional.
The Jinja2 templates are a working interim UI — keep as fallback until Phase 7 ships.

### Phase 7: React Frontend — DONE

The frontend was already scaffolded with React 18 + TypeScript + Tailwind + react-router-dom.
All pages and components were complete. Built successfully to `frontend/dist/`.

```
frontend/
  src/
    App.tsx                    ← router + Navbar
    main.tsx                   ← entry point
    pages/
      DashboardPage.tsx        ✓ stats, domain bars, log, quick-ask SSE, page list
      SearchPage.tsx           ✓ streaming Q&A, domain filter, citation chips
      WikiPage.tsx             ✓ markdown render, TOC, D3 mini-graph, backlinks, edit panel
      GraphPage.tsx            ✓ full D3 force-directed wikilink network
      IngestPage.tsx           ✓ URL / file upload / paste text tabs
      IntrospectPage.tsx       ✓ daily summary, research suggest, curiosity trends
      NotFoundPage.tsx         ✓
    components/
      Navbar.tsx               ✓ nav links + ThemeToggle
      ThemeToggle.tsx          ✓ dark/light mode (localStorage)
      DomainBadge.tsx          ✓
      CitationChip.tsx         ✓ inline citation display
      ClaudeLoader.tsx         ✓ three-dot bouncing loader with cycling witty words
      LoadingSpinner.tsx       ✓ custom CSS ring spinner with witty cycling words
      ErrorBanner.tsx          ✓
    lib/
      api.ts                   ✓ typed wrappers for all /api/* endpoints + SSE streamQuery
      types.ts                 ✓ all shared TypeScript types
      useKeyboardShortcut.ts   ✓ / shortcut to focus search input

FastAPI serves frontend/dist/ automatically (app.py detects it).
Jinja2 templates remain as fallback if dist/ is deleted.

REMAINING → delete mymem/web/templates/ + routes/pages.py (do after confirming SPA is stable)
```

---

## Phase 7b (V1-0001): RAG Infrastructure — DONE

PDF ingestion and hybrid search added as a parallel track alongside the React frontend.

### RAG System (`mymem/rag/`)

| File | Purpose |
|------|---------|
| `mymem/rag/store.py` | sqlite-vec chunk store, vector similarity search, `delete_source()` |
| `mymem/rag/pdf_parser.py` | pypdf extraction + paragraph-aware sliding-window chunking (800 chars, 80 overlap) |
| `mymem/rag/embedder.py` | Ollama `nomic-embed-text` 768-dim embeddings |
| `mymem/rag/ingest.py` | Orchestrate parse → embed → store |

### Router Refactor

Monolithic `mymem/pipeline/router.py` split into a package:
`router/__init__.py`, `_router.py`, `_chain.py`, `_cost.py`, `_registry.py`, `_types.py`, `_utils.py`

### Other V1-0001 Additions

- `mymem/observability/ingest_analytics.py` — quality tracking for YouTube ingests
- `mymem/web/routes/logs.py` — dedicated module for `GET /api/log` + `GET /api/heatmap` (extracted from `api.py`)
- `mymem/pipeline/query.py` — hybrid retrieval: wiki keyword + RAG vector combined
- `mymem/pipeline/search.py` — DDG + Wikipedia fallback + TF-IDF Phase 2
- `mymem/pipeline/ingest.py` — uploaded files persist to `raw/<subdir>/`; PDFs short-circuit to RAG-only
- `mymem serve --dev` flag — skips static serving, adds CORS for Vite dev server (`:5174`), enables reload
- Frontend: `ClaudeLoader`, `CitationChip`, per-port Vite config, SSE proxy fix

---

## Phase 7c (V1-0002): Wiki RAG Chunking + Dashboard Refactor — DONE

### Wiki RAG Chunking (`mymem/rag/wiki_chunker.py`)

Markdown/header + parent-child chunking strategy for wiki pages:
- Strip YAML frontmatter → extract `title`, `domain`, `tags`
- `MarkdownHeaderTextSplitter` on `#` / `##` / `###` → parent sections (≤ 4096 chars)
- `RecursiveCharacterTextSplitter` splits each section into ~300-token child chunks (30-token overlap)
- Embed text prefixed as `"{page_title} > {heading_path}: {child_text}"` for retrieval precision
- Each chunk stores: `source_path`, `source_slug`, `heading_path`, `parent_text`, `chunk_type`, `page_title`, `domain`, `tags`
- `rag/ingest.py` gets `ingest_wiki_page(force=True)` — clears + re-inserts on every wiki page write
- `pipeline/ingest.py` calls `_rag_index_wiki()` fire-and-forget after every wiki write

### Dashboard Layout Refactor (`DashboardPage.tsx`)

3-zone full-height layout: `left (240px) | center (flex-1) | right slide-in (420px)`
- Right panel: `w-0 → w-[420px]` via `transition-all duration-300` (Claude Code panel pattern)
- Answer output: `max-h-[55vh] overflow-y-auto` — scrollable, never pushes page
- `max-w-screen-2xl` on `<main>`; `h-[calc(100vh-56px-2rem)]` on root

---

## Phase 7d (V1-0003 — current): PDF Chunking Upgrade — IN PROGRESS

Upgrade `mymem/rag/pdf_parser.py` from paragraph-aware sliding-window to document-layout + metadata-aware strategy:
- Split on actual PDF headings/sections instead of arbitrary token counts
- Store per-chunk metadata: `heading_path`, `page_title`, `source_tags`, `modified_date`
- Target: 300–800 tokens per chunk, 50–150 token overlap
- Retrieve small chunks → return parent section when answering

---

## Phase 7d-UX (V1-0003): Frontend UX Overhaul — DONE

### Dashboard Chat Layout (`DashboardPage.tsx`)

Full 3-column chat layout — Perplexity/Notion AI style:
- **Left sidebar** (`w-52`): domain filter dots with counts, heatmap tiles per domain, stats footer
- **Center**: domain chip row + scrollable chat thread (`Message[]` array) + bottom input bar
- **Right panel** (`w-72`): Wiki Pages / Memory tabs with search + page list
- `Message` type: `{id, role, text, citations, phase, timestamp, error?}`
- `ThinkingLoader`: 3 bouncing dots with staggered delay, cycling status strings (`setInterval 1800ms`), shimmer skeleton lines
- `MessageBubble`: shows loader when `phase === 'streaming' && text === ''`, blinking cursor while streaming, prose + source cards + wiki cards when done
- Full-height layout via `-mx-4 -my-4 h-[calc(100vh-3.5rem)]` to break out of App padding

### Navbar Redesign (`Navbar.tsx`)

- Logo mark: `w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600` with glow blur layer + sparkles SVG
- Brand text: `My` (gray) + `Mem` (gradient `from-indigo-600 to-violet-600 bg-clip-text text-transparent`)
- Nav links right: Graph | Introspect | Search (hidden, `className="hidden"`) | divider | +Ingest | ThemeToggle

### Introspect Diversification (`IntrospectPage.tsx` + backend)

Three new interactive sections added to the Introspect page:

**Research Topic**
- Text input + Suggest button (Enter key supported)
- Calls `GET /api/introspect?topic=<query>` → renders LLM summary + swipeable recommended pages

**Test Yourself (Quiz Generator)**
- Generate 5 questions from recent wiki pages
- Per-card: question text + difficulty badge (easy/medium/hard), click-to-reveal hint + wiki page link
- Reveal all / Hide all controls; "New set" button to regenerate

**Knowledge Digest**
- 7-day / 30-day period toggle (clears digest on switch)
- Calls `GET /api/introspect/digest?period=N`
- Displays: stats bar (pages active, queries made, date range), themes grid with page links, emerging connections (emerald), knowledge gaps (amber), serendipity callout, italic open question

**Backend additions** (`mymem/pipeline/introspect.py`):
- `QuizQuestion`, `DigestTheme`, `DigestResult` dataclasses
- `generate_questions(wiki_dir, router, n_pages=5)` — picks N most-recent pages, JSON-only LLM prompt
- `generate_digest(wiki_dir, log_path, curiosity_db, router, period_days=7)` — reads log window, counts activity, JSON-only LLM prompt
- `_extract_json()` helper to strip markdown code fences before `json.loads`
- `GET /api/introspect/questions` and `GET /api/introspect/digest` endpoints

**Frontend additions**:
- `types.ts`: `QuizQuestion`, `DigestTheme`, `DigestResult`
- `api.ts`: `fetchQuestions(n)`, `fetchDigest(period)`
- `index.css`: `@keyframes fadeIn` for ThinkingLoader status text

---

## Phase 7e (Research): Markdown, HTML, and Generated Pages

Research whether MyMem should keep wiki pages as markdown only, move selected pages to HTML, or support a hybrid markdown + HTML page model. This must account for pages generated on the fly during ingest, query save, introspection, eval reporting, and future agent workflows.

### Questions to Answer

| Question | Why it matters |
|----------|----------------|
| Should markdown remain the canonical storage format? | Keeps Obsidian/editability/simple git diffs, but limits rich layouts and interactive blocks |
| Should HTML be stored, rendered, or generated on demand? | Determines whether HTML becomes source of truth, cache artifact, or frontend output |
| Which page types need HTML? | Dashboards, timelines, source reports, eval reports, graph views, and generated research briefs may need richer structure than markdown |
| Can a mixed model preserve wikilinks, backlinks, tags, domains, and RAG indexing? | Existing wiki machinery depends on markdown metadata and link extraction |
| How should generated-on-the-go content be cached and invalidated? | Query answers, introspect summaries, and ingest reports may be expensive to regenerate |

### Options to Evaluate

1. **Markdown canonical, HTML rendered at view time**
   - Keep `wiki/*.md` as source of truth.
   - Frontend/API converts markdown to HTML or React components when viewed.
   - Best for compatibility with current pipeline, Obsidian, git, and RAG indexing.

2. **Markdown canonical, optional generated HTML cache**
   - Store markdown pages normally.
   - Add `data/render_cache.db` or `wiki/.rendered/<slug>.html` for expensive rendered views.
   - Invalidate cache when page `updated` changes, renderer version changes, or linked dependencies change.

3. **Hybrid page types**
   - Add a `content_type` frontmatter field: `markdown`, `html`, `generated`, or `report`.
   - Markdown remains default; HTML/report pages opt into richer rendering.
   - Requires API and frontend support for safe rendering, sanitization, search, and backlinks.

4. **HTML canonical for selected generated artifacts**
   - Use HTML as durable output for reports that are layout-heavy or interactive.
   - Requires strong sanitization, metadata sidecars, and a way to extract text/links for index + RAG.
   - Highest risk; only pursue if markdown/rendered-cache cannot support the use cases.

### Generated-On-The-Go Requirements

- Generated pages must have stable slugs, provenance, source inputs, and renderer/model version metadata.
- Every generated artifact should declare whether it is `ephemeral`, `cached`, or `committed`.
- Ephemeral content can stream directly to the UI and disappear after the session.
- Cached content can be reused but regenerated when dependencies change.
- Committed content becomes a real wiki page and must update `index.md`, `log.md`, backlinks, curiosity events, and RAG chunks.
- Generated HTML must be sanitized before display and should never bypass the existing security layer.

### Research Tasks

1. Audit current markdown assumptions in `mymem/wiki/`, `mymem/pipeline/`, `mymem/rag/wiki_chunker.py`, and `frontend/src/pages/WikiPage.tsx`.
2. Prototype a `PageArtifact`/`RenderedPage` API shape for `/api/page/:slug` with `body`, `content_type`, `rendered_html`, `metadata`, and `cache_status`.
3. Test whether `marked.js` plus custom React block components is enough for rich generated pages before storing raw HTML.
4. Design cache invalidation rules using page `updated`, dependency slugs, renderer version, and prompt/model version.
5. Define sanitization rules for stored or generated HTML, including allowed tags, allowed attributes, link rewriting, and script removal.
6. Verify indexing behavior: wikilink extraction, backlinks, full-text search, RAG chunking, citations, and graph edges must work across markdown and HTML/generated pages.
7. Decide the V1 path, likely markdown canonical + optional render cache unless a specific page type proves it needs HTML canonical storage.

### Proposed Decision Gate

Before implementation, produce `docs/html_pages_research.md` with:
- Recommended storage model
- API response contract
- Frontend rendering approach
- Security/sanitization plan
- Cache invalidation plan
- Migration impact on existing `wiki/*.md`

---

## Phase 8 (Future): Query Improvements

V1-0004: Hybrid retrieval re-ranking, better citations, heading-path context in answers.

### Phase 9 (Future): Ontology Graph

Not in current build. See bottom of this file.

---

### Phase 9/10: Tests

Coverage target: ≥ 80% overall; 100% for `lint.py`, `types.py`, `tags.py`.
`test_web.py` tests `/api/*` routes with `TestClient` — unaffected by Phase 7.
