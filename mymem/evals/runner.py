"""
Eval runner — orchestrates all eval modules and persists results.

Usage:
    from mymem.evals.runner import run_evals, EvalConfig
    report = await run_evals(EvalConfig(...))
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from mymem.evals import chunking as chunking_mod
from mymem.evals import ingest_quality as iq_mod
from mymem.evals import retrieval as ret_mod
from mymem.evals.chunking import ChunkingReport
from mymem.evals.confidence import score_confidence
from mymem.evals.ingest_quality import WikiQualityReport
from mymem.evals.retrieval import RetrievalReport, load_cases
from mymem.evals.store import save_run
from mymem.observability.logger import get_logger
from mymem.wiki.page import list_pages

log = get_logger(__name__)


@dataclass
class EvalConfig:
    wiki_dir: Path
    data_dir: Path
    cases_path: Path = Path("tests/eval_cases/retrieval.yaml")
    run_chunks: bool = True
    run_wiki: bool = True
    run_retrieval: bool = True
    run_llm_judge: bool = False
    router: object = None           # ModelRouter — required only for llm-judge
    # Sample text for chunk ablation (if None, uses longest wiki page body)
    chunk_sample_text: str | None = None


@dataclass
class EvalReport:
    chunking: ChunkingReport | None = None
    wiki_quality: WikiQualityReport | None = None
    retrieval: RetrievalReport | None = None
    confidence_summary: dict = field(default_factory=dict)
    ragas_results: list = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        out: dict = {}
        if self.wiki_quality:
            out["wiki"] = {
                "total_pages": self.wiki_quality.total_pages,
                "mean_richness": self.wiki_quality.mean_richness,
                "stub_rate": round(self.wiki_quality.stub_rate, 3),
                "no_wikilinks_rate": round(self.wiki_quality.no_wikilinks_rate, 3),
            }
        if self.retrieval:
            out["retrieval"] = {
                "precision_at_k": self.retrieval.precision_at_k,
                "mrr": self.retrieval.mrr,
                "udcg": self.retrieval.udcg,
                "grade": self.retrieval.grade,
            }
        if self.confidence_summary:
            out["confidence"] = self.confidence_summary
        if self.chunking and self.chunking.ablation:
            best = min(
                (r for r in self.chunking.ablation if r.recommendation in ("OPTIMAL", "GOOD")),
                key=lambda r: r.duplicate_rate,
                default=None,
            )
            out["chunking"] = {
                "recommended_max_tokens": best.max_tokens if best else None,
                "current_max_tokens": self.chunking.current_max_tokens,
            }
        if self.skipped:
            out["skipped"] = self.skipped
        return out


async def run_evals(cfg: EvalConfig) -> EvalReport:
    report = EvalReport()
    evals_db = cfg.data_dir / "evals.db"
    analytics_db = cfg.data_dir / "mymem.db"

    # --- Wiki quality ---
    if cfg.run_wiki:
        log.info("Running wiki quality eval")
        try:
            wq = iq_mod.wiki_quality_report(cfg.wiki_dir)
            report.wiki_quality = wq
            # Confidence summary
            pages = list_pages(cfg.wiki_dir)
            states: dict[str, int] = {}
            for page in pages:
                cs = score_confidence(page)
                states[cs.state.value] = states.get(cs.state.value, 0) + 1
            report.confidence_summary = states
            save_run(evals_db, "wiki_quality", {
                "total_pages": wq.total_pages,
                "mean_richness": wq.mean_richness,
                "stub_rate": round(wq.stub_rate, 3),
                "confidence_states": states,
            })
        except Exception as exc:
            log.warning("wiki quality eval failed", error=str(exc))
            report.skipped.append(f"wiki_quality: {exc}")

    # --- Chunking ablation ---
    if cfg.run_chunks:
        log.info("Running chunk size ablation")
        try:
            sample = cfg.chunk_sample_text
            if not sample:
                pages = list_pages(cfg.wiki_dir)
                bodies = [p.body for p in pages if p.body]
                sample = max(bodies, key=len) if bodies else "No wiki pages found."
            ablation = chunking_mod.chunk_size_ablation(sample)
            efficiency = chunking_mod.efficiency_report(analytics_db)
            cr = ChunkingReport(
                ablation=ablation,
                efficiency_groups=efficiency,
                current_max_tokens=6000,  # document current state
                recommended_max_tokens=1024,
            )
            report.chunking = cr
            save_run(evals_db, "chunking", {
                "ablation_rows": len(ablation),
                "recommended_max_tokens": cr.recommended_max_tokens,
            })
        except Exception as exc:
            log.warning("chunking eval failed", error=str(exc))
            report.skipped.append(f"chunking: {exc}")

    # --- Retrieval ---
    if cfg.run_retrieval:
        log.info("Running retrieval eval")
        try:
            cases = load_cases(cfg.cases_path)
            if not cases:
                report.skipped.append("retrieval: no test cases in retrieval.yaml")
            else:
                ret = ret_mod.run_bm25_eval(cases, cfg.wiki_dir)
                report.retrieval = ret
                save_run(evals_db, "retrieval", {
                    "precision_at_k": ret.precision_at_k,
                    "mrr": ret.mrr,
                    "udcg": ret.udcg,
                    "grade": ret.grade,
                })
        except Exception as exc:
            log.warning("retrieval eval failed", error=str(exc))
            report.skipped.append(f"retrieval: {exc}")

    # --- LLM judge (RAGAS-lite) ---
    if cfg.run_llm_judge:
        if cfg.router is None:
            report.skipped.append("ragas: --llm-judge requires a router (run mymem eval --llm-judge)")
        else:
            log.info("LLM-judge eval skipped — no test cases wired yet")
            report.skipped.append("ragas: no Q&A test cases defined yet")

    return report
