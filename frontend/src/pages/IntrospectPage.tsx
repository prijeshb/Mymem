import { useEffect, useState } from 'react';
import { marked } from 'marked';
import { fetchIntrospect, fetchCuriosity, fetchDailySummaries, titleToSlug } from '../lib/api';
import type { IntrospectResult, Interest, DailySummary } from '../lib/types';
import { localIsoToday, formatDate, formatTime, formatToday } from '../lib/date';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { SwipeableCards } from '../components/SwipeableCards';
import type { CardData } from '../components/SwipeableCards';
import { Button, Card, Chip } from '@heroui/react';

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

  // Split on ## subheadings (keep delimiter at the start of each chunk)
  const sections = summary.split(/(?=^##\s)/m).filter(s => s.trim());

  if (sections.length > 1) {
    return sections.map((section, i) => {
      const lines   = section.trim().split('\n');
      const heading = lines[0].replace(/^#+\s*/, '').trim();
      const body    = lines.slice(1).join('\n').replace(/\[\[([^\]]+)\]\]/g, '$1').trim();
      const preview = body.slice(0, 300) + (body.length > 300 ? '…' : '');
      return {
        id:    `${date}-section-${i}`,
        title: heading || date,
        body:  preview || '—',
        meta,
        slug,
      };
    });
  }

  // Fallback: split by double-newline paragraphs
  const paras = summary
    .replace(/^#[^\n]*\n+/, '')  // strip top-level heading
    .split(/\n{2,}/)
    .map(p => p.replace(/\[\[([^\]]+)\]\]/g, '$1').trim())
    .filter(Boolean);

  if (paras.length <= 1) {
    // Single block — one card
    return [{
      id:    `${date}-0`,
      title: date,
      body:  paras[0]?.slice(0, 400) ?? '—',
      meta,
      slug,
    }];
  }

  return paras.slice(0, 8).map((p, i) => ({
    id:    `${date}-para-${i}`,
    title: date,
    body:  p.slice(0, 300) + (p.length > 300 ? '…' : ''),
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

export function IntrospectPage() {
  const todayIso = localIsoToday();
  const today    = formatToday();

  const [summary, setSummary]               = useState<IntrospectResult | null>(null);
  const [interests, setInterests]           = useState<Interest[]>([]);
  const [pastSummaries, setPast]            = useState<DailySummary[]>([]);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [error, setError]                   = useState<string | null>(null);

  const [todayCards, setTodayCards]     = useState<IntrospectResult | null>(null);
  const [loadingDay, setLoadingDay]     = useState(false);
  const [loadingPast, setLoadingPast]   = useState(true);
  const [loadingToday, setLoadingToday] = useState(true);

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

      {/* Past daily summaries — now below Today's Summaries */}
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
