// ── GraphStudio logic core ──────────────────────────────────────────────────
// Pure (non-React) helpers for the redesigned natural-language graph experience:
//   · salience scoring  → visual hierarchy
//   · level-of-detail   → which nodes show labels
//   · layout engines    → several user-selectable node orderings
//   · anchor index      → authoritative node↔paragraph mapping (jump + recall)
//   · settings          → persisted personalization
//
// Kept framework-free so it is trivially testable and reused by GraphStudio.tsx.

import type { GraphNode, GraphEdge, GraphResult, NodeLanguage } from "./home";
import { labelText } from "./home";
import {
  parseMdBlocks, normalizeMathStr, extractLabelNumber,
  BLOCK_INTRO_PATTERN, type MdBlock,
} from "./markdown";
import {
  nodeStatementText,
  normalizeSourceMatch as normMatch,
  sourceStatementBlockRange,
} from "./source-matching";

// ── Node palette (self-contained so this module never breaks the classic view) ──
// Each type carries a light bg AND a dark-tinted bg so nodes read well in both
// themes (dark uses a deep hue-tinted fill + colored border + light text, instead
// of glaring near-white cards on a dark canvas).
export interface StudioNodeStyle { border: string; borderDark: string; bg: string; bgDark: string; shape: string; glyph: string; }
export const STUDIO_NODE_STYLES: Record<string, StudioNodeStyle> = {
  "定义": { border: "#94a3b8", borderDark: "#9fb0c4", bg: "#f8fafc", bgDark: "#2c3138", shape: "box",     glyph: "D" },
  "公理": { border: "#c8a23a", borderDark: "#d9b659", bg: "#fffbef", bgDark: "#37301c", shape: "diamond", glyph: "A" },
  "定理": { border: "#3f9d6b", borderDark: "#5fb98a", bg: "#eef9f1", bgDark: "#1e3329", shape: "ellipse", glyph: "T" },
  "引理": { border: "#5b8fe0", borderDark: "#79a8ee", bg: "#eff5fe", bgDark: "#223047", shape: "box",     glyph: "L" },
  "推论": { border: "#9b7fd4", borderDark: "#b49ee6", bg: "#f5f1fe", bgDark: "#2e2845", shape: "box",     glyph: "C" },
  "性质": { border: "#4fa3ab", borderDark: "#6fc0c8", bg: "#eef8f9", bgDark: "#1d3438", shape: "box",     glyph: "P" },
  "命题": { border: "#c39b5e", borderDark: "#d6b277", bg: "#fdf7ec", bgDark: "#352d20", shape: "box",     glyph: "R" },
  "例子": { border: "#92a06f", borderDark: "#aab985", bg: "#f4f7ec", bgDark: "#2b301f", shape: "box",     glyph: "E" },
};
const FALLBACK_STYLE: StudioNodeStyle = { border: "#b6bcc4", borderDark: "#9aa0a8", bg: "#f7f8fa", bgDark: "#2e2c29", shape: "box", glyph: "?" };
export const STUDIO_NODE_TYPES = Object.keys(STUDIO_NODE_STYLES);
export function studioStyle(type: string): StudioNodeStyle {
  return STUDIO_NODE_STYLES[type] ?? FALLBACK_STYLE;
}
// Border colour is the canonical type colour; the dark variant is only used for
// node fills/strokes on the dark canvas. Legends/dots keep the light border hue.
export function studioColor(type: string): string { return studioStyle(type).border; }

// Type weight for salience (theorems/definitions matter most for navigation).
const TYPE_WEIGHT: Record<string, number> = {
  "定理": 1.0, "公理": 0.95, "定义": 0.9, "引理": 0.78,
  "命题": 0.72, "推论": 0.66, "性质": 0.6, "例子": 0.42,
};

// ── Salience ────────────────────────────────────────────────────────────────
// salience ∈ ~[0,1.2]; higher = more central → bigger, labelled first.
export function computeSalience(nodes: GraphNode[], edges: GraphEdge[]): Record<number, number> {
  const deg: Record<number, number> = {};
  nodes.forEach(n => { deg[n.id] = 0; });
  edges.forEach(e => { if (deg[e.from] !== undefined) deg[e.from]++; if (deg[e.to] !== undefined) deg[e.to]++; });
  const maxDeg = Math.max(1, ...Object.values(deg));
  const out: Record<number, number> = {};
  for (const n of nodes) {
    const w = TYPE_WEIGHT[n.node_type] ?? 0.5;
    const dNorm = Math.log1p(deg[n.id]) / Math.log1p(maxDeg);
    const numbered = /\d/.test(n.label || "") ? 0.15 : 0;
    out[n.id] = 0.5 * w + 0.5 * dNorm + numbered;
  }
  return out;
}

// Major nodes (labelled) = top `density` fraction by salience. density∈[0,1].
export function majorNodeSet(
  nodes: GraphNode[], salience: Record<number, number>, density: number,
): Set<number> {
  const ranked = [...nodes].sort((a, b) => (salience[b.id] ?? 0) - (salience[a.id] ?? 0));
  const count = Math.max(1, Math.ceil(ranked.length * Math.min(1, Math.max(0.05, density))));
  return new Set(ranked.slice(0, count).map(n => n.id));
}

// ── Canvas labels ───────────────────────────────────────────────────────────
function clip(s: string, n: number): string { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

const GENERIC = new Set(["定义","定理","引理","推论","公理","命题","性质","例子","theorem","definition","lemma","corollary","axiom","proposition","example"]);
// Compact on-canvas label: the informative concept title (most readable); fall
// back to the numbered reference label only when no real title exists.
export function studioLabel(n: GraphNode, lang: NodeLanguage): string {
  const zh = labelText(n.title_zh || "").trim();
  const en = labelText(n.title_en || "").trim();
  const base = lang === "en" ? (en || zh) : (zh || en);
  if (base && base.length >= 2 && !GENERIC.has(base.toLowerCase())) return clip(base, lang === "en" ? 36 : 22);
  const num = labelText((n.label || "").trim());
  return clip(num || base || `节点${n.id}`, 28);
}

// ── Layout engines ──────────────────────────────────────────────────────────
export type StudioLayout = "reading" | "swimlane" | "dag" | "force";
export type Pos = { x: number; y: number };

const docIdx = (n: GraphNode) => n.node_index_in_doc ?? n.id;

// Reading order: serpentine grid that strictly follows document order.
// `order` (when given) is the authoritative list of node ids in document order —
// derived from the source-text anchor index, so it reflects the real prose order
// rather than the weak node_index_in_doc fallback. Placement is boustrophedon:
// row 0 reads L→R, row 1 R→L, row 2 L→R … so consecutive document items always
// land in physically adjacent cells.
export function layoutReading(nodes: GraphNode[], order?: number[], colW = 230, rowH = 160): Record<number, Pos> {
  const byId = new Map(nodes.map(n => [n.id, n]));
  let ordered: GraphNode[];
  if (order && order.length) {
    const seen = new Set<number>();
    ordered = [];
    for (const id of order) { const n = byId.get(id); if (n && !seen.has(id)) { ordered.push(n); seen.add(id); } }
    // Safety: append any node missing from `order` (e.g. unmatched) in doc-idx order.
    for (const n of [...nodes].sort((a, b) => docIdx(a) - docIdx(b))) if (!seen.has(n.id)) ordered.push(n);
  } else {
    ordered = [...nodes].sort((a, b) => docIdx(a) - docIdx(b));
  }
  const cols = Math.max(4, Math.round(Math.sqrt(ordered.length * 1.7)));
  const pos: Record<number, Pos> = {};
  ordered.forEach((n, i) => {
    const row = Math.floor(i / cols);
    let col = i % cols;
    if (row % 2 === 1) col = cols - 1 - col;      // boustrophedon: snake back
    pos[n.id] = { x: col * colW, y: row * rowH };
  });
  return pos;
}

// Swimlanes: one vertical column per node type (labels run across the top),
// same-type nodes stacked top→bottom in document order.
export function layoutSwimlane(nodes: GraphNode[], colW = 230, rowH = 95): { pos: Record<number, Pos>; lanes: string[] } {
  const present = STUDIO_NODE_TYPES.filter(t => nodes.some(n => n.node_type === t));
  const extras = [...new Set(nodes.map(n => n.node_type))].filter(t => !present.includes(t));
  const lanes = [...present, ...extras];
  const laneOf = new Map(lanes.map((t, i) => [t, i]));
  const rowOf = new Map<number, number>();          // rank within its own type column
  const seen = new Map<string, number>();
  for (const n of [...nodes].sort((a, b) => docIdx(a) - docIdx(b))) {
    const r = seen.get(n.node_type) ?? 0;
    rowOf.set(n.id, r);
    seen.set(n.node_type, r + 1);
  }
  const pos: Record<number, Pos> = {};
  for (const n of nodes) {
    pos[n.id] = { x: (laneOf.get(n.node_type) ?? 0) * colW, y: (rowOf.get(n.id) ?? 0) * rowH };
  }
  return { pos, lanes };
}

// Dependency DAG: depth (longest prerequisite path) on Y, doc order within depth on X.
export function computeDepthsLocal(nodes: GraphNode[], edges: GraphEdge[]): Record<number, number> {
  const inDeg: Record<number, number> = {}; const adj: Record<number, number[]> = {};
  nodes.forEach(n => { inDeg[n.id] = 0; adj[n.id] = []; });
  edges.forEach(e => { if (inDeg[e.to] !== undefined) inDeg[e.to]++; (adj[e.from] ??= []).push(e.to); });
  const depths: Record<number, number> = {};
  const q = nodes.filter(n => !inDeg[n.id]).map(n => n.id);
  q.forEach(id => { depths[id] = 0; });
  while (q.length) {
    const cur = q.shift()!;
    for (const nx of adj[cur] ?? []) {
      const d = depths[cur] + 1;
      if (depths[nx] === undefined || depths[nx] < d) { depths[nx] = d; q.push(nx); }
    }
  }
  const maxD = Math.max(0, ...Object.values(depths));
  nodes.forEach(n => { depths[n.id] ??= maxD + 1; });
  return depths;
}

export function layoutDag(nodes: GraphNode[], depths: Record<number, number>, levelSep = 165, nodeSep = 215): Record<number, Pos> {
  const byDepth = new Map<number, GraphNode[]>();
  for (const n of nodes) { const d = depths[n.id] ?? 0; (byDepth.get(d) ?? byDepth.set(d, []).get(d)!).push(n); }
  const pos: Record<number, Pos> = {};
  for (const [depth, g] of byDepth) {
    g.sort((a, b) => docIdx(a) - docIdx(b));
    const totalW = (g.length - 1) * nodeSep;
    g.forEach((n, i) => { pos[n.id] = { x: -totalW / 2 + i * nodeSep, y: depth * levelSep }; });
  }
  return pos;
}

// ── Authoritative anchor index (graph ↔ source text) ────────────────────────
// The backend records each node's original `source_statement` as well as the
// shorter source_text. Rebuild the same block list and use the full statement
// first, so a source jump can recall the whole statement instead of one line.

function texEnvName(nodeType: string): string {
  return nodeType.replace(/\s+/g, "").replace(/\*$/, "").toLowerCase();
}

function texBlockEnv(text: string): string {
  const firstLine = text.split(/\n/, 1)[0] ?? text;
  const begin = /\\begin\s*\{\s*([A-Za-z*]+)\s*\}/.exec(firstLine);
  return begin ? begin[1].replace(/\*$/, "").toLowerCase() : "";
}

export interface AnchorIndex {
  nodeToBlocks: Map<number, number[]>;   // node id → block indices (primary first)
  blockToNodes: Map<number, number[]>;   // block index → node ids
  blocks: MdBlock[];
  coverage: number;                      // fraction of nodes with ≥1 block
  unmatched: number[];                   // node ids with no source block
}

export function buildAnchorIndex(nodes: GraphNode[], markdown: string): AnchorIndex {
  const blocks = parseMdBlocks(markdown);
  const normBlocks = blocks.map(b => ("text" in b ? normMatch((b as { text: string }).text) : ""));
  const nodeToBlocks = new Map<number, number[]>();
  const blockToNodes = new Map<number, number[]>();

  const link = (nodeId: number, blockIdx: number, primary = false) => {
    const arr = nodeToBlocks.get(nodeId) ?? [];
    if (!arr.includes(blockIdx)) { primary ? arr.unshift(blockIdx) : arr.push(blockIdx); nodeToBlocks.set(nodeId, arr); }
    const barr = blockToNodes.get(blockIdx) ?? [];
    if (!barr.includes(nodeId)) { barr.push(nodeId); blockToNodes.set(blockIdx, barr); }
  };

  for (const n of nodes) {
    let matched = false;

    // The original statement is the canonical source key. It can span a title,
    // paragraphs, and display equations, so link every matched source block.
    const statementText = nodeStatementText(n);
    const statementRange = sourceStatementBlockRange(blocks, statementText);
    if (statementRange) {
      for (let index = statementRange[0]; index <= statementRange[1]; index++) {
        link(n.id, index, index === statementRange[0]);
      }
      matched = true;
    }

    // TeX imports preserve their source key either as tex_label_key or label,
    // e.g. \label{lem-nested-ball} or \begin{definition}{...}{d1.1.1}.
    // It is the authoritative locator for a statement, including non-numeric keys.
    const texLabel = (n.tex_label_key || n.label || "").trim();
    if (!matched && texLabel) {
      const labelRe = new RegExp(`\\{\\s*${texLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\}`, "i");
      const env = texEnvName(n.node_type || "");
      for (let i = 0; i < blocks.length; i++) {
        const b = blocks[i]; if (!("text" in b)) continue;
        const t = (b as { text: string }).text;
        const blockEnv = texBlockEnv(t);
        if (labelRe.test(t) && (!env || !blockEnv || env === blockEnv)) {
          link(n.id, i, true);
          matched = true;
          break;
        }
      }
    }

    // A real node statement exists but did not match confidently. Do not let
    // broad title, number, or context fallbacks highlight an unrelated block.
    if (!matched && statementText) continue;

    // (1) Primary: exact source block via normalized inclusion.
    const src = normMatch(n.source_text || "");
    if (!matched && src.length >= 16) {
      const env = texEnvName(n.node_type || "");
      let best = -1, bestScore = Infinity;
      for (let i = 0; i < normBlocks.length; i++) {
        const nb = normBlocks[i];
        if (nb.length < 8) continue;
        if (nb.includes(src) || src.includes(nb)) {
          const b = blocks[i]; if (!("text" in b)) continue;
          const blockEnv = texBlockEnv((b as { text: string }).text);
          if (blockEnv === "introduction" && env !== "introduction") continue;
          // prefer the block whose length is closest to the source snippet
          const d = Math.abs(nb.length - src.length);
          const score = d
            + (blockEnv && env && blockEnv !== env ? 5000 : 0)
            - (blockEnv && env && blockEnv === env ? 5000 : 0);
          if (score < bestScore) { bestScore = score; best = i; }
        }
      }
      if (best >= 0) { link(n.id, best, true); matched = true; }
    }

    // (2) Numbered label inside an intro block ("定理 3.1", "Theorem 2").
    const num = extractLabelNumber(n.label || "");
    if (num) {
      const re = new RegExp(`\\b${num.replace(/\./g, "\\.")}\\b`);
      for (let i = 0; i < blocks.length; i++) {
        const b = blocks[i]; if (!("text" in b)) continue;
        const t = (b as { text: string }).text;
        if (BLOCK_INTRO_PATTERN.test(t.trimStart()) && re.test(t.slice(0, 90))) { link(n.id, i, !matched); matched = true; break; }
      }
    }

    // (3) Math title exact match inside a block's inline math.
    const titleNorm = normalizeMathStr(n.title_zh || n.title_en || "");
    if (titleNorm.length >= 5) {
      for (let i = 0; i < blocks.length; i++) {
        const b = blocks[i]; if (!("text" in b)) continue;
        const maths = [...(b as { text: string }).text.matchAll(/\$([^$\n]+?)\$/g)].map(m => normalizeMathStr(m[1]));
        if (maths.includes(titleNorm)) { link(n.id, i, !matched); matched = true; break; }
      }
    }

    // (4) Content fingerprint fallback. Older history entries may not carry
    // source_text, but their statement content is still a stronger locator than
    // a title or keyword alias.
    if (!matched) {
      const fp = normMatch((n.content || "").slice(0, 60));
      if (fp.length >= 14) {
        for (let i = 0; i < normBlocks.length; i++) {
          if (normBlocks[i].length >= 8 && (normBlocks[i].includes(fp.slice(0, 24)) || fp.includes(normBlocks[i].slice(0, 24)))) {
            link(n.id, i, true); matched = true; break;
          }
        }
      }
    }

    // (5) surface_anchor is a last-resort locator. A single term such as
    // "Stokes" can also occur in a table of contents, so require either two
    // normalized terms in the same block or one sufficiently specific phrase.
    if (!matched && n.surface_anchor?.anchor_terms) {
      const terms = [...new Set(n.surface_anchor.anchor_terms
        .map(normMatch)
        .filter(term => term.length >= 4))];
      let bestIndex = -1;
      let bestScore = 0;
      let bestPhraseLength = 0;
      for (let i = 0; i < normBlocks.length; i++) {
        const block = normBlocks[i];
        if (block.length < 8) continue;
        const matches = terms.filter(term => block.includes(term));
        const score = matches.length;
        const phraseLength = Math.max(0, ...matches.map(term => term.length));
        if (score > bestScore || (score === bestScore && phraseLength > bestPhraseLength)) {
          bestIndex = i;
          bestScore = score;
          bestPhraseLength = phraseLength;
        }
      }
      if (bestIndex >= 0 && (bestScore >= 2 || bestPhraseLength >= 18)) {
        link(n.id, bestIndex, true);
      }
    }
  }

  const unmatched = nodes.filter(n => !nodeToBlocks.has(n.id)).map(n => n.id);
  const coverage = nodes.length ? (nodes.length - unmatched.length) / nodes.length : 1;
  return { nodeToBlocks, blockToNodes, blocks, coverage, unmatched };
}

// ── Dependency edge taxonomy ────────────────────────────────────────────────
// The backend emits relation `label`/`description` in free Chinese. We bucket
// each edge into a small canonical taxonomy so we can colour edges by meaning
// and, since labels can't all fit, reveal text only on hover/select while the
// node drawer lists typed dependencies explicitly.
export type EdgeKind =
  | "derives" | "uses" | "specializes" | "generalizes"
  | "equivalent" | "defines" | "contradicts" | "exemplifies" | "related";

// `color` is the canonical (light-theme) hue, used for legends/badges/dots.
// `colorDark` is a desaturated, dimmed same-hue variant for edges drawn on the
// dark canvas (mirrors the node border/borderDark split) so the edge web reads
// as soft connective tissue instead of a glaring near-white net.
export interface EdgeKindMeta { key: EdgeKind; label: string; color: string; colorDark?: string; dashed?: boolean; }
export const EDGE_KINDS: Record<EdgeKind, EdgeKindMeta> = {
  derives:     { key: "derives",     label: "推导 / 依赖", color: "#d98c2b", colorDark: "#a8702e" },
  uses:        { key: "uses",        label: "使用 / 应用", color: "#2f86c5", colorDark: "#3c6f9e" },
  specializes: { key: "specializes", label: "特化 / 特例", color: "#7c5cd0", colorDark: "#6a5aa0" },
  generalizes: { key: "generalizes", label: "推广",        color: "#5aa06f", colorDark: "#4d7d5c" },
  equivalent:  { key: "equivalent",  label: "等价",        color: "#46a0a8", colorDark: "#427f85", dashed: true },
  defines:     { key: "defines",     label: "定义引用",     color: "#8a8f98", colorDark: "#5f636a" },
  contradicts: { key: "contradicts", label: "反例 / 矛盾",  color: "#c0564b", colorDark: "#9a5048", dashed: true },
  exemplifies: { key: "exemplifies", label: "举例",        color: "#9aa05f", colorDark: "#787c52", dashed: true },
  related:     { key: "related",     label: "相关",        color: "#b6bcc4", colorDark: "#565b62" },
};
const EDGE_RULES: Array<[RegExp, EdgeKind]> = [
  [/特例|特殊情形|特化|是.*的特例|special case/i, "specializes"],
  [/推广|一般化|generaliz/i, "generalizes"],
  [/等价|充要|iff|equivalent/i, "equivalent"],
  [/反例|矛盾|否定|contradict|counterexample/i, "contradicts"],
  [/举例|例证|示例|exemplif|instance of/i, "exemplifies"],
  [/引用定义|定义引用|套用定义|uses? definition|based on (the )?definition/i, "defines"],
  [/推导|推出|证明|导出|蕴含|derive|imply|prove/i, "derives"],
  [/依赖|前置|先于|prerequisite|depend|需要|基于|建立在/i, "derives"],
  [/使用|应用|利用|借助|调用|apply|use/i, "uses"],
];
export function classifyEdge(e: GraphEdge): EdgeKind {
  const hay = `${e.label || ""} ${e.description || ""}`;
  for (const [re, kind] of EDGE_RULES) if (re.test(hay)) return kind;
  return "related";
}

// ── Neighborhood (focus mode) ───────────────────────────────────────────────
export function neighborhood(id: number, edges: GraphEdge[], hops = 1): Set<number> {
  let frontier = new Set([id]); const seen = new Set([id]);
  for (let h = 0; h < hops; h++) {
    const next = new Set<number>();
    for (const e of edges) {
      if (frontier.has(e.from) && !seen.has(e.to)) next.add(e.to);
      if (frontier.has(e.to) && !seen.has(e.from)) next.add(e.from);
    }
    next.forEach(x => seen.add(x)); frontier = next;
  }
  return seen;
}

// ── Personalization settings (persisted) ────────────────────────────────────
export type ThemeMode = "light" | "dark" | "auto";
export interface StudioSettings {
  theme: ThemeMode;
  layout: StudioLayout;
  density: number;          // LOD: fraction of nodes that show labels
  dimOnFocus: boolean;      // dim non-neighbors when a node is selected
  showEdgeLabels: boolean;
  curvedEdges: boolean;
  readingPanel: boolean;    // open the source-text panel by default
  floatingDetail: boolean;  // show node detail as a floating card when the panel shows source text
}
export const DEFAULT_SETTINGS: StudioSettings = {
  theme: "light", layout: "reading", density: 0.55,
  dimOnFocus: true, showEdgeLabels: false, curvedEdges: true, readingPanel: false, floatingDetail: true,
};
const SETTINGS_KEY = "mathgraph.studio.settings.v1";
export function loadStudioSettings(): StudioSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_SETTINGS };
}
export function saveStudioSettings(s: StudioSettings) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* quota */ }
}

export function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "auto") {
    return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return mode;
}

// Convenience: dedupe by title (mirrors classic dedupeGraph so Studio matches).
export function dedupe(graph: GraphResult): GraphResult {
  const canon = new Map<number, number>(); const seen = new Map<string, number>();
  for (const n of graph.nodes) {
    const k = (n.title_zh || n.title_en || "").trim().toLowerCase();
    if (!k) continue;
    const e = seen.get(k); if (e === undefined) seen.set(k, n.id); else canon.set(n.id, e);
  }
  if (!canon.size) return graph;
  const resolve = (id: number) => canon.get(id) ?? id;
  const nodes = graph.nodes.filter(n => !canon.has(n.id));
  const ek = (e: GraphEdge) => `${e.from}→${e.to}:${(e.label || "").toLowerCase()}`;
  const se = new Set<string>();
  const edges = graph.edges.map(e => ({ ...e, from: resolve(e.from), to: resolve(e.to) }))
    .filter(e => e.from !== e.to && !se.has(ek(e)) && (se.add(ek(e)), true));
  return { nodes, edges };
}
