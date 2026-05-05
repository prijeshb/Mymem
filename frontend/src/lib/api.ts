import type {
  Page, PagedPages, Stats, GraphData, IngestResult, IntrospectResult,
  CuriosityResult, LintResult, SSEEvent, SourceType, Domain,
  WikiPageData, LogEntry, HeatmapData, DailySummary, ArchivedPage,
} from './types';

// In dev Vite proxies /api/* → :7860; in prod FastAPI serves everything
const BASE = '';

// ── GET helpers ──────────────────────────────────────────────────────────────

export async function fetchPages(domain?: Domain | '', tag?: string): Promise<Page[]> {
  const p = new URLSearchParams();
  if (domain) p.set('domain', domain);
  if (tag)    p.set('tag', tag);
  const res = await fetch(`${BASE}/api/pages?${p}`);
  if (!res.ok) throw new Error(`GET /api/pages: ${res.status}`);
  return res.json();
}

export async function fetchPagesPaged(
  page: number,
  limit: number,
  domain?: Domain | '',
  q?: string,
): Promise<PagedPages> {
  const p = new URLSearchParams();
  p.set('page', String(page));
  p.set('limit', String(limit));
  if (domain) p.set('domain', domain);
  if (q)      p.set('q', q);
  const res = await fetch(`${BASE}/api/pages?${p}`);
  if (!res.ok) throw new Error(`GET /api/pages: ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<Stats> {
  const res = await fetch(`${BASE}/api/stats`);
  if (!res.ok) throw new Error(`GET /api/stats: ${res.status}`);
  return res.json();
}

export async function fetchGraph(): Promise<GraphData> {
  const res = await fetch(`${BASE}/api/graph`);
  if (!res.ok) throw new Error(`GET /api/graph: ${res.status}`);
  return res.json();
}

export async function fetchLint(): Promise<LintResult> {
  const res = await fetch(`${BASE}/api/lint`);
  if (!res.ok) throw new Error(`GET /api/lint: ${res.status}`);
  return res.json();
}

export async function fetchIntrospect(topic?: string, dateStr?: string, force = false): Promise<IntrospectResult> {
  const p = new URLSearchParams();
  if (topic)   p.set('topic', topic);
  if (dateStr) p.set('date_str', dateStr);
  if (force)   p.set('force', 'true');
  const res = await fetch(`${BASE}/api/introspect?${p}`);
  if (!res.ok) throw new Error(`GET /api/introspect: ${res.status}`);
  return res.json();
}

export async function fetchCuriosity(limit = 20): Promise<CuriosityResult> {
  const res = await fetch(`${BASE}/api/curiosity?limit=${limit}`);
  if (!res.ok) throw new Error(`GET /api/curiosity: ${res.status}`);
  return res.json();
}

export async function fetchPage(slug: string): Promise<WikiPageData> {
  const res = await fetch(`${BASE}/api/page/${slug}`);
  if (!res.ok) throw new Error(`Page not found: ${slug}`);
  return res.json();
}

export async function patchPage(
  slug: string,
  tags: string[],
  domain: string,
): Promise<{ ok: boolean; tags: string[]; domain: string }> {
  const res = await fetch(`${BASE}/api/page/${slug}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags, domain }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Update failed');
  return data;
}

export async function deletePage(slug: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/api/page/${slug}`, { method: 'DELETE' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Delete failed');
  return data;
}

export async function archivePage(slug: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/api/page/${slug}/archive`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Archive failed');
  return data;
}

export async function restorePage(slug: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/api/page/${slug}/restore`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Restore failed');
  return data;
}

export async function fetchArchivedPages(): Promise<ArchivedPage[]> {
  const res = await fetch(`${BASE}/api/archived`);
  if (!res.ok) throw new Error(`GET /api/archived: ${res.status}`);
  return res.json();
}

export async function fetchLog(limit = 15): Promise<LogEntry[]> {
  const res = await fetch(`${BASE}/api/log?limit=${limit}`);
  if (!res.ok) throw new Error(`GET /api/log: ${res.status}`);
  return res.json();
}

export async function fetchHeatmap(): Promise<HeatmapData> {
  const res = await fetch(`${BASE}/api/heatmap`);
  if (!res.ok) throw new Error(`GET /api/heatmap: ${res.status}`);
  return res.json();
}

export async function fetchDailySummaries(limit = 14): Promise<DailySummary[]> {
  const res = await fetch(`${BASE}/api/daily?limit=${limit}`);
  if (!res.ok) throw new Error(`GET /api/daily: ${res.status}`);
  return res.json();
}

// ── POST /api/ingest ─────────────────────────────────────────────────────────

export async function postIngest(payload: {
  source: string;
  source_type: SourceType;
  tags: string[];
  domain: string;
}): Promise<IngestResult> {
  const res = await fetch(`${BASE}/api/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Ingest failed');
  return data;
}

// ── POST /api/upload (multipart) ─────────────────────────────────────────────

export async function postUpload(
  file: File,
  sourceType: SourceType,
  domain: string,
  tags: string[],
): Promise<IngestResult> {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('source_type', sourceType);
  fd.append('domain', domain);
  fd.append('tags', tags.join(','));
  const res = await fetch(`${BASE}/api/upload`, { method: 'POST', body: fd });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Upload failed');
  return data;
}

export async function postIngestText(
  text: string,
  title: string,
  sourceType: SourceType,
  domain: string,
  tags: string[],
): Promise<IngestResult> {
  const res = await fetch(`${BASE}/api/ingest-text`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, title, source_type: sourceType, domain, tags }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? 'Ingest failed');
  return data;
}

// ── POST /api/query — SSE async generator ────────────────────────────────────

export async function* streamQuery(payload: {
  question: string;
  domain: string;
  top_k: number;
  save: boolean;
}): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) throw new Error(`POST /api/query: ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() ?? '';
    for (const part of parts) {
      const line = part.replace(/^data: /, '');
      if (!line) continue;
      try { yield JSON.parse(line) as SSEEvent; } catch { /* skip malformed */ }
    }
  }
}

// ── GET /api/related-web — SSE stream ────────────────────────────────────────

export async function* streamRelatedWeb(
  concepts: string[],
  pageSlug?: string,
): AsyncGenerator<{ slug: string; web_links: import('./types').RelatedWebLink[] } | { done: true }> {
  if (!concepts.length) return;
  const p = new URLSearchParams({ concepts: concepts.join(',') });
  if (pageSlug) p.set('page_slug', pageSlug);
  const res = await fetch(`${BASE}/api/related-web?${p}`);
  if (!res.ok || !res.body) return;

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() ?? '';
    for (const part of parts) {
      const line = part.replace(/^data: /, '').trim();
      if (!line) continue;
      try { yield JSON.parse(line); } catch { /* skip malformed */ }
    }
  }
}

// ── Utility ──────────────────────────────────────────────────────────────────

export function titleToSlug(title: string): string {
  return title.toLowerCase().replace(/ /g, '-');
}
