# MyMem — Learnings, Errors & Evals

A living log of mistakes made, lessons learned, and feature delivery assessments.

---

## [2026-04-21] Web Search for Related Links (Phase 1)

**Feature requested:** Backend searches the web for related concepts, ranks by relevance, returns top 3.

**Delivered:**
- ✅ `mymem/pipeline/search.py` — DDG search + word-overlap cosine scoring + Wikipedia fallback
- ✅ Results cached in-process per session
- ✅ SSE streams results per concept as they arrive
- ❌ First attempt used `duckduckgo-search` package (now renamed to `ddgs`) — caught by runtime test
- ❌ Used `structlog` in search.py which wasn't available in test env — should use stdlib `logging`

**Lessons:**
- Always `python -c` test external packages before wiring them in.
- Use stdlib `logging` in new pipeline modules unless structlog is confirmed available.
- **Present plan + wait for confirmation before coding any new feature.**

---

## [2026-04-21] Related Concepts Web Links

**Feature requested:** Related concepts in WikiPage should show web search results (not just static URLs).

**What was delivered vs asked:**
- ✅ Wikipedia API search integrated
- ✅ SSE streaming so page doesn't block
- ✅ Popover on broken wikilinks showing Wikipedia results
- ❌ First attempt used wrong Wikipedia User-Agent → 403 errors, feature appeared broken
- ❌ Initial static URLs (`duckduckgo.com/?q=...`) were passed as "web links" with no actual search — user had to point this out
- ❌ Jumped to implementation of `duckduckgo-search` package without presenting plan first

**Errors made:**
1. Used `MyMem/0.1 (knowledge-bot)` as User-Agent for Wikipedia API → blocked with 403. Fix: use Mozilla-compatible UA.
2. Generated static search URLs without actually fetching results — presented as "web links" which confused the user.
3. Started coding before explaining plan when user asked for a new feature.

**Lessons:**
- Always test external API calls (`python -c "..."`) before wiring them into the backend.
- "Web links" means real fetched results, not generated URLs.
- **Always present plan and get confirmation before implementation.**
- Create `docs/` and plan files for every new feature.

---

## [2026-04-21] Broken Wikilink Behavior

**Feature requested:** Broken `[[wikilinks]]` should not show raw brackets and should search the web.

**What was delivered:**
- ✅ Stripped `[[` / `]]` from link display text
- ✅ Popover with Wikipedia results on click
- ❌ First delivered DuckDuckGo redirect (wrong) — user wanted inline results, not redirect

**Lesson:** Read the full intent: "search and show results" ≠ "redirect to search engine".

---

## [2026-04-21] Date Bug (created/updated = article publication date)

**Root cause:** `_COMPILE_SYSTEM` prompt told LLM to write `created`/`updated` in frontmatter → LLM used article's pub date. `_strip_frontmatter` sometimes failed (code block wrapping).

**Fix:** Removed dates from LLM prompt. Added `dataclasses.replace(page, updated=date.today())` in `write_page` as a guaranteed stamp.

**Lesson:** Never let LLM control system metadata (dates, IDs). Always override in code.

---

## [2026-04-21] 403 on Webpage Ingest

**Root cause:** httpx was using `User-Agent: MyMem/0.1 (knowledge-bot)` → blocked by sites like DataCenterDynamics.

**Fix:** Switched to Chrome-compatible UA + Accept headers. Added friendly 403 error message pointing user to paste-text fallback.

**Lesson:** Any HTTP client fetching public web pages must use a browser-like UA.

---

## [2026-04-21] Wiki Page Layout & Related Web Articles

**Feature requested:** Related Concepts in wiki page sidebar, web article links loaded async via SSE.

**What was delivered vs asked:**
- ✅ Left sidebar: TOC + Backlinks + Related Concepts (internal wikilinks as indigo links, broken as amber links to internal slug)
- ✅ Related Web Articles as full-width card grid below article body
- ✅ SSE always fires — page title used as primary concept so seed is never empty
- ✅ Sidebar logic extracted into `WikiSidePane` component (modular file structure)
- ❌ Multiple layout iterations needed before landing on correct structure (right sidebar → subsection → left sidebar)
- ❌ `allWebLinks` variable left unused after removing central section — caught and cleaned up

**Errors made:**
1. SSE not firing: guard `if (!seed.length) return` blocked SSE when pages had no `[[wikilinks]]`. Fix: always seed with page title.
2. `IndexEntry.path` is relative — `e.path.stat().st_mtime` resolved from wrong directory. Fix: `(wiki_dir / e.path).stat().st_mtime`.
3. Related Concepts filtered to `r.internal` only — hid broken wikilinks from sidebar. Fix: show all, style differently.
4. `Link` import left in `WikiPage.tsx` after moving sidebar to `WikiSidePane` — removed.

**Lessons:**
- Always seed SSE with at least one guaranteed concept (page title) so the stream fires even on pages with no wikilinks.
- `IndexEntry.path` from `index.md` is relative — always resolve against `wiki_dir` before calling filesystem methods.
- Extract sidebar/panel logic into dedicated components early — modular file structure rule applies.
- Save user's memory preference: always use modular file structure (one purpose per file).

---

## [2026-04-21] Security Audit — Input Validation & Injection Prevention

**Finding:** `mymem/security/` has well-built `validate.py` and `sanitize.py` — but **neither is wired into the actual pipeline or API routes**. The security layer exists in isolation.

**Critical gaps found:**
- Prompt injection: `sanitize_for_prompt()` / `sanitize_query()` exist but never called — raw user content goes directly into LLM prompts in both `ingest.py` and `query.py`
- Path traversal: `/api/page/{slug:path}` allows slashes; no `relative_to()` boundary check before file read
- Path traversal: `slug_to_path()` uses raw user question in filenames when saving Q&A pages
- API validation bypass: `api.py` defines loose Pydantic models instead of using the strict ones in `security/validate.py`
- File upload: `file.filename` suffix used directly to create temp file — no sanitization

**What is safe:**
- SQL injection: parameterized queries everywhere
- Command injection: no shell/subprocess calls anywhere

**Fix order:**
1. Path traversal in `api_page()` — add `path.resolve().is_relative_to(wiki_dir.resolve())` check
2. Prompt injection in `query.py` — call `sanitize_query()` before LLM
3. Prompt injection in `ingest.py` — call `sanitize_for_prompt()` before LLM
4. Replace loose API models with `security/validate.py` models
5. Sanitize `slug_to_path()` output

**See full audit:** `docs/security_audit.md`

**Lesson:** Writing security utilities is not enough — they must be imported and called at every entry point. Wire them in during initial implementation, not as an afterthought.
