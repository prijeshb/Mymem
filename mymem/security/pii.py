"""PII detection + redaction (ADR-018).

Regex-based detectors for the common high-confidence PII types — email, US SSN,
credit card (Luhn-validated), phone, IPv4. No external dependency or model. Name
detection (NER) is deliberately out of scope for v1: it needs a model and is far
more false-positive prone. Redaction replaces each match with a typed placeholder
(``[EMAIL]``) so the structure is preserved but the value is gone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
# 13–16 digit runs allowing space/dash groupings; Luhn-checked below to cut noise.
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# Order matters: redact the most specific/structured first so a phone's digits
# aren't partially eaten by the card matcher, etc.
_PLACEHOLDER = {
    "email": "[EMAIL]",
    "ssn": "[SSN]",
    "credit_card": "[CARD]",
    "phone": "[PHONE]",
    "ip": "[IP]",
}


@dataclass(frozen=True)
class PiiFinding:
    kind: str          # email | ssn | credit_card | phone | ip
    redacted: str      # safe preview (never the full value)


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 16:
        return False
    checksum = 0
    for i, d in enumerate(reversed(nums)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _preview(kind: str, match: str) -> str:
    head = re.sub(r"\s+", "", match)[:2]
    return f"{kind}:{head}***"


def detect_pii(text: str) -> list[PiiFinding]:
    """Return all PII findings in *text* (de-duplicated by kind+value)."""
    seen: set[tuple[str, str]] = set()
    findings: list[PiiFinding] = []

    def _add(kind: str, value: str) -> None:
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            findings.append(PiiFinding(kind=kind, redacted=_preview(kind, value)))

    for m in _EMAIL.finditer(text):
        _add("email", m.group(0))
    for m in _SSN.finditer(text):
        _add("ssn", m.group(0))
    for m in _CARD.finditer(text):
        if _luhn_ok(m.group(0)):
            _add("credit_card", m.group(0))
    for m in _PHONE.finditer(text):
        _add("phone", m.group(0))
    for m in _IPV4.finditer(text):
        _add("ip", m.group(0))
    return findings


def redact_pii(text: str) -> tuple[str, list[PiiFinding]]:
    """Replace every PII match with a typed placeholder. Returns (redacted, findings)."""
    def _card_sub(m: re.Match[str]) -> str:
        return _PLACEHOLDER["credit_card"] if _luhn_ok(m.group(0)) else m.group(0)

    findings = detect_pii(text)
    out = _EMAIL.sub(_PLACEHOLDER["email"], text)
    out = _SSN.sub(_PLACEHOLDER["ssn"], out)
    out = _CARD.sub(_card_sub, out)
    out = _PHONE.sub(_PLACEHOLDER["phone"], out)
    out = _IPV4.sub(_PLACEHOLDER["ip"], out)
    return out, findings
