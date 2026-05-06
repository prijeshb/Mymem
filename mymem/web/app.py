"""
FastAPI app factory for MyMem web UI.

Usage:
    mymem serve --port 7860
    # or directly:
    uvicorn mymem.web.app:create_app --factory --port 7860
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mymem.config import get_settings
from mymem.pipeline.router import router_from_settings


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WEB_DIR      = Path(__file__).parent
TEMPLATES_DIR = _WEB_DIR / "templates"
STATIC_DIR    = _WEB_DIR / "static"
FRONTEND_DIST = _WEB_DIR.parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[type-arg]
    settings = get_settings()
    settings.ensure_dirs()

    from mymem.observability.logger import configure_logging, get_logger
    try:
        log_file = Path(settings.observability.log_file) if settings.observability.log_file else None
        configure_logging(
            level=str(settings.observability.log_level),
            fmt=str(settings.observability.log_format),
            log_file=log_file,
        )
    except Exception:
        configure_logging()  # fallback to defaults
    _log = get_logger("mymem.web")
    _log.info("MyMem web UI starting",
              wiki=settings.paths.wiki, provider=settings.provider,
              log_file=str(log_file) if log_file else "stderr only")

    wiki_dir = Path(settings.paths.wiki).resolve()
    app.state.settings    = settings
    app.state.wiki_dir    = wiki_dir
    app.state.index_path  = wiki_dir / "index.md"
    app.state.log_path    = wiki_dir / "log.md"
    app.state.db_path      = Path(settings.paths.db).resolve()
    app.state.rag_db_path  = Path(settings.paths.db).resolve().parent / "rag.db"
    app.state.curiosity_db = Path("data/curiosity.db").resolve()
    app.state.router      = router_from_settings(settings)
    app.state.templates   = Jinja2Templates(directory=str(TEMPLATES_DIR))
    _log.info("MyMem ready")
    yield
    _log.info("MyMem web UI shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="MyMem",
        description="Personal LLM-powered knowledge base",
        lifespan=_lifespan,
    )

    # API routes first — must be registered before the catch-alls
    from mymem.web.routes.api import router as api_router
    from mymem.web.routes.logs import router as logs_router
    app.include_router(api_router, prefix="/api")
    app.include_router(logs_router, prefix="/api")

    dev_mode = bool(os.environ.get("MYMEM_DEV"))

    if dev_mode:
        # Allow Vite dev server (port 5174) to call the API directly
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5174"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/", include_in_schema=False)
        async def dev_root() -> JSONResponse:
            return JSONResponse({
                "mode": "dev",
                "ui":   "http://localhost:5174",
                "docs": "/docs",
            })

    elif FRONTEND_DIST.exists():
        # Production: serve the Vite build as static files
        assets_dir = FRONTEND_DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail=f"API route not found: /{full_path}")
            candidate = FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            index = FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(str(index))
            raise HTTPException(status_code=404, detail="Frontend not built")

    else:
        # Jinja2 fallback when frontend/dist is absent and not in dev mode
        if STATIC_DIR.exists():
            app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
        from mymem.web.routes.pages import router as pages_router
        app.include_router(pages_router)

    return app


# Module-level instance for uvicorn
app = create_app()
