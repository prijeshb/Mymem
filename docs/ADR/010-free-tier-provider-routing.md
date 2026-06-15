# ADR 010: Free-Tier Provider Routing

## Status: Accepted (V1-0008)

Goal: run the full ingest/query pipeline at **zero per-token cost**, surviving
free-tier rate limits. Anthropic credits were exhausted; the user wanted to rely
entirely on free providers. Each section records a decision.

---

## D1. NVIDIA NIM as primary free provider

**Chosen:** `provider: nvidia` with `meta/llama-3.3-70b-instruct` for heavy tasks.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **NVIDIA NIM** (chosen) | Free, no card, OpenAI-compatible, strong 70B models, 128k context | 40 RPM/account; free models deprecate on short notice (Kimi K2 → 410 observed) | ✅ |
| Anthropic Claude | Best quality | Account out of credits; paid | ❌ (revisit when funded) |
| OpenRouter `:free` | Many models incl. Kimi/MiniMax | On this account every `:free` slug returns 404 "paid only" or 429 — needs credit balance to be reliable | Defer |
| Local Ollama only | Fully offline, no limit | Slower; large models exceed local RAM | Kept as fallback floor |

**Revisit when:** Anthropic is funded (switch heavy tasks back to Claude) or
OpenRouter account gains credit.

**Rate limit (measured/researched):** NVIDIA free = **40 RPM, per account** —
so a swap on 429 must cross providers (D3), not pick another NVIDIA model.

---

## D2. Per-task models spread across providers (not one model everywhere)

**Chosen:** heavy tasks (compile/qa/merge/introspect) on NVIDIA; frequent light
tasks (classify/lint) on **Groq** `llama-3.1-8b-instant`. The router resolves each
model's provider from the registry, so one config maps to two free accounts.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Per-task, multi-provider** (chosen) | Spreads load over two 40-RPM-class buckets; right-sizes cheap tasks to a fast small model | Two keys to keep valid | ✅ |
| One model for all tasks | Simple config | Concentrates every call on one 40 RPM bucket; wrong-sizes lint/classify | ❌ (explicitly rejected by user) |

**Revisit when:** a third task class needs its own tier, or Groq limits tighten.

---

## D3. Cross-provider `FreeTierFallbackChain` for rate-limit swap

**Chosen:** on failure/429 the router walks a chain that crosses providers:
`preferred → Groq → NVIDIA-alt → Groq-small → OpenRouter(key-gated) → Ollama floor`.
Built on the existing per-model-provider resolution in `ModelRouter.call`, so no
call-site changes — only a new `IFallbackChain` implementation, selected in
`router_from_settings` when `provider ∈ {nvidia, groq, openrouter}`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Cross-provider chain** (chosen) | A per-account 429 doesn't stall the pipeline; Ollama floor guarantees progress; reuses Strategy/CoR already in the router | Chain order is a heuristic to maintain | ✅ |
| Same-provider NVIDIA alternates | Simplest | Useless: 40 RPM is per-account, the alt 429s too | ❌ |
| Retry-with-backoff on one model | No model switch | Wastes the 40 RPM window; slow | ❌ |

**Revisit when:** a provider exposes per-model (not per-account) limits, making
same-provider swaps useful.

---

## D4. Accept the `OPEN_ROUTER_API_KEY` misspelling via alias

**Chosen:** `validation_alias=AliasChoices("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY")`
on the settings field; also added `groq`/`openrouter` to the `provider` Literal.

**Why:** the user's `.env` used `OPEN_ROUTER_API_KEY` (extra underscore), so the
key silently read as unset. Accepting both spellings is resilient and touches no
secrets file. **Revisit when:** standardizing env var names project-wide.
