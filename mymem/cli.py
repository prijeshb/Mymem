"""
MyMem CLI — ingest / query / lint / introspect / serve / tags

Entry point: mymem (configured in pyproject.toml → project.scripts)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, TextColumn
from rich.table import Table

app          = typer.Typer(name="mymem", help="Personal LLM-powered knowledge base.", add_completion=False)
obsidian_app = typer.Typer(name="obsidian", help="Obsidian vault integration.")
graph_app    = typer.Typer(name="graph", help="Entity graph operations (ADR-007).")
pages_app    = typer.Typer(name="pages", help="Wiki page identity operations (ADR-013).")
app.add_typer(obsidian_app, name="obsidian")
app.add_typer(graph_app, name="graph")
app.add_typer(pages_app, name="pages")
console = Console()
err     = Console(stderr=True, style="red")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_settings():  # type: ignore[return]
    from mymem.config import get_settings
    from mymem.observability.logger import configure_logging
    from pathlib import Path as _Path
    settings = get_settings()
    log_file = _Path(settings.observability.log_file) if settings.observability.log_file else None
    configure_logging(
        level=settings.observability.log_level,
        fmt=settings.observability.log_format,
        log_file=log_file,
    )
    return settings


def _make_router(settings, llm_fn=None):  # type: ignore[return]
    from mymem.pipeline.router import router_from_settings
    return router_from_settings(settings, llm_fn=llm_fn)


def _paths(settings):  # type: ignore[return]
    wiki_dir     = Path(settings.paths.wiki)
    index_path   = wiki_dir / "index.md"
    log_path     = wiki_dir / "log.md"
    curiosity_db = Path("data/curiosity.db")
    return wiki_dir, index_path, log_path, curiosity_db


def _run(coro):  # type: ignore[return]
    """Run an async coroutine from sync Typer command.

    After the main coroutine completes, drains any background tasks that were
    scheduled with asyncio.ensure_future / create_task (e.g. extraction eval).
    Without this, asyncio.run() closes the loop before fire-and-forget tasks run.
    """
    async def _with_background_drain():
        result = await coro
        pending = [
            t for t in asyncio.all_tasks()
            if not t.done() and t is not asyncio.current_task()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return result

    return asyncio.run(_with_background_drain())


# ---------------------------------------------------------------------------
# mymem ingest
# ---------------------------------------------------------------------------

@app.command()
def ingest(
    source: str = typer.Argument(..., help="File path or URL to ingest"),
    source_type: str = typer.Option("article", "--type", "-t",
        help="article | paper | repo | dataset | image | youtube | podcast | tweet | webpage | book | newsletter | note"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain override"),
) -> None:
    """Ingest a source document into the wiki."""
    settings = _get_settings()
    settings.ensure_dirs()
    wiki_dir, index_path, log_path, curiosity_db = _paths(settings)
    router   = _make_router(settings)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    from mymem.pipeline.ingest import ingest_source
    from mymem.pipeline.introspect import log_curiosity_event
    from mymem.wiki.types import TagDomain
    from mymem.wiki.tags import domain_from_str, normalize_tags

    with Progress(TextColumn("[cyan]>[/cyan]"), TextColumn("{task.description}"), console=console) as prog:
        task = prog.add_task(f"Ingesting [cyan]{source}[/]…")
        result = _run(ingest_source(
            source,
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=router,
            source_type=source_type,
            tags=tag_list,
            domain=domain,
        ))
        prog.update(task, completed=True)

    if result.skipped:
        console.print(Panel(f"[yellow]Skipped:[/] {result.skip_reason}", title="Ingest"))
        raise typer.Exit(1)

    # Log curiosity events for written pages
    domain_obj = domain_from_str(domain) if domain else TagDomain.MISC
    norm_tags  = normalize_tags(tag_list)
    log_curiosity_event(curiosity_db, "ingest", domain_obj, norm_tags)

    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_row("[green]New pages[/]",     ", ".join(result.pages_written) or "—")
    t.add_row("[blue]Updated pages[/]",  ", ".join(result.pages_updated) or "—")
    t.add_row("[dim]Chunks used[/]",     str(result.chunk_count))
    t.add_row("[dim]Session cost[/]",    f"${router.session_cost:.4f}")
    console.print(Panel(t, title="[bold green]✓ Ingest complete[/]"))


# ---------------------------------------------------------------------------
# mymem query
# ---------------------------------------------------------------------------

@app.command()
def query(
    question: str = typer.Argument(..., help="Question to answer from the wiki"),
    top_k: int     = typer.Option(5,  "--top-k", "-k", help="Max pages to use as context"),
    save: bool     = typer.Option(False, "--save", "-s", help="Save answer as wiki page"),
    domain: str    = typer.Option("", "--domain", "-d", help="Filter to domain"),
) -> None:
    """Ask a question — answered from your wiki."""
    settings = _get_settings()
    settings.ensure_dirs()
    wiki_dir, index_path, log_path, curiosity_db = _paths(settings)
    router   = _make_router(settings)

    from mymem.pipeline.query import query_wiki
    from mymem.pipeline.introspect import log_curiosity_event
    from mymem.wiki.types import TagDomain
    from mymem.wiki.tags import domain_from_str

    domain_obj    = domain_from_str(domain) if domain else None
    domain_filter = TagDomain(domain) if domain else None

    with Progress(TextColumn("[cyan]>[/cyan]"), TextColumn("Searching wiki…"), console=console) as prog:
        task = prog.add_task("query")
        result = _run(query_wiki(
            question,
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=router,
            top_k=top_k,
            save=save,
            domain_filter=domain_filter,
        ))
        prog.update(task, completed=True)

    # Log curiosity event
    log_curiosity_event(
        curiosity_db, "query",
        domain_obj or TagDomain.MISC, [],
        query_text=question[:120],
    )

    console.print()
    console.print(Markdown(result.answer))

    if result.citations:
        console.print()
        console.print("[dim]Sources:[/] " + "  ".join(f"[[{c}]]" for c in result.citations))
    if result.saved_to:
        console.print(f"\n[green]Saved →[/] {result.saved_to}")
    console.print(f"\n[dim]Session cost: ${router.session_cost:.4f}[/]")


# ---------------------------------------------------------------------------
# mymem lint
# ---------------------------------------------------------------------------

@app.command()
def lint(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Check the wiki for orphans, broken links, and stub pages."""
    settings = _get_settings()
    wiki_dir = Path(settings.paths.wiki)

    from mymem.pipeline.lint import lint_wiki, format_lint_report, IssueKind

    issues = lint_wiki(wiki_dir)

    if as_json:
        typer.echo(json.dumps([
            {"kind": i.kind.value, "page": i.page_title, "detail": i.detail}
            for i in issues
        ], indent=2))
        return

    if not issues:
        console.print("[green]✓ Wiki is clean — no issues found.[/]")
        return

    table = Table(title=f"Lint Issues ({len(issues)})", show_lines=True)
    table.add_column("Kind",  style="yellow", width=12)
    table.add_column("Page",  style="cyan")
    table.add_column("Detail")

    color = {IssueKind.ORPHAN: "yellow", IssueKind.BROKEN_LINK: "red", IssueKind.STUB: "dim"}
    for issue in issues:
        table.add_row(
            f"[{color[issue.kind]}]{issue.kind.value}[/]",
            issue.page_title,
            issue.detail,
        )
    console.print(table)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mymem introspect
# ---------------------------------------------------------------------------

@app.command()
def introspect(
    topic: str  = typer.Option("", "--topic", "-t", help="Research suggestion mode"),
    date_str: str = typer.Option("", "--date",  help="Summarise a past date (YYYY-MM-DD)"),
    no_save: bool = typer.Option(False, "--no-save", help="Don't write daily page"),
) -> None:
    """Daily summary + curiosity-driven reading suggestions."""
    from datetime import date as dateclass
    settings = _get_settings()
    settings.ensure_dirs()
    wiki_dir, index_path, log_path, curiosity_db = _paths(settings)
    router   = _make_router(settings)

    from mymem.pipeline.introspect import introspect as run_introspect

    target_date = None
    if date_str:
        try:
            target_date = dateclass.fromisoformat(date_str)
        except ValueError:
            err.print(f"Invalid date: {date_str!r}. Use YYYY-MM-DD.")
            raise typer.Exit(1)

    with Progress(TextColumn("[cyan]>[/cyan]"), TextColumn("Generating summary…"), console=console) as prog:
        task = prog.add_task("introspect")
        result = _run(run_introspect(
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            curiosity_db=curiosity_db,
            router=router,
            target_date=target_date,
            topic=topic or None,
            save=not no_save and not topic,
        ))
        prog.update(task, completed=True)

    console.print()
    console.print(Panel(
        Markdown(result.summary),
        title=f"[bold]Introspect — {result.target_date}[/]",
    ))

    if result.recommendations:
        console.print("\n[bold]Suggested Reading[/]")
        for rec in result.recommendations:
            console.print(f"  • [cyan][[{rec.page_title}]][/] — {rec.reason}")

    if result.top_interests:
        rising = [i for i in result.top_interests if float(i["weight"]) >= 2.0]
        fading = [i for i in result.top_interests if float(i["weight"]) < 0.5]
        if rising:
            console.print("\n[bold]Rising ▲[/] " +
                "  ".join(f"[indigo]{i['domain']}/{i['tag']}[/]" for i in rising[:5]))
        if fading:
            console.print("[bold]Fading ▼[/] " +
                "  ".join(f"[dim]{i['domain']}/{i['tag']}[/]" for i in fading[:5]))

    if result.saved_to:
        console.print(f"\n[dim]Saved → {result.saved_to}[/]")


# ---------------------------------------------------------------------------
# mymem tags
# ---------------------------------------------------------------------------

@app.command()
def tags() -> None:
    """List all domains and tag frequencies from curiosity history."""
    settings     = _get_settings()
    _, _, _, curiosity_db = _paths(settings)

    from mymem.pipeline.introspect import top_interests
    interests = top_interests(curiosity_db, limit=50)

    if not interests:
        console.print("[dim]No curiosity data yet. Ingest some sources first.[/]")
        return

    table = Table(title="Tag Interests", show_lines=False)
    table.add_column("Domain", style="cyan", width=12)
    table.add_column("Tag",    style="green")
    table.add_column("Weight", justify="right")
    table.add_column("Trend",  width=8)

    for i in interests:
        w     = float(i["weight"])
        trend = "▲ rising" if w >= 2.0 else ("▼ fading" if w < 0.5 else "  stable")
        color = "green" if w >= 2.0 else ("dim" if w < 0.5 else "yellow")
        table.add_row(str(i["domain"]), str(i["tag"]), f"{w:.2f}", f"[{color}]{trend}[/]")

    console.print(table)


# ---------------------------------------------------------------------------
# mymem graph
# ---------------------------------------------------------------------------

def _graph_db_path(settings) -> Path:  # type: ignore[no-untyped-def]
    return Path(settings.paths.db).parent / "graph.db"


@graph_app.command("backfill")
def graph_backfill(
    classify: bool = typer.Option(False, "--classify",
        help="Also run Tier-2 LLM classification (types + aliases)"),
    limit: int = typer.Option(0, "--limit",
        help="Cap Tier-2 candidates per run (0 = no cap)"),
) -> None:
    """Migrate the existing wiki into the entity graph (Tier 1 + optional Tier 2).

    Tier 1 is structural and idempotent — safe to re-run any time as repair.
    """
    settings = _get_settings()
    wiki_dir, *_ = _paths(settings)
    graph_db = _graph_db_path(settings)

    from mymem.graph.backfill import classify_entities, seed_from_wiki
    from mymem.graph.store import init_db

    init_db(graph_db)
    report = _run(seed_from_wiki(graph_db, wiki_dir))

    table = Table(title="Tier-1 Structural Seed", show_lines=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Wiki pages", str(report.pages))
    table.add_row("Page entities", str(report.page_entities))
    table.add_row("Linked mentions", str(report.linked_mentions))
    table.add_row("Broken-link entities (new)", str(report.broken_link_entities))
    table.add_row("Total mentions", str(report.total_mentions))
    console.print(table)

    if classify:
        router = _make_router(settings)
        creport = _run(classify_entities(graph_db, router=router, limit=limit))
        console.print(
            f"[green]Tier-2 classify:[/] {creport.classified}/{creport.candidates} "
            "entities typed (aliases proposed where obvious)"
        )


@pages_app.command("backfill-ids")
def pages_backfill_ids() -> None:
    """Mint a stable id for every wiki page that lacks one (ADR-013).

    Idempotent and resumable — safe to re-run any time.
    """
    settings = _get_settings()
    wiki_dir, *_ = _paths(settings)

    from mymem.wiki.identity import backfill_page_ids

    report = backfill_page_ids(wiki_dir)

    table = Table(title="Page ID Backfill (ADR-013)", show_lines=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Wiki pages", str(report.total_pages))
    table.add_row("IDs minted", str(report.minted))
    table.add_row("Already had id", str(report.already_had))
    console.print(table)


@graph_app.command("stats")
def graph_stats() -> None:
    """Show entity graph health metrics (explosion alarms)."""
    settings = _get_settings()
    graph_db = _graph_db_path(settings)

    if not graph_db.exists():
        console.print("[dim]No graph database yet. Run `mymem graph backfill` first.[/]")
        return

    from mymem.graph.store import stats as graph_store_stats
    s = graph_store_stats(graph_db)

    table = Table(title="Entity Graph", show_lines=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Entities", str(s.total_entities))
    table.add_row("Mentions", str(s.total_mentions))
    table.add_row("Singletons (≤1 page)", str(s.singleton_count))
    rate_color = (
        "red" if s.singleton_rate > 0.8
        else "yellow" if s.singleton_rate > 0.5
        else "green"
    )
    table.add_row("Singleton rate", f"[{rate_color}]{s.singleton_rate:.0%}[/]")
    console.print(table)


# ---------------------------------------------------------------------------
# mymem eval
# ---------------------------------------------------------------------------

@app.command()
def eval(
    wiki: bool      = typer.Option(True,  "--wiki/--no-wiki",        help="Run wiki quality eval"),
    chunks: bool    = typer.Option(True,  "--chunks/--no-chunks",    help="Run chunk size ablation"),
    retrieval: bool = typer.Option(True,  "--retrieval/--no-retrieval", help="Run BM25 retrieval eval"),
    llm_judge: bool = typer.Option(False, "--llm-judge",             help="Enable RAGAS-lite via cloud model"),
    cases: str      = typer.Option("tests/eval_cases/retrieval.yaml", "--cases", help="Path to retrieval test cases YAML"),
) -> None:
    """Run eval suite: wiki quality, chunk ablation, retrieval, and optional LLM-judge."""
    import asyncio as _asyncio
    from mymem.evals.runner import EvalConfig, run_evals
    from mymem.evals.report import print_report

    settings = _get_settings()
    settings.ensure_dirs()
    wiki_dir, _, _, _ = _paths(settings)
    data_dir = Path("data")

    router = _make_router(settings) if llm_judge else None

    cases_path = Path(cases)
    if not cases_path.suffix == ".yaml":
        err.print(f"[red]Error:[/red] --cases must point to a .yaml file, got: {cases_path.name}")
        raise typer.Exit(1)
    if cases_path.is_absolute() and not str(cases_path).startswith(str(Path.cwd())):
        err.print("[red]Error:[/red] --cases path must be within the project directory")
        raise typer.Exit(1)

    cfg = EvalConfig(
        wiki_dir=wiki_dir,
        data_dir=data_dir,
        cases_path=cases_path,
        run_chunks=chunks,
        run_wiki=wiki,
        run_retrieval=retrieval,
        run_llm_judge=llm_judge,
        router=router,
    )

    with Progress(TextColumn("[cyan]>[/cyan]"), TextColumn("Running evals..."), console=console) as prog:
        t = prog.add_task("eval")
        report = _run(run_evals(cfg))
        prog.update(t, completed=True)

    print_report(report)


# ---------------------------------------------------------------------------
# mymem eval review
# ---------------------------------------------------------------------------

@app.command("eval-review")
def eval_review(
    limit: int  = typer.Option(20, "--limit", "-n", help="Number of runs to show"),
    fail_only: bool = typer.Option(False, "--fail-only", help="Show only FAIL grade runs"),
) -> None:
    """Review extraction consensus results — worst scoring ingests shown first."""
    from mymem.evals.store import recent_consensus_runs
    from rich.table import Table

    db = Path("data/evals.db")
    runs = recent_consensus_runs(db, limit=limit, order="worst_first")

    if not runs:
        console.print("[dim]No extraction consensus runs found. Ingest a source to generate results.[/dim]")
        return

    if fail_only:
        runs = [r for r in runs if r["grade"] == "FAIL"]
        if not runs:
            console.print("[green]No FAIL runs found.[/green]")
            return

    table = Table(title="Extraction Consensus Review (worst first)", show_lines=True)
    table.add_column("Source",    style="cyan",  max_width=28)
    table.add_column("Type",      style="dim",   width=9)
    table.add_column("Score",     justify="right", width=6)
    table.add_column("Grade",     width=6)
    table.add_column("Thesis",    width=7)
    table.add_column("Evidence",  justify="right", width=9)
    table.add_column("Dups",      justify="right", width=6)
    table.add_column("Gaps (reference found, pipeline missed)", style="yellow")

    grade_color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}

    for r in runs:
        grade = r["grade"]
        color = grade_color.get(grade, "white")
        gaps_text = ", ".join(r["gaps"]) if r["gaps"] else "[dim]none[/dim]"
        thesis_icon = "✓" if r["thesis_captured"] else "[red]✗[/red]"
        ev_rate = r.get("evidence_support_rate", 0.0)
        dup_rate = r.get("duplicate_rate", 0.0)
        ev_color = "green" if ev_rate >= 0.80 else "yellow" if ev_rate >= 0.50 else "red"
        dup_color = "green" if dup_rate < 0.10 else "yellow" if dup_rate < 0.25 else "red"
        table.add_row(
            r["source_id"],
            r["source_type"],
            f"{r['consensus_score']:.2f}",
            f"[{color}]{grade}[/]",
            thesis_icon,
            f"[{ev_color}]{ev_rate:.0%}[/]",
            f"[{dup_color}]{dup_rate:.0%}[/]",
            gaps_text,
        )

    console.print(table)
    console.print(
        f"\n[dim]Showing {len(runs)} runs. "
        "Evidence = fraction of ideas with source quotes. "
        "Dups = near-duplicate idea pair rate.[/dim]"
    )


# ---------------------------------------------------------------------------
# mymem serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    port: int  = typer.Option(7860, "--port", "-p", help="Port to listen on"),
    host: str  = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser on start"),
    dev: bool  = typer.Option(False, "--dev", help="Dev mode: skip serving frontend/dist, enable uvicorn reload"),
) -> None:
    """Start the web UI."""
    try:
        import uvicorn
    except ImportError:
        err.print("uvicorn not installed. Run: pip install uvicorn")
        raise typer.Exit(1)

    if dev:
        import os
        os.environ["MYMEM_DEV"] = "1"
        console.print(
            "[bold green]MyMem[/] API → [bold]http://{host}:{port}[/bold]  "
            "[dim](dev mode — open http://localhost:5173 after running npm run dev in frontend/)[/dim]"
            .format(host=host, port=port)
        )
    else:
        url = f"http://{host}:{port}"
        console.print(f"[bold green]MyMem[/] web UI → [link={url}]{url}[/link]")
        if open_browser:
            import threading, webbrowser
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "mymem.web.app:app",
        host=host,
        port=port,
        reload=dev,
        log_level="info" if dev else "warning",
    )


# ---------------------------------------------------------------------------
# mymem obsidian
# ---------------------------------------------------------------------------

@obsidian_app.command("setup")
def obsidian_setup(
    vault_path: Optional[Path] = typer.Option(
        None, "--vault-path", "-v",
        help="Create a directory junction/symlink at this path pointing to wiki/. "
             "Omit to just print the wiki folder path for manual setup.",
    ),
) -> None:
    """Link wiki/ as an Obsidian vault (or print the path to open manually)."""
    settings = _get_settings()
    wiki_dir  = Path(settings.paths.wiki).resolve()

    if not wiki_dir.exists():
        err.print(f"wiki/ directory not found at {wiki_dir}. Run [bold]mymem ingest[/] first.")
        raise typer.Exit(1)

    page_count = len(list(wiki_dir.glob("*.md")))

    if vault_path is None:
        console.print(Panel(
            f"[bold]Wiki path:[/bold] [cyan]{wiki_dir}[/cyan]\n\n"
            "Open Obsidian -> [bold]Open folder as vault[/bold] -> select the path above.\n\n"
            f"[dim]{page_count} markdown pages - YAML frontmatter + wikilinks natively supported.[/dim]\n\n"
            "Or create a vault link:\n"
            "  [bold]mymem obsidian setup --vault-path PATH[/bold]",
            title="Obsidian Setup",
        ))
        return

    vault_path = vault_path.resolve()
    if vault_path.exists():
        err.print(f"Target already exists: {vault_path}")
        raise typer.Exit(1)

    import platform
    import subprocess

    if platform.system() == "Windows":
        # Directory junction requires no admin on Windows (unlike symlinks)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(vault_path), str(wiki_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err.print(f"Failed to create junction: {result.stderr.strip()}")
            raise typer.Exit(1)
    else:
        vault_path.symlink_to(wiki_dir)

    console.print(Panel(
        f"[green]Done.[/] Vault link created:\n"
        f"  [cyan]{vault_path}[/] -> {wiki_dir}\n\n"
        "Open Obsidian -> [bold]Open folder as vault[/bold] -> select:\n"
        f"  [cyan]{vault_path}[/cyan]",
        title="[bold green]Obsidian Setup Complete[/]",
    ))


@obsidian_app.command("status")
def obsidian_status() -> None:
    """Show wiki compatibility info for Obsidian."""
    settings = _get_settings()
    wiki_dir  = Path(settings.paths.wiki).resolve()

    if not wiki_dir.exists():
        err.print(f"wiki/ directory not found at {wiki_dir}.")
        raise typer.Exit(1)

    pages = list(wiki_dir.glob("*.md"))
    pages_with_fm = 0
    pages_with_wikilinks = 0
    for p in pages:
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            if content.startswith("---"):
                pages_with_fm += 1
            if "[[" in content:
                pages_with_wikilinks += 1
        except OSError:
            pass

    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_row("[dim]Wiki path[/]",        str(wiki_dir))
    t.add_row("[dim]Total pages[/]",      str(len(pages)))
    t.add_row("[dim]With frontmatter[/]", f"[green]{pages_with_fm}[/] / {len(pages)}")
    t.add_row("[dim]With wikilinks[/]",   f"[cyan]{pages_with_wikilinks}[/] / {len(pages)}")
    t.add_row("[dim]Obsidian-ready[/]",   "[green]yes[/] YAML frontmatter + wikilinks natively supported")
    console.print(Panel(t, title="Obsidian Status"))

    console.print(
        f"\n[bold]To open in Obsidian:[/bold]\n"
        f"  Obsidian -> Open folder as vault -> [cyan]{wiki_dir}[/cyan]\n\n"
        "[dim]Or: [bold]mymem obsidian setup --vault-path PATH[/bold] to create a junction[/dim]"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
