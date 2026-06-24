"""Tests for the MCP access layer (ADR-017, Phase 1 — read-only).

Handlers are exercised directly (no live MCP client) and without a live LLM (the
`ask` test injects a fake router). Mirrors the project's no-LLM-in-tests rule.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from mymem.interop.mcp import auth, resources, tools
from mymem.interop.mcp.auth import AuthError, Scope
from mymem.interop.mcp.context import WikiContext, context_from_settings
from mymem.interop.mcp.payloads import AskResult, ConceptPayload, ConceptStub, GapItem
from mymem.wiki.index import IndexManager
from mymem.wiki.page import write_page
from mymem.wiki.types import IndexEntry, TagDomain, WikiPage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    write_page(
        WikiPage(
            title="Self Attention",
            body="# Self Attention\n\nScaled dot-product attention over a sequence. "
            "See [[Multi Head Attention]].",
            path=wiki_dir / "self-attention.md",
            tags=("attention",),
            domain=TagDomain.TECH,
            id="01TEST0000000000000000SELF",
        ),
        stamp_updated=False,
    )
    write_page(
        WikiPage(
            title="Multi Head Attention",
            body="# Multi Head Attention\n\nParallel attention heads run in parallel.",
            path=wiki_dir / "multi-head-attention.md",
            tags=("attention", "transformers"),
            domain=TagDomain.TECH,
            id="01TEST0000000000000000MHAX",
        ),
        stamp_updated=False,
    )
    idx = IndexManager(wiki_dir / "index.md")
    idx.save(
        [
            IndexEntry(
                title="Self Attention",
                path=Path("self-attention.md"),
                summary="Scaled dot-product attention",
                category="tech",
                domain=TagDomain.TECH,
                source_count=1,
            ),
            IndexEntry(
                title="Multi Head Attention",
                path=Path("multi-head-attention.md"),
                summary="Parallel attention heads",
                category="tech",
                domain=TagDomain.TECH,
                source_count=1,
            ),
        ]
    )
    return wiki_dir


@pytest.fixture
def ctx(wiki: Path, tmp_path: Path) -> WikiContext:
    return WikiContext(
        wiki_dir=wiki,
        index_path=wiki / "index.md",
        log_path=wiki / "log.md",
        graph_db=tmp_path / "graph.db",
        rag_db=tmp_path / "rag.db",
    )


class _FakeRouter:
    """Stand-in for ModelRouter — query_wiki only needs `.call` + `.session_cost`."""

    session_cost = 0.0

    async def call(self, prompt: str, *, task: str, system: str) -> str:
        return "Attention lets tokens attend to one another."


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------

def test_payload_as_dict_roundtrip() -> None:
    stub = ConceptStub(title="T", slug="t", domain="tech", description="d", score=1.5)
    assert stub.as_dict() == {
        "title": "T", "slug": "t", "domain": "tech", "description": "d", "score": 1.5,
    }
    assert AskResult("q", "a", ["c"]).as_dict()["citations"] == ["c"]
    assert GapItem("X", 3).as_dict() == {"concept": "X", "inbound_refs": 3}


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_check_token_ok() -> None:
    assert auth.check_token("secret", "secret") is Scope.READ


def test_check_token_fail_closed_when_unset() -> None:
    with pytest.raises(AuthError):
        auth.check_token("anything", None)
    with pytest.raises(AuthError):
        auth.check_token("anything", "")


def test_check_token_rejects_mismatch_and_missing() -> None:
    with pytest.raises(AuthError):
        auth.check_token("wrong", "secret")
    with pytest.raises(AuthError):
        auth.check_token(None, "secret")


def test_extract_bearer_parses_header() -> None:
    assert auth.extract_bearer({"authorization": "Bearer abc123"}) == "abc123"
    assert auth.extract_bearer({"Authorization": "bearer abc123"}) == "abc123"  # case-insensitive
    assert auth.extract_bearer({"authorization": "abc123"}) == "abc123"  # bare token tolerated
    assert auth.extract_bearer({}) is None


def test_authorize_request_local_allows_without_token() -> None:
    # no HTTP headers => stdio/in-memory local transport => allowed (not reachable)
    assert auth.authorize_request({}, "secret") is Scope.READ
    assert auth.authorize_request({}, None) is Scope.READ


def test_authorize_request_http_requires_valid_token() -> None:
    headers = {"host": "x", "authorization": "Bearer secret"}
    assert auth.authorize_request(headers, "secret") is Scope.READ
    # HTTP request with wrong / missing token is rejected
    with pytest.raises(AuthError):
        auth.authorize_request({"host": "x", "authorization": "Bearer nope"}, "secret")
    with pytest.raises(AuthError):
        auth.authorize_request({"host": "x"}, "secret")
    # HTTP request when no token is configured server-side => fail-closed
    with pytest.raises(AuthError):
        auth.authorize_request({"host": "x", "authorization": "Bearer secret"}, None)


# ---------------------------------------------------------------------------
# search_wiki
# ---------------------------------------------------------------------------

def test_search_wiki_returns_stubs(ctx: WikiContext) -> None:
    results = tools.search_wiki(ctx, "attention", limit=10)
    assert results and all(isinstance(s, ConceptStub) for s in results)
    by_title = {s.title: s for s in results}
    assert "Self Attention" in by_title
    assert by_title["Self Attention"].slug == "self-attention"
    assert by_title["Self Attention"].domain == "tech"


def test_search_wiki_domain_filter_excludes(ctx: WikiContext) -> None:
    assert tools.search_wiki(ctx, "attention", domain="finance") == []


def test_search_wiki_missing_index_returns_empty(tmp_path: Path) -> None:
    ctx = WikiContext(
        wiki_dir=tmp_path,
        index_path=tmp_path / "nope.md",
        log_path=tmp_path / "log.md",
        graph_db=tmp_path / "g.db",
        rag_db=tmp_path / "r.db",
    )
    assert tools.search_wiki(ctx, "x") == []


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------

def test_get_page_returns_okf_payload(ctx: WikiContext) -> None:
    payload = tools.get_page(ctx, "self-attention")
    assert isinstance(payload, ConceptPayload)
    assert payload.frontmatter["type"] == "tech"
    assert payload.frontmatter["title"] == "Self Attention"
    assert payload.frontmatter["id"] == "01TEST0000000000000000SELF"  # identity preserved
    assert payload.uri == "okf://concept/self-attention.md"
    # wikilink rewritten to an OKF markdown link
    assert "[Multi Head Attention](/multi-head-attention.md)" in payload.body
    # description is plain text — no leaked [[wikilink]] syntax (ADR-017 F1)
    assert "[[" not in str(payload.frontmatter["description"])
    assert "Multi Head Attention" in str(payload.frontmatter["description"])


def test_get_page_by_ulid_id(ctx: WikiContext) -> None:
    payload = tools.get_page(ctx, "01TEST0000000000000000MHAX")
    assert payload is not None
    assert payload.frontmatter["title"] == "Multi Head Attention"


def test_get_page_unknown_returns_none(ctx: WikiContext) -> None:
    assert tools.get_page(ctx, "does-not-exist") is None


def test_get_page_redacts_pii_when_enabled(wiki: Path, tmp_path: Path) -> None:
    # A page containing PII; serve it through a redact-on-serve context (ADR-018).
    from mymem.wiki.types import TagDomain, WikiPage

    write_page(
        WikiPage(
            title="Contact Card",
            body="# Contact Card\n\nReach me at jane@acme.com or 555-123-4567.",
            path=wiki / "contact-card.md",
            domain=TagDomain.PERSONAL,
            id="01TEST0000000000000000PIIX",
        ),
        stamp_updated=False,
    )
    redacting = WikiContext(
        wiki_dir=wiki, index_path=wiki / "index.md", log_path=wiki / "log.md",
        graph_db=tmp_path / "g.db", rag_db=tmp_path / "r.db", redact_pii=True,
    )
    payload = tools.get_page(redacting, "contact-card")
    assert payload is not None
    assert "[EMAIL]" in payload.body and "[PHONE]" in payload.body
    assert "jane@acme.com" not in payload.body

    # Default context (redact_pii=False) leaves content untouched.
    plain = tools.get_page(
        WikiContext(wiki_dir=wiki, index_path=wiki / "index.md", log_path=wiki / "log.md",
                    graph_db=tmp_path / "g.db", rag_db=tmp_path / "r.db"),
        "contact-card",
    )
    assert plain is not None and "jane@acme.com" in plain.body


# ---------------------------------------------------------------------------
# list_concepts
# ---------------------------------------------------------------------------

def test_list_concepts_all(ctx: WikiContext) -> None:
    titles = {c.title for c in tools.list_concepts(ctx)}
    assert titles == {"Self Attention", "Multi Head Attention"}


def test_list_concepts_tag_filter(ctx: WikiContext) -> None:
    titles = {c.title for c in tools.list_concepts(ctx, tag="transformers")}
    assert titles == {"Multi Head Attention"}


def test_list_concepts_domain_filter(ctx: WikiContext) -> None:
    assert tools.list_concepts(ctx, domain="finance") == []


# ---------------------------------------------------------------------------
# knowledge_gaps
# ---------------------------------------------------------------------------

def test_knowledge_gaps_missing_db(ctx: WikiContext) -> None:
    assert tools.knowledge_gaps(ctx) == []


def test_knowledge_gaps_ranks_pageless_entities(ctx: WikiContext, tmp_path: Path) -> None:
    db = tmp_path / "graph_built.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical TEXT, page_id TEXT);
        CREATE TABLE mentions (entity_id INTEGER, page_id TEXT);
        INSERT INTO entities (id, canonical, page_id) VALUES (1, 'Rotary Embeddings', NULL);
        INSERT INTO mentions (entity_id, page_id) VALUES (1, 'p1'), (1, 'p2');
        """
    )
    conn.commit()
    conn.close()
    gctx = dataclasses.replace(ctx, graph_db=db)
    assert tools.knowledge_gaps(gctx) == [GapItem(concept="Rotary Embeddings", inbound_refs=2)]


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

async def test_ask_synthesizes_with_fake_router(ctx: WikiContext) -> None:
    answering_ctx = dataclasses.replace(ctx, router=_FakeRouter())
    result = await tools.ask(answering_ctx, "attention heads")
    assert isinstance(result, AskResult)
    assert "attend" in result.answer  # came from the fake router, so a page matched
    assert "Self Attention" in result.citations
    assert result.question == "attention heads"


async def test_ask_requires_router(ctx: WikiContext) -> None:
    with pytest.raises(ValueError):
        await tools.ask(ctx, "q")


# ---------------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------------

def test_okf_index_resource(ctx: WikiContext) -> None:
    out = resources.okf_index(ctx)
    assert out.startswith("# Wiki")
    assert "[Self Attention](/self-attention.md)" in out
    assert "[Multi Head Attention](/multi-head-attention.md)" in out


def test_okf_concept_resource(ctx: WikiContext) -> None:
    out = resources.okf_concept(ctx, "self-attention")
    assert out is not None
    assert out.startswith("---")
    assert "title: Self Attention" in out


def test_okf_concept_resource_missing(ctx: WikiContext) -> None:
    assert resources.okf_concept(ctx, "nope") is None


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def test_context_from_settings_derives_paths(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        paths=SimpleNamespace(wiki=tmp_path / "wiki", db=tmp_path / "data" / "mymem.db"),
        security=SimpleNamespace(pii="redact"),
    )
    ctx = context_from_settings(settings)  # type: ignore[arg-type]
    assert ctx.index_path == tmp_path / "wiki" / "index.md"
    assert ctx.log_path == tmp_path / "wiki" / "log.md"
    assert ctx.graph_db == tmp_path / "data" / "graph.db"
    assert ctx.rag_db == tmp_path / "data" / "rag.db"
    assert ctx.router is None
    assert ctx.redact_pii is True  # pii != "off"


# ---------------------------------------------------------------------------
# server (skipped if fastmcp not installed)
# ---------------------------------------------------------------------------

def test_build_mcp_server_smoke(ctx: WikiContext) -> None:
    pytest.importorskip("fastmcp")
    from mymem.interop.mcp.server import build_mcp_server

    server = build_mcp_server(ctx)
    assert server is not None


# ---------------------------------------------------------------------------
# project-root detection (mcp serve launched from another CWD)
# ---------------------------------------------------------------------------

def test_detect_project_root_prefers_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from mymem.cli import _detect_project_root

    monkeypatch.setenv("MYMEM_PROJECT_DIR", str(tmp_path))
    assert _detect_project_root() == tmp_path


def test_detect_project_root_ignores_bad_env_falls_back_to_package(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from mymem.cli import _detect_project_root

    monkeypatch.setenv("MYMEM_PROJECT_DIR", str(Path("does") / "not" / "exist"))
    root = _detect_project_root()
    # editable install → repo root carrying pyproject.toml
    assert root is not None
    assert (root / "pyproject.toml").exists()
