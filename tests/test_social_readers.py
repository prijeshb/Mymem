"""Tests for mymem.pipeline.social_readers — real parsing/dispatch logic,
network mocked only at the httpx boundary.

These tests deliberately avoid trivially-passing mocks: token derivation,
JSON-to-text assembly, reader-chain dispatch order, and the syndication→nitter
fallback are all exercised against realistic payloads.
"""
from __future__ import annotations

import json
import re

import pytest

from mymem.pipeline.readers import WebSourceReader, _default_readers, read_source
from mymem.pipeline.social_readers import (
    RedditSourceReader,
    TweetSourceReader,
    _build_reddit_text,
    _build_tweet_text,
    _extract_tweet_id,
    _is_reddit_url,
    _is_twitter_url,
    _reddit_json_url,
    _syndication_token,
)

# ---------------------------------------------------------------------------
# Realistic fixtures — shapes mirror the real syndication / Reddit responses
# ---------------------------------------------------------------------------

SYNDICATION_PAYLOAD = {
    "__typename": "Tweet",
    "text": (
        "New survey: a taxonomy of LLM agent architectures.\n\n"
        "We break agents into planning, memory, tool-use, and reflection. "
        "Each has open problems worth tracking."
    ),
    "user": {"name": "DAIR.AI", "screen_name": "dair_ai"},
    "mediaDetails": [
        {"ext_alt_text": "Diagram of a four-part agent architecture"},
        {"ext_alt_text": ""},  # empty alt should be skipped
    ],
    "quoted_tweet": {
        "text": "The original agents paper that started this thread.",
        "user": {"screen_name": "someauthor"},
    },
}

REDDIT_PAYLOAD = [
    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "title": "What finally made transformers click for you?",
                        "author": "ml_learner",
                        "selftext": "Nothing helped until I drew the QKV matrices by hand.",
                        "url": "https://reddit.com/r/ml/comments/abc/",
                    },
                }
            ]
        },
    },
    {
        "kind": "Listing",
        "data": {
            "children": [
                {"kind": "t1", "data": {"author": "alice", "body": "The 3blue1brown animations."}},
                {"kind": "t1", "data": {"author": "bob", "body": "Numpy from scratch."}},
                {"kind": "more", "data": {"author": "", "body": ""}},  # non-comment, skipped
            ]
        },
    },
]


# ---------------------------------------------------------------------------
# Fake httpx boundary — routes by URL so dispatch/fallback are observable
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def install_fake_httpx(monkeypatch, route):
    """Patch httpx.AsyncClient so .get(url) returns route(url) -> FakeResponse."""
    import httpx

    requested: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, **_kwargs):
            requested.append(url)
            return route(url)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient())
    return requested


# ---------------------------------------------------------------------------
# URL detection + ID extraction
# ---------------------------------------------------------------------------

class TestUrlDetection:
    @pytest.mark.parametrize("url", [
        "https://x.com/dair_ai/status/2066174390048358760",
        "https://twitter.com/dair_ai/status/2066174390048358760?s=20",
        "https://fxtwitter.com/dair_ai/status/2066174390048358760",
        "https://nitter.net/dair_ai/status/2066174390048358760",
    ])
    def test_twitter_urls_detected(self, url):
        assert _is_twitter_url(url) is True

    def test_non_twitter_url_not_detected(self):
        assert _is_twitter_url("https://example.com/article") is False

    @pytest.mark.parametrize("url,expected", [
        ("https://x.com/dair_ai/status/2066174390048358760", "2066174390048358760"),
        ("https://twitter.com/dair_ai/status/123?s=20", "123"),
        ("https://x.com/i/web/status/456", "456"),
    ])
    def test_tweet_id_extraction(self, url, expected):
        assert _extract_tweet_id(url) == expected

    def test_tweet_id_missing_returns_none(self):
        assert _extract_tweet_id("https://x.com/dair_ai") is None

    def test_reddit_detection(self):
        assert _is_reddit_url("https://www.reddit.com/r/ml/comments/abc/title/")
        assert _is_reddit_url("https://redd.it/abc")
        assert not _is_reddit_url("https://example.com")


# ---------------------------------------------------------------------------
# Syndication token — structural correctness, not a self-referential snapshot
# ---------------------------------------------------------------------------

class TestSyndicationToken:
    def test_token_format(self):
        token = _syndication_token("2066174390048358760")
        # react-tweet strips all '0' runs and the '.', leaving base-36 alnum.
        assert re.fullmatch(r"[1-9a-z]+", token), f"unexpected token: {token!r}"
        assert "0" not in token
        assert "." not in token

    def test_token_is_deterministic(self):
        a = _syndication_token("123456789012345678")
        b = _syndication_token("123456789012345678")
        assert a == b

    def test_token_varies_with_id(self):
        assert _syndication_token("111") != _syndication_token("222")

    def test_token_golden_value_validated_against_live_api(self):
        """Regression guard anchored to a real-world value.

        This exact token was confirmed to return HTTP 200 from the live
        cdn.syndication.twimg.com endpoint for tweet 2066174390048358760.
        If the derivation algorithm drifts, this breaks before X rejects us.
        """
        assert _syndication_token("2066174390048358760") == "5b2tggf6ely89cmn9daemi"


# ---------------------------------------------------------------------------
# Tweet text assembly
# ---------------------------------------------------------------------------

class TestBuildTweetText:
    def test_includes_author_full_text_quote_and_alt(self):
        text = _build_tweet_text(SYNDICATION_PAYLOAD)
        assert "@dair_ai" in text
        assert "DAIR.AI" in text
        assert "taxonomy of LLM agent architectures" in text
        assert "planning, memory, tool-use, and reflection" in text  # full body, not truncated
        assert "Quoting @someauthor" in text
        assert "Diagram of a four-part agent architecture" in text

    def test_empty_alt_text_skipped(self):
        text = _build_tweet_text(SYNDICATION_PAYLOAD)
        assert text.count("Image:") == 1  # only the non-empty alt

    def test_no_text_returns_empty(self):
        assert _build_tweet_text({"user": {"screen_name": "x"}}) == ""

    def test_reply_parent_rendered(self):
        payload = {
            "text": "Adding one more point to the above.",
            "user": {"screen_name": "dair_ai"},
            "parent": {"text": "Original root tweet.", "user": {"screen_name": "dair_ai"}},
        }
        text = _build_tweet_text(payload)
        assert "In reply to @dair_ai: Original root tweet." in text

    def test_x_article_title_preview_and_url_expansion(self):
        """X Articles carry no tweet text — only a t.co link plus an `article`
        block. Shape mirrors the live cdn.syndication.twimg.com payload."""
        payload = {
            "text": "https://t.co/abc123",
            "user": {"name": "DAIR.AI", "screen_name": "dair_ai"},
            "entities": {
                "urls": [{
                    "url": "https://t.co/abc123",
                    "expanded_url": "http://x.com/i/article/2065955653609246721",
                }]
            },
            "article": {
                "title": "Top AI Papers of the Week",
                "preview_text": "Welcome to the roundup.\n1. MiniMax Sparse Attention",
            },
        }
        text = _build_tweet_text(payload)
        assert "Article: Top AI Papers of the Week" in text
        assert "MiniMax Sparse Attention" in text
        assert "x.com/i/article/2065955653609246721" in text  # t.co expanded
        assert "t.co/abc123" not in text  # short link replaced

    def test_article_only_with_empty_tweet_text_still_produced(self):
        payload = {
            "text": "",
            "user": {"screen_name": "dair_ai"},
            "article": {"title": "Long-form piece", "preview_text": "Intro paragraph."},
        }
        text = _build_tweet_text(payload)
        assert "Article: Long-form piece" in text
        assert "Intro paragraph." in text


# ---------------------------------------------------------------------------
# Reddit text assembly
# ---------------------------------------------------------------------------

class TestBuildRedditText:
    def test_post_and_comments_assembled(self):
        text = _build_reddit_text(REDDIT_PAYLOAD)
        assert "u/ml_learner" in text
        assert "What finally made transformers click" in text
        assert "drew the QKV matrices by hand" in text
        assert "- u/alice: The 3blue1brown animations." in text
        assert "- u/bob:" in text

    def test_non_comment_kinds_skipped(self):
        text = _build_reddit_text(REDDIT_PAYLOAD)
        assert text.count("- u/") == 2  # alice + bob, not the 'more' node

    def test_empty_payload_returns_empty(self):
        assert _build_reddit_text([]) == ""
        assert _build_reddit_text({}) == ""

    def test_json_url_construction(self):
        assert _reddit_json_url("https://reddit.com/r/ml/comments/abc/title/") == (
            "https://reddit.com/r/ml/comments/abc/title.json?raw_json=1&limit=20"
        )
        assert _reddit_json_url("https://reddit.com/r/ml/comments/abc?utm=x").startswith(
            "https://reddit.com/r/ml/comments/abc.json"
        )


# ---------------------------------------------------------------------------
# Reader-chain dispatch ordering
# ---------------------------------------------------------------------------

class TestChainOrdering:
    def test_tweet_url_claimed_before_web(self):
        readers = _default_readers()
        claimer = next(r for r in readers if r.can_handle("https://x.com/a/status/1", "tweet"))
        assert isinstance(claimer, TweetSourceReader)

    def test_reddit_url_claimed_before_web_even_as_webpage(self):
        readers = _default_readers()
        url = "https://www.reddit.com/r/ml/comments/abc/title/"
        claimer = next(r for r in readers if r.can_handle(url, "webpage"))
        assert isinstance(claimer, RedditSourceReader)

    def test_plain_article_url_still_goes_to_web(self):
        readers = _default_readers()
        claimer = next(r for r in readers if r.can_handle("https://example.com/post", "article"))
        assert isinstance(claimer, WebSourceReader)


# ---------------------------------------------------------------------------
# Integration via read_source — only the network boundary is mocked
# ---------------------------------------------------------------------------

class TestTweetIntegration:
    @pytest.mark.asyncio
    async def test_read_source_dispatches_tweet_to_syndication(self, monkeypatch):
        def route(url):
            if "cdn.syndication.twimg.com" in url:
                return FakeResponse(200, json.dumps(SYNDICATION_PAYLOAD))
            return FakeResponse(404, "")

        requested = install_fake_httpx(monkeypatch, route)
        text = await read_source(
            "https://x.com/dair_ai/status/2066174390048358760", source_type="tweet"
        )
        assert "taxonomy of LLM agent architectures" in text
        assert any("cdn.syndication.twimg.com" in u for u in requested)

    @pytest.mark.asyncio
    async def test_falls_back_to_nitter_when_syndication_fails(self, monkeypatch):
        nitter_html = (
            b"<html><body><div class='tweet-content'>"
            b"Full thread text rendered by nitter, long enough to pass the length gate."
            b"</div></body></html>"
        )

        def route(url):
            if "cdn.syndication.twimg.com" in url:
                return FakeResponse(404, "")  # syndication unavailable
            if "nitter" in url:
                return FakeResponse(200, nitter_html.decode())
            return FakeResponse(404, "")

        requested = install_fake_httpx(monkeypatch, route)
        text = await read_source(
            "https://x.com/dair_ai/status/2066174390048358760", source_type="tweet"
        )
        assert "Full thread text rendered by nitter" in text
        assert any("nitter" in u for u in requested), "nitter fallback should be attempted"

    @pytest.mark.asyncio
    async def test_all_fallbacks_fail_raises_actionable_error(self, monkeypatch):
        install_fake_httpx(monkeypatch, lambda url: FakeResponse(404, ""))
        with pytest.raises(RuntimeError, match="Paste the thread text"):
            await read_source("https://x.com/a/status/999", source_type="tweet")


class TestRedditIntegration:
    @pytest.mark.asyncio
    async def test_read_source_dispatches_reddit_to_json_api(self, monkeypatch):
        def route(url):
            assert url.endswith("title.json?raw_json=1&limit=20") or ".json" in url
            return FakeResponse(200, json.dumps(REDDIT_PAYLOAD))

        await_url = "https://www.reddit.com/r/ml/comments/abc/title/"
        install_fake_httpx(monkeypatch, route)
        text = await read_source(await_url, source_type="webpage")
        assert "What finally made transformers click" in text
        assert "- u/alice:" in text

    @pytest.mark.asyncio
    async def test_reddit_rate_limit_raises_actionable_error(self, monkeypatch):
        install_fake_httpx(monkeypatch, lambda url: FakeResponse(429, ""))
        with pytest.raises(RuntimeError, match="blocked the request"):
            await read_source(
                "https://www.reddit.com/r/ml/comments/abc/title/", source_type="webpage"
            )

    @pytest.mark.asyncio
    async def test_reddit_403_block_raises_actionable_error(self, monkeypatch):
        """Reddit really returns 403 (an HTML block page) to non-browser IPs even
        with a browser UA — observed live. The reader must surface a clear error."""
        install_fake_httpx(monkeypatch, lambda url: FakeResponse(403, "<html>blocked</html>"))
        with pytest.raises(RuntimeError, match="blocked the request"):
            await read_source(
                "https://www.reddit.com/r/ml/comments/abc/title/", source_type="webpage"
            )

    @pytest.mark.asyncio
    async def test_reddit_network_error_raises_runtime_error(self, monkeypatch):
        def boom(url):
            raise OSError("connection reset")

        install_fake_httpx(monkeypatch, boom)
        with pytest.raises(RuntimeError, match="Reddit fetch failed"):
            await read_source(
                "https://www.reddit.com/r/ml/comments/abc/title/", source_type="webpage"
            )


class TestTweetErrorPaths:
    @pytest.mark.asyncio
    async def test_syndication_network_error_falls_through_to_nitter(self, monkeypatch):
        """A raised network error on syndication must not abort — nitter is still tried."""
        nitter_html = (
            "<html><body><div class='tweet-content'>"
            "Recovered the full thread from a nitter mirror after syndication failed."
            "</div></body></html>"
        )

        def route(url):
            if "cdn.syndication.twimg.com" in url:
                raise OSError("connection reset by peer")
            if "nitter" in url:
                return FakeResponse(200, nitter_html)
            return FakeResponse(404, "")

        install_fake_httpx(monkeypatch, route)
        text = await read_source("https://x.com/a/status/123", source_type="tweet")
        assert "Recovered the full thread" in text

    @pytest.mark.asyncio
    async def test_missing_tweet_id_raises_value_error(self, monkeypatch):
        install_fake_httpx(monkeypatch, lambda url: FakeResponse(404, ""))
        with pytest.raises(ValueError, match="extract a tweet ID"):
            await read_source("https://x.com/dair_ai", source_type="tweet")
