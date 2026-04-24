"""
Introspect pipeline — daily summary and curiosity-driven reading suggestions.

Three modes:
    daily       No args — summarises today's log + saves wiki/daily/YYYY-MM-DD.md
    suggest     --topic "X" — ranks relevant past pages using curiosity weights
    ambient     Embedded in daily — gap suggestions, revisit suggestions

Curiosity weight decay:
    weight = Σ exp(-0.1 * days_ago)   half-life ≈ 7 days
    Stored per (domain, tag) in data/curiosity.db
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from mymem.pipeline.router import ModelRouter
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import list_pages, slug_to_path, write_page
from mymem.wiki.types import LogEntry, LogOperation, TagDomain, WikiPage


# ---------------------------------------------------------------------------
# Curiosity DB
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS curiosity_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT 'misc',
    tags       TEXT NOT NULL DEFAULT '[]',
    page_slug  TEXT,
    query_text TEXT
);

CREATE TABLE IF NOT EXISTS topic_weights (
    domain     TEXT NOT NULL,
    tag        TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 0.0,
    last_seen  TIMESTAMP NOT NULL,
    PRIMARY KEY (domain, tag)
);
"""

_DECAY_LAMBDA = 0.1  # half-life ≈ 7 days


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def log_curiosity_event(
    db_path: Path,
    event_type: str,
    domain: TagDomain,
    tags: list[str],
    page_slug: str | None = None,
    query_text: str | None = None,
) -> None:
    """Record a curiosity event and update topic weights."""
    conn = _open_db(db_path)
    now = datetime.now()
    try:
        conn.execute(
            "INSERT INTO curiosity_events (ts, event_type, domain, tags, page_slug, query_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now.isoformat(), event_type, domain.value, json.dumps(tags), page_slug, query_text),
        )
        # Update weights
        for tag in tags:
            existing = conn.execute(
                "SELECT weight, last_seen FROM topic_weights WHERE domain=? AND tag=?",
                (domain.value, tag),
            ).fetchone()
            if existing:
                days_ago = (now - datetime.fromisoformat(existing["last_seen"])).days
                decayed = existing["weight"] * math.exp(-_DECAY_LAMBDA * days_ago)
                new_weight = decayed + 1.0
            else:
                new_weight = 1.0
            conn.execute(
                "INSERT INTO topic_weights (domain, tag, weight, last_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(domain, tag) DO UPDATE SET weight=excluded.weight, last_seen=excluded.last_seen",
                (domain.value, tag, new_weight, now.isoformat()),
            )
        conn.commit()
    finally:
        conn.close()


def top_interests(db_path: Path, limit: int = 10) -> list[dict[str, object]]:
    """Return top (domain, tag) pairs by current weight."""
    if not db_path.exists():
        return []
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT domain, tag, weight FROM topic_weights ORDER BY weight DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"domain": r["domain"], "tag": r["tag"], "weight": r["weight"]} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    page_title:  str
    reason:      str
    last_seen:   date | None = None


@dataclass
class IntrospectResult:
    target_date:     date
    summary:         str
    generated_at:    datetime = field(default_factory=datetime.now)
    recommendations: list[Recommendation] = field(default_factory=list)
    saved_to:        str | None = None
    top_interests:   list[dict[str, object]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = """\
You are a personal knowledge assistant. Summarise the user's learning activity
for the given day based on the log entries and wiki pages they worked on.
Write 2-3 paragraphs. Be specific about what was explored and what connections emerged.
End with one sentence about what might be interesting to explore next.
"""


def _summary_prompt(log_entries: list[LogEntry], page_titles: list[str]) -> str:
    entry_lines = [
        f"- [{e.timestamp.strftime('%H:%M')}] {e.operation.value}: {e.description}"
        for e in log_entries
    ]
    return (
        "Log entries for today:\n" + "\n".join(entry_lines) + "\n\n"
        "Pages touched: " + ", ".join(page_titles)
    )


_SUGGEST_SYSTEM = """\
You are a research advisor. Based on the user's curiosity profile and research topic,
suggest 3-5 existing wiki pages they should revisit. Explain briefly why each is relevant.
Format: one bullet per page, "[[Page Title]] — reason"
"""


def _suggest_prompt(topic: str, interests: list[dict[str, object]], page_titles: list[str]) -> str:
    interest_str = ", ".join(
        f"{i['domain']}/{i['tag']} ({i['weight']:.1f})" for i in interests[:10]
    )
    return (
        f"Research topic: {topic}\n\n"
        f"User's top interests: {interest_str}\n\n"
        f"Available wiki pages: {', '.join(page_titles[:50])}"
    )


# ---------------------------------------------------------------------------
# Core introspect function
# ---------------------------------------------------------------------------

def _load_cached_summary(daily_dir: Path, target: date) -> IntrospectResult | None:
    """Return a cached IntrospectResult if wiki/daily/YYYY-MM-DD.md exists."""
    page_path = daily_dir / f"{target.isoformat()}.md"
    if not page_path.exists():
        return None
    try:
        text = page_path.read_text(encoding="utf-8")
        # Strip YAML frontmatter
        import re
        body = re.sub(r"^---\n.*?\n---\n?", "", text, flags=re.DOTALL).lstrip()
        # Extract generated_at from the heading "till HH:MM"
        generated_at = datetime.now().replace(hour=0, minute=0, second=0)
        m = re.search(r"till (\d{2}:\d{2})", body)
        if m:
            h, mn = map(int, m.group(1).split(":"))
            generated_at = datetime.combine(target, datetime.min.time().replace(hour=h, minute=mn))
        # Split off recommendations section
        parts = re.split(r"^## Reading Suggestions\s*$", body, maxsplit=1, flags=re.MULTILINE)
        summary_body = parts[0].strip()
        recs: list[Recommendation] = []
        if len(parts) > 1:
            for line in parts[1].splitlines():
                rm = re.match(r"-\s+\[\[(.+?)\]\]\s+[—–-]\s+(.+)", line)
                if rm:
                    recs.append(Recommendation(page_title=rm.group(1), reason=rm.group(2).strip()))
        return IntrospectResult(
            target_date=target,
            summary=summary_body,
            generated_at=generated_at,
            recommendations=recs,
            saved_to=str(page_path),
        )
    except Exception:
        return None


async def introspect(
    *,
    wiki_dir: Path,
    index_path: Path,
    log_path: Path,
    curiosity_db: Path,
    router: ModelRouter,
    target_date: date | None = None,
    topic: str | None = None,
    save: bool = True,
    force: bool = False,
) -> IntrospectResult:
    """
    Run the introspect pipeline.

    Args:
        wiki_dir:     Path to wiki/ directory.
        index_path:   Path to index.md.
        log_path:     Path to log.md.
        curiosity_db: Path to curiosity.db.
        router:       ModelRouter instance.
        target_date:  Date to summarise (defaults to today).
        topic:        If provided, run research suggestion mode.
        save:         If True, save daily summary to wiki/daily/.
        force:        If True, skip cache and regenerate even if saved summary exists.
    """
    target = target_date or date.today()

    # Return cached summary if available and not forcing regeneration
    if not topic and not force:
        daily_dir = wiki_dir / "daily"
        cached = _load_cached_summary(daily_dir, target)
        if cached is not None:
            cached.top_interests = top_interests(curiosity_db)
            return cached
    wiki_log = WikiLog(log_path)
    interests = top_interests(curiosity_db)

    # Research suggestion mode
    if topic:
        pages = list_pages(wiki_dir)
        page_titles = [p.title for p in pages]
        suggestion_text = await router.call(
            _suggest_prompt(topic, interests, page_titles),
            task="introspect",
            system=_SUGGEST_SYSTEM,
        )
        return IntrospectResult(
            target_date=target,
            summary=suggestion_text,
            generated_at=datetime.now(),
            top_interests=interests,
        )

    # Daily summary mode
    all_entries = wiki_log.load()
    day_entries = [
        e for e in all_entries
        if e.timestamp.date() == target
    ]

    # Collect titles of pages touched today
    touched_titles: list[str] = []
    for entry in day_entries:
        touched_titles.extend(entry.affected_pages)
    touched_titles = list(dict.fromkeys(touched_titles))  # deduplicate, preserve order

    all_pages = list_pages(wiki_dir)

    if not day_entries and not all_pages:
        summary = f"No activity recorded for {target.isoformat()} and no wiki pages found yet."
    elif not day_entries:
        # No log entries today — summarise the existing wiki instead
        recent_pages = sorted(all_pages, key=lambda p: p.updated, reverse=True)[:10]
        page_titles = [p.title for p in recent_pages]
        summary = await router.call(
            (
                f"No log entries for {target.isoformat()}.\n\n"
                f"Here are the most recently updated wiki pages:\n"
                + "\n".join(f"- {t}" for t in page_titles)
                + "\n\nWrite a brief overview of what this knowledge base covers "
                  "and suggest what would be interesting to explore next."
            ),
            task="introspect",
            system=_SUMMARY_SYSTEM,
        )
    else:
        summary = await router.call(
            _summary_prompt(day_entries, touched_titles),
            task="introspect",
            system=_SUMMARY_SYSTEM,
        )

    # Build ambient recommendations
    recs = _build_recommendations(all_pages, day_entries, interests)

    generated_at = datetime.now()
    result = IntrospectResult(
        target_date=target,
        summary=summary,
        generated_at=generated_at,
        recommendations=recs,
        top_interests=interests,
    )

    # Save daily summary page
    if save:
        daily_dir = wiki_dir / "daily"
        daily_dir.mkdir(exist_ok=True)
        till_str  = generated_at.strftime("%H:%M")
        page_path = daily_dir / f"{target.isoformat()}.md"
        rec_lines = "\n".join(
            f"- [[{r.page_title}]] — {r.reason}" for r in recs
        )
        body = (
            f"# Daily Summary — {target.isoformat()} till {till_str}\n\n"
            f"{summary}\n\n"
            f"## Reading Suggestions\n\n{rec_lines or '_No suggestions today._'}"
        )
        page = WikiPage(
            title=f"Daily Summary {target.isoformat()} till {till_str}",
            body=body,
            path=page_path,
            tags=["daily", "introspect"],
            domain=TagDomain.PERSONAL,
        )
        write_page(page)
        result.saved_to = str(page_path)

    # Log the introspect operation
    wiki_log.append(LogEntry(
        operation=LogOperation.INTROSPECT,
        description=f"Daily summary for {target.isoformat()}",
    ))

    return result


# ---------------------------------------------------------------------------
# Recommendation helpers
# ---------------------------------------------------------------------------

_REVISIT_DAYS = 14


def _build_recommendations(
    pages: list,
    day_entries: list[LogEntry],
    interests: list[dict[str, object]],
) -> list[Recommendation]:
    """Build ambient recommendations from wiki pages + curiosity profile."""
    recs: list[Recommendation] = []

    today = date.today()
    top_tags: set[str] = {str(i["tag"]) for i in interests[:5]}

    for page in pages:
        page_tags = set(page.tags)

        # Revisit: page not recently touched but matches interests
        if page_tags & top_tags and page.updated < today - timedelta(days=_REVISIT_DAYS):
            recs.append(Recommendation(
                page_title=page.title,
                reason=f"Matches your interests ({', '.join(page_tags & top_tags)}) "
                       f"but not revisited in {(today - page.updated).days} days",
                last_seen=page.updated,
            ))

    # Cap at 5 recommendations, prioritise most stale
    recs.sort(key=lambda r: r.last_seen or date.min)
    return recs[:5]
