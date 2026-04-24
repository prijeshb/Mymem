import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { Button } from '@heroui/react';
import { ALL_DOMAINS } from '../lib/types';
import { ThemeToggle } from './ThemeToggle';

function DocIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414A1 1 0 0119 9.414V19a2 2 0 01-2 2z" />
    </svg>
  );
}

export function Navbar() {
  const [searchParams] = useSearchParams();
  const activeDomain = searchParams.get('domain') ?? '';
  const navigate = useNavigate();

  return (
    <nav
      className="sticky top-0 z-50 border-b border-gray-800 bg-gray-950/90 backdrop-blur"
      aria-label="Main navigation"
    >
      <div className="mx-auto max-w-7xl px-4 flex items-center gap-4 h-14">
        {/* Logo */}
        <Link
          to="/"
          className="flex items-center gap-2 font-bold text-lg text-blue-600 hover:text-blue-800
                     outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
        >
          <DocIcon />
          MyMem
        </Link>

        {/* Domain filter pills */}
        <div
          className="hidden md:flex items-center gap-1 flex-1 overflow-x-auto"
          role="navigation"
          aria-label="Filter by domain"
        >
          {ALL_DOMAINS.map(d => (
            <Link
              key={d}
              to={`/search?domain=${d}`}
              className={`px-2.5 py-0.5 rounded-full text-xs font-medium border transition-colors
                outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500
                ${activeDomain === d
                  ? 'border-blue-500 text-blue-700 bg-blue-50'
                  : 'border-slate-300 text-slate-500 hover:border-blue-500 hover:text-blue-700'
                }`}
            >
              {d}
            </Link>
          ))}
        </div>

        {/* Right nav links */}
        <div className="flex items-center gap-3 ml-auto text-sm">
          <Link
            to="/graph"
            className="text-gray-400 hover:text-gray-100 transition-colors
                       outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
          >
            Graph
          </Link>
          <Link
            to="/introspect"
            className="text-gray-400 hover:text-gray-100 transition-colors
                       outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
          >
            Introspect
          </Link>
          <Button variant="primary" size="sm" onPress={() => navigate('/ingest')}>
            + Ingest
          </Button>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}
