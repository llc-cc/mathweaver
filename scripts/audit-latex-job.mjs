import { createServer } from "vite";
import fs from "node:fs";
import path from "node:path";
import katex from "katex";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const jobDir = process.argv[2];

if (!jobDir) {
  console.error("Usage: npm.cmd run audit:latex -- <job-directory>");
  process.exit(2);
}

function readJson(name) {
  return JSON.parse(fs.readFileSync(path.join(jobDir, name), "utf8"));
}

function addDisplayValue(values, owner, field, value) {
  if (typeof value === "string" && value.trim()) {
    values.push({ owner, field, text: value });
  } else if (Array.isArray(value)) {
    value.forEach((item, index) => addDisplayValue(values, owner, `${field}[${index}]`, item));
  } else if (value && typeof value === "object") {
    if (typeof value.text === "string") {
      addDisplayValue(values, owner, `${field}.text`, value.text);
    } else {
      Object.entries(value).forEach(([key, item]) => addDisplayValue(values, owner, `${field}.${key}`, item));
    }
  }
}

const MATRIX_ENV = "matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|array|cases|aligned|gathered|smallmatrix";

function matrixPattern() {
  return new RegExp(`\\\\begin\\{(?<env>${MATRIX_ENV})\\}(?<spec>\\{[^{}]*\\})?(?<body>[\\s\\S]*?)\\\\end\\{\\k<env>\\}`, "g");
}

function matrixBodySignature(body) {
  return body.replace(/\\+/g, "\\").replace(/\s+/g, "");
}

function matrixBodyDiffIsLostRowSlashes(target, source) {
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

function matrixRowSeparatorCount(body) {
  let count = 0;
  for (const match of body.matchAll(/\\+/g)) {
    if (match[0].length >= 2) count += 1;
  }
  return count;
}

function restoreValue(value, sourceMatches) {
  if (typeof value === "string") {
    return value.replace(matrixPattern(), (...args) => {
      const groups = args.at(-1);
      const choices = sourceMatches.get(matrixBodySignature(groups.body));
      const repairable = [...(choices || [])].filter(sourceBody =>
        matrixBodyDiffIsLostRowSlashes(groups.body, sourceBody),
      );
      if (repairable.length !== 1) return args[0];
      return `\\begin{${groups.env}}${groups.spec || ""}${repairable[0]}\\end{${groups.env}}`;
    });
  }
  if (Array.isArray(value)) return value.map(item => restoreValue(item, sourceMatches));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, restoreValue(item, sourceMatches)]));
  }
  return value;
}

function projectNodes(nodes, source) {
  return nodes.map(node => {
    const span = node.source_span;
    const excerpt = span && Number.isInteger(span.start) && Number.isInteger(span.end)
      && span.start >= 0 && span.end >= span.start && span.end <= source.length
      ? source.slice(span.start, span.end)
      : "";
    const retained = excerpt ? [excerpt] : [node.source_text, node.source_statement].filter(value => typeof value === "string" && value);
    const sourceMatches = new Map();
    for (const retainedText of retained) {
      for (const match of retainedText.matchAll(matrixPattern())) {
        const key = matrixBodySignature(match.groups.body);
        if (!sourceMatches.has(key)) sourceMatches.set(key, new Set());
        sourceMatches.get(key).add(match.groups.body);
      }
    }
    const projected = structuredClone(node);
    if (!projected.source_statement) {
      projected.source_statement = projected.source_original_form
        || projected.original_form
        || projected.remark?.original_form
        || projected.content
        || projected.source_text
        || "";
    }
    if (!projected.source_text) projected.source_text = projected.source_statement;
    for (const field of ["title", "title_zh", "title_en", "content", "proof", "source_text", "source_statement", "subject", "conditions", "conclusions"]) {
      if (field in projected) projected[field] = restoreValue(projected[field], sourceMatches);
    }
    return projected;
  });
}

function sourceMatchesForNode(node, source) {
  const span = node.source_span;
  const excerpt = span && Number.isInteger(span.start) && Number.isInteger(span.end)
    && span.start >= 0 && span.end >= span.start && span.end <= source.length
    ? source.slice(span.start, span.end)
    : "";
  const retained = excerpt ? [excerpt] : [node.source_text, node.source_statement].filter(value => typeof value === "string" && value);
  const sourceMatches = new Map();
  for (const retainedText of retained) {
    for (const match of retainedText.matchAll(matrixPattern())) {
      const key = matrixBodySignature(match.groups.body);
      const choices = sourceMatches.get(key) || new Set();
      choices.add(match.groups.body);
      sourceMatches.set(key, choices);
    }
  }
  return sourceMatches;
}

function auditValues(values, math, renderMath, matrixAuthorities = new Map()) {
  const failures = [];
  let formulaCount = 0;
  for (const value of values) {
    const rendered = renderToStaticMarkup(React.createElement(renderMath, { text: value.text }));
    if (rendered.includes("monospace") || rendered.includes("katex-error")) {
      failures.push({ ...value, error: "MathText rendered a source fallback", source: value.text });
    }
    const normalized = math.normalizeTexTextForMathText(value.text);
    const sourceMatches = matrixAuthorities.get(value.owner);
    if (sourceMatches) {
      for (const match of value.text.matchAll(matrixPattern())) {
        const choices = [...(sourceMatches.get(matrixBodySignature(match.groups.body)) || [])];
        if (choices.length !== 1) continue;
        if (matrixRowSeparatorCount(match.groups.body) !== matrixRowSeparatorCount(choices[0])) {
          failures.push({ ...value, error: "matrix row structure differs from source", source: match[0] });
        }
      }
    }
    for (const segment of math.parseMathSegments(normalized)) {
      if (segment.type === "text") {
        if (/\\begin\{|\\end\{/.test(segment.src)) {
          failures.push({ ...value, error: "TeX environment leaked into text", source: segment.src });
        }
        continue;
      }
      formulaCount += 1;
      try {
        const html = katex.renderToString(segment.src, {
          displayMode: segment.display,
          throwOnError: true,
          output: "html",
          strict: false,
          macros: math.mergeKatexMacros(),
        });
        if (html.includes("katex-error")) {
          failures.push({ ...value, error: "KaTeX emitted katex-error", source: segment.src });
        }
      } catch (error) {
        failures.push({ ...value, error: String(error?.message || error), source: segment.src });
      }
    }
  }
  return { auditedFields: values.length, formulaCount, failures };
}

const server = await createServer({
  root: process.cwd(),
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "silent",
});

try {
  const math = await server.ssrLoadModule("/app/routes/math.tsx");
  const sourceMatching = await server.ssrLoadModule("/app/routes/source-matching.ts");
  const sourceFile = fs.readdirSync(jobDir).find(name => name.toLowerCase().endsWith(".tex"));
  if (!sourceFile) throw new Error("Job audit requires the original .tex source beside nodes.json");
  const sourceText = fs.readFileSync(path.join(jobDir, sourceFile), "utf8");
  const nodes = projectNodes(readJson("nodes.json"), sourceText);
  const edges = readJson("edges.json");
  const nodeValues = [];
  const edgeValues = [];
  const nodeMatrixAuthorities = new Map();

  nodes.forEach((node, index) => {
    const owner = node.global_id || node.id || `node:${index}`;
    nodeMatrixAuthorities.set(owner, sourceMatchesForNode(node, sourceText));
    addDisplayValue(nodeValues, owner, "title_zh", node.title?.chinese ?? node.title_zh);
    addDisplayValue(nodeValues, owner, "title_en", node.title?.english ?? node.title_en);
    addDisplayValue(nodeValues, owner, "statement", sourceMatching.nodeStatementText(node));
    for (const field of ["content", "proof", "source_text", "source_statement", "subject", "conditions", "conclusions"]) {
      addDisplayValue(nodeValues, owner, field, node[field]);
    }
  });
  edges.forEach((edge, index) => {
    const owner = `edge:${index}`;
    addDisplayValue(edgeValues, owner, "label", edge.label ?? edge.relation ?? edge.name ?? edge["关系"]);
    addDisplayValue(edgeValues, owner, "description", edge.description ?? edge.explanation ?? edge.reason ?? edge["理由"]);
  });

  const nodeAudit = auditValues(nodeValues, math, math.MathText, nodeMatrixAuthorities);
  const edgeAudit = auditValues(edgeValues, math, math.MathText);
  const report = {
    jobDir,
    nodes: { count: nodes.length, ...nodeAudit, failingNodes: new Set(nodeAudit.failures.map(item => item.owner)).size },
    edges: { count: edges.length, ...edgeAudit, failingEdges: new Set(edgeAudit.failures.map(item => item.owner)).size },
    failures: [...nodeAudit.failures, ...edgeAudit.failures].slice(0, 50),
  };
  console.log(JSON.stringify(report, null, 2));
  if (nodeAudit.failures.length || edgeAudit.failures.length) process.exitCode = 1;
} finally {
  await server.close();
}
