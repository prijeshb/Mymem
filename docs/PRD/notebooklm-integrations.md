# PRD: External Integrations — NotebookLM / Obsidian / Notion

## Problem Statement

MyMem users already have knowledge scattered across NotebookLM notebooks, Obsidian vaults, and Notion workspaces. Getting that knowledge into MyMem currently requires manual copy-paste. This creates friction that discourages adoption and leaves existing curated knowledge siloed.

## Goals

- G1: Import Notion pages into MyMem with one CLI command
- G2: Allow Obsidian to read/write MyMem's wiki with zero code changes
- G3: Replicate NotebookLM's highest-value generation features (briefing doc, study guide) natively in MyMem
- G4: Provide a documented import path for NotebookLM notebooks via Apify export

## Non-Goals

- Building a real-time sync daemon between any two tools
- Replicating NotebookLM audio overviews (TTS complexity, licensing)
- Building a full Obsidian plugin (Tier 3; only if user demand justifies)
- A paid Notion integration (public API is sufficient)

## User Stories

- As a user, I want to run `mymem ingest "https://notion.so/My-Notes-abc123"` so that my Notion pages become wiki entries without manual copy-paste
- As a user, I want to open my `wiki/` folder in Obsidian so that I can browse and edit wiki pages with Obsidian's graph view and search
- As a user, I want to run `mymem brief` to get a briefing doc or study guide from my wiki, like NotebookLM generates from notebooks

## Acceptance Criteria

- [ ] `mymem ingest <notion-url> --type notion` fetches the page, converts to Markdown, runs through ingest pipeline, creates wiki page(s)
- [ ] Notion databases: each row ingested as a separate source
- [ ] Obsidian vault pointing documented in README with symlink instructions for Windows
- [ ] `mymem brief --type [briefing|study-guide|faq]` generates a structured document from recent/specified wiki pages
- [ ] `NOTION_API_KEY` loaded from `.env`; absent → clear error message with setup instructions
- [ ] Rate limit handling for Notion API (3 req/s, exponential backoff)
- [ ] Optional deps: `pip install -e ".[notion]"` installs `notion-client` + `notion2md`

## Success Metrics

- User can import a 10-page Notion workspace in < 2 minutes
- `mymem brief` output is usable without editing in > 80% of cases
- Zero breaking changes to existing ingest pipeline

## Timeline

- Research: Done (2026-06-01)
- Notion integration development: 1 day
- Brief/study-guide generation: 1 day
- Obsidian documentation: 0.5 day
- Testing: 0.5 day
- **Total estimate: 3 days**

## Dependencies

- `notion-client>=2.2.0` (optional)
- `notion2md>=1.4.0` (optional)
- Existing `mymem/pipeline/ingest.py` `_read_source()` dispatch
- Existing `mymem/pipeline/introspect.py` for brief generation

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Notion API changes breaking `notion2md` | Low | Medium | Pin version; fall back to raw block JSON |
| Temporary Notion image URLs expire | High | Low | Download images at ingest time or warn user |
| NotebookLM Apify actor goes paid/breaks | Medium | Low | Document manual export fallback |
| Obsidian + MyMem both writing same file simultaneously | Low | Medium | Recommend read-only Obsidian use or file watcher |
