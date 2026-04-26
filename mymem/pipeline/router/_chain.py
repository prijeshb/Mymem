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
