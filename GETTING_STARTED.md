# Getting Started — MyMem

## Prerequisites

- Python 3.11+
- Node.js (for frontend, if applicable)
- [Ollama](https://ollama.ai) running locally (default provider — no API key needed)

---

## 1. Activate Virtual Environment

```bash
# Windows CMD
venv\Scripts\activate

# Git Bash / bash
source venv/Scripts/activate
```

---

## 2. Install Dependencies

```bash
pip install -e ".[dev]"
```

---

## 3. Configure Environment

Create a `.env` file in the project root (already gitignored):

```env
# Required only if using Anthropic or OpenAI provider
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
```

Default provider is `ollama` — no key needed.

To switch provider, edit `config.yaml`:

```yaml
provider: anthropic
```

---

## 4. Start the Web UI

```bash
# Production mode — serves built frontend
mymem serve --port 7860

# Dev mode — enables CORS for Vite dev server, hot-reload, no static serving
mymem serve --port 7860 --dev
```

Open **http://localhost:7860** in your browser.

### Running frontend in dev mode (hot-reload)

```bash
# Terminal 1 — backend
mymem serve --port 7860 --dev

# Terminal 2 — frontend dev server (proxies /api → :7860)
cd frontend
npm run dev   # starts on http://localhost:5174
```

---

## 5. CLI Commands

```bash
mymem --help                                        # list all commands

mymem ingest raw/articles/my-article.md \
  --type article --tags tag-a,tag-b --domain tech   # ingest a file

mymem ingest "https://youtu.be/dQw4w9WgXcQ" \
  --type youtube --domain tech                      # ingest a YouTube video

mymem ingest "https://example.com/blog-post" \
  --type webpage --domain personal                  # ingest a web page

mymem ingest "https://podcast.com/episode/123" \
  --type podcast --tags ai,ml                       # ingest a podcast episode

mymem query "What is multi-head attention?" --save  # query the wiki

mymem lint                                          # check for broken links / orphans

mymem introspect                                    # daily summary + recommendations

mymem graph backfill                                # seed/repair the entity graph
mymem graph gaps                                    # rank concepts you link to but haven't written
mymem graph rekey                                   # migrate graph anchors slug → stable id

mymem export okf ./okf-bundle                       # export wiki → Open Knowledge Format bundle
mymem import okf ./okf-bundle                       # import an OKF bundle back into the wiki
```

---

## Source Types

| Type | Description | Input |
|------|-------------|-------|
| `article` | Blog post or written article | File path |
| `paper` | Academic / research paper | File path (`.txt`, `.pdf`) |
| `repo` | Code repository or technical project | File path |
| `dataset` | Data file or dataset | File path |
| `image` | Image or visual document | File path |
| `youtube` | YouTube video (auto-fetches transcript) | YouTube URL |
| `podcast` | Podcast episode or show notes | URL |
| `tweet` | Tweet or Twitter/X thread | URL |
| `webpage` | General web page | URL |
| `book` | Book or long-form text | File path (`.txt`, `.pdf`) |
| `newsletter` | Email newsletter | URL or `.txt` file |
| `note` | Personal note or journal entry | File path or text |

### YouTube ingestion requires an extra install

```bash
pip install youtube-transcript-api
# or install all media extras at once:
pip install -e ".[media]"
```

> Note: YouTube transcripts must be publicly available on the video. Auto-generated captions are used if no manual transcript exists.

---

## 6. RAG Search

PDF files are automatically RAG-indexed when uploaded via the web UI or ingested via CLI:

```bash
mymem ingest raw/papers/my-paper.pdf --type paper   # indexes to rag.db, skips LLM wiki extraction
```

Wiki pages are also RAG-indexed automatically after every write. Queries use hybrid retrieval
(keyword search over wiki + vector search over rag.db) and synthesize a final answer.

The embedding model (`nomic-embed-text`) must be pulled in Ollama before first use:

```bash
ollama pull nomic-embed-text
```

---

## 7. Run Tests

```bash
pytest                                              # run all tests
pytest --cov=mymem --cov-report=term-missing        # with coverage report
```

Minimum required coverage: **80%** (100% for `lint.py`).
All LLM calls are mocked — no running Ollama or API key needed for tests.

---

## Project Structure (quick ref)

```
raw/        # source documents (never modified by LLM)
wiki/       # LLM-generated markdown pages
mymem/      # Python package
  cli.py
  config.py
  pipeline/   # ingest, query, reconcile/compounding, router
  knowledge/  # claims ledger + okf/ (OKF export/import)
  graph/      # entity store, resolver, backfill, knowledge gaps
  wiki/
  web/
data/       # SQLite databases + logs (mymem / rag / claims / graph / curiosity / evals)
```

See `CLAUDE.md` for full architecture and `PLAN.md` for build order.
