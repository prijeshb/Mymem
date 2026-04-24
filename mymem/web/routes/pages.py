"""
HTML page routes — server-rendered with Jinja2.

GET /             → dashboard
GET /search       → chat / search
GET /wiki/{slug}  → wiki page viewer
GET /graph        → full knowledge graph
GET /ingest       → ingest form
GET /introspect   → daily summary + recommendations
"""

from __future__ import annotations

from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import list_pages, read_page

router = APIRouter()


def _templates(request: Request):  # type: ignore[return]
    return request.app.state.templates


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from datetime import date, timedelta
    import json as _json

    wiki_dir   = request.app.state.wiki_dir
    index_path = request.app.state.index_path
    log_path   = request.app.state.log_path

    pages   = list_pages(wiki_dir)
    mgr     = IndexManager(index_path)
    entries = mgr.load()
    log     = WikiLog(log_path)
    recent  = log.recent(10)

    # Domain breakdown for curiosity bars
    domain_counts: dict[str, int] = {}
    for e in entries:
        domain_counts[e.domain.value] = domain_counts.get(e.domain.value, 0) + 1

    # Activity heatmap — count log entries per day over the last 16 weeks (112 days)
    today     = date.today()
    start_day = today - timedelta(days=111)
    day_counts: dict[str, int] = {}
    for entry in log.load():
        d = entry.timestamp.date()
        if d >= start_day:
            key = d.isoformat()
            day_counts[key] = day_counts.get(key, 0) + 1

    # Build ordered list of 112 days with counts (oldest → newest)
    heatmap_days = []
    for i in range(112):
        d = start_day + timedelta(days=i)
        heatmap_days.append({"date": d.isoformat(), "count": day_counts.get(d.isoformat(), 0)})

    return _templates(request).TemplateResponse(request, "dashboard.html", {
        "page_count":    len(pages),
        "source_count":  sum(e.source_count for e in entries),
        "entry_count":   len(entries),
        "recent_log":    recent,
        "domain_counts": domain_counts,
        "domains":       sorted(domain_counts, key=lambda d: domain_counts[d], reverse=True),
        "heatmap_days":  _json.dumps(heatmap_days),
    })


# ---------------------------------------------------------------------------
# Search / Chat
# ---------------------------------------------------------------------------

@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, domain: str = ""):
    index_path = request.app.state.index_path
    mgr     = IndexManager(index_path)
    entries = mgr.load()
    pages   = [{"title": e.title, "domain": e.domain.value, "summary": e.summary}
               for e in entries]
    return _templates(request).TemplateResponse(request, "search.html", {
        "pages_json":     pages,
        "active_domain":  domain,
    })


# ---------------------------------------------------------------------------
# Wiki page viewer
# ---------------------------------------------------------------------------

@router.get("/wiki/{slug}", response_class=HTMLResponse)
async def wiki_page(request: Request, slug: str):
    wiki_dir = request.app.state.wiki_dir
    path     = wiki_dir / f"{slug}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")

    page   = read_page(path)
    # Render body to HTML (keep wikilinks as custom spans for JS to handle)
    import re
    body_for_render = re.sub(
        r"\[\[([^\]]+)\]\]",
        lambda m: f'<a class="wikilink" href="/wiki/{m.group(1).lower().replace(" ", "-")}">'
                  f'[[{m.group(1)}]]</a>',
        page.body,
    )
    body_html = markdown.markdown(
        body_for_render,
        extensions=["fenced_code", "tables", "toc"],
    )

    # Find backlinks
    all_pages = list_pages(wiki_dir)
    backlinks = [p for p in all_pages if page.title in p.wikilinks()]

    # Build TOC from headings
    headings = re.findall(r"^(#{1,3})\s+(.+)$", page.body, re.MULTILINE)
    toc = [{"level": len(h[0]), "text": h[1], "id": h[1].lower().replace(" ", "-")}
           for h in headings]

    return _templates(request).TemplateResponse(request, "wiki_page.html", {
        "page":      page,
        "body_html": body_html,
        "backlinks": backlinks,
        "toc":       toc,
    })


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------

@router.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request):
    return _templates(request).TemplateResponse(request, "graph.html", {})


# ---------------------------------------------------------------------------
# Ingest form
# ---------------------------------------------------------------------------

@router.get("/ingest", response_class=HTMLResponse)
async def ingest_page(request: Request):
    from mymem.wiki.types import TagDomain
    return _templates(request).TemplateResponse(request, "ingest.html", {
        "domains": TagDomain.values(),
    })


# ---------------------------------------------------------------------------
# Introspect
# ---------------------------------------------------------------------------

@router.get("/introspect", response_class=HTMLResponse)
async def introspect_page(request: Request):
    from mymem.pipeline.introspect import top_interests
    curiosity_db = request.app.state.curiosity_db
    interests    = top_interests(curiosity_db, limit=10)
    return _templates(request).TemplateResponse(request, "introspect.html", {
        "interests": interests,
    })
