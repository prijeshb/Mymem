"""
Async LLM client — Anthropic, Ollama, OpenAI, Groq, NVIDIA, OpenRouter.

Design:
  LLMProvider          — Strategy ABC: one concrete subclass per backend
  _OpenAICompatProvider — Bridge: same openai SDK, different base_url per provider
  build_provider()     — Factory (DIP: callers depend on LLMProvider, not concretes)
  complete()           — Backward-compatible facade; prefer build_provider() in new code

Adding a new provider:
  1. Subclass LLMProvider (or _OpenAICompatProvider for OpenAI-compatible APIs)
  2. Set _BASE_URL if OpenAI-compatible
  3. Add an elif branch in build_provider()
  No other files need to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Callable protocol — lightweight injection hook for testing
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMFn(Protocol):
    async def __call__(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Provider ABC — Strategy pattern
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """
    One subclass per LLM provider backend.

    Open/Closed: extend by subclassing — never modify this file.
    Liskov: every provider is a drop-in replacement for any other.
    """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible base — Bridge for Groq, NVIDIA, OpenRouter, OpenAI
# ---------------------------------------------------------------------------

class _OpenAICompatProvider(LLMProvider):
    """
    Bridge: reuses the openai SDK across all OpenAI-compatible endpoints.
    Subclasses set _BASE_URL to point at their API gateway.
    """

    _BASE_URL: str | None = None  # None → use default openai.com endpoint

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                f"openai package required for {type(self).__name__}"
            ) from exc

        kwargs: dict[str, object] = {"api_key": self._api_key}
        if self._BASE_URL is not None:
            kwargs["base_url"] = self._BASE_URL

        client = AsyncOpenAI(**kwargs)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""


class OpenAIProvider(_OpenAICompatProvider):
    """Standard OpenAI API (api.openai.com)."""
    _BASE_URL = None


class GroqProvider(_OpenAICompatProvider):
    """Groq inference API — fast open-weight model hosting."""
    _BASE_URL = "https://api.groq.com/openai/v1"


class NVIDIAProvider(_OpenAICompatProvider):
    """NVIDIA NIM — free-credit model API."""
    _BASE_URL = "https://integrate.api.nvidia.com/v1"


class OpenRouterProvider(_OpenAICompatProvider):
    """OpenRouter — multi-provider gateway with free-tier models."""
    _BASE_URL = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package required for Anthropic provider") from exc

        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        kwargs: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        msg = await client.messages.create(**kwargs)  # type: ignore[arg-type]
        block = msg.content[0]
        return block.text if hasattr(block, "text") else ""


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: int = 120,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str:
        try:
            from ollama import AsyncClient
        except ImportError as exc:
            raise RuntimeError("ollama package required for Ollama provider") from exc

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        client = AsyncClient(host=self._base_url, timeout=self._timeout)
        resp = await client.chat(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            options={"num_predict": max_tokens},
        )
        return resp.message.content or ""


# ---------------------------------------------------------------------------
# Gemini (stub — not yet implemented)
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    """Placeholder until google-generativeai is wired up."""

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        system: str,
        max_tokens: int,
    ) -> str:
        raise NotImplementedError(
            "Gemini provider is not yet implemented. "
            "Install google-generativeai and implement GeminiProvider.complete()."
        )


# ---------------------------------------------------------------------------
# Factory — Dependency Inversion: callers receive LLMProvider, not concretes
# ---------------------------------------------------------------------------

def build_provider(
    provider: str,
    *,
    anthropic_api_key: str = "",
    openai_api_key: str = "",
    groq_api_key: str = "",
    gemini_api_key: str = "",
    nvidia_api_key: str = "",
    openrouter_api_key: str = "",
    ollama_base_url: str = "http://localhost:11434",
    ollama_timeout: int = 120,
) -> LLMProvider:
    """
    Resolve *provider* string → concrete LLMProvider instance.
    Validates required credentials before instantiation (fail-fast).
    """
    if provider == "ollama":
        return OllamaProvider(base_url=ollama_base_url, timeout=ollama_timeout)
    if provider == "anthropic":
        if not anthropic_api_key:
            raise ValueError("anthropic_api_key is required for provider=anthropic")
        return AnthropicProvider(api_key=anthropic_api_key)
    if provider == "openai":
        if not openai_api_key:
            raise ValueError("openai_api_key is required for provider=openai")
        return OpenAIProvider(api_key=openai_api_key)
    if provider == "groq":
        if not groq_api_key:
            raise ValueError("groq_api_key is required for provider=groq")
        return GroqProvider(api_key=groq_api_key)
    if provider == "nvidia":
        if not nvidia_api_key:
            raise ValueError("nvidia_api_key is required for provider=nvidia")
        return NVIDIAProvider(api_key=nvidia_api_key)
    if provider == "openrouter":
        if not openrouter_api_key:
            raise ValueError("openrouter_api_key is required for provider=openrouter")
        return OpenRouterProvider(api_key=openrouter_api_key)
    if provider == "gemini":
        raise NotImplementedError(
            "Gemini provider is not yet implemented. "
            "Install google-generativeai and implement GeminiProvider.complete()."
        )
    raise ValueError(
        f"Unknown provider: {provider!r}. "
        "Choose ollama | anthropic | openai | groq | nvidia | openrouter | gemini"
    )


# ---------------------------------------------------------------------------
# Backward-compatible facade — prefer build_provider() in new code
# ---------------------------------------------------------------------------

async def complete(
    prompt: str,
    *,
    model: str,
    provider: str = "ollama",
    system: str = "",
    max_tokens: int = 4096,
    ollama_base_url: str = "http://localhost:11434",
    ollama_timeout: int = 120,
    anthropic_api_key: str = "",
    openai_api_key: str = "",
    groq_api_key: str = "",
    gemini_api_key: str = "",
    nvidia_api_key: str = "",
    openrouter_api_key: str = "",
) -> str:
    """
    Send a prompt to an LLM and return the response text.

    Delegates to build_provider(). In new code, call build_provider() directly
    so the provider instance can be reused across calls.

    Args:
        prompt:     User message.
        model:      Model identifier (e.g. "gemma4:12b", "claude-sonnet-4-6").
        provider:   "ollama" | "anthropic" | "openai" | "groq" | "nvidia" | "openrouter" | "gemini"
        system:     Optional system prompt.
        max_tokens: Maximum response tokens.
    """
    p = build_provider(
        provider,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        groq_api_key=groq_api_key,
        gemini_api_key=gemini_api_key,
        nvidia_api_key=nvidia_api_key,
        openrouter_api_key=openrouter_api_key,
        ollama_base_url=ollama_base_url,
        ollama_timeout=ollama_timeout,
    )
    return await p.complete(prompt, model=model, system=system, max_tokens=max_tokens)
