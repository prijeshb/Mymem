"""ModelRouter: orchestrates task routing, fallback, and cost tracking."""
from __future__ import annotations

import time

from mymem.observability.logger import get_logger
from mymem.pipeline.llm import complete
from mymem.pipeline.router._types import (
    IFallbackChain, ICostTracker, IModelRegistry, ITaskRouter, LLMCallable,
)
from mymem.pipeline.router._chain import OllamaFallbackChain
from mymem.pipeline.router._cost import SessionCostTracker
from mymem.pipeline.router._registry import DefaultModelRegistry
from mymem.pipeline.router._utils import estimate_tokens, fits_context

log = get_logger(__name__)

_CLOUD_DEFAULTS: dict[str, str] = {
    "compile":    "gemma4:31b-cloud",
    "merge":      "gemma4:31b-cloud",
    "qa":         "gemma4:31b-cloud",
    "introspect": "gemma4:31b-cloud",
    "lint":       "gemma4:31b-cloud",
    "classify":   "gemma4:31b-cloud",
    "embed":      "nomic-embed-text",
}


class ConfigTaskRouter(ITaskRouter):
    """Maps task names to model names, with per-task override support."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._models = {**_CLOUD_DEFAULTS, **(overrides or {})}

    def model_for(self, task: str) -> str:
        return self._models.get(task, "gemma3:12b")

    def update(self, task: str, model: str) -> None:
        self._models = {**self._models, task: model}


class ModelRouter:
    """
    Selects the right model for a task, runs it with fallback, and tracks cost.

    All dependencies are injectable — pass custom implementations for testing
    or to extend behaviour without subclassing ModelRouter itself.
    """

    def __init__(
        self,
        task_models: dict[str, str] | None = None,
        provider: str = "ollama",
        anthropic_api_key: str = "",
        openai_api_key: str = "",
        ollama_base_url: str = "http://localhost:11434",
        ollama_timeout: int = 120,
        cost_alert_usd: float = 1.0,
        llm_fn: LLMCallable | None = None,
        # Injectable abstractions
        registry: IModelRegistry | None = None,
        fallback_chain: IFallbackChain | None = None,
        cost_tracker: ICostTracker | None = None,
    ) -> None:
        self._provider       = provider
        self._ant_key        = anthropic_api_key
        self._oai_key        = openai_api_key
        self._ollama_url     = ollama_base_url
        self._ollama_timeout = ollama_timeout
        self._llm_fn         = llm_fn

        self._task_router = ConfigTaskRouter(task_models)
        self._registry    = registry or DefaultModelRegistry()
        self._chain       = fallback_chain or OllamaFallbackChain(anthropic_api_key, openai_api_key)
        self._cost        = cost_tracker or SessionCostTracker(cost_alert_usd)

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def model_for(self, task: str) -> str:
        return self._task_router.model_for(task)

    def spec_for(self, model: str):
        return self._registry.get(model)

    def needs_split(self, text: str, task: str) -> bool:
        return not fits_context(text, self.model_for(task), self._registry)

    @property
    def session_cost(self) -> float:
        return self._cost.session_total()

    # ------------------------------------------------------------------
    # LLM call with fallback
    # ------------------------------------------------------------------

    async def call(
        self,
        prompt: str,
        task: str,
        *,
        system: str = "",
        max_tokens: int = 4096,
        model_override: str | None = None,
    ) -> str:
        if self._llm_fn is not None:
            return await self._llm_fn(
                prompt,
                model=model_override or self.model_for(task),
                system=system,
                max_tokens=max_tokens,
            )

        preferred = model_override or self.model_for(task)
        attempts  = (
            [preferred] if model_override
            else self._chain.build(preferred, self._registry, self._provider)
        )

        last_exc: Exception = RuntimeError("No models available")
        for model in attempts:
            spec     = self._registry.get(model)
            provider = spec.provider if spec else self._provider
            log.info("LLM call", task=task, model=model, provider=provider,
                     prompt_chars=len(prompt))
            t0 = time.monotonic()
            try:
                result = await complete(
                    prompt,
                    model=model,
                    provider=provider,
                    system=system,
                    max_tokens=max_tokens,
                    ollama_base_url=self._ollama_url,
                    ollama_timeout=self._ollama_timeout,
                    anthropic_api_key=self._ant_key,
                    openai_api_key=self._oai_key,
                )
                elapsed = round(time.monotonic() - t0, 2)
                self._cost.record(model, estimate_tokens(prompt), estimate_tokens(result), self._registry)
                if self._cost.session_total() >= self._cost.alert_threshold():
                    log.warning(
                        "Session cost $%.4f reached alert threshold $%.2f",
                        self._cost.session_total(), self._cost.alert_threshold(),
                    )
                log.info("LLM call complete", task=task, model=model, elapsed_s=elapsed,
                         response_chars=len(result),
                         session_cost=f"${self._cost.session_total():.4f}")
                return result
            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 2)
                log.warning("Model failed — trying fallback", exc_info=True,
                            model=model, task=task, elapsed_s=elapsed, error=str(exc))
                last_exc = exc

        log.error("All models exhausted", task=task, tried=str(attempts))
        raise last_exc


def router_from_settings(settings: object, llm_fn: LLMCallable | None = None) -> ModelRouter:
    """Convenience factory — builds a ModelRouter from a Settings instance."""
    from mymem.config import Settings  # type: ignore[attr-defined]
    s: Settings = settings             # type: ignore[assignment]
    task_models: dict[str, str] = {
        "compile": s.models.compile,
        "qa":      s.models.qa,
        "lint":    s.models.lint,
        "embed":   s.models.embed,
    }
    for task in ("classify", "merge", "introspect", "export_slides", "export_charts"):
        if hasattr(s.models, task):
            task_models[task] = getattr(s.models, task)
    return ModelRouter(
        task_models=task_models,
        provider=s.provider,
        anthropic_api_key=s.anthropic_api_key or "",
        openai_api_key=s.openai_api_key or "",
        ollama_base_url=s.ollama.base_url,
        ollama_timeout=s.ollama.timeout_s,
        cost_alert_usd=s.observability.cost_alert_usd,
        llm_fn=llm_fn,
    )
