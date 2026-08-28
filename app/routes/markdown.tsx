import React, { useMemo } from "react";
import katex from "katex";
import { MathText, parseMathSegments, mergeKatexMacros } from "./math";
import type { LatexMacros } from "./math";
import type { GraphNode } from "./home";

// ── Text-Graph anchor matching ────────────────────────────────────────────────

export const GENERIC_MATH_WORDS = new Set(["定义", "定理", "引理", "推论", "公理", "命题", "性质", "例子", "证明", "注记"]);

export interface TextAnchor { term: string; nodeId: number; }

export function buildTextAnchors(nodes: GraphNode[]): TextAnchor[] {
  const anchors: TextAnchor[] = [];
  const seen = new Set<string>();
  for (const node of nodes) {
    const candidates: string[] = [];
    // Priority 1: numbered label (e.g. "定理 3.1") — most precise
    if (node.label && /\d/.test(node.label)) candidates.push(node.label.trim());
    // Priority 2: Chinese title ≥ 3 chars, not a bare generic word
    const tzh = (node.title_zh || "").trim();
    if (tzh.length >= 3 && !GENERIC_MATH_WORDS.has(tzh)) candidates.push(tzh);
    // Priority 3: English title ≥ 4 chars
    const ten = (node.title_en || "").trim();
    if (ten.length >= 4) candidates.push(ten);
    // Priority 4: surface_anchor terms from backend (if present)
    if (node.surface_anchor?.anchor_terms) {
      for (const t of node.surface_anchor.anchor_terms) {
        if (t.length >= 4 && !GENERIC_MATH_WORDS.has(t)) candidates.push(t);
      }
    }
    for (const term of candidates) {
      const key = term.toLowerCase();
      if (!seen.has(key)) { seen.add(key); anchors.push({ term, nodeId: node.id }); }
    }
  }
  // Sort descending by length so longer terms win over shorter substrings
  return anchors.sort((a, b) => b.term.length - a.term.length);
}

export function buildAnchorRegex(anchors: TextAnchor[]): RegExp | null {
  if (anchors.length === 0) return null;
  const escaped = anchors.map(a => a.term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`(${escaped.join("|")})`);  // no "g" flag — used per-call with exec loop
}

// ── Block-level anchor helpers ───────────────────────────────────────────────

export const BLOCK_INTRO_PATTERN = /^(definition|theorem|lemma|proposition|corollary|example|remark|notation|proof|定义|定理|引理|推论|命题|性质|例子|注)/i;

export function extractLabelNumber(label: string): string | null {
  const m = label.match(/(\d[\d.]*)/);
  return m ? m[1] : null;
}

export function normalizeMathStr(s: string): string {
  // Remove spaces, {}, $, \, keep alphanumeric and ^_
  return s.replace(/[\s{}$\\]/g, "").toLowerCase();
}

// Build a map from normalized LaTeX title string → nodeId for fast lookup
export function buildMathAnchorMap(nodes: GraphNode[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const node of nodes) {
    const titleNorm = normalizeMathStr(node.title_zh || node.title_en || "");
    if (titleNorm.length >= 4) {
      map.set(titleNorm, node.id);
    }
  }
  return map;
}

export function splitByAnchors(
  text: string,
  anchors: TextAnchor[],
  regex: RegExp,
): Array<{ kind: "text" | "anchor"; src: string; nodeId?: number }> {
  const parts: Array<{ kind: "text" | "anchor"; src: string; nodeId?: number }> = [];
  const termToId: Record<string, number> = {};
  for (const a of anchors) termToId[a.term.toLowerCase()] = a.nodeId;
  const gRegex = new RegExp(regex.source, "g");
  let last = 0, m: RegExpExecArray | null;
  while ((m = gRegex.exec(text)) !== null) {
    if (m.index > last) parts.push({ kind: "text", src: text.slice(last, m.index) });
    const matched = m[0];
    const nid = termToId[matched.toLowerCase()];
    parts.push({ kind: "anchor", src: matched, nodeId: nid });
    last = gRegex.lastIndex;
  }
  if (last < text.length) parts.push({ kind: "text", src: text.slice(last) });
  return parts;
}

// Renders text with LaTeX + clickable node anchors
export function AnchoredMathText({
  text, anchors, anchorRegex, onNodeClick, mathAnchors, macros,
}: {
  text: string;
  anchors: TextAnchor[];
  anchorRegex: RegExp | null;
  onNodeClick: (nodeId: number, el: HTMLElement) => void;
  mathAnchors?: Map<string, number>;
  macros?: LatexMacros;
}) {
  const segs = parseMathSegments(text);
  const mergedMacros = mergeKatexMacros(macros);
  return (
    <span>
      {segs.map((seg, i) => {
        if (seg.type === "math") {
          // Try to match inline math against mathAnchors for clickable LaTeX
          const mathNodeId = (!seg.display && mathAnchors)
            ? (() => {
                const norm = normalizeMathStr(seg.src);
                if (norm.length < 4) return undefined;
                // Check if norm contains or is contained in any mathAnchor key
                for (const [key, nid] of mathAnchors) {
                  if (norm.includes(key) || key.includes(norm)) return nid;
                }
                return undefined;
              })()
            : undefined;

          let rendered: React.ReactNode;
          try {
            const html = katex.renderToString(seg.src, {
              displayMode: seg.display, throwOnError: false, errorColor: "#999",
              output: "html", strict: false, macros: mergedMacros,
            });
            rendered = <span dangerouslySetInnerHTML={{ __html: html }} />;
          } catch {
            rendered = <span style={{ fontFamily: "monospace", fontSize: "0.88em", color: "var(--muted)" }}>{seg.src}</span>;
          }

          if (mathNodeId !== undefined) {
            return (
              <span key={i} className="mg-text-anchor" data-node-id={mathNodeId}
                onClick={(e) => onNodeClick(mathNodeId, e.currentTarget as HTMLElement)}>
                {rendered}
              </span>
            );
          }
          return <span key={i}>{rendered}</span>;
        }
        if (!anchorRegex) return <span key={i}>{seg.src}</span>;
        const parts = splitByAnchors(seg.src, anchors, anchorRegex);
        return (
          <span key={i}>
            {parts.map((p, j) =>
              p.kind === "anchor" && p.nodeId !== undefined
                ? <span key={j} className="mg-text-anchor" data-node-id={p.nodeId}
                    onClick={(e) => onNodeClick(p.nodeId!, e.currentTarget as HTMLElement)}>
                    {p.src}
                  </span>
                : <span key={j}>{p.src}</span>
            )}
          </span>
        );
      })}
    </span>
  );
}

// ── Markdown Viewer ──────────────────────────────────────────────────────────

export type MdBlock =
  | { type: "h1" | "h2" | "h3" | "h4"; text: string }
  | { type: "math-block"; text: string }
  | { type: "hr" }
  | { type: "paragraph"; text: string };

export function sourceTextForReading(source: string): string {
  const begin = /\\begin\s*\{\s*document\s*\}/i.exec(source);
  if (!begin) return source;
  const bodyStart = begin.index + begin[0].length;
  const body = source.slice(bodyStart);
  const end = /\\end\s*\{\s*document\s*\}/i.exec(body);
  return (end ? body.slice(0, end.index) : body).trimStart();
}

const TEX_BLOCK_ENVS = new Set([
  "definition", "theorem", "lemma", "proposition", "corollary", "example",
  "exercise", "proof", "remark", "note", "notation", "axiom", "problem",
  "introduction",
]);

function texHeading(line: string): MdBlock | null {
  const m = line.match(/^\\(chapter|section|subsection|subsubsection)\*?\s*\{(.+?)\}(?:\\label\s*\{.*?\})?\s*$/);
  if (!m) return null;
  const type = m[1] === "chapter" ? "h1" : m[1] === "section" ? "h2" : m[1] === "subsection" ? "h3" : "h4";
  return { type, text: m[2] } as MdBlock;
}

function isTexNoiseLine(line: string): boolean {
  const t = line.trim();
  return (
    t === "" ||
    /^%{3,}$/.test(t) ||
    /^\\(?:mainmatter|frontmatter|backmatter|tableofcontents|maketitle)\b/.test(t)
  );
}

function texBeginEnv(line: string): string | null {
  const m = line.match(/\\begin\s*\{\s*([A-Za-z*]+)\s*\}/);
  if (!m) return null;
  const env = m[1].replace(/\*$/, "").toLowerCase();
  return TEX_BLOCK_ENVS.has(env) ? env : null;
}

function parseTexBlocks(source: string): MdBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  const para: string[] = [];

  const flushPara = () => {
    const text = para.join("\n").trim();
    para.length = 0;
    if (text) blocks.push({ type: "paragraph", text });
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    const heading = texHeading(trimmed);
    const env = texBeginEnv(trimmed);

    if (heading) {
      flushPara();
      blocks.push(heading);
      continue;
    }

    if (env) {
      flushPara();
      const envLines = [line];
      let depth = 1;
      while (i + 1 < lines.length && depth > 0) {
        i++;
        const next = lines[i];
        const beginMatches = [...next.matchAll(/\\begin\s*\{\s*([A-Za-z*]+)\s*\}/g)]
          .map(m => m[1].replace(/\*$/, "").toLowerCase())
          .filter(name => name === env).length;
        const endMatches = [...next.matchAll(/\\end\s*\{\s*([A-Za-z*]+)\s*\}/g)]
          .map(m => m[1].replace(/\*$/, "").toLowerCase())
          .filter(name => name === env).length;
        depth += beginMatches - endMatches;
        envLines.push(next);
      }
      blocks.push({ type: "paragraph", text: envLines.join("\n").trim() });
      continue;
    }

    if (isTexNoiseLine(line)) {
      flushPara();
      continue;
    }

    para.push(line);
  }

  flushPara();
  return blocks;
}

export function parseMdBlocks(md: string): MdBlock[] {
  const source = sourceTextForReading(md);
  if (/\\(?:chapter|section|subsection|begin)\b/.test(source)) {
    return parseTexBlocks(source);
  }
  const lines = source.split("\n");
  const blocks: MdBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const types = ["h1", "h2", "h3", "h4"] as const;
      blocks.push({ type: types[Math.min(hm[1].length - 1, 3)], text: hm[2] });
      i++; continue;
    }
    if (line.trim() === "$$") {
      const math: string[] = [];
      i++;
      while (i < lines.length && lines[i].trim() !== "$$") { math.push(lines[i]); i++; }
      i++;
      blocks.push({ type: "math-block", text: math.join("\n") });
      continue;
    }
    if (/^-{3,}$/.test(line.trim()) || /^\*{3,}$/.test(line.trim())) {
      blocks.push({ type: "hr" }); i++; continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].match(/^#{1,4}\s/) &&
      lines[i].trim() !== "$$" &&
      !/^-{3,}$/.test(lines[i].trim())
    ) { para.push(lines[i]); i++; }
    if (para.length > 0) blocks.push({ type: "paragraph", text: para.join(" ") });
  }
  return blocks;
}

export function MarkdownViewer({ markdown, macros }: { markdown: string; macros?: LatexMacros }) {
  const blocks = parseMdBlocks(markdown);
  return (
    <div style={{ fontFamily: "Inter,system-ui,sans-serif", fontSize: 14, lineHeight: 1.85, color: "var(--ink)" }}>
      {blocks.map((block, i) => {
        switch (block.type) {
          case "h1":
            return <h1 key={i} style={{ fontSize: 20, fontWeight: 700, marginTop: 28, marginBottom: 8, color: "var(--ink)", borderBottom: "2px solid var(--line)", paddingBottom: 8 }}><MathText text={block.text} macros={macros} /></h1>;
          case "h2":
            return <h2 key={i} style={{ fontSize: 16, fontWeight: 600, marginTop: 22, marginBottom: 6, color: "var(--ink)" }}><MathText text={block.text} macros={macros} /></h2>;
          case "h3":
            return <h3 key={i} style={{ fontSize: 14, fontWeight: 600, marginTop: 16, marginBottom: 4, color: "var(--accent)" }}><MathText text={block.text} macros={macros} /></h3>;
          case "h4":
            return <h4 key={i} style={{ fontSize: 13, fontWeight: 600, marginTop: 12, marginBottom: 4, color: "var(--muted)" }}><MathText text={block.text} macros={macros} /></h4>;
          case "math-block":
            return <div key={i} style={{ margin: "14px 0", overflowX: "auto", padding: "6px 0" }}><MathText text={`$$${block.text}$$`} macros={macros} /></div>;
          case "hr":
            return <hr key={i} style={{ border: "none", borderTop: "1px solid var(--line)", margin: "18px 0" }} />;
          case "paragraph":
            return <p key={i} style={{ marginBottom: 10, color: "var(--ink)" }}><MathText text={block.text} macros={macros} /></p>;
          default:
            return null;
        }
      })}
    </div>
  );
}

// ── Linked Markdown Viewer (md-graph layout) ─────────────────────────────────

interface LinkedMarkdownViewerProps {
  markdown: string;
  nodes: GraphNode[];
  onNodeClick: (nodeId: number, el: HTMLElement) => void;
  activeNodeId: number | null;
  panelRef: React.RefObject<HTMLDivElement | null>;
  macros?: LatexMacros;
}

// Strip LaTeX + normalize whitespace, take first N chars → content fingerprint
export function contentFp(text: string, len = 45): string {
  return text.replace(/\$[^$]+\$/g, "").replace(/\s+/g, " ").trim().slice(0, len);
}

export function LinkedMarkdownViewer({ markdown, nodes, onNodeClick, activeNodeId, panelRef, macros }: LinkedMarkdownViewerProps) {
  const blocks = useMemo(() => parseMdBlocks(markdown), [markdown]);

  // Pre-build content fingerprints for Strategy 4
  const nodeFps = useMemo(() => {
    const fps: { nodeId: number; fp: string; fpShort: string }[] = [];
    for (const node of nodes) {
      const raw = node.source_text || node.content || "";
      if (!raw) continue;
      const fp = contentFp(raw, 45);
      if (fp.length >= 8) fps.push({ nodeId: node.id, fp, fpShort: fp.slice(0, 28) });
    }
    return fps;
  }, [nodes]);

  // For each block, find which nodes were extracted from it (source-block only).
  // Strategy 1 (title regex across full text) is intentionally removed — it
  // matched every occurrence of a node name and created noisy fine-grained links.
  const blockNodeIds = useMemo(() => {
    return blocks.map(block => {
      if (block.type === "hr") return [] as number[];
      const found = new Set<number>();
      const blockText = block.text;

      // Strategy 2: block starts with a numbered label keyword ("定理 3.1", "Theorem 2")
      if (BLOCK_INTRO_PATTERN.test(blockText.trimStart())) {
        const preview = blockText.slice(0, 80);
        for (const node of nodes) {
          const num = extractLabelNumber(node.label || "");
          if (num) {
            const numRe = new RegExp(`\\b${num.replace(/\./g, "\\.")}\\b`);
            if (numRe.test(preview)) found.add(node.id);
          }
        }
      }

      // Strategy 3: LaTeX title exact match inside block math spans
      const mathInBlock = [...blockText.matchAll(/\$([^$\n]+?)\$/g)]
        .map(m2 => normalizeMathStr(m2[1]))
        .filter(s => s.length >= 5);
      if (mathInBlock.length > 0) {
        for (const node of nodes) {
          const titleNorm = normalizeMathStr(node.title_zh || node.title_en || "");
          if (titleNorm.length >= 5 && mathInBlock.includes(titleNorm)) {
            found.add(node.id);
          }
        }
      }

      // Strategy 4: content fingerprint — the block's text matches the node's source_text
      if ("text" in block) {
        const bfp = contentFp(blockText, 45);
        if (bfp.length >= 8) {
          for (const { nodeId, fp, fpShort } of nodeFps) {
            if (bfp.includes(fpShort) || fp.includes(bfp.slice(0, 28))) {
              found.add(nodeId);
            }
          }
        }
      }

      return Array.from(found);
    });
  }, [blocks, nodes, nodeFps]);

  return (
    <div ref={panelRef as React.RefObject<HTMLDivElement>} style={{ fontFamily: "Inter,system-ui,sans-serif", fontSize: 14, lineHeight: 1.85, color: "var(--ink)" }}>
      {blocks.map((block, i) => {
        const nids = blockNodeIds[i];
        const isActive = activeNodeId !== null && nids.includes(activeNodeId);
        const dataAttr = nids.length > 0 ? { "data-node-ids": nids.join(" ") } : {};
        const isSource = nids.length > 0;
        const cls = [isActive ? "mg-paragraph-active" : "", isSource ? "mg-block-anchor" : ""].filter(Boolean).join(" ") || undefined;
        const handleClick = isSource
          ? (e: React.MouseEvent) => onNodeClick(nids[0], e.currentTarget as HTMLElement)
          : undefined;

        switch (block.type) {
          case "h1":
            return <h1 key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ fontSize: 20, fontWeight: 700, marginTop: 28, marginBottom: 8, color: "#111", borderBottom: "2px solid #E2DFD8", paddingBottom: 8 }}>
              <MathText text={block.text} macros={macros} />
            </h1>;
          case "h2":
            return <h2 key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ fontSize: 16, fontWeight: 600, marginTop: 22, marginBottom: 6, color: "var(--ink)" }}>
              <MathText text={block.text} macros={macros} />
            </h2>;
          case "h3":
            return <h3 key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ fontSize: 14, fontWeight: 600, marginTop: 16, marginBottom: 4, color: "var(--accent)" }}>
              <MathText text={block.text} macros={macros} />
            </h3>;
          case "h4":
            return <h4 key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ fontSize: 13, fontWeight: 600, marginTop: 12, marginBottom: 4, color: "var(--muted)" }}>
              <MathText text={block.text} macros={macros} />
            </h4>;
          case "math-block":
            return <div key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ margin: "14px 0", overflowX: "auto", padding: "6px 0" }}><MathText text={`$$${block.text}$$`} macros={macros} /></div>;
          case "hr":
            return <hr key={i} style={{ border: "none", borderTop: "1px solid var(--line)", margin: "18px 0" }} />;
          case "paragraph":
            return <p key={i} {...dataAttr} className={cls} onClick={handleClick} style={{ marginBottom: 10, color: "var(--ink)" }}>
              <MathText text={block.text} macros={macros} />
            </p>;
          default:
            return null;
        }
      })}
    </div>
  );
}
