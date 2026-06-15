"""
Diagnostic tests — verify Ollama is reachable and can respond.

These tests hit the real Ollama server; they are skipped automatically
if Ollama is not running. Run directly with:

    pytest tests/test_ollama.py -v -s
"""

from __future__ import annotations

import pytest
import httpx


OLLAMA_BASE = "http://localhost:11434"


def _ollama_running() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


ollama_required = pytest.mark.skipif(
    not _ollama_running(),
    reason="Ollama server not reachable at http://localhost:11434",
)


# ---------------------------------------------------------------------------
# 1. Server reachability
# ---------------------------------------------------------------------------

@ollama_required
def test_ollama_reachable() -> None:
    r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    assert r.status_code == 200, f"Unexpected status: {r.status_code}"


# ---------------------------------------------------------------------------
# 2. List available models
# ---------------------------------------------------------------------------

@ollama_required
def test_ollama_lists_models() -> None:
    r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    data = r.json()
    models = [m["name"] for m in data.get("models", [])]
    print(f"\nAvailable Ollama models: {models}")
    assert isinstance(models, list), "Expected a list of models"


# ---------------------------------------------------------------------------
# 3. Simple chat round-trip (uses the first available model)
# ---------------------------------------------------------------------------

@ollama_required
@pytest.mark.asyncio
async def test_ollama_chat_roundtrip() -> None:
    from ollama import AsyncClient

    r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    all_models = [m["name"] for m in r.json().get("models", [])]
    # Embedding-only models (nomic-embed-text, mxbai-embed-*, etc.) don't support chat
    chat_models = [m for m in all_models if "embed" not in m.lower()]
    if not chat_models:
        pytest.skip("No chat-capable models pulled in Ollama — run: ollama pull <model>")

    model = chat_models[0]
    print(f"\nTesting chat with model: {model}")

    client = AsyncClient(host=OLLAMA_BASE, timeout=30)
    resp = await client.chat(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        options={"num_predict": 10},
    )
    reply = resp.message.content or ""
    print(f"Response: {reply!r}")
    assert reply.strip(), "Expected a non-empty response from Ollama"


# ---------------------------------------------------------------------------
# 4. Verify configured task models are pulled
# ---------------------------------------------------------------------------

@ollama_required
def test_configured_models_available() -> None:
    """Every configured *Ollama-provider* model must be pulled locally.

    Cloud-provider models (nvidia/groq/openrouter/anthropic) cannot be pulled
    locally — they are validated by API key, not `ollama pull` — so this check
    resolves each model's provider via the registry and only enforces local
    availability for Ollama models. If the active config uses no Ollama models,
    there is nothing to verify and the test skips.
    """
    from mymem.config import get_settings
    from mymem.pipeline.router._registry import DefaultModelRegistry

    get_settings.cache_clear()
    settings = get_settings()
    registry = DefaultModelRegistry()

    r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    pulled = {m["name"] for m in r.json().get("models", [])}

    task_models = {
        "compile":    settings.models.compile,
        "qa":         settings.models.qa,
        "lint":       settings.models.lint,
        "merge":      settings.models.merge,
        "classify":   settings.models.classify,
        "introspect": settings.models.introspect,
    }

    def is_ollama_model(model: str) -> bool:
        spec = registry.get(model)
        if spec is not None:
            return spec.provider == "ollama"
        # Unknown to the registry: only treat as local when the active provider
        # is ollama (otherwise it's a cloud model we can't pull).
        return settings.provider == "ollama"

    ollama_models = {t: m for t, m in task_models.items() if is_ollama_model(m)}
    if not ollama_models:
        pytest.skip(
            f"No Ollama-provider models configured (provider={settings.provider}); "
            "cloud models are validated by API key, not by `ollama pull`."
        )

    missing = {t: m for t, m in ollama_models.items() if m not in pulled}
    if missing:
        lines = "\n".join(f"  ollama pull {model}  # for task '{task}'"
                          for task, model in missing.items())
        pytest.fail(
            f"These configured Ollama models are not pulled:\n{lines}\n\n"
            f"Pulled models: {sorted(pulled)}"
        )
