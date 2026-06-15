"""
Provider credential abstraction for the LLM router.

Adding a new provider requires only:
  1. Register the new key name in KeyMapCredentials.KNOWN_PROVIDERS
  2. Wire it into complete() in llm.py
  3. Pass the key via from_kwargs() — no router internals to touch
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ProviderCredentials(ABC):
    """Abstract store of API keys keyed by provider name."""

    @abstractmethod
    def api_key_for(self, provider: str) -> str:
        """Return the API key for *provider*, or empty string if not set."""
        ...

    @abstractmethod
    def to_llm_kwargs(self) -> dict[str, str]:
        """Return a dict of ``<provider>_api_key`` kwargs for ``complete()``."""
        ...


@dataclass(frozen=True)
class KeyMapCredentials(ProviderCredentials):
    """Concrete implementation backed by a plain dict."""

    KNOWN_PROVIDERS: tuple[str, ...] = field(
        default=(
            "anthropic",
            "openai",
            "groq",
            "nvidia",
            "openrouter",
        ),
        init=False,
        compare=False,
        repr=False,
    )

    _keys: dict[str, str] = field(default_factory=dict)

    def api_key_for(self, provider: str) -> str:
        return self._keys.get(provider, "")

    def to_llm_kwargs(self) -> dict[str, str]:
        return {f"{p}_api_key": self._keys.get(p, "") for p in self.KNOWN_PROVIDERS}

    @classmethod
    def from_kwargs(
        cls,
        *,
        anthropic: str = "",
        openai: str = "",
        groq: str = "",
        nvidia: str = "",
        openrouter: str = "",
    ) -> KeyMapCredentials:
        return cls(
            _keys={
                "anthropic": anthropic,
                "openai": openai,
                "groq": groq,
                "nvidia": nvidia,
                "openrouter": openrouter,
            }
        )
