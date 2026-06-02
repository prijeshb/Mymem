# ADR 004: External Integrations Strategy — NotebookLM, Obsidian, Notion

## Status: Proposed

## Context

Users have existing knowledge in NotebookLM, Obsidian, and Notion. MyMem needs a coherent strategy for ingesting that knowledge without becoming a sync daemon or reimplementing those tools' UX.

Three integration types were evaluated:
1. **NotebookLM** — no public API; Google-controlled
2. **Obsidian** — local Markdown vaults; same format as MyMem
3. **Notion** — full public REST API; block-based content model

## Decision

### NotebookLM

**Decision: Replicate features, not import.**

NotebookLM has no public API. Rather than depending on unofficial scrapers or third-party actors (fragile), focus on replicating its two highest-value generation features natively:

1. **Briefing doc generation** — `mymem brief --type briefing`
2. **Study guide / FAQ generation** — `mymem brief --type study-guide`

Both are structured LLM prompts over existing wiki pages. They extend `mymem/pipeline/introspect.py` with minimal new code.

For users who want to import existing NotebookLM notebooks: document the **Apify actor export path** (`clearpath/notebooklm-api`) which produces Markdown/JSON — feedable into `mymem ingest` as a `note` source. This is a documented workaround, not a maintained integration.

### Obsidian

**Decision: Zero-code integration via vault pointing. Document only.**

MyMem's `wiki/` directory and Obsidian vaults use identical formats (Markdown + YAML frontmatter + `[[wikilinks]]`). No adapter or sync layer is needed. Users point Obsidian at `wiki/` directly.

Document this in the README. Add symlink instructions for Windows. Do not build a file watcher or plugin in the initial sprint — only if explicit user demand surfaces.

### Notion

**Decision: Build a `notion` source type in the ingest pipeline.**

Notion has a stable public API with official Python SDK. The integration is ~60 LOC, entirely within `mymem/pipeline/ingest.py`, reuses all existing pipeline stages, and adds no architectural complexity.

Implementation:
- New `_read_notion()` handler dispatched from `_read_source()`
- `notion-client` + `notion2md` as optional dependencies under `pip install -e ".[notion]"`
- `NOTION_API_KEY` from `.env`
- Supports: single pages, recursive block trees, databases (batch ingest)

## Rationale

**Why replicate NotebookLM features instead of importing?**

The Apify and `notebooklm-py` paths depend on undocumented APIs. When Google locks them down (as they have with similar scrapers), the integration breaks silently. The user-facing value — briefing docs and study guides — can be generated from MyMem's own wiki with the same quality. Replicate the output, not the source.

**Why zero-code for Obsidian?**

The storage format is already identical. Any code written to "sync" the two would be solving a problem that doesn't exist. The only risk (concurrent writes) is mitigated by recommending Obsidian as a read-mostly interface while MyMem owns writes.

**Why build the Notion integration?**

It's the only external tool with a stable, versioned public API. The `notion2md` library handles the complex block-to-Markdown conversion. The result feeds directly into the existing pipeline with no new prompts, models, or storage required. High value, low risk, low effort.

## Alternatives Considered

### Build a full real-time sync daemon

**Rejected.** A sync daemon requires conflict resolution, locking, and persistent state. The value doesn't justify the complexity for a personal knowledge tool.

### Build an Obsidian plugin

**Deferred.** ~16 hours of TypeScript for a feature that vault-pointing solves for free. Revisit if users explicitly request deep Obsidian UI integration.

### Integrate with NotebookLM via unofficial `notebooklm-py`

**Rejected for now.** Depends on cookie-based Playwright scraping of undocumented Google RPCs. One internal Google refactor breaks it. Document as a power-user option, don't make it a first-class integration.

### Integrate with Google Drive API for NotebookLM export

**Deferred.** NotebookLM doesn't consistently persist sources back to Drive in an accessible format. More friction than the Apify path. Revisit if Google publishes a NotebookLM → Drive export spec.

## Consequences

**Positive:**
- Notion integration adds a high-value import path with minimal code and zero architecture changes
- Obsidian integration is effectively free — no maintenance burden
- Replicating NotebookLM features keeps MyMem self-contained and removes dependency on Google's product decisions

**Negative:**
- NotebookLM notebook import remains a manual/workaround step (Apify export → `mymem ingest`)
- If Notion changes their API significantly, `notion-client` + `notion2md` must be updated

**Risks:**
- Notion image URLs expire in ~1 hour; images referenced in ingested pages may break. Mitigation: download at ingest time or store raw URL with a documented caveat.
