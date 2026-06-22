# Research: Open Knowledge Format (OKF) — adopt as MyMem interop format

**Date:** 2026-06-18
**Branch context:** V1-0011
**Status:** Research complete → PRD/architecture/ADR-016 written

## TL;DR

Google Cloud's **Open Knowledge Format (OKF)** (released 2026-06-12, Apache 2.0) is a
*file-format spec*, not a runtime. It formalizes the "LLM wiki" pattern that **MyMem already
implements**: a directory of Markdown files with YAML frontmatter, one file per concept, with
inter-file markdown links forming a knowledge graph.

MyMem's wiki is **~85% OKF-shaped already**. The work is an **export adapter + import reader**,
not a storage refactor. Recommended scope: **export + import** (two-way interop). Do **not**
make OKF the native storage format — MyMem's ULID identity (ADR-013), bi-temporal claims
(ADR-015), and entity graph (ADR-007/008) exceed OKF v0.1.

## What OKF is

> "An open, vendor-neutral specification for representing the metadata, context, and curated
> knowledge that modern AI systems need." — Google Cloud

It standardizes the emergent LLM-wiki pattern into a portable bundle that any agent or human
tool can read. The value is interoperability: the same files are read by a human in an editor
and by an agent as context, with no translation layer.

### Spec at a glance (v0.1)

| Aspect | OKF rule |
|--------|----------|
| **Structure** | Directory of `.md` files; each file = one concept; hierarchical subdirs allowed |
| **Concept ID** | The file path within the bundle, `.md` removed (`tables/orders.md` → `tables/orders`) |
| **Frontmatter** | Only `type` is **required**. Recommended: `title`, `description`, `resource`, `tags`, `timestamp` (ISO 8601). Producers may add keys; consumers **must preserve unknown keys** |
| **Body** | Free-form UTF-8 markdown; no structural restrictions |
| **Links** | Standard markdown links; absolute-from-root (`/tables/customers.md`) recommended, relative allowed. A link asserts a *relationship*. Consumers must tolerate broken links |
| **Reserved files** | `index.md` (directory listing, **no frontmatter**), `log.md` (date-grouped change history, newest first, `**Action**: description` entries) |
| **Encoding** | UTF-8 markdown only |
| **Conformance** | Every non-reserved `.md` has parseable frontmatter with a non-empty `type`; reserved files follow their structures when present |

### Reference implementation

Repo: `GoogleCloudPlatform/knowledge-catalog/okf` (Apache 2.0). Ships:
- An **enrichment agent** (walks BigQuery, drafts OKF docs)
- A **static HTML graph visualizer** (no backend)
- **Sample bundles** (GA4 e-commerce, Stack Overflow, Bitcoin)
- Google Cloud's **Knowledge Catalog** ingests OKF and serves it to agents

## Why this matters for MyMem

MyMem's whole thesis (CLAUDE.md) is "an LLM incrementally builds and maintains a persistent
wiki of interlinked markdown files" — that is the exact pattern OKF formalizes. Adopting OKF:

1. **Interop out** — any OKF consumer (Google's visualizer, Knowledge Catalog, future agent
   tools) can read a MyMem wiki without a custom integration.
2. **Interop in** — OKF bundles published by others become an ingestable source type.
3. **Frontmatter hygiene** — writing the exporter forces MyMem to surface a per-page
   `description` and a `type`, improving its own metadata quality.
4. **Strategic positioning** — aligns MyMem with an emerging open standard rather than a
   bespoke format; cheap to do because the substrate already matches.

## Fit-gap analysis (MyMem ↔ OKF)

MyMem frontmatter today (`mymem/wiki/types.py:WikiPage`): `title`, `tags`, `sources`, `domain`,
`created`, `updated`, `archived`, `id` (ULID). Links use `[[wikilinks]]`. `index.md` is a
catalog; `log.md` uses `## [YYYY-MM-DD HH:MM] op | desc` headers.

| MyMem today | OKF wants | Gap & mapping |
|-------------|-----------|---------------|
| MD + YAML frontmatter | MD + YAML frontmatter | ✅ none |
| `domain` (10-value taxonomy) | `type` (**required**) | map `domain` → `type` (e.g. `type: "tech"`); keep `domain` as preserved extension key |
| `[[Title]]` wikilinks | `[text](/slug.md)` markdown links | **convert on export** (resolve title→slug→path); **convert on import** (markdown link → `[[title]]`) |
| `id` (ULID), `archived`, `created`, `updated`, `sources` | unknown keys | preserve as extension keys (OKF tolerates them) |
| `sources: [raw/x.md]` | `resource` (URI) | map first/primary source → `resource`; keep full `sources` as extension |
| `created`/`updated` (date) | `timestamp` (ISO 8601 datetime) | emit `updated` as `timestamp` in ISO 8601 |
| index summaries (`IndexEntry.summary`) | per-page `description` | populate `description` from the index summary / first paragraph |
| `index.md` catalog (has content) | `index.md` (no frontmatter) | already frontmatter-free; minor body-format alignment |
| `log.md` (`## [date time] op`) | `log.md` (date headings + `**Action**:`) | reformat on export (or ship a separate OKF log) |
| `wiki/qa/`, `wiki/daily/` subdirs | hierarchical subdirs | already compatible; paths become concept IDs |
| `## Knowledge Claims` section (ADR-015) | free-form body | passes through unchanged; bonus provenance for OKF consumers |

**Net:** one required transform (`domain`→`type`), two link transforms (wikilink ↔ markdown
link), and a handful of field renames. No schema migration, no storage change.

## Prior art surveyed

- **OKF reference repo** — the canonical spec + visualizer + sample bundles. Our export target.
- **Graphiti (Zep)** — temporally-aware KG for agent memory. Already referenced in ADR-007/008
  for entity resolution. Different layer (runtime graph DB vs file format); complementary, not
  a substitute.
- **Obsidian** — MyMem already links `wiki/` as an Obsidian vault (`mymem obsidian setup`).
  Obsidian uses `[[wikilinks]]`; OKF uses markdown links. The OKF exporter is the
  "transform" cousin of the existing zero-transform Obsidian integration.
- **Frontmatter parsing** — MyMem already reads/writes YAML frontmatter in `mymem/wiki/page.py`;
  no new dependency needed for export. Import reuses the existing frontmatter parser + the
  `SourceReader` Strategy chain in `mymem/pipeline/readers.py`.

## Dependency audit

**No new runtime dependencies required.**
- Export: pure Python over existing `wiki/page.py` + `wiki/index.py` + `pyyaml` (already present).
- Import: reuse `readers.py` (`SourceReader` ABC) + existing YAML parsing.
- Optional (not for v1): validate exported bundles against Google's visualizer manually.

## Risks & gotchas

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Wikilink → path resolution misses (title not found) | Med | Med | Resolve via the title→slug→id index (`wiki/identity.py`); emit a broken link OKF-style (consumers must tolerate) and log it |
| OKF spec churn (v0.1 → v0.2) | Med | Low | Pin to v0.1; export is additive; unknown-key tolerance means forward-compat is cheap |
| Import LLM cost — OKF bundles can be large | Med | Med | Treat OKF import as a normal ingest source → existing map-reduce + free-tier routing apply |
| Round-trip lossiness (export then re-import drift) | Low | Med | Preserve ULID `id`/`domain`/`sources` as extension keys so a MyMem-origin bundle re-imports losslessly |
| `type` taxonomy mismatch with external bundles | Med | Low | On import, map unknown `type` → closest `domain`, default `misc`; keep raw `type` as a tag |

## Recommendation

1. **Export adapter** (`mymem export okf <dir>`) — first slice, mirrors the Obsidian
   integration's CLI surface but with a real transform. Low risk.
2. **Import reader** — OKF bundle as a source type via the `SourceReader` chain; each concept
   file ingests as a source, links become wikilinks. Medium effort.
3. **Do not** adopt OKF as native storage — it would flatten MyMem's identity/claims/graph
   layers.

Priority: **P2** — schedule after the current V1-0011 compounding-ingest work.

## Sources

- [How the Open Knowledge Format can improve data sharing — Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/)
- [OKF SPEC.md — GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
- [Google Cloud Announces The Open Knowledge Format — Search Engine Journal](https://www.searchenginejournal.com/google-cloud-announces-the-open-knowledge-format/579253/)
- [Google Cloud's OKF turns scattered docs into Markdown files for AI agents — The Decoder](https://the-decoder.com/google-clouds-open-knowledge-format-turns-scattered-docs-into-markdown-files-for-ai-agents/)
- [Open Knowledge Format (OKF) — An Annotated Guide](https://okf.md/spec/)
