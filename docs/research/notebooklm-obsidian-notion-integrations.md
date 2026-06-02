# Research: NotebookLM / Obsidian / Notion Integrations

**Date**: 2026-06-01
**Goal**: Import data from external tools into MyMem + understand what features to replicate natively.

---

## 1. NotebookLM

### API Status

Google has **not released a public NotebookLM API** as of June 2026.
Three practical paths exist for extracting NotebookLM data:

| Path | Effort | Risk | Notes |
|------|--------|------|-------|
| **Apify Actor** (`clearpath/notebooklm-api`) | Low | Low | Exports notebooks as JSON/Markdown with citations. Free during beta. Uses App Password auth. |
| **`notebooklm-py` library** | Medium | Medium | Wraps undocumented RPC APIs via Playwright cookies. Can bulk-export sources + artifacts. Breakage risk if Google changes internals. |
| **Chrome Extension (NotebookLM Tools)** | None (manual) | None | Per-notebook ZIP export. No chat history, no media. One-time manual use only. |

### Recommended Import Path

1. User exports notebook via **Apify actor** → receives JSON with sources, conversation history, briefing docs, citations
2. New MyMem ingest endpoint accepts this JSON → parses sources → routes each through existing ingest pipeline
3. AI-generated briefing docs imported as initial wiki page content (saves re-synthesis cost)

### Features Worth Replicating

| Feature | In MyMem Already? | Effort to Add | Verdict |
|---------|------------------|--------------|---------|
| Source-grounded Q&A with citations | Partial (RAG returns pages, not inline citations) | Medium | **Add inline citations to query output** |
| Study guide / FAQ generation | No | Low | **Add `mymem brief --type study-guide`** |
| Briefing doc generation | No | Low | **Add `mymem brief --type briefing`** |
| Audio overviews (podcast) | No | Very High (TTS licensing) | Skip |
| Mind map / flashcards | No | Medium | Defer |

The two highest-value features to replicate: **briefing docs** and **study guides** — both are just structured LLM prompts over existing wiki pages. They slot into `mymem/pipeline/introspect.py` naturally.

---

## 2. Obsidian

### Compatibility Assessment

MyMem and Obsidian use **identical storage formats**:

| Feature | MyMem | Obsidian | Compatible? |
|---------|-------|----------|-------------|
| YAML frontmatter | ✓ | ✓ | Yes |
| `[[wikilinks]]` | ✓ | ✓ | Yes |
| Custom fields (`domain:`, `sources:`) | ✓ | ✓ (ignored silently) | Yes |
| Tags as YAML list | ✓ | ✓ | Yes |
| ISO 8601 dates | ✓ | ✓ | Yes |

**No format translation is needed.** Both tools read/write the same Markdown + YAML dialect.

### Integration Approaches (Ranked)

#### Tier 1 — Zero Code

**Option A: Point Obsidian vault at `wiki/`**
- Open Obsidian → "Open folder as vault" → select `wiki/`
- Obsidian reads all `.md` files immediately, renders `[[wikilinks]]`, shows properties panel
- Both tools read/write the same directory. Live coexistence.

**Option B: Symlink**
```powershell
New-Item -ItemType SymbolicLink -Path "C:\Users\prije\Obsidian\mymem" -Target "C:\Users\prije\Desktop\AI apps\MyMem\wiki"
```
- Same as A, but Obsidian sees `mymem` as a named vault subfolder
- Cleaner if user already has a multi-vault Obsidian setup

**Recommended: Start here. No engineering investment required.**

#### Tier 2 — ~50 Lines Python

**Option C: File watcher sync** (for unidirectional MyMem → Obsidian)
- `watchdog` library monitors `wiki/` for changes
- On write: copy `.md` to separate Obsidian vault directory
- Log sync event in `log.md`
- Use when: MyMem is source-of-truth, Obsidian is read-only mirror

#### Tier 3 — 8–16 hrs TypeScript

**Option D: Obsidian plugin**
- Custom plugin calling MyMem's `/api/*` endpoints
- Adds "Sync with MyMem" command palette entry, Properties panel for MyMem metadata
- Only worth it if user wants deep UI integration inside Obsidian

**Recommendation: Tier 1 (vault pointing) is sufficient. Document it in `mymem serve --help` and README.**

---

## 3. Notion

### API Availability

Notion has a **full public REST API** (`api.notion.com/v1`):
- Fetch pages: `GET /v1/pages/{pageId}`
- Fetch blocks (recursive): `GET /v1/blocks/{blockId}/children`
- Query databases: `POST /v1/databases/{databaseId}/query`
- Rate limit: **3 req/s** per integration
- Auth: `NOTION_API_KEY` (integration token) + share page with integration

### Conversion Library

**`notion2md`** (PyPI, actively maintained):
- Converts Notion block JSON → Markdown
- Handles: rich text, headings, lists, code blocks, tables, callouts, toggles
- ~5 lines to invoke

### Integration Architecture

```
Notion URL/ID
    ↓
_read_notion() [new handler in ingest.py]
    ├── Extract page ID from URL
    ├── GET /v1/pages/{id}  →  metadata (title, created_time, properties)
    ├── GET /v1/blocks/{id}/children  →  block JSON (recursive)
    └── notion2md.BlockConverter()  →  Markdown string
    ↓
Existing `note` ingest path (security scan → LLM extraction → wiki pages)
```

**Where the code lives**: New `_read_notion()` function in `mymem/pipeline/ingest.py` (~60 lines). Dispatched from `_read_source()` when `source_type == "notion"` or when URL matches `notion.so`.

**Optional deps** (add to `pyproject.toml`):
```toml
[project.optional-dependencies]
notion = [
    "notion-client>=2.2.0",
    "notion2md>=1.4.0",
]
```

**CLI usage:**
```bash
mymem ingest "https://notion.so/My-Page-abc123" --type notion --domain tech
mymem ingest "abc123def456" --type notion  # direct page ID
mymem ingest "https://notion.so/My-DB-abc123" --type notion  # database → batch ingest
```

**Key implementation notes:**
- Images in Notion use **temporary signed URLs** (expire in 1 hour) → download immediately or store raw URL with expiry warning
- Database ingest: each row = one `_read_notion()` call, then individually ingested
- Error handling: 429 → exponential backoff; 403 → print "Share this page with your Notion integration"

### Effort

~60 lines Python + 2–3 hours including tests. Smallest engineering investment of the three integrations.

---

## Priority Ranking

| Integration | Complexity | Value | Recommended Phase |
|-------------|-----------|-------|-------------------|
| Obsidian (vault pointing) | Zero | High | **Now — document only** |
| Notion import | Low (60 LOC) | High | **V1-0005 sprint** |
| NotebookLM briefing/study-guide replication | Medium | High | **V1-0005 sprint** |
| NotebookLM import (Apify) | Medium | Medium | V1-0006 |
| Obsidian file watcher | Low | Medium | V1-0006 if needed |
| Obsidian plugin | High | Low | Future / community |
| NotebookLM audio overviews | Very High | Low | Skip |
