import type { GraphNode } from "./home";
import type { MdBlock } from "./markdown";

export function normalizeSourceMatch(text: string): string {
  return text.replace(/\s+/g, "").replace(/[$\\{}*]/g, "").toLowerCase();
}

const MATRIX_ENV = "matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|array|cases|aligned|gathered|smallmatrix";

function matrixPattern(): RegExp {
  return new RegExp(
    `\\\\begin\\{(?<env>${MATRIX_ENV})\\}(?<spec>\\{[^{}]*\\})?(?<body>[\\s\\S]*?)\\\\end\\{\\k<env>\\}`,
    "g",
  );
}

function matrixBodySignature(body: string): string {
  return body.replace(/\\+/g, "\\").replace(/\s+/g, "");
}

function matrixBodyDiffIsLostRowSlashes(target: string, source: string): boolean {
  target = target.replace(/\s+/g, "");
  source = source.replace(/\s+/g, "");
  let targetIndex = 0;
  let sourceIndex = 0;
  let lost = false;
  while (targetIndex < target.length && sourceIndex < source.length) {
    if (target[targetIndex] === "\\" && source[sourceIndex] === "\\") {
      let targetEnd = targetIndex;
      while (targetEnd < target.length && target[targetEnd] === "\\") targetEnd += 1;
      let sourceEnd = sourceIndex;
      while (sourceEnd < source.length && source[sourceEnd] === "\\") sourceEnd += 1;
      const targetCount = targetEnd - targetIndex;
      const sourceCount = sourceEnd - sourceIndex;
      if (targetCount > sourceCount) return false;
      lost ||= targetCount < sourceCount;
      targetIndex = targetEnd;
      sourceIndex = sourceEnd;
      continue;
    }
    if (target[targetIndex] !== source[sourceIndex]) return false;
    targetIndex += 1;
    sourceIndex += 1;
  }
  return lost && targetIndex === target.length && sourceIndex === source.length;
}

function restoreLegacyMatrixText(value: string, sourceMatches: Map<string, Set<string>>): string {
  return value.replace(matrixPattern(), (whole, ...args) => {
    const groups = args.at(-1) as { env: string; spec?: string; body: string };
    const choices = new Set(
      [...(sourceMatches.get(matrixBodySignature(groups.body)) ?? [])]
        .filter(sourceBody => matrixBodyDiffIsLostRowSlashes(groups.body, sourceBody)),
    );
    if (choices.size !== 1) return whole;
    return `\\begin{${groups.env}}${groups.spec || ""}${[...choices][0]}\\end{${groups.env}}`;
  });
}

/** Repair only provable row-separator loss in an old cached source statement. */
export function repairLegacySourceStatement(sourceStatement: string, content: string): string {
  if (!sourceStatement || !content) return sourceStatement;
  const sourceMatches = new Map<string, Set<string>>();
  for (const match of content.matchAll(matrixPattern())) {
    const body = match.groups?.body;
    if (!body) continue;
    const key = matrixBodySignature(body);
    const choices = sourceMatches.get(key) ?? new Set<string>();
    choices.add(body);
    sourceMatches.set(key, choices);
  }
  return sourceMatches.size ? restoreLegacyMatrixText(sourceStatement, sourceMatches) : sourceStatement;
}

export function nodeStatementText(node: Pick<GraphNode, "content" | "source_statement">): string {
  const content = String(node.content || "").trim();
  const sourceStatement = String(node.source_statement || "").trim();
  return (sourceStatement ? repairLegacySourceStatement(sourceStatement, content) : content).trim();
}

export function sourceStatementBlockRange(blocks: MdBlock[], sourceStatement?: string): [number, number] | null {
  const statement = normalizeSourceMatch(sourceStatement || "");
  if (statement.length < 8) return null;

  const normalized = blocks.map(block => ("text" in block ? normalizeSourceMatch(block.text) : ""));
  let best: { start: number; end: number; matched: number; difference: number } | null = null;
  let ambiguous = false;

  for (let start = 0; start < blocks.length; start++) {
    let joined = "";
    for (let end = start; end < blocks.length; end++) {
      const block = blocks[end];
      if (block.type === "hr") break;
      if (end > start && /^h[1-4]$/.test(block.type)) break;

      const text = normalized[end];
      if (!text) continue;
      joined += text;

      const matched = Math.min(joined.length, statement.length);
      if (!statement.includes(joined) && !joined.includes(statement)) break;

      const difference = Math.abs(joined.length - statement.length);
      if (!best || matched > best.matched || (matched === best.matched && difference < best.difference)) {
        best = { start, end, matched, difference };
        ambiguous = false;
      } else if (matched === best.matched && difference === best.difference
        && (start !== best.start || end !== best.end)) {
        ambiguous = true;
      }
      if (joined.includes(statement)) break;
    }
  }

  if (!best || ambiguous) return null;
  const coverage = best.matched / statement.length;
  const extraContext = best.difference / statement.length;
  return coverage >= 0.82 && extraContext <= 0.35 ? [best.start, best.end] : null;
}
