"""Tests for the content-safety layer (ADR-018): PII, denylist, moderation, orchestrator."""
from __future__ import annotations

from mymem.config import SecurityConfig
from mymem.security.content_safety import Action, inspect_content
from mymem.security.denylist import check_denylist
from mymem.security.moderation import classify_content
from mymem.security.pii import detect_pii, redact_pii


# --------------------------------------------------------------------------- PII
class TestPii:
    def test_redacts_each_type(self) -> None:
        text = (
            "Email me at jane.doe@example.com or call 555-123-4567. "
            "SSN 123-45-6789, card 4111 1111 1111 1111, host 192.168.1.10."
        )
        out, findings = redact_pii(text)
        kinds = {f.kind for f in findings}
        assert kinds == {"email", "phone", "ssn", "credit_card", "ip"}
        for placeholder in ("[EMAIL]", "[PHONE]", "[SSN]", "[CARD]", "[IP]"):
            assert placeholder in out
        assert "jane.doe@example.com" not in out and "123-45-6789" not in out

    def test_invalid_card_not_redacted(self) -> None:
        # 16 digits but fails the Luhn check -> not a card.
        out, findings = redact_pii("number 1234 5678 9012 3456 here")
        assert all(f.kind != "credit_card" for f in findings)
        assert "[CARD]" not in out

    def test_clean_text_no_findings(self) -> None:
        assert detect_pii("the transformer architecture uses attention") == []

    def test_findings_never_expose_full_value(self) -> None:
        findings = detect_pii("reach me: secret@corp.com")
        assert findings and "secret@corp.com" not in findings[0].redacted


# ----------------------------------------------------------------------- denylist
class TestDenylist:
    def test_matches_word_and_phrase(self) -> None:
        terms = ["forbidden", "acme secret project"]
        assert check_denylist("This Forbidden topic", terms) == ["forbidden"]
        assert check_denylist("re: ACME Secret Project", terms) == ["acme secret project"]

    def test_word_boundary(self) -> None:
        assert check_denylist("forbiddenials are fine", ["forbid"]) == []

    def test_empty_terms_or_no_match(self) -> None:
        assert check_denylist("anything", []) == []
        assert check_denylist("clean text", ["banned"]) == []


# --------------------------------------------------------------------- moderation
class TestModeration:
    def test_flags_adult_high_confidence(self) -> None:
        res = classify_content("this is xxx porn content")
        assert res.flagged and res.high_confidence and "adult" in res.categories

    def test_low_confidence_single_mild_term(self) -> None:
        res = classify_content("a nude figure study in art class")
        assert res.flagged and not res.high_confidence

    def test_clean_text(self) -> None:
        res = classify_content("distributed systems and consensus protocols")
        assert not res.flagged and res.score == 0.0


# --------------------------------------------------------------- orchestrator
def _cfg(**kw: object) -> SecurityConfig:
    base: dict[str, object] = {
        "pii": "redact", "denylist": "block", "nsfw": "flag", "denylist_terms": ["forbidden"],
    }
    base.update(kw)
    return SecurityConfig(**base)  # type: ignore[arg-type]


class TestOrchestrator:
    def test_pii_redacted_but_allowed_by_default(self) -> None:
        d = inspect_content("contact a@b.com", _cfg())
        assert d.action is Action.ALLOW
        assert "[EMAIL]" in d.text and d.pii

    def test_denylist_blocks(self) -> None:
        d = inspect_content("this is a forbidden subject", _cfg())
        assert d.blocked and "forbidden" in d.denylisted

    def test_high_confidence_nsfw_escalates_to_block(self) -> None:
        # nsfw='flag' but a high-confidence hit escalates to block.
        d = inspect_content("xxx porn", _cfg())
        assert d.action is Action.BLOCK

    def test_low_confidence_nsfw_flags(self) -> None:
        d = inspect_content("a nude sketch", _cfg())
        assert d.action is Action.FLAG and not d.blocked

    def test_clean_text_allowed(self) -> None:
        d = inspect_content("attention mechanisms in transformers", _cfg())
        assert d.action is Action.ALLOW and d.text == "attention mechanisms in transformers"

    def test_off_disables_category(self) -> None:
        d = inspect_content("forbidden xxx a@b.com", _cfg(pii="off", denylist="off", nsfw="off"))
        assert d.action is Action.ALLOW and d.text == "forbidden xxx a@b.com"
