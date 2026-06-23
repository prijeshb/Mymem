import { describe, expect, it } from 'vitest';
import { edgeType, visibleEdges } from './graph';
import type { GraphEdge } from './types';

const visible = new Set(['A', 'B', 'C']);

describe('edgeType', () => {
  it('defaults missing/legacy type to wikilink', () => {
    expect(edgeType({ source: 'A', target: 'B' })).toBe('wikilink');
  });
  it('preserves an explicit shared type', () => {
    expect(edgeType({ source: 'A', target: 'B', type: 'shared' })).toBe('shared');
  });
});

describe('visibleEdges', () => {
  const edges: GraphEdge[] = [
    { source: 'A', target: 'B', type: 'wikilink' },
    { source: 'A', target: 'C', type: 'shared', weight: 2, via: ['X'] },
    { source: 'B', target: 'C' },                  // legacy: no type -> treated as wikilink
    { source: 'A', target: 'Z', type: 'shared' },  // Z not visible -> always dropped
  ];

  it('keeps shared edges when the toggle is on (but drops invisible endpoints)', () => {
    const out = visibleEdges(edges, visible, true);
    expect(out).toHaveLength(3);                   // A-B, A-C, B-C  (A-Z dropped)
    expect(out.some(e => e.type === 'shared')).toBe(true);
    expect(out.some(e => e.target === 'Z')).toBe(false);
  });

  it('drops shared edges when the toggle is off', () => {
    const out = visibleEdges(edges, visible, false);
    expect(out).toHaveLength(2);                   // A-B + legacy B-C, both wikilink
    expect(out.every(e => edgeType(e) === 'wikilink')).toBe(true);
  });

  it('always drops edges whose endpoints are not both visible', () => {
    expect(visibleEdges(edges, new Set(['A']), true)).toHaveLength(0);
  });
});
