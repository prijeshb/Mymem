import * as d3 from 'd3';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchGraph } from '../lib/api';
import type { GraphData, GraphNode } from '../lib/types';
import { ALL_DOMAINS } from '../lib/types';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { Button, Input } from '@heroui/react';

const DOMAIN_COLORS: Record<string, string> = {
  tech: '#6366f1', spiritual: '#a78bfa', finance: '#34d399',
  health: '#f472b6', reminder: '#fb923c', research: '#38bdf8',
  personal: '#facc15', creative: '#f87171', business: '#4ade80', misc: '#6b7280',
};

type D3Node = GraphNode & d3.SimulationNodeDatum;

export function GraphPage() {
  const svgRef    = useRef<SVGSVGElement>(null);
  const navigate  = useNavigate();
  const [data, setData]                 = useState<GraphData | null>(null);
  const [textFilter, setTextFilter]     = useState('');
  const [domainFilter, setDomainFilter] = useState('');
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);

  useEffect(() => {
    fetchGraph()
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!data || !svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const rect   = svgRef.current.getBoundingClientRect();
    const width  = rect.width  || 800;
    const height = rect.height || 600;

    const nodeFilter = (n: GraphNode) =>
      (!domainFilter || n.domain === domainFilter) &&
      (!textFilter   || n.id.toLowerCase().includes(textFilter.toLowerCase()));

    const filteredNodes = data.nodes.filter(nodeFilter);
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredEdges = data.edges.filter(
      e => nodeIds.has(e.source as string) && nodeIds.has(e.target as string),
    );

    const nodes: D3Node[] = filteredNodes.map(n => ({ ...n }));
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const edges = filteredEdges
      .map(e => ({
        source: nodeMap.get(e.source as string)!,
        target: nodeMap.get(e.target as string)!,
      }))
      .filter(e => e.source && e.target);

    svg.append('defs').append('marker')
      .attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
      .attr('refX', 18).attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', '#6366f1');

    const g = svg.append('g');

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 8])
      .on('zoom', e => g.attr('transform', e.transform.toString()));
    svg.call(zoom);

    const sim = d3.forceSimulation<D3Node>(nodes)
      .force('link', d3.forceLink<D3Node, d3.SimulationLinkDatum<D3Node>>(edges).id(d => d.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide(20));

    const link = g.append('g').selectAll('line')
      .data(edges).join('line')
      .attr('stroke', '#374151').attr('stroke-width', 1)
      .attr('marker-end', 'url(#arrow)');

    const node = g.append('g').selectAll<SVGCircleElement, D3Node>('circle')
      .data(nodes).join('circle')
      .attr('r', 8)
      .attr('fill', d => DOMAIN_COLORS[d.domain] ?? DOMAIN_COLORS['misc'])
      .attr('stroke', '#1f2937').attr('stroke-width', 2)
      .style('cursor', 'pointer')
      .on('click', (_, d) => navigate(`/wiki/${d.slug}`))
      .on('mouseover', function () { d3.select(this).attr('r', 11); })
      .on('mouseout',  function () { d3.select(this).attr('r', 8); })
      .call(d3.drag<SVGCircleElement, D3Node>()
        .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }),
      );

    const label = g.append('g').selectAll('text')
      .data(nodes).join('text')
      .attr('font-size', 10).attr('fill', '#9ca3af')
      .attr('dy', -12).attr('text-anchor', 'middle')
      .text(d => d.id.length > 20 ? d.id.slice(0, 18) + '…' : d.id)
      .style('pointer-events', 'none');

    sim.on('tick', () => {
      link
        .attr('x1', d => (d.source as D3Node).x ?? 0)
        .attr('y1', d => (d.source as D3Node).y ?? 0)
        .attr('x2', d => (d.target as D3Node).x ?? 0)
        .attr('y2', d => (d.target as D3Node).y ?? 0);
      node.attr('cx', d => d.x ?? 0).attr('cy', d => d.y ?? 0);
      label.attr('x', d => d.x ?? 0).attr('y', d => d.y ?? 0);
    });

    return () => { sim.stop(); };
  }, [data, textFilter, domainFilter, navigate]);

  return (
    <div className="flex flex-col gap-4" style={{ height: 'calc(100vh - 8rem)' }}>
      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <Input
          value={textFilter}
          onChange={e => setTextFilter(e.target.value)}
          placeholder="Filter nodes…"
          aria-label="Filter graph nodes"
          className="w-48"
        />
        <select
          value={domainFilter}
          onChange={e => setDomainFilter(e.target.value)}
          aria-label="Filter by domain"
          className="bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100
                     focus:outline-hidden focus:ring-2 focus:ring-indigo-400 [color-scheme:dark]"
        >
          <option value="">All domains</option>
          {ALL_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        {data && (
          <span className="text-xs text-gray-500 ml-auto">
            {data.nodes.length} nodes · {data.edges.length} edges · Click to open · Drag to move · Scroll to zoom
          </span>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3">
        {ALL_DOMAINS.map(d => (
          <Button
            key={d}
            variant="ghost"
            size="sm"
            onPress={() => setDomainFilter(prev => prev === d ? '' : d)}
            className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border transition-colors
              ${domainFilter === d ? 'border-white/40 opacity-100' : 'border-transparent opacity-60 hover:opacity-100'}`}
          >
            <span
              className="w-2.5 h-2.5 rounded-full flex-shrink-0"
              style={{ background: DOMAIN_COLORS[d] }}
            />
            <span className="text-gray-300 text-xs">{d}</span>
          </Button>
        ))}
      </div>

      {/* Graph */}
      <div className="flex-1 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <LoadingSpinner label="Loading graph…" />
          </div>
        )}
        {error && (
          <div className="p-4"><ErrorBanner message={error} /></div>
        )}
        {!loading && !error && data?.nodes.length === 0 && (
          <div className="flex items-center justify-center h-full text-gray-600 text-sm">
            No pages yet — ingest some sources to see the graph
          </div>
        )}
        <svg
          ref={svgRef}
          className="w-full h-full"
          aria-label="Knowledge graph"
          role="img"
        />
      </div>
    </div>
  );
}
