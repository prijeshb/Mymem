"""
Tests for the OKF core modules — _spec, _map, _links (ADR-016). Pure functions.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from mymem.knowledge.okf._links import (
    flatten_wikilinks,
    markdown_links_to_wikilinks,
    wikilinks_to_markdown,
)
from mymem.knowledge.okf._map import from_okf_frontmatter, to_okf_frontmatter
from mymem.knowledge.okf._spec import concept_id, has_valid_type
from mymem.wiki.types import TagDomain, WikiPage


# --------------------------------------------------------------------------- spec
class TestSpec:
    def test_has_valid_type(self) -> None:
        assert has_valid_type({"type": "tech"}) is True
        assert has_valid_type({"type": "  "}) is False
        assert has_valid_type({"type": 5}) is False
        assert has_valid_type({}) is False

    def test_concept_id_strips_md_and_normalizes_sep(self) -> None:
        assert concept_id("tables/orders.md") == "tables/orders"
        assert concept_id("qa\\multi-head.md") == "qa/multi-head"
        assert concept_id("index") == "index"


# --------------------------------------------------------------------------- map
def _page(**kw: object) -> WikiPage:
    base = dict(
        title="Self Attention",
        body="# Self Attention\n\nBody.",
        path=Path("self-attention.md"),
        tags=("ml", "attention"),
        sources=("paper.md", "blog.md"),
        domain=TagDomain.TECH,
        created=date(2026, 1, 1),
        updated=date(2026, 6, 1),
        id="01HZID",
    )
    base.update(kw)
    return WikiPage(**base)  # type: ignore[arg-type]


class TestMap:
    def test_to_okf_required_and_recommended_fields(self) -> None:
        fm = to_okf_frontmatter(_page(), description="A summary.")
        assert fm["type"] == "tech"                 # required
        assert fm["title"] == "Self Attention"
        assert fm["description"] == "A summary."
        assert fm["resource"] == "paper.md"         # primary source
        assert fm["tags"] == ["ml", "attention"]
        assert fm["timestamp"].startswith("2026-06-01T")

    def test_to_okf_preserves_extension_keys(self) -> None:
        fm = to_okf_frontmatter(_page(archived=True))
        assert fm["id"] == "01HZID"
        assert fm["domain"] == "tech"
        assert fm["sources"] == ["paper.md", "blog.md"]
        assert fm["created"] == "2026-01-01"
        assert fm["archived"] is True

    def test_roundtrip_is_lossless_for_mymem_origin(self) -> None:
        fm = to_okf_frontmatter(_page(), description="s")
        kw = from_okf_frontmatter(fm)
        assert kw["id"] == "01HZID"
        assert kw["domain"] == TagDomain.TECH
        assert list(kw["tags"]) == ["ml", "attention"]
        assert kw["sources"] == ["paper.md", "blog.md"]
        assert kw["created"] == date(2026, 1, 1)
        assert kw["updated"] == date(2026, 6, 1)

    def test_unknown_type_maps_to_misc_and_kept_as_tag(self) -> None:
        kw = from_okf_frontmatter({"type": "BigQuery Table", "title": "Orders"})
        assert kw["domain"] == TagDomain.MISC
        assert "bigquery-table" in kw["tags"]  # normalize_tags slugifies

    def test_resource_becomes_source_when_no_sources(self) -> None:
        kw = from_okf_frontmatter({"type": "tech", "resource": "https://x/y"})
        assert kw["sources"] == ["https://x/y"]

    def test_timestamp_accepts_datetime_and_date_objects(self) -> None:
        from datetime import UTC, datetime

        kw = from_okf_frontmatter({"type": "tech", "timestamp": datetime(2026, 5, 1, tzinfo=UTC)})
        assert kw["updated"] == date(2026, 5, 1)
        kw = from_okf_frontmatter({"type": "tech", "timestamp": date(2026, 4, 1)})
        assert kw["updated"] == date(2026, 4, 1)

    def test_bad_timestamp_falls_back_to_today(self) -> None:
        kw = from_okf_frontmatter({"type": "tech", "timestamp": "not-a-date"})
        assert kw["updated"] == date.today()

    def test_bad_created_falls_back_to_updated(self) -> None:
        kw = from_okf_frontmatter(
            {"type": "tech", "timestamp": "2026-03-15T00:00:00+00:00", "created": "garbage"}
        )
        assert kw["created"] == date(2026, 3, 15)


# --------------------------------------------------------------------------- links
class TestLinks:
    def test_wikilink_resolves_to_markdown_link(self) -> None:
        body = "See [[Self Attention]] and [[Cross Attention]]."
        out, unresolved = wikilinks_to_markdown(
            body, resolve=lambda t: "self-attention" if t == "Self Attention" else None
        )
        assert "[Self Attention](/self-attention.md)" in out
        # broken link still emitted (OKF-tolerant), and reported
        assert "[Cross Attention](/cross-attention.md)" in out
        assert unresolved == ["Cross Attention"]

    def test_markdown_links_back_to_wikilinks(self) -> None:
        body = "See [Self Attention](/self-attention.md) and [docs](https://x.com)."
        out = markdown_links_to_wikilinks(body)
        assert "[[Self Attention]]" in out
        assert "[docs](https://x.com)" in out  # non-md link untouched

    def test_roundtrip_links(self) -> None:
        body = "Ref [[Vector Search]]."
        md, _ = wikilinks_to_markdown(body, resolve=lambda t: "vector-search")
        back = markdown_links_to_wikilinks(md)
        assert back == "Ref [[Vector Search]]."

    def test_flatten_wikilinks_to_plain_text(self) -> None:
        # description fields must not carry link syntax (ADR-017 F1)
        text = "Moving to [[Microservices]] loses [[ACID]] guarantees."
        assert flatten_wikilinks(text) == "Moving to Microservices loses ACID guarantees."
        assert flatten_wikilinks("no links here") == "no links here"
