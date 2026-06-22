"""
Ingest background tasks (split out of ingest.py).

Fire-and-forget work scheduled after an ingest returns — never blocks or fails the ingest:
  _graph_extract_background          — entity extraction + mention recording (data/graph.db)
  _eval_extraction_background        — multi-LLM extraction consensus eval
  _eval_decision_agreement_background — compounding ship-gate eval (ADR-015 D15-D17)
  _build_reference_llm               — shared reference/judge LLM factory for the evals
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mymem.observability.logger import get_logger

if TYPE_CHECKING:
    from mymem.pipeline.compounding import AppliedDecision
    from mymem.pipeline.router import ModelRouter

log = get_logger(__name__)


async def _graph_extract_background(
    *,
    source_name: str,
    source_text: str,
    page_ids: list[str],
    router: ModelRouter,
    db_path: Path,
) -> None:
    """
    Fire-and-forget: extract entities from the source, resolve against the
    entity catalog, and record mentions on the pages this ingest touched.
    Mentions are anchored on each page's stable id (ADR-013/014).
    Never raises — graph failures must not affect ingest.
    """
    try:
        from mymem.graph.extractor import extract_entities
        from mymem.graph.resolver import resolve_entities
        from mymem.graph.store import add_mention, init_db, upsert_entity

        graph_db = db_path.parent / "graph.db"
        init_db(graph_db)

        extracted = await extract_entities(source_text, router=router)
        if not extracted:
            return

        resolutions = await resolve_entities(graph_db, extracted, router=router)

        for entity, resolution in zip(extracted, resolutions, strict=True):
            entity_id = resolution.entity_id
            if entity_id is None:
                entity_id = upsert_entity(
                    graph_db,
                    entity.name,
                    entity_type=entity.type,
                    description=entity.description,
                ).id
            for page_id in page_ids:
                add_mention(
                    graph_db, entity_id, page_id, span=entity.span, source_id=source_name
                )

        log.info(
            "Graph extraction complete",
            source=source_name, entities=len(extracted), pages=len(page_ids),
        )
    except Exception as exc:
        log.warning(
            "Graph extraction failed (background)", source=source_name, error=str(exc)
        )


def _build_reference_llm() -> tuple[str, Callable[..., Awaitable[str]]] | None:
    """(reference_model, llm_fn) for background evals per `eval_reference_provider`, or None
    when no API key is configured. Shared by the extraction-consensus and decision-agreement
    evals so the provider/key plumbing lives in one place. Isolated for patching in tests."""
    from mymem.config import get_settings
    from mymem.evals.extraction_consensus import (
        GROQ_DEFAULT_MODEL,
        NVIDIA_DEFAULT_MODEL,
        OPENROUTER_DEFAULT_MODEL,
    )
    from mymem.pipeline.llm import complete

    settings = get_settings()
    provider = settings.eval_reference_provider
    keys_models = {
        "groq": (settings.groq_api_key, GROQ_DEFAULT_MODEL),
        "gemini": (settings.gemini_api_key, "gemini-2.0-flash"),
        "nvidia": (settings.nvidia_api_key, NVIDIA_DEFAULT_MODEL),
        "openrouter": (settings.openrouter_api_key, OPENROUTER_DEFAULT_MODEL),
    }
    api_key, ref_model = keys_models.get(provider, (None, ""))
    if not api_key:
        log.debug("Background eval skipped — no API key for reference provider", provider=provider)
        return None

    async def _llm_fn(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return await complete(
            prompt,
            model=model,
            provider=provider,
            system=system,
            max_tokens=max_tokens,
            groq_api_key=api_key if provider == "groq" else "",
            gemini_api_key=api_key if provider == "gemini" else "",
            nvidia_api_key=api_key if provider == "nvidia" else "",
            openrouter_api_key=api_key if provider == "openrouter" else "",
        )

    return ref_model, _llm_fn


async def _eval_extraction_background(
    *,
    source_name: str,
    source_type: str,
    source_text: str,
    pipeline_ideas: list[dict[str, object]],
    router: ModelRouter,
    db_path: Path,
) -> None:
    """
    Fire-and-forget background task: run reference LLM extraction and score consensus.
    Skips silently if no API key is configured for the reference provider.
    Never raises — all errors are logged and swallowed.
    """
    try:
        from mymem.evals.extraction_consensus import run_extraction_consensus
        from mymem.evals.store import save_extraction_consensus

        ref = _build_reference_llm()
        if ref is None:
            return
        ref_model, _llm_fn = ref

        pipeline_model = (
            router.task_router.model_for("compile")
            if hasattr(router, "task_router") else "unknown"
        )

        result = await run_extraction_consensus(
            source_id=source_name,
            source_type=source_type,
            source_text=source_text,
            pipeline_ideas=[dict(i) for i in pipeline_ideas],
            pipeline_model=pipeline_model,
            reference_model=ref_model,
            llm_fn=_llm_fn,
        )

        evals_db = db_path.parent / "evals.db"
        save_extraction_consensus(evals_db, result)
        log.info(
            "Extraction consensus eval complete",
            source=source_name,
            grade=result.grade,
            consensus_score=result.consensus_score,
            gaps=list(result.gaps),
        )
    except Exception as exc:
        log.warning(
            "Extraction consensus eval failed (background)",
            source=source_name,
            error=str(exc),
        )


async def _eval_decision_agreement_background(
    *,
    source_name: str,
    applied: list[AppliedDecision],
    router: ModelRouter,
    db_path: Path,
) -> None:
    """Fire-and-forget: re-judge this ingest's reconcile decisions with a held-out LLM and
    record the agreement (ship gate, ADR-015 D15-D17). Skips when there's nothing to judge
    or no reference key. Never raises — eval failures must not affect ingest.
    """
    try:
        from mymem.evals.decision_agreement import cases_from_applied, run_decision_agreement
        from mymem.evals.store import save_run

        cases = cases_from_applied(applied)
        if not cases:
            return  # only trivial ADDs — no judgement to score
        ref = _build_reference_llm()
        if ref is None:
            return
        judge_model, judge_llm = ref

        pipeline_model = (
            router.task_router.model_for("reconcile")
            if hasattr(router, "task_router") else "unknown"
        )
        result = await run_decision_agreement(
            cases, pipeline_model=pipeline_model, judge_model=judge_model, llm_fn=judge_llm
        )

        evals_db = db_path.parent / "evals.db"
        save_run(
            evals_db,
            "decision_agreement",
            summary={
                "agreement_rate": result.agreement_rate,
                "target_agreement_rate": result.target_agreement_rate,
                "grade": result.grade,
                "n_cases": len(cases),
                "pipeline_model": pipeline_model,
                "judge_model": judge_model,
            },
            details={"comparisons": [dataclasses.asdict(c) for c in result.comparisons]},
        )
        log.info(
            "Decision-agreement eval complete",
            source=source_name,
            grade=result.grade,
            agreement_rate=result.agreement_rate,
            cases=len(cases),
        )
    except Exception as exc:
        log.warning(
            "Decision-agreement eval failed (background)",
            source=source_name,
            error=str(exc),
        )
