import type { EvalRun, ExtractionConsensusRun, EvalGrade } from '../lib/types';

// ── Suite registry ────────────────────────────────────────────────────────────
// Mirrors mymem/evals/runner.py registrations. Suites absent from the summary
// render as "never run" so gaps are visible instead of silently missing.

interface SuiteDef {
  key:          string;
  label:        string;
  hint:         string;
  neverRunHint: string;
}

const SUITES: SuiteDef[] = [
  { key: 'retrieval',            label: 'Retrieval',         hint: 'self-supervised RAG retrieval',  neverRunHint: 'mymem eval' },
  { key: 'chunking',             label: 'Chunking',          hint: 'chunk-size ablation',            neverRunHint: 'mymem eval' },
  { key: 'wiki_quality',         label: 'Wiki Quality',      hint: 'richness · stubs · confidence',  neverRunHint: 'mymem eval' },
  { key: 'extraction_consensus', label: 'Extraction',        hint: 'dual-LLM ingest agreement',      neverRunHint: 'runs after ingest' },
  { key: 'ragas',                label: 'Answer Quality',    hint: 'RAGAS-lite faithfulness',        neverRunHint: 'mymem eval --llm-judge' },
];

// ── Formatting helpers ────────────────────────────────────────────────────────

function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function pct(v: unknown): string {
  const n = asNum(v);
  return n === null ? '—' : `${Math.round(n * 100)}%`;
}

function num(v: unknown, digits = 2): string {
  const n = asNum(v);
  return n === null ? '—' : n.toFixed(digits);
}

function int(v: unknown): string {
  const n = asNum(v);
  return n === null ? '—' : String(Math.round(n));
}

function daysAgo(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

function agoLabel(days: number): string {
  if (days <= 0) return 'today';
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}

// ── Per-suite metric pickers ──────────────────────────────────────────────────

type Metric = { label: string; value: string };

function metricsFor(key: string, s: Record<string, unknown>): Metric[] {
  switch (key) {
    case 'retrieval':
      return [
        { label: 'P@k',  value: pct(s.precision_at_k) },
        { label: 'MRR',  value: num(s.mrr) },
        { label: 'uDCG', value: num(s.udcg) },
        { label: 'Mode', value: typeof s.mode === 'string' ? s.mode : '—' },
      ];
    case 'chunking':
      return [
        { label: 'Ablation rows',  value: int(s.ablation_rows) },
        { label: 'Best max tokens', value: int(s.recommended_max_tokens) },
      ];
    case 'wiki_quality': {
      const states = (s.confidence_states ?? {}) as Record<string, unknown>;
      return [
        { label: 'Pages',     value: int(s.total_pages) },
        { label: 'Richness',  value: num(s.mean_richness, 1) },
        { label: 'Stub rate', value: pct(s.stub_rate) },
        { label: 'Reviewed',  value: int(states.reviewed) },
      ];
    }
    case 'extraction_consensus':
      return [
        { label: 'Avg score', value: pct(s.mean_consensus_score) },
        { label: 'Pass rate', value: pct(s.pass_rate) },
        { label: 'Runs',      value: int(s.n_runs) },
        { label: 'Dup rate',  value: pct(s.mean_duplicate_rate) },
      ];
    case 'ragas':
      return [
        { label: 'Cases',        value: int(s.n_cases) },
        { label: 'Overall',      value: num(s.mean_overall) },
        { label: 'Faithfulness', value: num(s.mean_faithfulness) },
        { label: 'Skipped',      value: int(s.skipped) },
      ];
    default:
      // Generic fallback: first 4 scalar fields
      return Object.entries(s)
        .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
        .slice(0, 4)
        .map(([k, v]) => ({ label: k.replace(/_/g, ' '), value: String(v) }));
  }
}

// ── Badges ────────────────────────────────────────────────────────────────────

const GRADE_STYLES: Record<EvalGrade, string> = {
  PASS: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  WARN: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
  FAIL: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
};

function SuiteBadge({ grade }: { grade: EvalGrade | null }) {
  if (grade === null) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold
                       bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
        NO GRADE
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${GRADE_STYLES[grade]}`}>
      {grade}
    </span>
  );
}

function StalenessDot({ days }: { days: number }) {
  const color =
    days < 3   ? 'bg-emerald-500' :
    days <= 14 ? 'bg-yellow-500'  :
                 'bg-red-500';
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${color}`} aria-hidden="true" />;
}

// ── Cards ─────────────────────────────────────────────────────────────────────

function SuiteCard({ def, run }: { def: SuiteDef; run: EvalRun }) {
  const grade = (['PASS', 'WARN', 'FAIL'] as const).find(g => run.summary.grade === g) ?? null;
  const days = daysAgo(run.run_at);
  const metrics = metricsFor(def.key, run.summary);

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900
                    p-4 shadow-sm transition-shadow duration-200 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{def.label}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">{def.hint}</p>
        </div>
        <SuiteBadge grade={grade} />
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-3">
        {metrics.map(m => (
          <div key={m.label} className="flex items-baseline justify-between gap-2 min-w-0">
            <dt className="text-xs text-gray-500 dark:text-gray-400 truncate">{m.label}</dt>
            <dd className="text-xs font-semibold tabular-nums text-gray-900 dark:text-gray-100 truncate">
              {m.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="flex items-center gap-1.5 mt-3 pt-2 border-t border-gray-100 dark:border-gray-800">
        <StalenessDot days={days} />
        <span className="text-xs text-gray-500 dark:text-gray-400">last run {agoLabel(days)}</span>
      </div>
    </div>
  );
}

function NeverRunCard({ def }: { def: SuiteDef }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 dark:border-gray-700
                    bg-gray-50/50 dark:bg-gray-900/40 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-500 dark:text-gray-400 truncate">{def.label}</p>
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5 truncate">{def.hint}</p>
        </div>
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold
                         bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500">
          NEVER RUN
        </span>
      </div>
      <p className="text-xs text-gray-400 dark:text-gray-500 mt-3">
        Run via <code className="font-mono text-gray-500 dark:text-gray-400">{def.neverRunHint}</code>
      </p>
    </div>
  );
}

// ── Grid ──────────────────────────────────────────────────────────────────────

interface Props {
  summary:        Record<string, EvalRun>;
  extractionRuns: ExtractionConsensusRun[];
}

export function EvalSuiteGrid({ summary, extractionRuns }: Props) {
  // Extraction consensus lives in its own table — synthesize a summary card
  // from the runs list when the runner never recorded one in eval_runs.
  const merged: Record<string, EvalRun> = { ...summary };
  if (!merged.extraction_consensus && extractionRuns.length > 0) {
    const latest = extractionRuns.reduce((a, b) => (a.run_at > b.run_at ? a : b));
    const avg = extractionRuns.reduce((s, r) => s + r.consensus_score, 0) / extractionRuns.length;
    merged.extraction_consensus = {
      id: -1,
      run_at: latest.run_at,
      eval_type: 'extraction_consensus',
      summary: {
        grade: latest.grade,
        n_runs: extractionRuns.length,
        pass_rate: extractionRuns.filter(r => r.grade === 'PASS').length / extractionRuns.length,
        mean_consensus_score: avg,
      },
    };
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {SUITES.map(def => {
        const run = merged[def.key];
        return run
          ? <SuiteCard key={def.key} def={def} run={run} />
          : <NeverRunCard key={def.key} def={def} />;
      })}
    </div>
  );
}
