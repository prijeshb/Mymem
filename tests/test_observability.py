"""Tests for observability modules."""

import sqlite3
import time
from pathlib import Path

import pytest

from mymem.observability.logger import (
    configure_logging, get_logger, set_run_id, get_run_id,
)
from mymem.observability.tracer import (
    estimate_cost, trace_llm, session_cost, TraceRecord,
)


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class TestLogger:
    def test_configure_idempotent(self):
        """Calling configure_logging twice should not raise."""
        from mymem.observability import logger as log_module
        log_module._configured = False  # reset for test
        configure_logging(level="WARNING", fmt="json")
        configure_logging(level="DEBUG", fmt="rich")  # second call — no error
        log_module._configured = False  # clean up

    def test_run_id_propagation(self):
        rid = set_run_id("test123")
        assert get_run_id() == "test123"
        assert rid == "test123"

    def test_run_id_auto_generated(self):
        rid = set_run_id()
        assert len(rid) == 8
        assert get_run_id() == rid

    def test_get_logger_returns_context_logger(self):
        from mymem.observability.logger import ContextLogger
        log = get_logger("test")
        assert isinstance(log, ContextLogger)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

class TestCostEstimation:
    def test_local_model_zero_cost(self):
        cost = estimate_cost("gemma3:12b", input_tokens=10_000, output_tokens=5_000)
        assert cost == 0.0

    def test_anthropic_sonnet_cost(self):
        # 1M input tokens @ $3.00 = $3.00
        cost = estimate_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
        assert abs(cost - 3.0) < 0.0001

    def test_unknown_model_zero_cost(self):
        cost = estimate_cost("mystery-model:latest", input_tokens=1000, output_tokens=1000)
        assert cost == 0.0

    def test_output_tokens_more_expensive(self):
        input_cost = estimate_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=0)
        output_cost = estimate_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=1000)
        assert output_cost > input_cost


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

class TestTracer:
    def test_trace_llm_records_latency(self):
        with trace_llm("test_task", "gemma3:4b", "ollama") as t:
            time.sleep(0.01)
            t.record(input_tokens=100, output_tokens=200)

        assert t.latency_ms >= 10
        assert t.input_tokens == 100
        assert t.output_tokens == 200

    def test_trace_llm_records_error(self):
        with pytest.raises(RuntimeError):
            with trace_llm("fail_task", "gemma3:4b", "ollama") as t:
                raise RuntimeError("model timeout")

        assert t.error == "model timeout"
        assert t.latency_ms > 0

    def test_trace_persists_to_db(self, tmp_path):
        db = tmp_path / "test.db"
        set_run_id("testrun")

        with trace_llm("compile", "gemma3:12b", "ollama", db_path=db) as t:
            t.record(input_tokens=500, output_tokens=1000)

        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT task, model, input_tokens FROM llm_traces").fetchall()

        assert len(rows) == 1
        assert rows[0] == ("compile", "gemma3:12b", 500)

    def test_session_cost_sums_traces(self, tmp_path):
        db = tmp_path / "cost.db"
        set_run_id("cost_run")

        for _ in range(3):
            with trace_llm("qa", "claude-sonnet-4-6", "anthropic", db_path=db) as t:
                t.record(input_tokens=100_000, output_tokens=50_000)

        total = session_cost(db, "cost_run")
        # 3 × (100k * $3/1M + 50k * $15/1M) = 3 × (0.30 + 0.75) = $3.15
        assert abs(total - 3.15) < 0.01

    def test_session_cost_ignores_other_runs(self, tmp_path):
        db = tmp_path / "multi.db"

        set_run_id("run_a")
        with trace_llm("compile", "claude-sonnet-4-6", "anthropic", db_path=db) as t:
            t.record(input_tokens=1_000_000, output_tokens=0)  # $3.00

        set_run_id("run_b")
        cost_b = session_cost(db, "run_b")
        assert cost_b == 0.0

        cost_a = session_cost(db, "run_a")
        assert abs(cost_a - 3.0) < 0.001
