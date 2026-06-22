"""
Tests for mymem/knowledge/render.py — render a wiki "Knowledge Claims" section from a
page's claims, and sync it into a page body (ADR-011 / ADR-015 Phase 3 D13).

Pure string transforms — no LLM, no I/O. Surfaces the compounding ledger (active claims +
the SUPERSEDE audit trail) directly in the durable markdown.
"""
from __future__ import annotations

from mymem.knowledge.claims import Claim
from mymem.knowledge.render import (
    CLAIMS_END,
    CLAIMS_START,
    render_claims_section,
    render_page_body,
    sync_claims_section,
)


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
# render_claims_section
# ---------------------------------------------------------------------------

class TestRenderSection:
    def test_empty_claims_renders_nothing(self) -> None:
        assert render_claims_section([]) == ""

    def test_active_claims_listed_with_confidence(self) -> None:
        out = render_claims_section([_claim(1, "Self-attention is global.", confidence=0.8)])
        assert out.startswith(CLAIMS_START)
        assert out.endswith(CLAIMS_END)
        assert "## Knowledge Claims" in out
        assert "- Self-attention is global. (conf 0.8)" in out
        assert "Superseded" not in out  # none retired

    def test_superseded_subsection_struck_through_with_date(self) -> None:
        claims = [
            _claim(2, "Introduced in 2017.", confidence=1.0),
            _claim(1, "Introduced in 2014.", valid_to="2026-06-15", superseded_by=2),
        ]
        out = render_claims_section(claims)
        assert "- Introduced in 2017. (conf 1.0)" in out
        assert "### Superseded" in out
        assert "- ~~Introduced in 2014.~~ (retired 2026-06-15)" in out

    def test_all_superseded_still_renders_section(self) -> None:
        out = render_claims_section([_claim(1, "Old fact.", valid_to="2026-06-15")])
        assert "### Superseded" in out
        assert "~~Old fact.~~" in out

    def test_multiline_claim_text_flattened(self) -> None:
        out = render_claims_section([_claim(1, "line one\n  line two")])
        assert "- line one line two (conf 1.0)" in out


# ---------------------------------------------------------------------------
# sync_claims_section
# ---------------------------------------------------------------------------

BODY = "# Self-Attention\n\nSome LLM-compiled prose about attention."


class TestSyncSection:
    def test_appends_section_when_absent(self) -> None:
        out = sync_claims_section(BODY, [_claim(1, "A claim.")])
        assert out.startswith("# Self-Attention")
        assert "Some LLM-compiled prose" in out
        assert CLAIMS_START in out and CLAIMS_END in out

    def test_is_idempotent(self) -> None:
        once = sync_claims_section(BODY, [_claim(1, "A claim.")])
        twice = sync_claims_section(once, [_claim(1, "A claim.")])
        assert once == twice  # syncing the same claims twice is a no-op

    def test_replaces_existing_section(self) -> None:
        first = sync_claims_section(BODY, [_claim(1, "Old claim.")])
        updated = sync_claims_section(first, [_claim(2, "New claim.")])
        assert "New claim." in updated
        assert "Old claim." not in updated
        assert updated.count(CLAIMS_START) == 1  # exactly one section, not stacked

    def test_removes_section_when_no_claims(self) -> None:
        with_section = sync_claims_section(BODY, [_claim(1, "A claim.")])
        cleared = sync_claims_section(with_section, [])
        assert CLAIMS_START not in cleared
        assert cleared.strip() == BODY.strip()  # prose preserved, section gone

    def test_preserves_prose_above_section(self) -> None:
        out = sync_claims_section(BODY, [_claim(1, "A claim.")])
        prose, _, _ = out.partition(CLAIMS_START)
        assert prose.strip() == BODY.strip()


# ---------------------------------------------------------------------------
# render_page_body — render a complete body FROM claims (ADR-015 D20 / D11 end-state)
# ---------------------------------------------------------------------------

class TestRenderPageBody:
    def test_no_active_claims_returns_empty(self) -> None:
        # Safety: never replace a page body when there is nothing active to render.
        assert render_page_body("Attention", []) == ""
        superseded_only = [_claim(1, "Old.", valid_to="2026-06-15")]
        assert render_page_body("Attention", superseded_only) == ""

    def test_active_claims_become_the_body(self) -> None:
        out = render_page_body(
            "Self-Attention",
            [_claim(1, "Attention is global.", confidence=0.8)],
        )
        assert out.startswith("# Self-Attention")
        assert "- Attention is global. (conf 0.8)" in out
        # No LLM prose, no marker comments — the claims *are* the body.
        assert CLAIMS_START not in out and CLAIMS_END not in out
        assert "Superseded" not in out

    def test_superseded_trail_rendered_when_active_present(self) -> None:
        claims = [
            _claim(2, "Introduced in 2017.", confidence=1.0),
            _claim(1, "Introduced in 2014.", valid_to="2026-06-15", superseded_by=2),
        ]
        out = render_page_body("Transformers", claims)
        assert "- Introduced in 2017. (conf 1.0)" in out
        assert "### Superseded" in out
        assert "- ~~Introduced in 2014.~~ (retired 2026-06-15)" in out

    def test_see_also_wikilinks_preserved(self) -> None:
        out = render_page_body(
            "Self-Attention",
            [_claim(1, "A claim.")],
            see_also=["Transformers", "Embeddings", "Transformers"],  # dup ignored
        )
        assert "## See Also" in out
        assert "- [[Transformers]]" in out
        assert "- [[Embeddings]]" in out
        assert out.count("[[Transformers]]") == 1  # deduped, order preserved
        assert out.index("[[Transformers]]") < out.index("[[Embeddings]]")

    def test_empty_see_also_omits_section(self) -> None:
        out = render_page_body("X", [_claim(1, "A claim.")], see_also=[])
        assert "## See Also" not in out

    def test_multiline_claim_text_flattened(self) -> None:
        out = render_page_body("X", [_claim(1, "line one\n  line two")])
        assert "- line one line two (conf 1.0)" in out

    def test_is_idempotent_through_wikilink_reextraction(self) -> None:
        # Rendering, then re-rendering using the previous body's wikilinks, is stable.
        from mymem.wiki.types import WikiPage

        first = render_page_body(
            "Self-Attention", [_claim(1, "A claim.")], see_also=["Transformers"]
        )
        links = WikiPage(title="Self-Attention", body=first, path=tmp_pathless()).wikilinks()
        second = render_page_body("Self-Attention", [_claim(1, "A claim.")], see_also=links)
        assert first == second


def tmp_pathless():  # tiny helper so WikiPage construction needs no real path
    from pathlib import Path

    return Path("unused.md")


# ---------------------------------------------------------------------------
# Integration: ingest's _sync_claims_sections writes the section into the page file
# ---------------------------------------------------------------------------

class TestSyncSectionsWiring:
    def test_active_and_superseded_claims_written_to_page(self, tmp_path) -> None:
        from mymem.knowledge.claims import add_claim, init_db, supersede_claim
        from mymem.pipeline.ingest import _sync_claims_sections
        from mymem.wiki.page import read_page, write_page
        from mymem.wiki.types import WikiPage

        db_path = tmp_path / "data" / "mymem.db"
        claims_db = db_path.parent / "claims.db"
        init_db(claims_db)

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page_path = wiki_dir / "attention.md"
        write_page(WikiPage(title="Attention", body="# Attention\n\nProse.", path=page_path))
        page_id = read_page(page_path).id  # the id minted on write

        new = add_claim(
            claims_db, page_id=page_id, text="Introduced in 2017.", source_id="raw/a.md"
        )
        old = add_claim(
            claims_db, page_id=page_id, text="Introduced in 2014.", source_id="raw/old.md"
        )
        supersede_claim(claims_db, old.id, by=new.id, valid_to="2026-06-15")

        _sync_claims_sections(db_path, [(page_path, page_id)])

        body = read_page(page_path).body
        assert "## Knowledge Claims" in body
        assert "- Introduced in 2017. (conf 1.0)" in body          # active
        assert "- ~~Introduced in 2014.~~ (retired 2026-06-15)" in body  # SUPERSEDE trail visible
        assert body.lstrip().startswith("# Attention")              # prose preserved, above section
        assert body.index("# Attention") < body.index(CLAIMS_START)

    def test_noop_when_claims_db_absent(self, tmp_path) -> None:
        from mymem.pipeline.ingest import _sync_claims_sections

        # No claims.db next to db_path → helper returns quietly, touches nothing.
        _sync_claims_sections(tmp_path / "data" / "mymem.db", [])


class TestBodyFromClaimsWiring:
    """ADR-015 D20 — opt-in: render the body FROM claims instead of appending a section."""

    def _setup(self, tmp_path, body):
        from mymem.knowledge.claims import init_db
        from mymem.wiki.page import read_page, write_page
        from mymem.wiki.types import WikiPage

        db_path = tmp_path / "data" / "mymem.db"
        claims_db = db_path.parent / "claims.db"
        init_db(claims_db)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        page_path = wiki_dir / "attention.md"
        write_page(WikiPage(title="Attention", body=body, path=page_path))
        return db_path, claims_db, page_path, read_page(page_path).id

    def test_body_replaced_and_wikilinks_preserved(self, tmp_path) -> None:
        from mymem.knowledge.claims import add_claim
        from mymem.pipeline.ingest import _sync_claims_sections
        from mymem.wiki.page import read_page

        body = "# Attention\n\nLLM prose.\n\n## See Also\n\n- [[Transformers]]\n- [[Embeddings]]"
        db_path, claims_db, page_path, page_id = self._setup(tmp_path, body)
        add_claim(claims_db, page_id=page_id, text="Attention is global.", source_id="raw/a.md")

        _sync_claims_sections(db_path, [(page_path, page_id)], body_from_claims=True)

        new_body = read_page(page_path).body
        assert "- Attention is global. (conf 1.0)" in new_body
        assert "LLM prose." not in new_body                 # prose replaced by claims
        assert CLAIMS_START not in new_body                  # not the section-append mode
        assert "[[Transformers]]" in new_body                # graph survives
        assert "[[Embeddings]]" in new_body

    def test_prose_kept_when_no_active_claims(self, tmp_path) -> None:
        from mymem.pipeline.ingest import _sync_claims_sections
        from mymem.wiki.page import read_page

        body = "# Attention\n\nLLM prose only — no claims yet."
        db_path, claims_db, page_path, page_id = self._setup(tmp_path, body)

        _sync_claims_sections(db_path, [(page_path, page_id)], body_from_claims=True)

        # Safety: an empty ledger must never wipe the page to nothing.
        assert read_page(page_path).body.strip() == body.strip()

    def test_default_mode_still_appends_section(self, tmp_path) -> None:
        from mymem.knowledge.claims import add_claim
        from mymem.pipeline.ingest import _sync_claims_sections
        from mymem.wiki.page import read_page

        body = "# Attention\n\nLLM prose."
        db_path, claims_db, page_path, page_id = self._setup(tmp_path, body)
        add_claim(claims_db, page_id=page_id, text="A claim.", source_id="raw/a.md")

        _sync_claims_sections(db_path, [(page_path, page_id)])  # default: body_from_claims=False

        new_body = read_page(page_path).body
        assert "LLM prose." in new_body          # prose kept
        assert CLAIMS_START in new_body           # section appended (D13 behavior)
