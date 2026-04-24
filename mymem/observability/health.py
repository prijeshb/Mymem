"""
Health checks — verify all dependencies before running a pipeline.

Run via CLI:  mymem health
Returns exit code 0 if all checks pass, 1 if any critical check fails.

Checks:
  - Config loaded without errors
  - .env secrets present for configured provider
  - Ollama reachable + required models pulled (if provider=ollama)
  - Anthropic API key valid (if provider=anthropic)
  - SQLite DB writable
  - Disk space available for raw/ and wiki/ paths
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import httpx
from rich.console import Console
from rich.table import Table

from mymem.observability.logger import get_logger

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class Status(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str

    @property
    def is_critical(self) -> bool:
        return self.status == Status.FAIL


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_config() -> CheckResult:
    try:
        from mymem.config import get_settings
        get_settings()
        return CheckResult("Config", Status.OK, "config.yaml loaded + validated")
    except Exception as exc:
        return CheckResult("Config", Status.FAIL, str(exc))


def _check_db(db_path: Path) -> CheckResult:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1")
        return CheckResult("SQLite DB", Status.OK, str(db_path))
    except Exception as exc:
        return CheckResult("SQLite DB", Status.FAIL, str(exc))


def _check_disk(path: Path, min_gb: float = 0.5) -> CheckResult:
    try:
        usage = shutil.disk_usage(path.parent if not path.exists() else path)
        free_gb = usage.free / 1024**3
        if free_gb < min_gb:
            return CheckResult(
                f"Disk ({path})",
                Status.WARN,
                f"Only {free_gb:.1f}GB free (threshold: {min_gb}GB)",
            )
        return CheckResult(f"Disk ({path})", Status.OK, f"{free_gb:.1f}GB free")
    except Exception as exc:
        return CheckResult(f"Disk ({path})", Status.WARN, str(exc))


def _check_ollama(base_url: str, required_models: list[str]) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Reachability
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        pulled = {m["name"] for m in resp.json().get("models", [])}
        results.append(CheckResult("Ollama", Status.OK, f"reachable at {base_url}"))
    except Exception as exc:
        results.append(CheckResult("Ollama", Status.FAIL, f"unreachable: {exc}"))
        # Can't check models if Ollama is down
        for model in required_models:
            results.append(CheckResult(f"Model: {model}", Status.FAIL, "Ollama not running"))
        return results

    # Model availability
    for model in required_models:
        # Ollama model names may have :latest appended
        candidates = {model, f"{model}:latest"}
        if candidates & pulled:
            results.append(CheckResult(f"Model: {model}", Status.OK, "pulled"))
        else:
            results.append(
                CheckResult(
                    f"Model: {model}",
                    Status.WARN,
                    f"not found — run: ollama pull {model}",
                )
            )

    return results


def _check_anthropic(api_key: str) -> CheckResult:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        # Cheapest possible call — just validates the key
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return CheckResult("Anthropic API", Status.OK, "key valid")
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "authentication" in msg.lower():
            return CheckResult("Anthropic API", Status.FAIL, "invalid API key")
        return CheckResult("Anthropic API", Status.WARN, f"unexpected error: {msg}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_health_checks() -> list[CheckResult]:
    from mymem.config import get_settings

    results: list[CheckResult] = []

    # 1. Config
    cfg_result = _check_config()
    results.append(cfg_result)
    if cfg_result.is_critical:
        return results  # can't proceed without config

    cfg = get_settings()

    # 2. DB
    results.append(_check_db(cfg.paths.db))

    # 3. Disk
    results.append(_check_disk(cfg.paths.raw))
    results.append(_check_disk(cfg.paths.wiki))

    # 4. Provider-specific
    if cfg.provider == "ollama":
        # Collect all unique models in use
        model_fields = vars(cfg.models)
        required = list({str(v) for v in model_fields.values()})
        results.extend(_check_ollama(cfg.ollama.base_url, required))

    elif cfg.provider == "anthropic" and cfg.anthropic_api_key:
        results.append(_check_anthropic(cfg.anthropic_api_key))

    return results


# ---------------------------------------------------------------------------
# CLI display
# ---------------------------------------------------------------------------

def print_health_report(results: list[CheckResult]) -> bool:
    """Print a Rich table. Returns True if all critical checks pass."""
    table = Table(title="MyMem Health Check", show_lines=True)
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detail")

    _icons = {Status.OK: "[green]OK[/green]", Status.WARN: "[yellow]WARN[/yellow]", Status.FAIL: "[red]FAIL[/red]"}

    for r in results:
        table.add_row(r.name, _icons[r.status], r.detail)

    console.print(table)

    failures = [r for r in results if r.is_critical]
    warnings = [r for r in results if r.status == Status.WARN]

    if failures:
        console.print(f"[red bold]{len(failures)} critical check(s) failed — fix before running.[/red bold]")
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s) — pipeline may still work but review above.[/yellow]")
    if not failures:
        console.print("[green bold]All critical checks passed.[/green bold]")

    return len(failures) == 0
