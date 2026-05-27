import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { marked } from 'marked';
import { fetchStats, fetchPagesPaged, streamQuery, titleToSlug } from '../lib/api';
import type { Stats, Domain } from '../lib/types';
import { ALL_DOMAINS } from '../lib/types';
import { DomainBadge, DOMAIN_CLASSES } from '../components/DomainBadge';
import { CitationChip } from '../components/CitationChip';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { useKeyboardShortcut } from '../lib/useKeyboardShortcut';

// ── types ────────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  citations: string[];
  phase: 'done' | 'streaming' | 'error';
  timestamp: string;
  error?: string;
}

// ── helpers ──────────────────────────────────────────────────────────────────

const DOMAIN_DOT: Record<string, string> = {
  tech:      'bg-indigo-500',
  spiritual: 'bg-purple-500',
  finance:   'bg-emerald-500',
  health:    'bg-pink-500',
  reminder:  'bg-orange-400',
  research:  'bg-sky-500',
  personal:  'bg-yellow-500',
  creative:  'bg-red-500',
  business:  'bg-green-500',
  misc:      'bg-gray-400',
};

function fmtTime(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// ── main component ────────────────────────────────────────────────────────────

export function DashboardPage() {
  const PAGES_SIZE = 25;

  const [stats, setStats]               = useState<Stats | null>(null);
  const [pagedEntries, setPagedEntries] = useState<import('../lib/types').Page[]>([]);
  const [pagesTotal, setPagesTotal]     = useState(0);
  const [filter, setFilter]             = useState('');
  const [pagesCurrent, setPagesCurrent] = useState(0);
  const [messages, setMessages]         = useState<Message[]>([]);
  const [currentInput, setCurrentInput] = useState('');
  const [domain, setDomain]             = useState<Domain | ''>('');
  const [save, setSave]                 = useState(false);
  const [isStreaming, setIsStreaming]   = useState(false);
  const [loadingStats, setLoadingStats] = useState(true);
  const [loadingPages, setLoadingPages] = useState(true);
  const [error, setError]               = useState<string | null>(null);

  const inputRef  = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  useKeyboardShortcut('/', () => inputRef.current?.focus());

  useEffect(() => {
    if (threadRef.current)
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    fetchStats()
      .then(s => setStats(s))
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

  async function ask() {
    const q = currentInput.trim();
    if (!q || isStreaming) return;

    const userMsg: Message = {
      id: uid(), role: 'user', text: q,
      citations: [], phase: 'done', timestamp: fmtTime(new Date()),
    };
    const aId = uid();
    const assistantMsg: Message = {
      id: aId, role: 'assistant', text: '',
      citations: [], phase: 'streaming', timestamp: fmtTime(new Date()),
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setCurrentInput('');
    setIsStreaming(true);

    try {
      for await (const ev of streamQuery({ question: q, domain, top_k: 5, save })) {
        if (ev.type === 'token') {
          setMessages(prev => prev.map(m => m.id === aId ? { ...m, text: m.text + ev.text } : m));
        } else if (ev.type === 'done') {
          setMessages(prev => prev.map(m => m.id === aId ? { ...m, citations: ev.citations, phase: 'done' } : m));
        } else if (ev.type === 'error') {
          setMessages(prev => prev.map(m => m.id === aId ? { ...m, phase: 'error', error: ev.message } : m));
        }
      }
    } catch (e) {
      setMessages(prev => prev.map(m => m.id === aId ? { ...m, phase: 'error', error: String(e) } : m));
    } finally {
      setIsStreaming(false);
    }
  }

  const totalPages = Math.ceil(pagesTotal / PAGES_SIZE);

  return (
    /* -mx-4 cancels App's px-4; -my-4 + explicit height replaces py-4 */
    <div className="-mx-4 -my-4 flex h-[calc(100vh-3.5rem)] overflow-hidden
                    bg-white dark:bg-gray-950">

      {/* ── Left sidebar ── */}
      <aside className="w-52 shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-800
                        bg-gray-50 dark:bg-[#0d0d12] overflow-y-auto">

        {/* Domains section */}
        <div className="px-3 pt-4 pb-2">
          <div className="flex items-center justify-between mb-2 px-1">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
              Domains
            </span>
          </div>

          {loadingStats ? (
            <div className="py-2 flex justify-center"><LoadingSpinner size="sm" /></div>
          ) : stats ? (
            <ul className="space-y-0.5">
              {Object.entries(stats.domain_counts)
                .sort((a, b) => b[1] - a[1])
                .map(([dom, count]) => (
                  <li key={dom}>
                    <button
                      onClick={() => setDomain(domain === dom ? '' : dom as Domain)}
                      className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-sm
                                  transition-colors text-left
                                  ${domain === dom
                                    ? 'bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 font-medium'
                                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800/60'
                                  }`}
                    >
                      <span className={`w-2 h-2 rounded-full shrink-0 ${DOMAIN_DOT[dom] ?? 'bg-gray-400'}`} />
                      <span className="capitalize flex-1 text-xs">{dom}</span>
                      <span className="text-xs text-gray-400 dark:text-gray-500 tabular-nums">{count}</span>
                    </button>
                  </li>
                ))}
            </ul>
          ) : null}
        </div>

        {/* Divider */}
        <div className="mx-3 border-t border-gray-200 dark:border-gray-800 my-2" />

        {/* Heatmap tiles */}
        {stats && Object.keys(stats.domain_counts).length > 0 && (
          <div className="px-3 pb-3">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 block mb-2 px-1">
              Heatmap
            </span>
            <div className="grid grid-cols-2 gap-1.5">
              {Object.entries(stats.domain_counts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 8)
                .map(([dom, count]) => {
                  const cls = DOMAIN_CLASSES[dom] ?? DOMAIN_CLASSES['misc'];
                  return (
                    <Link
                      key={dom}
                      to={`/search?domain=${dom}`}
                      className={`rounded-xl p-2.5 flex flex-col gap-0.5 transition-opacity hover:opacity-90 ${cls}`}
                    >
                      <span className="text-[10px] font-semibold capitalize leading-none">{dom}</span>
                      <span className="text-lg font-bold leading-none">{count}</span>
                      <span className="text-[10px] opacity-60">pages</span>
                    </Link>
                  );
                })}
            </div>
          </div>
        )}

        {/* Stats footer */}
        {stats && (
          <div className="mt-auto px-3 pb-4 pt-2 border-t border-gray-200 dark:border-gray-800">
            <div className="grid grid-cols-3 gap-1">
              {([['Pages', stats.page_count], ['Sources', stats.source_count], ['Orphans', stats.orphan_count]] as const).map(
                ([label, val]) => (
                  <div key={label} className="rounded-lg bg-gray-100 dark:bg-gray-800/60 py-2 px-1 text-center">
                    <div className="text-sm font-bold text-gray-900 dark:text-gray-100 leading-none">{val}</div>
                    <div className="text-[10px] text-gray-400 mt-0.5">{label}</div>
                  </div>
                )
              )}
            </div>
          </div>
        )}
      </aside>

      {/* ── Center: chat ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        {/* Domain chips */}
        <div className="shrink-0 flex items-center gap-1.5 px-4 py-2 border-b border-gray-200 dark:border-gray-800
                        overflow-x-auto scrollbar-none bg-white dark:bg-gray-950">
          <button
            onClick={() => setDomain('')}
            className={`shrink-0 px-3 py-0.5 rounded-full text-xs font-medium border transition-all
              ${domain === ''
                ? 'bg-gray-800 dark:bg-white border-gray-800 dark:border-white text-white dark:text-gray-900'
                : 'border-gray-300 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-500 dark:hover:border-gray-500'
              }`}
          >
            all
          </button>
          {ALL_DOMAINS.map(d => (
            <button
              key={d}
              onClick={() => setDomain(domain === d ? '' : d)}
              className={`shrink-0 px-3 py-0.5 rounded-full text-xs font-medium border capitalize transition-all
                ${domain === d
                  ? `${DOMAIN_CLASSES[d] ?? ''} border-transparent`
                  : 'border-gray-300 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-400 dark:hover:border-gray-500'
                }`}
            >
              {d}
            </button>
          ))}
        </div>

        {/* Chat thread */}
        <div
          ref={threadRef}
          className="flex-1 overflow-y-auto px-4 py-6"
        >
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 select-none text-center">
              <div className="w-14 h-14 rounded-2xl bg-indigo-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
                {/* sparkle / star icon */}
                <svg className="w-7 h-7 text-white" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2l2.09 6.41L20.5 10l-6.41 2.09L12 18.5l-2.09-6.41L3.5 10l6.41-2.09L12 2z" />
                </svg>
              </div>
              <div>
                <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100">Ask your memory</h2>
                <p className="text-sm text-gray-400 dark:text-gray-500 mt-1.5 max-w-sm">
                  Ask questions about everything you've learned. I'll search your wiki and synthesize an answer.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center mt-2">
                {['What have I learned about AI?', 'Summarize my finance notes', 'What is stoic philosophy?'].map(s => (
                  <button
                    key={s}
                    onClick={() => setCurrentInput(s)}
                    className="px-3 py-1.5 rounded-full text-xs border border-gray-200 dark:border-gray-700
                               text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-600
                               dark:hover:border-indigo-500 dark:hover:text-indigo-400 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto space-y-8 pb-2">
              {messages.map(msg => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
            </div>
          )}
        </div>

        {/* Input bar */}
        <div className="shrink-0 border-t border-gray-200 dark:border-gray-800 px-4 py-3
                        bg-white dark:bg-gray-950">
          <div className="max-w-3xl mx-auto">
            <div className="flex items-center gap-2 rounded-2xl border border-gray-200 dark:border-gray-700
                            bg-gray-50 dark:bg-gray-900 px-4 py-2.5 shadow-sm
                            focus-within:border-indigo-400 dark:focus-within:border-indigo-500
                            focus-within:shadow-indigo-500/10 focus-within:shadow-md transition-all">

              {/* Attach */}
              <button
                className="p-1 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300
                           hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors shrink-0"
                title="Attach"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
              </button>

              {/* Ingest URL */}
              <Link
                to="/ingest"
                className="p-1 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300
                           hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors shrink-0"
                title="Ingest from URL"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253" />
                </svg>
              </Link>

              {/* Save toggle */}
              <button
                onClick={() => setSave(s => !s)}
                title={save ? 'Will save as wiki page' : 'Save as wiki page'}
                className={`p-1 rounded-lg transition-colors shrink-0
                  ${save
                    ? 'text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/50'
                    : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700'
                  }`}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M17 3H7a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2z" />
                </svg>
              </button>

              <input
                ref={inputRef}
                value={currentInput}
                onChange={e => setCurrentInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !e.shiftKey && ask()}
                placeholder="Ask anything… (press / to focus)"
                aria-label="Question"
                className="flex-1 bg-transparent text-sm text-gray-800 dark:text-gray-200
                           placeholder-gray-400 dark:placeholder-gray-500 outline-none caret-indigo-500"
              />

              {/* Domain indicator */}
              {domain && (
                <span className="shrink-0 text-xs text-indigo-500 dark:text-indigo-400 capitalize whitespace-nowrap">
                  in {domain}
                </span>
              )}

              {/* Send */}
              <button
                onClick={ask}
                disabled={isStreaming || !currentInput.trim()}
                className="shrink-0 w-8 h-8 rounded-full bg-indigo-600 hover:bg-indigo-700
                           disabled:opacity-40 disabled:cursor-not-allowed
                           flex items-center justify-center transition-colors shadow-sm"
                aria-label="Send"
              >
                <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Right panel: Wiki Pages ── */}
      <div className="w-72 shrink-0 flex flex-col border-l border-gray-200 dark:border-gray-800
                      bg-gray-50 dark:bg-[#0d0d12]">

        {/* Tabs */}
        <div className="flex shrink-0 border-b border-gray-200 dark:border-gray-800">
          <div className="flex-1 py-3 text-center text-sm font-semibold
                          text-indigo-600 dark:text-indigo-400
                          border-b-2 border-indigo-600 dark:border-indigo-400">
            Wiki Pages
          </div>
          <button className="flex-1 py-3 text-center text-sm font-medium
                             text-gray-400 hover:text-gray-600 dark:hover:text-gray-300
                             border-b-2 border-transparent transition-colors">
            Memory
          </button>
        </div>

        {/* Search */}
        <div className="shrink-0 px-3 py-2.5 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2 bg-white dark:bg-gray-800/60
                          rounded-lg border border-gray-200 dark:border-gray-700
                          px-3 py-1.5">
            <svg className="w-3.5 h-3.5 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <input
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Search wiki pages…"
              className="flex-1 bg-transparent text-xs text-gray-700 dark:text-gray-300
                         placeholder-gray-400 dark:placeholder-gray-500 outline-none"
            />
          </div>
        </div>

        {/* Pages list */}
        <div className="flex-1 overflow-y-auto">
          {loadingPages ? (
            <div className="py-8 flex justify-center"><LoadingSpinner size="sm" /></div>
          ) : pagedEntries.length === 0 ? (
            <div className="py-12 text-center text-xs text-gray-400 italic px-4">
              No pages yet — ingest some sources!
            </div>
          ) : (
            <>
              <div className="px-4 py-2.5">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
                  Recent · {pagesTotal}
                </span>
              </div>
              <div>
                {pagedEntries.map(p => (
                  <Link
                    key={p.title}
                    to={`/wiki/${titleToSlug(p.title)}`}
                    className="group flex items-center gap-2.5 px-4 py-2
                               hover:bg-gray-100 dark:hover:bg-gray-800/50 transition-colors"
                  >
                    <svg className="w-3.5 h-3.5 shrink-0 text-gray-300 dark:text-gray-600
                                    group-hover:text-gray-400 dark:group-hover:text-gray-500"
                      fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                    </svg>
                    <span
                      className="flex-1 min-w-0 text-xs text-gray-700 dark:text-gray-300
                                 group-hover:text-gray-900 dark:group-hover:text-gray-100 truncate"
                      title={p.title}
                    >
                      {p.title}
                    </span>
                    <DomainBadge domain={p.domain} />
                  </Link>
                ))}
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-between px-4 py-2.5
                                border-t border-gray-200 dark:border-gray-800 mt-1">
                  <button
                    onClick={() => setPagesCurrent(p => Math.max(0, p - 1))}
                    disabled={pagesCurrent === 0}
                    className="text-xs text-gray-500 disabled:opacity-30 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                  >
                    ‹ Prev
                  </button>
                  <span className="text-[10px] text-gray-400">
                    {pagesCurrent + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPagesCurrent(p => Math.min(totalPages - 1, p + 1))}
                    disabled={pagesCurrent >= totalPages - 1}
                    className="text-xs text-gray-500 disabled:opacity-30 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                  >
                    Next ›
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── ThinkingLoader ────────────────────────────────────────────────────────────

const THINKING_STATUSES = [
  'Searching memory…',
  'Reading wiki pages…',
  'Connecting ideas…',
  'Synthesizing answer…',
];

function ThinkingLoader() {
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setIdx(i => (i + 1) % THINKING_STATUSES.length), 1800);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="py-1 space-y-3">
      {/* Dots + cycling status */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="block w-2 h-2 rounded-full bg-indigo-500 animate-bounce"
              style={{ animationDelay: `${i * 0.15}s`, animationDuration: '0.9s' }}
            />
          ))}
        </div>
        <span
          key={idx}
          className="text-xs text-indigo-500 dark:text-indigo-400
                     animate-[fadeIn_0.4s_ease-in-out]"
        >
          {THINKING_STATUSES[idx]}
        </span>
      </div>

      {/* Shimmer skeleton lines */}
      <div className="space-y-2.5">
        {([88, 72, 60] as const).map((w, i) => (
          <div
            key={i}
            className="h-2 rounded-full animate-pulse
                       bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200
                       dark:from-gray-800 dark:via-gray-700 dark:to-gray-800"
            style={{ width: `${w}%`, animationDelay: `${i * 120}ms` }}
          />
        ))}
      </div>

      {/* Faint second paragraph skeleton */}
      <div className="space-y-2 pt-1 opacity-60">
        {([95, 80] as const).map((w, i) => (
          <div
            key={i}
            className="h-2 rounded-full animate-pulse
                       bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200
                       dark:from-gray-800 dark:via-gray-700 dark:to-gray-800"
            style={{ width: `${w}%`, animationDelay: `${(i + 3) * 120}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// ── MessageBubble ─────────────────────────────────────────────────────────────

function MessageBubble({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-gray-200 dark:bg-gray-700 shrink-0
                        flex items-center justify-center text-xs font-bold
                        text-gray-600 dark:text-gray-300">
          Y
        </div>
        <div className="flex-1 pt-0.5">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">You</span>
            <span className="text-xs text-gray-400">{message.timestamp}</span>
          </div>
          <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{message.text}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      {/* AI avatar */}
      <div className="w-7 h-7 rounded-full bg-indigo-600 shrink-0
                      flex items-center justify-center shadow shadow-indigo-500/20">
        <svg className="w-3.5 h-3.5 text-white" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2l2.09 6.41L20.5 10l-6.41 2.09L12 18.5l-2.09-6.41L3.5 10l6.41-2.09L12 2z" />
        </svg>
      </div>

      <div className="flex-1 min-w-0 pt-0.5">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">MyMem AI</span>
          <span className="text-xs text-gray-400">{message.timestamp}</span>
        </div>

        {message.phase === 'streaming' && message.text === '' && (
          <ThinkingLoader />
        )}

        {message.phase === 'streaming' && message.text !== '' && (
          <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed">
            {message.text}
            <span className="inline-block w-1.5 h-3.5 bg-indigo-500 ml-0.5 align-middle animate-pulse rounded-sm" />
          </p>
        )}

        {message.phase === 'done' && (
          <div>
            <div
              className="prose dark:prose-invert prose-sm max-w-none
                         prose-p:text-gray-700 dark:prose-p:text-gray-300 prose-p:leading-relaxed
                         prose-headings:text-gray-900 dark:prose-headings:text-gray-100 prose-headings:font-semibold
                         prose-h2:text-base prose-h3:text-sm
                         prose-a:text-indigo-600 dark:prose-a:text-indigo-400 prose-a:no-underline hover:prose-a:underline
                         prose-strong:text-gray-900 dark:prose-strong:text-gray-100
                         prose-code:text-indigo-700 dark:prose-code:text-indigo-300
                         prose-code:bg-indigo-50 dark:prose-code:bg-indigo-950/40
                         prose-code:rounded prose-code:px-1 prose-code:text-[0.8em] prose-code:font-normal
                         prose-pre:bg-gray-950 prose-pre:border prose-pre:border-gray-800 prose-pre:rounded-xl
                         prose-li:text-gray-700 dark:prose-li:text-gray-300
                         prose-li:marker:text-indigo-400"
              dangerouslySetInnerHTML={{ __html: marked.parse(message.text) as string }}
            />

            {/* Sources */}
            {message.citations.length > 0 && (
              <div className="mt-3">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">
                  Sources
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {message.citations.map(c => <CitationChip key={c} title={c} />)}
                </div>
              </div>
            )}

            {/* Related wiki page cards */}
            {message.citations.length > 0 && (
              <div className="mt-4">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
                  Related Wiki Pages
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {message.citations.slice(0, 3).map(title => {
                    const slug = titleToSlug(title);
                    return (
                      <Link
                        key={slug}
                        to={`/wiki/${slug}`}
                        className="group block rounded-xl border border-gray-200 dark:border-gray-800
                                   bg-white dark:bg-gray-900/60 p-3
                                   hover:border-indigo-300 dark:hover:border-indigo-700
                                   hover:shadow-sm transition-all"
                      >
                        <div className="flex items-start gap-2">
                          <svg className="w-3.5 h-3.5 shrink-0 text-gray-400 mt-0.5"
                            fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                              d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                          </svg>
                          <span className="text-xs font-medium text-gray-700 dark:text-gray-300
                                          group-hover:text-indigo-600 dark:group-hover:text-indigo-400
                                          leading-snug line-clamp-2">
                            {title}
                          </span>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {message.phase === 'error' && (
          <p className="text-sm text-red-500 dark:text-red-400">{message.error}</p>
        )}
      </div>
    </div>
  );
}
