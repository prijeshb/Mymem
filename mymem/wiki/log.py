"""
Wiki log — append-only operation log in log.md.

Format per entry:
    ## [YYYY-MM-DD HH:MM] operation | description
    Updated: page-a.md, page-b.md

Each header starts with '## [' so unix tooling works:
    grep "^## [" wiki/log.md | tail -5
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from mymem.wiki.types import LogEntry, LogOperation


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^## \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] (\w+) \| (.+)$"
)


def _parse_log(text: str) -> list[LogEntry]:
    entries: list[LogEntry] = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        ts_str, op_str, description = m.groups()
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
            op = LogOperation(op_str)
        except ValueError:
            i += 1
            continue

        # Collect any "Updated: ..." or "Result: ..." lines that follow
        affected: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("## ["):
            line = lines[i].strip()
            if line.startswith("Updated:") or line.startswith("Result"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    affected = [p.strip() for p in parts[1].split(",") if p.strip()]
            i += 1

        entries.append(
            LogEntry(
                operation=op,
                description=description.strip(),
                affected_pages=tuple(affected),
                timestamp=ts,
            )
        )

    return entries


# ---------------------------------------------------------------------------
# Rendering one entry
# ---------------------------------------------------------------------------

def _render_entry(entry: LogEntry) -> str:
    lines = [entry.header()]
    if entry.affected_pages:
        pages_str = ", ".join(entry.affected_pages)
        lines.append(f"Updated: {pages_str}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WikiLog
# ---------------------------------------------------------------------------

class WikiLog:
    """
    Append-only log over log.md.

    Entries are never deleted or overwritten — only appended.
    Parsing is lazy: load() reads from disk each time (the file may grow
    between calls in long-running server mode).
    """

    def __init__(self, log_path: Path) -> None:
        self._path = log_path

    def append(self, entry: LogEntry) -> None:
        """Append one entry to log.md. Creates the file if it doesn't exist."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rendered = _render_entry(entry)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(rendered + "\n")

    def load(self) -> list[LogEntry]:
        """Return all entries parsed from log.md, oldest first."""
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        return _parse_log(text)

    def recent(self, n: int = 10) -> list[LogEntry]:
        """Return the last n entries (most recent last)."""
        entries = self.load()
        return entries[-n:]

    def today(self) -> list[LogEntry]:
        """Return all entries from today (local date)."""
        today = datetime.now().date()
        return [e for e in self.load() if e.timestamp.date() == today]

    def by_operation(self, op: LogOperation) -> list[LogEntry]:
        """Return all entries of a specific operation type."""
        return [e for e in self.load() if e.operation == op]
