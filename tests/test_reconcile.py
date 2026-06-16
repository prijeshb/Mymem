"""
Tests for mymem/pipeline/reconcile.py — the ADD/MERGE/SUPERSEDE/NOOP decision core
(ADR-011 / ADR-015 Phase 3a).

Pure decision logic + claims-store apply. The LLM is injected via a ModelRouter built
on a fake llm_fn — no real network, no Ollama.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mymem.knowledge.claims import (
    ClaimsStats,
    add_claim,
    get_claim,
    init_db,
    stats,
)
from mymem.pipeline.reconcile import (
    Candidate,
    Decision,
    Proposition,
    ReconcileResult,
    apply_decision,
    build_decision_prompt,
    parse_decision,
    reconcile,
)
from mymem.pipeline.router import ModelRouter

PAGE = "01HPAGE0000000000000000001"


def _router(response: str) -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return response

    return ModelRouter(llm_fn=fake_llm)


CANDS = [
    Candidate(claim_id=1, text="Attention was introduced in 2017.", confidence=1.0),
    Candidate(claim_id=2, text="Self-attention scales quadratically.", confidence=0.8),
]


# ---------------------------------------------------------------------------
# build_decision_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_includes_proposition_and_candidates(self) -> None:
        prop = Proposition(text="Attention dates to 2014.", page_id=PAGE)
        prompt = build_decision_prompt(prop, CANDS)
        assert "Attention dates to 2014." in prompt
        assert "Attention was introduced in 2017." in prompt
        assert "1" in prompt and "2" in prompt  # candidate ids surfaced

    def test_lists_all_four_decisions(self) -> None:
        prompt = build_decision_prompt(Proposition(text="x", page_id=PAGE), CANDS)
        for word in ("ADD", "MERGE", "SUPERSEDE", "NOOP"):
            assert word in prompt


# ---------------------------------------------------------------------------
# parse_decision — robust, never raises, defaults to ADD
# ---------------------------------------------------------------------------

class TestParseDecision:
    def test_parses_clean_json(self) -> None:
        raw = json.dumps({"decision": "MERGE", "target_claim_id": 2, "reason": "same idea"})
        out = parse_decision(raw, CANDS)
        assert out == ReconcileResult(Decision.MERGE, target_claim_id=2, reason="same idea")

    def test_parses_json_embedded_in_prose(self) -> None:
        raw = 'Sure!\n```json\n{"decision":"NOOP","target_claim_id":1}\n```\nDone.'
        out = parse_decision(raw, CANDS)
        assert out.decision is Decision.NOOP
        assert out.target_claim_id == 1

    def test_case_insensitive_decision(self) -> None:
        out = parse_decision('{"decision":"supersede","target_claim_id":1}', CANDS)
        assert out.decision is Decision.SUPERSEDE

    def test_add_forces_target_none(self) -> None:
        out = parse_decision('{"decision":"ADD","target_claim_id":2}', CANDS)
        assert out.decision is Decision.ADD
        assert out.target_claim_id is None

    def test_unknown_decision_defaults_to_add(self) -> None:
        out = parse_decision('{"decision":"FROBNICATE","target_claim_id":1}', CANDS)
        assert out.decision is Decision.ADD

    def test_garbage_defaults_to_add(self) -> None:
        assert parse_decision("not json at all", CANDS).decision is Decision.ADD

    def test_invalid_json_in_braces_defaults_to_add(self) -> None:
        assert parse_decision("{not: valid, json}", CANDS).decision is Decision.ADD

    def test_non_string_decision_defaults_to_add(self) -> None:
        out = parse_decision('{"decision": 5, "target_claim_id": 1}', CANDS)
        assert out.decision is Decision.ADD

    def test_non_add_without_target_falls_back_to_add(self) -> None:
        # MERGE/SUPERSEDE/NOOP need a target; missing → can't act → ADD.
        out = parse_decision('{"decision":"MERGE"}', CANDS)
        assert out.decision is Decision.ADD

    def test_target_not_in_candidates_falls_back_to_add(self) -> None:
        out = parse_decision('{"decision":"MERGE","target_claim_id":999}', CANDS)
        assert out.decision is Decision.ADD


# ---------------------------------------------------------------------------
# reconcile — orchestration (LLM injected)
# ---------------------------------------------------------------------------

class TestReconcile:
    @pytest.mark.asyncio
    async def test_no_candidates_short_circuits_to_add_without_llm(self) -> None:
        called = False

        async def boom(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            nonlocal called
            called = True
            return "{}"

        router = ModelRouter(llm_fn=boom)
        out = await reconcile(Proposition(text="x", page_id=PAGE), [], router=router)
        assert out.decision is Decision.ADD
        assert called is False  # never hits the LLM when there is nothing to compare

    @pytest.mark.asyncio
    async def test_routes_llm_decision(self) -> None:
        router = _router('{"decision":"SUPERSEDE","target_claim_id":1,"reason":"newer date"}')
        prop = Proposition(text="Attention dates to 2014.", page_id=PAGE)
        out = await reconcile(prop, CANDS, router=router)
        assert out.decision is Decision.SUPERSEDE
        assert out.target_claim_id == 1


# ---------------------------------------------------------------------------
# apply_decision — claims-store side effects (real sqlite, no LLM)
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    return p


def _prop(text: str = "A new proposition.") -> Proposition:
    return Proposition(text=text, page_id=PAGE, source_span="quote")


class TestApplyDecision:
    def test_add_inserts_new_claim(self, db: Path) -> None:
        out = apply_decision(
            db, ReconcileResult(Decision.ADD), _prop(), source_id="raw/a.md"
        )
        assert out.text == "A new proposition."
        assert out.source_span == "quote"
        assert out.valid_to is None
        assert stats(db).total == 1

    def test_noop_corroborates_target(self, db: Path) -> None:
        existing = add_claim(db, page_id=PAGE, text="known", source_id="raw/old.md", confidence=0.5)
        out = apply_decision(
            db,
            ReconcileResult(Decision.NOOP, target_claim_id=existing.id),
            _prop(),
            source_id="raw/a.md",
        )
        assert out.id == existing.id
        assert out.confidence == pytest.approx(0.6)  # bumped, no new claim
        assert stats(db).total == 1

    def test_merge_bumps_confidence_no_new_claim(self, db: Path) -> None:
        existing = add_claim(db, page_id=PAGE, text="known", source_id="raw/old.md", confidence=0.5)
        out = apply_decision(
            db,
            ReconcileResult(Decision.MERGE, target_claim_id=existing.id),
            _prop(),
            source_id="raw/a.md",
        )
        assert out.id == existing.id
        assert out.confidence > 0.5
        assert stats(db).total == 1

    def test_supersede_retires_old_adds_new(self, db: Path) -> None:
        old = add_claim(db, page_id=PAGE, text="Attention from 2017.", source_id="raw/old.md")
        out = apply_decision(
            db,
            ReconcileResult(Decision.SUPERSEDE, target_claim_id=old.id),
            _prop("Attention from 2014."),
            source_id="raw/a.md",
        )
        assert out.text == "Attention from 2014."
        assert out.valid_to is None                  # the new claim is active
        retired = get_claim(db, old.id)
        assert retired is not None
        assert retired.valid_to is not None          # old claim retired
        assert retired.superseded_by == out.id
        assert stats(db) == ClaimsStats(total=2, active=1, superseded=1)

    def test_non_add_without_target_raises(self, db: Path) -> None:
        # Defensive: parse_decision should prevent this, but apply must not silently no-op.
        result = ReconcileResult(Decision.MERGE, target_claim_id=None)
        with pytest.raises(ValueError):
            apply_decision(db, result, _prop(), source_id="raw/a.md")
