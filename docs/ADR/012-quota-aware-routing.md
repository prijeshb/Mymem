# ADR 012: Quota-Aware Free-Tier Routing

## Status: Proposed (independent of ADR-011; shippable anytime)

Context: ADR-010 shipped a **static** cross-provider `FreeTierFallbackChain` (NVIDIA → Groq →
NVIDIA-alt → OpenRouter → Ollama floor). It only reacts *after* a 429 and treats every account as
always-available, so it re-hits a provider that is currently rate-limited and doesn't spread load
across per-account free buckets. This ADR records the decisions to make routing **quota-aware**.
Survey behind these choices: docs/research/knowledge-moat-and-free-tier-routing.md §4
(LiteLLM, OpenRouter, Copilot, Portkey, Cloudflare/Vercel).

---

## D1. In-process cooldown registry keyed off 429 + `Retry-After` (LiteLLM CooldownCache)

**Chosen:** a new `mymem/pipeline/router/_quota.py` holding a per-provider/per-account state registry;
on 429 set `cooldown_until = now + max(retry_after, backoff)`; the selector skips providers in cooldown.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **In-process cooldown registry** (chosen) | The dominant production pattern (LiteLLM/Helicone/OpenRouter); no external service; fits the local tool | A heuristic to maintain | ✅ |
| Keep static chain (ADR-010) | Simplest | Re-hits limited providers; wastes the chain head on guaranteed 429s | ❌ (what we're fixing) |
| External gateway (LiteLLM proxy/Portkey) | Batteries included | Adds a network hop + dependency to a local-first tool | ❌ |

**Explicitly avoid LiteLLM's documented bugs:** it drops `Retry-After` on 502/503/504
([#16286](https://github.com/BerriAI/litellm/issues/16286)) and ignores it in usage-based-v2
([#7669](https://github.com/BerriAI/litellm/issues/7669)). **We parse `Retry-After` for all error
classes** and clamp backoff sleeps to it. Use `time.monotonic()` (not wall-clock).

**Revisit when:** a provider exposes per-model (not per-account) limits.

---

## D2. Predictive token-bucket from `x-ratelimit-*` headers (avoid the 429)

**Chosen:** after each success, update a `RateWindow` (remaining RPM/TPM, reset_at) from response
headers; a pre-call check skips a provider whose remaining budget can't cover the estimated tokens —
*before* sending. Ollama (local) always passes.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Predictive token-bucket** (chosen) | Avoids burning the 40-RPM window on doomed calls; LiteLLM `enable_pre_call_checks` pattern | Header formats vary per provider | ✅ |
| React only to 429 (D1 alone) | Simpler | Still spends a request to discover the limit | ◑ (D1 is the floor; D2 is the optimization) |

**Mitigation for header variance:** a small per-provider header-parser map; when headers are absent,
fall back to pure reactive cooldown (D1).

**Revisit when:** providers change header schemas (keep the parser map in one place).

---

## D3. Multi-key / multi-account rotation (LiteLLM "multiple deployments, one model name")

**Chosen:** model multiple keys of one provider as separate accounts, each with its own
`ProviderState`/`RateWindow`; selection is weighted/round-robin over healthy accounts before falling
through to the next provider.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Per-account rotation** (chosen) | Multiplies free-tier throughput (each key = its own bucket); standard pattern | Manage multiple keys in `.env` | ✅ |
| Single key per provider | Simplest config | Caps throughput at one 40-RPM bucket | ◑ (works; rotation is opt-in) |

**Revisit when:** the user adds/removes accounts (keys read from `.env`, no code change).

---

## D4. Latency-EWMA preference + graceful degrade to Ollama (Copilot pattern)

**Chosen:** among healthy providers, prefer lowest EWMA latency (`0.3·sample + 0.7·prev`) with a small
buffer so others stay warm; when a session cost budget is exceeded, restrict routing to Ollama (the
zero-cost floor) — Copilot's "degrade to an included model when budget is spent."

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Latency-EWMA + degrade-to-local** (chosen) | Cheap (no Redis); spreads load; cost can't run away | EWMA is approximate | ✅ |
| Strict price-ordering only | Deterministic | Ignores live latency; hammers one provider | ❌ |
| Hard-fail on budget exceed | Predictable cost | Breaks interactive `mymem query` | ❌ |

**Revisit when:** local Ollama is unavailable on a given machine → make the floor configurable.

---

## D5. No call-site changes — implement as a pure selection layer

**Chosen:** `_quota.py` exposes a pure `select_provider(chain, now, registry) -> provider` (injectable
`now` + synthetic header dicts → fully unit-testable, no network). `_chain.py` consumes it; `_router.py`
updates the registry from response headers after each call. Same strategy seam as the existing
`IFallbackChain`; supersedes the static parts of ADR-010 without touching pipeline callers.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Pure selection layer** (chosen) | Testable without LLMs/network; no caller churn; matches router package style | One more module | ✅ |
| Inline the logic in `_router.call` | Fewer files | Hard to unit-test; violates <300-line/SRP rules | ❌ |

**Revisit when:** routing state needs to persist across processes → back the registry with SQLite.
