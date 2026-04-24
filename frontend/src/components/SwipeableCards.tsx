import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router-dom';

export interface CardData {
  id: string;
  title: string;
  subtitle?: string;
  body: string;
  meta?: string;
  slug?: string;
  domain?: string;
}

interface Props {
  cards: CardData[];
}

const DOMAIN_COLORS: Record<string, { dark: string; light: string }> = {
  tech:      { dark: 'from-indigo-900 to-indigo-950',   light: 'from-indigo-50 to-indigo-100' },
  spiritual: { dark: 'from-purple-900 to-purple-950',   light: 'from-purple-50 to-purple-100' },
  finance:   { dark: 'from-emerald-900 to-emerald-950', light: 'from-emerald-50 to-emerald-100' },
  health:    { dark: 'from-teal-900 to-teal-950',       light: 'from-teal-50 to-teal-100' },
  reminder:  { dark: 'from-amber-900 to-amber-950',     light: 'from-amber-50 to-amber-100' },
  research:  { dark: 'from-sky-900 to-sky-950',         light: 'from-sky-50 to-sky-100' },
  personal:  { dark: 'from-rose-900 to-rose-950',       light: 'from-rose-50 to-rose-100' },
  creative:  { dark: 'from-fuchsia-900 to-fuchsia-950', light: 'from-fuchsia-50 to-fuchsia-100' },
  business:  { dark: 'from-orange-900 to-orange-950',   light: 'from-orange-50 to-orange-100' },
  misc:      { dark: 'from-gray-800 to-gray-900',       light: 'from-gray-100 to-gray-200' },
};

const DOMAIN_BORDER: Record<string, { dark: string; light: string }> = {
  tech:      { dark: 'border-indigo-800',   light: 'border-indigo-200' },
  spiritual: { dark: 'border-purple-800',   light: 'border-purple-200' },
  finance:   { dark: 'border-emerald-800',  light: 'border-emerald-200' },
  health:    { dark: 'border-teal-800',     light: 'border-teal-200' },
  reminder:  { dark: 'border-amber-800',    light: 'border-amber-200' },
  research:  { dark: 'border-sky-800',      light: 'border-sky-200' },
  personal:  { dark: 'border-rose-800',     light: 'border-rose-200' },
  creative:  { dark: 'border-fuchsia-800',  light: 'border-fuchsia-200' },
  business:  { dark: 'border-orange-800',   light: 'border-orange-200' },
  misc:      { dark: 'border-gray-700',     light: 'border-gray-300' },
};

function subscribe(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  return () => obs.disconnect();
}

function useDark() {
  return useSyncExternalStore(
    subscribe,
    () => document.documentElement.classList.contains('dark'),
    () => false,
  );
}

function domainGradient(domain?: string, dark = true) {
  const key = domain ?? 'misc';
  const colors = DOMAIN_COLORS[key] ?? DOMAIN_COLORS.misc;
  return dark ? colors.dark : colors.light;
}

function domainBorder(domain?: string, dark = true) {
  const key = domain ?? 'misc';
  const borders = DOMAIN_BORDER[key] ?? DOMAIN_BORDER.misc;
  return dark ? borders.dark : borders.light;
}

export function SwipeableCards({ cards }: Props) {
  const [current, setCurrent]     = useState(0);
  const [offset, setOffset]       = useState(0);
  const [dragging, setDragging]   = useState(false);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const startX  = useRef(0);
  const navigate = useNavigate();
  const dark     = useDark();

  useEffect(() => { setCurrent(0); setDismissed(new Set()); }, [cards]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'ArrowRight') next();
      if (e.key === 'ArrowLeft')  prev();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (cards.length === 0) return null;

  const total      = cards.length;
  const visibleIdx = (() => {
    const a = cards.findIndex((_, i) => !dismissed.has(i) && i >= current);
    return a !== -1 ? a : cards.findIndex((_, i) => !dismissed.has(i));
  })();

  function onPointerDown(e: React.PointerEvent) {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    startX.current = e.clientX;
    setDragging(true);
    setOffset(0);
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!dragging) return;
    setOffset(e.clientX - startX.current);
  }
  function onPointerUp() {
    if (!dragging) return;
    setDragging(false);
    if (offset < -80) swipeLeft();
    else if (offset > 80) swipeRight();
    setOffset(0);
  }

  function next() {
    setCurrent(c => {
      for (let i = c + 1; i < total; i++) if (!dismissed.has(i)) return i;
      return c;
    });
  }
  function prev() {
    if (current <= 0) return;
    const newIdx = current - 1;
    setDismissed(d => { const s = new Set(d); s.delete(newIdx); return s; });
    setCurrent(newIdx);
  }
  function swipeLeft()  { setDismissed(s => new Set(s).add(visibleIdx)); next(); }
  function swipeRight() { prev(); }

  const stackIndices: number[] = [];
  let found = 0;
  for (let i = Math.max(0, visibleIdx); i < total && found < 3; i++) {
    if (!dismissed.has(i)) { stackIndices.push(i); found++; }
  }

  const remainingCount = total - dismissed.size;

  return (
    <div className="select-none">
      {/* Progress dots */}
      <div className="flex items-center gap-1.5 mb-4">
        {cards.map((_, i) => (
          <div
            key={i}
            className={`h-1 rounded-full transition-all duration-300 ${
              dismissed.has(i)
                ? dark ? 'w-2 bg-gray-700' : 'w-2 bg-gray-300'
                : i === visibleIdx
                ? 'w-6 bg-indigo-500'
                : dark ? 'w-2 bg-gray-600' : 'w-2 bg-gray-400'
            }`}
          />
        ))}
        <span className="ml-auto text-xs text-gray-500">{remainingCount} left</span>
      </div>

      {/* Card stack */}
      <div className="relative h-64 mb-4" style={{ perspective: '800px' }}>
        {stackIndices.length === 0 ? (
          <div className={`flex items-center justify-center h-full rounded-2xl border border-dashed
                          ${dark ? 'border-gray-700' : 'border-gray-300'}`}>
            <p className="text-sm text-gray-500">All caught up!</p>
          </div>
        ) : (
          stackIndices.slice().reverse().map((cardIdx, stackPos) => {
            const card   = cards[cardIdx];
            const isTop  = stackPos === stackIndices.length - 1;
            const depth  = stackIndices.length - 1 - stackPos;
            const scale      = 1 - depth * 0.04;
            const translateY = depth * 10;
            const rotate     = isTop && dragging ? offset * 0.08 : 0;
            const tx         = isTop ? offset : 0;

            return (
              <div
                key={cardIdx}
                className={`absolute inset-0 rounded-2xl bg-gradient-to-br
                            ${domainGradient(card.domain, dark)}
                            border ${domainBorder(card.domain, dark)}
                            p-5 flex flex-col
                            ${isTop ? 'cursor-grab active:cursor-grabbing shadow-lg' : 'pointer-events-none'}`}
                style={{
                  transform: `translateX(${tx}px) translateY(${translateY}px) scale(${scale}) rotate(${rotate}deg)`,
                  transition: dragging && isTop ? 'none' : 'transform 0.3s ease',
                  zIndex: stackIndices.length - depth,
                  opacity: 1 - depth * 0.1,
                }}
                onPointerDown={isTop ? onPointerDown : undefined}
                onPointerMove={isTop ? onPointerMove : undefined}
                onPointerUp={isTop ? onPointerUp : undefined}
                onPointerCancel={isTop ? onPointerUp : undefined}
              >
                {/* Domain tag */}
                {card.domain && (
                  <span className={`self-start px-2 py-0.5 rounded-full text-xs mb-2
                                   ${dark ? 'bg-black/30 text-gray-300' : 'bg-white/60 text-gray-600'}`}>
                    {card.domain}
                  </span>
                )}

                {/* Title */}
                <h3 className={`text-base font-semibold mb-1 line-clamp-2
                               ${dark ? 'text-gray-100' : 'text-gray-900'}`}>
                  {card.title}
                </h3>

                {/* Subtitle */}
                {card.subtitle && (
                  <p className={`text-xs mb-2 line-clamp-1
                                ${dark ? 'text-gray-400' : 'text-gray-500'}`}>
                    {card.subtitle}
                  </p>
                )}

                {/* Body */}
                <p className={`text-sm flex-1 line-clamp-3
                              ${dark ? 'text-gray-300' : 'text-gray-700'}`}>
                  {card.body}
                </p>

                {/* Footer */}
                <div className={`flex items-center justify-between mt-3 pt-3 border-t
                                ${dark ? 'border-white/10' : 'border-black/10'}`}>
                  {card.meta && (
                    <span className={`text-xs ${dark ? 'text-gray-500' : 'text-gray-500'}`}>
                      {card.meta}
                    </span>
                  )}
                  {card.slug && (
                    <button
                      onPointerDown={e => e.stopPropagation()}
                      onClick={() => navigate(`/wiki/${card.slug}`)}
                      className="ml-auto px-3 py-1 text-xs bg-indigo-600 hover:bg-indigo-500
                                 text-white rounded-lg transition-colors
                                 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      Read more →
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Arrow controls */}
      <div className="flex items-center justify-between px-1">
        <button
          onClick={prev}
          disabled={current === 0}
          aria-label="Previous card"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                     transition-all disabled:opacity-30 disabled:cursor-not-allowed
                     focus:outline-none focus:ring-2 focus:ring-indigo-400
                     ${dark
                       ? 'bg-gray-800 text-gray-400 hover:text-gray-100 hover:bg-gray-700'
                       : 'bg-gray-100 text-gray-500 hover:text-gray-800 hover:bg-gray-200'}`}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M15 19l-7-7 7-7" />
          </svg>
          Prev
        </button>
        <p className={`text-xs ${dark ? 'text-gray-600' : 'text-gray-400'}`}>
          swipe or use arrows
        </p>
        <button
          onClick={swipeLeft}
          disabled={stackIndices.length === 0}
          aria-label="Next card"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                     transition-all disabled:opacity-30 disabled:cursor-not-allowed
                     focus:outline-none focus:ring-2 focus:ring-indigo-400
                     ${dark
                       ? 'bg-gray-800 text-gray-400 hover:text-gray-100 hover:bg-gray-700'
                       : 'bg-gray-100 text-gray-500 hover:text-gray-800 hover:bg-gray-200'}`}
        >
          Next
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  );
}
