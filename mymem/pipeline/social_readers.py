"""
Social source readers — recover full text from social/thread URLs that the
generic web reader cannot see.

Why this exists:
    X/Twitter and Reddit serve a near-empty, JavaScript-rendered HTML shell to
    non-browser clients. trafilatura/httpx therefore see only a single tweet's
    ``og:description`` (or nothing at all), which is why a pasted thread used to
    compile into just one wiki idea. These readers use each platform's no-auth
    JSON endpoint to fetch the real content server-side.

Design:
    Strategy pattern — each class is a ``SourceReader`` registered in the reader
    chain *ahead* of the generic ``WebSourceReader`` (see
    ``readers._default_readers``). Open/Closed: add a platform by subclassing,
    never by editing an existing reader.

    Fetch and parse are kept as separate pure functions so the assembly logic
    can be tested against realistic payloads without touching the network.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from mymem.observability.logger import get_logger
from mymem.pipeline.readers import SourceReader

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_TWITTER_HOSTS = (
    "twitter.com/",
    "x.com/",
    "fxtwitter.com/",
    "vxtwitter.com/",
    "fixupx.com/",
    "nitter.",
)
_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"

# Public nitter mirrors used only as a last-resort thread fallback. Most are
# unreliable; the syndication API is the real workhorse. Empty is acceptable.
_NITTER_INSTANCES: tuple[str, ...] = (
    "https://nitter.net",
    "https://nitter.poast.org",
)

_REDDIT_HOSTS = ("reddit.com/", "redd.it/")
_MAX_REDDIT_COMMENTS = 20
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_twitter_url(url: str) -> bool:
    lowered = url.lower()
    return any(host in lowered for host in _TWITTER_HOSTS)


def _is_reddit_url(url: str) -> bool:
    lowered = url.lower()
    return any(host in lowered for host in _REDDIT_HOSTS)


def _extract_tweet_id(url: str) -> str | None:
    """Pull the numeric status ID from any tweet URL form (status/ or i/web/status/)."""
    m = re.search(r"/status(?:es)?/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/i/web/status/(\d+)", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Syndication token — port of react-tweet's getToken()
# ---------------------------------------------------------------------------

def _float_to_base36(value: float) -> str:
    """Replicate JavaScript ``Number.prototype.toString(36)`` for a positive float."""
    if value <= 0:
        return "0"
    int_part = int(value)
    frac = value - int_part

    if int_part == 0:
        int_str = "0"
    else:
        chars: list[str] = []
        n = int_part
        while n > 0:
            chars.append(_BASE36[n % 36])
            n //= 36
        int_str = "".join(reversed(chars))

    frac_chars: list[str] = []
    count = 0
    while frac > 0 and count < 20:
        frac *= 36
        digit = int(frac)
        frac_chars.append(_BASE36[digit])
        frac -= digit
        count += 1

    return int_str + ("." + "".join(frac_chars) if frac_chars else "")


def _syndication_token(tweet_id: str) -> str:
    """Derive the syndication request token from a tweet ID (no auth needed).

    Mirrors react-tweet: ``((id / 1e15) * PI).toString(36).replace(/(0+|\\.)/g, '')``.
    """
    value = (int(tweet_id) / 1e15) * math.pi
    return re.sub(r"(0+|\.)", "", _float_to_base36(value))


# ---------------------------------------------------------------------------
# HTTP boundary (mocked in tests)
# ---------------------------------------------------------------------------

async def _http_get(url: str, *, accept: str = "*/*") -> tuple[int, str]:
    """GET *url* with a browser UA; return (status_code, body_text)."""
    import httpx

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": _UA, "Accept": accept},
    ) as client:
        resp = await client.get(url)
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Tweet text assembly (pure)
# ---------------------------------------------------------------------------

def _tweet_one_line(data: dict[str, Any]) -> str:
    user = data.get("user") or {}
    handle = user.get("screen_name", "")
    text = (data.get("text") or "").strip()
    prefix = f"@{handle}: " if handle else ""
    return f"{prefix}{text}".strip()


def _expand_tco(text: str, data: dict[str, Any]) -> str:
    """Replace t.co short links with their expanded URLs from the entities block."""
    urls = (data.get("entities") or {}).get("urls") or []
    for entry in urls:
        if not isinstance(entry, dict):
            continue
        short, expanded = entry.get("url"), entry.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)
    return text


def _article_block(data: dict[str, Any]) -> list[str]:
    """Title + preview for an X Article tweet (long-form), if present.

    The no-auth syndication endpoint only exposes the article's title and a
    truncated ``preview_text`` — the full body requires authentication.
    """
    article = data.get("article")
    if not isinstance(article, dict):
        return []
    title = (article.get("title") or "").strip()
    preview = (article.get("preview_text") or "").strip()
    block: list[str] = []
    if title:
        block.append(f"Article: {title}")
    if preview:
        block.append(preview)
    return block


def _build_tweet_text(data: dict[str, Any]) -> str:
    """Assemble readable text from a syndication ``tweet-result`` payload.

    Includes the author, full (untruncated) tweet text, any quoted tweet, image
    alt-text, and — for X Articles (long-form) — the article title and preview.
    Returns "" if the payload carries no usable content.
    """
    text = _expand_tco((data.get("text") or "").strip(), data)
    article = _article_block(data)
    if not text and not article:
        return ""

    user = data.get("user") or {}
    name = user.get("name", "")
    handle = user.get("screen_name", "")
    header = "[Tweet"
    if handle:
        header += f" by @{handle}"
        if name:
            header += f" ({name})"
    header += "]"

    parts: list[str] = [header]
    if text:
        parts.append(text)
    parts.extend(article)

    parent = data.get("parent")
    if isinstance(parent, dict) and (parent.get("text") or "").strip():
        parts.append(f"In reply to {_tweet_one_line(parent)}")

    quoted = data.get("quoted_tweet")
    if isinstance(quoted, dict) and (quoted.get("text") or "").strip():
        parts.append(f"Quoting {_tweet_one_line(quoted)}")

    alts = [
        (m.get("ext_alt_text") or "").strip()
        for m in (data.get("mediaDetails") or [])
        if isinstance(m, dict) and (m.get("ext_alt_text") or "").strip()
    ]
    for alt in alts:
        parts.append(f"Image: {alt}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tweet fetch strategies
# ---------------------------------------------------------------------------

async def _read_tweet_via_syndication(tweet_id: str) -> str:
    """Fetch a single tweet's full text via the public syndication API. "" on failure."""
    token = _syndication_token(tweet_id)
    url = f"{_SYNDICATION_URL}?id={tweet_id}&lang=en&token={token}"
    try:
        status, body = await _http_get(url, accept="application/json")
    except Exception as exc:  # noqa: BLE001 — network failures are non-fatal here
        log.warning("Tweet syndication fetch failed", tweet_id=tweet_id, error=str(exc))
        return ""
    if status != 200 or not body.strip():
        log.warning("Tweet syndication empty/non-200", tweet_id=tweet_id, status=status)
        return ""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        log.warning("Tweet syndication returned non-JSON", tweet_id=tweet_id)
        return ""
    if not isinstance(data, dict):
        return ""
    text = _build_tweet_text(data)
    if text:
        log.info("Tweet read via syndication", tweet_id=tweet_id, chars=len(text))
    return text


async def _read_tweet_via_nitter(tweet_id: str) -> str:
    """Last-resort: render the thread from a nitter mirror and strip to text."""
    from mymem.pipeline.readers import _html_to_text

    for instance in _NITTER_INSTANCES:
        url = f"{instance}/i/status/{tweet_id}"
        try:
            status, body = await _http_get(url, accept="text/html")
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            log.warning("Nitter fetch failed", instance=instance, error=str(exc))
            continue
        if status != 200 or not body.strip():
            continue
        text = _html_to_text(body)
        if text and len(text.strip()) > 50:
            log.info("Tweet read via nitter", tweet_id=tweet_id, instance=instance)
            return text.strip()
    return ""


# ---------------------------------------------------------------------------
# Reddit text assembly (pure)
# ---------------------------------------------------------------------------

def _reddit_json_url(url: str) -> str:
    """Turn a Reddit permalink into its no-auth ``.json`` API URL."""
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if not base.endswith(".json"):
        base += ".json"
    return f"{base}?raw_json=1&limit={_MAX_REDDIT_COMMENTS}"


def _build_reddit_text(data: Any, *, max_comments: int = _MAX_REDDIT_COMMENTS) -> str:
    """Assemble post body + top comments from a Reddit listing JSON response."""
    if not isinstance(data, list) or not data:
        return ""

    parts: list[str] = []
    post_children = (
        data[0].get("data", {}).get("children", [])
        if isinstance(data[0], dict)
        else []
    )
    if post_children:
        post = post_children[0].get("data", {})
        author = post.get("author", "")
        title = (post.get("title") or "").strip()
        selftext = (post.get("selftext") or "").strip()
        parts.append(f"[Reddit post by u/{author}]" if author else "[Reddit post]")
        if title:
            parts.append(f"# {title}")
        if selftext:
            parts.append(selftext)
        elif post.get("url"):
            parts.append(f"Link: {post['url']}")

    if len(data) > 1 and isinstance(data[1], dict):
        comments = data[1].get("data", {}).get("children", [])
        lines: list[str] = []
        for child in comments:
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue
            cdata = child.get("data", {})
            body = (cdata.get("body") or "").strip()
            if not body:
                continue
            lines.append(f"- u/{cdata.get('author', '')}: {body}")
            if len(lines) >= max_comments:
                break
        if lines:
            parts.append("Top comments:")
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# SourceReader strategies
# ---------------------------------------------------------------------------

class TweetSourceReader(SourceReader):
    """Read a tweet/thread via the syndication API, falling back to nitter."""

    def can_handle(self, source: str, source_type: str) -> bool:
        return source_type == "tweet" or _is_twitter_url(source)

    async def read(self, source: str, source_type: str) -> str:
        tweet_id = _extract_tweet_id(source)
        if not tweet_id:
            raise ValueError(f"Could not extract a tweet ID from URL: {source!r}")

        text = await _read_tweet_via_syndication(tweet_id)
        if text:
            return text

        text = await _read_tweet_via_nitter(tweet_id)
        if text:
            return text

        raise RuntimeError(
            f"Could not fetch tweet {tweet_id}. X serves no readable content to "
            "non-browser clients and all fallbacks failed. Paste the thread text "
            "directly via 'Paste text' instead."
        )


class RedditSourceReader(SourceReader):
    """Read a Reddit post + top comments via the no-auth ``.json`` endpoint."""

    def can_handle(self, source: str, source_type: str) -> bool:
        return source.startswith(("http://", "https://")) and _is_reddit_url(source)

    async def read(self, source: str, source_type: str) -> str:
        url = _reddit_json_url(source)
        try:
            status, body = await _http_get(url, accept="application/json")
        except Exception as exc:  # noqa: BLE001 — surface as actionable error
            raise RuntimeError(f"Reddit fetch failed for {source}: {exc}") from exc

        if status in (403, 429):
            raise RuntimeError(
                f"Reddit blocked the request ({status}) for {source}. "
                "Try pasting the text directly via 'Paste text'."
            )
        if status != 200:
            raise RuntimeError(f"Reddit returned HTTP {status} for {source}.")

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Reddit returned non-JSON for {source}.") from exc

        text = _build_reddit_text(data)
        if not text.strip():
            raise RuntimeError(f"No readable Reddit content found at {source}.")
        log.info("Reddit read via JSON API", source=source, chars=len(text))
        return text
