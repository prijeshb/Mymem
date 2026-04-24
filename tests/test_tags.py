"""Tests for mymem.wiki.tags — domain taxonomy and tag helpers."""

from __future__ import annotations

import pytest

from mymem.wiki.tags import (
    dataview_table, domain_from_str, infer_domain,
    normalize_tag, normalize_tags,
)
from mymem.wiki.types import TagDomain


class TestNormalizeTags:
    def test_lowercases(self):
        assert normalize_tag("Python") == "python"

    def test_strips_whitespace(self):
        assert normalize_tag("  ml  ") == "ml"

    def test_spaces_to_hyphens(self):
        assert normalize_tag("machine learning") == "machine-learning"

    def test_normalize_tags_deduplicates(self):
        result = normalize_tags(["ml", "ML", "ml"])
        assert result == ["ml"]

    def test_normalize_tags_drops_empty(self):
        result = normalize_tags(["", "  ", "python"])
        assert result == ["python"]

    def test_normalize_tags_preserves_order(self):
        result = normalize_tags(["beta", "alpha", "gamma"])
        assert result == ["beta", "alpha", "gamma"]


class TestInferDomain:
    def test_tech_from_tags(self):
        assert infer_domain(["python", "ml"]) == TagDomain.TECH

    def test_spiritual_from_title(self):
        assert infer_domain([], title="Stoicism and Mindfulness") == TagDomain.SPIRITUAL

    def test_finance_from_body(self):
        assert infer_domain([], body="investing in markets and budgeting") == TagDomain.FINANCE

    def test_health_detected(self):
        assert infer_domain(["fitness", "nutrition"]) == TagDomain.HEALTH

    def test_fallback_to_misc(self):
        assert infer_domain([]) == TagDomain.MISC

    def test_reminder_detected(self):
        assert infer_domain(["todo", "deadline"]) == TagDomain.REMINDER


class TestDomainFromStr:
    def test_valid_domain(self):
        assert domain_from_str("tech") == TagDomain.TECH

    def test_case_insensitive(self):
        assert domain_from_str("SPIRITUAL") == TagDomain.SPIRITUAL

    def test_strips_whitespace(self):
        assert domain_from_str("  finance  ") == TagDomain.FINANCE

    def test_unknown_falls_back_to_misc(self):
        assert domain_from_str("unknown-domain") == TagDomain.MISC

    def test_empty_falls_back_to_misc(self):
        assert domain_from_str("") == TagDomain.MISC


class TestDataviewTable:
    def test_with_domain(self):
        snippet = dataview_table(TagDomain.TECH, limit=10)
        assert "```dataview" in snippet
        assert "FROM #tech" in snippet
        assert "LIMIT 10" in snippet

    def test_without_domain(self):
        snippet = dataview_table(None)
        assert 'FROM ""' in snippet

    def test_sort_field(self):
        snippet = dataview_table(sort_by="created")
        assert "SORT created DESC" in snippet

    def test_closes_code_fence(self):
        snippet = dataview_table()
        assert snippet.strip().endswith("```")


class TestTagDomain:
    def test_all_domains_present(self):
        domains = TagDomain.values()
        assert "spiritual" in domains
        assert "tech" in domains
        assert "finance" in domains
        assert "health" in domains
        assert "reminder" in domains
        assert "research" in domains
        assert "personal" in domains
        assert "creative" in domains
        assert "business" in domains
        assert "misc" in domains

    def test_domain_is_string_enum(self):
        assert TagDomain.TECH.value == "tech"
