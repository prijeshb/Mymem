"""
Tests for mymem/evals/decision_agreement.py — the compounding-ingest ship gate
(ADR-011 PRD §Success Metrics #1 / ADR-015 D15-D16).

A held-out judge LLM re-decides ADD/MERGE/SUPERSEDE/NOOP for the same propositions the
pipeline saw; agreement rate is the merge-precision metric. Judge LLM is injected — no
network. Reuses reconcile's prompt/parse so the judge runs the exact same task.
"""
from __future__ import annotations

import json

import pytest

from mymem.evals.decision_agreement import (
    DecisionAgreementResult,
    DecisionCase,
    run_decision_agreement,
    score_decision_agreement,
)
from mymem.pipeline.reconcile import Candidate, Decision, ReconcileResult

CANDS = (Candidate(claim_id=1, text="Introduced in 2017."),)


def _case(decision: Decision, target: int | None = None, prop: str = "p") -> DecisionCase:
    return DecisionCase(
        proposition=prop,
        candidates=CANDS,
        pipeline_decision=decision,
        pipeline_target=target,
    )


def _judge(decision: Decision, target: int | None = None) -> ReconcileResult:
    return ReconcileResult(decision, target_claim_id=target)


# ---------------------------------------------------------------------------
# score_decision_agreement (pure)
# ---------------------------------------------------------------------------

class TestScore:
    def test_full_agreement_passes(self) -> None:
        cases = [_case(Decision.MERGE, 1), _case(Decision.ADD)]
        judges = [_judge(Decision.MERGE, 1), _judge(Decision.ADD)]
        out = score_decision_agreement(cases, judges, pipeline_model="p", judge_model="j")
        assert out.agreement_rate == 1.0
        assert out.grade == "PASS"

    def test_no_agreement_fails(self) -> None:
        cases = [_case(Decision.MERGE, 1), _case(Decision.NOOP, 1)]
        judges = [_judge(Decision.ADD), _judge(Decision.SUPERSEDE, 1)]
        out = score_decision_agreement(cases, judges, pipeline_model="p", judge_model="j")
        assert out.agreement_rate == 0.0
        assert out.grade == "FAIL"

    def test_partial_agreement_warns(self) -> None:
        cases = [_case(Decision.MERGE, 1), _case(Decision.ADD), _case(Decision.NOOP, 1)]
        judges = [_judge(Decision.MERGE, 1), _judge(Decision.ADD), _judge(Decision.SUPERSEDE, 1)]
        out = score_decision_agreement(cases, judges, pipeline_model="p", judge_model="j")
        assert out.agreement_rate == pytest.approx(0.667, abs=0.001)
        assert out.grade == "WARN"

    def test_empty_is_warn_not_certifiable(self) -> None:
        out = score_decision_agreement([], [], pipeline_model="p", judge_model="j")
        assert out.agreement_rate == 0.0
        assert out.grade == "WARN"
        assert out.comparisons == ()

    def test_target_agreement_tracked_for_agreed_nonadd(self) -> None:
        # Both say MERGE but on different claims → label agrees, target does not.
        cases = [_case(Decision.MERGE, 1)]
        judges = [_judge(Decision.MERGE, 2)]
        out = score_decision_agreement(cases, judges, pipeline_model="p", judge_model="j")
        assert out.agreement_rate == 1.0          # decision label agrees
        assert out.target_agreement_rate == 0.0    # but the targeted claim differs

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            score_decision_agreement([_case(Decision.ADD)], [], pipeline_model="p", judge_model="j")


# ---------------------------------------------------------------------------
# run_decision_agreement (judge LLM injected)
# ---------------------------------------------------------------------------

class TestRun:
    @pytest.mark.asyncio
    async def test_judge_agreeing_scores_pass(self) -> None:
        async def judge_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return json.dumps({"decision": "MERGE", "target_claim_id": 1})

        cases = [_case(Decision.MERGE, 1)]
        out = await run_decision_agreement(
            cases, pipeline_model="p", judge_model="j", llm_fn=judge_llm
        )
        assert isinstance(out, DecisionAgreementResult)
        assert out.agreement_rate == 1.0
        assert out.judge_model == "j"

    @pytest.mark.asyncio
    async def test_judge_failure_falls_back_to_add(self) -> None:
        async def boom(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            raise RuntimeError("judge down")

        # Pipeline said MERGE; judge fails → treated as ADD → disagreement.
        cases = [_case(Decision.MERGE, 1)]
        out = await run_decision_agreement(
            cases, pipeline_model="p", judge_model="j", llm_fn=boom
        )
        assert out.comparisons[0].judge_decision is Decision.ADD
        assert out.agreement_rate == 0.0

    @pytest.mark.asyncio
    async def test_judge_garbage_parses_to_add(self) -> None:
        async def junk(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return "not json"

        cases = [_case(Decision.ADD)]
        out = await run_decision_agreement(
            cases, pipeline_model="p", judge_model="j", llm_fn=junk
        )
        # Pipeline ADD, judge garbage→ADD → they agree.
        assert out.agreement_rate == 1.0
