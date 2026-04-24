"""
Query pipeline — search the wiki and synthesize an answer via LLM.

Flow:
    1. Read index.md → keyword search for relevant pages
    2. Load top-k page bodies
    3. LLM synthesizes answer with citations
    4. Optionally save the answer as a new wiki page
    5. Log the query + curiosity event
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mymem.observability.logger import get_logger, set_run_id
from mymem.pipeline.router import ModelRouter
from mymem.security.sanitize import sanitize_query
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import read_page, slug_to_path, write_page
from mymem.wiki.types import IndexEntry, LogEntry, LogOperation, TagDomain, WikiPage

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    question:   str
    answer:     str
    citations:  list[str] = field(default_factory=list)
    saved_to:   str | None = None


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_QA_SYSTEM = """\
You are a research assistant with access to a personal wiki. Answer the question
using ONLY the provided wiki pages. Cite your sources using [[Page Title]] format.
If the wiki does not contain enough information, say so clearly.
Be concise and direct. Use markdown formatting in your answer.
"""


def _qa_prompt(question: str, pages_content: list[tuple[str, str]]) -> str:
    context_parts = [
        f"=== {title} ===\n{body}"
        for title, body in pages_content
    ]
    context = "\n\n".join(context_parts)
    return f"Question: {question}\n\nWiki pages:\n\n{context}"


# ---------------------------------------------------------------------------
# Core query function
# ---------------------------------------------------------------------------

async def query_wiki(
    question: str,
    *,
    wiki_dir: Path,
    index_path: Path,
    log_path: Path,
    router: ModelRouter,
    top_k: int = 5,
    save: bool = False,
    domain_filter: TagDomain | None = None,
) -> QueryResult:
    """
    Answer a question using the wiki.

    Args:
        question:      The user's question.
        wiki_dir:      Path to the wiki/ directory.
        index_path:    Path to index.md.
        log_path:      Path to log.md.
        router:        ModelRouter instance.
        top_k:         Maximum number of wiki pages to include as context.
        save:          If True, save the answer as a new wiki page.
        domain_filter: Optional domain to restrict search.
    """
    run_id = set_run_id()
    log.info("Query started", question=question[:80], top_k=top_k,
             domain=domain_filter.value if domain_filter else "any", run_id=run_id)

    # Check for injection — raises ValueError on HIGH risk, keeps original for search/log
    safe_question, _risk = sanitize_query(question)

    index_mgr = IndexManager(index_path)

    # 1. Find relevant pages
    log.debug("Searching index", question=question[:60])
    candidates = index_mgr.search(question, top_k=top_k * 2)
    if domain_filter:
        candidates = [e for e in candidates if e.domain == domain_filter]
    candidates = candidates[:top_k]
    log.info("Pages found", count=len(candidates),
             titles=[e.title for e in candidates])

    # 2. Load page bodies
    pages_content: list[tuple[str, str]] = []
    citations: list[str] = []

    for entry in candidates:
        page_path = wiki_dir / entry.path if not Path(entry.path).is_absolute() else Path(entry.path)
        try:
            page = read_page(page_path)
            pages_content.append((page.title, page.body))
            citations.append(page.title)
        except FileNotFoundError:
            log.warning("Index entry missing on disk", title=entry.title, path=str(page_path))
            continue

    # 3. Synthesize answer
    if pages_content:
        log.info("Synthesizing answer", pages=len(pages_content))
        answer = await router.call(
            _qa_prompt(safe_question, pages_content),
            task="qa",
            system=_QA_SYSTEM,
        )
        log.debug("Answer synthesized", chars=len(answer))
    else:
        log.warning("No relevant pages found — returning empty-wiki response")
        answer = (
            "The wiki does not contain any pages relevant to this question yet. "
            "Try ingesting some sources on this topic first."
        )

    result = QueryResult(question=question, answer=answer, citations=citations)

    # 4. Optionally save the answer as a wiki page
    if save and answer:
        saved_path = slug_to_path(wiki_dir, f"qa-{question[:40]}")
        log.info("Saving answer as wiki page", path=str(saved_path))
        page = WikiPage(
            title=f"Q: {question[:80]}",
            body=f"# Q: {question}\n\n{answer}\n\n## Sources\n\n"
                 + "\n".join(f"- [[{c}]]" for c in citations),
            path=saved_path,
            tags=["qa", "query"],
            domain=TagDomain.MISC,
        )
        write_page(page)
        result.saved_to = str(saved_path)

        index_mgr.upsert(IndexEntry(
            title=page.title,
            path=saved_path.relative_to(wiki_dir) if wiki_dir in saved_path.parents else saved_path,
            summary=question[:120],
            category="qa",
            domain=TagDomain.MISC,
        ))

    # 5. Log the query
    wiki_log = WikiLog(log_path)
    wiki_log.append(LogEntry(
        operation=LogOperation.QUERY,
        description=question[:120],
        affected_pages=(result.saved_to,) if result.saved_to else (),
    ))

    log.info("Query complete", citations=len(citations),
             saved=bool(result.saved_to), cost=f"${router.session_cost:.4f}")
    return result
