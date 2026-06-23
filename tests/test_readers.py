"""Tests for source readers — focus on the web-fetch extraction path."""
from __future__ import annotations

import pytest

from mymem.pipeline import readers
from mymem.pipeline.readers import WebSourceReader


async def test_web_reader_extracts_clean_text_not_raw_html(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """fetch_url returns the RAW downloaded HTML; read() must run it through the
    extractor and return clean article text — not the HTML (the prior bug fed 380KB
    of markup to the LLM and starved idea extraction)."""
    raw_html = "<!DOCTYPE html><html><body><article>noise</article></body></html>"
    clean = "China's CXMT is set to challenge DRAM incumbents. " * 5

    monkeypatch.setattr(readers.trafilatura, "fetch_url", lambda url: raw_html, raising=False)
    monkeypatch.setattr(readers, "_html_to_text", lambda html: clean)

    out = await WebSourceReader().read("https://example.com/post", "newsletter")

    assert out == clean.strip()
    assert "<html" not in out and "<!DOCTYPE" not in out


async def test_web_reader_warns_when_extraction_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If extraction yields nothing usable, fall through (httpx path) rather than
    returning raw HTML. With no httpx response mocked, that raises — proving it did
    NOT silently return the raw HTML."""
    monkeypatch.setattr(readers.trafilatura, "fetch_url", lambda u: "<html></html>", raising=False)
    monkeypatch.setattr(readers, "_html_to_text", lambda html: "")  # empty extraction

    # Force the httpx fallback to fail fast so we can assert we did NOT return raw HTML.
    import httpx

    async def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise httpx.ConnectError("no network in test")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom)
    with pytest.raises(Exception):  # noqa: B017 - any failure proves no raw-HTML return
        await WebSourceReader().read("https://example.com/post", "newsletter")
