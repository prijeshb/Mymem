"""
Reconcile pipeline — the ADD/MERGE/SUPERSEDE/NOOP decision (ADR-011 / ADR-015 Phase 3).

A freshly extracted proposition is compared against the top-k similar *active* claims.
An LLM decides, per proposition, how it relates to existing knowledge:

  ADD       — no equivalent exists        → insert a new claim
  MERGE     — augments an existing claim   → corroborate it (page body enriched by ingest)
  SUPERSEDE — contradicts an existing claim → retire the old claim, insert the new one
  NOOP      — already represented          → corroborate only

The decision logic is pure and the LLM is injected (a ModelRouter) so it mocks cleanly.
`apply_decision` is the only side-effecting function and touches the claims store only —
wiki page writes stay in ingest (single responsibility).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mymem.knowledge.claims import Claim, add_claim, corroborate, supersede_claim
from mymem.observability.logger import get_logger
from mymem.pipeline.router import ModelRouter
from mymem.security.sanitize import sanitize_for_prompt

log = get_logger(__name__)

MERGE_DELTA = 0.1  # confidence bump on MERGE/NOOP corroboration


class Decision(StrEnum):
    ADD = "ADD"
    MERGE = "MERGE"
    SUPERSEDE = "SUPERSEDE"
    NOOP = "NOOP"


@dataclass(frozen=True)
class Proposition:
    """An atomic proposition to reconcile against existing knowledge."""
    text: str
    page_id: str
    source_span: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class Candidate:
    """An existing active claim offered to the decision as a comparison target."""
    claim_id: int
    text: str
    confidence: float = 1.0


@dataclass(frozen=True)
class ReconcileResult:
    decision: Decision
    target_claim_id: int | None = None  # required for MERGE/SUPERSEDE/NOOP
    reason: str = ""


RECONCILE_SYSTEM = (
    "You reconcile a new proposition against existing knowledge claims. "
    "Choose exactly one action and reply with ONLY a JSON object: "
    '{"decision": "ADD|MERGE|SUPERSEDE|NOOP", "target_claim_id": <id or null>, '
    '"reason": "<short>"}. '
    "ADD: no existing claim covers it. MERGE: it adds nuance to a listed claim. "
    "SUPERSEDE: it contradicts/updates a listed claim (the new one is more correct). "
    "NOOP: a listed claim already states the same thing. "
    "target_claim_id must be one of the listed ids for MERGE/SUPERSEDE/NOOP, null for ADD."
)


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------

def build_decision_prompt(prop: Proposition, candidates: list[Candidate]) -> str:
    """Build the decision prompt. Candidate + proposition text are sanitized (defense
    in depth — they already passed the ingest scanner, but this prompt is assembled fresh)."""
    safe_prop, _ = sanitize_for_prompt(prop.text)
    lines = [
        "New proposition:",
        f"  {safe_prop}",
        "",
        "Existing claims (id: text [confidence]):",
    ]
    for c in candidates:
        safe_text, _ = sanitize_for_prompt(c.text)
        lines.append(f"  {c.claim_id}: {safe_text} [{c.confidence:.2f}]")
    lines += [
        "",
        "Decide ADD, MERGE, SUPERSEDE, or NOOP for the new proposition.",
        'Reply with ONLY the JSON object described in the system prompt.',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _coerce_decision(value: object) -> Decision | None:
    if not isinstance(value, str):
        return None
    try:
        return Decision(value.strip().upper())
    except ValueError:
        return None


def parse_decision(raw: str, candidates: list[Candidate]) -> ReconcileResult:
    """Parse an LLM decision robustly. Never raises — anything unparseable, unknown, or
    un-actionable (a non-ADD decision with no valid target) degrades to a safe ADD."""
    match = _JSON_RE.search(raw or "")
    if not match:
        return ReconcileResult(Decision.ADD, reason="unparseable decision")
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ReconcileResult(Decision.ADD, reason="invalid json decision")
    # A brace-matched substring that parses always yields a dict.

    decision = _coerce_decision(obj.get("decision"))
    if decision is None:
        return ReconcileResult(Decision.ADD, reason="unknown decision")
    reason = str(obj.get("reason", ""))

    if decision is Decision.ADD:
        return ReconcileResult(Decision.ADD, reason=reason)

    target = obj.get("target_claim_id")
    valid_ids = {c.claim_id for c in candidates}
    if not isinstance(target, int) or target not in valid_ids:
        return ReconcileResult(Decision.ADD, reason=f"{decision.value} had no valid target")
    return ReconcileResult(decision, target_claim_id=target, reason=reason)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

async def reconcile(
    prop: Proposition,
    candidates: list[Candidate],
    *,
    router: ModelRouter,
    max_tokens: int = 256,
) -> ReconcileResult:
    """Decide how `prop` relates to `candidates`. With no candidates the answer is
    necessarily ADD, so the LLM is skipped entirely (saves a call per novel proposition)."""
    if not candidates:
        return ReconcileResult(Decision.ADD, reason="no existing claims")
    prompt = build_decision_prompt(prop, candidates)
    raw = await router.call(
        prompt, task="reconcile", system=RECONCILE_SYSTEM, max_tokens=max_tokens
    )
    result = parse_decision(raw, candidates)
    log.info(
        "Reconcile decision",
        decision=result.decision.value,
        target=result.target_claim_id,
        candidates=len(candidates),
    )
    return result


# ---------------------------------------------------------------------------
# apply (claims-store side effects only)
# ---------------------------------------------------------------------------

def apply_decision(
    db_path: Path,
    result: ReconcileResult,
    prop: Proposition,
    *,
    source_id: str,
    valid_to: str | None = None,
) -> Claim:
    """Apply a decision to claims.db and return the resulting/affected claim.

    ADD/SUPERSEDE insert the proposition as a new claim; MERGE/NOOP corroborate the
    target. SUPERSEDE additionally retires the old claim (bi-temporal, never deleted).
    """
    if result.decision is Decision.ADD:
        return _insert(db_path, prop, source_id)

    if result.target_claim_id is None:
        raise ValueError(f"{result.decision.value} requires a target_claim_id")

    if result.decision in (Decision.MERGE, Decision.NOOP):
        return corroborate(db_path, result.target_claim_id, delta=MERGE_DELTA)

    # SUPERSEDE: add the new (correct) claim, then retire the contradicted one.
    new = _insert(db_path, prop, source_id)
    supersede_claim(db_path, result.target_claim_id, by=new.id, valid_to=valid_to)
    return new


def _insert(db_path: Path, prop: Proposition, source_id: str) -> Claim:
    return add_claim(
        db_path,
        page_id=prop.page_id,
        text=prop.text,
        source_id=source_id,
        source_span=prop.source_span,
        confidence=prop.confidence,
    )
