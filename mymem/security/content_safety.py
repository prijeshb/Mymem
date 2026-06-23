"""Content-safety orchestrator (ADR-018).

Runs the PII, denylist, and moderation detectors over a piece of text and resolves a
single action from the per-category config (``security.pii`` / ``denylist`` / ``nsfw``).
Actions form an ordered ladder allow < flag < block; the strictest triggered category
wins. High-confidence moderation hits escalate a ``flag`` category to ``block`` (the
"flag, but block on high confidence" rule). PII defaults to ``redact`` — the returned
``text`` is the redacted copy, so callers store/serve the safe version.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from mymem.observability.logger import get_logger
from mymem.security.denylist import check_denylist
from mymem.security.moderation import ModerationResult, classify_content
from mymem.security.pii import PiiFinding, detect_pii, redact_pii

if TYPE_CHECKING:
    from mymem.config import SecurityConfig

log = get_logger(__name__)


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"


_ORDER = {Action.ALLOW: 0, Action.FLAG: 1, Action.BLOCK: 2}


def _escalate(a: Action, b: Action) -> Action:
    return a if _ORDER[a] >= _ORDER[b] else b


@dataclass(frozen=True)
class SafetyDecision:
    action: Action
    text: str                                  # PII-redacted copy (safe to store/serve)
    reasons: tuple[str, ...] = ()
    pii: tuple[PiiFinding, ...] = ()
    denylisted: tuple[str, ...] = ()
    moderation: ModerationResult | None = None

    @property
    def blocked(self) -> bool:
        return self.action is Action.BLOCK


def inspect_content(text: str, security: SecurityConfig) -> SafetyDecision:
    """Inspect *text* and resolve a SafetyDecision from the security config."""
    action = Action.ALLOW
    reasons: list[str] = []
    out = text

    # 1. PII — redact (default) or detect-and-flag/block.
    pii: tuple[PiiFinding, ...] = ()
    if security.pii != "off":
        if security.pii == "redact":
            out, found = redact_pii(out)
            pii = tuple(found)
            if found:
                reasons.append(f"pii_redacted={len(found)}")
        else:
            pii = tuple(detect_pii(out))
            if pii:
                action = _escalate(action, Action.BLOCK if security.pii == "block" else Action.FLAG)
                reasons.append(f"pii={len(pii)}")

    # 2. Denylist — banned terms/topics.
    denied: tuple[str, ...] = ()
    if security.denylist != "off":
        denied = tuple(check_denylist(out, list(security.denylist_terms)))
        if denied:
            block_it = security.denylist == "block"
            action = _escalate(action, Action.BLOCK if block_it else Action.FLAG)
            reasons.append("denylist=" + ",".join(denied))

    # 3. Adult/toxicity — flag, escalating to block on high confidence (or if configured).
    mod: ModerationResult | None = None
    if security.nsfw != "off":
        mod = classify_content(out)
        if mod.flagged:
            block_it = security.nsfw == "block" or mod.high_confidence
            action = _escalate(action, Action.BLOCK if block_it else Action.FLAG)
            reasons.append("nsfw=" + ",".join(mod.categories))

    if action is not Action.ALLOW:
        log.warning("Content safety decision", action=action.value, reasons=reasons)

    return SafetyDecision(
        action=action, text=out, reasons=tuple(reasons),
        pii=pii, denylisted=denied, moderation=mod,
    )
