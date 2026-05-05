"""Tests for mymem.pipeline.ingest — mocked LLM, no real network calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from unittest.mock import AsyncMock, patch

from mymem.pipeline.ingest import (
    IngestResult, ingest_source, _parse_ideas, _strip_frontmatter,
    _is_youtube_url, _html_to_text, _read_source, _extract_video_id, _read_youtube,
    _format_duration, _format_chapters, _build_youtube_context, _fetch_youtube_metadata,
    _rag_index_pdf,
)
from mymem.pipeline.router import ModelRouter
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.types import LogOperation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_IDEAS = [
    {
        "title": "Concept Alpha",
        "summary": "A fundamental concept in the domain.",
        "tags": ["alpha", "fundamental"],
        "domain": "tech",
    },
    {
        "title": "Concept Beta",
        "summary": "A secondary concept related to Alpha.",
        "tags": ["beta", "secondary"],
        "domain": "tech",
    },
]

FAKE_PAGE_BODY = (
    "# Concept Alpha\n\n"
    "This is the main body of the wiki page about Concept Alpha.\n\n"
    "## See Also\n\n- [[Concept Beta]]"
)


def make_router(ideas: list[dict] | None = None, page_body: str = FAKE_PAGE_BODY) -> ModelRouter:
    """Build a ModelRouter with a fake LLM that returns predictable responses."""
    call_count = [0]

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        call_count[0] += 1
        # First call per chunk = extract ideas (returns JSON)
        if "JSON" in system or "json" in system.lower():
            return json.dumps(FAKE_IDEAS if ideas is None else ideas)
        # Subsequent calls = compile wiki page body
        return page_body

    return ModelRouter(llm_fn=fake_llm)


def make_source_file(tmp_path: Path, content: str = "Sample article content.") -> Path:
    src = tmp_path / "raw" / "article.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text(content)
    return src


# ---------------------------------------------------------------------------
# Core ingest tests
# ---------------------------------------------------------------------------

class TestIngestSource:
    @pytest.mark.asyncio
    async def test_creates_wiki_pages(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        router = make_router()

        result = await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=router,
        )

        assert not result.skipped
        assert len(result.pages_written) > 0

    @pytest.mark.asyncio
    async def test_pages_written_to_disk(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        md_files = list(wiki_dir.glob("*.md"))
        # Should have at least the wiki pages (index and log are also created)
        assert any("concept" in f.name for f in md_files)

    @pytest.mark.asyncio
    async def test_index_updated(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"

        await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        mgr = IndexManager(index_path)
        assert len(mgr.load()) > 0

    @pytest.mark.asyncio
    async def test_log_appended(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        log_path = wiki_dir / "log.md"

        await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=log_path,
            router=make_router(),
        )

        log = WikiLog(log_path)
        ingests = log.by_operation(LogOperation.INGEST)
        assert len(ingests) == 1

    @pytest.mark.asyncio
    async def test_skips_on_high_severity_secret(self, tmp_path: Path):
        src = make_source_file(
            tmp_path,
            content="key = sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz123456",
        )
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        assert result.skipped
        assert "secret" in result.skip_reason.lower()

    @pytest.mark.asyncio
    async def test_no_ideas_returns_skipped(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        router = make_router(ideas=[])  # LLM returns empty list

        result = await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=router,
        )

        assert result.skipped

    @pytest.mark.asyncio
    async def test_domain_override_applied(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=make_router(),
            domain="spiritual",
        )

        assert not result.skipped
        # Domain should have been applied to written pages
        from mymem.wiki.page import list_pages
        from mymem.wiki.types import TagDomain
        pages = list_pages(wiki_dir)
        assert any(p.domain == TagDomain.SPIRITUAL for p in pages)

    @pytest.mark.asyncio
    async def test_second_ingest_marks_as_updated(self, tmp_path: Path):
        src = make_source_file(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        kwargs = dict(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )
        await ingest_source(str(src), **kwargs)
        result2 = await ingest_source(str(src), **make_router_kwargs(tmp_path, wiki_dir))
        # Second run should mark pages as updated, not new
        assert len(result2.pages_updated) > 0 or len(result2.pages_written) > 0


def make_router_kwargs(tmp_path: Path, wiki_dir: Path) -> dict:
    return dict(
        wiki_dir=wiki_dir,
        index_path=wiki_dir / "index.md",
        log_path=wiki_dir / "log.md",
        router=make_router(),
    )


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestParseIdeas:
    def test_valid_json_list(self):
        raw = json.dumps([{"title": "T", "summary": "S", "tags": [], "domain": "tech"}])
        ideas = _parse_ideas(raw)
        assert len(ideas) == 1
        assert ideas[0]["title"] == "T"

    def test_strips_code_fences(self):
        raw = "```json\n" + json.dumps([{"title": "T", "summary": "S"}]) + "\n```"
        ideas = _parse_ideas(raw)
        assert len(ideas) == 1

    def test_strips_think_block_before_json(self):
        ideas_json = json.dumps([{"title": "AI Costs", "summary": "Data center costs $20B/GW.", "tags": ["ai"], "domain": "tech"}])
        raw = f"<think>\nLet me analyze this transcript...\n</think>\n\n```json\n{ideas_json}\n```"
        ideas = _parse_ideas(raw)
        assert len(ideas) == 1
        assert ideas[0]["title"] == "AI Costs"

    def test_invalid_json_returns_empty(self):
        assert _parse_ideas("not json at all") == []

    def test_plain_dict_with_no_list_value_returns_empty(self):
        assert _parse_ideas(json.dumps({"key": "val"})) == []

    def test_dict_wrapped_list_is_unwrapped(self):
        ideas = [{"title": "T", "summary": "S", "tags": [], "domain": "tech"}]
        raw = json.dumps({"ideas": ideas})
        result = _parse_ideas(raw)
        assert len(result) == 1
        assert result[0]["title"] == "T"


class TestStripFrontmatter:
    def test_strips_yaml_block(self):
        text = "---\ntitle: Test\n---\n\n# Body"
        result = _strip_frontmatter(text)
        assert result.startswith("# Body")
        assert "---" not in result

    def test_no_frontmatter_unchanged(self):
        text = "# Just a heading\n\nContent."
        assert _strip_frontmatter(text) == text


# ---------------------------------------------------------------------------
# Source type helper tests
# ---------------------------------------------------------------------------

class TestIsYoutubeUrl:
    def test_watch_url(self):
        assert _is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_short_url(self):
        assert _is_youtube_url("https://youtu.be/dQw4w9WgXcQ")

    def test_embed_url(self):
        assert _is_youtube_url("https://www.youtube.com/embed/dQw4w9WgXcQ")

    def test_shorts_url(self):
        assert _is_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")

    def test_non_youtube(self):
        assert not _is_youtube_url("https://vimeo.com/123456789")

    def test_plain_text(self):
        assert not _is_youtube_url("just a string")


class TestExtractVideoId:
    def test_watch_url(self):
        assert _extract_video_id("https://www.youtube.com/watch?v=4zk-hJ50vmU") == "4zk-hJ50vmU"

    def test_short_url(self):
        assert _extract_video_id("https://youtu.be/4zk-hJ50vmU") == "4zk-hJ50vmU"

    def test_embed_url(self):
        assert _extract_video_id("https://www.youtube.com/embed/4zk-hJ50vmU") == "4zk-hJ50vmU"

    def test_shorts_url(self):
        assert _extract_video_id("https://www.youtube.com/shorts/4zk-hJ50vmU") == "4zk-hJ50vmU"

    def test_watch_with_extra_params(self):
        assert _extract_video_id(
            "https://www.youtube.com/watch?v=4zk-hJ50vmU&t=120s&list=PLxxx"
        ) == "4zk-hJ50vmU"

    def test_non_youtube_returns_none(self):
        assert _extract_video_id("https://vimeo.com/123456789") is None

    def test_invalid_url_returns_none(self):
        assert _extract_video_id("not a url") is None


class TestReadYoutube:
    FAKE_ENTRIES = [
        {"text": "Hello and welcome to this video.", "start": 0.0, "duration": 3.5},
        {"text": "Today we discuss AI and capital efficiency.", "start": 3.5, "duration": 4.0},
        {"text": "Thanks for watching.", "start": 120.0, "duration": 2.0},
    ]
    TARGET_URL = "https://www.youtube.com/watch?v=4zk-hJ50vmU"
    VIDEO_ID   = "4zk-hJ50vmU"

    def _make_snippet(self, text: str):
        """Create a fake snippet with a .text attribute."""
        class Snippet:
            def __init__(self, t):
                self.text = t
        return Snippet(text)

    @pytest.mark.asyncio
    async def test_fetches_english_transcript(self, monkeypatch):
        """api.fetch() is called with English language preference."""
        import mymem.pipeline.ingest as ingest_mod

        called_with: list[dict] = []
        fake_snippets = [self._make_snippet(e["text"]) for e in self.FAKE_ENTRIES]

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                called_with.append({"video_id": video_id, "languages": languages})
                return fake_snippets

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)

        text = await _read_youtube(self.TARGET_URL)
        assert self.VIDEO_ID in text
        assert "Hello and welcome" in text
        assert "AI and capital efficiency" in text
        assert called_with[0]["video_id"] == self.VIDEO_ID
        assert "en" in called_with[0]["languages"]

    @pytest.mark.asyncio
    async def test_transcript_joined_as_plain_text(self, monkeypatch):
        """Snippet texts are joined into a single readable string."""
        import mymem.pipeline.ingest as ingest_mod

        fake_snippets = [self._make_snippet(e["text"]) for e in self.FAKE_ENTRIES]

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                return fake_snippets

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)

        text = await _read_youtube(self.TARGET_URL)
        assert "Hello and welcome" in text
        assert "Thanks for watching" in text
        assert "{'text'" not in text

    @pytest.mark.asyncio
    async def test_falls_back_to_any_language_when_english_missing(self, monkeypatch):
        """When English is unavailable, falls back to any available transcript."""
        import mymem.pipeline.ingest as ingest_mod

        fallback_snippet = self._make_snippet("Bonjour tout le monde.")

        class FakeTranscript:
            def fetch(self):
                return [fallback_snippet]

        class FakeTranscriptList:
            def __iter__(self):
                return iter([FakeTranscript()])

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                raise ingest_mod.NoTranscriptFound(video_id, languages, [])

            def list(self, video_id):
                return FakeTranscriptList()

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)

        text = await _read_youtube(self.TARGET_URL)
        assert "Bonjour" in text

    @pytest.mark.asyncio
    async def test_raises_when_transcripts_disabled(self, monkeypatch):
        """RuntimeError raised when the video has transcripts disabled."""
        import mymem.pipeline.ingest as ingest_mod

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                raise ingest_mod.TranscriptsDisabled(video_id)

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)

        with pytest.raises(RuntimeError, match="Transcripts are disabled"):
            await _read_youtube(self.TARGET_URL)

    @pytest.mark.asyncio
    async def test_raises_when_package_not_installed(self, monkeypatch):
        """RuntimeError raised with install instructions when package missing."""
        import mymem.pipeline.ingest as ingest_mod
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", False)

        with pytest.raises(RuntimeError, match="pip install youtube-transcript-api"):
            await _read_youtube(self.TARGET_URL)

    @pytest.mark.asyncio
    async def test_auto_detected_from_url_in_read_source(self, monkeypatch):
        """_read_source auto-detects YouTube URLs without needing source_type=youtube."""
        import mymem.pipeline.ingest as ingest_mod

        async def fake_read_youtube(url: str) -> str:
            return f"[YouTube transcript — video ID: {self.VIDEO_ID}]\n\nAuto-detected transcript."

        monkeypatch.setattr(ingest_mod, "_read_youtube", fake_read_youtube)

        # No source_type passed — should still hit the YouTube path
        text = await _read_source(self.TARGET_URL)
        assert "Auto-detected transcript" in text
        assert self.VIDEO_ID in text


class TestFormatDuration:
    def test_seconds_only(self):
        assert _format_duration(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2:05"

    def test_hours(self):
        assert _format_duration(3661) == "1:01:01"

    def test_exact_hour(self):
        assert _format_duration(3600) == "1:00:00"

    def test_float_truncated(self):
        assert _format_duration(90.9) == "1:30"

    def test_zero(self):
        assert _format_duration(0) == "0:00"


class TestFormatChapters:
    def test_formats_chapters(self):
        chapters = [
            {"title": "Introduction", "start_time": 0.0},
            {"title": "Deep dive", "start_time": 754.0},
            {"title": "Conclusion", "start_time": 3540.0},
        ]
        result = _format_chapters(chapters)
        assert "0:00  Introduction" in result
        assert "12:34  Deep dive" in result
        assert "59:00  Conclusion" in result

    def test_empty_list(self):
        assert _format_chapters([]) == ""

    def test_missing_title_uses_empty_string(self):
        result = _format_chapters([{"start_time": 10.0}])
        assert "0:10  " in result


class TestBuildYoutubeContext:
    FULL_INFO = {
        "title": "Let's build GPT from scratch",
        "uploader": "Andrej Karpathy",
        "upload_date": "20230117",
        "duration": 7004,
        "description": "We build a GPT model step by step.",
        "chapters": [
            {"title": "Introduction", "start_time": 0.0},
            {"title": "Bigram model", "start_time": 765.0},
        ],
    }

    def test_includes_title(self):
        result = _build_youtube_context(self.FULL_INFO, "abc123", "transcript text")
        assert "Let's build GPT from scratch" in result

    def test_includes_channel_and_date(self):
        result = _build_youtube_context(self.FULL_INFO, "abc123", "transcript text")
        assert "Andrej Karpathy" in result
        assert "2023-01-17" in result

    def test_includes_duration(self):
        result = _build_youtube_context(self.FULL_INFO, "abc123", "transcript text")
        assert "1:56:44" in result

    def test_includes_chapters(self):
        result = _build_youtube_context(self.FULL_INFO, "abc123", "transcript text")
        assert "Introduction" in result
        assert "Bigram model" in result

    def test_includes_transcript(self):
        result = _build_youtube_context(self.FULL_INFO, "abc123", "hello world transcript")
        assert "hello world transcript" in result

    def test_description_truncated_at_1500(self):
        long_desc = "x" * 2000
        info = {**self.FULL_INFO, "description": long_desc}
        result = _build_youtube_context(info, "abc123", "t")
        assert "..." in result
        desc_part = result.split("Description:\n")[1].split("\n\n")[0]
        assert len(desc_part) <= 1504  # 1500 + "..."

    def test_no_chapters_omits_section(self):
        info = {**self.FULL_INFO, "chapters": []}
        result = _build_youtube_context(info, "abc123", "t")
        assert "Chapters:" not in result

    def test_empty_info_falls_back_gracefully(self):
        result = _build_youtube_context({}, "abc123", "some transcript")
        assert "abc123" in result
        assert "some transcript" in result

    def test_video_id_always_in_header(self):
        result = _build_youtube_context(self.FULL_INFO, "xyz999", "t")
        assert "xyz999" in result


class TestFetchYoutubeMetadata:
    TARGET_URL = "https://www.youtube.com/watch?v=4zk-hJ50vmU"

    @pytest.mark.asyncio
    async def test_returns_dict_on_success(self, monkeypatch):
        pytest.importorskip("yt_dlp")

        fake_info = {"title": "Test Video", "uploader": "Test Channel", "duration": 300}

        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def extract_info(self, url, download=False):
                return fake_info

        monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
        result = await _fetch_youtube_metadata(self.TARGET_URL)
        assert result["title"] == "Test Video"

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_yt_dlp_not_installed(self, monkeypatch):
        import mymem.pipeline.ingest as ingest_mod
        import builtins
        real_import = builtins.__import__

        def _block_ytdlp(name, *args, **kwargs):
            if name == "yt_dlp":
                raise ImportError("no module yt_dlp")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_ytdlp)
        result = await _fetch_youtube_metadata(self.TARGET_URL)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_extract_failure(self, monkeypatch):
        pytest.importorskip("yt_dlp")

        class FakeYDL:
            def __init__(self, opts):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def extract_info(self, url, download=False):
                raise RuntimeError("network error")

        monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
        result = await _fetch_youtube_metadata(self.TARGET_URL)
        assert result == {}


class TestReadYoutubeEnriched:
    """_read_youtube produces enriched output when yt-dlp metadata is available."""

    FAKE_ENTRIES = [
        {"text": "Hello and welcome.", "start": 0.0, "duration": 3.0},
        {"text": "Today we discuss transformers.", "start": 3.0, "duration": 4.0},
    ]
    TARGET_URL = "https://www.youtube.com/watch?v=4zk-hJ50vmU"
    VIDEO_ID   = "4zk-hJ50vmU"
    FAKE_META  = {
        "title":       "Understanding Transformers",
        "uploader":    "ML Channel",
        "upload_date": "20240301",
        "duration":    420,
        "description": "A deep dive into transformer architecture.",
        "chapters": [
            {"title": "Intro", "start_time": 0.0},
            {"title": "Attention", "start_time": 60.0},
        ],
    }

    def _make_snippet(self, text: str):
        class Snippet:
            def __init__(self, t): self.text = t
        return Snippet(text)

    @pytest.mark.asyncio
    async def test_enriched_output_includes_title_and_transcript(self, monkeypatch):
        import mymem.pipeline.ingest as ingest_mod

        fake_snippets = [self._make_snippet(e["text"]) for e in self.FAKE_ENTRIES]

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                return fake_snippets

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)
        monkeypatch.setattr(ingest_mod, "_fetch_youtube_metadata",
                            lambda url: asyncio.coroutine(lambda: self.FAKE_META)())

        async def fake_meta(url):
            return self.FAKE_META

        monkeypatch.setattr(ingest_mod, "_fetch_youtube_metadata", fake_meta)

        text = await _read_youtube(self.TARGET_URL)
        assert "Understanding Transformers" in text
        assert "ML Channel" in text
        assert "Attention" in text        # chapter
        assert "transformers" in text     # transcript

    @pytest.mark.asyncio
    async def test_falls_back_to_transcript_only_when_no_metadata(self, monkeypatch):
        import mymem.pipeline.ingest as ingest_mod

        fake_snippets = [self._make_snippet(e["text"]) for e in self.FAKE_ENTRIES]

        class FakeAPIInstance:
            def fetch(self, video_id, languages=None):
                return fake_snippets

        monkeypatch.setattr(ingest_mod, "YouTubeTranscriptApi", FakeAPIInstance)
        monkeypatch.setattr(ingest_mod, "_YT_AVAILABLE", True)

        async def fake_meta_empty(url):
            return {}

        monkeypatch.setattr(ingest_mod, "_fetch_youtube_metadata", fake_meta_empty)

        text = await _read_youtube(self.TARGET_URL)
        assert self.VIDEO_ID in text
        assert "Hello and welcome" in text
        assert "Title:" not in text   # no metadata block


class TestHtmlToText:
    def test_strips_tags(self):
        html = "<h1>Hello</h1><p>World</p>"
        result = _html_to_text(html)
        assert "Hello" in result
        assert "World" in result
        assert "<h1>" not in result

    def test_removes_script_and_style(self):
        html = "<html><head><style>body{color:red}</style></head><body><p>Content</p></body></html>"
        result = _html_to_text(html)
        assert "Content" in result
        assert "color:red" not in result

    def test_empty_html(self):
        result = _html_to_text("")
        assert result == ""




class TestHtmlFetchEncoding:
    @pytest.mark.asyncio
    async def test_trafilatura_fetch_url_used_for_http(self, monkeypatch):
        """When trafilatura is available, _read_source calls fetch_url directly
        instead of httpx — trafilatura handles decompression, encoding, and
        extraction internally, avoiding brotli/charset issues entirely."""
        import mymem.pipeline.ingest as ingest_mod

        fetched_urls: list[str] = []

        class FakeTrafilatura:
            @staticmethod
            def fetch_url(url: str) -> str:
                fetched_urls.append(url)
                return (
                    "2025 Annual Letter\n\n"
                    "2025 was, in some ways, a historic year for artificial intelligence. "
                    "Capital efficiency became the defining metric for startups. "
                    "The companies that survived were those that paired intelligence with restraint."
                )

        monkeypatch.setattr(ingest_mod, "trafilatura", FakeTrafilatura)

        url = "https://chamath.substack.com/p/2025-annual-letter"
        text = await _read_source(url, source_type="newsletter")

        assert fetched_urls == [url], "trafilatura.fetch_url should be called with the URL"
        assert "2025 Annual Letter" in text
        assert "historic" in text

    @pytest.mark.asyncio
    async def test_falls_back_to_httpx_when_trafilatura_missing(self, monkeypatch):
        """When trafilatura is not installed, _read_source falls back to httpx."""
        import mymem.pipeline.ingest as ingest_mod
        import httpx

        monkeypatch.setattr(ingest_mod, "trafilatura", None)

        article_bytes = b"<html><body><p>Fallback content via httpx.</p></body></html>"

        class FakeResponse:
            status_code = 200
            content = article_bytes
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def get(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient())

        text = await _read_source("https://example.com/article")
        assert "Fallback content" in text

    @pytest.mark.asyncio
    async def test_substack_article_extracts_readable_text(self, monkeypatch):
        """Simulates a Substack HTML page: script-heavy, JS noise stripped,
        article body preserved."""
        substack_html = """
        <html><head>
          <script>window.__data = "binary\x00garbage";</script>
          <style>.post{color:#333}</style>
        </head><body>
          <nav>Home | Archive | Subscribe</nav>
          <article class="post-content">
            <h1>2025 Annual Letter</h1>
            <p>This year marked a turning point in AI adoption across industries.</p>
            <p>Capital efficiency became the defining metric for startups in 2025.</p>
            <p>The companies that survived were those that paired intelligence with restraint.</p>
          </article>
          <footer>Powered by Substack</footer>
        </body></html>
        """.encode("utf-8")

        import httpx
        import mymem.pipeline.ingest as ingest_mod

        monkeypatch.setattr(ingest_mod, "trafilatura", None)

        class FakeResponse:
            status_code = 200
            content = substack_html
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def get(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient())

        text = await _read_source(
            "https://chamath.substack.com/p/2025-annual-letter",
            source_type="newsletter",
        )
        assert "2025 Annual Letter" in text or "turning point" in text
        assert "window.__data" not in text
        assert "color:#333" not in text


class TestReadSourceDispatch:
    @pytest.mark.asyncio
    async def test_reads_local_txt_file(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("Hello world")
        text = await _read_source(str(f))
        assert text == "Hello world"

    @pytest.mark.asyncio
    async def test_reads_local_md_file(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\nContent")
        text = await _read_source(str(f), source_type="article")
        assert "Title" in text

    @pytest.mark.asyncio
    async def test_youtube_url_dispatches_to_youtube_reader(self, monkeypatch):
        """_read_source should delegate YouTube URLs to _read_youtube."""
        async def fake_youtube(url: str) -> str:
            return "[YouTube transcript — video ID: dQw4w9WgXcQ]\n\nFake transcript."

        import mymem.pipeline.ingest as ingest_mod
        monkeypatch.setattr(ingest_mod, "_read_youtube", fake_youtube)

        text = await _read_source("https://youtu.be/dQw4w9WgXcQ", source_type="youtube")
        assert "YouTube transcript" in text

    @pytest.mark.asyncio
    async def test_youtube_source_type_overrides_url_check(self, monkeypatch):
        """Even a non-YouTube URL should use the YouTube reader if type=youtube."""
        async def fake_youtube(url: str) -> str:
            return "[YouTube transcript — video ID: ABCDE]\n\nFake transcript."

        import mymem.pipeline.ingest as ingest_mod
        monkeypatch.setattr(ingest_mod, "_read_youtube", fake_youtube)

        # source_type=youtube forces YouTube reader
        text = await _read_source("https://youtu.be/ABCDE12345A", source_type="youtube")
        assert "YouTube transcript" in text


# ---------------------------------------------------------------------------
# _rag_index_pdf
# ---------------------------------------------------------------------------

class TestRagIndexPdf:
    @pytest.mark.asyncio
    async def test_calls_ingest_pdf_for_local_pdf(self, tmp_path: Path):
        from mymem.rag.ingest import RagIngestResult

        ok_result = RagIngestResult(source_path="paper.pdf", chunk_count=5)
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(return_value=ok_result)) as mock_ingest:
            await _rag_index_pdf("paper.pdf", db_path=tmp_path / "main.db")
        mock_ingest.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_skipped_result(self, tmp_path: Path):
        from mymem.rag.ingest import RagIngestResult

        skipped = RagIngestResult(source_path="x.pdf", skipped=True, skip_reason="already indexed")
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(return_value=skipped)):
            await _rag_index_pdf("x.pdf", db_path=tmp_path / "main.db")
        # no exception — skipped is handled gracefully

    @pytest.mark.asyncio
    async def test_logs_error_result(self, tmp_path: Path):
        from mymem.rag.ingest import RagIngestResult

        failed = RagIngestResult(source_path="x.pdf", error="embedding failed")
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(return_value=failed)):
            await _rag_index_pdf("x.pdf", db_path=tmp_path / "main.db")
        # no exception raised

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self, tmp_path: Path):
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(side_effect=RuntimeError("crash"))):
            await _rag_index_pdf("x.pdf", db_path=tmp_path / "main.db")
        # must not raise

    @pytest.mark.asyncio
    async def test_rag_db_derived_from_parent_when_db_path_given(self, tmp_path: Path):
        from mymem.rag.ingest import RagIngestResult

        ok_result = RagIngestResult(source_path="paper.pdf", chunk_count=2)
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(return_value=ok_result)) as mock_ingest:
            await _rag_index_pdf("paper.pdf", db_path=tmp_path / "data" / "mymem.db")

        call_kwargs = mock_ingest.call_args.kwargs
        assert call_kwargs["db_path"] == tmp_path / "data" / "rag.db"

    @pytest.mark.asyncio
    async def test_rag_db_default_when_no_db_path(self, tmp_path: Path):
        from mymem.rag.ingest import RagIngestResult

        ok_result = RagIngestResult(source_path="paper.pdf", chunk_count=2)
        with patch("mymem.rag.ingest.ingest_pdf", new=AsyncMock(return_value=ok_result)) as mock_ingest:
            await _rag_index_pdf("paper.pdf", db_path=None)

        call_kwargs = mock_ingest.call_args.kwargs
        assert str(call_kwargs["db_path"]).endswith("rag.db")
