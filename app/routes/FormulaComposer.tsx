import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PanelBottomClose } from "lucide-react";
import type { MathfieldElement, Selection } from "mathlive";
import { MathText, type LatexMacros } from "./math";
import {
  applyMatrixDelimiter,
  CALCULUS_OPERATOR_TEMPLATES,
  customizeFormulaMenuItems,
  matrixLatex,
  type FormulaPresentation,
  type MatrixEnvironment,
} from "./formula-input";
import { FloatingWorkspaceWindow } from "./FloatingWorkspaceWindow";
import {
  computeKeyboardDismissPosition,
  type FloatingTopRightPosition,
} from "./floating-window";
import "mathlive/fonts.css";

type FormulaCategory = "common" | "relations" | "sets" | "calculus" | "greek" | "matrix";

interface FormulaTemplate {
  label: string;
  latex: string;
  previewLatex: string;
  hint?: string;
}

interface FormulaComposerProps {
  anchorElement: HTMLElement | null;
  initialValue: string;
  initialPresentation: FormulaPresentation;
  editing: boolean;
  macros?: LatexMacros;
  active: boolean;
  onActivate: () => void;
  onCommit: (latex: string, presentation: FormulaPresentation) => void;
  onCancel: () => void;
}

const CATEGORIES: Array<{ id: FormulaCategory; label: string }> = [
  { id: "common", label: "常用" },
  { id: "relations", label: "关系" },
  { id: "sets", label: "集合与逻辑" },
  { id: "calculus", label: "微积分" },
  { id: "greek", label: "希腊字母" },
  { id: "matrix", label: "矩阵" },
];

const TEMPLATES: Record<Exclude<FormulaCategory, "matrix">, FormulaTemplate[]> = {
  common: [
    { label: "分数", latex: "\\frac{#@}{#?}", previewLatex: "\\frac{a}{b}" },
    { label: "上标", latex: "#@^{#?}", previewLatex: "x^n" },
    { label: "下标", latex: "#@_{#?}", previewLatex: "x_i" },
    { label: "平方根", latex: "\\sqrt{#0}", previewLatex: "\\sqrt{x}" },
    { label: "n 次根", latex: "\\sqrt[#?]{#0}", previewLatex: "\\sqrt[n]{x}" },
    { label: "括号", latex: "\\left( #0 \\right)", previewLatex: "\\left(x\\right)" },
    { label: "绝对值", latex: "\\left|#0\\right|", previewLatex: "\\left|x\\right|" },
    { label: "范数", latex: "\\left\\|#0\\right\\|", previewLatex: "\\left\\|x\\right\\|" },
    { label: "正负号", latex: "\\pm", previewLatex: "\\pm" },
    { label: "点乘", latex: "\\cdot", previewLatex: "a\\cdot b" },
    { label: "叉乘", latex: "\\times", previewLatex: "a\\times b" },
    { label: "无穷", latex: "\\infty", previewLatex: "\\infty" },
  ],
  relations: [
    { label: "等于", latex: "=", previewLatex: "=" },
    { label: "不等于", latex: "\\ne", previewLatex: "\\ne" },
    { label: "小于等于", latex: "\\le", previewLatex: "\\le" },
    { label: "大于等于", latex: "\\ge", previewLatex: "\\ge" },
    { label: "约等于", latex: "\\approx", previewLatex: "\\approx" },
    { label: "正比于", latex: "\\propto", previewLatex: "\\propto" },
    { label: "趋向", latex: "\\to", previewLatex: "\\to" },
    { label: "推出", latex: "\\Rightarrow", previewLatex: "\\Rightarrow" },
    { label: "双向箭头", latex: "\\leftrightarrow", previewLatex: "\\leftrightarrow" },
    { label: "相似", latex: "\\sim", previewLatex: "\\sim" },
    { label: "加号", latex: "+", previewLatex: "+" },
    { label: "减号", latex: "-", previewLatex: "-" },
  ],
  sets: [
    { label: "属于", latex: "\\in", previewLatex: "\\in" },
    { label: "不属于", latex: "\\notin", previewLatex: "\\notin" },
    { label: "真子集", latex: "\\subset", previewLatex: "\\subset" },
    { label: "子集", latex: "\\subseteq", previewLatex: "\\subseteq" },
    { label: "真超集", latex: "\\supset", previewLatex: "\\supset" },
    { label: "超集", latex: "\\supseteq", previewLatex: "\\supseteq" },
    { label: "空集", latex: "\\varnothing", previewLatex: "\\varnothing" },
    { label: "并集", latex: "\\cup", previewLatex: "\\cup" },
    { label: "交集", latex: "\\cap", previewLatex: "\\cap" },
    { label: "任意", latex: "\\forall", previewLatex: "\\forall" },
    { label: "存在", latex: "\\exists", previewLatex: "\\exists" },
    { label: "否定", latex: "\\neg", previewLatex: "\\neg" },
    { label: "自然数", latex: "\\mathbb{N}", previewLatex: "\\mathbb{N}" },
    { label: "整数", latex: "\\mathbb{Z}", previewLatex: "\\mathbb{Z}" },
    { label: "有理数", latex: "\\mathbb{Q}", previewLatex: "\\mathbb{Q}" },
    { label: "实数", latex: "\\mathbb{R}", previewLatex: "\\mathbb{R}" },
    { label: "复数", latex: "\\mathbb{C}", previewLatex: "\\mathbb{C}" },
  ],
  calculus: [
    { label: "求和", ...CALCULUS_OPERATOR_TEMPLATES.sum },
    { label: "连乘", ...CALCULUS_OPERATOR_TEMPLATES.product },
    { label: "积分", latex: "\\int_{#?}^{#?}", previewLatex: "\\int_a^b" },
    { label: "偏微分", latex: "\\partial", previewLatex: "\\partial" },
    { label: "梯度", latex: "\\nabla", previewLatex: "\\nabla" },
    { label: "极限", ...CALCULUS_OPERATOR_TEMPLATES.limit },
    { label: "导数", latex: "\\frac{d#@}{d#?}", previewLatex: "\\frac{dy}{dx}" },
    { label: "偏导数", latex: "\\frac{\\partial #@}{\\partial #?}", previewLatex: "\\frac{\\partial f}{\\partial x}" },
    { label: "角", latex: "\\angle", previewLatex: "\\angle ABC" },
    { label: "向量", latex: "\\vec{#0}", previewLatex: "\\vec{v}" },
  ],
  greek: [
    { label: "阿尔法", latex: "\\alpha", previewLatex: "\\alpha" },
    { label: "贝塔", latex: "\\beta", previewLatex: "\\beta" },
    { label: "伽马", latex: "\\gamma", previewLatex: "\\gamma" },
    { label: "德尔塔", latex: "\\delta", previewLatex: "\\delta" },
    { label: "艾普西龙", latex: "\\epsilon", previewLatex: "\\epsilon" },
    { label: "泽塔", latex: "\\zeta", previewLatex: "\\zeta" },
    { label: "伊塔", latex: "\\eta", previewLatex: "\\eta" },
    { label: "西塔", latex: "\\theta", previewLatex: "\\theta" },
    { label: "约塔", latex: "\\iota", previewLatex: "\\iota" },
    { label: "卡帕", latex: "\\kappa", previewLatex: "\\kappa" },
    { label: "拉姆达", latex: "\\lambda", previewLatex: "\\lambda" },
    { label: "缪", latex: "\\mu", previewLatex: "\\mu" },
    { label: "纽", latex: "\\nu", previewLatex: "\\nu" },
    { label: "克西", latex: "\\xi", previewLatex: "\\xi" },
    { label: "奥密克戎", latex: "o", previewLatex: "o" },
    { label: "派", latex: "\\pi", previewLatex: "\\pi" },
    { label: "柔", latex: "\\rho", previewLatex: "\\rho" },
    { label: "西格马", latex: "\\sigma", previewLatex: "\\sigma" },
    { label: "陶", latex: "\\tau", previewLatex: "\\tau" },
    { label: "宇普西龙", latex: "\\upsilon", previewLatex: "\\upsilon" },
    { label: "斐", latex: "\\phi", previewLatex: "\\phi" },
    { label: "希", latex: "\\chi", previewLatex: "\\chi" },
    { label: "普西", latex: "\\psi", previewLatex: "\\psi" },
    { label: "欧米伽", latex: "\\omega", previewLatex: "\\omega" },
    { label: "大写伽马", latex: "\\Gamma", previewLatex: "\\Gamma" },
    { label: "大写德尔塔", latex: "\\Delta", previewLatex: "\\Delta" },
    { label: "大写西塔", latex: "\\Theta", previewLatex: "\\Theta" },
    { label: "大写拉姆达", latex: "\\Lambda", previewLatex: "\\Lambda" },
    { label: "大写克西", latex: "\\Xi", previewLatex: "\\Xi" },
    { label: "大写派", latex: "\\Pi", previewLatex: "\\Pi" },
    { label: "大写西格马", latex: "\\Sigma", previewLatex: "\\Sigma" },
    { label: "大写宇普西龙", latex: "\\Upsilon", previewLatex: "\\Upsilon" },
    { label: "大写斐", latex: "\\Phi", previewLatex: "\\Phi" },
    { label: "大写普西", latex: "\\Psi", previewLatex: "\\Psi" },
    { label: "大写欧米伽", latex: "\\Omega", previewLatex: "\\Omega" },
  ],
};

function isFormulaEmpty(latex: string) {
  return !latex.replace(/\\placeholder\{\}/g, "").trim();
}

function getMathVirtualKeyboard() {
  if (typeof window === "undefined" || !("mathVirtualKeyboard" in window)) return null;
  return window.mathVirtualKeyboard;
}

function copySelection(selection: Readonly<Selection>): Selection {
  return {
    ranges: selection.ranges.map(([start, end]) => [start, end]),
    direction: selection.direction,
  };
}

export function FormulaComposer({ anchorElement, initialValue, initialPresentation, editing, macros, active, onActivate, onCommit, onCancel }: FormulaComposerProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const fieldRef = useRef<MathfieldElement | null>(null);
  const lastSelectionRef = useRef<Selection | null>(null);
  const [category, setCategory] = useState<FormulaCategory>("common");
  const [presentation, setPresentation] = useState<FormulaPresentation>(initialPresentation);
  const [value, setValue] = useState(initialValue);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [matrixRows, setMatrixRows] = useState(3);
  const [matrixColumns, setMatrixColumns] = useState(3);
  const [matrixEnvironment, setMatrixEnvironment] = useState<MatrixEnvironment>("bmatrix");
  const [keyboardDismissPosition, setKeyboardDismissPosition] = useState<FloatingTopRightPosition | null>(null);

  useEffect(() => {
    let disposed = false;
    let field: MathfieldElement | null = null;
    let onInput: (() => void) | null = null;
    let onSelectionChange: (() => void) | null = null;
    let keyboard: Window["mathVirtualKeyboard"] | null = null;
    let keyboardAnimationFrame = 0;
    let scheduleKeyboardDismissSync: (() => void) | null = null;
    setLoadState("loading");
    setError("");
    setKeyboardDismissPosition(null);
    void import("mathlive")
      .then(({ MathfieldElement }) => {
        if (disposed || !hostRef.current) return;
        if (!customElements.get("math-field")) customElements.define("math-field", MathfieldElement);
        MathfieldElement.strings = {
          "zh-cn": {
            "menu.insert": "插入",
            "menu.insert.abs": "绝对值",
            "menu.insert.nth-root": "n 次根",
            "menu.insert.log-base": "以 a 为底的对数",
            "menu.insert.heading-calculus": "微积分",
            "menu.insert.derivative": "导数",
            "menu.insert.nth-derivative": "n 阶导数",
            "menu.insert.integral": "积分",
            "menu.insert.sum": "求和",
            "menu.insert.product": "连乘",
            "menu.insert.heading-complex-numbers": "复数",
            "menu.insert.modulus": "模",
            "menu.insert.argument": "辐角",
            "menu.insert.real-part": "实部",
            "menu.insert.imaginary-part": "虚部",
            "menu.insert.conjugate": "共轭",
            "tooltip.menu": "菜单",
          },
        };
        MathfieldElement.locale = "zh-cn";
        field = new MathfieldElement();
        field.className = "proof-ws-mathfield";
        field.setAttribute("aria-label", "公式编辑器");
        field.setAttribute("placeholder", "在这里输入公式");
        field.smartFence = true;
        field.mathVirtualKeyboardPolicy = "manual";
        if (macros && Object.keys(macros).length > 0) field.macros = macros;
        field.value = initialValue;
        onInput = () => setValue(field?.getValue("latex") || "");
        onSelectionChange = () => {
          if (field) lastSelectionRef.current = copySelection(field.selection);
        };
        field.addEventListener("input", onInput);
        field.addEventListener("selection-change", onSelectionChange);
        hostRef.current.replaceChildren(field);
        lastSelectionRef.current = copySelection(field.selection);
        field.menuItems = customizeFormulaMenuItems(field.menuItems, (environment) => {
          if (!field) return;
          const result = applyMatrixDelimiter(field, lastSelectionRef.current, environment);
          setValue(result.latex);
          if (result.changed) lastSelectionRef.current = copySelection(field.selection);
        });
        fieldRef.current = field;
        keyboard = getMathVirtualKeyboard();
        const syncKeyboardDismiss = () => {
          keyboardAnimationFrame = 0;
          if (disposed || !keyboard?.visible) {
            if (!disposed) setKeyboardDismissPosition(null);
            return;
          }
          const bounds = keyboard.boundingRect;
          const keyboardBounds = bounds.right > bounds.left && bounds.bottom > bounds.top
            ? bounds
            : { left: 0, right: window.innerWidth, top: 0, bottom: window.innerHeight };
          setKeyboardDismissPosition(computeKeyboardDismissPosition(
            keyboardBounds,
            { width: window.innerWidth, height: window.innerHeight },
          ));
        };
        scheduleKeyboardDismissSync = () => {
          if (keyboardAnimationFrame) window.cancelAnimationFrame(keyboardAnimationFrame);
          keyboardAnimationFrame = window.requestAnimationFrame(syncKeyboardDismiss);
        };
        keyboard?.addEventListener("virtual-keyboard-toggle", scheduleKeyboardDismissSync);
        keyboard?.addEventListener("geometrychange", scheduleKeyboardDismissSync);
        window.addEventListener("resize", scheduleKeyboardDismissSync);
        scheduleKeyboardDismissSync();
        setLoadState("ready");
        window.requestAnimationFrame(() => field?.focus());
      })
      .catch(() => {
        if (!disposed) {
          setLoadState("error");
          setError("公式编辑器加载失败，可继续手动输入 LaTeX。 ");
        }
      });
    return () => {
      disposed = true;
      if (keyboardAnimationFrame) window.cancelAnimationFrame(keyboardAnimationFrame);
      if (scheduleKeyboardDismissSync) {
        keyboard?.removeEventListener("virtual-keyboard-toggle", scheduleKeyboardDismissSync);
        keyboard?.removeEventListener("geometrychange", scheduleKeyboardDismissSync);
        window.removeEventListener("resize", scheduleKeyboardDismissSync);
      }
      keyboard?.hide({ animate: true });
      if (field && onInput) field.removeEventListener("input", onInput);
      if (field && onSelectionChange) field.removeEventListener("selection-change", onSelectionChange);
      field?.remove();
      fieldRef.current = null;
      lastSelectionRef.current = null;
    };
  }, [initialValue, loadAttempt, macros]);

  const insert = (latex: string) => {
    const field = fieldRef.current;
    if (!field) return;
    setError("");
    field.focus();
    field.insert(latex, { insertionMode: "replaceSelection" });
    setValue(field.getValue("latex"));
  };

  const insertMatrix = (rows: number, columns: number, environment = matrixEnvironment) => {
    insert(matrixLatex(rows, columns, environment));
    setPresentation("block");
  };

  const hideKeyboard = () => {
    setKeyboardDismissPosition(null);
    getMathVirtualKeyboard()?.hide({ animate: true });
  };

  const cancel = () => {
    hideKeyboard();
    onCancel();
  };

  const commit = () => {
    const field = fieldRef.current;
    const latex = field ? field.getValue("latex-without-placeholders") : value;
    if (isFormulaEmpty(latex)) {
      setError("请先输入公式内容。");
      field?.focus();
      return;
    }
    hideKeyboard();
    onCommit(latex, presentation);
  };

  const retry = () => setLoadAttempt((current) => current + 1);
  const templates = category === "matrix" ? [] : TEMPLATES[category];
  const matrixEnvironmentLabel = matrixEnvironment === "bmatrix" ? "方括号矩阵" : matrixEnvironment === "pmatrix" ? "圆括号矩阵" : "行列式";
  const keyboardPortalRoot = typeof document === "undefined" ? null : document.body;
  const keyboardDismissTheme = (anchorElement?.closest(".gs-root") as HTMLElement | null)?.dataset.theme || "light";

  return (
    <>
      <FloatingWorkspaceWindow
        anchorElement={anchorElement}
        title={editing ? "编辑公式" : "插入公式"}
        subtitle="点击符号或直接输入，拖动此处可移动浮窗"
        ariaLabel={editing ? "编辑公式" : "插入公式"}
        className="proof-ws-formula"
        preferredWidth={520}
        maxHeight={600}
        splitGraphHeight
        placement="top-right"
        active={active}
        onActivate={onActivate}
        onClose={cancel}
      >
      <div className="proof-ws-formula-field">
        <div className="proof-ws-formula-host" ref={hostRef} />
        {loadState === "loading" && <span className="proof-ws-formula-loading">正在加载公式编辑器…</span>}
        {loadState === "error" && (
          <div className="proof-ws-formula-load-error">
            <span>{error}</span>
            <button type="button" onClick={retry}>重试</button>
          </div>
        )}
      </div>

      <div className="proof-ws-formula-palette">
        <div className="proof-ws-formula-mode" role="group" aria-label="公式排版方式">
          <span>排版</span>
          <button type="button" className={presentation === "inline" ? "active" : ""} onClick={() => setPresentation("inline")}>行内公式</button>
          <button type="button" className={presentation === "block" ? "active" : ""} onClick={() => setPresentation("block")}>独立公式</button>
        </div>

        <div className="proof-ws-formula-tabs" role="tablist" aria-label="公式符号分类">
          {CATEGORIES.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={category === item.id}
              className={category === item.id ? "active" : ""}
              onClick={() => setCategory(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        {category !== "matrix" ? (
          <div className="proof-ws-formula-grid">
            {templates.map((item) => (
              <button
                key={`${item.label}-${item.latex}`}
                type="button"
                aria-label={`插入${item.label}`}
                title={item.hint || `${item.label} · ${item.latex}`}
                onClick={() => insert(item.latex)}
              >
                <MathText className="proof-ws-formula-template-preview" text={`\\(${item.previewLatex}\\)`} macros={macros} />
                <span className="proof-ws-formula-template-label">{item.label}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="proof-ws-matrix-panel">
            <div className="proof-ws-matrix-presets">
              {[2, 3, 4].map((size) => (
                <button key={size} type="button" aria-label={`插入 ${size}×${size} 矩阵`} onClick={() => insertMatrix(size, size)}>
                  <span className="proof-ws-matrix-preset-preview" style={{ gridTemplateColumns: `repeat(${size}, 3px)` }} aria-hidden="true">
                    {Array.from({ length: size * size }, (_, index) => <i key={index} />)}
                  </span>
                  <span>{size} × {size}</span>
                </button>
              ))}
            </div>
            <div className="proof-ws-matrix-controls">
              <label>行 <select value={matrixRows} onChange={(event) => setMatrixRows(Number(event.target.value))}>{Array.from({ length: 6 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}</select></label>
              <span>×</span>
              <label>列 <select value={matrixColumns} onChange={(event) => setMatrixColumns(Number(event.target.value))}>{Array.from({ length: 6 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}</select></label>
              <select aria-label="矩阵括号" value={matrixEnvironment} onChange={(event) => setMatrixEnvironment(event.target.value as MatrixEnvironment)}>
                <option value="bmatrix">[ ] 方括号</option>
                <option value="pmatrix">( ) 圆括号</option>
                <option value="vmatrix">| | 行列式</option>
              </select>
            </div>
            <button type="button" className="proof-ws-matrix-insert" onClick={() => insertMatrix(matrixRows, matrixColumns)}>
              <strong>插入 {matrixRows} × {matrixColumns}</strong>
              <span>{matrixEnvironmentLabel}</span>
            </button>
            <small>插入后点击单元格填写，按 Tab 移动到下一格。</small>
          </div>
        )}

        {error && loadState !== "error" && <div className="proof-ws-formula-error" role="alert">{error}</div>}
      </div>
        <div className="proof-ws-formula-actions">
          <button type="button" onClick={cancel}>取消</button>
          <button type="button" className="primary" onClick={commit} disabled={loadState !== "ready"}>{editing ? "更新公式" : "插入到光标处"}</button>
        </div>
      </FloatingWorkspaceWindow>
      {keyboardDismissPosition && keyboardPortalRoot && createPortal(
        <button
          type="button"
          className="proof-ws-keyboard-dismiss"
          data-theme={keyboardDismissTheme}
          style={{ top: keyboardDismissPosition.top, right: keyboardDismissPosition.right }}
          aria-label="收起虚拟键盘"
          title="收起虚拟键盘"
          onMouseDown={(event) => event.preventDefault()}
          onClick={hideKeyboard}
        >
          <PanelBottomClose size={21} aria-hidden="true" />
        </button>,
        keyboardPortalRoot,
      )}
    </>
  );
}
