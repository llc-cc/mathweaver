import type React from "react";
import katex from "katex";
import { labelText } from "./home";

function balanceLatexBraces(s: string): string {
  let depth = 0;
  for (const ch of s) {
    if (ch === "{") depth += 1;
    else if (ch === "}" && depth > 0) depth -= 1;
  }
  return depth > 0 ? `${s}${"}".repeat(depth)}` : s;
}

// ── Math rendering ───────────────────────────────────────────────────────────

const LATEX_N_COMMANDS = new Set([
  "nabla",
  "natural",
  "neg",
  "neq",
  "ne",
  "ni",
  "not",
  "notin",
  "notni",
  "notag",
  "nonumber",
  "nolimits",
  "newline",
  "newcommand",
  "renewcommand",
  "newenvironment",
  "renewenvironment",
  "newtheorem",
  "newpage",
  "noindent",
  "null",
  "nu",
  "nwarrow",
  "nearrow",
  "nleftarrow",
  "nrightarrow",
  "nleftrightarrow",
  "nLeftarrow",
  "nRightarrow",
  "nLeftrightarrow",
  "nless",
  "ngtr",
  "nleq",
  "ngeq",
  "nleqq",
  "ngeqq",
  "nleqslant",
  "ngeqslant",
  "nsubset",
  "nsupset",
  "nsubseteq",
  "nsupseteq",
  "nsubseteqq",
  "nsupseteqq",
  "nmid",
  "nparallel",
  "ncong",
  "nprec",
  "nsucc",
  "npreceq",
  "nsucceq",
  "nexists",
  "nVDash",
  "nVdash",
  "nvDash",
  "nvdash",
]);

function normalizeLegacyTextFontCommands(text: string): string {
  const mathRe = /(\$\$[\s\S]+?\$\$|\$[\s\S]+?\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\))/g;
  let out = "";
  let last = 0;
  let match: RegExpExecArray | null;
  const cleanText = (s: string) =>
    s
      .replace(/\{\\bf\s+([^{}$]+)\}/g, "$1")
      .replace(/\{\\bf\s+/g, "");
  while ((match = mathRe.exec(text)) !== null) {
    out += cleanText(text.slice(last, match.index));
    out += match[0];
    last = match.index + match[0].length;
  }
  out += cleanText(text.slice(last));
  return out.replace(/(\$[\s\S]*?\$)\}\}/g, "$1");
}

function decodeEscapedNewlines(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    if (text.startsWith("\\n", i)) {
      let j = i + 2;
      let command = "n";
      while (/[A-Za-z]/.test(text[j] || "")) {
        command += text[j];
        j += 1;
      }
      if (!LATEX_N_COMMANDS.has(command)) {
        out += "\n";
        i += 2;
        continue;
      }
    }
    out += text[i];
    i += 1;
  }
  return out;
}

// Pre-process LaTeX before KaTeX: normalize pipeline-generated custom commands.
export function preprocessLatex(s: string): string {
  s = s.replace(/\u2212/g, "-");
  // \Abs{arg} or \Abs(arg) → |arg|
  s = s.replace(/\\Abs\s*\{([^}]*)\}/g, "|$1|");
  s = s.replace(/\\Abs\s*\(([^)]*)\)/g, "|$1|");
  // \Norm{arg} or \Norm(arg) → \|arg\|
  s = s.replace(/\\Norm\s*\{([^}]*)\}/g, "\\|$1\\|");
  s = s.replace(/\\Norm\s*\(([^)]*)\)/g, "\\|$1\\|");
  // \Map → \mathrm{Map}
  s = s.replace(/\\Map\b/g, "\\mathrm{Map}");
  // \ConvergesTo → \to
  s = s.replace(/\\ConvergesTo\b/g, "\\to");
  // \operatorname* → \operatorname (KaTeX doesn't support starred form)
  s = s.replace(/\\operatorname\s*[*]/g, "\\operatorname");
  // "l i m" style spaced-out text inside {} after _ → collapse
  s = s.replace(/\{\s*([a-zA-Z])\s+([a-zA-Z])\s+([a-zA-Z])\s*\}/g, "{$1$2$3}");
  s = s.replace(/\\(operatorname|mathrm|text)\s*\{\s*([A-Za-z](?:\s+[A-Za-z]){1,})\s*\}/g, (_, cmd: string, body: string) =>
    `\\${cmd}{${body.replace(/\s+/g, "")}}`
  );
  s = s.replace(/\\textbf\s*\{\s*\\mathit\s*\{([^{}]+)\}\s*\}/g, "\\boldsymbol{$1}");
  s = s.replace(/\\textit\s*\{\s*\\mathit\s*\{([^{}]+)\}\s*\}/g, "\\mathit{$1}");
  s = s.replace(/\\textbf\s*\{\s*([^{}]+)\s*\}/g, "\\mathbf{$1}");
  s = s.replace(/\\textit\s*\{\s*([^{}]+)\s*\}/g, "\\mathit{$1}");
  s = s.replace(/\{\\bf\s+([A-Za-z]+)\}/g, "\\mathbf{$1}");
  // \infty without \to before it in subscripts (e.g. _ { m \infty } → _{m \to \infty})
  s = s.replace(/(_\s*\{\s*[a-zA-Z]\s*)\\infty/g, "$1\\to\\infty");
  // Pipeline pseudo-commands: \text{Equivalence/Implication/...} → proper symbols
  s = s.replace(/\\text\s*\{\s*Equivalence\s*\}/g, "\\leftrightarrow");
  s = s.replace(/\\text\s*\{\s*Implication\s*\}/g, "\\Rightarrow");
  s = s.replace(/\\text\s*\{\s*Conjunction\s*\}/g, "\\wedge");
  s = s.replace(/\\text\s*\{\s*Disjunction\s*\}/g, "\\vee");
  // \joinrel → remove (pure spacing/joining glyph, meaningless alone)
  s = s.replace(/\\joinrel/g, "");
  // \Limit → \lim
  s = s.replace(/\\Limit\b/g, "\\lim");
  // \VSubset → \Subset (double subset ⋐, supported by KaTeX)
  s = s.replace(/\\VSubset\b/g, "\\Subset");
  // Old TeX font switches sometimes appear in OCR/PDF output.
  s = s.replace(/\\em\s+([A-Za-z])/g, "\\mathit{$1}");
  s = s.replace(/\\cal\s+([A-Za-z])/g, "\\mathcal{$1}");
  s = s.replace(/\\sf\s+([A-Za-z])/g, "\\mathsf{$1}");
  s = s.replace(/\\textbf\s*\{\s*\\mathit\s*\{([^{}]+)\}\s*\}/g, "\\boldsymbol{$1}");
  s = s.replace(/\\textit\s*\{\s*\\mathit\s*\{([^{}]+)\}\s*\}/g, "\\mathit{$1}");
  return balanceLatexBraces(s);
}

function parseImplicitMathSegments(text: string): Array<{ type: "text" | "math"; src: string; display: boolean }> {
  const segments: Array<{ type: "text" | "math"; src: string; display: boolean }> = [];
  const trigger = /\\[A-Za-z]+|\\[^\sA-Za-z]|[A-Za-zΑ-ω]\s*[_^]\s*(?:\{[^{}]+\}|[A-Za-z0-9Α-ω]+)|[_^]\s*\{[^{}]+\}/g;
  const mathChar = /[A-Za-z0-9Α-ω∞∂∇≤≥≠≈∈∉⊂⊆⊊⊃⊇∪∩∫∑∏√±×·→←↔⇒⇔()[\]{}.,:;+\-*/=<>|_^\\\s]/;
  const shortLeft = /(?:[()[\]{}|+\-*/=<>]\s*)?(?:[A-Za-zΑ-ω]{1,3}|\d+)\s*$/;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = trigger.exec(text)) !== null) {
    let start = m.index;
    const prefix = text.slice(last, start);
    let left = prefix.match(shortLeft)?.[0] ?? "";
    const leftStart = start - left.length;
    if (left && leftStart > last && /[A-Za-z]$/.test(text[leftStart - 1])) left = "";
    if (/^(if|is|in|of|to|as|or|and|for|the|then|with)$/i.test(left.trim())) left = "";
    if (left && !/\s[A-Za-z]{3,}\s*$/.test(left)) start -= left.length;

    let end = m.index + m[0].length;
    while (end < text.length && mathChar.test(text[end])) {
      if (/^-(?:th|st|nd|rd)\b/i.test(text.slice(end))) break;
      if (/\s/.test(text[end])) {
        const rest = text.slice(end);
        const word = rest.match(/^\s+([A-Za-z]+)(?!\s*[_^])/);
        if (word && (word[1].length > 1 || /^(if|is|in|of|to|as|or|and|for|the|then|with)$/i.test(word[1]))) break;
      }
      end++;
    }

    const raw = text.slice(start, end);
    const src = raw.trim();
    if (!src) continue;
    if (start > last) segments.push({ type: "text", src: text.slice(last, start), display: false });
    segments.push({ type: "math", src: preprocessLatex(src), display: false });
    last = end;
    trigger.lastIndex = end;
  }
  if (last < text.length) segments.push({ type: "text", src: text.slice(last), display: false });
  return segments.length ? segments : [{ type: "text", src: text, display: false }];
}

// Splits text into alternating plain / math segments.
// Handles: $...$ \(...\) inline; $$...$$ \[...\] display.
// Also handles unclosed $$ or \[ by treating everything after as display math.
export function parseMathSegments(text: string): Array<{ type: "text" | "math"; src: string; display: boolean; unclosed?: boolean }> {
  const segments: Array<{ type: "text" | "math"; src: string; display: boolean; unclosed?: boolean }> = [];
  const pushText = (src: string) => {
    if (!src) return;
    segments.push(...parseImplicitMathSegments(src));
  };
  // Closed display, closed inline, THEN unclosed display (must be last alternative)
  const re = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$[\s\S]+?\$|\\\([\s\S]+?\\\)|\$\$[\s\S]+$|\\\[[\s\S]+$)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) pushText(text.slice(last, m.index));
    const raw = m[0];
    const isDisplay = raw.startsWith("$$") || raw.startsWith("\\[");
    // An unclosed run is matched only by the trailing `...$` alternatives,
    // i.e. it has an opening delimiter but no matching closing one.
    const unclosed =
      (raw.startsWith("$$") && !/\$\$$/.test(raw)) ||
      (raw.startsWith("\\[") && !/\\\]$/.test(raw));
    // Extract inner content (strip open + close delimiters, or just open for unclosed)
    let inner = raw;
    if (raw.startsWith("$$"))      inner = raw.replace(/^\$\$/, "").replace(/\$\$$/, "");
    else if (raw.startsWith("\\[")) inner = raw.replace(/^\\\[/, "").replace(/\\\]$/, "");
    else if (raw.startsWith("\\(")) inner = raw.replace(/^\\\(/, "").replace(/\\\)$/, "");
    else                            inner = raw.replace(/^\$/, "").replace(/\$$/, "");
    segments.push({ type: "math", src: preprocessLatex(inner.trim()), display: isDisplay, unclosed });
    last = m.index + raw.length;
  }
  if (last < text.length) pushText(text.slice(last));
  return segments;
}

export const KATEX_MACROS: Record<string, string> = {
  "\\R": "\\mathbb{R}", "\\N": "\\mathbb{N}", "\\Z": "\\mathbb{Z}",
  "\\C": "\\mathbb{C}", "\\Q": "\\mathbb{Q}",
  "\\emu": "u", "\\emv": "v",
  "\\abs": "|#1|", "\\norm": "\\|#1\\|",
  "\\HF": "\\mathrm{H}\\mathbb{F}_2",
  "\\BESS": "\\mathrm{BESS}",
  "\\SynSS": "\\mathrm{SynSS}",
  "\\ASS": "\\mathrm{ASS}",
  "\\Ext": "\\operatorname{Ext}",
  "\\Hom": "\\operatorname{Hom}",
  "\\Map": "\\operatorname{Map}",
  "\\Spec": "\\operatorname{Spec}",
  "\\Syn": "\\operatorname{Syn}",
  "\\ba": "\\boldsymbol{a}",
  "\\bb": "\\boldsymbol{b}",
  "\\bc": "\\boldsymbol{c}",
  "\\bd": "\\boldsymbol{d}",
  "\\be": "\\boldsymbol{e}",
  "\\bf": "\\boldsymbol{f}",
  "\\bg": "\\boldsymbol{g}",
  "\\bp": "\\boldsymbol{p}",
  "\\bq": "\\boldsymbol{q}",
  "\\br": "\\boldsymbol{r}",
  "\\bs": "\\boldsymbol{s}",
  "\\bu": "\\boldsymbol{u}",
  "\\bv": "\\boldsymbol{v}",
  "\\bw": "\\boldsymbol{w}",
  "\\bx": "\\boldsymbol{x}",
  "\\by": "\\boldsymbol{y}",
  "\\bz": "\\boldsymbol{z}",
  "\\bD": "\\boldsymbol{D}",
  "\\bF": "\\boldsymbol{F}",
  "\\bI": "\\boldsymbol{I}",
  "\\bO": "\\boldsymbol{O}",
  "\\bbf": "\\boldsymbol{f}",
  "\\bgf": "\\boldsymbol{g}",
  "\\balpha": "\\boldsymbol{\\alpha}",
  "\\bbeta": "\\boldsymbol{\\beta}",
  "\\bgamma": "\\boldsymbol{\\gamma}",
  "\\bdelta": "\\boldsymbol{\\delta}",
  "\\bepsilon": "\\boldsymbol{\\epsilon}",
  "\\bzeta": "\\boldsymbol{\\zeta}",
  "\\btheta": "\\boldsymbol{\\theta}",
  "\\biota": "\\boldsymbol{\\iota}",
  "\\bkappa": "\\boldsymbol{\\kappa}",
  "\\blambda": "\\boldsymbol{\\lambda}",
  "\\bmu": "\\boldsymbol{\\mu}",
  "\\bnu": "\\boldsymbol{\\nu}",
  "\\bxi": "\\boldsymbol{\\xi}",
  "\\bpi": "\\boldsymbol{\\pi}",
  "\\brho": "\\boldsymbol{\\rho}",
  "\\bsigma": "\\boldsymbol{\\sigma}",
  "\\btau": "\\boldsymbol{\\tau}",
  "\\bupsilon": "\\boldsymbol{\\upsilon}",
  "\\bphi": "\\boldsymbol{\\phi}",
  "\\bchi": "\\boldsymbol{\\chi}",
  "\\bpsi": "\\boldsymbol{\\psi}",
  "\\bomega": "\\boldsymbol{\\omega}",
  "\\bvarepsilon": "\\boldsymbol{\\varepsilon}",
  "\\bvarphi": "\\boldsymbol{\\varphi}",
  "\\bvartheta": "\\boldsymbol{\\vartheta}",
  "\\bvarpi": "\\boldsymbol{\\varpi}",
  "\\bvarrho": "\\boldsymbol{\\varrho}",
  "\\bvarsigma": "\\boldsymbol{\\varsigma}",
  "\\bGamma": "\\boldsymbol{\\Gamma}",
  "\\bDelta": "\\boldsymbol{\\Delta}",
  "\\bTheta": "\\boldsymbol{\\Theta}",
  "\\bLambda": "\\boldsymbol{\\Lambda}",
  "\\bXi": "\\boldsymbol{\\Xi}",
  "\\bPi": "\\boldsymbol{\\Pi}",
  "\\bSigma": "\\boldsymbol{\\Sigma}",
  "\\bUpsilon": "\\boldsymbol{\\Upsilon}",
  "\\bPhi": "\\boldsymbol{\\Phi}",
  "\\bPsi": "\\boldsymbol{\\Psi}",
  "\\bOmega": "\\boldsymbol{\\Omega}",
  "\\mcA": "\\mathcal{A}",
  "\\mcB": "\\mathcal{B}",
  "\\mcC": "\\mathcal{C}",
  "\\mcD": "\\mathcal{D}",
  "\\mcE": "\\mathcal{E}",
  "\\mcF": "\\mathcal{F}",
  "\\mcG": "\\mathcal{G}",
  "\\mcH": "\\mathcal{H}",
  "\\mcI": "\\mathcal{I}",
  "\\mcJ": "\\mathcal{J}",
  "\\mcK": "\\mathcal{K}",
  "\\mcL": "\\mathcal{L}",
  "\\mcM": "\\mathcal{M}",
  "\\mcN": "\\mathcal{N}",
  "\\mcO": "\\mathcal{O}",
  "\\mcP": "\\mathcal{P}",
  "\\mcQ": "\\mathcal{Q}",
  "\\mcR": "\\mathcal{R}",
  "\\mcS": "\\mathcal{S}",
  "\\mcT": "\\mathcal{T}",
  "\\mcU": "\\mathcal{U}",
  "\\mcV": "\\mathcal{V}",
  "\\mcW": "\\mathcal{W}",
  "\\mcX": "\\mathcal{X}",
  "\\mcY": "\\mathcal{Y}",
  "\\mcZ": "\\mathcal{Z}",
};

export type LatexMacros = Record<string, string>;

export function mergeKatexMacros(macros?: LatexMacros): LatexMacros {
  return macros ? { ...KATEX_MACROS, ...macros } : KATEX_MACROS;
}

function normalizeTexTextForMathText(text: string): string {
  if (!text) return text;

  text = decodeEscapedNewlines(text);
  text = normalizeLegacyTextFontCommands(text);
  text = unwrapTexParagraphCommands(text);

  text = text.replace(
    /\\begin\{(equation\*?|gather\*?|multline\*?)\}([\s\S]*?)\\end\{\1\}/g,
    (_, _env: string, body: string) => `\n\n$$\n${String(body).replace(/\\label\s*\{[^{}]*\}/g, "").trim()}\n$$\n\n`
  );
  text = text.replace(
    /\\begin\{align\*?\}([\s\S]*?)\\end\{align\*?\}/g,
    (_, body: string) => `\n\n$$\n\\begin{aligned}\n${String(body).replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\nonumber/g, "").trim()}\n\\end{aligned}\n$$\n\n`
  );
  text = text.replace(
    /\\begin\{eqnarray\*?\}([\s\S]*?)\\end\{eqnarray\*?\}/g,
    (_, body: string) => `\n\n$$\n\\begin{aligned}\n${String(body).replace(/\\label\s*\{[^{}]*\}/g, "").replace(/\\nonumber/g, "").trim()}\n\\end{aligned}\n$$\n\n`
  );

  const convertList = (_match: string, env: string, body: string) => {
    const ordered = env === "enumerate";
    const items: Array<{ label?: string; body: string }> = [];
    const itemRe = /\\item(?:\s*\[([^\]]*)\])?/g;
    let match: RegExpExecArray | null;
    let currentLabel: string | undefined;
    let currentStart = -1;
    while ((match = itemRe.exec(String(body))) !== null) {
      if (currentStart >= 0) {
        const itemBody = String(body).slice(currentStart, match.index).trim();
        if (itemBody) items.push({ label: currentLabel, body: itemBody });
      }
      currentLabel = match[1]?.trim();
      currentStart = itemRe.lastIndex;
    }
    if (currentStart >= 0) {
      const itemBody = String(body).slice(currentStart).trim();
      if (itemBody) items.push({ label: currentLabel, body: itemBody });
    }
    if (!items.length) return "";
    return "\n\n" + items.map((item, i) => {
      const prefix = item.label || (ordered ? `${i + 1}.` : "-");
      return `${prefix} ${item.body}`;
    }).join("\n") + "\n\n";
  };
  let previous = "";
  while (previous !== text) {
    previous = text;
    text = text.replace(/\\begin\{(itemize|enumerate)\}([\s\S]*?)\\end\{\1\}/g, convertList);
  }

  text = text.replace(/\\label\s*\{[^{}]*\}/g, "");
  text = text.replace(/\\nonumber/g, "");
  text = text.replace(/\\eqref\s*\{([^{}]+)\}/g, (_, key: string) => `(${String(key).split(":").pop() || key})`);
  text = text.replace(/\\(?:autoref|cref|Cref|nameref|ref)\s*\{([^{}]+)\}/g, (_, key: string) => String(key).split(":").pop() || key);
  text = unwrapTexColorCommands(text);
  text = text.replace(/\{\\color\s*\{[^{}]+\}\s*\{\{\\bf\s+([^{}]*)\}\}\}/g, "$1");
  text = text.replace(/\\color\s*\{[^{}]+\}\s*\{\{\\bf\s+([^{}]*)\}\}/g, "$1");
  let colorPrevious = "";
  while (colorPrevious !== text) {
    colorPrevious = text;
    text = text.replace(/\\color\s*\{[^{}]+\}\s*\{([^{}]*)\}/g, "$1");
    text = text.replace(/\{\\color\s*\{[^{}]+\}\s*\{([^{}]*)\}\}/g, "$1");
    text = text.replace(/\\color\s*\{[^{}]+\}\s*([^{}\\]*(?:\\(?:ref|eqref)\{[^{}]+\})?[^{}]*)/g, "$1");
  }
  text = text.replace(/\{\\bf\s+([^{}]+)\}/g, (_, body: string) => {
    const trimmed = String(body).trim();
    return /^[A-Za-z]+$/.test(trimmed) ? `\\mathbf{${trimmed}}` : trimmed;
  });
  text = text.replace(/\\textbf\s*\{([^{}]+)\}/g, (_, body: string) => {
    const trimmed = String(body).trim();
    return /^[A-Za-z]+$/.test(trimmed) ? `\\mathbf{${trimmed}}` : trimmed;
  });
  return text;
}

function readTexGroup(text: string, start: number): { body: string; end: number } | null {
  if (text[start] !== "{") return null;
  let depth = 0;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return { body: text.slice(start + 1, i), end: i + 1 };
    }
  }
  return null;
}

function unwrapTexParagraphCommands(text: string): string {
  const command = /\\par\b/g;
  let out = "";
  let pos = 0;
  let match: RegExpExecArray | null;
  while ((match = command.exec(text)) !== null) {
    out += text.slice(pos, match.index);
    let cursor = command.lastIndex;
    while (/\s/.test(text[cursor] || "")) cursor++;
    const body = readTexGroup(text, cursor);
    if (body) {
      out += body.body;
      pos = body.end;
      command.lastIndex = body.end;
    } else {
      out += "\n\n";
      pos = cursor;
      command.lastIndex = cursor;
    }
  }
  return out + text.slice(pos);
}

function unwrapTexColorCommands(text: string): string {
  const needle = "\\color";
  let out = "";
  let pos = 0;
  while (true) {
    const start = text.indexOf(needle, pos);
    if (start < 0) return out + text.slice(pos);
    out += text.slice(pos, start);
    let cursor = start + needle.length;
    while (/\s/.test(text[cursor] || "")) cursor++;
    const color = readTexGroup(text, cursor);
    if (!color) {
      out += needle;
      pos = start + needle.length;
      continue;
    }
    cursor = color.end;
    while (/\s/.test(text[cursor] || "")) cursor++;
    const body = readTexGroup(text, cursor);
    if (!body) {
      pos = cursor;
      continue;
    }
    if (out.endsWith("{") && text[body.end] === "}") {
      out = out.slice(0, -1);
      pos = body.end + 1;
    } else {
      pos = body.end;
    }
    out += body.body;
  }
}

export function MathText({ text, className, style, macros }: { text: string; className?: string; style?: React.CSSProperties; macros?: LatexMacros }) {
  if (!text) return null;
  const segs = parseMathSegments(normalizeTexTextForMathText(text));
  const mergedMacros = mergeKatexMacros(macros);
  return (
    <span className={className} style={style}>
      {segs.map((s, i) => {
        if (s.type === "text") return <span key={i}>{s.src}</span>;
        try {
          const html = katex.renderToString(s.src, {
            displayMode: s.display,
            throwOnError: s.unclosed ? true : false,
            errorColor: "#999999",
            output: "html",
            strict: false,
            macros: mergedMacros,
          });
          return <span key={i} dangerouslySetInnerHTML={{ __html: html }} />;
        } catch {
          // An unclosed/truncated delimiter would otherwise dump raw LaTeX
          // source; drop it gracefully rather than exposing the source.
          if (s.unclosed) return <span key={i} style={{ color: "var(--muted)" }}>…</span>;
          // Last resort for a malformed but closed formula: show raw source.
          return <span key={i} style={{ fontFamily: "monospace", fontSize: "0.88em", color: "var(--muted)" }}>{s.src}</span>;
        }
      })}
    </span>
  );
}

// Renders a title that may contain undelimited LaTeX (e.g. W^{k,p}).
// If the text has math markers or a raw LaTeX command, delegates to MathText.
// Otherwise converts to Unicode via labelText() which is always readable.
export function SmartTitle({ text, className, style, macros }: { text: string; className?: string; style?: React.CSSProperties; macros?: LatexMacros }) {
  if (/\$|\\\(|\\\[|\\[A-Za-z]+/.test(text)) {
    return <MathText text={text} className={className} style={style} macros={macros} />;
  }
  return <span className={className} style={style}>{labelText(text)}</span>;
}
