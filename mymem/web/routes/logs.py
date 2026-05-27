"""
Log API routes.

GET /api/log      → recent wiki log entries
GET /api/heatmap  → 16-week daily activity counts
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mymem.wiki.log import WikiLog

router = APIRouter()


@router.get("/log")
async def api_log(request: Request, limit: int = 15) -> JSONResponse:
    wiki_log = WikiLog(request.app.state.log_path)
    entries = wiki_log.recent(limit)
    return JSONResponse([
        {
            "ts":             e.timestamp.isoformat(),
            "operation":      e.operation.value,
            "description":    e.description,
            "affected_pages": list(e.affected_pages),
        }
        for e in entries
    ])


@router.get("/heatmap")
async def api_heatmap(request: Request) -> JSONResponse:
    from datetime import date, timedelta

    wiki_log  = WikiLog(request.app.state.log_path)
    today     = date.today()
    start_day = today - timedelta(days=111)

    day_counts: dict[str, int] = {}
    for entry in wiki_log.load():
        d = entry.timestamp.date()
        if d >= start_day:
            key = d.isoformat()
            day_counts[key] = day_counts.get(key, 0) + 1

    days = [
        {"date": (start_day + timedelta(days=i)).isoformat(),
         "count": day_counts.get((start_day + timedelta(days=i)).isoformat(), 0)}
        for i in range(112)
    ]
    return JSONResponse({"days": days})
