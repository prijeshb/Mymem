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

# Model used by default when provider=groq (free tier available)
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Model used by default when provider=nvidia NIM (free credits available)
NVIDIA_DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"

# Model used by default when provider=openrouter (free models available)
# mistralai/mistral-7b-instruct:free is reliably free; fallback: microsoft/phi-3-mini-128k-instruct:free
OPENROUTER_DEFAULT_MODEL = "mistralai/mistral-7b-instruct:free"

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
    consensus_score: float            # matched / max(len(pipeline), len(reference))
    gaps: tuple[str, ...]             # reference titles not matched in pipeline
    false_positives: tuple[str, ...]  # pipeline titles not matched in reference
    thesis_captured: bool             # pipeline captured the reference's main_thesis idea
    grade: str                        # PASS | WARN | FAIL
    evidence_support_rate: float = 0.0   # fraction of pipeline ideas with len(evidence) >= 1
    duplicate_rate: float = 0.0          # fraction of pipeline idea pairs with high ROUGE-1 overlap


# ---------------------------------------------------------------------------
# Reference extractor prompt
# ---------------------------------------------------------------------------

_REFERENCE_SYSTEM = """\
You are an independent knowledge evaluator.
Given a source document, identify the concepts a knowledgeable reader MUST understand.
Be strict: if two concepts overlap significantly, keep only the more distinct one.

Return only a valid JSON array:
[
  {
    "title": "3-8 word searchable concept title",
    "summary": "2-3 sentence explanation grounded only in the source",
    "why_it_matters": "Why this is worth preserving in a personal wiki",
    "evidence": ["short source-grounded quote or paraphrase"],
    "chunk_id": 0,
    "importance": 3,
    "main_thesis": true,
    "tags": ["lowercase"],
    "domain": "tech|research|business|personal|creative|finance|health|spiritual|reminder|misc"
  }
]
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
    """Combine title + summary + why_it_matters into a single string for matching."""
    return " ".join(filter(None, [
        str(idea.get("title", "")),
        str(idea.get("summary", "")),
        str(idea.get("why_it_matters", "")),
    ])).strip()


def _compute_evidence_support_rate(ideas: list[dict]) -> float:
    """Fraction of ideas that have at least one evidence item."""
    if not ideas:
        return 0.0
    supported = sum(
        1 for i in ideas
        if isinstance(i.get("evidence"), list) and len(i.get("evidence", [])) >= 1
    )
    return round(supported / len(ideas), 3)


def _compute_duplicate_rate(ideas: list[dict]) -> float:
    """Fraction of pipeline idea pairs with ROUGE-1 overlap >= 0.6 (proxy for semantic duplicate)."""
    if len(ideas) < 2:
        return 0.0
    from mymem.evals.metrics import rouge1_f1
    texts = [_idea_text(i) for i in ideas]
    duplicate_pairs = 0
    total_pairs = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            total_pairs += 1
            if rouge1_f1(texts[i], texts[j]) >= 0.6:
                duplicate_pairs += 1
    return round(duplicate_pairs / total_pairs, 3) if total_pairs > 0 else 0.0


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
# Semantic matching (async, embedding-cosine)
# ---------------------------------------------------------------------------

EMBED_MATCH_THRESHOLD = 0.78  # cosine similarity threshold for nomic-embed-text 768-dim

EmbedFn = Callable[..., Awaitable[list[list[float]]]]


async def _match_ideas_semantic(
    pipeline_ideas: list[dict],
    reference_ideas: list[dict],
    embed_fn: EmbedFn,
) -> list[IdeaMatch]:
    """Embedding-cosine matching. Falls back to ROUGE-1 if embed_fn raises."""
    if not pipeline_ideas or not reference_ideas:
        return []
    try:
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        p_texts = [_idea_text(i) for i in pipeline_ideas]
        r_texts = [_idea_text(i) for i in reference_ideas]
        p_vecs = await embed_fn(p_texts)
        r_vecs = await embed_fn(r_texts)
        sim_matrix = cosine_similarity(np.array(p_vecs), np.array(r_vecs))
        matches: list[IdeaMatch] = []
        for idx, p_idea in enumerate(pipeline_ideas):
            best_idx = int(sim_matrix[idx].argmax())
            best_score = float(sim_matrix[idx, best_idx])
            matches.append(IdeaMatch(
                pipeline_title=str(p_idea.get("title", "")),
                reference_title=str(reference_ideas[best_idx].get("title", "")),
                rouge1_score=round(best_score, 3),
                matched=best_score >= EMBED_MATCH_THRESHOLD,
            ))
        return matches
    except Exception as exc:
        log.warning("Semantic matching failed — falling back to ROUGE-1", error=str(exc))
        return _match_ideas(pipeline_ideas, reference_ideas)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _grade(consensus_score: float, thesis_captured: bool, evidence_support_rate: float = 1.0) -> str:
    if consensus_score >= 0.67 and thesis_captured and evidence_support_rate >= 0.80:
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

    evidence_support_rate = _compute_evidence_support_rate(pipeline_ideas)
    duplicate_rate = _compute_duplicate_rate(pipeline_ideas)
    grade = _grade(consensus_score, thesis_captured, evidence_support_rate)

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
        evidence_support_rate=evidence_support_rate,
        duplicate_rate=duplicate_rate,
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
    embed_fn: EmbedFn | None = None,
) -> ExtractionConsensusResult:
    """
    Run the reference LLM on the source text, then score consensus.

    Args:
        llm_fn:    Async LLM call — injected so tests can mock it.
                   Signature: async (prompt, *, model, system, max_tokens) -> str
        embed_fn:  Optional async embed call for semantic matching.
                   Signature: async (texts: list[str]) -> list[list[float]]
                   If None, falls back to ROUGE-1 matching.
    """
    source_preview = source_text[:8000]
    prompt = (
        f"Source: {source_id}\nType: {source_type}\n\n"
        f"---\n{source_preview}\n---"
    )

    try:
        raw = await llm_fn(
            prompt,
            model=reference_model,
            system=_REFERENCE_SYSTEM,
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

    # Use semantic matching if embed_fn provided, otherwise ROUGE-1
    if embed_fn is not None and pipeline_ideas and reference_ideas:
        matches = await _match_ideas_semantic(pipeline_ideas, reference_ideas, embed_fn)
    else:
        matches = _match_ideas(pipeline_ideas, reference_ideas)

    matched_count = sum(1 for m in matches if m.matched)
    denom = max(len(pipeline_ideas), len(reference_ideas))
    consensus_score = round(matched_count / denom, 3) if denom > 0 else 0.0
    gaps = tuple(_unmatched_reference_titles(reference_ideas, matches))
    false_positives = tuple(m.pipeline_title for m in matches if not m.matched)

    thesis_ref_texts = [
        _idea_text(r) for r in reference_ideas if r.get("main_thesis") is True
    ]
    thesis_captured = False
    if thesis_ref_texts:
        for p_idea in pipeline_ideas:
            if any(rouge1_f1(_idea_text(p_idea), t) >= MATCH_THRESHOLD for t in thesis_ref_texts):
                thesis_captured = True
                break
    elif reference_ideas:
        thesis_captured = consensus_score >= 0.50

    evidence_support_rate = _compute_evidence_support_rate(pipeline_ideas)
    duplicate_rate = _compute_duplicate_rate(pipeline_ideas)
    grade = _grade(consensus_score, thesis_captured, evidence_support_rate)

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
        evidence_support_rate=evidence_support_rate,
        duplicate_rate=duplicate_rate,
    )
