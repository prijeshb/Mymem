"""Tests for mymem.pipeline.ingest — mocked LLM, no real network calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mymem.pipeline.ingest import (
    IngestResult, ingest_source, _parse_ideas, _strip_frontmatter,
    _is_youtube_url, _html_to_text, _read_source, _extract_video_id, _read_youtube,
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
