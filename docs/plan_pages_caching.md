# Plan: Wiki Pages Table Caching Strategies

## Strategy 3 — Frontend Stale-While-Revalidate (Best UX, ~2 hrs)

Replace raw `fetch` calls with React Query (`@tanstack/react-query`).
Pages show instantly from cache while a background refetch happens silently.

```ts
import { useQuery } from '@tanstack/react-query';

const { data } = useQuery({
  queryKey: ['pages', currentPage, filter, domain],
  queryFn: () => fetchPagesPaged(currentPage, PAGE_SIZE, domain, filter),
  staleTime: 30_000,           // treat data fresh for 30s
  placeholderData: keepPreviousData,  // no flicker on page change
});
```

**Benefits:**
- Instant page navigation — previous pages served from cache
- No loading spinner on back-navigation
- Automatic background refresh after staleTime

**Files affected:**
- `frontend/package.json` — add `@tanstack/react-query`
- `frontend/src/main.tsx` — wrap app in `<QueryClientProvider>`
- `frontend/src/pages/DashboardPage.tsx` — replace `useState` + `useEffect` fetch with `useQuery`
- `frontend/src/pages/SearchPage.tsx` — same

---

## Strategy 4 — SQLite-Backed Index (Long-term, ~4 hrs)

Replace `index.md` parsing with a proper SQLite table + FTS5 full-text search.
Queries become O(log n) with native LIKE/MATCH instead of Python list comprehensions.

```sql
CREATE TABLE pages (
  title        TEXT PRIMARY KEY,
  summary      TEXT,
  domain       TEXT,
  source_count INT,
  tags         TEXT,
  updated_at   TEXT
);
CREATE VIRTUAL TABLE pages_fts USING fts5(title, summary, content='pages');
```

**Benefits:**
- Fast server-side search (FTS5 vs Python string scan)
- Cursor-based pagination — no file parsing overhead
- Native `ORDER BY updated_at DESC` — accurate recency sort
- No regex parsing of `index.md` on every request

**Files affected:**
- `mymem/wiki/index.py` — replace `IndexManager` with SQLite-backed version
- `mymem/wiki/types.py` — add `updated_at` to `IndexEntry`
- `mymem/web/routes/api.py` — update `api_pages` to use SQL query
- `data/mymem.db` — add `pages` + `pages_fts` tables (migration needed)

---

## Recommendation

| | Strategy 3 (React Query) | Strategy 4 (SQLite index) |
|--|--|--|
| Effort | ~2 hrs | ~4 hrs |
| UX gain | Instant navigation, no flicker | Faster search, accurate sort |
| Risk | Low | Medium (index migration) |
| Priority | Implement first | Plan for Phase 2 |

Do Strategy 3 first for immediate UX win. Strategy 4 when wiki grows large enough that Python list scanning becomes slow (>500 pages).
