"""Fallback chain: ordered model attempt list, cloud-first."""
from __future__ import annotations

from mymem.pipeline.router._types import IFallbackChain, IModelRegistry

# Free cloud model first; local small models as last resort.
# Subscription-only models excluded (deepseek-v4-flash, kimi-k2.6, nemotron-3-super → 403).
# gemma4:latest excluded (9.6 GB, exceeds available system RAM).
_OLLAMA_CHAIN: list[str] = [
    "gemma4:31b-cloud",
    "gemma4:12b",
    "gemma3:12b",
    "gemma3:4b",
]

_ANTHROPIC_CHAIN: list[str] = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
]


class OllamaFallbackChain(IFallbackChain):
    """
    Cloud-first Ollama fallback chain.
    Appends Anthropic cloud models at the end when an API key is present.
    """

    def __init__(self, anthropic_key: str = "", openai_key: str = "") -> None:
        self._ant_key = anthropic_key
        self._oai_key = openai_key

    def build(
        self,
        preferred: str,
        registry: IModelRegistry,
        provider: str,
    ) -> list[str]:
        same_provider = [
            m for m in _OLLAMA_CHAIN
            if m != preferred
            and (s := registry.get(m)) is not None
            and s.provider == provider
        ]
        cloud: list[str] = []
        if self._ant_key:
            cloud += [
                m for m in _ANTHROPIC_CHAIN
                if (s := registry.get(m)) is not None and s.provider == "anthropic"
            ]
        return [preferred] + same_provider + cloud


# ---------------------------------------------------------------------------
# Free-tier cross-provider chain
# ---------------------------------------------------------------------------

# Ordered free-tier candidates. NVIDIA's 40 RPM limit is per-account, so when a
# call is rate-limited (429) the swap must cross to a DIFFERENT provider with its
# own limit — not just another NVIDIA model. Groq (separate account) comes first
# after the preferred model, then a second NVIDIA model, then OpenRouter (gated on
# key), and finally Ollama cloud which has no per-request API limit.
_FREE_CHAIN: list[str] = [
    "llama-3.3-70b-versatile",                 # groq (separate account limit)
    "nvidia/llama-3.3-nemotron-super-49b-v1",  # nvidia (alt model)
    "llama-3.1-8b-instant",                    # groq (fast/small)
    "meta-llama/llama-3.3-70b-instruct:free",  # openrouter (key-gated, needs credit)
    "gemma4:31b-cloud",                        # ollama cloud floor — no API limit
]


class FreeTierFallbackChain(IFallbackChain):
    """
    Cross-provider fallback for free-tier setups (provider = nvidia/groq/openrouter).

    On a rate-limit or failure, the router moves to the next model; this chain makes
    the next model live on a *different* free provider so per-account limits don't
    block the whole pipeline. Candidates whose provider has no configured key are
    skipped; the Ollama floor is always retained as the last resort.
    """

    def __init__(self, *, has_groq: bool, has_openrouter: bool) -> None:
        self._has_groq = has_groq
        self._has_openrouter = has_openrouter

    def _key_available(self, provider: str) -> bool:
        if provider == "groq":
            return self._has_groq
        if provider == "openrouter":
            return self._has_openrouter
        return True  # nvidia / ollama need no extra gating here

    def build(
        self,
        preferred: str,
        registry: IModelRegistry,
        provider: str,
    ) -> list[str]:
        chain: list[str] = [preferred]
        for model in _FREE_CHAIN:
            if model == preferred:
                continue
            spec = registry.get(model)
            if spec is None or not self._key_available(spec.provider):
                continue
            if model not in chain:
                chain.append(model)
        return chain
