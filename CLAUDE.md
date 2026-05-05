# MyMem — Claude Project Context

## What This Is

MyMem is a personal LLM-powered wiki system with two interfaces:
- **CLI** — `mymem ingest / query / lint / serve` from the terminal
- **Web UI** — FastAPI dashboard for chat, search, and browsing (http://localhost:7860)

Instead of RAG (re-deriving answers every query), the LLM incrementally builds and maintains
a persistent wiki of interlinked markdown files. New sources are compiled once; queries read
the pre-built wiki.

## Stack

**Backend**
- **Language**: Python 3.11+ (strict mypy)
- **CLI**: Typer + Rich
- **API**: FastAPI — pure JSON `/api/*` routes, no HTML rendering
- **Config**: Pydantic Settings + config.yaml (secrets in .env only)
- **LLM providers**: Ollama (default, local), Anthropic, OpenAI
- **Storage**: SQLite (via sqlite-vec) for traces; markdown files for wiki
- **Testing**: pytest + pytest-asyncio + pytest-cov (≥ 80% coverage required)
- **Linting**: ruff + mypy strict

**Frontend**
- **Framework**: React 18 + TypeScript
- **Styling**: Tailwind CSS v3
- **Build tool**: Vite 5
- **Routing**: react-router-dom v6 (SPA, history mode)
- **Graph**: D3.js v7 (force-directed knowledge graph)
- **Markdown**: marked.js (wiki page rendering)
- **Dev server**: `npm run dev` in `frontend/` (proxies `/api` to FastAPI on :7860)

## Dev Setup

```bash
# Backend
pip install -e ".[dev]"                         # install with dev deps
mymem serve --port 7860                         # start FastAPI (API only)
pytest                                          # run tests
pytest --cov=mymem --cov-report=term-missing    # with coverage

# Frontend (separate terminal)
cd frontend
npm install
npm run dev          # Vite dev server on http://localhost:5173 (proxies /api → :7860)
npm run build        # builds to frontend/dist/ — served by FastAPI in prod

# Combined (production mode)
cd frontend && npm run build && cd ..
mymem serve --port 7860                         # serves frontend/dist/ + /api/*
```

## Directory Structure

```
raw/                        # Immutable source documents (never modified by LLM)
  articles/
  papers/
  datasets/
  images/
  repos/
wiki/                       # LLM-generated markdown pages
  index.md                  # Catalog + home page (updated on every ingest)
  log.md                    # Append-only operation log
  <slug>.md                 # Individual wiki pages with YAML frontmatter
outputs/
  charts/                   # matplotlib charts
  slides/                   # Marp slide decks
data/
  mymem.db                  # SQLite: LLM traces, cost tracking
  curiosity.db              # Curiosity events + topic weights
  mymem.log                 # Structured log file

frontend/                   # Preact + Tailwind SPA
  src/
    main.jsx                # App entry — router setup
    app.jsx                 # Root: nav, theme toggle
    pages/
      Dashboard.jsx         # Stats, heatmap, curiosity bars, quick search
      Search.jsx            # Streaming Q&A with SSE, domain filter
      WikiPage.jsx          # Markdown render, scroll-spy TOC, graph toggle, backlinks
      Graph.jsx             # Full force-directed wikilink network (D3)
      Ingest.jsx            # URL / file upload / paste text form
      Introspect.jsx        # Daily summary + curiosity recommendations
    components/
      DomainBadge.jsx       # Colored domain pill (reused everywhere)
      PageCard.jsx          # Page list item with title + domain badge
      Heatmap.jsx           # 16-week activity heatmap
      CuriosityBars.jsx     # Domain frequency bar chart
      KnowledgeGraph.jsx    # D3 force-directed graph component
      ScrollSpyToc.jsx      # Scroll-aware table of contents
      EditMetaPanel.jsx     # Edit domain + tags on wiki pages
    lib/
      api.js                # fetch wrappers for all /api/* endpoints
      sse.js                # SSE streaming helper for /api/query
  index.html
  vite.config.js            # Proxy /api → http://localhost:7860
  tailwind.config.js
  package.json
  dist/                     # Built output — served by FastAPI in prod (gitignored)

mymem/
  config.py                 # Settings (pydantic-settings + config.yaml)
  cli.py                    # Typer CLI: ingest / query / lint / serve
  wiki/
    types.py                # WikiPage, IndexEntry, LogEntry, LogOperation
    page.py                 # read_page, write_page, list_pages
    index.py                # IndexManager (load/save/upsert/remove)
    log.py                  # WikiLog (append/load/recent)
  pipeline/
    llm.py                  # LLM client abstraction (Anthropic/Ollama/OpenAI)
    router.py               # ModelRouter: select model, fallback chain, cost tracking
    splitter.py             # ChunkSplitter: split long docs, merge results
    ingest.py               # ingest_source(): raw → wiki pages via LLM
    query.py                # query_wiki(): wiki search + LLM synthesis
    lint.py                 # lint_wiki(): pure analysis, no LLM needed
    introspect.py           # Daily summary + curiosity engine
  web/
    app.py                  # FastAPI factory — serves frontend/dist/ + /api/* routes
    routes/
      api.py                # All JSON API endpoints (no HTML)
      pages.py              # LEGACY: Jinja2 fallback (used only when frontend/dist missing)
    templates/              # LEGACY: Jinja2 templates (kept as dev fallback only)
    static/                 # LEGACY: raw CSS/JS (superseded by Vite build)
  observability/            # DONE: logger, tracer, health
  security/                 # DONE: scanner, sanitize, validate
```

## Wiki Page Format

Every wiki page is a markdown file with YAML frontmatter:

```markdown
---
title: Concept Title
tags: [tag-a, tag-b]
domain: tech
sources: [source-article.md]
created: 2026-04-01
updated: 2026-04-08
---

# Concept Title

Main content here.

## See Also

- [[Related Concept A]]
- [[Related Concept B]]
```

- **`title`** — human-readable, used as the display name in index.md
- **`tags`** — lowercase, used for filtering and grouping
- **`sources`** — filenames from `raw/` that contributed to this page
- **`created` / `updated`** — ISO date strings (YYYY-MM-DD)
- **`[[Link]]`** — wikilinks; `wikilinks()` extracts them

Slug convention: `title.lower().replace(" ", "-")` + `.md`

## index.md Format

```markdown
# Wiki Index

## Concepts

- [Concept A](concept-a.md) — One-line summary of what this page covers (2 sources)
- [Concept B](concept-b.md) — One-line summary (1 source)

## Papers

- [Paper Title](paper-title.md) — One-line summary (1 source)
```

- Grouped by category (from `IndexEntry.category`)
- One line per entry: `- [Title](path) — summary (N sources)`
- Updated atomically on every ingest

## log.md Format

```markdown
## [2026-04-08 14:23] ingest | source-article.md
Updated: transformer-architecture.md, attention-mechanism.md

## [2026-04-08 15:01] query | What is multi-head attention?
Result saved: wiki/qa/multi-head-attention.md
```

- Each entry header starts with `## [` — parseable with `grep "^## \[" log.md`
- Append-only — never overwrite previous entries
- Operations: `ingest`, `query`, `lint`

## Operations

### Ingest
```bash
mymem ingest raw/articles/my-article.md --type article --tags tag-a,tag-b --domain tech
mymem ingest "https://youtu.be/VIDEO_ID" --type youtube --domain tech
mymem ingest "https://example.com/post" --type webpage --domain personal
# or via Web UI: POST /api/ingest
```
Flow: security scan → read source (type-aware) → **router selects model** (splits if too long) → LLM extract ideas → LLM write/update wiki pages → update index.md → log curiosity event → append log.md

**Source types:** `article` | `paper` | `repo` | `dataset` | `image` | `youtube` | `podcast` | `tweet` | `webpage` | `book` | `newsletter` | `note`

YouTube requires: `pip install youtube-transcript-api` (or `pip install -e ".[media]"`)

### Query
```bash
mymem query "What is the difference between self-attention and cross-attention?" --save --domain tech
# or via Web UI: chat interface at /search (streaming SSE)
```
Flow: read index.md → find relevant pages by keyword + domain → LLM synthesize → log curiosity event → (if --save) write answer as wiki page → append log.md

### Lint
```bash
mymem lint
# or via Web UI: GET /api/lint
```
Flow (pure Python, no LLM): scan all wiki pages → detect orphans → detect broken `[[wikilinks]]` → detect stubs → print report

### Introspect
```bash
mymem introspect                          # today's summary + ambient recommendations
mymem introspect --topic "stoic ethics"   # research suggestion mode
mymem introspect --date 2026-04-07        # past day summary
# or via Web UI: /introspect
```
Flow: read today's log.md entries → read touched pages → LLM daily summary → read curiosity.db weights → rank wiki pages by relevance decay → output recommendations → save `wiki/daily/YYYY-MM-DD.md`

### Serve
```bash
mymem serve --port 7860
```
Starts FastAPI + uvicorn. Dashboard at http://localhost:7860.

### Tags
```bash
mymem tags          # list all domains + tag frequencies from curiosity.db
```

## Web UI

**Design reference:** [sage-wiki](https://github.com/xoai/sage-wiki) — adopt its layout patterns,
component structure, and aesthetic.

### Architecture

FastAPI is a **pure JSON API server** — it has no HTML rendering responsibility.
The Preact SPA (built by Vite into `frontend/dist/`) is served as static files.
All `/api/*` calls are proxied to FastAPI by Vite in dev; served directly in prod.

```
Browser → Vite dev server (:5173)
             ├─ /api/*  → proxy → FastAPI (:7860)
             └─ /*      → Preact SPA (HMR)

Browser → FastAPI (:7860) [production]
             ├─ /api/*        → JSON routes
             ├─ /assets/*     → Vite build assets
             └─ /*            → frontend/dist/index.html (SPA fallback)
```

### Key UI features

- **Dark/light mode toggle** with system preference detection (`prefers-color-scheme`)
- **Force-directed knowledge graph** — wikilink network, nodes colored by domain (D3)
- **Streaming Q&A panel** — conversational with inline citations via SSE
- **Scroll-spy TOC** — outline that follows the reader; toggles to local graph view
- **Hybrid search** — ranked results with preview snippets + domain filter pills
- **Activity heatmap** — 16-week GitHub-style ingest/query heatmap on dashboard
- **Broken links in gray** — visually distinguish incomplete wikilink references
- **Edit domain & tags** inline on wiki pages (PATCH /api/page/:slug)

### API Endpoints (FastAPI — unchanged by frontend migration)

| Endpoint | Description |
|----------|-------------|
| `POST /api/query` | Streaming SSE — answer + citations |
| `GET /api/pages` | Page list (filterable by domain/tag) |
| `GET /api/stats` | Page count, sources, orphans, domain breakdown, session cost |
| `GET /api/graph` | Graph nodes + edges as JSON |
| `POST /api/ingest` | Trigger ingest from URL or path |
| `POST /api/upload` | Multipart file upload → ingest |
| `POST /api/ingest-text` | Paste raw text → ingest |
| `GET /api/page/:slug` | Single page data (title, body, domain, tags, backlinks, toc) |
| `PATCH /api/page/:slug` | Update domain + tags on a wiki page |
| `GET /api/log` | Recent wiki log entries |
| `GET /api/lint` | Lint issues as JSON |
| `GET /api/introspect` | Today's summary + recommendations as JSON |
| `GET /api/curiosity` | Top domains, tags, trend direction (rising/fading) |

### Ontology Graph (planned — not in initial build)

A future layer on top of the wikilink graph. Where the wikilink graph shows *navigation connections*
(page A links to page B), the ontology graph shows *semantic relationships*:

```
[Concept A] —is-a→ [Concept B]
[Concept A] —part-of→ [Domain X]
[Concept A] —contradicts→ [Concept C]
[Concept A] —evidence-for→ [Claim Y]
```

Relationship types: `is-a`, `part-of`, `related-to`, `contradicts`, `supports`, `derives-from`, `see-also`

**Implementation approach (when ready):**
- LLM extracts typed relationships during ingest → stored in `data/ontology.db`
- Separate `GET /ontology` page with D3.js force-directed graph, filterable by relationship type
- `GET /api/ontology` endpoint returns nodes + typed edges as JSON


**File placeholders to add when building:**
- `mymem/pipeline/ontology.py` — extract + store typed relationships
- `data/ontology.db` — SQLite: nodes, edges, relationship types
- `mymem/web/routes/ontology.py` — API + page route

## What's Already Built

| Module | Status |
|--------|--------|
| `mymem/config.py` | DONE |
| `mymem/observability/` | DONE + tested |
| `mymem/security/` | DONE + tested |
| `mymem/wiki/types.py` | DONE — includes canonical `slugify()` and `archived` field |
| `mymem/wiki/tags.py` | DONE |
| `mymem/wiki/page.py` | DONE — includes `list_archived_pages()` |
| `mymem/wiki/index.py` | DONE |
| `mymem/wiki/log.py` | DONE |
| `mymem/pipeline/llm.py` | DONE |
| `mymem/pipeline/router.py` | DONE |
| `mymem/pipeline/splitter.py` | DONE |
| `mymem/pipeline/search.py` | DONE — DDG + Wikipedia fallback + TF-IDF Phase 2 |
| `mymem/pipeline/ingest.py` | DONE — auto RAG-indexes local PDFs after wiki generation |
| `mymem/pipeline/query.py` | DONE — hybrid wiki + RAG vector retrieval |
| `mymem/pipeline/lint.py` | DONE |
| `mymem/pipeline/introspect.py` | DONE |
| `mymem/cli.py` | DONE |
| `mymem/rag/store.py` | DONE — sqlite-vec chunk store (100% tested) |
| `mymem/rag/pdf_parser.py` | DONE — pypdf extraction + sliding-window chunking (100% tested) |
| `mymem/rag/embedder.py` | DONE — Ollama nomic-embed-text, 768-dim (96% tested) |
| `mymem/rag/ingest.py` | DONE — orchestrates parse → embed → store (100% tested) |
| `mymem/web/routes/api.py` | DONE — all JSON endpoints incl. delete/archive/restore/rag |
| `mymem/web/app.py` | DONE — serves SPA or Jinja2 fallback |
| `mymem/web/routes/pages.py` | LEGACY — Jinja2 fallback only |
| `mymem/web/templates/` | LEGACY — replaced by React SPA |
| `frontend/` | DONE — React + TypeScript SPA, built to `frontend/dist/` |
| `data/curiosity.db` | DONE — schema created on first run |
| `data/rag.db` | DONE — created on first PDF ingest (sqlite-vec virtual table) |

## Tag Taxonomy

Every wiki page has a `domain` (one of the pre-defined domains below) plus free-form `tags`.
Domain is set by LLM at ingest time, overridable via `--domain`. Tags are always lowercase.

| Domain | Keywords / Examples |
|--------|-------------------|
| `spiritual` | meditation, stoicism, philosophy, mindfulness, religion, consciousness |
| `tech` | ml, python, systems, databases, devops, security, programming |
| `finance` | investing, crypto, tax, budgeting, markets, trading |
| `health` | fitness, nutrition, sleep, mental-health, therapy |
| `reminder` | todo, follow-up, deadline, action-item, note-to-self |
| `research` | paper, study, hypothesis, experiment, literature |
| `personal` | journal, goals, reflection, relationships, identity |
| `creative` | writing, design, music, art, fiction |
| `business` | strategy, product, marketing, ops, startup |
| `misc` | catch-all for anything unclassified |

Domain in frontmatter:
```yaml
domain: tech
tags: [ml, attention, transformers]
```

## Multi-LLM Router

`mymem/pipeline/router.py` — never call `llm.py` directly from pipeline code.
Always go through the router so fallbacks and task-splitting are applied automatically.

### Model Registry (in `config.yaml`)

```yaml
models:
  compile: gemma4:12b          # long-doc ingest (128k context)
  qa: gemma3:12b               # wiki Q&A
  lint: gemma3:4b              # fast health checks
  classify: gemma3:4b          # domain/tag classification
  merge: gemma4:27b            # merge compiled chunks
  introspect: gemma4:12b       # daily summary + recommendations
  embed: nomic-embed-text      # embeddings (always local)
```

### Fallback Chain (if model unavailable)

```
gemma4:27b → gemma4:12b → gemma3:12b → claude-haiku-4-5 → claude-sonnet-4-6
```

### Task Splitting

When a document exceeds the model's context window:
1. `ChunkSplitter` divides into overlapping chunks (10% overlap)
2. Each chunk compiled independently by `compile` model
3. Partial pages merged by `merge` model (larger context)
4. Final page written; log records: "compiled in N chunks"

## Introspect + Curiosity Engine

`mymem/pipeline/introspect.py` — three modes:

**Daily Summary** (no args): reads today's log + touched pages → LLM summary → saves `wiki/daily/YYYY-MM-DD.md`

**Research Suggestion** (`--topic "X"`): reads curiosity profile → finds related past pages → ranks by tag overlap + domain match + recency decay → returns top 5

**Ambient Recommendations** (embedded in daily summary):
- Topics with rising weight in `curiosity.db` but sparse wiki coverage → "gap suggestions"
- Pages not revisited in > 14 days but historically high-weight → "revisit suggestions"
- Queries with no saved wiki page → "unsaved insight suggestions"

### Curiosity Weight Decay

`weight = Σ exp(-0.1 * days_ago)` — half-life ≈ 7 days. Recent activity dominates.
Stored per (domain, tag) pair in `data/curiosity.db`.

## Code Rules

- **Immutable data** — use frozen dataclasses; never mutate in place
- **No `any` type** — strict mypy, every function fully typed
- **No hardcoded secrets** — all from `.env` or `config.yaml`
- **No LLM calls in tests** — inject `llm_fn` parameter; mock in tests
- **Error handling** — never silently swallow; log with context
- **File size** — keep modules < 300 lines; split if growing

## Environment Variables

```
ANTHROPIC_API_KEY=   # required if provider=anthropic
OPENAI_API_KEY=      # required if provider=openai
```

`.env` is gitignored. Provider defaults to `ollama` (no key needed).

## Config Override

Edit `config.yaml` to change provider or model assignments per task:

```yaml
provider: anthropic
# Anthropic model routing (uncomment to override):
# anthropic_models:
#   compile: claude-sonnet-4-6
#   lint: claude-haiku-4-5-20251001
#   qa: claude-sonnet-4-6
```

## Testing Rules

- Run `pytest` before marking any task done
- Mock all LLM calls — never require a running Ollama/Anthropic in tests
- `lint.py` must have 100% coverage (it's pure Python)
- Use `tmp_path` fixture for all file I/O tests
- Web routes tested with FastAPI `TestClient` (no real server needed)
- See PLAN.md for the full build order
