import type { GraphNode } from "./home";
import type { MdBlock } from "./markdown";

export function normalizeSourceMatch(text: string): string {
  return text.replace(/\s+/g, "").replace(/[$\\{}*]/g, "").toLowerCase();
}

export function nodeStatementText(node: Pick<GraphNode, "content" | "source_statement">): string {
  return String(node.source_statement || node.content || "").trim();
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
