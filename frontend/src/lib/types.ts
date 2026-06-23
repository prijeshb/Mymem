export type Domain =
  | 'spiritual' | 'tech' | 'finance' | 'health'
  | 'reminder' | 'research' | 'personal' | 'creative'
  | 'business' | 'misc';

export const ALL_DOMAINS: Domain[] = [
  'tech', 'spiritual', 'finance', 'health', 'reminder',
  'research', 'personal', 'creative', 'business', 'misc',
];

export const SOURCE_TYPES = [
  'article', 'paper', 'repo', 'dataset', 'image',
  'youtube', 'podcast', 'tweet', 'webpage', 'book', 'newsletter', 'note',
] as const;
export type SourceType = typeof SOURCE_TYPES[number];

export interface HeatmapDay {
  date: string;   // YYYY-MM-DD
  count: number;
}

export interface HeatmapData {
  days: HeatmapDay[];
}

export interface Page {
  title: string;
  path: string;
  summary: string;
  domain: Domain;
  tags: string[];
  sources: number;
}

export interface PagedPages {
  items: Page[];
  total: number;
  page: number;
  limit: number;
}

export interface Stats {
  page_count: number;
  source_count: number;
  orphan_count: number;
  session_cost: number;
  domain_counts: Record<string, number>;
}

export interface GraphNode {
  id: string;
  slug: string;
  domain: Domain;
  tags: string[];
}

export interface GraphEdge {
  source: string;
  target: string;
  /** 'wikilink' = a resolved [[link]] between pages; 'shared' = pages that mention the same concept. */
  type?: 'wikilink' | 'shared';
  /** for 'shared' edges: how many concepts the two pages share. */
  weight?: number;
  /** for 'shared' edges: a few of the shared concept names. */
  via?: string[];
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type SSEEvent =
  | { type: 'token'; text: string }
  | { type: 'done'; citations: string[]; saved_to: string | null }
  | { type: 'error'; message: string };

export interface IngestResult {
  skipped: boolean;
  skip_reason: string | null;
  pages_written: string[];
  pages_updated: string[];
  chunk_count: number;
  rag_only: boolean;
  rag_chunks: number;
}

export interface Recommendation {
  page: string;
  reason: string;
  last_seen: string | null;
}

export type Trend = 'rising' | 'stable' | 'fading';

export interface Interest {
  domain: string;
  tag: string;
  weight: number;
  trend: Trend;
}

export interface IntrospectResult {
  date: string;
  generated_at: string;   // ISO datetime — when the LLM finished
  summary: string;
  saved_to: string | null;
  recommendations: Recommendation[];
  top_interests: Interest[];
}

export interface CuriosityResult {
  interests: Interest[];
}

export interface DailySummary {
  date:  string;   // YYYY-MM-DD
  title: string;
  body:  string;
  slug:  string;   // "daily/YYYY-MM-DD"
}

export interface LintIssue {
  kind: string;
  page: string;
  detail: string;
}

export interface LintResult {
  count: number;
  issues: LintIssue[];
  report: string;
}

export interface RelatedWebLink {
  label: string;
  url: string;
  snippet?: string;
  source?: string;
}

export interface RelatedConcept {
  title: string;
  slug: string;
  internal: boolean;
  web_links: RelatedWebLink[];
}

export interface WikiPageData {
  title: string;
  body: string;
  domain: Domain;
  tags: string[];
  sources: string[];
  created: string;
  updated: string;
  slug: string;
  archived: boolean;
  backlinks: Array<{ title: string; slug: string }>;
  toc: Array<{ level: number; text: string; id: string }>;
  related: RelatedConcept[] | undefined;
}

export interface ArchivedPage {
  title: string;
  slug: string;
  domain: Domain;
  tags: string[];
  updated: string;
}

export interface LogEntry {
  ts: string;
  operation: string;
  description: string;
  affected_pages: string[];
}

export interface QuizQuestion {
  question:   string;
  page_title: string;
  hint:       string;
  difficulty: 'easy' | 'medium' | 'hard';
}

export interface DigestTheme {
  theme:   string;
  pages:   string[];
  insight: string;
}

export interface DigestResult {
  period_days:          number;
  date_range:           string;
  pages_active:         number;
  queries_made:         number;
  themes:               DigestTheme[];
  emerging_connections: string[];
  knowledge_gaps:       string[];
  serendipity:          string;
  open_question:        string;
}

export interface TraceRow {
  id:            number;
  task:          string;
  model:         string;
  provider:      string;
  started_at:    string;
  latency_ms:    number;
  input_tokens:  number;
  output_tokens: number;
  cost_usd:      number;
  error:         string | null;
}

export interface TraceByModel {
  model:          string;
  calls:          number;
  avg_latency_ms: number;
  total_cost_usd: number;
  error_rate:     number;
}

export interface TraceByTask {
  task:           string;
  calls:          number;
  avg_latency_ms: number;
  total_cost_usd: number;
}

export interface TracesTotals {
  calls:          number | null;
  total_cost_usd: number | null;
  avg_latency_ms: number | null;
}

export interface TracesData {
  recent:   TraceRow[];
  by_model: TraceByModel[];
  by_task:  TraceByTask[];
  totals:   TracesTotals;
}

export interface EvalRun {
  id:        number;
  run_at:    string;
  eval_type: string;
  summary:   Record<string, unknown>;
}

export type EvalGrade = 'PASS' | 'WARN' | 'FAIL';

export interface ExtractionConsensusRun {
  id:               number;
  run_at:           string;
  source_id:        string;
  source_type:      string;
  pipeline_model:   string;
  reference_model:  string;
  consensus_score:  number;
  thesis_captured:  boolean;
  grade:            EvalGrade;
  gaps:             string[];
  false_positives:  string[];
  full_result?:     Record<string, unknown>;
}

export interface EvalsExtractionResult {
  runs:  ExtractionConsensusRun[];
  total: number;
}
