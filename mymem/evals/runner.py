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

import random

from mymem.evals import chunking as chunking_mod
from mymem.evals import ingest_quality as iq_mod
from mymem.evals import retrieval as ret_mod
from mymem.evals.chunking import ChunkingReport
from mymem.evals.confidence import score_confidence
from mymem.evals.ingest_quality import WikiQualityReport
from mymem.evals.ragas_lite import RagasResult, run_ragas_eval
from mymem.evals.retrieval import RetrievalReport, generate_self_supervised_cases, load_cases
from mymem.evals.store import save_run
from mymem.observability.logger import get_logger
from mymem.wiki.page import list_pages
from mymem.wiki.types import WikiPage

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
    run_extraction_consensus: bool = False  # requires router
    router: object = None           # ModelRouter — required only for llm-judge + consensus
    ragas_n: int = 5                # pages to sample for RAGAS-lite
    # Sample text for chunk ablation (if None, uses longest wiki page body)
    chunk_sample_text: str | None = None


@dataclass
class EvalReport:
    chunking: ChunkingReport | None = None
    wiki_quality: WikiQualityReport | None = None
    retrieval: RetrievalReport | None = None
    confidence_summary: dict = field(default_factory=dict)
    ragas_results: list = field(default_factory=list)
    extraction_consensus_summary: dict = field(default_factory=dict)
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
        if self.ragas_results:
            scores = [r.overall for r in self.ragas_results if r.overall is not None]
            out["ragas"] = {
                "n_cases": len(self.ragas_results),
                "mean_overall": round(sum(scores) / len(scores), 3) if scores else 0.0,
                "skipped": sum(1 for r in self.ragas_results if r.skipped),
            }
        if self.extraction_consensus_summary:
            out["extraction_consensus"] = self.extraction_consensus_summary
        if self.skipped:
            out["skipped"] = self.skipped
        return out


def _slug_to_question(slug: str) -> str:
    return f"What is {slug.replace('-', ' ')}?"


def _mean_field(results: list[RagasResult], field: str) -> float:
    scores = []
    for r in results:
        sub = getattr(r, field, None)
        if sub and not r.skipped:
            val = getattr(sub, field, None)
            if val is not None:
                scores.append(float(val))
    return round(sum(scores) / len(scores), 3) if scores else 0.0


async def _run_ragas_cases(cfg: EvalConfig) -> list[RagasResult]:
    """
    Sample N wiki pages and evaluate each with RAGAS-lite.

    Self-supervised Q&A:
      question = "What is {slug as phrase}?"
      context  = full page body (capped at 3000 chars)
      answer   = first paragraph of the page body

    This measures whether each wiki page body faithfully and relevantly
    answers its own title question — no hand-curated test cases needed.
    """
    pages: list[WikiPage] = [
        p for p in list_pages(cfg.wiki_dir)
        if not p.archived and len(p.body or "") >= 200
    ]
    rng = random.Random(42)
    sample = rng.sample(pages, min(cfg.ragas_n, len(pages)))

    results: list[RagasResult] = []
    for page in sample:
        body = page.body or ""
        question = _slug_to_question(page.slug)
        context = body[:3000]
        # First non-empty paragraph as the evaluated answer
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        answer = paragraphs[0] if paragraphs else body[:500]

        result = await run_ragas_eval(question, context, answer, cfg.router)
        log.info(
            "RAGAS case",
            slug=page.slug,
            overall=result.overall,
            skipped=result.skipped,
        )
        results.append(result)

    return results


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
                "grade": wq.grade,
            })
        except Exception as exc:
            log.warning("wiki quality eval failed", error=str(exc), exc_info=True)
            report.skipped.append("wiki_quality: eval failed (check logs)")

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
                "grade": cr.grade,
            })
        except Exception as exc:
            log.warning("chunking eval failed", error=str(exc), exc_info=True)
            report.skipped.append("chunking: eval failed (check logs)")

    # --- Retrieval ---
    if cfg.run_retrieval:
        log.info("Running retrieval eval")
        try:
            yaml_cases = load_cases(cfg.cases_path)
            if yaml_cases:
                cases = yaml_cases
                mode = "yaml"
                log.info("Using pinned YAML cases", count=len(cases))
            else:
                pages = list_pages(cfg.wiki_dir)
                cases = generate_self_supervised_cases(pages, n=20)
                mode = "self-supervised"
                log.info("Using self-supervised cases", count=len(cases))

            if not cases:
                report.skipped.append("retrieval: no wiki pages available for self-supervised eval")
            else:
                ret = ret_mod.run_bm25_eval(cases, cfg.wiki_dir, mode=mode)
                report.retrieval = ret
                save_run(evals_db, "retrieval", {
                    "precision_at_k": ret.precision_at_k,
                    "mrr": ret.mrr,
                    "udcg": ret.udcg,
                    "grade": ret.grade,
                    "mode": mode,
                })
        except Exception as exc:
            log.warning("retrieval eval failed", error=str(exc), exc_info=True)
            report.skipped.append("retrieval: eval failed (check logs)")

    # --- Extraction consensus summary (from stored runs) ---
    if cfg.run_extraction_consensus:
        log.info("Loading extraction consensus summary")
        try:
            from mymem.evals.store import recent_consensus_runs
            runs = recent_consensus_runs(evals_db, limit=100)
            if runs:
                grades = [r["grade"] for r in runs]
                pass_rate = round(grades.count("PASS") / len(grades), 3)
                warn_rate = round(grades.count("WARN") / len(grades), 3)
                fail_rate = round(grades.count("FAIL") / len(grades), 3)
                scores = [r["consensus_score"] for r in runs]
                ev_rates = [r.get("evidence_support_rate", 0.0) for r in runs]
                dup_rates = [r.get("duplicate_rate", 0.0) for r in runs]
                report.extraction_consensus_summary = {
                    "n_runs": len(runs),
                    "pass_rate": pass_rate,
                    "warn_rate": warn_rate,
                    "fail_rate": fail_rate,
                    "mean_consensus_score": round(sum(scores) / len(scores), 3),
                    "mean_evidence_support_rate": round(sum(ev_rates) / len(ev_rates), 3),
                    "mean_duplicate_rate": round(sum(dup_rates) / len(dup_rates), 3),
                }
                save_run(evals_db, "extraction_consensus", report.extraction_consensus_summary)
            else:
                report.skipped.append("extraction_consensus: no stored runs yet")
        except Exception as exc:
            log.warning("extraction consensus summary failed", error=str(exc), exc_info=True)
            report.skipped.append("extraction_consensus: summary failed (check logs)")

    # --- LLM judge (RAGAS-lite) ---
    if cfg.run_llm_judge:
        if cfg.router is None:
            report.skipped.append("ragas: --llm-judge requires a router (run mymem eval --llm-judge)")
        else:
            log.info("Running RAGAS-lite eval")
            try:
                ragas_results = await _run_ragas_cases(cfg)
                report.ragas_results = ragas_results
                scores = [r.overall for r in ragas_results if r.overall is not None]
                save_run(evals_db, "ragas", {
                    "n_cases": len(ragas_results),
                    "n_scored": len(scores),
                    "mean_overall": round(sum(scores) / len(scores), 3) if scores else 0.0,
                    "mean_faithfulness": _mean_field(ragas_results, "faithfulness"),
                    "mean_relevancy": _mean_field(ragas_results, "relevancy"),
                })
            except Exception as exc:
                log.warning("RAGAS-lite eval failed", error=str(exc), exc_info=True)
                report.skipped.append("ragas: eval failed (check logs)")

    return report
