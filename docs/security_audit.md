# MyMem — Security Audit

## Input Validation Audit (Ingest Entry Points)

### POST /api/ingest (URL/Direct Source)
**Current Validation:**
- `source` field is plain `str` — no type/URL/path validation
- `source_type` is unconstrained `str` (comments list options, not enforced)
- `tags` passed as unvalidated `list[str]`
- `domain` passed as unvalidated `str`

**Missing:**
- URL format validation (scheme check, host validation)
- File path traversal protection
- Allowed file type enforcement
- File size limits
- `source_type` whitelist (accepts any string)
- Domain/tag format validation

---

### POST /api/upload (File Upload)
**Current Validation:**
- No validation on `UploadFile` size or MIME type
- `filename` trust issue: directly extracts suffix without sanitizing
- `source_type`, `domain`, `tags` strings unconstrained
- Creates temp file with untrusted extension

**Missing:**
- File size check before reading
- MIME type validation
- Filename sanitization (current code vulnerable to directory traversal via malicious filename)
- `source_type` enforcement
- Tag/domain format validation

---

### POST /api/ingest-text (Paste Text)
**Current Validation:**
- `text` checked for empty (`if not req.text.strip()`) — only check present

**Missing:**
- Text length/size limit
- `source_type` whitelist
- Domain/tag format validation
- Title path-traversal protection

---

### ingest_source() Function
**Current:**
- HIGH-severity secret scan — catches API keys/credentials
- No file size limit check before reading
- No URL validation in `_read_source()`

**Missing:**
- File size limit enforcement (`validate.py` defines `check_file_size()` but never called)
- Max text length validation
- Prompt injection detection (`sanitize.py` has it but not called before ingest)

---

### Security Module (validate.py, sanitize.py)
**Available but Unused:**
- `validate.py` defines strict `IngestRequest` model with URL/file checks and size limits — not wired into `api.py`
- `sanitize.py` has prompt injection detection and wrapping — not called in ingest pipeline
- Secret scanner runs once at ingest start, but content is processed unfiltered afterward

---

## Injection Attack Prevention Audit

### What Is Protected

| Vector | Status |
|--------|--------|
| SQL injection | Parameterized queries everywhere — CLEAN |
| Command injection | No shell/subprocess calls — CLEAN |
| XSS | API returns markdown (not HTML), frontend owns rendering — LOW |

---

### CRITICAL — Prompt Injection (Never Wired)

`mymem/security/sanitize.py` has well-designed `sanitize_for_prompt()` / `sanitize_query()` with HIGH/LOW pattern detection and XML delimiter wrapping. **Neither is ever called.**

- `ingest.py` — embeds raw `source_text` directly into LLM prompts. `sanitize_for_prompt` never imported.
- `query.py` — injects raw user question into LLM prompt. `sanitize_query` never called.
- `api.py` — uses custom loose Pydantic models; the validated models in `security/validate.py` are entirely unused.

---

### HIGH — Path Traversal (Two Places)

1. **`/api/page/{slug:path}`** — FastAPI allows slashes in `{slug:path}`. A request to `/api/page/../../etc/passwd` resolves outside `wiki_dir`. No `relative_to()` boundary check before file read.

2. **`slug_to_path()` in `page.py`** — user question used raw in filename when saving Q&A pages. Characters like `../` not stripped by `.lower().replace(" ", "-")`.

---

### Full Risk Table

| Vector | Status | Severity |
|--------|--------|----------|
| SQL injection | Parameterized queries everywhere | None |
| Command injection | No shell calls | None |
| Prompt injection (ingest) | Sanitizer exists but never called | CRITICAL |
| Prompt injection (query) | Sanitizer exists but never called | CRITICAL |
| API input validation | Custom models bypass `security/validate.py` | HIGH |
| Path traversal (`/api/page/{slug}`) | No boundary check on slug | HIGH |
| Path traversal (`slug_to_path` via question) | Raw question used in filename | HIGH |
| XSS | API returns markdown, not HTML | Low (frontend-dependent) |

---

## Required Fixes (Priority Order)

1. **`api_page()` — path traversal** (exploitable now)
   ```python
   path = wiki_dir / f"{slug}.md"
   if not path.resolve().is_relative_to(wiki_dir.resolve()):
       raise HTTPException(status_code=400, detail="Invalid slug")
   ```

2. **`query.py` — prompt injection**
   Call `sanitize_query(question)` at top of `query_wiki()`, raise on HIGH risk.

3. **`ingest.py` — prompt injection**
   Call `sanitize_for_prompt(source_text)` before passing content to `_extract_prompt` / `_compile_prompt`.

4. **`api.py` — validation bypass**
   Replace loose `QueryRequest` / `IngestRequest` with validated models from `mymem.security.validate`.

5. **`slug_to_path()` — path traversal**
   Sanitize slug with `_SAFE_FILENAME_RE` from `validate.py` before constructing path.
