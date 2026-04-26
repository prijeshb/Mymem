"""Session-scoped LLM cost tracker."""
from __future__ import annotations

from mymem.pipeline.router._types import ICostTracker, IModelRegistry


class SessionCostTracker(ICostTracker):
    def __init__(self, alert_usd: float = 1.0) -> None:
        self._total = 0.0
        self._alert = alert_usd

    def record(
        self,
        model: str,
        in_tokens: int,
        out_tokens: int,
        registry: IModelRegistry,
    ) -> None:
        spec = registry.get(model)
        if spec is None:
            return
        cost = (
            in_tokens  / 1_000_000 * spec.cost_per_1m_input +
            out_tokens / 1_000_000 * spec.cost_per_1m_output
        )
        self._total += cost

    def session_total(self) -> float:
        return self._total

    def alert_threshold(self) -> float:
        return self._alert
