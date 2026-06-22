# ADR 016: Open Knowledge Format (OKF) Integration

## Status: Accepted (implemented in V1-0013)

**Date:** 2026-06-18 (accepted 2026-06-22)
**Priority:** P2
**Relates to:** ADR-003 (wiki storage = markdown), ADR-004 (external integrations),
ADR-013/014 (stable page identity). **Research:** docs/research/open-knowledge-format.md ·
**PRD:** docs/PRD/okf-integration.md · **Architecture:** docs/architecture/okf-integration.md

## Context

Google Cloud released the **Open Knowledge Format (OKF) v0.1** (2026-06-12, Apache 2.0) — an
open spec that standardizes the "LLM wiki" pattern as a portable bundle of markdown files with
YAML frontmatter, one file per concept, markdown links forming a graph. MyMem already stores
knowledge this way (ADR-003), so the format aligns almost exactly. The question is whether and
how to adopt OKF: as an interchange format, or as the native storage model.

OKF requires only a `type` frontmatter field; recommends `title`/`description`/`resource`/
`tags`/`timestamp`; reserves `index.md` and `log.md`; uses standard markdown links; and requires
consumers to preserve unknown keys and tolerate broken links.

## Decision

Adopt OKF as a **two-way interchange format**, not as native storage:

1. Build an **export adapter** (`mymem/knowledge/okf/`, `mymem export okf <dir>`) that emits a
   spec-conformant OKF v0.1 bundle from the live wiki (pure transform).
2. Build a **direct importer** (`mymem import okf <dir>`) that maps each OKF concept file back to
   a `WikiPage` — the exact inverse of export.
3. **Keep MyMem's native storage unchanged.** ULID identity (ADR-013), bi-temporal claims
   (ADR-015), and the entity graph (ADR-007/008) stay authoritative; OKF is a projection.
4. **Preserve MyMem-specific fields as OKF extension keys** (`id`, `domain`, `sources`,
   `archived`, `created`) so MyMem-origin bundles round-trip losslessly.

### Implementation decision (V1-0013): direct import, not the LLM ingest pipeline

The original sketch (and the architecture doc) routed import through the `SourceReader` chain into
the LLM ingest pipeline. **Rejected during build** because it conflicts with the PRD's lossless
round-trip ship gate (G4): LLM ingest re-derives content and mints new pages, so it cannot
preserve the ULID `id` or exact body/links. The direct importer (`okf/importer.py`:
`from_okf_frontmatter` + `markdown_links_to_wikilinks` + `write_page(stamp_updated=False)`) is the
true inverse of export — lossless, identity-stable, and zero-LLM-cost. Existing pages are skipped
unless `--overwrite`. An unknown `type` maps to domain `misc` and is kept as a tag.

| Import approach | Pros | Cons | Verdict |
|---|---|---|---|
| **Direct map (chosen)** | Lossless round-trip (id/body/links preserved); no LLM cost; simple inverse of export | Doesn't re-extract/compound external bundles into claims | ✅ |
| Via `SourceReader` → LLM ingest | Reuses extraction/compounding; external bundles get the full treatment | Breaks G4 round-trip; new ids; LLM cost per concept; lossy | ❌ |

A future option: add an *optional* `--ingest` flag that additionally runs imported concepts
through the pipeline for claim extraction, on top of the lossless direct write.

## Rationale

- The substrate already matches (~85%); the only required transform is `domain`→`type` plus
  link-syntax conversion. Highest interop payoff per unit of effort.
- Export-as-projection mirrors the existing Obsidian integration (ADR-004) and adds no risk to
  the storage layer.
- Import via the existing `SourceReader` Strategy chain is Open/Closed — no edits to existing
  readers, and OKF concepts automatically benefit from extraction, compounding, and graphing.
- Extension-key preservation makes round-trips identity-stable, protecting ADR-013 guarantees.

## Alternatives Considered

1. **OKF as native storage (refactor the wiki to be OKF-native)** — rejected. OKF v0.1 is
   deliberately minimal (only `type` required); MyMem's identity/claims/graph layers exceed it.
   Refactoring storage down to OKF would lose capability and churn stable code for no gain.
2. **Export only (no import)** — rejected as the end state (chosen as the *first slice*).
   One-way interop leaves out ingesting externally published OKF bundles, which is half the
   value and cheap via the existing reader chain.
3. **Do nothing / keep Obsidian-only interop** — rejected. Obsidian uses `[[wikilinks]]`, not
   OKF markdown links; it does not make MyMem readable by OKF-aware agents or the Knowledge
   Catalog, and forgoes an emerging open standard that fits MyMem's thesis.
4. **Map `domain` → a `tag` and invent a fixed `type`** — rejected. `domain` is the natural
   concept-category and the closest semantic match to OKF `type`; collapsing it loses meaning.

## Consequences

- **Positive:** MyMem wikis become consumable by any OKF tool (Google's visualizer, Knowledge
  Catalog, future agents); OKF bundles become an ingestable source; writing the exporter
  improves MyMem's own frontmatter (`description`, `type`); zero new runtime dependencies.
- **Negative / tradeoffs accepted:** an extra projection surface to keep conformant as the wiki
  evolves; OKF v0.1 is young and may change; round-trips through *non-MyMem* OKF bundles can be
  lossy (no `id`/`domain` to restore) — acceptable, handled by mapping defaults.
- **Risks:** wikilink→path resolution misses (mitigated via the identity index + tolerant broken
  links); untrusted import input (mitigated via the existing security scanner + path-traversal
  guard); spec churn (mitigated by pinning v0.1 + additive export).

## Revisit when

- OKF publishes v0.2 → assess new fields/conformance and bump the exporter.
- Google's Knowledge Catalog or another consumer requires fields beyond v0.1.
- A web-UI export/import surface is requested (currently deferred; CLI-first).
- Round-trip lossiness on external bundles becomes a real workflow (consider a richer mapping).
