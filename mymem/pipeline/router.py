"""
Multi-LLM router — model selection, token estimation, and fallback chain.

Never call llm.py directly from pipeline code. Always use ModelRouter so
fallbacks, cost guards, and task-splitting are applied automatically.

Model registry context windows (tokens):
    gemma3:4b   →   8 192
    gemma3:12b  →   8 192
    gemma4:12b  → 131 072  (128k)
    gemma4:27b  → 131 072  (128k)
    claude-haiku-4-5        → 200 000
    claude-sonnet-4-6       → 200 000
    claude-opus-4-6         → 200 000

Fallback chain (when configured model unavailable):
    gemma4:27b → gemma4:12b → gemma3:12b → claude-haiku-4-5 → claude-sonnet-4-6
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

from mymem.observability.logger import get_logger
from mymem.pipeline.llm import complete

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    name:         str
    provider:     str          # ollama | anthropic | openai
    context_tokens: int
    cost_per_1m_input:  float  # USD, 0.0 for local
    cost_per_1m_output: float


_REGISTRY: dict[str, ModelSpec] = {
    "gemma3:4b": ModelSpec(
        name="gemma3:4b", provider="ollama",
        context_tokens=8_192, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "gemma3:12b": ModelSpec(
        name="gemma3:12b", provider="ollama",
        context_tokens=8_192, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "gemma4:12b": ModelSpec(
        name="gemma4:12b", provider="ollama",
        context_tokens=131_072, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "gemma4:27b": ModelSpec(
        name="gemma4:27b", provider="ollama",
        context_tokens=131_072, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "gemma4:31b-cloud": ModelSpec(
        name="gemma4:31b-cloud", provider="ollama",
        context_tokens=131_072, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "gemma4:latest": ModelSpec(
        name="gemma4:latest", provider="ollama",
        context_tokens=131_072, cost_per_1m_input=0.0, cost_per_1m_output=0.0,
    ),
    "claude-haiku-4-5": ModelSpec(
        name="claude-haiku-4-5", provider="anthropic",
        context_tokens=200_000, cost_per_1m_input=0.80, cost_per_1m_output=4.0,
    ),
    "claude-haiku-4-5-20251001": ModelSpec(
        name="claude-haiku-4-5-20251001", provider="anthropic",
        context_tokens=200_000, cost_per_1m_input=0.80, cost_per_1m_output=4.0,
    ),
    "claude-sonnet-4-6": ModelSpec(
        name="claude-sonnet-4-6", provider="anthropic",
        context_tokens=200_000, cost_per_1m_input=3.0, cost_per_1m_output=15.0,
    ),
    "claude-opus-4-6": ModelSpec(
        name="claude-opus-4-6", provider="anthropic",
        context_tokens=200_000, cost_per_1m_input=15.0, cost_per_1m_output=75.0,
    ),
}

# Task → preferred model name (overridden by config.yaml at runtime)
_DEFAULT_TASK_MODELS: dict[str, str] = {
    "compile":    "gemma4:31b-cloud",
    "qa":         "gemma4:31b-cloud",
    "lint":       "gemma4:31b-cloud",
    "classify":   "gemma4:31b-cloud",
    "merge":      "gemma4:31b-cloud",
    "introspect": "gemma4:31b-cloud",
    "embed":      "nomic-embed-text",
}

# Ordered Ollama fallback chain — first available wins
_FALLBACK_CHAIN: list[str] = [
    "gemma4:31b-cloud",
    "gemma4:latest",
    "gemma4:27b",
    "gemma4:12b",
    "gemma3:12b",
    "gemma3:4b",
]

# Cloud fallback — used when all Ollama models are unavailable
_CLOUD_FALLBACK_CHAIN: list[str] = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
]


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Rough token estimate: ~4 chars per token (BPE average).
    Conservative — always over-estimates slightly.
    """
    return max(1, len(text) // 4)


def fits_context(text: str, model: str, reserve_output: int = 2048) -> bool:
    """
    True if the text fits in the model's context window with room for output.
    """
    spec = _REGISTRY.get(model)
    if spec is None:
        return True  # unknown model — assume it fits, let it fail at runtime
    return estimate_tokens(text) + reserve_output <= spec.context_tokens


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    spec = _REGISTRY.get(model)
    if spec is None:
        return 0.0
    return (
        input_tokens  / 1_000_000 * spec.cost_per_1m_input +
        output_tokens / 1_000_000 * spec.cost_per_1m_output
    )


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

LLMCallable = Callable[..., Awaitable[str]]


class ModelRouter:
    """
    Selects the right model for a task, handles fallbacks, and injects
    provider credentials from Settings.
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
        # Injected in tests to avoid real LLM calls
        llm_fn: LLMCallable | None = None,
    ) -> None:
        self._task_models   = {**_DEFAULT_TASK_MODELS, **(task_models or {})}
        self._provider      = provider
        self._ant_key       = anthropic_api_key
        self._oai_key       = openai_api_key
        self._ollama_url    = ollama_base_url
        self._ollama_timeout = ollama_timeout
        self._cost_alert    = cost_alert_usd
        self._llm_fn        = llm_fn
        self._session_cost  = 0.0

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def model_for(self, task: str) -> str:
        return self._task_models.get(task, "gemma3:12b")

    def spec_for(self, model: str) -> ModelSpec | None:
        return _REGISTRY.get(model)

    def needs_split(self, text: str, task: str) -> bool:
        model = self.model_for(task)
        return not fits_context(text, model)

    # ------------------------------------------------------------------
    # LLM call (with fallback)
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
        """
        Call the LLM for a given task. Tries the configured model first,
        then walks the fallback chain on connection / availability errors.
        """
        if self._llm_fn is not None:
            return await self._llm_fn(
                prompt, model=model_override or self.model_for(task),
                system=system, max_tokens=max_tokens,
            )

        models_to_try = (
            [model_override] if model_override
            else self._build_attempt_list(task)
        )

        last_exc: Exception = RuntimeError("No models available")
        for model in models_to_try:
            spec = _REGISTRY.get(model)
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
                self._track_cost(model, estimate_tokens(prompt), estimate_tokens(result))
                log.info("LLM call complete", task=task, model=model,
                         elapsed_s=elapsed, response_chars=len(result),
                         session_cost=f"${self._session_cost:.4f}")
                return result
            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 2)
                log.warning("Model failed — trying fallback", exc_info=True,
                            model=model, task=task, elapsed_s=elapsed, error=str(exc))
                last_exc = exc

        log.error("All models exhausted", task=task, tried=str(models_to_try))
        raise last_exc

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    def _track_cost(self, model: str, in_tok: int, out_tok: int) -> None:
        cost = estimate_cost(model, in_tok, out_tok)
        self._session_cost += cost
        if cost > 0 and self._session_cost >= self._cost_alert:
            log.warning(
                "Session cost $%.4f reached alert threshold $%.2f",
                self._session_cost, self._cost_alert,
            )

    @property
    def session_cost(self) -> float:
        return self._session_cost

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_attempt_list(self, task: str) -> list[str]:
        preferred = self.model_for(task)

        # 1. Preferred model + same-provider fallbacks
        same_provider = [
            m for m in _FALLBACK_CHAIN
            if m != preferred
            and _REGISTRY.get(m, ModelSpec(m, self._provider, 0, 0, 0)).provider == self._provider
        ]

        # 2. Cloud fallbacks when API keys are present — used only after all
        #    same-provider models are exhausted
        cloud: list[str] = []
        if self._ant_key:
            cloud += [
                m for m in _CLOUD_FALLBACK_CHAIN
                if _REGISTRY.get(m) and _REGISTRY[m].provider == "anthropic"
            ]
        if self._oai_key:
            cloud += [
                m for m in _CLOUD_FALLBACK_CHAIN
                if _REGISTRY.get(m) and _REGISTRY[m].provider == "openai"
            ]

        return [preferred] + same_provider + cloud


def router_from_settings(settings: object, llm_fn: LLMCallable | None = None) -> ModelRouter:
    """
    Convenience factory — builds a ModelRouter from a Settings instance.
    Import lazily to avoid circular imports.
    """
    from mymem.config import Settings  # type: ignore[attr-defined]
    s: Settings = settings  # type: ignore[assignment]
    task_models: dict[str, str] = {
        "compile":    s.models.compile,
        "qa":         s.models.qa,
        "lint":       s.models.lint,
        "embed":      s.models.embed,
    }
    # Optional tasks — only add if the model attr exists on the config
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
