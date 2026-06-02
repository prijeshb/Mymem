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
    from mymem.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    pulled = {m["name"] for m in r.json().get("models", [])}

    task_models = {
        "compile":    settings.models.compile,
        "qa":         settings.models.qa,
        "lint":       settings.models.lint,
    }

    missing = {task: model for task, model in task_models.items() if model not in pulled}
    if missing:
        lines = "\n".join(f"  ollama pull {model}  # for task '{task}'"
                          for task, model in missing.items())
        pytest.fail(
            f"These configured models are not pulled in Ollama:\n{lines}\n\n"
            f"Pulled models: {sorted(pulled)}"
        )
