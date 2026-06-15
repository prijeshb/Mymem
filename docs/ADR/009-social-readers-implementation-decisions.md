# ADR 009: Social Source Readers — Implementation Decision Record

## Status: Accepted (V1-0008)

Pasting an X/Twitter thread URL compiled into **one** wiki idea. Root cause: the
`tweet` source type had no dedicated reader, so it fell through to the generic
`WebSourceReader` (trafilatura/httpx). X is a JS-rendered SPA behind auth — a
non-browser client only sees the root tweet's `og:description`, so the LLM was
starved of input. This ADR records the decisions made adding first-class readers
for X/Twitter and Reddit.

Each section: what we chose, the alternatives, pros/cons, and when to revisit.

### Design principles applied
- **SRP** — fetch strategies live in `social_readers.py`, separate from the
  generic web/file readers; assembly (`_build_tweet_text`, `_build_reddit_text`)
  is split from transport (`_http_get`).
- **OCP** — a new platform is a new `SourceReader` subclass registered in the
  chain; no existing reader is edited (D3).
- **LSP** — `TweetSourceReader`/`RedditSourceReader` honor the `SourceReader`
  contract: `can_handle()` then `read() -> str`, raising only the documented
  `ValueError`/`RuntimeError`.
- **DIP** — the network boundary is the injectable seam (`_http_get` patched at
  `httpx.AsyncClient` in tests); parsing functions are pure.
- **GoF: Strategy** (one reader per platform) + **Chain of Responsibility**
  (first reader to claim the source wins) — both already established in
  `readers.py`; this change extends them rather than introducing new machinery.

---

## D1. Tweet fetch: public syndication API over scraping or official API

**Chosen:** `cdn.syndication.twimg.com/tweet-result` — the no-auth endpoint that
powers embedded tweets — with a token derived from the tweet ID (port of
react-tweet's `getToken`).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Syndication API** (chosen) | No auth, no key, server-side full untruncated text + quoted tweet + media alt-text; stable shape used by react-tweet in production | Single tweet, not full self-thread (mitigated by D4); unofficial, could change | ✅ |
| Official X API v2 | Supported, thread expansion | Paid tier for meaningful access; API key contradicts the "personal, key-optional" posture; heavy for a wiki ingester | ❌ |
| Scrape the X HTML page | No dependency | Returns a JS shell — this is the bug we're fixing | ❌ |
| `snscrape` / `twikit` | Full threads | Fragile, frequently broken by X changes; some need credentials | Defer |

**Revisit when:** the syndication endpoint starts returning 404/empty for valid
public tweets, or full self-thread unrolling becomes a hard requirement.

---

## D2. New module `social_readers.py` rather than growing `readers.py`

**Chosen:** a dedicated module; register its readers in `readers._default_readers()`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Separate module** (chosen) | `readers.py` was already 438 lines (over the 300 house limit); social fetching is a distinct concern (token math, JSON assembly) | One more import; a lazy import to dodge a cycle (D6) | ✅ |
| Add classes inside `readers.py` | No new file | Pushes the file toward 650+ lines, worsening an existing violation | ❌ |

**Revisit when:** a third social platform makes the module exceed ~300 lines — then
split per platform.

---

## D3. Registration: extend the chain, don't branch existing readers

**Chosen:** insert `TweetSourceReader` and `RedditSourceReader` ahead of
`WebSourceReader` in the ordered chain. `RedditSourceReader.can_handle()` claims
any `reddit.com` URL regardless of `source_type`, so even a link the frontend
tagged `webpage` is routed correctly.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **New readers in the chain** (chosen) | Pure OCP — no edit to working readers; ordering makes precedence explicit | Must keep specific-before-generic ordering correct | ✅ |
| `if source_type == "tweet"` branch in `WebSourceReader` | Fewer classes | Violates OCP/SRP; every new platform adds an `elif` to a working reader | ❌ |

**Revisit when:** dispatch needs more than first-match (e.g. content negotiation).

---

## D4. Nitter as a bounded last-resort fallback, kept minimal

**Chosen:** if syndication yields nothing, try a short hardcoded list of nitter
mirrors and strip HTML to text; if all fail, raise an actionable "paste the text"
error rather than degrading silently.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Minimal nitter fallback** (chosen) | Recovers full threads when a mirror is alive; clear error when not | Public mirrors are mostly dead/rate-limited in 2024+ | ✅ |
| No fallback | Simpler | Loses the one path that returns a full self-thread | ❌ |
| Self-hosted nitter | Reliable | Infra burden for a personal tool | Defer |

**Revisit when:** the public mirrors prove consistently dead in practice (then drop
them) or thread completeness matters enough to self-host.

---

## D5. Reddit via the `.json` permalink endpoint over PRAW

**Chosen:** append `.json` to the permalink (`?raw_json=1&limit=20`), parse post
selftext + top comments. Treat 403/429 as an actionable "paste the text" error.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **`.json` endpoint** (chosen) | No auth, no dependency; same httpx path as everything else; comments included | Rate-limited on default UAs (we send a browser UA); unofficial | ✅ |
| PRAW + OAuth app | Official, robust | Requires registering an app + credentials — contradicts key-optional posture | ❌ |

**Revisit when:** Reddit tightens `.json` access enough that a browser UA no longer
suffices.

---

## D6. Lazy, cached reader chain to break the import cycle

**Chosen:** replace the module-level `_chain = SourceReaderChain()` with an
`@lru_cache`d `_get_chain()`. `social_readers` imports `SourceReader` from
`readers`, and `readers._default_readers()` imports `social_readers` — building the
chain at import time deadlocked. Lazy construction defers the social import to
first use; `lru_cache` keeps it a singleton without a mutable global (honors the
immutability rule).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **`@lru_cache` accessor** (chosen) | Breaks the cycle; no `global` mutation; readers are stateless so caching is safe | Indirection vs a bare module constant | ✅ |
| `global _chain` lazy singleton | Familiar | Mutable module state — against the house immutability rule | ❌ |
| Move social readers' base class out to a third module | No cycle | More files for one ABC; over-engineered | Defer |

**Revisit when:** readers gain per-instance state (then caching a singleton is wrong).

---

## D7. Separate fetch from parse for honest tests

**Chosen:** `_build_tweet_text` / `_build_reddit_text` are pure functions over the
response dicts; only `_http_get` touches the network. Tests assert real assembly
(author, full body, quoted tweet, alt-text, comment filtering), token structure,
chain dispatch order, and the syndication→nitter fallback — mocking only
`httpx.AsyncClient`, never the reader's own logic.

**Revisit when:** never expected to — this is the testing convention going forward.
