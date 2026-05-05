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
