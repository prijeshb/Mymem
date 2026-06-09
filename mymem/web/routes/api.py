"""
API routes — JSON and SSE endpoints consumed by the frontend.

POST /api/query      → streaming SSE LLM answer
GET  /api/pages      → list all pages (filterable)
GET  /api/stats      → dashboard stats
GET  /api/graph      → nodes + edges for force-directed graph
POST /api/ingest     → trigger ingest
GET  /api/lint       → lint issues as JSON
GET  /api/introspect → daily summary + recommendations
GET  /api/curiosity  → top interests + trend direction
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

import shutil

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from mymem.observability.logger import get_logger
from mymem.pipeline.ingest import ingest_source
from mymem.pipeline.introspect import introspect, top_interests, generate_questions, generate_digest
from mymem.pipeline.lint import format_lint_report, lint_wiki
from mymem.pipeline.query import query_wiki
from mymem.wiki.index import IndexManager
from mymem.wiki.page import list_pages
from mymem.wiki.types import TagDomain

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Related-concepts helper
# ---------------------------------------------------------------------------

def _title_to_slug(title: str) -> str:
    return title.lower().replace(" ", "-")


def _normalize_slug(slug: str) -> str:
    if len(slug) > 200:
        raise HTTPException(status_code=400, detail="Slug too long")
    from mymem.wiki.types import slugify
    return slugify(slug)


from mymem.pipeline.search import search_concept


def build_related_concepts(
    wikilinks: list[str],
    existing_slugs: set[str],
) -> list[dict]:
    """
    Build a deduplicated skeleton of related-concept payloads.
    web_links is empty here — populated asynchronously by build_related_concepts_async.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for title in wikilinks:
        slug = _title_to_slug(title)
        if slug in seen:
            continue
        seen.add(slug)
        result.append({
            "title": title,
            "slug": slug,
            "internal": slug in existing_slugs,
            "web_links": [],
        })
    return result


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    question:      str
    domain:        str = ""
    top_k:         int = 5
    save:          bool = False


class IngestRequest(BaseModel):
    source:      str
    # article | paper | repo | dataset | image | youtube | podcast | tweet
    # webpage | book | newsletter | note
    source_type: str = "article"
    tags:        list[str] = []
    domain:      str = ""


class IngestTextRequest(BaseModel):
    text:        str
    # article | paper | repo | dataset | image | youtube | podcast | tweet
    # webpage | book | newsletter | note
    source_type: str = "article"
    tags:        list[str] = []
    domain:      str = ""
    title:       str = ""    # optional hint for the wiki page title


# ---------------------------------------------------------------------------
# POST /api/query  (streaming SSE)
# ---------------------------------------------------------------------------

@router.post("/query")
async def api_query(req: QueryRequest, request: Request) -> StreamingResponse:
    wiki_dir   = request.app.state.wiki_dir
    index_path = request.app.state.index_path
    log_path   = request.app.state.log_path
    llm_router = request.app.state.router

    domain_filter = TagDomain(req.domain) if req.domain else None

    async def event_stream() -> AsyncIterator[str]:
        try:
            rag_db = request.app.state.rag_db_path
            result = await query_wiki(
                req.question,
                wiki_dir=wiki_dir,
                index_path=index_path,
                log_path=log_path,
                router=llm_router,
                top_k=req.top_k,
                save=req.save,
                domain_filter=domain_filter,
                rag_db_path=rag_db if rag_db.exists() else None,
            )
            # Stream answer in chunks for a streaming feel
            words = result.answer.split(" ")
            chunk: list[str] = []
            for word in words:
                chunk.append(word)
                if len(chunk) >= 8:
                    data = json.dumps({"type": "token", "text": " ".join(chunk) + " "})
                    yield f"data: {data}\n\n"
                    chunk = []
                    await asyncio.sleep(0)
            if chunk:
                data = json.dumps({"type": "token", "text": " ".join(chunk)})
                yield f"data: {data}\n\n"

            # Send citations + done signal
            done = json.dumps({
                "type":      "done",
                "citations": result.citations,
                "saved_to":  result.saved_to,
            })
            yield f"data: {done}\n\n"

        except Exception as exc:
            log.exception("query stream failed", error=str(exc))
            err = json.dumps({"type": "error", "message": "Query failed. Please try again."})
            yield f"data: {err}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET /api/pages
# ---------------------------------------------------------------------------

@router.get("/pages")
async def api_pages(
    request: Request,
    domain: str = "",
    tag: str = "",
    q: str = "",
    page: int = 0,
    limit: int = 0,
) -> JSONResponse:
    index_path = request.app.state.index_path
    mgr     = IndexManager(index_path)
    entries = mgr.load()

    # Most recently modified pages first
    wiki_dir = request.app.state.wiki_dir
    entries.sort(
        key=lambda e: (wiki_dir / e.path).stat().st_mtime if (wiki_dir / e.path).exists() else 0,
        reverse=True,
    )

    if domain:
        entries = [e for e in entries if e.domain.value == domain]
    if tag:
        entries = [e for e in entries if tag in e.tags]
    if q:
        ql = q.lower()
        entries = [
            e for e in entries
            if ql in e.title.lower() or ql in (e.summary or "").lower()
        ]

    total = len(entries)

    if limit > 0:
        entries = entries[page * limit : (page + 1) * limit]

    items = [
        {
            "title":   e.title,
            "path":    str(e.path),
            "summary": e.summary,
            "domain":  e.domain.value,
            "tags":    list(e.tags),
            "sources": e.source_count,
        }
        for e in entries
    ]

    if limit > 0:
        return JSONResponse({"items": items, "total": total, "page": page, "limit": limit})

    return JSONResponse(items)


# ---------------------------------------------------------------------------
# GET /api/stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def api_stats(request: Request) -> JSONResponse:
    wiki_dir      = request.app.state.wiki_dir
    index_path    = request.app.state.index_path
    curiosity_db  = request.app.state.curiosity_db
    llm_router    = request.app.state.router

    pages   = list_pages(wiki_dir)
    mgr     = IndexManager(index_path)
    entries = mgr.load()

    from mymem.pipeline.lint import lint_wiki, IssueKind
    issues   = lint_wiki(wiki_dir)
    orphans  = sum(1 for i in issues if i.kind == IssueKind.ORPHAN)

    domain_counts: dict[str, int] = {}
    for e in entries:
        domain_counts[e.domain.value] = domain_counts.get(e.domain.value, 0) + 1

    from mymem.rag.store import count_chunks
    wiki_chunks = count_chunks(request.app.state.rag_db_path, chunk_type="child")

    return JSONResponse({
        "page_count":    len(pages),
        "source_count":  sum(e.source_count for e in entries),
        "orphan_count":  orphans,
        "session_cost":  round(llm_router.session_cost, 4),
        "domain_counts": domain_counts,
        "wiki_chunks":   wiki_chunks,
    })


# ---------------------------------------------------------------------------
# GET /api/graph
# ---------------------------------------------------------------------------

@router.get("/graph")
async def api_graph(request: Request) -> JSONResponse:
    wiki_dir = request.app.state.wiki_dir
    pages    = list_pages(wiki_dir)

    title_set = {p.title for p in pages}
    nodes = [
        {
            "id":     p.title,
            "slug":   p.slug,
            "domain": p.domain.value,
            "tags":   list(p.tags),
        }
        for p in pages
    ]
    edges = []
    for page in pages:
        for link in page.wikilinks():
            if link in title_set:
                edges.append({"source": page.title, "target": link})

    return JSONResponse({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# POST /api/ingest
# ---------------------------------------------------------------------------

@router.post("/ingest")
async def api_ingest(req: IngestRequest, request: Request) -> JSONResponse:
    wiki_dir   = request.app.state.wiki_dir
    index_path = request.app.state.index_path
    log_path   = request.app.state.log_path
    llm_router = request.app.state.router

    try:
        result = await ingest_source(
            req.source,
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=llm_router,
            source_type=req.source_type,
            tags=req.tags,
            domain=req.domain,
            max_concepts=request.app.state.settings.pipeline.max_concepts,
            db_path=request.app.state.db_path,
        )
        return JSONResponse({
            "skipped":       result.skipped,
            "skip_reason":   result.skip_reason,
            "pages_written": result.pages_written,
            "pages_updated": result.pages_updated,
            "chunk_count":   result.chunk_count,
        })
    except Exception as exc:
        log.exception("ingest failed", source=req.source, error=str(exc))
        raise HTTPException(status_code=500, detail="Ingest failed. Check server logs for details.")


# ---------------------------------------------------------------------------
# POST /api/upload  (multipart file upload → ingest)
# ---------------------------------------------------------------------------

_SOURCE_TYPE_SUBDIR: dict[str, str] = {
    "paper":     "papers",
    "book":      "books",
    "article":   "articles",
    "dataset":   "datasets",
    "repo":      "repos",
    "image":     "images",
    "note":      "notes",
}


@router.post("/upload")
async def api_upload(
    request: Request,
    file:        UploadFile = File(...),
    source_type: str        = Form("article"),
    domain:      str        = Form(""),
    tags:        str        = Form(""),      # comma-separated
) -> JSONResponse:
    wiki_dir   = request.app.state.wiki_dir
    index_path = request.app.state.index_path
    log_path   = request.app.state.log_path
    llm_router = request.app.state.router
    settings   = request.app.state.settings

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Save to raw/<subdir>/ so PDFs persist for RAG indexing
    raw_root = Path(settings.paths.raw).resolve()
    subdir   = _SOURCE_TYPE_SUBDIR.get(source_type, "misc")
    dest_dir = raw_root / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "upload").name.replace(" ", "_")
    dest_path = dest_dir / safe_name
    # Avoid silent overwrites — append suffix if file already exists
    if dest_path.exists():
        stem   = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        with dest_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        result = await ingest_source(
            str(dest_path),
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=llm_router,
            source_type=source_type,
            tags=tag_list,
            domain=domain,
            max_concepts=settings.pipeline.max_concepts,
            db_path=request.app.state.db_path,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("upload failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({
        "skipped":       result.skipped,
        "skip_reason":   result.skip_reason,
        "pages_written": result.pages_written,
        "pages_updated": result.pages_updated,
        "chunk_count":   result.chunk_count,
        "rag_only":      result.rag_only,
        "rag_chunks":    result.rag_chunks,
        "saved_to":      str(dest_path.relative_to(Path.cwd()) if dest_path.is_relative_to(Path.cwd()) else dest_path),
    })


# ---------------------------------------------------------------------------
# POST /api/ingest-text  (paste raw text → ingest)
# ---------------------------------------------------------------------------

@router.post("/ingest-text")
async def api_ingest_text(req: IngestTextRequest, request: Request) -> JSONResponse:
    wiki_dir   = request.app.state.wiki_dir
    index_path = request.app.state.index_path
    log_path   = request.app.state.log_path
    llm_router = request.app.state.router

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    # mkstemp avoids the Windows NamedTemporaryFile locking issue — the fd is
    # closed before ingest_source opens the file, so no FILE_SHARE conflict.
    fd, tmp_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            if req.title:
                tmp.write(f"# {req.title}\n\n")
            tmp.write(req.text)

        result = await ingest_source(
            tmp_path,
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=llm_router,
            source_type=req.source_type,
            tags=req.tags,
            domain=req.domain,
            max_concepts=request.app.state.settings.pipeline.max_concepts,
            db_path=request.app.state.db_path,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("ingest-text failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return JSONResponse({
        "skipped":       result.skipped,
        "skip_reason":   result.skip_reason,
        "pages_written": result.pages_written,
        "pages_updated": result.pages_updated,
        "chunk_count":   result.chunk_count,
    })


# ---------------------------------------------------------------------------
# GET /api/page/{slug}  (React wiki page viewer)
# ---------------------------------------------------------------------------

@router.get("/page/{slug:path}")
async def api_page(slug: str, request: Request) -> JSONResponse:
    import re
    wiki_dir = request.app.state.wiki_dir

    from mymem.wiki.page import read_page, list_pages
    slug = _normalize_slug(slug)
    path = wiki_dir / f"{slug}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    if not path.resolve().is_relative_to(wiki_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid slug")

    page     = read_page(path)
    all_pgs  = list_pages(wiki_dir)
    existing_slugs = {_title_to_slug(p.title) for p in all_pgs}
    backlinks = [
        {"title": p.title, "slug": p.slug}
        for p in all_pgs if page.title in p.wikilinks()
    ]
    headings = re.findall(r"^(#{1,3})\s+(.+)$", page.body, re.MULTILINE)
    toc = [
        {"level": len(h[0]), "text": h[1], "id": h[1].lower().replace(" ", "-")}
        for h in headings
    ]
    related = build_related_concepts(page.wikilinks(), existing_slugs)
    return JSONResponse({
        "title":     page.title,
        "body":      page.body,
        "domain":    page.domain.value,
        "tags":      list(page.tags),
        "sources":   list(page.sources),
        "created":   page.created.isoformat(),
        "updated":   page.updated.isoformat(),
        "slug":      slug,
        "archived":  page.archived,
        "backlinks": backlinks,
        "toc":       toc,
        "related":   related,
    })


# ---------------------------------------------------------------------------
# GET /api/related-web  — SSE stream of Wikipedia results per concept
# ---------------------------------------------------------------------------

@router.get("/related-web")
async def api_related_web(
    request: Request,
    concepts: str = "",
    page_slug: str = "",
) -> StreamingResponse:
    """
    Stream web search results for a comma-separated list of concept titles.

    Each SSE event is JSON: { slug, web_links: [{label, url, snippet, source}] }
    A final { done: true } event signals completion.

    page_slug: slug of the current wiki page — used for TF-IDF context (Phase 2).
    """
    titles = [t.strip() for t in concepts.split(",") if t.strip()]

    page_body = ""
    if page_slug:
        try:
            wiki_dir: Path = request.app.state.wiki_dir
            page_path = wiki_dir / f"{page_slug}.md"
            if page_path.exists():
                from mymem.wiki.page import read_page as _read_page
                page_body = _read_page(page_path).body
        except Exception:
            pass

    async def _generate() -> AsyncIterator[str]:
        for title in titles:
            links = await search_concept(title, page_body=page_body)
            payload = json.dumps({"slug": _title_to_slug(title), "web_links": links})
            yield f"data: {payload}\n\n"
        yield 'data: {"done":true}\n\n'

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# PATCH /api/page/{slug}  (update tags + domain)
# ---------------------------------------------------------------------------

class PageUpdateRequest(BaseModel):
    tags:   list[str] = []
    domain: str = ""


@router.patch("/page/{slug:path}")
async def api_page_update(slug: str, req: PageUpdateRequest, request: Request) -> JSONResponse:
    from mymem.wiki.page import read_page, write_page
    from mymem.wiki.tags import domain_from_str, normalize_tags
    from dataclasses import replace

    wiki_dir = request.app.state.wiki_dir
    slug = _normalize_slug(slug)
    path = wiki_dir / f"{slug}.md"
    if not path.resolve().is_relative_to(wiki_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid slug")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    page = read_page(path)
    updated = replace(
        page,
        tags=normalize_tags(req.tags),
        domain=domain_from_str(req.domain) if req.domain else page.domain,
    )
    write_page(updated)
    return JSONResponse({"ok": True, "tags": list(updated.tags), "domain": updated.domain.value})


# ---------------------------------------------------------------------------
# DELETE /api/page/{slug}
# ---------------------------------------------------------------------------

@router.delete("/page/{slug:path}")
async def api_page_delete(slug: str, request: Request) -> JSONResponse:
    from mymem.wiki.page import read_page
    from mymem.wiki.index import IndexManager

    wiki_dir = request.app.state.wiki_dir
    slug = _normalize_slug(slug)
    path = wiki_dir / f"{slug}.md"
    if not path.resolve().is_relative_to(wiki_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid slug")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    page = read_page(path)
    path.unlink()

    index_path = wiki_dir / "index.md"
    if index_path.exists():
        IndexManager(index_path).remove(page.title)

    return JSONResponse({"ok": True, "deleted": slug})


# ---------------------------------------------------------------------------
# POST /api/page/{slug}/archive   POST /api/page/{slug}/restore
# ---------------------------------------------------------------------------

@router.post("/page/{slug:path}/archive")
async def api_page_archive(slug: str, request: Request) -> JSONResponse:
    from mymem.wiki.page import read_page, write_page
    from mymem.wiki.index import IndexManager
    import dataclasses

    wiki_dir = request.app.state.wiki_dir
    slug = _normalize_slug(slug)
    path = wiki_dir / f"{slug}.md"
    if not path.resolve().is_relative_to(wiki_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid slug")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    page = read_page(path)
    if page.archived:
        return JSONResponse({"ok": True, "archived": True})

    write_page(dataclasses.replace(page, archived=True))

    index_path = wiki_dir / "index.md"
    if index_path.exists():
        IndexManager(index_path).remove(page.title)

    return JSONResponse({"ok": True, "archived": True})


@router.post("/page/{slug:path}/restore")
async def api_page_restore(slug: str, request: Request) -> JSONResponse:
    from mymem.wiki.page import read_page, write_page
    from mymem.wiki.index import IndexManager
    from mymem.wiki.types import IndexEntry
    import dataclasses

    wiki_dir = request.app.state.wiki_dir
    slug = _normalize_slug(slug)
    path = wiki_dir / f"{slug}.md"
    if not path.resolve().is_relative_to(wiki_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid slug")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    page = read_page(path)
    if not page.archived:
        return JSONResponse({"ok": True, "archived": False})

    restored = dataclasses.replace(page, archived=False)
    write_page(restored)

    index_path = wiki_dir / "index.md"
    if index_path.exists():
        IndexManager(index_path).upsert(IndexEntry(
            title=restored.title,
            path=path.relative_to(wiki_dir),
            summary="",
            category=restored.domain.value,
            source_count=len(restored.sources),
            domain=restored.domain,
            tags=restored.tags,
        ))

    return JSONResponse({"ok": True, "archived": False})


# ---------------------------------------------------------------------------
# GET /api/archived  (list archived pages)
# ---------------------------------------------------------------------------

@router.get("/archived")
async def api_archived(request: Request) -> JSONResponse:
    from mymem.wiki.page import list_archived_pages

    wiki_dir = request.app.state.wiki_dir
    pages = list_archived_pages(wiki_dir)
    return JSONResponse([
        {
            "title":  p.title,
            "slug":   p.slug,
            "domain": p.domain.value,
            "tags":   list(p.tags),
            "updated": p.updated.isoformat(),
        }
        for p in pages
    ])


# ---------------------------------------------------------------------------
# GET /api/lint
# ---------------------------------------------------------------------------

@router.get("/lint")
async def api_lint(request: Request) -> JSONResponse:
    wiki_dir = request.app.state.wiki_dir
    issues   = lint_wiki(wiki_dir)
    return JSONResponse({
        "count": len(issues),
        "issues": [
            {"kind": i.kind.value, "page": i.page_title, "detail": i.detail}
            for i in issues
        ],
        "report": format_lint_report(issues),
    })


# ---------------------------------------------------------------------------
# GET /api/daily  (list saved daily summary pages)
# ---------------------------------------------------------------------------

@router.get("/daily")
async def api_daily(request: Request, limit: int = 14) -> JSONResponse:
    wiki_dir  = request.app.state.wiki_dir
    daily_dir = wiki_dir / "daily"
    if not daily_dir.exists():
        return JSONResponse([])

    from mymem.wiki.page import read_page

    results = []
    for md_file in sorted(daily_dir.glob("*.md"), reverse=True)[:limit]:
        try:
            page = read_page(md_file)
            results.append({
                "date":  md_file.stem,          # YYYY-MM-DD
                "title": page.title,
                "body":  page.body,
                "slug":  f"daily/{md_file.stem}",
            })
        except Exception:
            continue

    return JSONResponse(results)


# ---------------------------------------------------------------------------
# GET /api/introspect
# ---------------------------------------------------------------------------

@router.get("/introspect")
async def api_introspect(
    request: Request,
    topic: str = "",
    date_str: str = "",
    force: bool = False,
) -> JSONResponse:
    wiki_dir     = request.app.state.wiki_dir
    index_path   = request.app.state.index_path
    log_path     = request.app.state.log_path
    curiosity_db = request.app.state.curiosity_db
    llm_router   = request.app.state.router

    from datetime import date
    target_date: date | None = None
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")

    result = await introspect(
        wiki_dir=wiki_dir,
        index_path=index_path,
        log_path=log_path,
        curiosity_db=curiosity_db,
        router=llm_router,
        target_date=target_date,
        topic=topic or None,
        save=not bool(topic),
        force=force,
    )
    return JSONResponse({
        "date":         result.target_date.isoformat(),
        "generated_at": result.generated_at.isoformat(),
        "summary":      result.summary,
        "saved_to":     result.saved_to,
        "recommendations": [
            {
                "page":      r.page_title,
                "reason":    r.reason,
                "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            }
            for r in result.recommendations
        ],
        "top_interests": result.top_interests,
    })


# ---------------------------------------------------------------------------
# GET /api/introspect/questions
# ---------------------------------------------------------------------------

@router.get("/introspect/questions")
async def api_introspect_questions(
    request: Request,
    n: int = 5,
) -> JSONResponse:
    wiki_dir   = request.app.state.wiki_dir
    llm_router = request.app.state.router

    questions = await generate_questions(wiki_dir=wiki_dir, router=llm_router, n_pages=n)
    return JSONResponse([
        {
            "question":   q.question,
            "page_title": q.page_title,
            "hint":       q.hint,
            "difficulty": q.difficulty,
        }
        for q in questions
    ])


# ---------------------------------------------------------------------------
# GET /api/introspect/digest
# ---------------------------------------------------------------------------

@router.get("/introspect/digest")
async def api_introspect_digest(
    request:    Request,
    period:     int = 7,
) -> JSONResponse:
    wiki_dir     = request.app.state.wiki_dir
    log_path     = request.app.state.log_path
    curiosity_db = request.app.state.curiosity_db
    llm_router   = request.app.state.router

    result = await generate_digest(
        wiki_dir=wiki_dir,
        log_path=log_path,
        curiosity_db=curiosity_db,
        router=llm_router,
        period_days=period,
    )
    return JSONResponse({
        "period_days":          result.period_days,
        "date_range":           result.date_range,
        "pages_active":         result.pages_active,
        "queries_made":         result.queries_made,
        "themes":               [{"theme": t.theme, "pages": t.pages, "insight": t.insight} for t in result.themes],
        "emerging_connections": result.emerging_connections,
        "knowledge_gaps":       result.knowledge_gaps,
        "serendipity":          result.serendipity,
        "open_question":        result.open_question,
    })


# ---------------------------------------------------------------------------
# GET /api/curiosity
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET /api/analytics/youtube  — enrichment A/B comparison
# ---------------------------------------------------------------------------

@router.get("/analytics/youtube")
async def api_analytics_youtube(request: Request) -> JSONResponse:
    """
    Compare wiki page quality between enriched (yt-dlp metadata) and plain
    (transcript-only) YouTube ingests.

    Response shape:
      {
        enriched: { count, avg_concepts, avg_page_chars, avg_wikilinks },
        plain:    { count, avg_concepts, avg_page_chars, avg_wikilinks },
        delta:    { concepts_pct, page_chars_pct, wikilinks_pct }   // % improvement
      }
    """
    from mymem.observability.ingest_analytics import youtube_comparison, recent_ingests

    db_path = request.app.state.db_path
    stats   = youtube_comparison(db_path)
    recent  = recent_ingests(db_path, limit=10)

    def _delta_pct(enriched: float, plain: float) -> float | None:
        if plain == 0:
            return None
        return round((enriched - plain) / plain * 100, 1)

    return JSONResponse({
        "enriched": {
            "count":           stats.enriched_count,
            "avg_concepts":    round(stats.enriched_avg_concepts, 2),
            "avg_page_chars":  round(stats.enriched_avg_page_chars),
            "avg_wikilinks":   round(stats.enriched_avg_wikilinks, 2),
        },
        "plain": {
            "count":           stats.plain_count,
            "avg_concepts":    round(stats.plain_avg_concepts, 2),
            "avg_page_chars":  round(stats.plain_avg_page_chars),
            "avg_wikilinks":   round(stats.plain_avg_wikilinks, 2),
        },
        "delta": {
            "concepts_pct":    _delta_pct(stats.enriched_avg_concepts,    stats.plain_avg_concepts),
            "page_chars_pct":  _delta_pct(stats.enriched_avg_page_chars,  stats.plain_avg_page_chars),
            "wikilinks_pct":   _delta_pct(stats.enriched_avg_wikilinks,   stats.plain_avg_wikilinks),
        },
        "recent": recent,
    })


# ---------------------------------------------------------------------------
# GET /api/evals/summary
# ---------------------------------------------------------------------------

@router.get("/evals/summary")
async def api_evals_summary(request: Request) -> JSONResponse:
    """Latest eval run summary for each eval type, read from data/evals.db."""
    from mymem.evals.store import latest_summary

    db_path = request.app.state.db_path
    evals_db = db_path.parent / "evals.db"
    return JSONResponse(latest_summary(evals_db))


@router.get("/evals/history")
async def api_evals_history(request: Request, limit: int = 30) -> JSONResponse:
    """Historical eval runs per type — for trend charts in the dashboard."""
    from mymem.evals.store import history_by_type

    db_path = request.app.state.db_path
    evals_db = db_path.parent / "evals.db"
    return JSONResponse(history_by_type(evals_db, limit_per_type=max(1, min(limit, 100))))


@router.get("/evals/extraction")
async def api_evals_extraction(
    request: Request,
    limit: int = 50,
    order: str = "recent_first",
    grade: str = "",
) -> JSONResponse:
    """Extraction consensus eval runs — recent or worst-first, optional grade filter."""
    from mymem.evals.store import recent_consensus_runs

    db_path = request.app.state.db_path
    evals_db = db_path.parent / "evals.db"
    valid_order = order if order in ("recent_first", "worst_first") else "recent_first"
    runs = recent_consensus_runs(evals_db, limit=max(1, min(limit, 200)), order=valid_order)
    if grade in ("PASS", "WARN", "FAIL"):
        runs = [r for r in runs if r["grade"] == grade]
    return JSONResponse({"runs": runs, "total": len(runs)})


@router.get("/curiosity")
async def api_curiosity(request: Request, limit: int = 10) -> JSONResponse:
    curiosity_db = request.app.state.curiosity_db
    interests    = top_interests(curiosity_db, limit=limit)

    # Tag as rising (weight > 2) or fading (weight < 0.5) for trend display
    for item in interests:
        w = float(item["weight"])  # type: ignore[arg-type]
        item["trend"] = "rising" if w >= 2.0 else ("fading" if w < 0.5 else "stable")

    return JSONResponse({"interests": interests})


# ---------------------------------------------------------------------------
# GET /api/rag/sources  — list indexed PDFs
# ---------------------------------------------------------------------------

@router.get("/rag/sources")
async def api_rag_sources(request: Request) -> JSONResponse:
    rag_db: Path = request.app.state.rag_db_path
    if not rag_db.exists():
        return JSONResponse({"sources": []})

    from mymem.rag.store import list_sources
    sources = list_sources(rag_db)
    return JSONResponse({"sources": sources})


# ---------------------------------------------------------------------------
# GET /api/traces  — LLM call latency / cost breakdown
# ---------------------------------------------------------------------------

@router.get("/traces")
async def api_traces(request: Request, limit: int = 50) -> JSONResponse:
    """
    Return recent LLM traces plus per-model and per-task aggregates.

    Response shape:
      recent   — last N rows from llm_traces, newest first
      by_model — calls / avg_latency_ms / total_cost_usd / error_rate per model
      by_task  — calls / avg_latency_ms / total_cost_usd per task
      totals   — overall call count, cost, avg latency
    """
    import sqlite3

    db_path: Path = request.app.state.db_path
    if not db_path.exists():
        return JSONResponse({"recent": [], "by_model": [], "by_task": [], "totals": {}})

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Check table exists
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "llm_traces" not in tables:
                return JSONResponse({"recent": [], "by_model": [], "by_task": [], "totals": {}})

            recent = [
                dict(row) for row in conn.execute(
                    """SELECT id, task, model, provider, started_at,
                              latency_ms, input_tokens, output_tokens, cost_usd, error
                       FROM llm_traces
                       ORDER BY id DESC LIMIT ?""",
                    (max(1, min(limit, 500)),),
                ).fetchall()
            ]

            by_model = [
                dict(row) for row in conn.execute(
                    """SELECT model,
                              COUNT(*)                                    AS calls,
                              ROUND(AVG(latency_ms), 1)                  AS avg_latency_ms,
                              ROUND(SUM(cost_usd), 6)                    AS total_cost_usd,
                              ROUND(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)
                                    * 1.0 / COUNT(*), 3)                 AS error_rate
                       FROM llm_traces
                       GROUP BY model
                       ORDER BY calls DESC""",
                ).fetchall()
            ]

            by_task = [
                dict(row) for row in conn.execute(
                    """SELECT task,
                              COUNT(*)                                    AS calls,
                              ROUND(AVG(latency_ms), 1)                  AS avg_latency_ms,
                              ROUND(SUM(cost_usd), 6)                    AS total_cost_usd
                       FROM llm_traces
                       GROUP BY task
                       ORDER BY calls DESC""",
                ).fetchall()
            ]

            totals_row = conn.execute(
                """SELECT COUNT(*)                   AS calls,
                          ROUND(SUM(cost_usd), 6)   AS total_cost_usd,
                          ROUND(AVG(latency_ms), 1) AS avg_latency_ms
                   FROM llm_traces"""
            ).fetchone()
            totals = dict(totals_row) if totals_row else {}

    except Exception:
        log.exception("Failed to read llm_traces")
        raise HTTPException(status_code=500, detail="Failed to read trace data")

    return JSONResponse({"recent": recent, "by_model": by_model, "by_task": by_task, "totals": totals})
