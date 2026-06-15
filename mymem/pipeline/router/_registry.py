"""Default model registry pre-seeded with known model specifications."""
from __future__ import annotations

from mymem.pipeline.router._types import IModelRegistry, ModelSpec


class DefaultModelRegistry(IModelRegistry):
    def __init__(self) -> None:
        self._specs: dict[str, ModelSpec] = {}
        self._seed()

    def get(self, name: str) -> ModelSpec | None:
        return self._specs.get(name)

    def register(self, spec: ModelSpec) -> None:
        self._specs[spec.name] = spec

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    def _seed(self) -> None:
        entries: list[ModelSpec] = [
            # ── Ollama cloud ──────────────────────────────────────────────────
            ModelSpec("deepseek-v4-flash:cloud", "ollama", 1_000_000, 0.0, 0.0),
            ModelSpec("kimi-k2.6:cloud",          "ollama", 131_072,   0.0, 0.0),
            ModelSpec("nemotron-3-super:cloud",    "ollama", 131_072,   0.0, 0.0),
            ModelSpec("gemma4:31b-cloud",          "ollama", 131_072,   0.0, 0.0),
            # ── Ollama local ──────────────────────────────────────────────────
            ModelSpec("gemma4:27b",  "ollama", 131_072, 0.0, 0.0),
            ModelSpec("gemma4:12b",  "ollama", 131_072, 0.0, 0.0),
            ModelSpec("gemma3:12b",  "ollama", 8_192,   0.0, 0.0),
            ModelSpec("gemma3:4b",   "ollama", 8_192,   0.0, 0.0),
            # ── Anthropic ─────────────────────────────────────────────────────
            ModelSpec("claude-haiku-4-5",          "anthropic", 200_000, 0.80, 4.0),
            ModelSpec("claude-haiku-4-5-20251001", "anthropic", 200_000, 0.80, 4.0),
            ModelSpec("claude-sonnet-4-6",         "anthropic", 200_000, 3.0,  15.0),
            ModelSpec("claude-opus-4-6",           "anthropic", 200_000, 15.0, 75.0),
            # ── NVIDIA NIM (free tier, 40 RPM/account) ───────────────────────
            ModelSpec("meta/llama-3.3-70b-instruct",                "nvidia", 128_000, 0.0, 0.0),
            ModelSpec("nvidia/llama-3.3-nemotron-super-49b-v1",     "nvidia", 128_000, 0.0, 0.0),
            ModelSpec("nvidia/llama-3.1-nemotron-70b-instruct",     "nvidia", 128_000, 0.0, 0.0),
            ModelSpec("mistralai/mistral-large-2-instruct",         "nvidia", 128_000, 0.0, 0.0),
            # ── Groq (free tier, separate per-account limit) ─────────────────
            ModelSpec("llama-3.3-70b-versatile", "groq", 128_000, 0.0, 0.0),
            ModelSpec("llama-3.1-8b-instant",    "groq", 128_000, 0.0, 0.0),
            # ── OpenRouter free tier (needs account credit to be reliable) ───
            ModelSpec("meta-llama/llama-3.3-70b-instruct:free", "openrouter", 65_536, 0.0, 0.0),
            ModelSpec("meta-llama/llama-3.2-3b-instruct:free", "openrouter", 131_072, 0.0, 0.0),
        ]
        for spec in entries:
            self.register(spec)


# Module-level singleton — used as the default by utility functions.
_default_registry: IModelRegistry = DefaultModelRegistry()
