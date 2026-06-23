"""Demo: content-safety engine (ADR-018) over representative inputs.

Run:  venv/Scripts/python.exe scripts/demo_content_safety.py
"""
from __future__ import annotations

from mymem.config import SecurityConfig
from mymem.security.content_safety import inspect_content

# Same defaults as config.yaml: pii=redact, denylist=block, nsfw=flag (block on high-conf).
cfg = SecurityConfig(
    pii="redact",
    denylist="block",
    nsfw="flag",
    denylist_terms=["project zephyr", "internal-only"],
)

CASES = [
    ("Clean technical note",
     "Multi-head attention lets transformer tokens attend to one another in parallel."),
    ("PII (email/phone/SSN/card/IP)",
     "Reach Jane at jane.doe@acme.com or 555-123-4567. SSN 123-45-6789, "
     "card 4111 1111 1111 1111, server 10.0.42.7."),
    ("Banned term (denylist)",
     "Notes on Project Zephyr roadmap and the internal-only pricing model."),
    ("Adult content (high confidence)",
     "This page links to xxx porn and other explicit material."),
    ("Mild flag (low confidence)",
     "An art-history essay discussing a nude figure study."),
    ("Combined: PII + denylist",
     "Email ceo@acme.com about Project Zephyr before launch."),
]


def main() -> None:
    print("=" * 78)
    print("CONTENT-SAFETY DEMO  (pii=redact  denylist=block  nsfw=flag)")
    print("=" * 78)
    for title, text in CASES:
        d = inspect_content(text, cfg)
        print(f"\n▶ {title}")
        print(f"  in : {text}")
        print(f"  ACTION : {d.action.value.upper()}")
        if d.reasons:
            print(f"  reasons: {', '.join(d.reasons)}")
        if d.text != text:
            print(f"  out: {d.text}")
        if d.pii:
            print(f"  pii : {[f'{p.kind}' for p in d.pii]}")
        if d.denylisted:
            print(f"  deny: {list(d.denylisted)}")
        if d.moderation and d.moderation.flagged:
            m = d.moderation
            print(f"  nsfw: score={m.score} cats={list(m.categories)} high_conf={m.high_confidence}")
    print("\n" + "=" * 78)
    print("DEMO COMPLETE")


if __name__ == "__main__":
    main()
