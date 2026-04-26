"""Pure utility functions for token estimation and cost calculation."""
from __future__ import annotations

from mymem.pipeline.router._types import IModelRegistry
from mymem.pipeline.router._registry import _default_registry


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (BPE average)."""
    return max(1, len(text) // 4)


def fits_context(
    text: str,
    model: str,
    registry: IModelRegistry = _default_registry,
    reserve_output: int = 2048,
) -> bool:
    """True if text fits in the model's context window with room for output."""
    spec = registry.get(model)
    if spec is None:
        return True  # unknown model — assume it fits, fail at runtime
    return estimate_tokens(text) + reserve_output <= spec.context_tokens


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    registry: IModelRegistry = _default_registry,
) -> float:
    spec = registry.get(model)
    if spec is None:
        return 0.0
    return (
        input_tokens  / 1_000_000 * spec.cost_per_1m_input +
        output_tokens / 1_000_000 * spec.cost_per_1m_output
    )
