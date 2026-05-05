"""Tests for mymem.observability.ingest_analytics."""

from __future__ import annotations

from pathlib import Path

import pytest

from mymem.observability.ingest_analytics import (
    EnrichmentStats,
    record_ingest,
    recent_ingests,
    youtube_comparison,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record(db: Path, *, metadata_enriched: bool, concepts: int = 3,
            avg_chars: float = 1000.0, avg_wikilinks: float = 4.0) -> None:
    record_ingest(
        db,
        source_type="youtube",
        metadata_enriched=metadata_enriched,
        source_chars=5000,
        concepts_extracted=concepts,
        pages_written=concepts,
        pages_updated=0,
        avg_page_chars=avg_chars,
        avg_wikilinks=avg_wikilinks,
    )


# ---------------------------------------------------------------------------
# record_ingest
# ---------------------------------------------------------------------------

class TestRecordIngest:
    def test_creates_table_and_row(self, tmp_path: Path):
        db = tmp_path / "test.db"
        record_ingest(
            db,
            source_type="youtube",
            metadata_enriched=True,
            source_chars=12000,
            concepts_extracted=3,
            pages_written=2,
            pages_updated=1,
            avg_page_chars=850.0,
            avg_wikilinks=3.5,
        )
        import sqlite3
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT * FROM ingest_analytics").fetchone()
        assert row is not None
        assert row[2] == "youtube"       # source_type
        assert row[3] == 1               # metadata_enriched
        assert row[5] == 3               # concepts_extracted

    def test_plain_ingest_stored_as_zero(self, tmp_path: Path):
        db = tmp_path / "test.db"
        record_ingest(
            db,
            source_type="youtube",
            metadata_enriched=False,
            source_chars=4000,
            concepts_extracted=2,
            pages_written=2,
            pages_updated=0,
            avg_page_chars=600.0,
            avg_wikilinks=1.5,
        )
        import sqlite3
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT metadata_enriched FROM ingest_analytics").fetchone()
        assert row[0] == 0

    def test_multiple_rows_accumulate(self, tmp_path: Path):
        db = tmp_path / "test.db"
        for _ in range(5):
            record_ingest(
                db, source_type="youtube", metadata_enriched=True,
                source_chars=1000, concepts_extracted=3,
                pages_written=1, pages_updated=0,
                avg_page_chars=500.0, avg_wikilinks=2.0,
            )
        import sqlite3
        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM ingest_analytics").fetchone()[0]
        assert count == 5

    def test_silent_on_bad_db_path(self):
        bad_path = Path("/nonexistent/deeply/nested/db.db")
        # Should not raise
        record_ingest(
            bad_path, source_type="youtube", metadata_enriched=True,
            source_chars=0, concepts_extracted=0,
            pages_written=0, pages_updated=0,
            avg_page_chars=0.0, avg_wikilinks=0.0,
        )


# ---------------------------------------------------------------------------
# youtube_comparison
# ---------------------------------------------------------------------------

class TestYoutubeComparison:
    def test_empty_db_returns_zero_stats(self, tmp_path: Path):
        db = tmp_path / "test.db"
        stats = youtube_comparison(db)
        assert isinstance(stats, EnrichmentStats)
        assert stats.enriched_count == 0
        assert stats.plain_count == 0

    def test_separates_enriched_and_plain(self, tmp_path: Path):
        db = tmp_path / "test.db"
        _record(db, metadata_enriched=True,  concepts=4, avg_chars=1200.0, avg_wikilinks=5.0)
        _record(db, metadata_enriched=True,  concepts=3, avg_chars=1000.0, avg_wikilinks=4.0)
        _record(db, metadata_enriched=False, concepts=2, avg_chars=600.0,  avg_wikilinks=2.0)

        stats = youtube_comparison(db)
        assert stats.enriched_count == 2
        assert stats.plain_count == 1

    def test_averages_computed_correctly(self, tmp_path: Path):
        db = tmp_path / "test.db"
        _record(db, metadata_enriched=True, concepts=4, avg_chars=1200.0, avg_wikilinks=6.0)
        _record(db, metadata_enriched=True, concepts=2, avg_chars=800.0,  avg_wikilinks=2.0)

        stats = youtube_comparison(db)
        assert stats.enriched_avg_concepts == pytest.approx(3.0)
        assert stats.enriched_avg_page_chars == pytest.approx(1000.0)
        assert stats.enriched_avg_wikilinks == pytest.approx(4.0)

    def test_enriched_better_than_plain(self, tmp_path: Path):
        db = tmp_path / "test.db"
        # Simulate enriched producing richer pages
        _record(db, metadata_enriched=True,  concepts=4, avg_chars=1400.0, avg_wikilinks=5.0)
        _record(db, metadata_enriched=False, concepts=2, avg_chars=700.0,  avg_wikilinks=2.0)

        stats = youtube_comparison(db)
        assert stats.enriched_avg_concepts > stats.plain_avg_concepts
        assert stats.enriched_avg_page_chars > stats.plain_avg_page_chars
        assert stats.enriched_avg_wikilinks > stats.plain_avg_wikilinks

    def test_ignores_non_youtube_rows(self, tmp_path: Path):
        db = tmp_path / "test.db"
        record_ingest(
            db, source_type="article", metadata_enriched=False,
            source_chars=2000, concepts_extracted=3,
            pages_written=2, pages_updated=0,
            avg_page_chars=900.0, avg_wikilinks=3.0,
        )
        stats = youtube_comparison(db)
        assert stats.enriched_count == 0
        assert stats.plain_count == 0


# ---------------------------------------------------------------------------
# recent_ingests
# ---------------------------------------------------------------------------

class TestRecentIngests:
    def test_returns_most_recent_first(self, tmp_path: Path):
        db = tmp_path / "test.db"
        for i in range(5):
            record_ingest(
                db, source_type="youtube", metadata_enriched=(i % 2 == 0),
                source_chars=1000 * (i + 1), concepts_extracted=i + 1,
                pages_written=1, pages_updated=0,
                avg_page_chars=500.0, avg_wikilinks=2.0,
            )
        rows = recent_ingests(db, limit=3)
        assert len(rows) == 3
        # Most recent has highest concepts_extracted (i=4 → 5)
        assert rows[0]["concepts_extracted"] == 5

    def test_limit_respected(self, tmp_path: Path):
        db = tmp_path / "test.db"
        for _ in range(10):
            _record(db, metadata_enriched=True)
        assert len(recent_ingests(db, limit=4)) == 4

    def test_empty_db_returns_empty_list(self, tmp_path: Path):
        db = tmp_path / "test.db"
        assert recent_ingests(db) == []

    def test_row_has_expected_keys(self, tmp_path: Path):
        db = tmp_path / "test.db"
        _record(db, metadata_enriched=True, concepts=3)
        row = recent_ingests(db, limit=1)[0]
        assert "source_type" in row
        assert "metadata_enriched" in row
        assert "concepts_extracted" in row
        assert "avg_page_chars" in row
        assert "avg_wikilinks" in row
        assert "created_at" in row


# ---------------------------------------------------------------------------
# API endpoint (via FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestAnalyticsEndpoint:
    def test_returns_comparison_shape(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from mymem.web.app import create_app

        app = create_app()
        app.state.db_path = tmp_path / "test.db"

        # Seed some data
        _record(app.state.db_path, metadata_enriched=True,  concepts=4, avg_chars=1200.0, avg_wikilinks=5.0)
        _record(app.state.db_path, metadata_enriched=False, concepts=2, avg_chars=700.0,  avg_wikilinks=2.0)

        client = TestClient(app)
        resp = client.get("/api/analytics/youtube")
        assert resp.status_code == 200

        data = resp.json()
        assert "enriched" in data
        assert "plain" in data
        assert "delta" in data
        assert "recent" in data

        assert data["enriched"]["count"] == 1
        assert data["plain"]["count"] == 1
        assert data["enriched"]["avg_concepts"] == 4.0
        assert data["plain"]["avg_concepts"] == 2.0

    def test_delta_computes_percentage(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from mymem.web.app import create_app

        app = create_app()
        app.state.db_path = tmp_path / "test.db"

        _record(app.state.db_path, metadata_enriched=True,  concepts=6, avg_chars=1200.0, avg_wikilinks=6.0)
        _record(app.state.db_path, metadata_enriched=False, concepts=3, avg_chars=600.0,  avg_wikilinks=3.0)

        client = TestClient(app)
        data = client.get("/api/analytics/youtube").json()

        # enriched is 2× plain → +100%
        assert data["delta"]["concepts_pct"] == pytest.approx(100.0)
        assert data["delta"]["page_chars_pct"] == pytest.approx(100.0)
        assert data["delta"]["wikilinks_pct"] == pytest.approx(100.0)

    def test_empty_db_returns_zeros(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from mymem.web.app import create_app

        app = create_app()
        app.state.db_path = tmp_path / "test.db"

        client = TestClient(app)
        data = client.get("/api/analytics/youtube").json()
        assert data["enriched"]["count"] == 0
        assert data["plain"]["count"] == 0
        assert data["delta"]["concepts_pct"] is None


# ---------------------------------------------------------------------------
# Error branches — DB failure paths
# ---------------------------------------------------------------------------

class TestErrorBranches:
    def test_record_ingest_swallows_db_error(self, tmp_path: Path):
        from unittest.mock import patch
        import sqlite3

        db = tmp_path / "test.db"
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk full")):
            record_ingest(db, source_type="youtube", metadata_enriched=True,
                          source_chars=100, concepts_extracted=1, pages_written=1,
                          pages_updated=0, avg_page_chars=500.0, avg_wikilinks=2.0)
        # must not raise

    def test_youtube_comparison_swallows_db_error(self, tmp_path: Path):
        from unittest.mock import patch
        import sqlite3

        db = tmp_path / "test.db"
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("corrupt")):
            stats = youtube_comparison(db)
        assert stats.enriched_count == 0
        assert stats.plain_count == 0

    def test_recent_ingests_swallows_db_error(self, tmp_path: Path):
        from unittest.mock import patch
        import sqlite3

        db = tmp_path / "test.db"
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
            result = recent_ingests(db, limit=5)
        assert result == []
