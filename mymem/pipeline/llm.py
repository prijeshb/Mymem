"""
Thin async LLM client — wraps Anthropic, Ollama, OpenAI, Groq, and Gemini.

Never call this directly from pipeline code. Always go through router.py so
fallbacks and task-splitting are applied automatically.

Usage (injectable in tests):
    from mymem.pipeline.llm import complete
    text = await complete("Summarise this.", model="gemma3:12b", provider="ollama")
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Protocol — lets tests inject a fake llm_fn without importing real clients
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
# Ollama (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

async def _complete_ollama(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    base_url: str = "http://localhost:11434",
    timeout: int = 120,
) -> str:
    try:
        from ollama import AsyncClient
    except ImportError as e:
        raise RuntimeError("ollama package required for Ollama provider") from e

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = AsyncClient(host=base_url, timeout=timeout)
    resp = await client.chat(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        options={"num_predict": max_tokens},
    )
    return resp.message.content or ""


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

async def _complete_anthropic(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    api_key: str,
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic package required for Anthropic provider") from e

    client = anthropic.AsyncAnthropic(api_key=api_key)
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
# OpenAI
# ---------------------------------------------------------------------------

async def _complete_openai(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    api_key: str,
) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise RuntimeError("openai package required for OpenAI provider") from e

    client = AsyncOpenAI(api_key=api_key)
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


# ---------------------------------------------------------------------------
# Groq (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

async def _complete_groq(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    api_key: str,
) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise RuntimeError("openai package required for Groq provider") from e

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )
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


# ---------------------------------------------------------------------------
# NVIDIA (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

async def _complete_nvidia(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    api_key: str,
) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise RuntimeError("openai package required for NVIDIA provider") from e

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://integrate.api.nvidia.com/v1",
    )
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


# ---------------------------------------------------------------------------
# Gemini (future — stub until google-generativeai is added)
# ---------------------------------------------------------------------------

async def _complete_gemini(
    prompt: str,
    *,
    model: str,
    system: str,
    max_tokens: int,
    api_key: str,
) -> str:
    # Install: pip install google-generativeai
    # Then replace this stub with the real implementation.
    raise NotImplementedError(
        "Gemini provider is not yet implemented. "
        "Install google-generativeai and wire up _complete_gemini()."
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def complete(
    prompt: str,
    *,
    model: str,
    provider: str = "ollama",
    system: str = "",
    max_tokens: int = 4096,
    # provider credentials / config
    ollama_base_url: str = "http://localhost:11434",
    ollama_timeout: int = 120,
    anthropic_api_key: str = "",
    openai_api_key: str = "",
    groq_api_key: str = "",
    gemini_api_key: str = "",
    nvidia_api_key: str = "",
) -> str:
    """
    Send a prompt to an LLM and return the response text.

    Args:
        prompt:   The user message.
        model:    Model identifier (e.g. "gemma4:12b", "claude-sonnet-4-6").
        provider: "ollama" | "anthropic" | "openai" | "groq" | "gemini"
        system:   Optional system prompt.
        max_tokens: Maximum tokens in the response.
    """
    if provider == "ollama":
        return await _complete_ollama(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            base_url=ollama_base_url,
            timeout=ollama_timeout,
        )
    if provider == "anthropic":
        if not anthropic_api_key:
            raise ValueError("anthropic_api_key is required for provider=anthropic")
        return await _complete_anthropic(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            api_key=anthropic_api_key,
        )
    if provider == "openai":
        if not openai_api_key:
            raise ValueError("openai_api_key is required for provider=openai")
        return await _complete_openai(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            api_key=openai_api_key,
        )
    if provider == "groq":
        if not groq_api_key:
            raise ValueError("groq_api_key is required for provider=groq")
        return await _complete_groq(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            api_key=groq_api_key,
        )
    if provider == "gemini":
        if not gemini_api_key:
            raise ValueError("gemini_api_key is required for provider=gemini")
        return await _complete_gemini(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            api_key=gemini_api_key,
        )
    if provider == "nvidia":
        if not nvidia_api_key:
            raise ValueError("nvidia_api_key is required for provider=nvidia")
        return await _complete_nvidia(
            prompt,
            model=model,
            system=system,
            max_tokens=max_tokens,
            api_key=nvidia_api_key,
        )
    raise ValueError(
        f"Unknown provider: {provider!r}. "
        "Choose ollama | anthropic | openai | groq | gemini | nvidia"
    )
