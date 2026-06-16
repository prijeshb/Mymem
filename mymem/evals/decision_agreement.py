"""
Decision-agreement eval — the compounding-ingest ship gate (ADR-011 PRD §Success Metrics #1).

A held-out judge LLM independently decides ADD / MERGE / SUPERSEDE / NOOP for the same
propositions (and candidate claims) the pipeline saw. The fraction of decisions whose
*label* matches the pipeline is the merge-precision metric; among agreed non-ADD decisions
we also track whether they targeted the same claim.

The judge runs the exact same task as the live pipeline — it reuses reconcile's prompt
builder, system prompt, and parser — only the model differs. The LLM is injected so the
scoring + run logic is fully testable without a network. Mirrors `extraction_consensus.py`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mymem.observability.logger import get_logger
from mymem.pipeline.reconcile import (
    RECONCILE_SYSTEM,
    Candidate,
    Decision,
    Proposition,
    ReconcileResult,
    build_decision_prompt,
    parse_decision,
)

log = get_logger(__name__)

LLMFn = Callable[..., Awaitable[str]]

PASS_THRESHOLD = 0.80  # decision-label agreement to ship
WARN_THRESHOLD = 0.60


@dataclass(frozen=True)
class DecisionCase:
    """One pipeline decision to be re-judged."""
    proposition: str
    candidates: tuple[Candidate, ...]
    pipeline_decision: Decision
    pipeline_target: int | None = None


@dataclass(frozen=True)
class DecisionComparison:
    proposition: str
    pipeline_decision: Decision
    judge_decision: Decision
    pipeline_target: int | None
    judge_target: int | None
    agree: bool  # decision labels match


@dataclass(frozen=True)
class DecisionAgreementResult:
    comparisons: tuple[DecisionComparison, ...]
    agreement_rate: float        # agreed labels / total
    target_agreement_rate: float  # same target / agreed non-ADD decisions
    pipeline_model: str
    judge_model: str
    grade: str                   # PASS | WARN | FAIL


def _grade(agreement_rate: float, total: int) -> str:
    if total == 0:
        return "WARN"  # nothing to certify
    if agreement_rate >= PASS_THRESHOLD:
        return "PASS"
    if agreement_rate >= WARN_THRESHOLD:
        return "WARN"
    return "FAIL"


def score_decision_agreement(
    cases: list[DecisionCase],
    judge_results: list[ReconcileResult],
    *,
    pipeline_model: str,
    judge_model: str,
) -> DecisionAgreementResult:
    """Pure: compare pipeline decisions against judge decisions. No I/O, no LLM."""
    if len(cases) != len(judge_results):
        raise ValueError(
            f"cases/judge_results length mismatch: {len(cases)} vs {len(judge_results)}"
        )

    comparisons = tuple(
        DecisionComparison(
            proposition=case.proposition,
            pipeline_decision=case.pipeline_decision,
            judge_decision=judge.decision,
            pipeline_target=case.pipeline_target,
            judge_target=judge.target_claim_id,
            agree=case.pipeline_decision == judge.decision,
        )
        for case, judge in zip(cases, judge_results, strict=True)
    )

    total = len(comparisons)
    agreed = sum(1 for c in comparisons if c.agree)
    agreement_rate = round(agreed / total, 3) if total else 0.0

    agreed_nonadd = [
        c for c in comparisons if c.agree and c.pipeline_decision is not Decision.ADD
    ]
    target_hits = sum(1 for c in agreed_nonadd if c.pipeline_target == c.judge_target)
    target_rate = round(target_hits / len(agreed_nonadd), 3) if agreed_nonadd else 0.0

    return DecisionAgreementResult(
        comparisons=comparisons,
        agreement_rate=agreement_rate,
        target_agreement_rate=target_rate,
        pipeline_model=pipeline_model,
        judge_model=judge_model,
        grade=_grade(agreement_rate, total),
    )


async def run_decision_agreement(
    cases: list[DecisionCase],
    *,
    pipeline_model: str,
    judge_model: str,
    llm_fn: LLMFn,
) -> DecisionAgreementResult:
    """Ask the judge LLM to re-decide each case, then score agreement.

    A judge that errors or returns garbage degrades to ADD (via reconcile's parser) — the
    same safe default the live pipeline uses, so a flaky judge can't crash the eval.
    """
    judge_results: list[ReconcileResult] = []
    for case in cases:
        prompt = build_decision_prompt(
            Proposition(text=case.proposition, page_id=""), list(case.candidates)
        )
        try:
            raw = await llm_fn(
                prompt, model=judge_model, system=RECONCILE_SYSTEM, max_tokens=256
            )
            judge_results.append(parse_decision(raw, list(case.candidates)))
        except Exception as exc:
            log.warning("Judge decision failed — scoring as ADD", error=str(exc))
            judge_results.append(ReconcileResult(Decision.ADD, reason="judge failed"))

    return score_decision_agreement(
        cases, judge_results, pipeline_model=pipeline_model, judge_model=judge_model
    )
