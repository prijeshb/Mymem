import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@heroui/react';
import { ThemeToggle } from './ThemeToggle';

export function Navbar() {
  const navigate = useNavigate();

  return (
    <nav
      className="sticky top-0 z-50 border-b border-gray-200 dark:border-gray-800
                 bg-white/90 dark:bg-gray-950/90 backdrop-blur"
      aria-label="Main navigation"
    >
      <div className="mx-auto max-w-screen-2xl px-4 flex items-center h-14">

        {/* ── Logo ── */}
        <Link
          to="/"
          className="flex items-center gap-2.5 group shrink-0
                     outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg"
          aria-label="MyMem home"
        >
          {/* Icon mark */}
          <div className="relative w-8 h-8 shrink-0">
            {/* Glow layer */}
            <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600
                            blur-[6px] opacity-50 group-hover:opacity-70 transition-opacity" />
            {/* Icon container */}
            <div className="relative w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600
                            flex items-center justify-center shadow-md">
              {/* Sparkles — AI/memory concept */}
              <svg className="w-4 h-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
              </svg>
            </div>
          </div>

          {/* Brand text */}
          <span className="font-bold text-base tracking-tight leading-none select-none">
            <span className="text-gray-900 dark:text-white">My</span>
            <span className="bg-gradient-to-r from-indigo-600 to-violet-600
                             dark:from-indigo-400 dark:to-violet-400
                             bg-clip-text text-transparent">Mem</span>
          </span>
        </Link>

        {/* ── Spacer ── */}
        <div className="flex-1" />

        {/* ── Right: nav + actions ── */}
        <div className="flex items-center gap-0.5">
          <Link
            to="/graph"
            className="px-3 py-1.5 rounded-lg text-sm text-gray-500 dark:text-gray-400
                       hover:text-gray-900 dark:hover:text-gray-100
                       hover:bg-gray-100 dark:hover:bg-gray-800
                       transition-colors outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            Graph
          </Link>
          <Link
            to="/introspect"
            className="px-3 py-1.5 rounded-lg text-sm text-gray-500 dark:text-gray-400
                       hover:text-gray-900 dark:hover:text-gray-100
                       hover:bg-gray-100 dark:hover:bg-gray-800
                       transition-colors outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            Introspect
          </Link>
          <Link
            to="/evals"
            className="px-3 py-1.5 rounded-lg text-sm text-gray-500 dark:text-gray-400
                       hover:text-gray-900 dark:hover:text-gray-100
                       hover:bg-gray-100 dark:hover:bg-gray-800
                       transition-colors outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            Evals
          </Link>

          {/* Search — hidden for now, keep for later */}
          <Link
            to="/search"
            className="hidden px-3 py-1.5 rounded-lg text-sm text-gray-500 dark:text-gray-400
                       hover:text-gray-900 dark:hover:text-gray-100
                       hover:bg-gray-100 dark:hover:bg-gray-800
                       transition-colors outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            Search
          </Link>

          <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-2" />

          <Button variant="primary" size="sm" onPress={() => navigate('/ingest')}>
            + Ingest
          </Button>
          <ThemeToggle />
        </div>

      </div>
    </nav>
  );
}
