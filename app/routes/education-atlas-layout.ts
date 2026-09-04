import type { GraphEdge, GraphNode } from "./home";

export type AchievementAtlasLandmarkKind = "lighthouse" | "monument" | "highland" | "camp" | "reef" | "island";
export type AchievementAtlasRouteKind = "trail" | "stream" | "meadow";

export interface AchievementAtlasPoint {
  x: number;
  y: number;
}
export interface AchievementAtlasNetworkEdge {
  key: string;
  edge: GraphEdge;
  routeKind: AchievementAtlasRouteKind;
}

export interface AchievementAtlasNetworkLayout {
  width: number;
  height: number;
  positions: Record<number, AchievementAtlasPoint>;
  landmarkKinds: Record<number, AchievementAtlasLandmarkKind>;
  degreeByNode: Record<number, number>;
  neighborIdsByNode: Record<number, number[]>;
  edges: AchievementAtlasNetworkEdge[];
}

export interface AchievementAtlasRouteCurve {
  path: string;
  arrow: AchievementAtlasPoint & { angle: number };
}

const ROUTE_ANCHOR_RADIUS = 64;
const LANDMARK_FOOTPRINT_RADIUS = 102;
const COMPONENT_GAP = 178;
const CANVAS_PADDING = 150;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function nodeOrder(left: GraphNode, right: GraphNode) {
  const leftIndex = Number.isFinite(left.node_index_in_doc) ? left.node_index_in_doc! : Number.MAX_SAFE_INTEGER;
  const rightIndex = Number.isFinite(right.node_index_in_doc) ? right.node_index_in_doc! : Number.MAX_SAFE_INTEGER;
  return leftIndex - rightIndex || left.id - right.id;
}

function edgeSort(left: GraphEdge, right: GraphEdge) {
  return left.from - right.from
    || left.to - right.to
    || String(left.label || "").localeCompare(String(right.label || ""))
    || String(left.description || "").localeCompare(String(right.description || ""))
    || String(left.strength || "").localeCompare(String(right.strength || ""));
}

function hashText(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function edgeIdentity(edge: GraphEdge, duplicateIndex: number) {
  return `${edge.from}>${edge.to}:${edge.label || ""}:${edge.description || ""}:${edge.strength || ""}:${duplicateIndex}`;
}

function typeText(node: Pick<GraphNode, "node_type">) {
  return String(node.node_type || "").toLowerCase();
}

function isDefinitionNode(node: Pick<GraphNode, "node_type">) {
  return /definition|定义|axiom|公理|notation|记号/.test(typeText(node));
}

function isTheoremNode(node: Pick<GraphNode, "node_type">) {
  return /theorem|定理|proposition|命题|lemma|引理|corollary|推论/.test(typeText(node));
}

function isExampleNode(node: Pick<GraphNode, "node_type">) {
  return /example|例子|示例|反例|counterexample|exercise|习题/.test(typeText(node));
}

export function deriveAchievementAtlasLandmarkKind(
  node: Pick<GraphNode, "node_type">,
  degree: number,
  isComponentHub = false,
): AchievementAtlasLandmarkKind {
  if (degree === 0) return "reef";
  if (isComponentHub) return "lighthouse";
  if (isDefinitionNode(node)) return "monument";
  if (isTheoremNode(node)) return "highland";
  if (isExampleNode(node)) return "camp";
  return "island";
}

export function deriveAchievementAtlasRouteKind(key: string): AchievementAtlasRouteKind {
  const kind = hashText(key) % 3;
  return kind === 0 ? "trail" : kind === 1 ? "stream" : "meadow";
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

function connectedComponents(nodes: GraphNode[], neighborIdsByNode: Record<number, number[]>) {
  const nodeById = new Map(nodes.map(node => [node.id, node]));
  const visited = new Set<number>();
  const components: GraphNode[][] = [];
  [...nodes].sort(nodeOrder).forEach(node => {
    if (visited.has(node.id)) return;
    const queue = [node.id];
    const component: GraphNode[] = [];
    visited.add(node.id);
    while (queue.length) {
      const currentId = queue.shift()!;
      const current = nodeById.get(currentId);
      if (current) component.push(current);
      (neighborIdsByNode[currentId] || []).forEach(nextId => {
        if (!visited.has(nextId)) {
          visited.add(nextId);
          queue.push(nextId);
        }
      });
    }
    components.push(component.sort(nodeOrder));
  });
  return components;
}

function relaxComponent(
  nodes: GraphNode[],
  edges: AchievementAtlasNetworkEdge[],
  degreeByNode: Record<number, number>,
): Record<number, AchievementAtlasPoint> {
  const positions: Record<number, AchievementAtlasPoint> = {};
  const ordered = [...nodes].sort((left, right) => degreeByNode[right.id] - degreeByNode[left.id] || nodeOrder(left, right));
  ordered.forEach((node, index) => {
    const angle = index * GOLDEN_ANGLE;
    const radius = 40 + Math.sqrt(index + 1) * 64;
    positions[node.id] = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
  if (ordered.length < 2) return positions;

  const componentNodeIds = new Set(ordered.map(node => node.id));
  const componentEdges = edges.filter(item => componentNodeIds.has(item.edge.from) && componentNodeIds.has(item.edge.to));
  const iterations = ordered.length > 250 ? 42 : ordered.length > 120 ? 76 : 136;
  const minimumDistance = ordered.length > 250 ? 148 : ordered.length > 180 ? 168 : 208;

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const forces: Record<number, AchievementAtlasPoint> = {};
    ordered.forEach(node => { forces[node.id] = { x: 0, y: 0 }; });

    for (let leftIndex = 0; leftIndex < ordered.length; leftIndex += 1) {
      const left = ordered[leftIndex];
      for (let rightIndex = leftIndex + 1; rightIndex < ordered.length; rightIndex += 1) {
        const right = ordered[rightIndex];
        const leftPosition = positions[left.id];
        const rightPosition = positions[right.id];
        let dx = rightPosition.x - leftPosition.x;
        let dy = rightPosition.y - leftPosition.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 0.001) {
          const angle = (hashText(`${left.id}:${right.id}`) % 360) * Math.PI / 180;
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          distance = 1;
        }
        const separation = distance < minimumDistance
          ? (minimumDistance - distance) * .17
          : Math.min(3, 2850 / (distance * distance));
        const forceX = dx / distance * separation;
        const forceY = dy / distance * separation;
        forces[left.id].x -= forceX;
        forces[left.id].y -= forceY;
        forces[right.id].x += forceX;
        forces[right.id].y += forceY;
      }
    }

    componentEdges.forEach(({ edge }) => {
      const from = positions[edge.from];
      const to = positions[edge.to];
      let dx = to.x - from.x;
      let dy = to.y - from.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const desiredLength = 218 + Math.min(64, (degreeByNode[edge.from] + degreeByNode[edge.to]) * 4);
      const pull = (distance - desiredLength) * .014;
      dx = dx / distance * pull;
      dy = dy / distance * pull;
      forces[edge.from].x += dx;
      forces[edge.from].y += dy;
      forces[edge.to].x -= dx;
      forces[edge.to].y -= dy;
    });

    ordered.forEach(node => {
      const position = positions[node.id];
      const force = forces[node.id];
      position.x += clamp(force.x - position.x * .0009, -18, 18);
      position.y += clamp(force.y - position.y * .0009, -18, 18);
    });
  }
  return positions;
}

function componentBounds(nodes: GraphNode[], positions: Record<number, AchievementAtlasPoint>) {
  const xs = nodes.map(node => positions[node.id].x);
  const ys = nodes.map(node => positions[node.id].y);
  return {
    minX: Math.min(...xs) - LANDMARK_FOOTPRINT_RADIUS,
    maxX: Math.max(...xs) + LANDMARK_FOOTPRINT_RADIUS,
    minY: Math.min(...ys) - LANDMARK_FOOTPRINT_RADIUS,
    maxY: Math.max(...ys) + LANDMARK_FOOTPRINT_RADIUS,
  };
}

export function buildAchievementAtlasNetworkLayout(nodes: GraphNode[], edges: GraphEdge[]): AchievementAtlasNetworkLayout {
  const orderedNodes = [...nodes].sort(nodeOrder);
  if (!orderedNodes.length) {
    return { width: 820, height: 560, positions: {}, landmarkKinds: {}, degreeByNode: {}, neighborIdsByNode: {}, edges: [] };
  }
  const nodeIds = new Set(orderedNodes.map(node => node.id));
  const sortedEdges = edges.filter(edge => nodeIds.has(edge.from) && nodeIds.has(edge.to)).sort(edgeSort);
  const duplicateCounts = new Map<string, number>();
  const atlasEdges = sortedEdges.map(edge => {
    const base = `${edge.from}>${edge.to}:${edge.label || ""}:${edge.description || ""}:${edge.strength || ""}`;
    const duplicateIndex = duplicateCounts.get(base) || 0;
    duplicateCounts.set(base, duplicateIndex + 1);
    const key = edgeIdentity(edge, duplicateIndex);
    return { key, edge, routeKind: deriveAchievementAtlasRouteKind(key) };
  });
  const degreeByNode: Record<number, number> = {};
  const neighbors = new Map<number, Set<number>>();
  orderedNodes.forEach(node => {
    degreeByNode[node.id] = 0;
    neighbors.set(node.id, new Set());
  });
  atlasEdges.forEach(({ edge }) => {
    degreeByNode[edge.from] += 1;
    degreeByNode[edge.to] += 1;
    if (edge.from !== edge.to) {
      neighbors.get(edge.from)?.add(edge.to);
      neighbors.get(edge.to)?.add(edge.from);
    }
  });
  const neighborIdsByNode = Object.fromEntries(orderedNodes.map(node => [node.id, [...(neighbors.get(node.id) || [])].sort((left, right) => left - right)])) as Record<number, number[]>;
  const components = connectedComponents(orderedNodes, neighborIdsByNode);
  const targetRowWidth = Math.max(1040, Math.ceil(Math.sqrt(orderedNodes.length)) * 300);
  const positions: Record<number, AchievementAtlasPoint> = {};
  const landmarkKinds: Record<number, AchievementAtlasLandmarkKind> = {};
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  let maxRight = 0;

  const componentLayouts = components.map(component => {
    const componentPositions = relaxComponent(component, atlasEdges, degreeByNode);
    const bounds = componentBounds(component, componentPositions);
    return {
      component,
      componentPositions,
      bounds,
      width: bounds.maxX - bounds.minX,
      height: bounds.maxY - bounds.minY,
    };
  }).sort((left, right) => right.width * right.height - left.width * left.height || nodeOrder(left.component[0], right.component[0]));

  componentLayouts.forEach(({ component, componentPositions, bounds, width, height }) => {
    if (cursorX > 0 && cursorX + width > targetRowWidth) {
      cursorX = 0;
      cursorY += rowHeight + COMPONENT_GAP;
      rowHeight = 0;
    }
    const hub = component.length >= 4
      ? [...component].sort((left, right) => degreeByNode[right.id] - degreeByNode[left.id] || nodeOrder(left, right))[0]
      : undefined;
    component.forEach(node => {
      const position = componentPositions[node.id];
      positions[node.id] = {
        x: cursorX + position.x - bounds.minX + CANVAS_PADDING,
        y: cursorY + position.y - bounds.minY + CANVAS_PADDING,
      };
      landmarkKinds[node.id] = deriveAchievementAtlasLandmarkKind(node, degreeByNode[node.id], hub?.id === node.id && degreeByNode[node.id] > 1);
    });
    cursorX += width + COMPONENT_GAP;
    rowHeight = Math.max(rowHeight, height);
    maxRight = Math.max(maxRight, cursorX - COMPONENT_GAP);
  });

  return {
    width: Math.max(1040, maxRight + CANVAS_PADDING * 2),
    height: Math.max(700, cursorY + rowHeight + CANVAS_PADDING * 2),
    positions,
    landmarkKinds,
    degreeByNode,
    neighborIdsByNode,
    edges: atlasEdges,
  };
}

export function buildAchievementAtlasRouteCurve(
  from: AchievementAtlasPoint,
  to: AchievementAtlasPoint,
  key: string,
): AchievementAtlasRouteCurve {
  let dx = to.x - from.x;
  let dy = to.y - from.y;
  let distance = Math.hypot(dx, dy);
  if (distance < 1) {
    const loopRadius = ROUTE_ANCHOR_RADIUS + 28;
    return {
      path: `M ${from.x + ROUTE_ANCHOR_RADIUS} ${from.y} C ${from.x + loopRadius} ${from.y - loopRadius}, ${from.x - loopRadius} ${from.y - loopRadius}, ${from.x - ROUTE_ANCHOR_RADIUS} ${from.y}`,
      arrow: { x: from.x - ROUTE_ANCHOR_RADIUS, y: from.y, angle: 90 },
    };
  }
  const unitX = dx / distance;
  const unitY = dy / distance;
  const normalX = -unitY;
  const normalY = unitX;
  const bendDirection = hashText(key) % 2 ? 1 : -1;
  const bend = clamp(distance * .19, 30, 86) * bendDirection;
  const start = { x: from.x + unitX * ROUTE_ANCHOR_RADIUS, y: from.y + unitY * ROUTE_ANCHOR_RADIUS };
  const end = { x: to.x - unitX * ROUTE_ANCHOR_RADIUS, y: to.y - unitY * ROUTE_ANCHOR_RADIUS };
  const controlOne = { x: start.x + (end.x - start.x) * .32 + normalX * bend, y: start.y + (end.y - start.y) * .32 + normalY * bend };
  const controlTwo = { x: start.x + (end.x - start.x) * .68 + normalX * bend, y: start.y + (end.y - start.y) * .68 + normalY * bend };
  const arrowAngle = Math.atan2(end.y - controlTwo.y, end.x - controlTwo.x) * 180 / Math.PI;
  return {
    path: `M ${start.x} ${start.y} C ${controlOne.x} ${controlOne.y}, ${controlTwo.x} ${controlTwo.y}, ${end.x} ${end.y}`,
    arrow: { x: end.x, y: end.y, angle: arrowAngle },
  };
}

export function deriveAchievementAtlasFocus(nodeId: number, layout: AchievementAtlasNetworkLayout) {
  const nodeIds = new Set([nodeId, ...(layout.neighborIdsByNode[nodeId] || [])]);
  const edgeKeys = new Set(layout.edges
    .filter(item => item.edge.from === nodeId || item.edge.to === nodeId)
    .map(item => item.key));
  return { nodeIds, edgeKeys };
}

