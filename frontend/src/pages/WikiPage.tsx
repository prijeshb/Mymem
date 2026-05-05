import { useEffect, useRef, useState } from 'react';
import { useMatch, useNavigate } from 'react-router-dom';
import { marked } from 'marked';
import { archivePage, deletePage, fetchPage, fetchPages, patchPage, restorePage, streamRelatedWeb, titleToSlug } from '../lib/api';
import type { Domain, RelatedConcept, WikiPageData } from '../lib/types';
import { ALL_DOMAINS } from '../lib/types';
import { DomainBadge } from '../components/DomainBadge';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { WikiSidePane } from '../components/WikiSidePane';
import { Button, Input } from '@heroui/react';

function renderBody(body: string): string {
  const withLinks = body.replace(/\[\[([^\]]+)\]\]/g, (_, title) => {
    const slug = titleToSlug(title);
    return `<a class="wikilink" href="/wiki/${slug}" data-title="${title}">${title}</a>`;
  });
  return marked.parse(withLinks) as string;
}

export function WikiPage() {
  const match    = useMatch('/wiki/*');
  const slug     = match?.params['*'] ?? '';
  const navigate = useNavigate();

  const [page, setPage]             = useState<WikiPageData | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [activeId, setActiveId]     = useState('');
  const articleRef = useRef<HTMLElement>(null);

  const [related, setRelated]        = useState<RelatedConcept[]>([]);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [popover, setPopover]        = useState<{ slug: string; title: string; x: number; y: number } | null>(null);

  const [editing, setEditing]       = useState(false);
  const [editTags, setEditTags]     = useState('');
  const [editDomain, setEditDomain] = useState<Domain>('misc');
  const [saving, setSaving]         = useState(false);
  const [saveError, setSaveError]   = useState<string | null>(null);

  const [menuOpen, setMenuOpen]           = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting]           = useState(false);
  const [archiving, setArchiving]         = useState(false);
  const [actionError, setActionError]     = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    setError(null);
    setRelated([]);
    fetchPage(slug)
      .then(p => {
        setPage(p);
        // Always include page title as first concept; append wikilinks after (deduped)
        const titleSlug = titleToSlug(p.title);
        const wikiItems = (p.related ?? []).filter(r => r.slug !== titleSlug);
        const seed = [
          { title: p.title, slug: titleSlug, internal: true, web_links: [] },
          ...wikiItems.map(r => ({ ...r, web_links: [] })),
        ];
        setRelated(seed);
        setRelatedLoading(true);
        (async () => {
          try {
            for await (const ev of streamRelatedWeb(seed.map(r => r.title), slug)) {
              if ('done' in ev) break;
              setRelated(prev => prev.map(r =>
                r.slug === ev.slug ? { ...r, web_links: ev.web_links } : r,
              ));
            }
          } finally {
            setRelatedLoading(false);
          }
        })();
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    if (!popover) return;
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('.wikilink-popover, a.wikilink.broken'))
        setPopover(null);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [popover]);

  useEffect(() => {
    if (!articleRef.current || !page) return;
    const headings = articleRef.current.querySelectorAll('h1,h2,h3');
    if (!headings.length) return;

    const obs = new IntersectionObserver(
      entries => {
        const visible = entries.filter(e => e.isIntersecting);
        if (visible.length) setActiveId(visible[0].target.id);
      },
      { rootMargin: '-20% 0px -70% 0px' },
    );
    headings.forEach(h => { if (h.id) obs.observe(h); });
    return () => obs.disconnect();
  }, [page]);

  useEffect(() => {
    if (!articleRef.current) return;
    fetchPages().then(pages => {
      const slugSet = new Set(pages.map(p => titleToSlug(p.title)));
      articleRef.current?.querySelectorAll('a.wikilink').forEach(a => {
        const href = a.getAttribute('href')?.replace('/wiki/', '') ?? '';
        if (!slugSet.has(href)) {
          a.classList.add('broken');
          a.removeAttribute('href');
          a.setAttribute('role', 'button');
          a.setAttribute('tabindex', '0');
        }
      });
    }).catch(() => { /* non-fatal */ });
  }, [page]);

  function startEdit() {
    if (!page) return;
    setEditTags(page.tags.join(', '));
    setEditDomain(page.domain);
    setSaveError(null);
    setEditing(true);
  }

  async function saveEdit() {
    if (!page || !slug) return;
    setSaving(true);
    setSaveError(null);
    try {
      const tags = editTags.split(',').map(t => t.trim()).filter(Boolean);
      const result = await patchPage(slug, tags, editDomain);
      setPage({ ...page, tags: result.tags, domain: result.domain as Domain });
      setEditing(false);
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  async function handleArchive() {
    if (!slug) return;
    setArchiving(true);
    setActionError(null);
    setMenuOpen(false);
    try {
      await archivePage(slug);
      setPage(p => p ? { ...p, archived: true } : p);
    } catch (e) {
      setActionError(String(e));
    } finally {
      setArchiving(false);
    }
  }

  async function handleRestore() {
    if (!slug) return;
    setArchiving(true);
    setActionError(null);
    setMenuOpen(false);
    try {
      await restorePage(slug);
      setPage(p => p ? { ...p, archived: false } : p);
    } catch (e) {
      setActionError(String(e));
    } finally {
      setArchiving(false);
    }
  }

  async function handleDelete() {
    if (!slug) return;
    setDeleting(true);
    setActionError(null);
    try {
      await deletePage(slug);
      navigate('/');
    } catch (e) {
      setActionError(String(e));
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (loading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>;
  if (error)   return <div className="max-w-2xl mx-auto py-8"><ErrorBanner message={error} /></div>;
  if (!page)   return null;

  const allWebLinks = related.flatMap(r => r.web_links);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-8">

      <WikiSidePane
        toc={page.toc}
        activeId={activeId}
        backlinks={page.backlinks}
        related={related}
        pageTitle={page.title}
      />

      {/* ── Main content: Article + Related Web Articles ── */}
      <div className="min-w-0">
        {/* Page header */}
        <div className="mb-6 pb-4 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-start gap-3 mb-3 flex-wrap">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 leading-tight flex-1">
              {page.title}
            </h1>
            {page.archived && (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium
                               bg-amber-100 text-amber-700
                               dark:bg-amber-900/40 dark:text-amber-400">
                Archived
              </span>
            )}
            <DomainBadge domain={page.domain} />

            {/* ⋮ Actions dropdown */}
            <div ref={menuRef} className="relative">
              <button
                onClick={() => { setMenuOpen(o => !o); setConfirmDelete(false); setActionError(null); }}
                disabled={archiving || deleting}
                aria-label="Page actions"
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 dark:hover:text-gray-300
                           hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors disabled:opacity-40"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                  <circle cx="8" cy="2.5" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13.5" r="1.5"/>
                </svg>
              </button>

              {menuOpen && !confirmDelete && (
                <div className="absolute right-0 top-9 z-50 w-44
                                bg-white dark:bg-gray-900
                                border border-gray-200 dark:border-gray-700
                                rounded-xl shadow-lg py-1 text-sm">
                  {page.archived ? (
                    <>
                      <button
                        onClick={handleRestore}
                        className="w-full text-left px-4 py-2
                                   text-gray-700 dark:text-gray-300
                                   hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                      >
                        Restore
                      </button>
                      <button
                        onClick={() => { setMenuOpen(false); setConfirmDelete(true); }}
                        className="w-full text-left px-4 py-2
                                   text-red-600 dark:text-red-400
                                   hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                      >
                        Delete permanently
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={handleArchive}
                        className="w-full text-left px-4 py-2
                                   text-gray-700 dark:text-gray-300
                                   hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                      >
                        Archive
                      </button>
                      <button
                        onClick={() => { setMenuOpen(false); setConfirmDelete(true); }}
                        className="w-full text-left px-4 py-2
                                   text-red-600 dark:text-red-400
                                   hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {editing ? (
            <div className="space-y-3">
              <div className="flex gap-2 flex-wrap items-end">
                <div className="flex-1 min-w-48">
                  <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                    Tags (comma separated)
                  </label>
                  <Input
                    value={editTags}
                    onChange={e => setEditTags(e.target.value)}
                    placeholder="ml, python, tutorial"
                    fullWidth
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                    Domain
                  </label>
                  <select
                    value={editDomain}
                    onChange={e => setEditDomain(e.target.value as Domain)}
                    className="bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600
                               rounded-lg px-3 py-1.5 text-sm text-gray-900 dark:text-gray-100
                               focus:outline-none focus:ring-2 focus:ring-indigo-400
                               [color-scheme:light] dark:[color-scheme:dark]"
                  >
                    {ALL_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                </div>
              </div>
              {saveError && <p className="text-xs text-red-500">{saveError}</p>}
              <div className="flex gap-2">
                <Button variant="primary" size="sm" onPress={saveEdit} isDisabled={saving}>
                  {saving ? 'Saving…' : 'Save'}
                </Button>
                <Button variant="secondary" size="sm" onPress={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5 items-center">
              {page.tags.map(t => (
                <span
                  key={t}
                  className="px-2 py-0.5 rounded-full text-xs font-medium
                             bg-gray-100 text-gray-600
                             dark:bg-gray-800 dark:text-gray-300"
                >
                  #{t}
                </span>
              ))}
              <Button
                variant="outline"
                size="sm"
                onPress={startEdit}
                aria-label="Edit tags and domain"
                className="border-dashed text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              >
                + edit
              </Button>
            </div>
          )}

          <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
            Created {page.created} · Updated {page.updated}
            {page.sources.length > 0 && ` · Sources: ${page.sources.join(', ')}`}
          </p>

          {actionError && <p className="text-xs text-red-500 mt-1">{actionError}</p>}
          {confirmDelete && (
            <div className="flex items-center gap-2 mt-2">
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {page.archived ? 'Delete permanently?' : 'Delete this page?'}
              </span>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-3 py-1 text-xs font-medium rounded-lg
                           bg-red-600 hover:bg-red-700 text-white
                           disabled:opacity-50 transition-colors"
              >
                {deleting ? 'Deleting…' : 'Confirm'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                disabled={deleting}
                className="px-3 py-1 text-xs font-medium rounded-lg
                           bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300
                           hover:bg-gray-200 dark:hover:bg-gray-700
                           disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>

        {/* Article body */}
        <div className="relative">
          <article
            ref={articleRef}
            className="prose dark:prose-invert prose-sm max-w-none
                       prose-a:text-blue-600 dark:prose-a:text-indigo-400
                       dark:prose-pre:bg-gray-900 dark:prose-pre:border dark:prose-pre:border-gray-700"
            dangerouslySetInnerHTML={{ __html: renderBody(page.body) }}
            onClick={e => {
              const a = (e.target as HTMLElement).closest('a.wikilink.broken');
              if (!a) { setPopover(null); return; }
              e.preventDefault();
              const title = a.getAttribute('data-title') ?? a.textContent ?? '';
              const slug  = titleToSlug(title);
              const rect  = a.getBoundingClientRect();
              const scrollY = window.scrollY;
              setPopover(prev =>
                prev?.slug === slug ? null : { slug, title, x: rect.left, y: rect.bottom + scrollY + 6 }
              );
            }}
          />

          {/* Broken-wikilink popover */}
          {popover && (() => {
            const concept = related.find(r => r.slug === popover.slug);
            return (
              <div
                style={{ top: popover.y, left: popover.x }}
                className="wikilink-popover absolute z-50 w-72 bg-white dark:bg-gray-900
                           border border-gray-200 dark:border-gray-700
                           rounded-xl shadow-lg p-3"
              >
                <p className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
                  {popover.title}
                </p>
                {!concept || concept.web_links.length === 0 ? (
                  <div className="space-y-2">
                    {[1, 2, 3].map(n => (
                      <div key={n} className="animate-pulse space-y-1">
                        <div className="h-2.5 bg-gray-200 dark:bg-gray-700 rounded w-4/5" />
                        <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded w-full" />
                      </div>
                    ))}
                  </div>
                ) : (
                  <ul className="space-y-2">
                    {concept.web_links.map(lnk => (
                      <li key={lnk.url}>
                        <a
                          href={lnk.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="group block"
                          onClick={e => e.stopPropagation()}
                        >
                          <span className="text-[11px] font-medium text-blue-600 dark:text-blue-400
                                          group-hover:underline line-clamp-1 block">
                            {lnk.label}
                          </span>
                          {lnk.snippet && (
                            <span className="text-[10px] text-gray-500 dark:text-gray-500
                                            line-clamp-2 mt-0.5 block leading-snug">
                              {lnk.snippet}
                            </span>
                          )}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
                <button
                  onClick={() => setPopover(null)}
                  className="absolute top-2 right-2 text-gray-400 hover:text-gray-600
                             dark:hover:text-gray-300 text-xs"
                >
                  ✕
                </button>
              </div>
            );
          })()}
        </div>

        {/* ── Related Web Articles subsection ── */}
        <section className="mt-10 pt-6 border-t border-gray-200 dark:border-gray-700">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4 flex items-center gap-2">
            Related Web Articles
            {relatedLoading && (
              <span className="inline-block w-2 h-2 rounded-full bg-sky-400 animate-pulse" />
            )}
          </h2>

          {relatedLoading && allWebLinks.length === 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {[1, 2, 3].map(n => (
                <div key={n} className="animate-pulse rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-2">
                  <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-4/5" />
                  <div className="h-2.5 bg-gray-100 dark:bg-gray-800 rounded w-full" />
                  <div className="h-2.5 bg-gray-100 dark:bg-gray-800 rounded w-3/4" />
                </div>
              ))}
            </div>
          ) : allWebLinks.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {allWebLinks.map(lnk => (
                <a
                  key={lnk.url}
                  href={lnk.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group block rounded-xl border border-gray-200 dark:border-gray-700
                             bg-white dark:bg-gray-800/40 p-4
                             hover:border-blue-400 dark:hover:border-blue-500
                             hover:shadow-sm transition-all"
                >
                  <p className="text-sm font-medium text-blue-600 dark:text-blue-400
                                group-hover:underline line-clamp-2 leading-snug mb-1">
                    {lnk.label}
                  </p>
                  {lnk.snippet && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 line-clamp-3 leading-relaxed">
                      {lnk.snippet}
                    </p>
                  )}
                  {lnk.source && (
                    <span className="inline-block mt-2 text-[10px] font-medium
                                     text-gray-400 dark:text-gray-500 uppercase tracking-wide">
                      {lnk.source}
                    </span>
                  )}
                </a>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400 dark:text-gray-600 italic">No web articles found.</p>
          )}
        </section>
      </div>
    </div>
  );
}
