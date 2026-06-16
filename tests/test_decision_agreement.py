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
    cases_from_applied,
    run_decision_agreement,
    score_decision_agreement,
)
from mymem.pipeline.compounding import AppliedDecision
from mymem.pipeline.reconcile import Candidate, Decision, Proposition, ReconcileResult

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

def _applied(
    decision: Decision, candidates: tuple[Candidate, ...], target: int | None = None
) -> AppliedDecision:
    from mymem.knowledge.claims import Claim

    claim = Claim(
        id=99, page_id="01HPAGE0000000000000000001", text="t", source_id="raw/a.md",
        source_span="", confidence=1.0, valid_from="2026-06-01", valid_to=None,
        superseded_by=None, created="2026-06-01T00:00:00+00:00",
    )
    return AppliedDecision(
        proposition=Proposition(text="prop text", page_id="01HPAGE0000000000000000001"),
        candidates=candidates,
        result=ReconcileResult(decision, target_claim_id=target),
        claim=claim,
    )


# ---------------------------------------------------------------------------
# cases_from_applied — capture decisions worth judging
# ---------------------------------------------------------------------------

class TestCasesFromApplied:
    def test_maps_fields(self) -> None:
        applied = [_applied(Decision.MERGE, CANDS, target=1)]
        cases = cases_from_applied(applied)
        assert len(cases) == 1
        assert cases[0].proposition == "prop text"
        assert cases[0].candidates == CANDS
        assert cases[0].pipeline_decision is Decision.MERGE
        assert cases[0].pipeline_target == 1

    def test_drops_trivial_no_candidate_adds(self) -> None:
        # An ADD with no candidates wasn't an LLM judgement — exclude it from the metric.
        applied = [
            _applied(Decision.ADD, candidates=()),       # trivial, dropped
            _applied(Decision.MERGE, CANDS, target=1),   # real judgement, kept
        ]
        cases = cases_from_applied(applied)
        assert [c.pipeline_decision for c in cases] == [Decision.MERGE]

    def test_keeps_add_when_candidates_were_present(self) -> None:
        # ADD despite candidates IS a judgement (the LLM chose not to merge) — keep it.
        cases = cases_from_applied([_applied(Decision.ADD, CANDS)])
        assert len(cases) == 1


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


# ---------------------------------------------------------------------------
# Background wiring: _eval_decision_agreement_background captures → judges → persists
# ---------------------------------------------------------------------------

class TestBackgroundWiring:
    @pytest.mark.asyncio
    async def test_persists_decision_agreement_run(self, tmp_path, monkeypatch) -> None:
        from mymem.evals.store import latest_runs
        from mymem.pipeline import ingest as ingest_mod
        from mymem.pipeline.router import ModelRouter

        async def judge_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return json.dumps({"decision": "MERGE", "target_claim_id": 1})

        # Patch the shared reference-LLM factory so no API key / network is needed.
        monkeypatch.setattr(
            ingest_mod, "_build_reference_llm", lambda: ("judge-model", judge_llm)
        )

        applied = [_applied(Decision.MERGE, CANDS, target=1)]  # one real judgement
        db_path = tmp_path / "data" / "mymem.db"
        await ingest_mod._eval_decision_agreement_background(
            source_name="raw/a.md",
            applied=applied,
            router=ModelRouter(llm_fn=judge_llm),
            db_path=db_path,
        )

        runs = latest_runs(db_path.parent / "evals.db")
        da = [r for r in runs if r["eval_type"] == "decision_agreement"]
        assert len(da) == 1
        assert da[0]["summary"]["agreement_rate"] == 1.0
        assert da[0]["summary"]["grade"] == "PASS"
        assert da[0]["summary"]["n_cases"] == 1

    @pytest.mark.asyncio
    async def test_skips_when_only_trivial_adds(self, tmp_path, monkeypatch) -> None:
        from mymem.evals.store import latest_runs
        from mymem.pipeline import ingest as ingest_mod
        from mymem.pipeline.router import ModelRouter

        called = False

        def _ref():
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(ingest_mod, "_build_reference_llm", _ref)

        applied = [_applied(Decision.ADD, candidates=())]  # nothing to judge
        db_path = tmp_path / "data" / "mymem.db"
        await ingest_mod._eval_decision_agreement_background(
            source_name="raw/a.md",
            applied=applied,
            router=ModelRouter(llm_fn=lambda *a, **k: ""),  # type: ignore[arg-type]
            db_path=db_path,
        )
        # No cases → returns before even building the reference LLM; nothing persisted.
        assert called is False
        runs = latest_runs(db_path.parent / "evals.db")
        assert [r for r in runs if r["eval_type"] == "decision_agreement"] == []
