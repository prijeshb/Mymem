# MyMem — Product Overview Spec

**Status:** Living (updated as product evolves)
**Last Updated:** 2026-04-22

---

## 1. What Is MyMem?

MyMem is a **personal LLM-powered knowledge wiki**. Instead of re-deriving answers from raw sources on every query (RAG), the LLM incrementally **builds and maintains a persistent wiki** of interlinked markdown pages. Sources are compiled once; queries read the pre-built wiki.

> Think of it as your second brain — you feed it articles, papers, videos, and notes; it organises everything into an interconnected wiki that grows smarter over time.

---

## 2. Core Concept

```
Raw sources (URLs, files, text, YouTube)
        │
        ▼
   [Ingest Pipeline]
   LLM reads source → extracts key concepts
   → writes/updates wiki pages (markdown)
        │
        ▼
   wiki/ directory
   Interlinked .md pages with YAML frontmatter
        │
        ▼
   [Query Pipeline]
   User asks question → search index
   → LLM synthesises answer from wiki pages
        │
        ▼
   Answer with citations
```

---

## 3. Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI (Python 3.11+) |
| LLM providers | Ollama (local, default), Anthropic, OpenAI |
| Storage | Markdown files (wiki), SQLite (traces, curiosity) |
| Frontend | React 18 + TypeScript + Vite |
| Styling | Tailwind CSS v3 |
| Graph | D3.js v7 (force-directed) |
| Testing | pytest (backend), Vitest (frontend) |

---

## 4. Pages & Screens

### 4.1 Dashboard (`/`)

The home screen. Overview of the knowledge base.

```
┌──────────────────────────────────────────────────────┐
│  MyMem                              [dark/light 🌙]  │
├─────────────────┬────────────────────────────────────┤
│                 │                                    │
│  Overview       │  Domain Breakdown                  │
│  ─────────────  │  ████ tech (12)                   │
│  Pages:   31    │  ███  spiritual (8)                │
│  Sources: 18    │  ██   finance (5)                  │
│  Orphans:  2    │  ─────────────────                 │
│  Cost: $0.02    │                                    │
│                 │  Activity Heatmap (16 weeks)        │
│  Domains        │  ░░▒░░▓░░░▒▓▓░░░░░░░░▒░░          │
│  [tech] [ml]    │                                    │
│                 │  Recent Log                        │
│                 │  • ingest: article.md              │
│                 │  • query: What is attention?       │
│                 │                                    │
│                 │  Quick Ask                         │
│                 │  [_________________________] [Ask] │
└─────────────────┴────────────────────────────────────┘
```

**Components:**
- Stats panel (pages, sources, orphans, session cost)
- Domain breakdown tiles
- 16-week activity heatmap
- Recent log entries
- Quick Ask (inline SSE streaming Q&A)
- Wiki Pages table (paginated, filterable, sortable by most recent)

---

### 4.2 Wiki Page (`/wiki/:slug`)

Individual wiki page viewer.

```
┌────────────────────────────────────────────────────────────┐
│  MyMem                                                     │
├──────────────┬───────────────────────────────┬────────────┤
│              │                               │            │
│  Contents    │  [Page Title]      [domain]   │            │
│  ──────────  │  #tag1 #tag2  [+ edit]        │            │
│  > Overview  │  Created … · Updated …        │            │
│  > Mechanism │  ─────────────────────────    │            │
│  > Pros/Cons │                               │            │
│              │  Article body (markdown)       │            │
│  Backlinks   │  with [[wikilinks]] rendered  │            │
│  ──────────  │  as indigo links (internal)   │            │
│  ← Page A    │  or amber links (broken)      │            │
│  ← Page B    │                               │            │
│              │  ─────────────────────────    │            │
│  Related     │  Related Web Articles         │            │
│  Concepts    │  ┌──────┐ ┌──────┐ ┌──────┐  │            │
│  ──────────  │  │ art1 │ │ art2 │ │ art3 │  │            │
│  Concept A   │  └──────┘ └──────┘ └──────┘  │            │
│  Concept B   │  (loaded async via SSE)        │            │
│  ~Concept C  │                               │            │
│  (amber=new) │                               │            │
└──────────────┴───────────────────────────────┴────────────┘
```

**Left sidebar:** TOC (scroll-spy) · Backlinks · Related Concepts
**Main area:** Page header (title, domain badge, tags, dates) · Article body · Related Web Articles grid
**Inline edit:** Tags + domain editable in place (PATCH `/api/page/:slug`)
**Broken wikilinks:** Amber colour, link to internal slug (may 404 until ingested)
**Related Web Articles:** SSE-streamed card grid, always fires (page title as seed)

---

### 4.3 Search (`/search`)

Streaming Q&A interface.

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│  [Domain ▼]  [Question___________________] [Ask →]  │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ Answer streams here word by word...          │   │
│  │                                              │   │
│  │ Citations: [[Page A]] [[Page B]]             │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  Wiki Pages  [Filter___] [domain ▼]  Page 1/3 ◀▶   │
│  ┌──────────────────────────────────────────────┐   │
│  │ Title               Domain   Tags            │   │
│  │ ─────────────────────────────────────────── │   │
│  │ AI as a Judge       tech     [ai] [eval]     │   │
│  │ Cross Entropy       tech     [ml] [loss]     │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

**Features:** Domain filter · SSE streaming answer · Citations · Save answer as wiki page · Paginated pages table (most recent first)

---

### 4.4 Graph (`/graph`)

Force-directed wikilink network.

```
┌──────────────────────────────────────────┐
│                                          │
│     ●─────●                              │
│    /│      \                             │
│   ● │       ●────●                      │
│    \│      /                             │
│     ●─────●         ● = wiki page        │
│                     ─ = [[wikilink]]     │
│  [domain filter pills]                   │
│  [tech] [ml] [spiritual]                 │
└──────────────────────────────────────────┘
```

Nodes coloured by domain. Click node → navigate to wiki page.

---

### 4.5 Ingest (`/ingest`)

Add new knowledge sources.

```
┌─────────────────────────────────────────────┐
│  Add Source                                 │
│                                             │
│  ● URL    ○ File upload    ○ Paste text     │
│                                             │
│  [https://___________________________]      │
│                                             │
│  Type: [webpage ▼]  Domain: [tech ▼]       │
│  Tags: [ml, attention]                      │
│                                             │
│  [Ingest →]                                 │
│                                             │
│  ─────────────────────────────────────────  │
│  ✓ Wrote: attention-mechanism.md            │
│  ✓ Updated: transformer-architecture.md    │
└─────────────────────────────────────────────┘
```

**Tabs:** URL · File upload (drag & drop) · Paste text
**Source types:** article, paper, repo, dataset, youtube, webpage, podcast, tweet, book, newsletter, note

---

### 4.6 Introspect (`/introspect`)

Daily summary + curiosity recommendations.

```
┌──────────────────────────────────────────────┐
│  Today's Summary  [date picker]  [Refresh]   │
│                                              │
│  You ingested 3 sources today covering       │
│  transformer architecture and evaluation...  │
│                                              │
│  ─────────────────────────────────────────   │
│  Recommendations                             │
│  ┌────────────────────────────────────────┐  │
│  │ 📚 Revisit: Cross Entropy (14d ago)   │  │
│  │ 💡 Gap: You follow LLM eval but have  │  │
│  │    no pages on benchmarking methods   │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Curiosity Profile                           │
│  tech     ████████░░ rising ↑               │
│  spiritual ████░░░░░ stable →               │
└──────────────────────────────────────────────┘
```

---

## 5. Backend API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/query` | SSE streaming Q&A answer |
| GET | `/api/pages` | List pages (paginated, filtered, sorted by recency) |
| GET | `/api/stats` | Dashboard stats |
| GET | `/api/graph` | Force-directed graph nodes + edges |
| POST | `/api/ingest` | Ingest from URL or file path |
| POST | `/api/upload` | Multipart file upload → ingest |
| POST | `/api/ingest-text` | Paste raw text → ingest |
| GET | `/api/page/:slug` | Single page (body, TOC, backlinks, related concepts) |
| PATCH | `/api/page/:slug` | Update domain + tags |
| GET | `/api/related-web` | SSE stream of web article results per concept |
| GET | `/api/log` | Recent log entries |
| GET | `/api/lint` | Lint issues (orphans, broken links, stubs) |
| GET | `/api/introspect` | Daily summary + recommendations |
| GET | `/api/curiosity` | Top domains/tags with trend direction |
| GET | `/api/heatmap` | 16-week activity heatmap data |
| GET | `/api/daily` | Saved daily summary pages |

---

## 6. Wiki Page Format

```markdown
---
title: Concept Title
domain: tech
tags: [ml, attention]
sources: [source-article.md]
created: 2026-04-01
updated: 2026-04-22
---

# Concept Title

Body content with [[Related Concept]] wikilinks.

## See Also
- [[Another Concept]]
```

---

## 7. Domain Taxonomy

| Domain | Examples |
|--------|---------|
| `tech` | ml, python, systems, databases |
| `spiritual` | meditation, stoicism, mindfulness |
| `finance` | investing, crypto, budgeting |
| `health` | fitness, nutrition, sleep |
| `reminder` | todo, follow-up, deadlines |
| `research` | papers, hypotheses, experiments |
| `personal` | journal, goals, reflection |
| `creative` | writing, design, music |
| `business` | strategy, product, marketing |
| `misc` | catch-all |

---

## 8. Security Requirements

- [ ] All slug/path inputs validated against `wiki_dir` boundary (path traversal prevention)
- [ ] User queries sanitized before LLM (`sanitize_query`)
- [ ] Ingested content sanitized before LLM (`sanitize_for_prompt`)
- [ ] File uploads: size limit, MIME type check, filename sanitization
- [ ] Source types validated against whitelist
- [ ] No shell/subprocess calls with user input

---

## 9. Planned Features (Backlog)

| Feature | Spec | Priority |
|---------|------|----------|
| React Query caching (stale-while-revalidate) | `feat_react_query_cache.md` | P1 |
| SQLite-backed page index (replace index.md) | `feat_sqlite_index.md` | P2 |
| Ontology graph (typed relationships) | `feat_ontology_graph.md` | P2 |
| Full input validation (wire security/validate.py) | `feat_input_validation.md` | P1 |
| Phase 2 web search (TF-IDF + sklearn cosine) | `feat_web_search_phase2.md` | P2 |
| Extraction quality eval + A/B comparison | `docs/PRD/extraction-eval.md` | P1 |
