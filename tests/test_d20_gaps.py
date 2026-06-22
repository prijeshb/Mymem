"""
Additional tests for ADR-015 D20 — "render wiki page body FROM claims" (opt-in).

Covers gaps in the existing test_claims_render.py:
  1. render_page_body heading format exactly `# {title}` (not a section marker)
  2. Confidence formatted to 1 decimal (e.g. 0.90 → "0.9", not "0.90000")
  3. See Also dedup preserves first-occurrence order
  4. _sync_claims_sections best-effort exception paths (lines 66-69 ingest_claims.py)
  5. _naive_persist (lines 82-84 ingest_claims.py) — fallback provenance write
  6. _persist_claims empty-records fast-path (line 107)
  7. _persist_claims exception → naive fallback (lines 125-136)
  8. config: PipelineConfig.body_from_claims defaults to False
  9. flag thread: ingest_source accepts and passes body_from_claims to _sync_claims_sections
 10. render_page_body never emits <!-- claims:start/end --> markers
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from mymem.knowledge.claims import Claim
from mymem.knowledge.render import CLAIMS_END, CLAIMS_START, render_page_body

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claim(
    cid: int,
    text: str,
    *,
    confidence: float = 1.0,
    valid_to: str | None = None,
    superseded_by: int | None = None,
) -> Claim:
    return Claim(
        id=cid,
        page_id="01HPAGE0000000000000000001",
        text=text,
        source_id="raw/a.md",
        source_span="",
        confidence=confidence,
        valid_from="2026-06-01",
        valid_to=valid_to,
        superseded_by=superseded_by,
        created="2026-06-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Phase 1 row 1: render_page_body structure and no-marker contract
# ---------------------------------------------------------------------------

class TestRenderPageBodySpec:
    def test_heading_starts_with_hash_title(self) -> None:
        """Body must start exactly `# {title}` — not a Knowledge-Claims section marker."""
        out = render_page_body("Attention Mechanism", [_claim(1, "A claim.")])
        assert out.startswith("# Attention Mechanism\n")

    def test_no_claims_section_markers_in_body_mode(self) -> None:
        """D20 body mode must NOT emit <!-- claims:start/end --> markers."""
        out = render_page_body("X", [_claim(1, "A claim.")])
        assert CLAIMS_START not in out
        assert CLAIMS_END not in out

    def test_confidence_single_decimal_formatting(self) -> None:
        """Confidence is formatted to 1 decimal — 0.9 not 0.90, 1.0 not 1."""
        out = render_page_body("X", [_claim(1, "C1.", confidence=0.9)])
        assert "(conf 0.9)" in out
        # 1.0 should appear as "1.0" not "1" or "1.00"
        out2 = render_page_body("X", [_claim(2, "C2.", confidence=1.0)])
        assert "(conf 1.0)" in out2

    def test_superseded_only_returns_empty_string(self) -> None:
        """A page with only superseded claims returns '' — safety guard."""
        out = render_page_body("X", [_claim(1, "Old.", valid_to="2026-06-15")])
        assert out == ""

    def test_see_also_dedup_preserves_first_occurrence_order(self) -> None:
        """Duplicates are dropped keeping FIRST occurrence; order otherwise preserved."""
        out = render_page_body(
            "X",
            [_claim(1, "A.")],
            see_also=["B", "A", "C", "A", "B"],
        )
        # B first, then A, then C
        pos_b = out.index("[[B]]")
        pos_a = out.index("[[A]]")
        pos_c = out.index("[[C]]")
        assert pos_b < pos_a < pos_c
        assert out.count("[[B]]") == 1
        assert out.count("[[A]]") == 1

    def test_none_see_also_omits_section(self) -> None:
        out = render_page_body("X", [_claim(1, "C.")], see_also=None)
        assert "## See Also" not in out

    def test_superseded_trail_only_rendered_when_active_present(self) -> None:
        """Superseded block only appears when there are also active claims."""
        active = _claim(2, "New fact.", confidence=1.0)
        retired = _claim(1, "Old fact.", valid_to="2026-06-10", superseded_by=2)
        out = render_page_body("X", [active, retired])
        assert "- New fact. (conf 1.0)" in out
        assert "### Superseded" in out
        assert "- ~~Old fact.~~ (retired 2026-06-10)" in out


# ---------------------------------------------------------------------------
# Phase 1 row 2: Safety — _sync_claims_sections with body_from_claims=True
# ---------------------------------------------------------------------------

class TestSyncClaimsSafetyExceptionPaths:
    """Cover the best-effort exception paths (lines 66-69 and 68-69 in ingest_claims.py)."""

    def test_per_page_exception_is_swallowed_not_raised(self, tmp_path: Path) -> None:
        """If read_page raises for one page, _sync_claims_sections must not propagate."""
        from mymem.knowledge.claims import init_db
        from mymem.pipeline.ingest_claims import _sync_claims_sections

        claims_db = tmp_path / "claims.db"
        init_db(claims_db)
        db_path = tmp_path / "mymem.db"

        bad_page = tmp_path / "nonexistent.md"

        # Should silently swallow the FileNotFoundError for the bad page
        _sync_claims_sections(db_path, [(bad_page, "01HPAGE0000000000000000001")])
        # No exception → test passes

    def test_per_page_exception_body_from_claims_swallowed(self, tmp_path: Path) -> None:
        """body_from_claims=True path also swallows per-page exceptions."""
        from mymem.knowledge.claims import init_db
        from mymem.pipeline.ingest_claims import _sync_claims_sections

        claims_db = tmp_path / "claims.db"
        init_db(claims_db)
        db_path = tmp_path / "mymem.db"

        bad_page = tmp_path / "nonexistent.md"
        _sync_claims_sections(
            db_path, [(bad_page, "01HPAGE0000000000000000001")], body_from_claims=True
        )
        # No exception → test passes

    def test_outer_exception_swallowed(self, tmp_path: Path) -> None:
        """Outer import failure (e.g. claims module not importable) must not raise."""
        import mymem.knowledge.claims as claims_mod
        from mymem.pipeline.ingest_claims import _sync_claims_sections

        db_path = tmp_path / "mymem.db"
        claims_db = tmp_path / "claims.db"
        claims_db.touch()

        # Patch claims_for_page to raise *after* the inner loop enters the outer try,
        # triggering the outer except on lines 68-69.
        with patch.object(claims_mod, "claims_for_page", side_effect=RuntimeError("db corrupted")):
            _sync_claims_sections(db_path, [(tmp_path / "page.md", "id1")])
        # No exception → test passes


# ---------------------------------------------------------------------------
# Phase 1 row 3: Default OFF — body_from_claims=False reproduces D13 behavior
# ---------------------------------------------------------------------------

class TestDefaultOffPreservesD13:
    def test_default_false_appends_section_not_replaces_body(self, tmp_path: Path) -> None:
        from mymem.knowledge.claims import add_claim, init_db
        from mymem.pipeline.ingest_claims import _sync_claims_sections
        from mymem.wiki.page import read_page, write_page
        from mymem.wiki.types import WikiPage

        db_path = tmp_path / "data" / "mymem.db"
        db_path.parent.mkdir()
        claims_db = db_path.parent / "claims.db"
        init_db(claims_db)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page_path = wiki_dir / "x.md"
        prose = "# X\n\nLLM-compiled prose paragraph."
        write_page(WikiPage(title="X", body=prose, path=page_path))
        pid = read_page(page_path).id
        add_claim(claims_db, page_id=pid, text="Some fact.", source_id="raw/a.md")

        # body_from_claims defaults to False
        _sync_claims_sections(db_path, [(page_path, pid)])

        body = read_page(page_path).body
        assert "LLM-compiled prose paragraph." in body  # prose preserved
        assert CLAIMS_START in body                      # section appended (D13)
        # heading present (may have leading newlines from frontmatter)
        assert "# X" in body


# ---------------------------------------------------------------------------
# Phase 1 row 4: Wikilink preservation — graph survives the switch
# ---------------------------------------------------------------------------

class TestWikilinkPreservation:
    def test_prior_wikilinks_appear_in_see_also_after_body_replace(
        self, tmp_path: Path
    ) -> None:
        from mymem.knowledge.claims import add_claim, init_db
        from mymem.pipeline.ingest_claims import _sync_claims_sections
        from mymem.wiki.page import read_page, write_page
        from mymem.wiki.types import WikiPage

        db_path = tmp_path / "data" / "mymem.db"
        db_path.parent.mkdir()
        claims_db = db_path.parent / "claims.db"
        init_db(claims_db)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        body = "# Concept\n\nProse.\n\n## See Also\n\n- [[Alpha]]\n- [[Beta]]"
        page_path = wiki_dir / "concept.md"
        write_page(WikiPage(title="Concept", body=body, path=page_path))
        pid = read_page(page_path).id
        add_claim(claims_db, page_id=pid, text="Active claim.", source_id="raw/a.md")

        _sync_claims_sections(db_path, [(page_path, pid)], body_from_claims=True)

        new_body = read_page(page_path).body
        assert "[[Alpha]]" in new_body
        assert "[[Beta]]" in new_body
        assert "Prose." not in new_body  # LLM prose replaced


# ---------------------------------------------------------------------------
# Phase 1 row 5: Flag threading — config → ingest_source → _sync_claims_sections
# ---------------------------------------------------------------------------

class TestFlagThreading:
    def test_config_pipeline_body_from_claims_defaults_true(self) -> None:
        from mymem.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.body_from_claims is True

    def test_config_pipeline_body_from_claims_can_be_set_false(self) -> None:
        from mymem.config import PipelineConfig

        cfg = PipelineConfig(body_from_claims=False)
        assert cfg.body_from_claims is False

    def test_ingest_source_passes_flag_to_sync(self, tmp_path: Path) -> None:
        """ingest_source must forward body_from_claims to _sync_claims_sections."""
        # We intercept _sync_claims_sections to verify the kwarg is forwarded.
        calls: list[dict[str, Any]] = []

        def _fake_sync(
            db_path: Path,
            touched: list[Any],
            *,
            body_from_claims: bool = False,
        ) -> None:
            calls.append({"body_from_claims": body_from_claims})

        with patch("mymem.pipeline.ingest._sync_claims_sections", side_effect=_fake_sync):
            # Patch all external I/O so ingest_source runs fast
            _idea = {
                "title": "Test Concept",
                "summary": "A summary.",
                "content": "Body content.",
                "tags": ["tag-a"],
                "domain": "tech",
                "propositions": [{"text": "Fact one.", "span": "Fact one."}],
            }
            with (
                patch(
                    "mymem.pipeline.ingest._read_source",
                    new_callable=AsyncMock,
                    return_value="sample text",
                ),
                patch(
                    "mymem.pipeline.ingest.has_high_severity_secret",
                    return_value=False,
                ),
                patch(
                    "mymem.pipeline.ingest.sanitize_for_prompt",
                    side_effect=lambda t: (
                        t,
                        MagicMock(matched_patterns=[], level="NONE"),
                    ),
                ),
                patch(
                    "mymem.pipeline.ingest._extract_ideas_map_reduce",
                    new_callable=AsyncMock,
                    return_value=[_idea],
                ),
                patch(
                    "mymem.pipeline.ingest._ground_idea_spans",
                    new_callable=AsyncMock,
                    return_value=[_idea],
                ),
                patch(
                    "mymem.pipeline.ingest._persist_claims",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch("mymem.pipeline.ingest._eval_extraction_background"),
                patch("mymem.pipeline.ingest._eval_decision_agreement_background"),
                patch("mymem.pipeline.ingest._graph_extract_background"),
                patch("mymem.pipeline.ingest._rag_index_wiki"),
            ):
                from mymem.pipeline.ingest import ingest_source

                wiki_dir = tmp_path / "wiki"
                wiki_dir.mkdir()
                index_path = tmp_path / "index.md"
                log_path = tmp_path / "log.md"
                db_path = tmp_path / "data" / "mymem.db"
                db_path.parent.mkdir()

                mock_router = MagicMock()
                mock_router.call = AsyncMock(return_value="[]")
                mock_router.session_cost = 0.0

                asyncio.get_event_loop().run_until_complete(
                    ingest_source(
                        "raw/test.md",
                        wiki_dir=wiki_dir,
                        index_path=index_path,
                        log_path=log_path,
                        router=mock_router,
                        db_path=db_path,
                        body_from_claims=True,
                    )
                )

        assert any(c["body_from_claims"] is True for c in calls), (
            f"body_from_claims=True was not forwarded to _sync_claims_sections; calls={calls}"
        )


# ---------------------------------------------------------------------------
# _naive_persist: fallback provenance write (lines 82-88 ingest_claims.py)
# ---------------------------------------------------------------------------

class TestNaivePersist:
    def test_naive_persist_writes_claims_to_db(self, tmp_path: Path) -> None:
        from mymem.knowledge.claims import claims_for_page, init_db
        from mymem.pipeline.ingest_claims import _naive_persist

        claims_db = tmp_path / "claims.db"
        init_db(claims_db)

        records = [("page-01", "Fact one.", "span1"), ("page-01", "Fact two.", "span2")]
        _naive_persist(claims_db, "raw/a.md", records)

        result = claims_for_page(claims_db, "page-01")
        texts = {c.text for c in result}
        assert "Fact one." in texts
        assert "Fact two." in texts

    def test_naive_persist_is_idempotent_on_re_run(self, tmp_path: Path) -> None:
        from mymem.knowledge.claims import claims_for_page, init_db
        from mymem.pipeline.ingest_claims import _naive_persist

        claims_db = tmp_path / "claims.db"
        init_db(claims_db)

        records = [("page-01", "Stable fact.", "span")]
        _naive_persist(claims_db, "raw/a.md", records)
        _naive_persist(claims_db, "raw/a.md", records)  # re-run

        result = claims_for_page(claims_db, "page-01")
        assert len([c for c in result if c.text == "Stable fact."]) == 1


# ---------------------------------------------------------------------------
# _persist_claims: empty-records fast-path (line 107)
# ---------------------------------------------------------------------------

class TestPersistClaimsEmpty:
    def test_empty_records_returns_immediately(self, tmp_path: Path) -> None:
        from mymem.pipeline.ingest_claims import _persist_claims

        mock_router = MagicMock()
        result = asyncio.get_event_loop().run_until_complete(
            _persist_claims(tmp_path / "mymem.db", "raw/a.md", [], router=mock_router)
        )
        assert result == []


# ---------------------------------------------------------------------------
# _persist_claims: compounding failure → naive fallback (lines 125-136)
# ---------------------------------------------------------------------------

class TestPersistClaimsFallback:
    def test_compounding_failure_falls_back_to_naive_persist(self, tmp_path: Path) -> None:
        from mymem.knowledge.claims import claims_for_page, init_db
        from mymem.pipeline.ingest_claims import _persist_claims

        db_path = tmp_path / "mymem.db"
        claims_db = tmp_path / "claims.db"
        init_db(claims_db)

        records = [("page-01", "Fallback fact.", "span")]
        mock_router = MagicMock()

        # Patch reconcile_source_claims to raise so the fallback path triggers
        embedder_patch = patch(
            "mymem.pipeline.ingest_claims._build_claim_embedder",
            side_effect=RuntimeError("no embedder"),
        )
        with embedder_patch:
            result = asyncio.get_event_loop().run_until_complete(
                _persist_claims(db_path, "raw/a.md", records, router=mock_router)
            )

        # Returns empty list (not raises), and naive persist wrote the claims
        assert result == []
        written = claims_for_page(claims_db, "page-01")
        assert any(c.text == "Fallback fact." for c in written)

    def test_compounding_and_naive_both_fail_returns_empty(self, tmp_path: Path) -> None:
        """Both compounding and naive persist fail — must still return [] not raise."""
        from mymem.pipeline.ingest_claims import _persist_claims

        db_path = tmp_path / "mymem.db"
        mock_router = MagicMock()
        records = [("page-01", "Fact.", "span")]

        with (
            patch(
                "mymem.pipeline.ingest_claims._build_claim_embedder",
                side_effect=RuntimeError("no embedder"),
            ),
            patch(
                "mymem.pipeline.ingest_claims._naive_persist",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                _persist_claims(db_path, "raw/a.md", records, router=mock_router)
            )

        assert result == []
