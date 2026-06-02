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

splitter = ChunkSplitter(max_tokens=1024)
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
    rag_only:      bool = False
    rag_chunks:    int = 0


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


async def _fetch_youtube_metadata(url: str) -> dict:
    """Fetch video metadata (title, description, chapters, …) via yt-dlp.

    Returns an empty dict if yt-dlp is not installed or the fetch fails.
    Runs the blocking yt-dlp call in a thread executor so the event loop
    stays responsive while waiting.
    """
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError:
        return {}

    def _extract() -> dict:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info or {}

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _extract)
    except Exception as exc:
        log.warning("yt-dlp metadata fetch failed", url=url, error=str(exc))
        return {}


def _format_duration(seconds: int | float) -> str:
    """Convert seconds to H:MM:SS or M:SS string."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _format_chapters(chapters: list[dict]) -> str:
    """Format chapter list as 'MM:SS  Title' lines."""
    return "\n".join(
        f"{_format_duration(ch.get('start_time', 0))}  {ch.get('title', '')}"
        for ch in chapters
    )


def _build_youtube_context(info: dict, video_id: str, transcript: str) -> str:
    """Combine yt-dlp metadata with transcript into a rich context string for the LLM."""
    parts: list[str] = []

    title        = info.get("title", "")
    channel      = info.get("uploader") or info.get("channel", "")
    upload_date  = info.get("upload_date", "")      # "YYYYMMDD"
    duration     = info.get("duration")
    description  = (info.get("description") or "").strip()
    chapters     = info.get("chapters") or []

    # Header block
    header = f"[YouTube Video — ID: {video_id}]"
    if title:
        header += f"\nTitle: {title}"
    meta: list[str] = []
    if channel:
        meta.append(f"Channel: {channel}")
    if upload_date and len(upload_date) == 8:
        meta.append(f"Published: {upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}")
    if duration is not None:
        meta.append(f"Duration: {_format_duration(duration)}")
    if meta:
        header += "\n" + "  |  ".join(meta)
    parts.append(header)

    if description:
        preview = description[:1500] + ("..." if len(description) > 1500 else "")
        parts.append(f"Description:\n{preview}")

    if chapters:
        parts.append(f"Chapters:\n{_format_chapters(chapters)}")

    parts.append(f"Transcript:\n{transcript}")
    return "\n\n".join(parts)


async def _read_youtube(url: str) -> str:
    """Fetch YouTube transcript enriched with video metadata when yt-dlp is available.

    Metadata (title, description, chapters, channel, date) is fetched via yt-dlp
    concurrently with the transcript to keep total latency low.
    Falls back to transcript-only output if yt-dlp is not installed.
    """
    if not _YT_AVAILABLE:
        raise RuntimeError(
            "youtube-transcript-api is required for YouTube ingestion.\n"
            "Install it with:  pip install youtube-transcript-api"
        )

    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Cannot extract YouTube video ID from URL: {url!r}")

    log.info("Fetching YouTube video", video_id=video_id)

    # Kick off metadata fetch in background while transcript fetch runs
    metadata_task = asyncio.ensure_future(_fetch_youtube_metadata(url))

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except TranscriptsDisabled:
        metadata_task.cancel()
        raise RuntimeError(f"Transcripts are disabled for YouTube video: {video_id}")
    except NoTranscriptFound:
        try:
            transcript_list = api.list(video_id)
            transcript = next(iter(transcript_list))
            fetched = transcript.fetch()
        except StopIteration:
            metadata_task.cancel()
            raise RuntimeError(f"No transcripts available for video: {video_id}")

    transcript_text = " ".join(s.text for s in fetched if s.text.strip())
    log.info("YouTube transcript fetched", video_id=video_id, chars=len(transcript_text))

    info = await metadata_task
    if info:
        log.info("YouTube metadata fetched", video_id=video_id, title=info.get("title", ""))
        return _build_youtube_context(info, video_id, transcript_text)

    # Fallback: transcript only (yt-dlp not installed or fetch failed)
    return f"[YouTube transcript — video ID: {video_id}]\n\n{transcript_text}"


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
    "youtube":    "a YouTube video (transcript with title, description, and chapter markers)",
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
    db_path: Path | None = None,
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

    # 3a. Local PDFs — skip LLM extraction entirely; just RAG-index for search
    is_local_pdf = (
        not source.startswith(("http://", "https://"))
        and Path(source).suffix.lower() == ".pdf"
    )
    if is_local_pdf:
        rag_chunks = await _rag_index_pdf(source, db_path=db_path)
        log.info("PDF RAG-indexed (search only, no wiki extraction)",
                 source=source_name, chunks=rag_chunks)
        return IngestResult(
            source_path=source,
            rag_only=True,
            rag_chunks=rag_chunks,
        )

    # 3b. Extract ideas (split if needed)
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

        # Async best-effort wiki RAG indexing (fire-and-forget; never blocks ingest)
        if db_path:
            asyncio.ensure_future(_rag_index_wiki(page_path, db_path=db_path))

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

    # Record quality analytics for all ingests
    if db_path:
        _record_ingest_analytics(
            db_path=db_path,
            source_type=source_type,
            source=source,
            source_text=source_text,
            all_ideas=all_ideas,
            result=result,
            wiki_dir=wiki_dir,
        )

    # Background extraction consensus eval (fire-and-forget, never blocks ingest)
    if db_path and all_ideas:
        asyncio.ensure_future(
            _eval_extraction_background(
                source_name=source_name,
                source_type=source_type,
                source_text=source_text,
                pipeline_ideas=all_ideas,
                router=router,
                db_path=db_path,
            )
        )

    return result


async def _rag_index_pdf(source: str, *, db_path: Path | None) -> int:
    """Index a local PDF into the RAG vector store. Returns chunk count (0 on failure)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_pdf

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_pdf(
            Path(source),
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info("RAG index: already indexed", source=source, reason=rag_result.skip_reason)
        elif rag_result.ok:
            log.info("RAG index: complete", source=source, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source, error=rag_result.error)
        return rag_result.chunk_count if rag_result.ok else 0
    except Exception as exc:
        log.warning("RAG indexing raised unexpectedly", source=source, error=str(exc))
        return 0


async def _rag_index_wiki(page_path: Path, *, db_path: Path | None) -> None:
    """Index a wiki page into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_wiki_page

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        result = await ingest_wiki_page(
            page_path,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
            force=True,
        )
        if result.ok:
            log.info("Wiki RAG indexed", path=str(page_path), chunks=result.chunk_count)
        elif not result.skipped:
            log.warning("Wiki RAG index failed", path=str(page_path), error=result.error)
    except Exception as exc:
        log.warning("Wiki RAG indexing raised unexpectedly", path=str(page_path), error=str(exc))


async def _rag_index_text(source_name: str, text: str, *, db_path: Path | None) -> None:
    """Index raw text into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_text_chunks

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_text_chunks(
            text,
            source_id=source_name,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info("RAG index: already indexed", source=source_name, reason=rag_result.skip_reason)
        elif rag_result.ok:
            log.info("RAG index: complete", source=source_name, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source_name, error=rag_result.error)
    except Exception as exc:
        log.warning("RAG text indexing raised unexpectedly — continuing", source=source_name, error=str(exc))


def _record_ingest_analytics(
    *,
    db_path: Path,
    source_type: str,
    source: str,
    source_text: str,
    all_ideas: list[dict[str, object]],
    result: "IngestResult",
    wiki_dir: Path,
) -> None:
    """Measure quality of generated pages and persist analytics record for all source types."""
    from mymem.evals.metrics import duplicate_rate
    from mymem.observability.ingest_analytics import record_ingest

    all_touched = result.pages_written + result.pages_updated
    page_chars: list[int] = []
    page_wikilinks: list[int] = []
    for title in all_touched:
        page_path = slug_to_path(wiki_dir, title)
        try:
            p = read_page(page_path)
            page_chars.append(len(p.body))
            page_wikilinks.append(len(p.wikilinks()))
        except Exception:
            pass

    avg_chars = sum(page_chars) / len(page_chars) if page_chars else 0.0
    avg_links = sum(page_wikilinks) / len(page_wikilinks) if page_wikilinks else 0.0

    # Measure duplicate ideas across chunks
    idea_summaries = [str(idea.get("summary", "")) for idea in all_ideas if idea.get("summary")]
    dup_rate = duplicate_rate(idea_summaries) if len(idea_summaries) >= 2 else 0.0

    is_youtube = source_type == "youtube" or _is_youtube_url(source)

    record_ingest(
        db_path,
        source_type=source_type,
        metadata_enriched=is_youtube and "[YouTube Video — ID:" in source_text,
        source_chars=len(source_text),
        concepts_extracted=len(all_ideas),
        pages_written=len(result.pages_written),
        pages_updated=len(result.pages_updated),
        avg_page_chars=avg_chars,
        avg_wikilinks=avg_links,
        chunk_count=result.chunk_count,
        idea_duplicate_rate=round(dup_rate, 3),
    )


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


async def _eval_extraction_background(
    *,
    source_name: str,
    source_type: str,
    source_text: str,
    pipeline_ideas: list[dict[str, object]],
    router: "ModelRouter",
    db_path: Path,
) -> None:
    """
    Fire-and-forget background task: run reference LLM extraction and score consensus.
    Skips silently if GROQ_API_KEY / GEMINI_API_KEY is not configured.
    Never raises — all errors are logged and swallowed.
    """
    try:
        from mymem.config import get_settings
        from mymem.evals.extraction_consensus import (
            GROQ_DEFAULT_MODEL,
            NVIDIA_DEFAULT_MODEL,
            run_extraction_consensus,
        )
        from mymem.evals.store import save_extraction_consensus
        from mymem.pipeline.llm import complete

        settings = get_settings()
        provider = settings.eval_reference_provider

        if provider == "groq":
            api_key = settings.groq_api_key
            ref_model = GROQ_DEFAULT_MODEL
        elif provider == "gemini":
            api_key = settings.gemini_api_key
            ref_model = "gemini-2.0-flash"
        elif provider == "nvidia":
            api_key = settings.nvidia_api_key
            ref_model = NVIDIA_DEFAULT_MODEL
        else:
            api_key = None
            ref_model = ""

        if not api_key:
            log.debug(
                "Extraction eval skipped — no API key for reference provider",
                provider=provider,
            )
            return

        async def _llm_fn(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return await complete(
                prompt,
                model=model,
                provider=provider,
                system=system,
                max_tokens=max_tokens,
                groq_api_key=api_key if provider == "groq" else "",
                gemini_api_key=api_key if provider == "gemini" else "",
                nvidia_api_key=api_key if provider == "nvidia" else "",
            )

        pipeline_model = router.task_router.model_for("compile") if hasattr(router, "task_router") else "unknown"

        result = await run_extraction_consensus(
            source_id=source_name,
            source_type=source_type,
            source_text=source_text,
            pipeline_ideas=[dict(i) for i in pipeline_ideas],
            pipeline_model=pipeline_model,
            reference_model=ref_model,
            llm_fn=_llm_fn,
        )

        evals_db = db_path.parent / "evals.db"
        save_extraction_consensus(evals_db, result)
        log.info(
            "Extraction consensus eval complete",
            source=source_name,
            grade=result.grade,
            consensus_score=result.consensus_score,
            gaps=list(result.gaps),
        )
    except Exception as exc:
        log.warning(
            "Extraction consensus eval failed (background)",
            source=source_name,
            error=str(exc),
        )
