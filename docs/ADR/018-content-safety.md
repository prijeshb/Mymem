# ADR 018: Content-safety layer (PII, denylist, moderation)

## Status: Accepted (engine implemented in V1-0015; enforcement wiring in progress)

**Date:** 2026-06-23 · **Priority:** P1
**Relates to:** ADR-017 (MCP exposure raises the threat model), the existing
`security/` layer (prompt-injection `sanitize.py`, secret `scanner.py`, input `validate.py`).

## Context

MyMem already had input validation, SQL/command/path-injection defenses, prompt-injection
sanitization, and secret scanning — all wired into ingest/query/API. Missing, relative to the
"industry-standard" content-safety baseline, were: **PII detection/redaction**, a **banned-term/topic
denylist**, **adult/toxicity moderation**, and **moderation of LLM output** (the existing guards run on
input only). For a local single-user tool these were lower priority — but the **MCP/network direction**
(ADR-017: remote agents may contribute and be served content) makes output moderation + PII redaction a
prerequisite before network exposure, and sharing/exporting (OKF) makes PII redaction matter on publish.

## Decision

Add a dependency-free **content-safety engine** under `mymem/security/`, config-driven and
output-aware:

- `pii.py` — regex detection + redaction for email / US SSN / credit-card (Luhn-validated) / phone /
  IPv4 → typed placeholders (`[EMAIL]`…). Name NER is out of scope for v1 (needs a model, high FP rate).
- `denylist.py` — literal, word-boundary, case-insensitive matching of `security.denylist_terms`.
- `moderation.py` — lexicon-based adult/toxicity scoring with a high-confidence signal (no model).
- `content_safety.py` — orchestrator: per-category config (`security.{pii,denylist,nsfw}` ∈
  `off|flag|redact|block`) resolved to one action on the ladder **allow < flag < block**; the strictest
  triggered category wins; **high-confidence moderation escalates `flag` → `block`**; PII default is
  `redact` (returned text is the safe copy). Config field `moderate_output` also inspects LLM-generated
  text before it is stored/served.

Enforcement points (wiring): ingest **input** (block/redact source), ingest **output**
(`moderate_output` on generated pages), MCP **serve** (redact PII in `get_page`/`search`).

## Rationale

- **No new dependency / no external API** fits MyMem's local-first ethos; regex + lexicon give high
  precision on the highest-value PII and explicit content. A local ML classifier (e.g. detoxify) is a
  clean future upgrade behind the same `classify_content` interface.
- **Per-category + high-confidence escalation** matches the requested policy ("configurable per
  category; block on high confidence") and keeps a personal tool from over-censoring the user's own
  benign content while still hard-blocking explicit/banned material.
- **Output moderation** closes the gap that the prior guards were input-only — important once content is
  served to other agents (ADR-017).

## Alternatives Considered

1. **ML toxicity/NSFW classifier (detoxify/transformers)** — rejected for v1: heavy dependency (torch),
   slower, overkill for a local tool. Kept as the upgrade path behind the same interface.
2. **Cloud moderation API (OpenAI/Perspective)** — rejected: violates local-first, adds a network
   dependency + per-call cost + sends content off-box.
3. **Block-everything-on-any-detection** — rejected: too disruptive for a personal wiki; redact-PII /
   flag-mild / block-high-confidence is the usable default.
4. **Do nothing (rely on existing guards)** — rejected: leaves PII/adult/banned/output uncovered, which
   blocks the network/sharing direction.

## Consequences

- **Positive:** industry-standard coverage (PII/denylist/NSFW/output) with zero new deps; one orchestrator
  callable from ingest + MCP; per-category config; auditable WARNING logs on every flag/block.
- **Negative / tradeoffs:** lexicon/regex are heuristic — false negatives on obfuscated content and some
  false positives; name PII not covered in v1; the NSFW lexicon needs curation.
- **Risks:** over-redaction of legitimate content (mitigated: PII default redact-not-block; Luhn on
  cards; word boundaries on denylist); under-detection (mitigated: documented upgrade path to a model).

## Revisit when

- Network exposure / Phase-2 contribute lands → make output moderation + inbound scan mandatory.
- False-positive/negative rate becomes a real workflow issue → swap `moderation.py` for a local model.
- A name/address PII requirement appears → add optional NER (presidio/spaCy) behind a flag.
