"""
Rich console report for eval results.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from mymem.evals.runner import EvalReport

console = Console()

_GRADE_STYLE = {"PASS": "green", "WARN": "yellow", "FAIL": "red", "OPTIMAL": "green bold",
                "GOOD": "green", "OK": "cyan", "HIGH_DUPLICATION": "red", "POOR_QUALITY": "red"}


def _g(grade: str) -> str:
    style = _GRADE_STYLE.get(grade, "white")
    return f"[{style}]{grade}[/{style}]"


def print_report(report: EvalReport) -> None:
    console.print()
    console.rule("[bold cyan]MyMem Eval Report[/bold cyan]")

    # --- Wiki Quality ---
    if report.wiki_quality:
        wq = report.wiki_quality
        t = Table(title="Wiki Quality", box=box.SIMPLE_HEAVY, show_lines=False)
        t.add_column("Metric", style="dim")
        t.add_column("Value", justify="right")
        t.add_column("Status", justify="center")
        t.add_row("Total pages", str(wq.total_pages), "")
        t.add_row("Mean richness (0-10)", f"{wq.mean_richness:.2f}",
                  _g("PASS") if wq.mean_richness >= 5 else _g("WARN") if wq.mean_richness >= 3 else _g("FAIL"))
        t.add_row("Median richness", f"{wq.median_richness:.2f}", "")
        t.add_row("Stubs (< 300 chars)", f"{wq.stub_count} ({wq.stub_rate:.0%})",
                  _g("PASS") if wq.stub_rate < 0.1 else _g("WARN") if wq.stub_rate < 0.3 else _g("FAIL"))
        t.add_row("No wikilinks", f"{wq.no_wikilinks_count} ({wq.no_wikilinks_rate:.0%})",
                  _g("PASS") if wq.no_wikilinks_rate < 0.2 else _g("WARN"))
        t.add_row("No tags", str(wq.no_tags_count), "")
        console.print(t)

        if report.confidence_summary:
            ct = Table(title="Lifecycle States", box=box.SIMPLE, show_header=False)
            ct.add_column("State", style="dim")
            ct.add_column("Count", justify="right")
            for state, count in sorted(report.confidence_summary.items()):
                ct.add_row(state, str(count))
            console.print(ct)

        # Bottom 5 weakest pages
        weak = [p for p in wq.pages if p.richness_score < 3][:5]
        if weak:
            wt = Table(title="Weakest Pages", box=box.SIMPLE, show_lines=False)
            wt.add_column("Slug", style="dim", no_wrap=True)
            wt.add_column("Richness", justify="right")
            wt.add_column("Chars", justify="right")
            wt.add_column("Links", justify="right")
            for p in weak:
                wt.add_row(p.slug[:40], f"{p.richness_score:.1f}", str(p.body_chars), str(p.wikilink_count))
            console.print(wt)

    # --- Chunking Ablation ---
    if report.chunking and report.chunking.ablation:
        t = Table(title="Chunk Size Ablation (sample text)", box=box.SIMPLE_HEAVY)
        t.add_column("max_tokens", justify="right")
        t.add_column("chunks", justify="right")
        t.add_column("HOPE score", justify="right")
        t.add_column("grade", justify="center")
        t.add_column("dup rate", justify="right")
        t.add_column("verdict", justify="center")
        for row in report.chunking.ablation:
            t.add_row(
                str(row.max_tokens),
                str(row.chunk_count),
                f"{row.avg_hope:.3f}",
                _g(row.hope_grade),
                f"{row.duplicate_rate:.1%}",
                _g(row.recommendation),
            )
        console.print(t)

        if report.chunking.efficiency_groups:
            eg = Table(title="Quality by Chunk Count (from ingest history)", box=box.SIMPLE)
            eg.add_column("chunk_count", justify="right")
            eg.add_column("n ingests", justify="right")
            eg.add_column("avg concepts", justify="right")
            eg.add_column("avg page chars", justify="right")
            eg.add_column("avg wikilinks", justify="right")
            eg.add_column("dup rate", justify="right")
            for g in report.chunking.efficiency_groups:
                eg.add_row(
                    str(g.chunk_count), str(g.n_ingests),
                    f"{g.avg_concepts:.1f}", f"{g.avg_page_chars:.0f}",
                    f"{g.avg_wikilinks:.1f}", f"{g.avg_duplicate_rate:.1%}",
                )
            console.print(eg)

    # --- Retrieval ---
    if report.retrieval:
        r = report.retrieval
        t = Table(title=f"Retrieval Eval (BM25 @ k={r.k}, {r.mode})", box=box.SIMPLE_HEAVY)
        t.add_column("Metric", style="dim")
        t.add_column("Value", justify="right")
        t.add_column("Status", justify="center")
        t.add_row("Precision@k", f"{r.precision_at_k:.3f}", _g(r.grade))
        t.add_row("MRR", f"{r.mrr:.3f}", "")
        t.add_row("UDCG", f"{r.udcg:.3f}", "")
        t.add_row("Hits / Total", f"{r.hits} / {r.total_cases}", "")
        console.print(t)

        misses = [res for res in r.results if not res.hit]
        if misses:
            mt = Table(title="Missed Cases", box=box.SIMPLE)
            mt.add_column("Query", no_wrap=True, max_width=50)
            mt.add_column("Expected slug", style="dim")
            mt.add_column("Top-1 returned")
            for m in misses[:5]:
                mt.add_row(m.query[:50], m.expected_slug, m.top_k_slugs[0] if m.top_k_slugs else "—")
            console.print(mt)

    # --- Skipped ---
    if report.skipped:
        console.print(Panel(
            "\n".join(f"  [dim]•[/dim] {s}" for s in report.skipped),
            title="[yellow]Skipped[/yellow]", border_style="yellow",
        ))

    console.print()
