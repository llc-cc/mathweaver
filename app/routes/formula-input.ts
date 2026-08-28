export type FormulaPresentation = "inline" | "block";

export const CALCULUS_OPERATOR_TEMPLATES = {
  sum: { latex: "\\sum_{#?}^{#?}", previewLatex: "\\displaystyle\\sum_{i=1}^{n}" },
  product: { latex: "\\prod_{#?}^{#?}", previewLatex: "\\displaystyle\\prod_{i=1}^{n}" },
  limit: { latex: "\\lim_{#?\\to #?}", previewLatex: "\\displaystyle\\lim_{x\\to 0}" },
} as const;

export type FormulaDelimiterStyle =
  | "dollar-inline"
  | "dollar-block"
  | "paren-inline"
  | "bracket-block";

export interface FormulaMatch {
  start: number;
  end: number;
  inner: string;
  presentation: FormulaPresentation;
  style: FormulaDelimiterStyle;
}

export interface FormulaCommitResult {
  text: string;
  selectionStart: number;
  selectionEnd: number;
  match: FormulaMatch;
}

const formulaPattern = /\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)|(?<!\$)\$([\s\S]*?)(?<!\$)\$(?!\$)/g;

function matchFromRegex(match: RegExpExecArray): FormulaMatch {
  const raw = match[0];
  const start = match.index;
  if (raw.startsWith("$$")) {
    return { start, end: start + raw.length, inner: match[1] || "", presentation: "block", style: "dollar-block" };
  }
  if (raw.startsWith("\\[")) {
    return { start, end: start + raw.length, inner: match[2] || "", presentation: "block", style: "bracket-block" };
  }
  if (raw.startsWith("\\(")) {
    return { start, end: start + raw.length, inner: match[3] || "", presentation: "inline", style: "paren-inline" };
  }
  return { start, end: start + raw.length, inner: match[4] || "", presentation: "inline", style: "dollar-inline" };
}

export function findFormulaRanges(text: string): FormulaMatch[] {
  const result: FormulaMatch[] = [];
  formulaPattern.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = formulaPattern.exec(text)) !== null) {
    result.push(matchFromRegex(match));
  }
  return result;
}

export interface FormulaTextSegment {
  start: number;
  end: number;
  text: string;
  formula: FormulaMatch | null;
}

export function buildFormulaTextSegments(text: string): FormulaTextSegment[] {
  const ranges = findFormulaRanges(text);
  const segments: FormulaTextSegment[] = [];
  let cursor = 0;
  for (const formula of ranges) {
    if (formula.start > cursor) {
      segments.push({ start: cursor, end: formula.start, text: text.slice(cursor, formula.start), formula: null });
    }
    segments.push({ start: formula.start, end: formula.end, text: text.slice(formula.start, formula.end), formula });
    cursor = formula.end;
  }
  if (cursor < text.length || segments.length === 0) {
    segments.push({ start: cursor, end: text.length, text: text.slice(cursor), formula: null });
  }
  return segments;
}

export function findFormulaAt(text: string, selectionStart: number, selectionEnd = selectionStart): FormulaMatch | null {
  const start = Math.max(0, Math.min(selectionStart, text.length));
  const end = Math.max(start, Math.min(selectionEnd, text.length));
  return findFormulaRanges(text).find((item) => {
    if (start === end) return start >= item.start && start < item.end;
    return start < item.end && end > item.start;
  }) || null;
}

function delimiters(style: FormulaDelimiterStyle): [string, string] {
  if (style === "dollar-block") return ["$$", "$$"];
  if (style === "dollar-inline") return ["$", "$"];
  if (style === "bracket-block") return ["\\[", "\\]"];
  return ["\\(", "\\)"];
}

function defaultStyle(presentation: FormulaPresentation): FormulaDelimiterStyle {
  return presentation === "block" ? "bracket-block" : "paren-inline";
}

export function commitFormula(
  text: string,
  selectionStart: number,
  selectionEnd: number,
  innerLatex: string,
  presentation: FormulaPresentation,
): FormulaCommitResult {
  const existing = findFormulaAt(text, selectionStart, selectionEnd);
  const start = existing?.start ?? Math.max(0, Math.min(selectionStart, text.length));
  const end = existing?.end ?? Math.max(start, Math.min(selectionEnd, text.length));
  const style = existing?.style ?? defaultStyle(presentation);
  const [open, close] = delimiters(style);
  const inner = innerLatex.trim();
  const replacement = `${open}${inner}${close}`;
  const nextText = `${text.slice(0, start)}${replacement}${text.slice(end)}`;
  const nextPosition = start + replacement.length;
  return {
    text: nextText,
    selectionStart: nextPosition,
    selectionEnd: nextPosition,
    match: {
      start,
      end: nextPosition,
      inner,
      presentation: existing?.presentation ?? presentation,
      style,
    },
  };
}

export type MatrixEnvironment = "bmatrix" | "pmatrix" | "vmatrix";

export type MatrixDelimiterEnvironment = "matrix" | "pmatrix" | "bmatrix" | "vmatrix" | "Bmatrix";

const MATRIX_DELIMITER_ENVIRONMENTS: Record<string, MatrixDelimiterEnvironment> = {
  "environment-no-border": "matrix",
  "environment-parentheses": "pmatrix",
  "environment-brackets": "bmatrix",
  "environment-bar": "vmatrix",
  "environment-braces": "Bmatrix",
};

const HIDDEN_FORMULA_MENU_ITEMS = new Set(["color", "background-color"]);

interface FormulaMenuEntry {
  id?: string;
  submenu?: readonly unknown[];
}

export function customizeFormulaMenuItems<T>(
  menuItems: readonly T[],
  onSelectMatrixDelimiter: (environment: MatrixDelimiterEnvironment) => void,
): readonly T[] {
  const result: T[] = [];
  for (const item of menuItems) {
    const entry = item as T & FormulaMenuEntry;
    if (entry.id && HIDDEN_FORMULA_MENU_ITEMS.has(entry.id)) continue;
    if (entry.submenu) {
      result.push({
        ...item,
        submenu: customizeFormulaMenuItems(entry.submenu, onSelectMatrixDelimiter),
      } as T);
      continue;
    }
    const environment = entry.id ? MATRIX_DELIMITER_ENVIRONMENTS[entry.id] : undefined;
    result.push(environment ? {
      ...item,
      onMenuSelect: () => onSelectMatrixDelimiter(environment),
    } as T : item);
  }
  return result;
}

interface MatrixDelimiterField<TSelection> {
  focus: () => void;
  selection: TSelection;
  executeCommand: (selector: ["setEnvironment", MatrixDelimiterEnvironment]) => boolean;
  getValue: (format: "latex") => string;
}

export function applyMatrixDelimiter<TSelection>(
  field: MatrixDelimiterField<TSelection>,
  selection: TSelection | null,
  environment: MatrixDelimiterEnvironment,
): { changed: boolean; latex: string } {
  field.focus();
  if (selection !== null) field.selection = selection;
  const changed = field.executeCommand(["setEnvironment", environment]);
  return { changed, latex: field.getValue("latex") };
}

export function matrixLatex(rows: number, columns: number, environment: MatrixEnvironment = "bmatrix"): string {
  if (!Number.isInteger(rows) || rows < 1 || rows > 6) throw new Error("矩阵行数必须为 1–6");
  if (!Number.isInteger(columns) || columns < 1 || columns > 6) throw new Error("矩阵列数必须为 1–6");
  const cell = "\\placeholder{}";
  const row = Array.from({ length: columns }, () => cell).join(" & ");
  const body = Array.from({ length: rows }, () => row).join(" \\\\\n");
  return `\\begin{${environment}}\n${body}\n\\end{${environment}}`;
}
