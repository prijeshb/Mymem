"""
Extraction quality eval — multi-LLM consensus.

After every ingest, a second independent LLM (Groq llama-3.3-70b by default,
Gemini when configured) re-extracts ideas from the same source text.
The two idea sets are compared by ROUGE-1 summary overlap.

Agreement between two different models = high-confidence idea.
Disagreement surfaces gaps and false positives for human review.

Flow:
    pipeline_ideas (already extracted)
        │
        └── reference LLM re-extracts → reference_ideas
                │
            _match_ideas() — ROUGE-1 pairwise matching
                │
            score_consensus() → ExtractionConsensusResult
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Awaitable

from mymem.evals.metrics import rouge1_f1
from mymem.observability.logger import get_logger

log = get_logger(__name__)

# Model used by default when provider=groq
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Model used by default when provider=nvidia
NVIDIA_DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"

# Minimum ROUGE-1 F1 for two ideas to be considered the same concept
MATCH_THRESHOLD = 0.20

LLMFn = Callable[..., Awaitable[str]]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdeaMatch:
    pipeline_title: str
    reference_title: str
    rouge1_score: float
    matched: bool


@dataclass(frozen=True)
class ExtractionConsensusResult:
    source_id: str
    source_type: str
    pipeline_model: str
    reference_model: str
    pipeline_ideas: tuple[dict, ...]
    reference_ideas: tuple[dict, ...]
    matches: tuple[IdeaMatch, ...]
    consensus_score: float       # matched / max(len(pipeline), len(reference))
    gaps: tuple[str, ...]        # reference titles not matched in pipeline
    false_positives: tuple[str, ...]  # pipeline titles not matched in reference
    thesis_captured: bool        # pipeline captured the reference's main_thesis idea
    grade: str                   # PASS | WARN | FAIL


# ---------------------------------------------------------------------------
# Reference extractor prompt
# ---------------------------------------------------------------------------

_REFERENCE_SYSTEM = """\
You are an independent knowledge evaluator.
Given a source document, identify the concepts a knowledgeable reader MUST understand.
Be strict: if two concepts overlap significantly, keep only the more distinct one.

For each concept output:
  "title": what someone would search for to find this concept (3-8 words)
  "summary": 2-3 sentences — core insight, key facts, why it matters
  "main_thesis": true if this concept captures the single main point of the source, false otherwise
  "tags": 2-4 lowercase tags
  "domain": one of spiritual|tech|finance|health|reminder|research|personal|creative|business|misc

Output only valid JSON array. Max {max_concepts} ideas.
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_reference_ideas(raw: str) -> list[dict]:
    """Parse LLM JSON output into a list of idea dicts."""
    cleaned = raw.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return [d for d in v if isinstance(d, dict)]
    except (json.JSONDecodeError, ValueError):
        log.debug("_parse_reference_ideas: JSON parse failed", raw_preview=raw[:200])
    return []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _idea_text(idea: dict) -> str:
    """Combine title + summary into a single string for matching."""
    return f"{idea.get('title', '')} {idea.get('summary', '')}".strip()


def _match_ideas(
    pipeline_ideas: list[dict],
    reference_ideas: list[dict],
) -> list[IdeaMatch]:
    """
    For each pipeline idea, find the best-matching reference idea by ROUGE-1 F1.
    Returns one IdeaMatch per pipeline idea.
    """
    if not pipeline_ideas or not reference_ideas:
        return []

    matches: list[IdeaMatch] = []
    for p_idea in pipeline_ideas:
        p_text = _idea_text(p_idea)
        best_score = 0.0
        best_ref_title = ""
        for r_idea in reference_ideas:
            score = rouge1_f1(p_text, _idea_text(r_idea))
            if score > best_score:
                best_score = score
                best_ref_title = str(r_idea.get("title", ""))
        matches.append(IdeaMatch(
            pipeline_title=str(p_idea.get("title", "")),
            reference_title=best_ref_title,
            rouge1_score=round(best_score, 3),
            matched=best_score >= MATCH_THRESHOLD,
        ))
    return matches


def _unmatched_reference_titles(
    reference_ideas: list[dict],
    matches: list[IdeaMatch],
) -> list[str]:
    """Reference idea titles that no pipeline idea matched against."""
    matched_ref_titles = {m.reference_title for m in matches if m.matched}
    return [
        str(r.get("title", ""))
        for r in reference_ideas
        if str(r.get("title", "")) not in matched_ref_titles
    ]


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _grade(consensus_score: float, thesis_captured: bool) -> str:
    if consensus_score >= 0.67 and thesis_captured:
        return "PASS"
    if consensus_score >= 0.50 or thesis_captured:
        return "WARN"
    return "FAIL"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_consensus(
    source_id: str,
    source_type: str,
    pipeline_model: str,
    reference_model: str,
    pipeline_ideas: list[dict],
    reference_ideas: list[dict],
) -> ExtractionConsensusResult:
    """
    Compare pipeline-extracted ideas against reference-extracted ideas.
    Pure function — no I/O, no LLM calls.
    """
    matches = _match_ideas(pipeline_ideas, reference_ideas)
    matched_count = sum(1 for m in matches if m.matched)
    denom = max(len(pipeline_ideas), len(reference_ideas))
    consensus_score = round(matched_count / denom, 3) if denom > 0 else 0.0

    gaps = tuple(_unmatched_reference_titles(reference_ideas, matches))
    false_positives = tuple(m.pipeline_title for m in matches if not m.matched)

    # thesis_captured: any pipeline idea matched a reference idea flagged main_thesis=True
    thesis_ref_texts = [
        _idea_text(r) for r in reference_ideas if r.get("main_thesis") is True
    ]
    thesis_captured = False
    if thesis_ref_texts:
        for p_idea in pipeline_ideas:
            p_text = _idea_text(p_idea)
            if any(rouge1_f1(p_text, t) >= MATCH_THRESHOLD for t in thesis_ref_texts):
                thesis_captured = True
                break
    elif not reference_ideas:
        thesis_captured = False
    else:
        # No reference idea marked main_thesis — use highest-scoring match as proxy
        thesis_captured = consensus_score >= 0.50

    grade = _grade(consensus_score, thesis_captured)

    return ExtractionConsensusResult(
        source_id=source_id,
        source_type=source_type,
        pipeline_model=pipeline_model,
        reference_model=reference_model,
        pipeline_ideas=tuple(pipeline_ideas),
        reference_ideas=tuple(reference_ideas),
        matches=tuple(matches),
        consensus_score=consensus_score,
        gaps=gaps,
        false_positives=false_positives,
        thesis_captured=thesis_captured,
        grade=grade,
    )


# ---------------------------------------------------------------------------
# Reference LLM extraction
# ---------------------------------------------------------------------------

async def run_extraction_consensus(
    source_id: str,
    source_type: str,
    source_text: str,
    pipeline_ideas: list[dict],
    pipeline_model: str,
    reference_model: str,
    llm_fn: LLMFn,
    max_concepts: int = 5,
) -> ExtractionConsensusResult:
    """
    Run the reference LLM on the source text, then score consensus.

    Args:
        llm_fn: Callable matching LLMFn protocol — injected so tests can mock it.
                Signature: async (prompt, *, model, system, max_tokens) -> str
    """
    # Truncate source to avoid token overrun on very long sources
    source_preview = source_text[:6000]

    system = _REFERENCE_SYSTEM.format(max_concepts=max_concepts)
    prompt = (
        f"Source: {source_id}\nType: {source_type}\n\n"
        f"---\n{source_preview}\n---"
    )

    try:
        raw = await llm_fn(
            prompt,
            model=reference_model,
            system=system,
            max_tokens=2048,
        )
        reference_ideas = _parse_reference_ideas(raw)
        if not reference_ideas:
            log.warning(
                "Reference extractor returned no ideas",
                source_id=source_id,
                raw_preview=raw[:200],
            )
    except Exception as exc:
        log.warning(
            "Reference extraction failed — scoring as zero",
            source_id=source_id,
            error=str(exc),
        )
        reference_ideas = []

    return score_consensus(
        source_id=source_id,
        source_type=source_type,
        pipeline_model=pipeline_model,
        reference_model=reference_model,
        pipeline_ideas=pipeline_ideas,
        reference_ideas=reference_ideas,
    )
