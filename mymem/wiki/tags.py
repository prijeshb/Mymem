"""
Tag taxonomy — domain registry, tag normalisation, and Dataview snippets.

Domain is a required field on every wiki page. Tags are free-form but always
lowercase and stripped. This module provides helpers for both.
"""

from __future__ import annotations

from mymem.wiki.types import TagDomain


# ---------------------------------------------------------------------------
# Domain keyword hints (used by LLM classifier prompt)
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS: dict[TagDomain, list[str]] = {
    TagDomain.SPIRITUAL: [
        "meditation", "stoicism", "philosophy", "mindfulness",
        "religion", "consciousness", "spirituality", "yoga",
        "buddhism", "taoism", "ethics", "virtue",
    ],
    TagDomain.TECH: [
        "ml", "ai", "python", "programming", "systems", "database",
        "devops", "security", "software", "algorithm", "architecture",
        "cloud", "api", "llm", "neural", "code",
    ],
    TagDomain.FINANCE: [
        "investing", "crypto", "tax", "budgeting", "markets",
        "trading", "portfolio", "stocks", "bonds", "economy",
        "money", "savings", "returns",
    ],
    TagDomain.HEALTH: [
        "fitness", "nutrition", "sleep", "mental-health", "therapy",
        "exercise", "diet", "wellness", "workout", "recovery",
        "mindset", "habit",
    ],
    TagDomain.REMINDER: [
        "todo", "follow-up", "deadline", "action-item", "note-to-self",
        "task", "reminder", "checklist", "due",
    ],
    TagDomain.RESEARCH: [
        "paper", "study", "hypothesis", "experiment", "literature",
        "analysis", "methodology", "findings", "abstract", "citation",
    ],
    TagDomain.PERSONAL: [
        "journal", "goals", "reflection", "relationships", "identity",
        "growth", "values", "memory", "biography", "diary",
    ],
    TagDomain.CREATIVE: [
        "writing", "design", "music", "art", "fiction",
        "poetry", "sketch", "story", "craft", "photography",
    ],
    TagDomain.BUSINESS: [
        "strategy", "product", "marketing", "ops", "startup",
        "management", "leadership", "sales", "growth", "revenue",
    ],
}


# ---------------------------------------------------------------------------
# Tag normalisation
# ---------------------------------------------------------------------------

def normalize_tag(tag: str) -> str:
    """Lowercase, strip whitespace, replace spaces with hyphens."""
    return tag.strip().lower().replace(" ", "-")


def normalize_tags(tags: list[str]) -> list[str]:
    """Normalize a list of tags, drop empty strings, deduplicate."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        n = normalize_tag(t)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def infer_domain(tags: list[str], title: str = "", body: str = "") -> TagDomain:
    """
    Heuristic domain inference from tags, title, and body text.
    Returns the domain with the most keyword hits; falls back to MISC.

    The LLM pipeline uses this as a fallback when no --domain is specified.
    For accurate classification the router should use the classify model.
    """
    text = " ".join(tags + [title, body]).lower()
    scores: dict[TagDomain, int] = {d: 0 for d in TagDomain}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[domain] += 1
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else TagDomain.MISC


# ---------------------------------------------------------------------------
# Dataview snippet generator (for embedding in Obsidian pages)
# ---------------------------------------------------------------------------

def dataview_table(
    domain: TagDomain | None = None,
    limit: int = 20,
    sort_by: str = "updated",
) -> str:
    """
    Generate a Dataview query block for embedding in an Obsidian page.

    Example output (domain=tech):
        ```dataview
        TABLE sources, tags, updated
        FROM #tech
        SORT updated DESC
        LIMIT 20
        ```
    """
    from_clause = f"FROM #{domain.value}" if domain else 'FROM ""'
    return (
        "```dataview\n"
        f"TABLE sources, tags, {sort_by}\n"
        f"{from_clause}\n"
        f"SORT {sort_by} DESC\n"
        f"LIMIT {limit}\n"
        "```"
    )


def domain_from_str(value: str) -> TagDomain:
    """Parse a string into a TagDomain, falling back to MISC."""
    try:
        return TagDomain(value.strip().lower())
    except ValueError:
        return TagDomain.MISC
