import type { GraphEdge } from './types';

export type EdgeKind = 'wikilink' | 'shared';

/** Normalize an edge's kind; legacy/missing types default to a wikilink. */
export function edgeType(e: GraphEdge): EdgeKind {
  return e.type ?? 'wikilink';
}

/**
 * Edges to render: drops shared-concept edges when the toggle is off, and drops
 * any edge whose endpoints aren't both in the visible node set (domain/text filters).
 */
export function visibleEdges(
  edges: GraphEdge[],
  nodeIds: Set<string>,
  showShared: boolean,
): GraphEdge[] {
  return edges.filter(
    e =>
      (showShared || edgeType(e) !== 'shared') &&
      nodeIds.has(e.source) &&
      nodeIds.has(e.target),
  );
}
