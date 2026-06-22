# PRD: Open Knowledge Format (OKF) Integration

**Priority:** P2 (medium)
**Status:** Proposed
**Research:** docs/research/open-knowledge-format.md
**Architecture:** docs/architecture/okf-integration.md
**ADR:** docs/ADR/016-okf-integration.md

## Problem Statement

MyMem stores knowledge as an interlinked markdown-plus-frontmatter wiki — the exact pattern
Google Cloud just standardized as the Open Knowledge Format (OKF). Today that wiki is only
readable by MyMem (and, with zero transform, Obsidian). There is no portable way to (a) hand a
MyMem wiki to any OKF-aware agent/tool, or (b) ingest knowledge bundles others publish in OKF.
Adopting OKF as a two-way interchange format unlocks both with low effort because the substrate
already matches.

## Goals

- **G1:** `mymem export okf <dir>` produces a **spec-conformant** OKF v0.1 bundle from the live
  wiki (every non-reserved file has parseable frontmatter with non-empty `type`).
- **G2:** Wikilinks (`[[Title]]`) are converted to OKF markdown links (`/slug.md`) with correct
  resolution; broken links are emitted OKF-tolerantly and logged.
- **G3:** OKF bundles can be **ingested** as a source type — each concept file enters the normal
  ingest pipeline; OKF links become wikilinks.
- **G4:** A MyMem-origin bundle **round-trips losslessly** (export → import preserves ULID `id`,
  `domain`, `sources` via extension keys).
- **G5:** No new runtime dependencies; export/import covered by tests at ≥ 80% (export module
  targets 100%, it is pure transform).

## Non-Goals

- Making OKF the **native** wiki storage format (explicitly rejected — see ADR-016).
- Live two-way sync / a running daemon. Export and import are explicit, on-demand commands.
- Implementing Google's enrichment agent or hosting the HTML visualizer.
- Supporting OKF spec versions beyond v0.1.

## User Stories

- As a **MyMem user**, I want to export my wiki to an OKF bundle so I can open it in Google's
  visualizer or feed it to an OKF-aware agent.
- As a **MyMem user**, I want to ingest a published OKF bundle so external curated knowledge
  enters my wiki and graph.
- As a **developer**, I want export to be a pure, well-tested transform so conformance is
  guaranteed and round-trips are lossless.

## Acceptance Criteria

- [ ] **AC1:** `mymem export okf <dir>` writes one `.md` per wiki page with frontmatter
      containing a non-empty `type`, plus `title`, `description`, `resource`, `tags`, `timestamp`.
- [ ] **AC2:** `domain` maps to `type`; `updated` maps to ISO-8601 `timestamp`;
      `IndexEntry.summary`/first paragraph maps to `description`.
- [ ] **AC3:** `[[Title]]` links are rewritten to `[Title](/slug.md)` using the title→slug→id
      index; unresolved targets emit a `/_/unresolved` (or logged broken) link and are reported.
- [ ] **AC4:** Bundle includes a frontmatter-free `index.md` and an OKF-format `log.md`.
- [ ] **AC5:** ULID `id`, `domain`, `sources`, `archived`, `created` are preserved as extension
      frontmatter keys.
- [ ] **AC6:** `mymem ingest <bundle-dir> --type okf` (or an OKF `SourceReader`) ingests each
      concept; OKF markdown links become `[[wikilinks]]`; unknown `type` maps to a `domain`
      (default `misc`) with the raw `type` kept as a tag.
- [ ] **AC7:** Export → import of a MyMem-origin bundle preserves page identity (same ULID `id`).
- [ ] **AC8:** Exported bundle passes a conformance check (script asserts G1's rule on every file).
- [ ] **AC9:** Tests ≥ 80% overall; export transform module = 100%; no new runtime deps.

## Success Metrics

- Conformance: 100% of exported files pass the OKF v0.1 conformance rule.
- Round-trip fidelity: 0 identity changes (ULID stable) across export→import on the live wiki.
- Link resolution: % of wikilinks resolved on export (target ≥ 95% on the live 144-page wiki).
- Adoption smoke: exported bundle renders in Google's static OKF visualizer (manual check).

## Timeline

- Research: done
- Development: ~2–3 focused sessions (export slice → import slice → round-trip tests)
- Testing: bundled per slice (TDD)

## Dependencies

- `mymem/wiki/page.py`, `wiki/index.py`, `wiki/log.py` — read live wiki + frontmatter.
- `mymem/wiki/identity.py` — title→slug→id resolution for link rewriting.
- `mymem/pipeline/readers.py` — `SourceReader` chain for the import side.
- `mymem/pipeline/ingest.py` — concept files flow through normal ingest.
- No external service; no new package.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Wikilink targets unresolvable on export | Med | Med | Resolve via identity index; emit OKF-tolerant broken link + report count |
| OKF spec evolves past v0.1 | Med | Low | Pin v0.1; additive export; unknown-key tolerance keeps forward-compat cheap |
| Large OKF bundles inflate import LLM cost | Med | Med | Import = normal ingest → map-reduce + free-tier routing already apply |
| Round-trip drift | Low | Med | Preserve `id`/`domain`/`sources` as extension keys |
| External `type` taxonomy mismatch | Med | Low | Map unknown `type`→`domain` (default `misc`), keep raw `type` as tag |

## Out-of-scope follow-ups

- OKF v0.2 tracking when published.
- Web UI button for export/import (CLI-first for v1).
- Bundle diffing / incremental export.
