import { useRef, useState } from 'react';
import { SharedElementTransition } from 'react-aria-components';
import { postIngest, postUpload, postIngestText } from '../lib/api';
import type { IngestResult, SourceType } from '../lib/types';
import { ALL_DOMAINS, SOURCE_TYPES } from '../lib/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { Button, Card, Input, Tabs, TextArea } from '@heroui/react';

type Tab = 'url' | 'file' | 'text';

function estimateTokens(chars: number): number {
  return Math.ceil(chars / 4);
}

const CHUNK_THRESHOLD_TOKENS = 24_000;

function TokenBadge({ tokens }: { tokens: number }) {
  if (tokens === 0) return null;

  const willChunk = tokens > CHUNK_THRESHOLD_TOKENS;
  const cls =
    tokens < 2_000  ? 'text-green-400 border-green-800 bg-green-950' :
    tokens < 8_000  ? 'text-yellow-400 border-yellow-800 bg-yellow-950' :
    tokens < CHUNK_THRESHOLD_TOKENS ? 'text-orange-400 border-orange-800 bg-orange-950' :
                      'text-red-400 border-red-800 bg-red-950';

  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs ${cls}`}>
      ~{tokens.toLocaleString()} tokens
      {willChunk && <span title="Text will be split into chunks for processing">· will chunk</span>}
    </span>
  );
}

function SharedFields({
  sourceType, setSourceType,
  domain, setDomain,
  tags, setTags,
}: {
  sourceType: SourceType; setSourceType: (v: SourceType) => void;
  domain: string; setDomain: (v: string) => void;
  tags: string; setTags: (v: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="source-type" className="block text-xs font-medium text-gray-300 mb-1">
          Source type
        </label>
        <select
          id="source-type"
          value={sourceType}
          onChange={e => setSourceType(e.target.value as SourceType)}
          className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100
                     focus:outline-hidden focus:ring-2 focus:ring-indigo-400 [color-scheme:dark]"
        >
          {SOURCE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="domain" className="block text-xs font-medium text-gray-300 mb-1">Domain</label>
        <select
          id="domain"
          value={domain}
          onChange={e => setDomain(e.target.value)}
          className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100
                     focus:outline-hidden focus:ring-2 focus:ring-indigo-400 [color-scheme:dark]"
        >
          <option value="">Auto-detect</option>
          {ALL_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="tags" className="block text-xs font-medium text-gray-300 mb-1">
          Tags <span className="text-gray-500 font-normal">(comma separated)</span>
        </label>
        <Input
          id="tags"
          value={tags}
          onChange={e => setTags(e.target.value)}
          placeholder="ml, python, tutorial"
          aria-label="Tags, comma separated"
          fullWidth
        />
      </div>
    </div>
  );
}

function ResultCard({ result }: { result: IngestResult }) {
  if (result.skipped) {
    return (
      <div role="alert" className="p-4 rounded-lg border border-yellow-700 bg-yellow-950 text-yellow-200 text-sm">
        <strong>Skipped:</strong> {result.skip_reason}
      </div>
    );
  }
  return (
    <div role="alert" className="p-4 rounded-lg border border-green-700 bg-green-950 text-green-200 text-sm space-y-1">
      <div className="font-semibold mb-2">✓ Ingest complete</div>
      <div>New pages: <strong>{result.pages_written.join(', ') || 'none'}</strong></div>
      <div>Updated: <strong>{result.pages_updated.join(', ') || 'none'}</strong></div>
      <div>Chunks: <strong>{result.chunk_count}</strong></div>
    </div>
  );
}

export function IngestPage() {
  const [tab, setTab] = useState<Tab>('url');

  const [sourceType, setSourceType] = useState<SourceType>('article');
  const [domain, setDomain]         = useState('');
  const [tags, setTags]             = useState('');

  const [url, setUrl]               = useState('');
  const [file, setFile]             = useState<File | null>(null);
  const fileInputRef                = useRef<HTMLInputElement>(null);
  const [text, setText]             = useState('');
  const [textTitle, setTextTitle]   = useState('');

  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState<IngestResult | null>(null);
  const [error, setError]     = useState<string | null>(null);

  function tagList() {
    return tags.split(',').map(t => t.trim()).filter(Boolean);
  }

  async function submitUrl(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true); setResult(null); setError(null);
    try {
      const r = await postIngest({ source: url, source_type: sourceType, domain, tags: tagList() });
      setResult(r);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function submitText(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setLoading(true); setResult(null); setError(null);
    try {
      const r = await postIngestText(text, textTitle, sourceType, domain, tagList());
      setResult(r);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function submitFile(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setLoading(true); setResult(null); setError(null);
    try {
      const r = await postUpload(file, sourceType, domain, tagList());
      setResult(r);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <Card>
        <Card.Header>
          <Card.Title className="text-lg font-semibold text-gray-100">Ingest a Source</Card.Title>
        </Card.Header>
        <Card.Content className="space-y-6">
          {/* Tabs */}
          <SharedElementTransition>
          <Tabs
            selectedKey={tab}
            onSelectionChange={key => setTab(key as Tab)}
            aria-label="Ingest method"
          >
            <Tabs.ListContainer>
              <Tabs.List>
                <Tabs.Tab id="url">URL / Path</Tabs.Tab>
                <Tabs.Tab id="file">Upload File</Tabs.Tab>
                <Tabs.Tab id="text">Paste Text</Tabs.Tab>
              </Tabs.List>
              <Tabs.Indicator />
            </Tabs.ListContainer>

            <Tabs.Panel id="url" className="pt-4">
              <SharedFields
                sourceType={sourceType} setSourceType={setSourceType}
                domain={domain} setDomain={setDomain}
                tags={tags} setTags={setTags}
              />
              <form onSubmit={submitUrl} className="space-y-4 mt-4">
                <div>
                  <label htmlFor="source-url" className="block text-xs font-medium text-gray-300 mb-1">
                    URL or server file path
                  </label>
                  <Input
                    id="source-url"
                    type="text"
                    value={url}
                    onChange={e => setUrl(e.target.value)}
                    placeholder="https://example.com/article"
                    required
                    fullWidth
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    Paste a URL, or a path relative to the server's working directory.
                    To upload a local file use the <strong className="text-gray-400">Upload File</strong> tab.
                  </p>
                </div>
                <Button type="submit" variant="primary" fullWidth isDisabled={loading}>
                  {loading ? 'Ingesting…' : 'Ingest Source'}
                </Button>
              </form>
            </Tabs.Panel>

            <Tabs.Panel id="file" className="pt-4">
              <SharedFields
                sourceType={sourceType} setSourceType={setSourceType}
                domain={domain} setDomain={setDomain}
                tags={tags} setTags={setTags}
              />
              <form onSubmit={submitFile} className="space-y-4 mt-4">
                <div>
                  <label htmlFor="file-input" className="block text-xs font-medium text-gray-300 mb-1">
                    Choose file
                  </label>
                  <input
                    id="file-input"
                    ref={fileInputRef}
                    type="file"
                    accept=".md,.txt,.pdf,.html,.rst,.json,.csv"
                    onChange={e => setFile(e.target.files?.[0] ?? null)}
                    required
                    className="block w-full text-sm text-gray-300 cursor-pointer
                               file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0
                               file:text-sm file:font-medium file:bg-indigo-600 file:text-white
                               hover:file:bg-indigo-500 focus:outline-hidden"
                  />
                  <div className="mt-1 flex items-center justify-between">
                    <p className="text-xs text-gray-500">Supports .md, .txt, .pdf, .html, .rst, .json, .csv</p>
                    {file && <TokenBadge tokens={estimateTokens(file.size)} />}
                  </div>
                  {file && (
                    <p className="mt-0.5 text-xs text-gray-600">
                      {(file.size / 1024).toFixed(1)} KB · token estimate assumes plain text
                    </p>
                  )}
                </div>
                <Button type="submit" variant="primary" fullWidth isDisabled={loading || !file}>
                  {loading ? 'Uploading…' : 'Upload & Ingest'}
                </Button>
              </form>
            </Tabs.Panel>

            <Tabs.Panel id="text" className="pt-4">
              <SharedFields
                sourceType={sourceType} setSourceType={setSourceType}
                domain={domain} setDomain={setDomain}
                tags={tags} setTags={setTags}
              />
              <form onSubmit={submitText} className="space-y-4 mt-4">
                <div>
                  <label htmlFor="text-title" className="block text-xs font-medium text-gray-300 mb-1">
                    Title <span className="text-gray-500 font-normal">(optional — helps the LLM name the page)</span>
                  </label>
                  <Input
                    id="text-title"
                    type="text"
                    value={textTitle}
                    onChange={e => setTextTitle(e.target.value)}
                    placeholder="My note on X"
                    fullWidth
                  />
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label htmlFor="text-body" className="block text-xs font-medium text-gray-300">
                      Content
                    </label>
                    <TokenBadge tokens={estimateTokens(text.length)} />
                  </div>
                  <TextArea
                    id="text-body"
                    value={text}
                    onChange={e => setText(e.target.value)}
                    placeholder="Paste or type your text here…"
                    required
                    rows={10}
                    fullWidth
                    className="font-mono leading-relaxed"
                  />
                  <p className="mt-1 text-xs text-gray-600">
                    {text.length.toLocaleString()} chars · estimate only (actual depends on tokenizer)
                  </p>
                </div>
                <Button type="submit" variant="primary" fullWidth isDisabled={loading || !text.trim()}>
                  {loading ? 'Ingesting…' : 'Ingest Text'}
                </Button>
              </form>
            </Tabs.Panel>
          </Tabs>
          </SharedElementTransition>

          {/* Results */}
          {(result || error) && (
            <div className="space-y-3">
              {error  && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
              {result && <ResultCard result={result} />}
            </div>
          )}
        </Card.Content>
      </Card>
    </div>
  );
}
