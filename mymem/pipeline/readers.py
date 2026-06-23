"""
Source reading strategies — read raw content from any supported source type.

Design patterns applied:
  Strategy               — SourceReader: one subclass per fetching approach
  Chain of Responsibility — SourceReaderChain: tries readers in order
  Open/Closed Principle  — add a source type by subclassing, never touch existing readers
  Template Method        — SourceReader.can_handle() + SourceReader.read() contract

Usage:
    text = await read_source("https://youtu.be/xyz", source_type="youtube")
    text = await read_source("raw/paper.pdf", source_type="book")
    text = await read_source("raw/article.md")
"""
from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

from mymem.observability.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Optional runtime dependencies
# ---------------------------------------------------------------------------

try:
    import trafilatura  # type: ignore[import-untyped]
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
# SourceReader ABC — Strategy pattern
# ---------------------------------------------------------------------------

class SourceReader(ABC):
    """
    Strategy: read raw text from one category of source.

    Subclasses implement two methods:
      can_handle(source, source_type) → True if this reader claims this source
      read(source, source_type)       → the raw text string

    Open/Closed: add a source type by subclassing — never modify existing readers.
    """

    @abstractmethod
    def can_handle(self, source: str, source_type: str) -> bool:
        """Return True if this reader can handle *source* / *source_type*."""
        ...

    @abstractmethod
    async def read(self, source: str, source_type: str) -> str:
        """Read and return the raw text for *source*."""
        ...


# ---------------------------------------------------------------------------
# YouTube reader
# ---------------------------------------------------------------------------

class YoutubeSourceReader(SourceReader):
    """Fetch transcript + metadata for a YouTube video URL."""

    def can_handle(self, source: str, source_type: str) -> bool:
        return source_type == "youtube" or _is_youtube_url(source)

    async def read(self, source: str, source_type: str) -> str:
        return await _read_youtube(source)


# ---------------------------------------------------------------------------
# Web page reader (trafilatura + httpx fallback)
# ---------------------------------------------------------------------------

class WebSourceReader(SourceReader):
    """Fetch and extract text from any HTTP(S) URL."""

    def can_handle(self, source: str, source_type: str) -> bool:
        return source.startswith(("http://", "https://"))

    async def read(self, source: str, source_type: str) -> str:
        if trafilatura is not None:
            # fetch_url is blocking I/O — run off the event loop. It returns the RAW
            # downloaded HTML; extract clean article text from it via _html_to_text.
            # Returning the raw HTML (the old bug) floods the LLM with markup/nav/scripts
            # and starves idea extraction — a 34KB article arrived as 380KB of HTML.
            loop = asyncio.get_running_loop()
            downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, source)
            if downloaded:
                text = _html_to_text(downloaded)
                if text and len(text.strip()) > 100:
                    log.info("trafilatura fetched + extracted URL",
                             text_chars=len(text), url=source)
                    return text.strip()
            log.warning("trafilatura returned no usable content for URL", url=source)
        else:
            log.warning(
                "trafilatura not installed — falling back to httpx. "
                "Run: pip install trafilatura"
            )

        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "httpx or trafilatura required for URL ingestion"
            ) from exc

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


# ---------------------------------------------------------------------------
# PDF / book reader
# ---------------------------------------------------------------------------

class PdfSourceReader(SourceReader):
    """Extract text from a local PDF file using pypdf."""

    def can_handle(self, source: str, source_type: str) -> bool:
        if source.startswith(("http://", "https://")):
            return False
        return Path(source).suffix.lower() == ".pdf" or source_type == "book"

    async def read(self, source: str, source_type: str) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf required for PDF/book ingestion") from exc
        reader = PdfReader(str(source))
        return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# Local file reader (catch-all)
# ---------------------------------------------------------------------------

class LocalFileSourceReader(SourceReader):
    """Read any local file as plain UTF-8 text."""

    def can_handle(self, source: str, source_type: str) -> bool:
        return not source.startswith(("http://", "https://"))

    async def read(self, source: str, source_type: str) -> str:
        return Path(source).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# SourceReaderChain — Chain of Responsibility
# ---------------------------------------------------------------------------

class SourceReaderChain:
    """
    Tries registered readers in order; the first one that claims the source wins.

    Chain of Responsibility: each reader either handles the request or passes it on.
    The chain is ordered — more specific readers (YouTube, PDF) must be registered
    before more general ones (Web, LocalFile).
    """

    def __init__(self, readers: list[SourceReader] | None = None) -> None:
        self._readers = readers or _default_readers()

    async def read(self, source: str, source_type: str = "article") -> str:
        for reader in self._readers:
            if reader.can_handle(source, source_type):
                return await reader.read(source, source_type)
        raise RuntimeError(
            f"No reader found for source={source!r}, source_type={source_type!r}"
        )


def _default_readers() -> list[SourceReader]:
    """Return the default ordered reader list.

    Platform-specific readers (YouTube, Tweet, Reddit) must precede the generic
    WebSourceReader so they claim their URLs first; the catch-all LocalFile
    reader is always last. Social readers are imported lazily to avoid a circular
    import (social_readers imports from this module).
    """
    from mymem.pipeline.social_readers import RedditSourceReader, TweetSourceReader

    return [
        YoutubeSourceReader(),
        TweetSourceReader(),
        RedditSourceReader(),
        WebSourceReader(),
        PdfSourceReader(),
        LocalFileSourceReader(),
    ]


# ---------------------------------------------------------------------------
# Module-level convenience function — backward-compatible with ingest.py
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_chain() -> SourceReaderChain:
    """Build the default reader chain once, lazily.

    Lazy construction is required: _default_readers() imports social_readers,
    which imports this module — building the chain at import time would deadlock
    on a circular import. Cached because the readers are stateless.
    """
    return SourceReaderChain()


async def read_source(source: str, source_type: str = "article") -> str:
    """
    Read text from a local file path or HTTP(S) URL.

    Dispatches based on source_type:
      youtube      → fetch transcript via youtube-transcript-api
      tweet / x.com→ syndication API (full thread text), nitter fallback
      reddit.com   → no-auth .json endpoint (post + top comments)
      webpage      → HTTP fetch + HTML → plain-text stripping
      podcast      → HTTP fetch + HTML strip (RSS/show-notes page)
      pdf / book   → pypdf text extraction (file path only)
      *            → plain file read
    """
    return await _get_chain().read(source, source_type)


# ---------------------------------------------------------------------------
# YouTube helpers (used by YoutubeSourceReader + tests)
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


async def _fetch_youtube_metadata(url: str) -> dict[str, Any]:
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
    upload_date  = info.get("upload_date", "")
    duration     = info.get("duration")
    description  = (info.get("description") or "").strip()
    chapters     = info.get("chapters") or []

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
    """Fetch YouTube transcript enriched with video metadata when yt-dlp is available."""
    if not _YT_AVAILABLE:
        raise RuntimeError(
            "youtube-transcript-api is required for YouTube ingestion.\n"
            "Install it with:  pip install youtube-transcript-api"
        )

    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Cannot extract YouTube video ID from URL: {url!r}")

    log.info("Fetching YouTube video", video_id=video_id)

    metadata_task = asyncio.ensure_future(_fetch_youtube_metadata(url))

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
    except TranscriptsDisabled:
        metadata_task.cancel()
        try:
            await metadata_task
        except asyncio.CancelledError:
            pass
        raise RuntimeError(f"Transcripts are disabled for YouTube video: {video_id}")
    except NoTranscriptFound:
        try:
            transcript_list = api.list(video_id)
            transcript = next(iter(transcript_list))
            fetched = transcript.fetch()
        except StopIteration:
            metadata_task.cancel()
            try:
                await metadata_task
            except asyncio.CancelledError:
                pass
            raise RuntimeError(f"No transcripts available for video: {video_id}")

    transcript_text = " ".join(s.text for s in fetched if s.text.strip())
    log.info("YouTube transcript fetched", video_id=video_id, chars=len(transcript_text))

    info = await metadata_task
    if info:
        log.info("YouTube metadata fetched", video_id=video_id, title=info.get("title", ""))
        return _build_youtube_context(info, video_id, transcript_text)

    return f"[YouTube transcript — video ID: {video_id}]\n\n{transcript_text}"


# ---------------------------------------------------------------------------
# HTML extraction helper (used by WebSourceReader + tests)
# ---------------------------------------------------------------------------

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
        log.warning(
            "trafilatura not installed — falling back to BeautifulSoup. "
            "Run: pip install trafilatura"
        )

    html_str: str = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html_str)

    soup = BeautifulSoup(html_str, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)
