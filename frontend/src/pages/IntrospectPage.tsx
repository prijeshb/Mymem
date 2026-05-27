import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { marked } from 'marked';
import {
  fetchIntrospect, fetchCuriosity, fetchDailySummaries,
  fetchQuestions, fetchDigest, titleToSlug,
} from '../lib/api';
import type {
  IntrospectResult, Interest, DailySummary,
  QuizQuestion, DigestResult,
} from '../lib/types';
import { localIsoToday, formatDate, formatTime, formatToday } from '../lib/date';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { SwipeableCards } from '../components/SwipeableCards';
import type { CardData } from '../components/SwipeableCards';
import { Button, Card, Chip, Input } from '@heroui/react';

function renderWithWikilinks(text: string): string {
  const withLinks = text.replace(/\[\[([^\]]+)\]\]/g, (_, title) => {
    const slug = titleToSlug(title);
    return `<a class="wikilink" href="/wiki/${slug}">[[${title}]]</a>`;
  });
  return marked.parse(withLinks) as string;
}

/** Split an LLM summary (markdown) into swipeable cards — one per ## section. */
function summaryToCards(summary: string, date: string, generatedAt?: string): CardData[] {
  const meta = generatedAt ? `as of ${formatTime(generatedAt)}` : date;
  const slug = undefined;

  const sections = summary.split(/(?=^##\s)/m).filter(s => s.trim());

  if (sections.length > 1) {
    return sections.map((section, i) => {
      const lines   = section.trim().split('\n');
      const heading = lines[0].replace(/^#+\s*/, '').trim();
      const body    = lines.slice(1).join('\n').replace(/\[\[([^\]]+)\]\]/g, '$1').trim();
      const preview = body.slice(0, 300) + (body.length > 300 ? '…' : '');
      return { id: `${date}-section-${i}`, title: heading || date, body: preview || '—', meta, slug };
    });
  }

  const paras = summary
    .replace(/^#[^\n]*\n+/, '')
    .split(/\n{2,}/)
    .map(p => p.replace(/\[\[([^\]]+)\]\]/g, '$1').trim())
    .filter(Boolean);

  if (paras.length <= 1) {
    return [{ id: `${date}-0`, title: date, body: paras[0]?.slice(0, 400) ?? '—', meta, slug }];
  }

  return paras.slice(0, 8).map((p, i) => ({
    id: `${date}-para-${i}`,
    title: date,
    body: p.slice(0, 300) + (p.length > 300 ? '…' : ''),
    meta,
    slug,
  }));
}

function TrendBadge({ interest }: { interest: Interest }) {
  const cls =
    interest.trend === 'rising'
      ? 'bg-indigo-100 border border-indigo-300 text-indigo-700 dark:bg-indigo-950 dark:border-indigo-700 dark:text-indigo-300'
      : interest.trend === 'fading'
      ? 'bg-gray-100 border border-gray-300 text-gray-600 dark:bg-gray-900 dark:border-gray-700 dark:text-gray-500'
      : 'bg-gray-100 border border-gray-200 text-gray-600 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-400';
  return (
    <Chip size="sm" className={cls}>
      {interest.domain}/{interest.tag}
      {interest.trend === 'rising' && (
        <span className="text-indigo-600 dark:text-indigo-400 ml-1">{interest.weight.toFixed(1)}</span>
      )}
    </Chip>
  );
}

const DIFFICULTY_CLS: Record<string, string> = {
  easy:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  medium: 'bg-amber-100  text-amber-700  dark:bg-amber-950  dark:text-amber-300',
  hard:   'bg-red-100    text-red-700    dark:bg-red-950    dark:text-red-300',
};

function QuizCard({ q, revealed, onToggle }: {
  q: QuizQuestion;
  revealed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full text-left rounded-xl border border-gray-200 dark:border-gray-800
                 bg-white dark:bg-gray-900 p-4 transition-all
                 hover:border-indigo-300 dark:hover:border-indigo-700
                 hover:shadow-md focus-visible:ring-2 focus-visible:ring-indigo-500 outline-none"
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <p className="text-sm font-medium text-gray-900 dark:text-gray-100 leading-snug">
          {q.question}
        </p>
        <span className={`shrink-0 text-xs font-semibold px-2 py-0.5 rounded-full ${DIFFICULTY_CLS[q.difficulty] ?? DIFFICULTY_CLS.medium}`}>
          {q.difficulty}
        </span>
      </div>

      {revealed ? (
        <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800 space-y-2">
          <p className="text-sm text-gray-700 dark:text-gray-300">{q.hint}</p>
          <Link
            to={`/wiki/${titleToSlug(q.page_title)}`}
            onClick={e => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs text-indigo-600 dark:text-indigo-400
                       hover:underline font-medium"
          >
            <span>→</span>
            <span>{q.page_title}</span>
          </Link>
        </div>
      ) : (
        <p className="text-xs text-gray-400 dark:text-gray-600 mt-1">
          Click to reveal hint
        </p>
      )}
    </button>
  );
}

function DigestView({ d }: { d: DigestResult }) {
  return (
    <div className="space-y-5">
      {/* Stats bar */}
      <div className="flex flex-wrap gap-3">
        <div className="flex-1 min-w-[120px] rounded-lg bg-indigo-50 dark:bg-indigo-950/40 px-4 py-3 text-center">
          <p className="text-2xl font-bold text-indigo-700 dark:text-indigo-300">{d.pages_active}</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-0.5">pages active</p>
        </div>
        <div className="flex-1 min-w-[120px] rounded-lg bg-violet-50 dark:bg-violet-950/40 px-4 py-3 text-center">
          <p className="text-2xl font-bold text-violet-700 dark:text-violet-300">{d.queries_made}</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-0.5">queries made</p>
        </div>
        <div className="flex-1 min-w-[200px] rounded-lg bg-gray-50 dark:bg-gray-900 px-4 py-3 text-center">
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{d.date_range}</p>
          <p className="text-xs text-gray-500 dark:text-gray-500 mt-0.5">{d.period_days}-day window</p>
        </div>
      </div>

      {/* Themes */}
      {d.themes.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400 mb-3">
            Themes
          </p>
          <div className="grid gap-3">
            {d.themes.map((t, i) => (
              <div key={i} className="rounded-lg border border-gray-200 dark:border-gray-800 p-3">
                <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-1">{t.theme}</p>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">{t.insight}</p>
                <div className="flex flex-wrap gap-1.5">
                  {t.pages.map(pg => (
                    <Link
                      key={pg}
                      to={`/wiki/${titleToSlug(pg)}`}
                      className="text-xs bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300
                                 px-2 py-0.5 rounded-md hover:underline"
                    >
                      {pg}
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Emerging connections + Knowledge gaps side by side */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {d.emerging_connections.length > 0 && (
          <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 p-4">
            <p className="text-xs font-semibold uppercase tracking-widest text-emerald-700 dark:text-emerald-400 mb-2">
              Emerging Connections
            </p>
            <ul className="space-y-1.5">
              {d.emerging_connections.map((c, i) => (
                <li key={i} className="text-sm text-gray-700 dark:text-gray-300 flex items-start gap-2">
                  <span className="text-emerald-500 shrink-0 mt-0.5">◈</span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        )}
        {d.knowledge_gaps.length > 0 && (
          <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-4">
            <p className="text-xs font-semibold uppercase tracking-widest text-amber-700 dark:text-amber-400 mb-2">
              Knowledge Gaps
            </p>
            <ul className="space-y-1.5">
              {d.knowledge_gaps.map((g, i) => (
                <li key={i} className="text-sm text-gray-700 dark:text-gray-300 flex items-start gap-2">
                  <span className="text-amber-500 shrink-0 mt-0.5">◇</span>
                  {g}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Serendipity */}
      {d.serendipity && (
        <div className="rounded-lg border-l-4 border-violet-500 bg-violet-50 dark:bg-violet-950/30 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-widest text-violet-600 dark:text-violet-400 mb-1">
            Serendipity
          </p>
          <p className="text-sm text-gray-700 dark:text-gray-300">{d.serendipity}</p>
        </div>
      )}

      {/* Open question */}
      {d.open_question && (
        <div className="rounded-xl bg-gradient-to-r from-indigo-50 to-violet-50
                        dark:from-indigo-950/40 dark:to-violet-950/40 p-4 text-center">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-600 dark:text-indigo-400 mb-2">
            Open Question
          </p>
          <p className="text-base font-medium text-gray-800 dark:text-gray-200 italic">
            "{d.open_question}"
          </p>
        </div>
      )}
    </div>
  );
}

export function IntrospectPage() {
  const todayIso = localIsoToday();
  const today    = formatToday();

  // Daily summary
  const [summary, setSummary]               = useState<IntrospectResult | null>(null);
  const [interests, setInterests]           = useState<Interest[]>([]);
  const [pastSummaries, setPast]            = useState<DailySummary[]>([]);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [error, setError]                   = useState<string | null>(null);
  const [todayCards, setTodayCards]         = useState<IntrospectResult | null>(null);
  const [loadingDay, setLoadingDay]         = useState(false);
  const [loadingPast, setLoadingPast]       = useState(true);
  const [loadingToday, setLoadingToday]     = useState(true);

  // Research topic
  const [topicInput, setTopicInput]   = useState('');
  const [topicResult, setTopicResult] = useState<IntrospectResult | null>(null);
  const [loadingTopic, setLoadingTopic] = useState(false);

  // Quiz
  const [questions, setQuestions]         = useState<QuizQuestion[]>([]);
  const [loadingQuestions, setLoadingQuestions] = useState(false);
  const [revealedIdx, setRevealedIdx]     = useState<Set<number>>(new Set());

  // Digest
  const [digestPeriod, setDigestPeriod] = useState<7 | 30>(7);
  const [digest, setDigest]             = useState<DigestResult | null>(null);
  const [loadingDigest, setLoadingDigest] = useState(false);

  useEffect(() => {
    fetchCuriosity(20)
      .then(c => setInterests(c.interests))
      .catch(e => setError(String(e)));

    setLoadingPast(true);
    fetchDailySummaries(14)
      .then(past => setPast(past.filter(d => d.date < todayIso)))
      .catch(e => setError(String(e)))
      .finally(() => setLoadingPast(false));

    setLoadingToday(true);
    fetchIntrospect(undefined, todayIso)
      .then(s => setTodayCards(s))
      .catch(e => setError(String(e)))
      .finally(() => setLoadingToday(false));
  }, []);

  async function refreshTodayCards() {
    setLoadingDay(true);
    try {
      const r = await fetchIntrospect(undefined, todayIso, true);
      setTodayCards(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingDay(false);
    }
  }

  function loadSummary() {
    setLoadingSummary(true);
    setSummary(null);
    setError(null);
    fetchIntrospect()
      .then(s => setSummary(s))
      .catch(e => setError(String(e)))
      .finally(() => setLoadingSummary(false));
  }

  async function handleTopicSuggest() {
    if (!topicInput.trim()) return;
    setLoadingTopic(true);
    setTopicResult(null);
    try {
      const r = await fetchIntrospect(topicInput.trim());
      setTopicResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingTopic(false);
    }
  }

  async function handleGenerateQuestions() {
    setLoadingQuestions(true);
    setQuestions([]);
    setRevealedIdx(new Set());
    try {
      const qs = await fetchQuestions(5);
      setQuestions(qs);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingQuestions(false);
    }
  }

  async function handleGenerateDigest() {
    setLoadingDigest(true);
    setDigest(null);
    try {
      const d = await fetchDigest(digestPeriod);
      setDigest(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingDigest(false);
    }
  }

  function toggleRevealed(i: number) {
    setRevealedIdx(prev => {
      const s = new Set(prev);
      s.has(i) ? s.delete(i) : s.add(i);
      return s;
    });
  }

  const todaySummaryCards: CardData[] = todayCards
    ? summaryToCards(todayCards.summary, todayIso, todayCards.generated_at)
    : [];
  const todayRecoCards: CardData[] = todayCards?.recommendations.map((r): CardData => ({
    id:       r.page,
    title:    r.page,
    subtitle: 'Recommended read',
    body:     r.reason,
    meta:     r.last_seen ? `Last seen: ${r.last_seen}` : undefined,
    slug:     titleToSlug(r.page),
  })) ?? [];

  const rising = interests.filter(i => i.trend === 'rising');
  const fading = interests.filter(i => i.trend === 'fading');
  const stable = interests.filter(i => i.trend === 'stable');

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Introspect</h1>
          <p className="text-xs text-gray-600 dark:text-gray-500 mt-0.5">{today}</p>
        </div>
        {summary?.generated_at && (
          <Chip size="sm" className="bg-gray-100 border border-gray-300 text-gray-600 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-400">
            till&nbsp;<span className="text-indigo-600 dark:text-indigo-300 font-medium">{formatTime(summary.generated_at)}</span>
          </Chip>
        )}
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Daily summary */}
      <Card>
        <Card.Header className="flex items-center justify-between">
          <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            Today's Summary
          </Card.Title>
          {summary && (
            <Button
              variant="ghost"
              size="sm"
              onPress={loadSummary}
              isDisabled={loadingSummary}
              aria-label="Refresh summary"
            >
              ↻ Refresh
            </Button>
          )}
        </Card.Header>
        <Card.Content>
          {loadingSummary ? (
            <LoadingSpinner />
          ) : summary ? (
            <div
              className="prose dark:prose-invert prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: renderWithWikilinks(summary.summary) }}
            />
          ) : (
            <div className="flex flex-col items-center gap-3 py-4">
              <p className="text-sm text-gray-600 dark:text-gray-500">
                Summarise your knowledge activity for today.
              </p>
              <Button variant="primary" onPress={loadSummary}>
                Generate Summary
              </Button>
            </div>
          )}
        </Card.Content>
      </Card>

      {/* Recommendations */}
      {summary && (
        <Card>
          <Card.Header>
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
              Suggested Reading
            </Card.Title>
          </Card.Header>
          <Card.Content>
            {summary.recommendations.length > 0 ? (
              <SwipeableCards
                cards={summary.recommendations.map((r): CardData => ({
                  id:       r.page,
                  title:    r.page,
                  subtitle: 'Recommended read',
                  body:     r.reason,
                  meta:     r.last_seen ? `Last seen: ${r.last_seen}` : undefined,
                  slug:     titleToSlug(r.page),
                }))}
              />
            ) : (
              <p className="text-sm text-gray-600 dark:text-gray-500">
                No suggestions yet — revisit pages after 14 days or ingest more sources.
              </p>
            )}
          </Card.Content>
        </Card>
      )}

      {/* ── Research Topic ─────────────────────────────────────────────────── */}
      <Card>
        <Card.Header>
          <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            Research Topic
          </Card.Title>
        </Card.Header>
        <Card.Content className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-500">
            Enter a topic and get personalised research suggestions drawn from your wiki.
          </p>
          <div className="flex gap-2">
            <Input
              value={topicInput}
              onChange={e => setTopicInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleTopicSuggest()}
              placeholder="e.g. stoic resilience, transformer attention…"
              aria-label="Research topic"
              fullWidth
            />
            <Button
              variant="primary"
              onPress={handleTopicSuggest}
              isDisabled={loadingTopic || !topicInput.trim()}
            >
              {loadingTopic ? 'Thinking…' : 'Suggest'}
            </Button>
          </div>

          {loadingTopic && <LoadingSpinner />}

          {topicResult && (
            <div className="space-y-4 pt-1">
              <div
                className="prose dark:prose-invert prose-sm max-w-none rounded-lg
                           bg-gray-50 dark:bg-gray-900 p-4"
                dangerouslySetInnerHTML={{ __html: renderWithWikilinks(topicResult.summary) }}
              />
              {topicResult.recommendations.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500 dark:text-gray-500 mb-3">Related pages in your wiki</p>
                  <SwipeableCards
                    cards={topicResult.recommendations.map((r): CardData => ({
                      id:       r.page,
                      title:    r.page,
                      subtitle: 'Recommended read',
                      body:     r.reason,
                      meta:     r.last_seen ? `Last seen: ${r.last_seen}` : undefined,
                      slug:     titleToSlug(r.page),
                    }))}
                  />
                </div>
              )}
            </div>
          )}
        </Card.Content>
      </Card>

      {/* ── Test Yourself ──────────────────────────────────────────────────── */}
      <Card>
        <Card.Header className="flex items-center justify-between">
          <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            Test Yourself
          </Card.Title>
          {questions.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onPress={handleGenerateQuestions}
              isDisabled={loadingQuestions}
            >
              ↻ New set
            </Button>
          )}
        </Card.Header>
        <Card.Content className="space-y-3">
          {questions.length === 0 && !loadingQuestions && (
            <div className="flex flex-col items-center gap-3 py-4">
              <p className="text-sm text-gray-600 dark:text-gray-500 text-center">
                Generate 5 quiz questions from your recent wiki pages.
                Click any card to reveal the hint.
              </p>
              <Button variant="primary" onPress={handleGenerateQuestions}>
                Generate Questions
              </Button>
            </div>
          )}

          {loadingQuestions && <LoadingSpinner />}

          {questions.length > 0 && (
            <>
              <div className="flex justify-end">
                <button
                  onClick={() => setRevealedIdx(new Set(questions.map((_, i) => i)))}
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                >
                  Reveal all
                </button>
                <span className="mx-2 text-gray-300 dark:text-gray-700">·</span>
                <button
                  onClick={() => setRevealedIdx(new Set())}
                  className="text-xs text-gray-500 dark:text-gray-500 hover:underline"
                >
                  Hide all
                </button>
              </div>
              <div className="space-y-2">
                {questions.map((q, i) => (
                  <QuizCard
                    key={i}
                    q={q}
                    revealed={revealedIdx.has(i)}
                    onToggle={() => toggleRevealed(i)}
                  />
                ))}
              </div>
            </>
          )}
        </Card.Content>
      </Card>

      {/* ── Weekly Digest ──────────────────────────────────────────────────── */}
      <Card>
        <Card.Header className="flex items-center justify-between flex-wrap gap-2">
          <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            Knowledge Digest
          </Card.Title>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              {([7, 30] as const).map(p => (
                <button
                  key={p}
                  onClick={() => { setDigestPeriod(p); setDigest(null); }}
                  className={`px-3 py-1 text-xs font-medium transition-colors ${
                    digestPeriod === p
                      ? 'bg-indigo-600 text-white'
                      : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                  }`}
                >
                  {p}d
                </button>
              ))}
            </div>
            <Button
              variant="primary"
              size="sm"
              onPress={handleGenerateDigest}
              isDisabled={loadingDigest}
            >
              {loadingDigest ? 'Generating…' : digest ? '↻ Refresh' : 'Generate'}
            </Button>
          </div>
        </Card.Header>
        <Card.Content>
          {loadingDigest ? (
            <LoadingSpinner />
          ) : digest ? (
            <DigestView d={digest} />
          ) : (
            <p className="text-sm text-gray-600 dark:text-gray-500 text-center py-4">
              Analyse your last {digestPeriod} days — themes, connections, gaps, and more.
            </p>
          )}
        </Card.Content>
      </Card>

      {/* Today's Summaries — cards up to current time */}
      <Card>
        <Card.Header className="flex items-center justify-between">
          <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
            Today's Summaries
          </Card.Title>
          <div className="flex items-center gap-2">
            {todayCards?.generated_at && (
              <span className="text-xs text-gray-500 dark:text-gray-500">
                till {formatTime(todayCards.generated_at)}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onPress={refreshTodayCards}
              isDisabled={loadingDay}
              aria-label="Refresh today's summaries"
            >
              ↻
            </Button>
          </div>
        </Card.Header>
        <Card.Content>
          {loadingToday || loadingDay ? (
            <LoadingSpinner />
          ) : todaySummaryCards.length > 0 ? (
            <div className="space-y-4">
              <SwipeableCards cards={todaySummaryCards} />
              {todayRecoCards.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500 dark:text-gray-500 mb-3">Recommended from today</p>
                  <SwipeableCards cards={todayRecoCards} />
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-600 dark:text-gray-600 text-center py-3">
              No activity recorded for today yet.
            </p>
          )}
        </Card.Content>
      </Card>

      {/* Past daily summaries */}
      {(loadingPast || pastSummaries.length > 0) && (
        <Card>
          <Card.Header>
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
              Previous Days
            </Card.Title>
          </Card.Header>
          <Card.Content>
            {loadingPast ? (
              <LoadingSpinner />
            ) : (
              <SwipeableCards
                cards={pastSummaries.map((d): CardData => ({
                  id:       d.date,
                  title:    formatDate(d.date),
                  subtitle: d.title,
                  body:     d.body.replace(/^#.*\n+/, '').slice(0, 300).trimEnd() + (d.body.length > 300 ? '…' : ''),
                  slug:     d.slug,
                }))}
              />
            )}
          </Card.Content>
        </Card>
      )}

      {/* Curiosity trends */}
      {interests.length > 0 && (
        <Card>
          <Card.Header>
            <Card.Title className="text-xs font-semibold uppercase tracking-widest text-gray-500 dark:text-gray-400">
              Curiosity Trends
            </Card.Title>
          </Card.Header>
          <Card.Content className="space-y-4">
            {rising.length > 0 && (
              <div>
                <p className="text-xs text-gray-600 dark:text-gray-500 mb-2">Rising ▲</p>
                <div className="flex flex-wrap gap-2">
                  {rising.map(i => <TrendBadge key={`${i.domain}/${i.tag}`} interest={i} />)}
                </div>
              </div>
            )}
            {stable.length > 0 && (
              <div>
                <p className="text-xs text-gray-600 dark:text-gray-500 mb-2">Stable</p>
                <div className="flex flex-wrap gap-2">
                  {stable.map(i => <TrendBadge key={`${i.domain}/${i.tag}`} interest={i} />)}
                </div>
              </div>
            )}
            {fading.length > 0 && (
              <div>
                <p className="text-xs text-gray-600 dark:text-gray-500 mb-2">Fading ▼</p>
                <div className="flex flex-wrap gap-2">
                  {fading.map(i => <TrendBadge key={`${i.domain}/${i.tag}`} interest={i} />)}
                </div>
              </div>
            )}
          </Card.Content>
        </Card>
      )}
    </div>
  );
}
