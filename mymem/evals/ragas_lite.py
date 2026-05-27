"""
RAGAS-lite — reference-free faithfulness + answer relevancy via LLM judge.

Uses the ModelRouter (Anthropic fallback when Ollama is unavailable).
Skip gracefully if no provider is reachable.

Faithfulness: are all claims in the answer supported by the retrieved context?
Answer relevancy: does the answer actually address the question?

Both are measured by decomposing the answer into atomic claims and asking the
LLM to verify each one — a lightweight version of the RAGAS approach that works
with any model via the existing router.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from mymem.observability.logger import get_logger

log = get_logger(__name__)

_FAITHFULNESS_SYSTEM = """\
You are an evaluator. Given a QUESTION, CONTEXT (source passages), and ANSWER,
decompose the answer into atomic claims and classify each as:
  "supported"    — the context explicitly supports this claim
  "unsupported"  — the claim is not found in the context
  "contradicted" — the context contradicts this claim

Return JSON only:
{"claims": [{"claim": "...", "verdict": "supported|unsupported|contradicted"}]}
"""

_RELEVANCY_SYSTEM = """\
You are an evaluator. Given a QUESTION and ANSWER, rate how well the answer
addresses the question on a scale 0.0-1.0 where:
  1.0 = fully answers the question with no irrelevant content
  0.5 = partially answers but misses key aspects
  0.0 = does not answer the question at all

Return JSON only: {"score": 0.0-1.0, "reason": "one sentence"}
"""


@dataclass
class ClaimVerdict:
    claim: str
    verdict: str  # supported | unsupported | contradicted


@dataclass
class FaithfulnessResult:
    claims: list[ClaimVerdict] = field(default_factory=list)
    faithfulness: float = 0.0   # fraction of supported claims
    hallucination_rate: float = 0.0

    @property
    def grade(self) -> str:
        if self.faithfulness >= 0.75:
            return "PASS"
        if self.faithfulness >= 0.6:
            return "WARN"
        return "FAIL"


@dataclass
class RelevancyResult:
    score: float = 0.0
    reason: str = ""

    @property
    def grade(self) -> str:
        if self.score >= 0.7:
            return "PASS"
        if self.score >= 0.5:
            return "WARN"
        return "FAIL"


@dataclass
class RagasResult:
    faithfulness: FaithfulnessResult | None = None
    relevancy: RelevancyResult | None = None
    skipped: bool = False
    skip_reason: str = ""

    @property
    def overall(self) -> float | None:
        scores = []
        if self.faithfulness:
            scores.append(self.faithfulness.faithfulness)
        if self.relevancy:
            scores.append(self.relevancy.score)
        return round(sum(scores) / len(scores), 3) if scores else None


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return json.loads(cleaned)


async def eval_faithfulness(
    question: str,
    context: str,
    answer: str,
    router,  # ModelRouter
) -> FaithfulnessResult:
    prompt = (
        f"QUESTION: {question}\n\n"
        f"CONTEXT:\n{context[:4000]}\n\n"
        f"ANSWER:\n{answer}"
    )
    try:
        raw = await router.call(prompt, task="lint", system=_FAITHFULNESS_SYSTEM)
        data = _parse_json(raw)
        claims = [
            ClaimVerdict(claim=c["claim"], verdict=c["verdict"])
            for c in data.get("claims", [])
        ]
        if not claims:
            return FaithfulnessResult()
        supported = sum(1 for c in claims if c.verdict == "supported")
        contradicted = sum(1 for c in claims if c.verdict == "contradicted")
        return FaithfulnessResult(
            claims=claims,
            faithfulness=round(supported / len(claims), 3),
            hallucination_rate=round((len(claims) - supported) / len(claims), 3),
        )
    except Exception as exc:
        log.warning("faithfulness eval failed", error=str(exc))
        return FaithfulnessResult()


async def eval_relevancy(
    question: str,
    answer: str,
    router,  # ModelRouter
) -> RelevancyResult:
    prompt = f"QUESTION: {question}\n\nANSWER:\n{answer}"
    try:
        raw = await router.call(prompt, task="lint", system=_RELEVANCY_SYSTEM)
        data = _parse_json(raw)
        return RelevancyResult(
            score=round(float(data.get("score", 0.0)), 3),
            reason=str(data.get("reason", "")),
        )
    except Exception as exc:
        log.warning("relevancy eval failed", error=str(exc))
        return RelevancyResult()


async def run_ragas_eval(
    question: str,
    context: str,
    answer: str,
    router,  # ModelRouter | None
) -> RagasResult:
    if router is None:
        return RagasResult(skipped=True, skip_reason="no router — pass --llm-judge")
    try:
        faithfulness = await eval_faithfulness(question, context, answer, router)
        relevancy = await eval_relevancy(question, answer, router)
        return RagasResult(faithfulness=faithfulness, relevancy=relevancy)
    except Exception as exc:
        return RagasResult(skipped=True, skip_reason=str(exc))
