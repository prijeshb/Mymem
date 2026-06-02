import { useEffect, useState } from 'react';
import { fetchEvalsExtraction } from '../lib/api';
import type { ExtractionConsensusRun, EvalGrade } from '../lib/types';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';

// ── Grade badge ───────────────────────────────────────────────────────────────

const GRADE_STYLES: Record<EvalGrade, string> = {
  PASS: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  WARN: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
  FAIL: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
};

function GradeBadge({ grade }: { grade: EvalGrade }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${GRADE_STYLES[grade]}`}>
      {grade}
    </span>
  );
}

// ── Score bar ─────────────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 67 ? 'bg-emerald-500' :
    pct >= 50 ? 'bg-yellow-500' :
                'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-gray-600 dark:text-gray-400">{pct}%</span>
    </div>
  );
}

// ── Pill list ─────────────────────────────────────────────────────────────────

function PillList({ items, color }: { items: string[]; color: string }) {
  if (!items.length) return <span className="text-xs text-gray-400">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((t, i) => (
        <span key={i} className={`px-1.5 py-0.5 rounded text-xs ${color}`}>{t}</span>
      ))}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
      <div className="w-16 h-16 rounded-2xl bg-indigo-100 dark:bg-indigo-900/30 flex items-center justify-center">
        <svg className="w-8 h-8 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round"
            d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <div>
        <p className="font-semibold text-gray-800 dark:text-gray-100">No eval runs yet</p>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Extraction consensus runs automatically after every ingest.<br />
          Ingest a source to generate your first eval.
        </p>
      </div>
    </div>
  );
}

// ── Row ───────────────────────────────────────────────────────────────────────

function RunRow({ run }: { run: ExtractionConsensusRun }) {
  const [open, setOpen] = useState(false);
  const date = new Date(run.run_at).toLocaleString(undefined, {
    dateStyle: 'short', timeStyle: 'short',
  });
  const sourceLabel = run.source_id.split('/').pop() ?? run.source_id;

  return (
    <>
      <tr
        className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50
                   cursor-pointer transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <td className="py-3 px-4">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate max-w-[18rem]"
             title={run.source_id}>
            {sourceLabel}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">{run.source_type} · {date}</p>
        </td>
        <td className="py-3 px-4">
          <ScoreBar score={run.consensus_score} />
        </td>
        <td className="py-3 px-4">
          <GradeBadge grade={run.grade} />
        </td>
        <td className="py-3 px-4 text-center">
          {run.thesis_captured
            ? <span className="text-emerald-500 text-sm">✓</span>
            : <span className="text-red-400 text-sm">✗</span>}
        </td>
        <td className="py-3 px-4 text-xs text-gray-500 dark:text-gray-400">
          {run.gaps.length > 0 ? (
            <span className="text-yellow-600 dark:text-yellow-400">{run.gaps.length} gap{run.gaps.length !== 1 ? 's' : ''}</span>
          ) : (
            <span className="text-emerald-500">clean</span>
          )}
        </td>
        <td className="py-3 px-4 text-right">
          <svg className={`w-4 h-4 text-gray-400 inline transition-transform ${open ? 'rotate-180' : ''}`}
               fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </td>
      </tr>

      {open && (
        <tr className="bg-gray-50 dark:bg-gray-900/40 border-b border-gray-100 dark:border-gray-800">
          <td colSpan={6} className="px-4 py-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                  Gaps — reference found, pipeline missed
                </p>
                <PillList
                  items={run.gaps}
                  color="bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
                />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                  False positives — pipeline found, reference didn't
                </p>
                <PillList
                  items={run.false_positives}
                  color="bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300"
                />
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Models</p>
                <p className="text-xs text-gray-600 dark:text-gray-400">
                  Pipeline: <span className="font-mono">{run.pipeline_model}</span>
                  <br />
                  Reference: <span className="font-mono">{run.reference_model}</span>
                </p>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

type OrderKey = 'recent_first' | 'worst_first';
type GradeFilter = EvalGrade | '';

export function EvalsPage() {
  const [runs, setRuns]         = useState<ExtractionConsensusRun[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState('');
  const [order, setOrder]       = useState<OrderKey>('recent_first');
  const [grade, setGrade]       = useState<GradeFilter>('');

  useEffect(() => {
    setLoading(true);
    setError('');
    fetchEvalsExtraction(50, order, grade)
      .then(d => { setRuns(d.runs); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, [order, grade]);

  const passCount = runs.filter(r => r.grade === 'PASS').length;
  const warnCount = runs.filter(r => r.grade === 'WARN').length;
  const failCount = runs.filter(r => r.grade === 'FAIL').length;
  const avgScore  = runs.length
    ? runs.reduce((s, r) => s + r.consensus_score, 0) / runs.length
    : 0;

  return (
    <div className="max-w-5xl mx-auto py-6 space-y-6">

      {/* ── Header ── */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Eval Dashboard</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Extraction consensus — pipeline vs. reference LLM agreement after each ingest.
        </p>
      </div>

      {/* ── Summary chips ── */}
      {!loading && !error && runs.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {[
            { label: 'Avg score', value: `${Math.round(avgScore * 100)}%`, color: 'text-indigo-600 dark:text-indigo-400' },
            { label: 'PASS', value: passCount, color: 'text-emerald-600 dark:text-emerald-400' },
            { label: 'WARN', value: warnCount, color: 'text-yellow-600 dark:text-yellow-400' },
            { label: 'FAIL', value: failCount, color: 'text-red-600 dark:text-red-400' },
          ].map(({ label, value, color }) => (
            <div key={label}
                 className="flex flex-col items-center px-5 py-3 rounded-xl
                            bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 shadow-sm">
              <span className={`text-2xl font-bold tabular-nums ${color}`}>{value}</span>
              <span className="text-xs text-gray-500 mt-0.5">{label}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Filters ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden text-sm">
          {(['recent_first', 'worst_first'] as OrderKey[]).map(o => (
            <button key={o}
                    onClick={() => setOrder(o)}
                    className={`px-3 py-1.5 transition-colors ${
                      order === o
                        ? 'bg-indigo-600 text-white'
                        : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800'
                    }`}>
              {o === 'recent_first' ? 'Recent first' : 'Worst first'}
            </button>
          ))}
        </div>

        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden text-sm">
          {(['', 'PASS', 'WARN', 'FAIL'] as GradeFilter[]).map(g => (
            <button key={g}
                    onClick={() => setGrade(g)}
                    className={`px-3 py-1.5 transition-colors ${
                      grade === g
                        ? 'bg-indigo-600 text-white'
                        : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800'
                    }`}>
              {g || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      {loading && <div className="flex justify-center py-16"><LoadingSpinner /></div>}
      {error   && <ErrorBanner message={error} />}

      {!loading && !error && runs.length === 0 && <EmptyState />}

      {!loading && !error && runs.length > 0 && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden bg-white dark:bg-gray-900">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                <th className="py-2.5 px-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">Source</th>
                <th className="py-2.5 px-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">Consensus</th>
                <th className="py-2.5 px-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">Grade</th>
                <th className="py-2.5 px-4 text-center text-xs font-semibold text-gray-500 uppercase tracking-wide">Thesis</th>
                <th className="py-2.5 px-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">Gaps</th>
                <th className="py-2.5 px-4" />
              </tr>
            </thead>
            <tbody>
              {runs.map(run => <RunRow key={run.id} run={run} />)}
            </tbody>
          </table>
        </div>
      )}

    </div>
  );
}
