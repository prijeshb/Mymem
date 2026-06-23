"""Tests for mymem.pipeline.router and mymem.pipeline.splitter."""

from __future__ import annotations

import pytest

from mymem.pipeline.router import (
    ModelRouter,
    estimate_cost,
    estimate_tokens,
    fits_context,
)
from mymem.pipeline.splitter import ChunkSplitter, merge_prompt, merge_system_prompt

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") >= 1

    def test_scales_with_length(self):
        short = estimate_tokens("hello")
        long  = estimate_tokens("hello " * 1000)
        assert long > short

    def test_approx_ratio(self):
        # 4000 chars ≈ 1000 tokens
        assert 800 <= estimate_tokens("x" * 4000) <= 1200


class TestFitsContext:
    def test_short_text_fits_small_model(self):
        assert fits_context("hello world", "gemma3:4b")

    def test_huge_text_does_not_fit_small_model(self):
        huge = "word " * 10_000  # ~50k chars → ~12.5k tokens > 8192
        assert not fits_context(huge, "gemma3:4b")

    def test_large_text_fits_gemma4(self):
        # gemma4:12b has 131k context
        medium = "word " * 5_000  # ~25k chars → ~6.25k tokens
        assert fits_context(medium, "gemma4:12b")

    def test_unknown_model_assumed_fits(self):
        assert fits_context("any text", "unknown-model:latest")


class TestEstimateCost:
    def test_local_model_zero_cost(self):
        assert estimate_cost("gemma4:12b", 100_000, 50_000) == 0.0

    def test_sonnet_input_cost(self):
        # $3.00 per 1M input tokens
        cost = estimate_cost("claude-sonnet-4-6", 1_000_000, 0)
        assert abs(cost - 3.0) < 0.001

    def test_output_more_expensive_than_input(self):
        inp = estimate_cost("claude-sonnet-4-6", 1000, 0)
        out = estimate_cost("claude-sonnet-4-6", 0, 1000)
        assert out > inp

    def test_unknown_model_zero_cost(self):
        assert estimate_cost("mystery:model", 999_999, 999_999) == 0.0


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class TestModelRouter:
    def test_default_task_models(self):
        router = ModelRouter()
        assert router.model_for("compile") == "gemma4:31b-cloud"
        assert router.model_for("qa") == "gemma4:31b-cloud"
        assert router.model_for("lint") == "gemma4:31b-cloud"

    def test_task_model_override(self):
        router = ModelRouter(task_models={"compile": "claude-sonnet-4-6"})
        assert router.model_for("compile") == "claude-sonnet-4-6"
        # Other tasks unaffected
        assert router.model_for("qa") == "gemma4:31b-cloud"

    def test_unknown_task_fallback(self):
        router = ModelRouter()
        model = router.model_for("nonexistent_task")
        assert isinstance(model, str)

    def test_needs_split_short_text(self):
        router = ModelRouter()
        assert not router.needs_split("hello", "compile")

    def test_needs_split_long_text(self):
        router = ModelRouter(task_models={"compile": "gemma3:4b"})
        long_text = "word " * 10_000
        assert router.needs_split(long_text, "compile")

    @pytest.mark.asyncio
    async def test_call_uses_injected_llm_fn(self):
        async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return f"response from {model}"

        router = ModelRouter(llm_fn=fake_llm)
        result = await router.call("test prompt", task="qa")
        assert "response from" in result

    @pytest.mark.asyncio
    async def test_call_passes_system_prompt(self):
        received: dict[str, str] = {}

        async def capture(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            received["system"] = system
            return "ok"

        router = ModelRouter(llm_fn=capture)
        await router.call("prompt", task="qa", system="Be concise.")
        assert received["system"] == "Be concise."

    def test_session_cost_starts_at_zero(self):
        router = ModelRouter()
        assert router.session_cost == 0.0

    def test_db_path_stored(self, tmp_path):
        db = tmp_path / "test.db"
        router = ModelRouter(db_path=db)
        assert router._db_path == db

    def test_db_path_none_by_default(self):
        router = ModelRouter()
        assert router._db_path is None

    @pytest.mark.asyncio
    async def test_trace_written_to_db(self, tmp_path):
        """trace_llm() persists a row to llm_traces when db_path is set."""
        import sqlite3

        db = tmp_path / "traces.db"

        async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return "traced response"

        # llm_fn bypasses the real complete() but NOT trace_llm — we test the
        # non-llm_fn path by using a patched complete instead.
        from unittest.mock import AsyncMock, patch

        with patch("mymem.pipeline.router._router.complete", new=AsyncMock(return_value="traced")):
            router = ModelRouter(
                task_models={"qa": "gemma3:4b"},
                db_path=db,
            )
            result = await router.call("hello", task="qa")

        assert result == "traced"
        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT task, model FROM llm_traces").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "qa"
        assert rows[0][1] == "gemma3:4b"


# ---------------------------------------------------------------------------
# FreeTierFallbackChain — cross-provider swap on rate limit
# ---------------------------------------------------------------------------

class TestFreeTierFallbackChain:
    def _reg(self):
        from mymem.pipeline.router._registry import DefaultModelRegistry
        return DefaultModelRegistry()

    def _chain(self, *, has_groq=True, has_openrouter=True):
        from mymem.pipeline.router._chain import FreeTierFallbackChain
        return FreeTierFallbackChain(has_groq=has_groq, has_openrouter=has_openrouter)

    def test_preferred_is_first_and_chain_crosses_providers(self):
        chain = self._chain().build("meta/llama-3.3-70b-instruct", self._reg(), "nvidia")
        assert chain[0] == "meta/llama-3.3-70b-instruct"
        reg = self._reg()
        providers = [reg.get(m).provider for m in chain]
        # The second attempt must be a DIFFERENT provider (groq) — a same-account
        # NVIDIA 429 would otherwise block the whole pipeline.
        assert providers[0] == "nvidia"
        assert "groq" in providers
        assert providers[1] == "groq"

    def test_ollama_floor_always_last(self):
        chain = self._chain(has_groq=False, has_openrouter=False).build(
            "meta/llama-3.3-70b-instruct", self._reg(), "nvidia"
        )
        assert chain[-1] == "gemma4:31b-cloud"

    def test_groq_excluded_without_key(self):
        chain = self._chain(has_groq=False).build(
            "meta/llama-3.3-70b-instruct", self._reg(), "nvidia"
        )
        reg = self._reg()
        assert not any(reg.get(m).provider == "groq" for m in chain)

    def test_openrouter_gated_on_key(self):
        reg = self._reg()
        pref = "meta/llama-3.3-70b-instruct"
        with_key = self._chain(has_openrouter=True).build(pref, reg, "nvidia")
        without = self._chain(has_openrouter=False).build(pref, reg, "nvidia")
        assert any(reg.get(m).provider == "openrouter" for m in with_key)
        assert not any(reg.get(m).provider == "openrouter" for m in without)

    def test_preferred_not_duplicated_when_already_in_chain(self):
        # Preferred is itself a chain member (groq small model).
        chain = self._chain().build("llama-3.1-8b-instant", self._reg(), "groq")
        assert chain.count("llama-3.1-8b-instant") == 1

    @pytest.mark.asyncio
    async def test_router_swaps_provider_on_rate_limit(self):
        """A 429 on the NVIDIA model must make the router retry on Groq and succeed."""
        from unittest.mock import patch

        from mymem.pipeline.router._chain import FreeTierFallbackChain
        from mymem.pipeline.router._credentials import KeyMapCredentials
        from mymem.pipeline.router._registry import DefaultModelRegistry

        calls: list[tuple[str, str]] = []

        async def fake_complete(prompt, *, model, provider, **kwargs):
            calls.append((model, provider))
            if provider == "nvidia":
                raise RuntimeError("Error code: 429 - rate limit exceeded")
            return f"ok-from-{provider}"

        router = ModelRouter(
            task_models={"compile": "meta/llama-3.3-70b-instruct"},
            provider="nvidia",
            credentials=KeyMapCredentials.from_kwargs(groq="gk", nvidia="nk"),
            fallback_chain=FreeTierFallbackChain(has_groq=True, has_openrouter=False),
            registry=DefaultModelRegistry(),
        )
        with patch("mymem.pipeline.router._router.complete", new=fake_complete):
            result = await router.call("prompt", task="compile")

        assert result == "ok-from-groq"
        assert calls[0][1] == "nvidia"   # tried NVIDIA first
        assert calls[1][1] == "groq"     # swapped to Groq on the 429

    async def test_fallback_is_logged_with_next_model(self):
        """A handled fallback is recorded to the log as a clean WARNING naming the
        failed AND next model — no error-level traceback for a recovered retry."""
        import logging
        from unittest.mock import patch

        from mymem.pipeline.router._chain import FreeTierFallbackChain
        from mymem.pipeline.router._credentials import KeyMapCredentials
        from mymem.pipeline.router._registry import DefaultModelRegistry

        async def fake_complete(prompt, *, model, provider, **kwargs):
            if provider == "nvidia":
                raise RuntimeError("Error code: 429 - rate limit exceeded")
            return f"ok-from-{provider}"

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        mlog = logging.getLogger("mymem")  # children propagate up to here
        handler = _Capture()
        handler.setLevel(logging.WARNING)
        mlog.addHandler(handler)
        try:
            router = ModelRouter(
                task_models={"compile": "meta/llama-3.3-70b-instruct"},
                provider="nvidia",
                credentials=KeyMapCredentials.from_kwargs(groq="gk", nvidia="nk"),
                fallback_chain=FreeTierFallbackChain(has_groq=True, has_openrouter=False),
                registry=DefaultModelRegistry(),
            )
            with patch("mymem.pipeline.router._router.complete", new=fake_complete):
                result = await router.call("prompt", task="compile")
        finally:
            mlog.removeHandler(handler)

        assert result == "ok-from-groq"
        fallbacks = [r for r in records if "falling back" in r.getMessage().lower()]
        assert fallbacks, "expected a fallback to be logged"
        rec = fallbacks[0]
        assert rec.levelno == logging.WARNING  # handled retry, not an error
        assert getattr(rec, "failed_model", None) == "meta/llama-3.3-70b-instruct"
        assert getattr(rec, "next_model", None)  # names the model it fell back to


# ---------------------------------------------------------------------------
# ChunkSplitter
# ---------------------------------------------------------------------------

class TestChunkSplitter:
    def test_short_text_single_chunk(self):
        splitter = ChunkSplitter(max_tokens=1000)
        chunks = splitter.split("hello world")
        assert len(chunks) == 1
        assert chunks[0] == "hello world"

    def test_long_text_multiple_chunks(self):
        splitter = ChunkSplitter(max_tokens=100)
        text = "word " * 500
        chunks = splitter.split(text)
        assert len(chunks) > 1

    def test_chunks_cover_full_text(self):
        splitter = ChunkSplitter(max_tokens=50, overlap=0.1)
        words = ["word"] * 200
        text = " ".join(words)
        chunks = splitter.split(text)
        # All words should appear in at least one chunk
        combined = " ".join(chunks)
        for word in words[:10]:
            assert word in combined

    def test_invalid_overlap_raises(self):
        with pytest.raises(ValueError):
            ChunkSplitter(overlap=1.5)

    def test_estimated_chunks(self):
        splitter = ChunkSplitter(max_tokens=100)
        short = "hi"
        assert splitter.estimated_chunks(short) == 1
        long = "word " * 1000
        assert splitter.estimated_chunks(long) > 1


class TestMergePrompt:
    def test_includes_all_partials(self):
        partials = ["Part A content", "Part B content", "Part C content"]
        prompt = merge_prompt(partials)
        assert "PARTIAL 1" in prompt
        assert "PARTIAL 2" in prompt
        assert "PARTIAL 3" in prompt
        assert "Part A content" in prompt

    def test_includes_title_hint(self):
        prompt = merge_prompt(["content"], title="My Page")
        assert "My Page" in prompt

    def test_merge_system_prompt_non_empty(self):
        system = merge_system_prompt()
        assert len(system) > 50
        assert "wiki" in system.lower()
