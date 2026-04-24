import { Link } from 'react-router-dom';
import type { RelatedConcept } from '../lib/types';
import { titleToSlug } from '../lib/api';

interface TocEntry {
  level: number;
  text: string;
  id: string;
}

interface Backlink {
  slug: string;
  title: string;
}

interface WikiSidePaneProps {
  toc: TocEntry[];
  activeId: string;
  backlinks: Backlink[];
  related: RelatedConcept[];
  pageTitle: string;
}

export function WikiSidePane({ toc, activeId, backlinks, related, pageTitle }: WikiSidePaneProps) {
  const concepts = related.filter(r => r.slug !== titleToSlug(pageTitle));

  return (
    <aside className="hidden lg:flex flex-col gap-6 sticky top-20 self-start max-h-[calc(100vh-6rem)] overflow-y-auto">

      {/* TOC */}
      <nav aria-label="Table of contents"
        className="bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl p-4"
      >
        <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-3">
          Contents
        </p>
        {toc.length === 0 ? (
          <p className="text-xs text-gray-400 dark:text-gray-600 italic">No headings</p>
        ) : (
          <ul className="space-y-0.5">
            {toc.map(h => {
              const isActive = activeId === h.id;
              const indent = (h.level - 1) * 12;
              return (
                <li key={h.id}>
                  <a
                    href={`#${h.id}`}
                    style={{ paddingLeft: `${indent + 8}px` }}
                    className={`block text-xs py-1 pr-2 rounded transition-colors truncate outline-none
                      focus-visible:ring-2 focus-visible:ring-indigo-400 border-l-2
                      ${isActive
                        ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400 font-semibold bg-indigo-50 dark:bg-indigo-950/40'
                        : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600'
                      }`}
                  >
                    {h.text}
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      {/* Backlinks */}
      <div className="bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-3">
          Backlinks
        </p>
        {backlinks.length > 0 ? (
          <ul className="space-y-2">
            {backlinks.map(b => (
              <li key={b.slug}>
                <Link
                  to={`/wiki/${b.slug}`}
                  className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800
                             dark:text-indigo-400 dark:hover:text-indigo-300
                             transition-colors outline-none
                             focus-visible:ring-2 focus-visible:ring-indigo-400 rounded truncate"
                >
                  <span className="text-gray-400">←</span>
                  <span className="truncate">{b.title}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-gray-400 dark:text-gray-600 italic">No pages link here yet</p>
        )}
      </div>

      {/* Related Concepts */}
      {concepts.length > 0 && (
        <div className="bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl p-4">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-3">
            Related Concepts
          </p>
          <ul className="space-y-2">
            {concepts.map(r => (
              <li key={r.slug}>
                <Link
                  to={`/wiki/${r.slug}`}
                  className={`text-xs truncate block transition-colors hover:underline outline-none
                    focus-visible:ring-2 focus-visible:ring-indigo-400 rounded
                    ${r.internal
                      ? 'text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300'
                      : 'text-amber-600 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300'
                    }`}
                  title={r.internal ? undefined : 'Not in wiki yet'}
                >
                  {r.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
