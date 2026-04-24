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
      CitationChip.tsx         ✓
      LoadingSpinner.tsx       ✓
      ErrorBanner.tsx          ✓
    lib/
      api.ts                   ✓ typed wrappers for all /api/* endpoints + SSE streamQuery
      types.ts                 ✓ all shared TypeScript types
      useKeyboardShortcut.ts   ✓ / shortcut to focus search input

FastAPI serves frontend/dist/ automatically (app.py detects it).
Jinja2 templates remain as fallback if dist/ is deleted.

REMAINING → delete mymem/web/templates/ + routes/pages.py (do after confirming SPA is stable)

### Phase 8 (Future): Ontology Graph

Not in current build. See bottom of this file.
```

### Phase 9: Tests

Coverage target: ≥ 80% overall; 100% for `lint.py`, `types.py`, `tags.py`.
`test_web.py` tests `/api/*` routes with `TestClient` — unaffected by Phase 7.
