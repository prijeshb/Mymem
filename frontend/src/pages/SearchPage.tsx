import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { marked } from 'marked';
import { fetchPagesPaged, streamQuery, titleToSlug } from '../lib/api';
import type { Domain, Page } from '../lib/types';
import { ALL_DOMAINS } from '../lib/types';
import { DomainBadge } from '../components/DomainBadge';
import { CitationChip } from '../components/CitationChip';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { useKeyboardShortcut } from '../lib/useKeyboardShortcut';
import { Button, Card, Input } from '@heroui/react';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  citations: string[];
  phase: 'done' | 'streaming' | 'error';
}

export function SearchPage() {
  const [searchParams] = useSearchParams();
  const initDomain = (searchParams.get('domain') ?? '') as Domain | '';

  const PAGE_SIZE = 10;

  const [pagedPages, setPagedPages]   = useState<Page[]>([]);
  const [pagesTotal, setPagesTotal]   = useState(0);
  const [pageFilter, setPageFilter]   = useState('');
  const [currentPage, setCurrentPage] = useState(0);
  const [domain, setDomain]           = useState<Domain | ''>(initDomain);
  const [save, setSave]               = useState(false);
  const [question, setQuestion]       = useState('');
  const [messages, setMessages]       = useState<Message[]>([]);
  const [loading, setLoading]         = useState(true);
  const inputRef  = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const totalPages = Math.ceil(pagesTotal / PAGE_SIZE);

  useKeyboardShortcut('/', () => inputRef.current?.focus());

  useEffect(() => {
    setLoading(true);
    fetchPagesPaged(currentPage, PAGE_SIZE, domain || undefined, pageFilter || undefined)
      .then(r => { setPagedPages(r.items); setPagesTotal(r.total); })
      .finally(() => setLoading(false));
  }, [currentPage, domain, pageFilter]);

  useEffect(() => { setCurrentPage(0); }, [pageFilter, domain]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function submit() {
    const q = question.trim();
    if (!q) return;
    setQuestion('');

    const userMsg: Message = { role: 'user', text: q, citations: [], phase: 'done' };
    const assistantMsg: Message = { role: 'assistant', text: '', citations: [], phase: 'streaming' };
    setMessages(prev => [...prev, userMsg, assistantMsg]);

    try {
      for await (const ev of streamQuery({ question: q, domain, top_k: 5, save })) {
        if (ev.type === 'token') {
          setMessages(prev => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, text: last.text + ev.text };
            return next;
          });
        } else if (ev.type === 'done') {
          setMessages(prev => {
            const next = [...prev];
            next[next.length - 1] = { ...next[next.length - 1], citations: ev.citations, phase: 'done' };
            return next;
          });
        } else if (ev.type === 'error') {
          setMessages(prev => {
            const next = [...prev];
            next[next.length - 1] = { ...next[next.length - 1], text: ev.message, phase: 'error' };
            return next;
          });
        }
      }
    } catch (e) {
      setMessages(prev => {
        const next = [...prev];
        next[next.length - 1] = { ...next[next.length - 1], text: String(e), phase: 'error' };
        return next;
      });
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-6 h-[calc(100vh-8rem)]">
      {/* ── Chat panel ── */}
      <Card className="flex flex-col overflow-hidden">
        {/* Controls */}
        <div className="px-4 py-3 border-b border-gray-800 flex items-center gap-3 flex-wrap">
          <select
            value={domain}
            onChange={e => setDomain(e.target.value as Domain | '')}
            aria-label="Domain filter"
            className="bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5 text-xs text-gray-100
                       focus:outline-hidden focus:ring-2 focus:ring-indigo-400 [color-scheme:dark]"
          >
            <option value="">All domains</option>
            {ALL_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer ml-auto">
            <input
              type="checkbox"
              checked={save}
              onChange={e => setSave(e.target.checked)}
              className="rounded border-gray-600 bg-gray-800 focus:ring-indigo-400"
            />
            Save answers
          </label>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-gray-600 text-sm">
              Ask anything — answered from your wiki
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-xl px-4 py-3 text-sm ${
                msg.role === 'user'
                  ? 'bg-indigo-600 text-white rounded-br-sm'
                  : 'bg-gray-800 text-gray-200 rounded-bl-sm'
              }`}>
                {msg.role === 'user' ? (
                  <p>{msg.text}</p>
                ) : msg.phase === 'streaming' ? (
                  <p className="whitespace-pre-wrap">
                    {msg.text}
                    <span className="typing-dot ml-1">●</span>
                  </p>
                ) : msg.phase === 'error' ? (
                  <p className="text-red-400">{msg.text}</p>
                ) : (
                  <>
                    <div
                      className="prose dark:prose-invert prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: marked.parse(msg.text) as string }}
                    />
                    {msg.citations.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {msg.citations.map(c => <CitationChip key={c} title={c} />)}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-4 py-3 border-t border-gray-800 flex gap-2">
          <Input
            ref={inputRef}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && submit()}
            placeholder="Ask a question… (press / to focus)"
            aria-label="Question"
            fullWidth
          />
          <Button
            variant="primary"
            size="sm"
            onPress={submit}
            aria-label="Send question"
          >
            Ask
          </Button>
        </div>
      </Card>

      {/* ── Page browser sidebar ── */}
      <div className="flex flex-col gap-3 min-h-0">
          <Input
            value={pageFilter}
            onChange={e => setPageFilter(e.target.value)}
            placeholder="Search pages…"
            aria-label="Filter pages"
            fullWidth
          />

          {loading ? <LoadingSpinner size="sm" /> : (
            <>
              {/* Table */}
              <div className="overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="bg-gray-100 dark:bg-gray-800 sticky top-0 z-10">
                      <th className="text-left px-3 py-2 font-semibold text-gray-500 dark:text-gray-400 w-full">Title</th>
                      <th className="text-left px-2 py-2 font-semibold text-gray-500 dark:text-gray-400 whitespace-nowrap">Domain</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pagedPages.length === 0 ? (
                      <tr>
                        <td colSpan={2} className="text-center py-6 text-gray-500 italic">No pages found</td>
                      </tr>
                    ) : pagedPages.map((p, i) => (
                      <tr
                        key={p.title}
                        className={`border-t border-gray-800 dark:border-gray-700 transition-colors
                          ${i % 2 === 0
                            ? 'bg-transparent'
                            : 'bg-gray-900/30 dark:bg-gray-800/20'
                          }
                          hover:bg-indigo-50/60 dark:hover:bg-indigo-950/30`}
                      >
                        <td className="px-3 py-2 max-w-0">
                          <Link
                            to={`/wiki/${titleToSlug(p.title)}`}
                            className="block truncate font-medium text-gray-800 dark:text-gray-200
                                       hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors
                                       outline-none focus-visible:underline"
                            title={p.title}
                          >
                            {p.title}
                          </Link>
                          {p.summary && (
                            <p className="truncate text-gray-500 dark:text-gray-500 mt-0.5">{p.summary}</p>
                          )}
                        </td>
                        <td className="px-2 py-2 whitespace-nowrap">
                          <DomainBadge domain={p.domain} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {pagesTotal > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-gray-500 dark:text-gray-500">
                    {currentPage * PAGE_SIZE + 1}–{Math.min((currentPage + 1) * PAGE_SIZE, pagesTotal)} of {pagesTotal}
                  </span>
                  <div className="flex gap-1">
                    <button
                      onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                      disabled={currentPage === 0}
                      className="px-2 py-1 text-[11px] rounded border border-gray-300 dark:border-gray-600
                                 text-gray-500 dark:text-gray-400 disabled:opacity-30
                                 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    >
                      ‹ Prev
                    </button>
                    <button
                      onClick={() => setCurrentPage(p => Math.min(totalPages - 1, p + 1))}
                      disabled={currentPage >= totalPages - 1}
                      className="px-2 py-1 text-[11px] rounded border border-gray-300 dark:border-gray-600
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
      </div>
    </div>
  );
}
