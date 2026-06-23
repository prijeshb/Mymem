# MyMem

A personal LLM-powered wiki that **builds and maintains itself** from your sources.

Drop in articles, PDFs, YouTube videos, web pages, or raw notes — the LLM reads them, extracts knowledge, and writes interlinked markdown pages. Queries read the pre-built wiki instead of re-deriving answers every time.

---

## How it works

```
Sources (URLs, PDFs, YouTube, files)
        ↓  ingest
   LLM synthesises
        ↓
  Persistent wiki  ←→  Wikilink graph
  (markdown pages)
        ↓  query
  Hybrid search (BM25 + RAG vector)
        ↓
  LLM answer + citations
```

Unlike pure RAG tools that index raw chunks, MyMem **accumulates knowledge** — each source is compiled into structured wiki pages that grow and interlink over time.

---

## Screenshots

### Dashboard — chat interface with domain heatmap and wiki sidebar
![Dashboard](docs/screenshots/dashboard.png)

### Search — Q&A answered from your wiki
![Search](docs/screenshots/search.png)

### Knowledge Graph — D3 force-directed wikilink network
![Graph](docs/screenshots/graph.png)

### Introspect — daily summary, quiz generator, knowledge digest
![Introspect](docs/screenshots/introspect.png)

### Ingest — add any source (URL, file upload, paste text)
![Ingest](docs/screenshots/ingest.png)

---

## Features

- **LLM wiki compiler** — sources compiled into interlinked markdown pages; knowledge accumulates over time
- **Hybrid retrieval** — BM25 keyword search + RAG vector search (sqlite-vec) combined at query time
- **Multi-LLM router** — Ollama (local, default), Anthropic, OpenAI with automatic fallback chain
- **Domain taxonomy** — pages tagged by domain (tech, finance, spiritual, health, personal, …) for filtered retrieval
- **Knowledge graph** — D3 force-directed wikilink network, filterable by domain
- **Entity graph & knowledge gaps** — typed entities anchored on stable page ids; surfaces "pages worth writing next" (concepts you link to but haven't written) ranked by reference count
- **Compounding claims** — bi-temporal claim ledger with ADD/MERGE/SUPERSEDE provenance; pages can render their body from accumulated claims
- **OKF interchange** — export to / import from Google Cloud's Open Knowledge Format (markdown bundles), with lossless identity-stable round-trips
- **Introspect engine** — daily summary, research topic suggestions, quiz generator, knowledge digest
- **PDF support** — layout-aware chunking, RAG-only indexing for dense documents
- **Source types** — article, paper, repo, dataset, image, YouTube, podcast, tweet, webpage, book, note
- **Eval framework** — wiki quality, chunk ablation (HOPE score), self-supervised BM25 retrieval eval, RAGAS-lite LLM judge
- **Dark / light mode** — system preference detected, togglable

---

## Quick start

Works the same on macOS, Linux, and Windows (Python ≥3.11 + Node for the UI).

### Backend

```bash
# 1. Create a virtualenv and install
python -m venv venv
#   macOS/Linux:  source venv/bin/activate
#   Windows:      venv\Scripts\activate
pip install -e ".[dev]"

# 2. (Optional) personalise config — sensible defaults apply with no config.yaml
cp config.example.yaml config.yaml   # defaults to LOCAL Ollama, no API key needed
cp .env.example .env                 # only needed for a hosted provider's key

# 3. Build the UI (served by the API at /), then start the server
cd frontend && npm install && npm run build && cd ..
mymem serve --port 7860
```

Opens at **http://localhost:7860**

> **Providers:** the default is local **Ollama** (no key — just `ollama serve` with the
> models in `config.example.yaml` pulled). To use a hosted provider, set `provider:` in
> `config.yaml` and put its key in `.env`.
>
> **Windows note:** if you hit a `charmap` error, prefix commands with `PYTHONUTF8=1`
> (PowerShell: `$env:PYTHONUTF8=1`).

### Frontend (dev mode, hot reload)

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173, proxies /api → :7860
npm test           # Vitest unit tests
```

### CLI

```bash
# Ingest a source
mymem ingest "https://example.com/article" --type article --domain tech

# Ingest a local PDF
mymem ingest raw/papers/attention.pdf --type paper --tags ml,attention

# Ingest a YouTube video
mymem ingest "https://youtu.be/VIDEO_ID" --type youtube --domain tech

# Ask a question
mymem query "What is the difference between self-attention and cross-attention?"

# Run the eval suite
mymem eval

# Lint the wiki (find orphans, broken wikilinks, stubs)
mymem lint

# Daily introspection
mymem introspect

# Entity graph: seed/repair, then see the highest-value pages to write next
mymem graph backfill
mymem graph gaps

# Interop: export the wiki to an OKF bundle, or import one back (lossless round-trip)
mymem export okf ./okf-bundle
mymem import okf ./okf-bundle
```

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Typer |
| LLM providers | Ollama (local), Anthropic, OpenAI |
| Vector store | sqlite-vec (embedded, no server) |
| Frontend | React 18 + TypeScript, Vite, Tailwind CSS v3 |
| Graph | D3.js v7 (force-directed) |
| Config | Pydantic Settings + config.yaml |
| Testing | pytest + pytest-asyncio (≥80% coverage) |

---

## Project structure

```
raw/          # Immutable source documents (never modified by LLM)
wiki/         # LLM-generated markdown pages with YAML frontmatter
data/         # SQLite databases (traces, RAG embeddings, curiosity weights, evals)
frontend/     # React SPA → built to frontend/dist/ served by FastAPI in prod
mymem/
  pipeline/   # ingest, query, lint, introspect, reconcile/compounding, multi-LLM router
  knowledge/  # bi-temporal claims ledger + retrieval + render; okf/ (OKF export/import)
  graph/      # entity store, extractor, 3-tier resolver, backfill, knowledge gaps
  rag/        # sqlite-vec chunk store, embedder, wiki chunker, PDF parser
  wiki/       # page CRUD, index, log, types, stable-id identity
  evals/      # wiki quality, chunking ablation, retrieval eval, RAGAS-lite
  web/        # FastAPI routes + app factory
  observability/ # structured logger, LLM call tracer, health
  security/   # input scanner, sanitizer, validator
```

---

## Environment variables

```bash
ANTHROPIC_API_KEY=   # required if provider=anthropic
OPENAI_API_KEY=      # required if provider=openai
```

Copy `.env.example` to `.env` (or set directly). Provider defaults to `ollama` — no key needed for local use.

---

## Configuration

Edit `config.yaml` to change models per task:

```yaml
provider: ollama   # ollama | anthropic | openai

models:
  compile:    gemma3:12b       # long-doc ingest
  qa:         gemma3:12b       # wiki Q&A
  lint:       gemma3:4b        # fast health checks
  embed:      nomic-embed-text # embeddings (always local)
```

---

## Eval suite

```bash
mymem eval
```

Runs automatically with self-supervised test cases derived from your wiki:

| Eval | What it measures |
|---|---|
| Wiki quality | Richness score, stub rate, wikilink density, lifecycle states |
| Chunk ablation | HOPE score (boundary integrity, context completeness) across chunk sizes |
| Retrieval (BM25) | Precision@k, MRR, UDCG — self-supervised from your own pages |
| RAGAS-lite | Faithfulness + answer relevancy via LLM judge (`--llm-judge`) |

---

## Source types supported

`article` · `paper` · `repo` · `dataset` · `image` · `youtube` · `podcast` · `tweet` · `webpage` · `book` · `newsletter` · `note`

YouTube requires: `pip install -e ".[media]"`

---

## License

MIT
