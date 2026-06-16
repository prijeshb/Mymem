"""
Tests for mymem/knowledge/claims.py — bi-temporal claims store (claims.db).

Implements ADR-011 / ADR-015 Phase 2: each atomic proposition becomes a claim
keyed on the page's stable ULID (ADR-013), with verbatim provenance, confidence,
and bi-temporal validity (SUPERSEDE never hard-deletes).

Pure SQLite — no LLM, no embedder. Target: 100% coverage (same standard as
rag/store.py and graph/store.py).
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mymem.knowledge.claims import (
    Claim,
    ClaimsStats,
    NewClaim,
    add_claim,
    claims_for_page,
    claims_for_source,
    corroborate,
    delete_source,
    get_claim,
    init_db,
    replace_source_claims,
    stats,
    supersede_claim,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    return p


def _add(db: Path, **over: object) -> Claim:
    kw: dict[str, object] = dict(
        page_id="01HPAGE0000000000000000001",
        text="Self-attention lets every token attend to every other token.",
        source_id="raw/articles/attention.md",
    )
    kw.update(over)
    return add_claim(db, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_file_and_is_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "claims.db"
        init_db(p)
        init_db(p)  # second call must not raise
        assert p.exists()

    def test_empty_stats_on_fresh_db(self, db: Path) -> None:
        assert stats(db) == ClaimsStats(total=0, active=0, superseded=0)


# ---------------------------------------------------------------------------
# add_claim
# ---------------------------------------------------------------------------

class TestAddClaim:
    def test_returns_active_claim_with_defaults(self, db: Path) -> None:
        c = _add(db)
        assert c.id > 0
        assert c.page_id == "01HPAGE0000000000000000001"
        assert c.source_span == ""
        assert c.confidence == 1.0
        assert c.valid_from == date.today().isoformat()
        assert c.valid_to is None          # active
        assert c.superseded_by is None
        assert c.created  # ISO datetime stamped

    def test_persists_span_and_confidence(self, db: Path) -> None:
        c = _add(db, source_span="every token attend to every other token", confidence=0.5)
        assert c.source_span == "every token attend to every other token"
        assert c.confidence == 0.5

    def test_custom_valid_from(self, db: Path) -> None:
        c = _add(db, valid_from="2026-01-01")
        assert c.valid_from == "2026-01-01"

    @pytest.mark.parametrize("blank_field", ["page_id", "text", "source_id"])
    def test_rejects_blank_required_fields(self, db: Path, blank_field: str) -> None:
        with pytest.raises(ValueError):
            _add(db, **{blank_field: "   "})

    @pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
    def test_rejects_out_of_range_confidence(self, db: Path, bad: float) -> None:
        with pytest.raises(ValueError):
            _add(db, confidence=bad)


# ---------------------------------------------------------------------------
# get_claim / queries
# ---------------------------------------------------------------------------

class TestQueries:
    def test_get_claim_roundtrip(self, db: Path) -> None:
        c = _add(db)
        assert get_claim(db, c.id) == c

    def test_get_missing_returns_none(self, db: Path) -> None:
        assert get_claim(db, 999) is None

    def test_claims_for_page_filters_by_page(self, db: Path) -> None:
        _add(db, page_id="01HPAGE0000000000000000001")
        _add(db, page_id="01HPAGE0000000000000000002")
        rows = claims_for_page(db, "01HPAGE0000000000000000001")
        assert len(rows) == 1
        assert rows[0].page_id == "01HPAGE0000000000000000001"

    def test_claims_for_page_active_only(self, db: Path) -> None:
        keep = _add(db)
        old = _add(db)
        supersede_claim(db, old.id, by=keep.id)
        all_rows = claims_for_page(db, old.page_id)
        active = claims_for_page(db, old.page_id, active_only=True)
        assert len(all_rows) == 2
        assert [c.id for c in active] == [keep.id]

    def test_claims_for_source(self, db: Path) -> None:
        _add(db, source_id="raw/a.md")
        _add(db, source_id="raw/b.md")
        assert len(claims_for_source(db, "raw/a.md")) == 1


# ---------------------------------------------------------------------------
# supersede_claim (bi-temporal, never deletes)
# ---------------------------------------------------------------------------

class TestSupersede:
    def test_marks_old_claim_superseded(self, db: Path) -> None:
        new = _add(db, text="Attention was introduced in 2017.")
        old = _add(db, text="Attention was introduced in 1995.")
        supersede_claim(db, old.id, by=new.id, valid_to="2026-06-15")
        refreshed = get_claim(db, old.id)
        assert refreshed is not None
        assert refreshed.valid_to == "2026-06-15"
        assert refreshed.superseded_by == new.id
        # The superseding claim stays active; nothing is deleted.
        assert get_claim(db, new.id).valid_to is None  # type: ignore[union-attr]
        assert stats(db) == ClaimsStats(total=2, active=1, superseded=1)

    def test_valid_to_defaults_to_today(self, db: Path) -> None:
        new = _add(db)
        old = _add(db)
        supersede_claim(db, old.id, by=new.id)
        assert get_claim(db, old.id).valid_to == date.today().isoformat()  # type: ignore[union-attr]

    def test_raises_when_old_missing(self, db: Path) -> None:
        new = _add(db)
        with pytest.raises(ValueError):
            supersede_claim(db, 999, by=new.id)

    def test_raises_when_superseding_claim_missing(self, db: Path) -> None:
        old = _add(db)
        with pytest.raises(ValueError):
            supersede_claim(db, old.id, by=999)


# ---------------------------------------------------------------------------
# corroborate (NOOP/MERGE confidence bump, clamped)
# ---------------------------------------------------------------------------

class TestCorroborate:
    def test_bumps_confidence(self, db: Path) -> None:
        c = _add(db, confidence=0.5)
        out = corroborate(db, c.id, delta=0.2)
        assert out.confidence == pytest.approx(0.7)

    def test_clamps_at_one(self, db: Path) -> None:
        c = _add(db, confidence=0.95)
        out = corroborate(db, c.id, delta=0.5)
        assert out.confidence == 1.0

    def test_raises_when_missing(self, db: Path) -> None:
        with pytest.raises(ValueError):
            corroborate(db, 999)


# ---------------------------------------------------------------------------
# delete_source (cascade; nulls dangling superseded_by)
# ---------------------------------------------------------------------------

class TestDeleteSource:
    def test_deletes_only_matching_source(self, db: Path) -> None:
        _add(db, source_id="raw/a.md")
        _add(db, source_id="raw/a.md")
        _add(db, source_id="raw/b.md")
        removed = delete_source(db, "raw/a.md")
        assert removed == 2
        assert claims_for_source(db, "raw/a.md") == []
        assert len(claims_for_source(db, "raw/b.md")) == 1

    def test_no_match_returns_zero(self, db: Path) -> None:
        assert delete_source(db, "raw/missing.md") == 0

    def test_nulls_dangling_superseded_by(self, db: Path) -> None:
        # An older claim (source b) was superseded by a newer one (source a).
        old = _add(db, source_id="raw/b.md")
        new = _add(db, source_id="raw/a.md")
        supersede_claim(db, old.id, by=new.id)
        delete_source(db, "raw/a.md")   # removes the superseding claim
        refreshed = get_claim(db, old.id)
        assert refreshed is not None
        assert refreshed.superseded_by is None  # pointer cleaned, no orphan FK


# ---------------------------------------------------------------------------
# replace_source_claims (transactional per-source rebuild)
# ---------------------------------------------------------------------------

class TestReplaceSourceClaims:
    def test_replaces_prior_claims_for_source(self, db: Path) -> None:
        _add(db, source_id="raw/a.md", text="old claim")
        out = replace_source_claims(
            db,
            "raw/a.md",
            [
                NewClaim(page_id="01HPAGE0000000000000000001", text="new claim 1"),
                NewClaim(
                    page_id="01HPAGE0000000000000000001",
                    text="new claim 2",
                    source_span="grounded quote",
                    confidence=0.8,
                ),
            ],
        )
        rows = claims_for_source(db, "raw/a.md")
        assert {c.text for c in rows} == {"new claim 1", "new claim 2"}
        assert len(out) == 2
        assert out[1].source_span == "grounded quote"
        assert out[1].confidence == 0.8

    def test_empty_list_clears_source(self, db: Path) -> None:
        _add(db, source_id="raw/a.md")
        out = replace_source_claims(db, "raw/a.md", [])
        assert out == []
        assert claims_for_source(db, "raw/a.md") == []

    def test_leaves_other_sources_untouched(self, db: Path) -> None:
        _add(db, source_id="raw/b.md", text="keep me")
        replace_source_claims(
            db, "raw/a.md", [NewClaim(page_id="01HPAGE0000000000000000001", text="x")]
        )
        assert len(claims_for_source(db, "raw/b.md")) == 1


# ---------------------------------------------------------------------------
# Integration: ingest_source persists claims keyed on the page's stable id
# ---------------------------------------------------------------------------

_IDEAS = [
    {"title": "Concept Alpha", "summary": "Alpha is fundamental.", "domain": "tech"},
    {"title": "Concept Beta", "summary": "Beta builds on Alpha.", "domain": "tech"},
]

_CAND_ID_RE = re.compile(r"^\s*(\d+):", re.MULTILINE)


# Deterministic per-text 768-dim vectors: identical text → identical (cosine 1.0),
# distinct text → orthogonal (cosine 0.0). Mirrors reality (distinct concepts ≠ candidates;
# re-ingesting the same concept retrieves its prior claim), unlike an all-identical stub.
_SLOT: dict[str, int] = {}


def _vec_for(text: str) -> list[float]:
    if text not in _SLOT:
        _SLOT[text] = len(_SLOT) % 768
    v = [0.0] * 768
    v[_SLOT[text]] = 1.0
    return v


class _StubEmbedder:
    """768-dim one-hot per unique text — no Ollama, deterministic, collision-free."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vec_for(t) for t in texts]


def _ingest_router() -> object:
    """Reconcile-aware fake: extraction → ideas, compile → body, decision → NOOP on the
    first listed candidate (parsed out of the prompt) so re-ingest corroborates."""
    from mymem.pipeline.router import ModelRouter

    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        if "reconcile" in system.lower():
            m = _CAND_ID_RE.search(prompt)
            if m:
                return json.dumps({"decision": "NOOP", "target_claim_id": int(m.group(1))})
            return json.dumps({"decision": "ADD"})
        if "json" in system.lower():
            return json.dumps(_IDEAS)
        return "# Body\n\nCompiled page content."

    return ModelRouter(llm_fn=fake_llm)


async def _run_ingest(src: Path, wiki_dir: Path, db_path: Path) -> None:
    from mymem.pipeline.ingest import ingest_source

    # Patch the fire-and-forget RAG indexer and the claim embedder so the test needs no Ollama.
    # _rag_index_wiki is called via ingest's namespace; _build_claim_embedder is looked up in
    # ingest_claims (where _persist_claims lives), so patch it there.
    with (
        patch("mymem.pipeline.ingest._rag_index_wiki", new=AsyncMock(return_value=None)),
        patch("mymem.pipeline.ingest_claims._build_claim_embedder", return_value=_StubEmbedder()),
    ):
        await ingest_source(
            str(src),
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            db_path=db_path,
            router=_ingest_router(),  # type: ignore[arg-type]
        )


class TestIngestPersistsClaims:
    @pytest.mark.asyncio
    async def test_persists_one_claim_per_page_keyed_on_page_id(self, tmp_path: Path) -> None:
        from mymem.wiki.page import list_pages

        src = tmp_path / "raw" / "a.md"
        src.parent.mkdir(parents=True)
        src.write_text("Alpha is fundamental. Beta builds on Alpha.")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        db_path = tmp_path / "data" / "mymem.db"

        await _run_ingest(src, wiki_dir, db_path)

        claims_db = db_path.parent / "claims.db"
        assert claims_db.exists()
        assert stats(claims_db) == ClaimsStats(total=2, active=2, superseded=0)

        page_ids = {p.id for p in list_pages(wiki_dir)}
        for pid in page_ids:
            page_claims = claims_for_page(claims_db, pid)
            assert len(page_claims) == 1  # claim keyed on the stable page id

    @pytest.mark.asyncio
    async def test_reingest_compounds_not_accretes(self, tmp_path: Path) -> None:
        src = tmp_path / "raw" / "a.md"
        src.parent.mkdir(parents=True)
        src.write_text("Alpha is fundamental. Beta builds on Alpha.")
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        db_path = tmp_path / "data" / "mymem.db"

        await _run_ingest(src, wiki_dir, db_path)
        await _run_ingest(src, wiki_dir, db_path)  # re-ingest → retrieve→NOOP→corroborate

        claims_db = db_path.parent / "claims.db"
        # The decision pipeline corroborates the existing claims — it must not duplicate them.
        assert stats(claims_db) == ClaimsStats(total=2, active=2, superseded=0)
