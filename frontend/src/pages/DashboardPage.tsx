import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { marked } from 'marked';
import { fetchStats, fetchPagesPaged, fetchLog, streamQuery, titleToSlug } from '../lib/api';
import type { Stats, LogEntry, Domain } from '../lib/types';
import { ALL_DOMAINS } from '../lib/types';
import { DomainBadge } from '../components/DomainBadge';
import { CitationChip } from '../components/CitationChip';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { useKeyboardShortcut } from '../lib/useKeyboardShortcut';
import { Button, Card, Input } from '@heroui/react';

interface StreamState {
  text: string;
  citations: string[];
  phase: 'idle' | 'streaming' | 'done' | 'error';
  error?: string;
}

const DOMAIN_TILE_DARK: Record<string, { bg: string; text: string }> = {
  tech:      { bg: '#1e2d45', text: '#6ea8fe' },
  spiritual: { bg: '#271d40', text: '#c4a7e7' },
  finance:   { bg: '#1a2e22', text: '#75b798' },
  health:    { bg: '#1a2e2e', text: '#6edff6' },
  reminder:  { bg: '#332916', text: '#ffc107' },
  research:  { bg: '#1a2b40', text: '#79c0ff' },
  personal:  { bg: '#381d28', text: '#f1aeb5' },
  creative:  { bg: '#321736', text: '#d8b4fe' },
  business:  { bg: '#33241a', text: '#ffb347' },
  misc:      { bg: '#2b3035', text: '#adb5bd' },
};

const DOMAIN_TILE_LIGHT: Record<string, { bg: string; text: string }> = {
  tech:      { bg: '#eef2ff', text: '#4338ca' },
  spiritual: { bg: '#f5f3ff', text: '#7c3aed' },
  finance:   { bg: '#f0fdf4', text: '#15803d' },
  health:    { bg: '#f0fdfa', text: '#0f766e' },
  reminder:  { bg: '#fffbeb', text: '#b45309' },
  research:  { bg: '#f0f9ff', text: '#0369a1' },
  personal:  { bg: '#fff1f2', text: '#be123c' },
  creative:  { bg: '#fdf4ff', text: '#a21caf' },
  business:  { bg: '#fff7ed', text: '#c2410c' },
  misc:      { bg: '#f8fafc', text: '#475569' },
};

function useDomainTileColors() {
  const isDark = document.documentElement.classList.contains('dark');
  return isDark ? DOMAIN_TILE_DARK : DOMAIN_TILE_LIGHT;
}

function DomainHeatmap({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  const tileColors = useDomainTileColors();

  return (
    <Card>
      <Card.Header>
        <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-400">
          Domain Heatmap
        </Card.Title>
      </Card.Header>
      <Card.Content>
        <div className="grid grid-cols-2 gap-2">
          {entries.map(([domain, count]) => {
            const colors = tileColors[domain] ?? tileColors.misc;
            const intensity = count / max;
            return (
              <Link
                key={domain}
                to={`/search?domain=${domain}`}
                className="relative rounded-lg p-3 overflow-hidden flex flex-col gap-1
                           transition-transform hover:scale-[1.03] outline-hidden
                           focus-visible:ring-2 focus-visible:ring-indigo-400"
                style={{ backgroundColor: colors.bg }}
              >
                <div
                  className="absolute bottom-0 left-0 h-0.5 rounded-b-lg transition-all"
                  style={{ width: `${intensity * 100}%`, backgroundColor: colors.text }}
                />
                <span className="text-xs font-medium capitalize" style={{ color: colors.text }}>
                  {domain}
                </span>
                <span className="text-xl font-bold text-gray-100 leading-none">{count}</span>
                <span className="text-xs" style={{ color: colors.text, opacity: 0.7 }}>
                  {count === 1 ? 'page' : 'pages'}
                </span>
              </Link>
            );
          })}
        </div>
      </Card.Content>
    </Card>
  );
}

export function DashboardPage() {
  const PAGES_SIZE = 10;

  const [stats, setStats]               = useState<Stats | null>(null);
  const [pagedEntries, setPagedEntries] = useState<import('../lib/types').Page[]>([]);
  const [pagesTotal, setPagesTotal]     = useState(0);
  const [log, setLog]                   = useState<LogEntry[]>([]);
  const [filter, setFilter]             = useState('');
  const [pagesCurrent, setPagesCurrent] = useState(0);
  const [question, setQuestion]         = useState('');
  const [domain, setDomain]             = useState<Domain | ''>('');
  const [save, setSave]                 = useState(false);
  const [stream, setStream]             = useState<StreamState>({ text: '', citations: [], phase: 'idle' });
  const [loadingStats, setLoadingStats] = useState(true);
  const [loadingPages, setLoadingPages] = useState(true);
  const [error, setError]               = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useKeyboardShortcut('/', () => inputRef.current?.focus());

  useEffect(() => {
    Promise.all([fetchStats(), fetchLog(15)])
      .then(([s, l]) => { setStats(s); setLog(l); })
      .catch(e => setError(String(e)))
      .finally(() => setLoadingStats(false));
  }, []);

  useEffect(() => {
    setLoadingPages(true);
    fetchPagesPaged(pagesCurrent, PAGES_SIZE, undefined, filter || undefined)
      .then(r => { setPagedEntries(r.items); setPagesTotal(r.total); })
      .catch(e => setError(String(e)))
      .finally(() => setLoadingPages(false));
  }, [pagesCurrent, filter]);

  useEffect(() => { setPagesCurrent(0); }, [filter]);

  const totalPagesCount = Math.ceil(pagesTotal / PAGES_SIZE);

  async function ask() {
    if (!question.trim()) return;
    setStream({ text: '', citations: [], phase: 'streaming' });
    try {
      for await (const ev of streamQuery({ question, domain, top_k: 5, save })) {
        if (ev.type === 'token') {
          setStream(s => ({ ...s, text: s.text + ev.text }));
        } else if (ev.type === 'done') {
          setStream(s => ({ ...s, citations: ev.citations, phase: 'done' }));
        } else if (ev.type === 'error') {
          setStream(s => ({ ...s, phase: 'error', error: ev.message }));
        }
      }
    } catch (e) {
      setStream(s => ({ ...s, phase: 'error', error: String(e) }));
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
      {/* ── Left sidebar ── */}
      <aside className="space-y-4">
        {/* Stats */}
        <Card>
          <Card.Header>
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-400">
              Overview
            </Card.Title>
          </Card.Header>
          <Card.Content>
            {loadingStats ? <LoadingSpinner size="sm" /> : stats ? (
              <dl className="space-y-2">
                {[
                  ['Pages', stats.page_count],
                  ['Sources', stats.source_count],
                  ['Orphans', stats.orphan_count],
                  ['Session cost', `$${stats.session_cost.toFixed(4)}`],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex items-center justify-between text-sm">
                    <dt className="text-gray-400">{label}</dt>
                    <dd className="font-semibold text-gray-100">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </Card.Content>
        </Card>

        {/* Domain heatmap */}
        {stats && Object.keys(stats.domain_counts).length > 0 && (
          <DomainHeatmap counts={stats.domain_counts} />
        )}

        {/* Recent log */}
        {log.length > 0 && (
          <Card>
            <Card.Header>
              <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-400">
                Recent Activity
              </Card.Title>
            </Card.Header>
            <Card.Content>
              <ul className="space-y-2">
                {log.slice(0, 8).map((entry, i) => (
                  <li key={i} className="text-xs">
                    <span className={`inline-block px-1.5 py-0.5 rounded text-xs mr-1.5 ${
                      entry.operation === 'ingest'
                        ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
                        : entry.operation === 'query'
                        ? 'bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300'
                        : entry.operation === 'introspect'
                        ? 'bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300'
                        : 'bg-gray-800 text-gray-400'
                    }`}>
                      {entry.operation}
                    </span>
                    <span className="text-gray-400 truncate">{entry.description}</span>
                  </li>
                ))}
              </ul>
            </Card.Content>
          </Card>
        )}
      </aside>

      {/* ── Right main area ── */}
      <div className="space-y-6">
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        {/* Quick ask */}
        <Card>
          <Card.Header>
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-400">
              Quick Ask
            </Card.Title>
          </Card.Header>
          <Card.Content className="space-y-3">
            <div className="flex gap-2">
              <Input
                ref={inputRef}
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && ask()}
                placeholder="Ask anything from your wiki… (press / to focus)"
                aria-label="Question"
                fullWidth
              />
              <select
                value={domain}
                onChange={e => setDomain(e.target.value as Domain | '')}
                aria-label="Domain filter"
                className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-2 text-sm text-gray-100
                           focus:outline-hidden focus:ring-2 focus:ring-indigo-400 [color-scheme:dark]"
              >
                <option value="">All domains</option>
                {ALL_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
              <Button
                variant="primary"
                size="sm"
                onPress={ask}
                isDisabled={stream.phase === 'streaming'}
              >
                Ask
              </Button>
            </div>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
              <input
                type="checkbox"
                checked={save}
                onChange={e => setSave(e.target.checked)}
                className="rounded border-gray-600 bg-gray-800 focus:ring-indigo-400"
              />
              Save answer as wiki page
            </label>

            {stream.phase !== 'idle' && (
              <div className="mt-4 p-4 rounded-lg bg-gray-950 border border-gray-800">
                {stream.phase === 'streaming' && (
                  <p className="text-sm text-gray-200 whitespace-pre-wrap">
                    {stream.text}
                    <span className="typing-dot ml-1">●</span>
                  </p>
                )}
                {stream.phase === 'done' && (
                  <>
                    <div
                      className="prose dark:prose-invert prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: marked.parse(stream.text) as string }}
                    />
                    {stream.citations.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {stream.citations.map(c => <CitationChip key={c} title={c} />)}
                      </div>
                    )}
                  </>
                )}
                {stream.phase === 'error' && (
                  <p className="text-sm text-red-400">{stream.error}</p>
                )}
              </div>
            )}
          </Card.Content>
        </Card>

        {/* Pages list */}
        <Card>
          <Card.Header className="flex items-center justify-between">
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-400">
              Wiki Pages ({pagesTotal})
            </Card.Title>
            <Input
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Filter…"
              aria-label="Filter pages"
              className="w-48"
            />
          </Card.Header>
          <Card.Content className="p-0">
            {loadingPages ? <LoadingSpinner /> : (
              <>
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                      <th className="text-left px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 w-full">Title</th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 whitespace-nowrap">Domain</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pagedEntries.length === 0 ? (
                      <tr>
                        <td colSpan={2} className="py-8 text-sm text-center text-gray-500 italic">
                          No pages yet — ingest some sources!
                        </td>
                      </tr>
                    ) : pagedEntries.map((p, i) => (
                      <tr
                        key={p.title}
                        className={`border-b border-gray-100 dark:border-gray-800 transition-colors
                          ${i % 2 === 1 ? 'bg-gray-50/60 dark:bg-gray-800/20' : ''}
                          hover:bg-indigo-50/70 dark:hover:bg-indigo-950/30`}
                      >
                        <td className="px-4 py-2.5 max-w-0">
                          <Link
                            to={`/wiki/${titleToSlug(p.title)}`}
                            className="block truncate font-medium text-indigo-600 hover:text-indigo-800
                                       dark:text-indigo-300 dark:hover:text-indigo-200
                                       outline-none focus-visible:underline"
                            title={p.title}
                          >
                            {p.title}
                          </Link>
                          {p.summary && (
                            <p className="truncate text-xs text-gray-500 dark:text-gray-500 mt-0.5">{p.summary}</p>
                          )}
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap">
                          <DomainBadge domain={p.domain} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* Pagination */}
                {pagesTotal > PAGES_SIZE && (
                  <div className="flex items-center justify-between px-4 py-2.5 border-t border-gray-100 dark:border-gray-800">
                    <span className="text-xs text-gray-500 dark:text-gray-500">
                      {pagesCurrent * PAGES_SIZE + 1}–{Math.min((pagesCurrent + 1) * PAGES_SIZE, pagesTotal)} of {pagesTotal} pages
                    </span>
                    <div className="flex gap-1">
                      <button
                        onClick={() => setPagesCurrent(p => Math.max(0, p - 1))}
                        disabled={pagesCurrent === 0}
                        className="px-2.5 py-1 text-xs rounded border border-gray-300 dark:border-gray-600
                                   text-gray-500 dark:text-gray-400 disabled:opacity-30
                                   hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                      >
                        ‹ Prev
                      </button>
                      <button
                        onClick={() => setPagesCurrent(p => Math.min(totalPagesCount - 1, p + 1))}
                        disabled={pagesCurrent >= totalPagesCount - 1}
                        className="px-2.5 py-1 text-xs rounded border border-gray-300 dark:border-gray-600
                                   text-gray-500 dark:text-gray-400 disabled:opacity-30
                                   hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                      >
                        Next ›
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </Card.Content>
        </Card>
      </div>
    </div>
  );
}
