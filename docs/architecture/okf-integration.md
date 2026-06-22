# System Design: Open Knowledge Format (OKF) Integration

**PRD:** docs/PRD/okf-integration.md · **ADR:** docs/ADR/016-okf-integration.md
**Research:** docs/research/open-knowledge-format.md

> **Shipped (V1-0013) — supersedes the import design below.** Import was implemented as a
> **direct, lossless inverse of export** (`mymem/knowledge/okf/importer.py`: `import_okf`,
> CLI `mymem import okf`), **not** as a `SourceReader` feeding the LLM ingest pipeline. The
> pipeline route was rejected during build because it re-derives content and mints new ids,
> breaking the PRD's lossless round-trip ship gate (G4). See ADR-016 "Implementation decision".
> The `SourceReader`/`--type okf` description in the Import sections below is the original
> sketch, kept for context.

## Overview

Two on-demand adapters bridge MyMem's wiki and the OKF v0.1 file format:
an **exporter** (wiki → OKF bundle, a pure transform) and an **importer** (OKF bundle → wiki,
the direct inverse of export — see the banner above). Neither changes how MyMem stores
knowledge — OKF is an interchange surface, not the storage model.

## Architecture (ASCII)

```
                 ┌──────────────── MyMem core (unchanged) ───────────────┐
                 │  wiki/*.md (frontmatter + [[wikilinks]])               │
                 │  data/claims.db · data/graph.db · identity index       │
                 └───────────────────────────────────────────────────────┘
                         ▲                                   │
        import (ingest)  │                                   │  export (transform)
                         │                                   ▼
   OKF bundle ──► OkfSourceReader ──► ingest_source()   OkfExporter ──► OKF bundle dir
   (dir of .md)     (readers.py)       (pipeline)       (knowledge/okf/)   (/*.md,index.md,log.md)
        ▲                                                                       │
        └───────────────────── round-trips losslessly (ULID id preserved) ─────┘
```

## Components

### New module: `mymem/knowledge/okf/`

Small, focused files (per project rule: modules < 300 lines):

| File | Responsibility |
|------|----------------|
| `okf/_spec.py` | OKF constants: required field `type`, recommended fields, reserved filenames (`index.md`, `log.md`), conformance predicate |
| `okf/_map.py` | Field mapping both directions: `domain`↔`type`, `updated`↔`timestamp` (ISO 8601), `summary`↔`description`, `sources[0]`↔`resource`; extension-key preservation (`id`, `domain`, `sources`, `archived`, `created`) |
| `okf/_links.py` | Link transforms: `[[Title]]` → `[Title](/slug.md)` (export, via identity index) and `[text](/path.md)` → `[[Title]]` (import) |
| `okf/exporter.py` | `export_okf(wiki_dir, out_dir) -> ExportReport` — pure transform over `list_pages()`; writes concept files + `index.md` + `log.md`; returns counts + unresolved-link report |
| `okf/conformance.py` | `check_bundle(dir) -> ConformanceReport` — asserts every non-reserved `.md` has parseable frontmatter with non-empty `type` |

### Import: extend `mymem/pipeline/readers.py`

- New `OkfSourceReader(SourceReader)` (Strategy/Open-Closed — no edits to existing readers):
  - `can_handle(source, source_type)` → `source_type == "okf"` or dir contains OKF-shaped `.md`.
  - `read(...)` → walks the bundle; for each concept file, returns its body with OKF links
    rewritten to `[[wikilinks]]` and a synthesized header from `title`/`type`.
- Register `OkfSourceReader` in the reader chain. `"okf"` is added to the source-type set.

### CLI: `mymem/cli.py`

- `mymem export okf <out-dir> [--wiki-dir]` → calls `export_okf`, prints `ExportReport`
  (files written, links resolved/broken). Mirrors the `obsidian` subcommand surface.
- `mymem ingest <bundle-dir> --type okf` → routes through `OkfSourceReader` → `ingest_source`.

### Web UI (deferred, not v1)

Optional later: `POST /api/export/okf` + an Ingest-page tab. CLI-first for v1.

## Data Flow

### Export (wiki → OKF)
1. `export_okf(wiki_dir, out_dir)` loads pages via `wiki/page.py:list_pages()`.
2. For each page: `_map.to_okf_frontmatter(page)` (domain→type, updated→timestamp,
   summary→description, sources[0]→resource; preserve `id`/`domain`/`sources` as extensions).
3. `_links.wikilinks_to_md(body, resolve)` rewrites `[[Title]]` → `/slug.md` using
   `wiki/identity.py`; unresolved → logged + counted, emitted OKF-tolerantly.
4. Write concept file at the same relative path (`wiki/qa/foo.md` → `qa/foo.md`).
5. Generate frontmatter-free `index.md` (from `wiki/index.py`) and OKF-format `log.md`.
6. `conformance.check_bundle(out_dir)` asserts G1; return `ExportReport`.

### Import (OKF → wiki)
1. `mymem ingest <dir> --type okf` → `OkfSourceReader` claims it.
2. Per concept file: parse frontmatter; `_map.from_okf_frontmatter(...)` maps `type`→`domain`
   (unknown → `misc`, raw `type` kept as a tag), `timestamp`→`updated`, `description`→summary;
   restore `id` if present (lossless round-trip).
3. `_links.md_to_wikilinks(body)` rewrites `/path.md` links → `[[Title]]`.
4. Feed each concept to `ingest_source()` as a source → normal extraction/compounding/graph.

## Field Mapping (authoritative)

| MyMem `WikiPage` | OKF frontmatter | Direction notes |
|------------------|-----------------|-----------------|
| `domain` | `type` (**required**) | export: value of domain; import: type→domain, default `misc`, raw kept as tag |
| `title` | `title` | 1:1 |
| `IndexEntry.summary` / first paragraph | `description` | export-derived; import → summary |
| `sources[0]` | `resource` | export: primary source URI; full `sources` preserved as extension |
| `updated` (date) | `timestamp` (ISO 8601) | widen date→datetime on export; truncate on import |
| `tags` | `tags` | 1:1 |
| `id` (ULID) | `id` (extension) | preserved both ways → identity-stable round-trip |
| `domain`,`sources`,`archived`,`created` | extension keys | preserved verbatim (OKF tolerates unknown keys) |
| `[[Title]]` | `[Title](/slug.md)` | `_links` transform both ways |

## Security Considerations

- **Import = untrusted input.** OKF bundles are external files. Route through the existing
  `mymem/security/` scanner + `readers.py` sanitation that all ingest sources already use.
- **Path traversal:** importer must reject concept paths escaping the bundle root
  (`..`, absolute paths). Conformance/import resolves and validates each path is under root.
- **No SSRF surface** for export (local files only). `resource` URLs in imported bundles are
  treated as opaque metadata — not fetched.
- No secrets involved; export writes only wiki content the user already owns.

## Performance Considerations

- Export is O(pages) pure transform — trivial for the 144-page live wiki.
- Import cost is dominated by LLM ingest; bounded by existing map-reduce + free-tier routing.
- Link resolution uses the in-memory identity index (no per-link disk scan).

## API Contract (CLI v1)

```
mymem export okf OUT_DIR [--wiki-dir PATH]
  → writes OKF bundle; prints: pages, links_resolved, links_broken, conformant(bool)

mymem ingest BUNDLE_DIR --type okf [--domain D]
  → ingests each concept; prints per-concept ingest summary
```

(HTTP endpoints deferred to a follow-up.)

## Testing Strategy

- **Unit (100% on transform):** `_map` round-trip (domain↔type, date↔timestamp, extension
  preservation); `_links` both directions incl. unresolved-target handling; `_spec`
  conformance predicate.
- **Integration:** `export_okf` on a `tmp_path` fixture wiki → assert conformance + exact
  frontmatter; `OkfSourceReader` on a sample bundle → assert wikilinks + domain mapping.
- **Round-trip:** build fixture wiki → export → import → assert same ULID `id`, same `domain`,
  same `tags`, links intact.
- **No LLM in tests:** import tests inject `llm_fn` (project rule); transform tests are pure.
- **Conformance test:** export the live wiki in CI smoke and assert `check_bundle` passes.
