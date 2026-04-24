"""
Ingest pipeline — raw source → wiki pages via LLM.

Flow:
    1. Security scan (block on HIGH-severity secrets)
    2. Read source text (file or URL)
    3. Router selects model; if too long → ChunkSplitter splits it
    4. LLM extracts key ideas per chunk
    5. LLM writes/updates wiki pages
    6. index.md updated
    7. curiosity event logged
    8. log.md appended
"""

from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Awaitable

from mymem.observability.logger import get_logger, set_run_id
from mymem.pipeline.router import ModelRouter
from mymem.pipeline.splitter import ChunkSplitter, merge_prompt, merge_system_prompt
from mymem.security.sanitize import sanitize_for_prompt
from mymem.security.scanner import has_high_severity_secret
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import read_page, slug_to_path, write_page
from mymem.wiki.tags import domain_from_str, normalize_tags
from mymem.wiki.types import IndexEntry, LogEntry, LogOperation, TagDomain, WikiPage

log = get_logger(__name__)

try:
    import trafilatura as trafilatura  # type: ignore[import-untyped]
except ImportError:
    trafilatura = None  # type: ignore[assignment]

try:
    from youtube_transcript_api import YouTubeTranscriptApi as YouTubeTranscriptApi  # type: ignore[import-untyped]
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound  # type: ignore[import-untyped]
    _YT_AVAILABLE = True
except ImportError:
    YouTubeTranscriptApi = None  # type: ignore[assignment,misc]
    _YT_AVAILABLE = False

    class TranscriptsDisabled(Exception):  # type: ignore[no-redef]
        """Sentinel — real class lives in youtube_transcript_api."""

    class NoTranscriptFound(Exception):  # type: ignore[no-redef]
        """Sentinel — real class lives in youtube_transcript_api."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class IngestResult:
    source_path:   str
    pages_written: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    chunk_count:   int = 1
    skipped:       bool = False
    skip_reason:   str = ""


# ---------------------------------------------------------------------------
# Source reading
# ---------------------------------------------------------------------------

async def _read_source(source: str, source_type: str = "article") -> str:
    """Read text from a local file path or HTTP(S) URL.

    Dispatches based on source_type:
      youtube    → fetch transcript via youtube-transcript-api
      webpage    → HTTP fetch + HTML → plain-text stripping
      podcast    → HTTP fetch + HTML strip (RSS/show-notes page)
      tweet      → HTTP fetch + HTML strip
      pdf / book → pypdf text extraction (file path only)
      *          → plain file read
    """
    # --- YouTube ----------------------------------------------------------
    if source_type == "youtube" or _is_youtube_url(source):
        return await _read_youtube(source)

    # --- Generic URL (webpage / podcast / tweet / article) ----------------
    if source.startswith(("http://", "https://")):
        # trafilatura.fetch_url handles fetch + decompression + encoding
        # detection + extraction in one call — no need to manage httpx,
        # brotli, charset guessing, or BeautifulSoup ourselves.
        if trafilatura is not None:
            text = trafilatura.fetch_url(source)
            if text and len(text.strip()) > 100:
                log.info("trafilatura fetched URL", chars=len(text), url=source)
                return text.strip()
            log.warning("trafilatura returned no content for URL", url=source)
        else:
            log.warning("trafilatura not installed — falling back to httpx. "
                        "Run: pip install trafilatura")

        # httpx fallback (plain-text / JSON responses, or no trafilatura)
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError("httpx or trafilatura required for URL ingestion") from e
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            resp = await client.get(source)
            if resp.status_code == 403:
                raise RuntimeError(
                    f"Access denied (403) for {source}. "
                    "Try pasting the article text directly via 'Paste text'."
                )
            resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            return _html_to_text(resp.content)
        return resp.content.decode("utf-8", errors="replace")

    # --- Local file -------------------------------------------------------
    path = Path(source)
    if path.suffix.lower() == ".pdf" or source_type == "book":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("pypdf required for PDF/book ingestion") from e
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Source-type helpers
# ---------------------------------------------------------------------------

def _is_youtube_url(url: str) -> bool:
    """Return True if *url* looks like a YouTube video link."""
    return (
        "youtube.com/watch" in url
        or "youtu.be/" in url
        or "youtube.com/embed/" in url
        or "youtube.com/shorts/" in url
    )


def _extract_video_id(url: str) -> str | None:
    """Extract the 11-char video ID from any YouTube URL form."""
    import re
    patterns = [
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


async def _read_youtube(url: str) -> str:
    """Fetch a YouTube video transcript as plain text.

    Tries English first, falls back to any available language.
    Raises RuntimeError if transcripts are disabled or the package is not installed.
    """
    if not _YT_AVAILABLE:
        raise RuntimeError(
            "youtube-transcript-api is required for YouTube ingestion.\n"
            "Install it with:  pip install youtube-transcript-api"
        )

    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Cannot extract YouTube video ID from URL: {url!r}")

    log.info("Fetching YouTube transcript", video_id=video_id)
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except TranscriptsDisabled:
        raise RuntimeError(f"Transcripts are disabled for YouTube video: {video_id}")
    except NoTranscriptFound:
        try:
            transcript_list = api.list(video_id)
            transcript = next(iter(transcript_list))
            fetched = transcript.fetch()
        except StopIteration:
            raise RuntimeError(f"No transcripts available for video: {video_id}")

    lines = [s.text for s in fetched if s.text.strip()]
    full_text = " ".join(lines)
    log.info("YouTube transcript fetched", video_id=video_id, chars=len(full_text))
    return f"[YouTube transcript — video ID: {video_id}]\n\n{full_text}"


def _html_to_text(html: bytes | str) -> str:
    """Extract readable article text from HTML via trafilatura or BeautifulSoup."""
    if trafilatura is not None:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                output_format="txt",
            )
        except TypeError:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
        if text and text.strip():
            log.info("trafilatura extracted text", chars=len(text))
            return text.strip()
    else:
        log.warning("trafilatura not installed — falling back to BeautifulSoup. "
                    "Run: pip install trafilatura")

    html_str: str = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        import re
        return re.sub(r"<[^>]+>", " ", html_str)

    soup = BeautifulSoup(html_str, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM_TMPL = """\
You are a knowledge curator. Given a source document, extract the key ideas,
concepts, and facts that are worth preserving in a personal wiki.
Output a JSON array of objects, each with:
  "title": short page title (3-6 words)
  "summary": 2-3 sentences covering the core insight, key facts or numbers, and why it matters
  "tags": list of lowercase tags
  "domain": one of spiritual|tech|finance|health|reminder|research|personal|creative|business|misc
Do not include more than {max_concepts} ideas. Output only valid JSON.
"""

_SOURCE_TYPE_HINTS: dict[str, str] = {
    "article":    "a written article or blog post",
    "paper":      "an academic or research paper",
    "repo":       "a code repository or technical project",
    "dataset":    "a data file or dataset",
    "image":      "an image or visual document",
    "youtube":    "a YouTube video transcript",
    "podcast":    "a podcast episode or show notes",
    "tweet":      "a tweet or Twitter/X thread",
    "webpage":    "a general web page",
    "book":       "a book or long-form text",
    "newsletter": "an email newsletter",
    "note":       "a personal note or journal entry",
}

_COMPILE_SYSTEM = """\
You are a wiki author. Given source material and a concept to document,
write a wiki page in markdown with YAML frontmatter.

Frontmatter fields required: title, domain, tags, sources.
Do NOT include created or updated — those are set by the system.
Body: use ## headings, include [[wikilinks]] to related concepts.
Output only the markdown — no commentary.
"""


def _extract_prompt(
    source_text: str,
    source_name: str,
    source_type: str = "article",
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> str:
    hint = _SOURCE_TYPE_HINTS.get(source_type, f"a {source_type}")
    section_note = f" (part {chunk_index} of {total_chunks})" if total_chunks > 1 else ""
    header = f"Source: {source_name}{section_note}\nType: {hint}\n"
    return f"{header}\n---\n{source_text}\n---"


def _compile_prompt(idea_title: str, idea_summary: str, source_text: str, domain: str) -> str:
    preview = textwrap.shorten(source_text, width=6000, placeholder="...")
    return (
        f"Write a wiki page for: {idea_title}\n"
        f"Summary hint: {idea_summary}\n"
        f"Domain: {domain}\n\n"
        f"Source material:\n{preview}"
    )


# ---------------------------------------------------------------------------
# Core ingest function
# ---------------------------------------------------------------------------

async def ingest_source(
    source: str,
    *,
    wiki_dir: Path,
    index_path: Path,
    log_path: Path,
    router: ModelRouter,
    source_type: str = "article",
    tags: list[str] | None = None,
    domain: str = "",
    title_hint: str | None = None,
    max_concepts: int = 3,
) -> IngestResult:
    """
    Ingest a source document into the wiki.

    Args:
        source:      File path or URL.
        wiki_dir:    Path to the wiki/ directory.
        index_path:  Path to index.md.
        log_path:    Path to log.md.
        router:      ModelRouter instance (inject a mocked one in tests).
        source_type: article | paper | repo | dataset | image
        tags:        Optional tag override list.
        domain:      Optional domain override string.
        title_hint:  Optional page title override.
    """
    run_id = set_run_id()
    source_name = Path(source).name if not source.startswith("http") else source
    log.info("Ingest started", source=source_name, run_id=run_id,
             source_type=source_type, domain=domain, tags=tags)

    # 1. Read source
    log.debug("Reading source", source=source)
    source_text = await _read_source(source, source_type=source_type)
    log.info("Source read", source=source_name, chars=len(source_text))

    # 2. Security scan
    log.debug("Scanning for secrets", source=source_name)
    if has_high_severity_secret(source_text):
        log.warning("HIGH-severity secret detected — skipping", source=source_name)
        return IngestResult(
            source_path=source,
            skipped=True,
            skip_reason="HIGH-severity secret detected in source — ingestion blocked",
        )

    # Sanitize source text before it enters any LLM prompt
    source_text, ingest_risk = sanitize_for_prompt(source_text)
    if ingest_risk.matched_patterns:
        log.warning(
            "Prompt injection patterns detected in source",
            source=source_name,
            risk=ingest_risk.level,
            patterns=ingest_risk.matched_patterns,
        )

    # 3. Extract ideas (split if needed)
    splitter = ChunkSplitter(max_tokens=6000)
    chunks = splitter.split(source_text)
    chunk_count = len(chunks)
    log.info("Extracting ideas", source=source_name, chunks=chunk_count)

    extract_system = _EXTRACT_SYSTEM_TMPL.format(max_concepts=max_concepts)
    all_ideas: list[dict[str, object]] = []
    for i, chunk in enumerate(chunks, 1):
        log.info("Extracting chunk", chunk=i, of=chunk_count, chars=len(chunk))
        user_prompt = _extract_prompt(
            chunk, source_name,
            source_type=source_type,
            chunk_index=i,
            total_chunks=chunk_count,
        )
        raw = await router.call(
            user_prompt,
            task="compile",
            system=extract_system,
        )
        ideas = _parse_ideas(raw)
        if not ideas:
            dump_path = Path(__file__).parents[2] / "data" / f"debug_chunk_{i}_of_{chunk_count}.txt"
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            dump_path.write_text(
                f"=== SYSTEM PROMPT ===\n{extract_system}\n\n"
                f"=== USER PROMPT ===\n{user_prompt}\n\n"
                f"=== LLM RESPONSE ===\n{raw}\n",
                encoding="utf-8",
            )
            log.warning(
                "Chunk produced no ideas — full prompt+response dumped to file",
                chunk=i, of=chunk_count,
                dump_file=str(dump_path.resolve()),
            )
        else:
            log.info("Chunk ideas extracted", chunk=i, count=len(ideas))
        all_ideas.extend(ideas)

    if not all_ideas:
        log.warning("No ideas extracted — skipping", source=source_name)
        return IngestResult(
            source_path=source, skipped=True,
            skip_reason="LLM returned no extractable ideas",
        )

    log.info("Ideas extracted", source=source_name, total=len(all_ideas))

    # 4. Compile each idea into a wiki page
    index_mgr  = IndexManager(index_path)
    wiki_log   = WikiLog(log_path)
    result     = IngestResult(source_path=source, chunk_count=chunk_count)
    inferred_domain = domain_from_str(domain) if domain else TagDomain.MISC
    extra_tags = normalize_tags(tags or [])

    for idx, idea in enumerate(all_ideas[:max_concepts], 1):
        idea_title   = str(idea.get("title", "Untitled"))
        idea_summary = str(idea.get("summary", ""))
        idea_tags    = normalize_tags(
            [str(t) for t in (idea.get("tags") or [])] + extra_tags
        )
        idea_domain = (
            inferred_domain
            if inferred_domain != TagDomain.MISC
            else domain_from_str(str(idea.get("domain", "misc")))
        )

        page_path = slug_to_path(wiki_dir, idea_title)
        is_update = page_path.exists()
        log.info(
            f"Compiling page {idx}/{min(len(all_ideas), 10)}",
            title=idea_title, domain=idea_domain.value,
            action="update" if is_update else "create",
        )

        # Compile the wiki page body via LLM
        page_body = await router.call(
            _compile_prompt(idea_title, idea_summary, source_text, idea_domain.value),
            task="compile",
            system=_COMPILE_SYSTEM,
        )
        log.debug("Page compiled", title=idea_title, chars=len(page_body))

        page_body = _strip_frontmatter(page_body)
        existing_created = date.today()
        if is_update:
            try:
                existing_created = read_page(page_path).created
            except Exception:
                pass

        page = WikiPage(
            title=idea_title,
            body=page_body,
            path=page_path,
            tags=idea_tags,  # type: ignore[arg-type]
            sources=[source_name],
            domain=idea_domain,
            created=existing_created,
        )
        write_page(page)
        log.debug("Page written", path=str(page_path))

        # Update index
        index_mgr.upsert(IndexEntry(
            title=idea_title,
            path=page_path.relative_to(wiki_dir) if wiki_dir in page_path.parents else page_path,
            summary=idea_summary,
            category=idea_domain.value,
            source_count=1,
            domain=idea_domain,
            tags=tuple(idea_tags),
        ))

        if is_update:
            result.pages_updated.append(idea_title)
        else:
            result.pages_written.append(idea_title)

    # 5. Log the ingest operation
    all_touched = result.pages_written + result.pages_updated
    wiki_log.append(LogEntry(
        operation=LogOperation.INGEST,
        description=source_name,
        affected_pages=tuple(all_touched),
    ))

    log.info(
        "Ingest complete", source=source_name,
        pages_written=len(result.pages_written),
        pages_updated=len(result.pages_updated),
        chunks=chunk_count, cost=f"${router.session_cost:.4f}",
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ideas(raw: str) -> list[dict[str, object]]:
    """Parse LLM JSON output into a list of idea dicts."""
    import json, re
    cleaned = raw.strip()
    # Strip <think>…</think> reasoning blocks (emitted by thinking models)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return [d for d in v if isinstance(d, dict)]
    except (json.JSONDecodeError, ValueError):
        log.debug("_parse_ideas: JSON parse failed", raw_preview=raw[:200])
    return []


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block if the LLM accidentally included it."""
    import re
    return re.sub(r"^---\n.*?\n---\n?", "", text, flags=re.DOTALL).lstrip()
