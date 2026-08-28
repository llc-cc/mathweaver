import katex from "katex";
import { describe, expect, it } from "vitest";
import {
  applyMatrixDelimiter,
  buildFormulaTextSegments,
  CALCULUS_OPERATOR_TEMPLATES,
  commitFormula,
  customizeFormulaMenuItems,
  findFormulaAt,
  findFormulaRanges,
  matrixLatex,
  type MatrixDelimiterEnvironment,
} from "./formula-input";
import {
  clampFloatingPosition,
  computeFloatingPosition,
  computeKeyboardDismissPosition,
  computePointerTooltipPosition,
  computeVisibleGraphBounds,
  computeWorkspaceFloatingPosition,
  floatingWindowWidth,
  pointInClientRects,
  workspaceFloatingWindowWidth,
  workspaceSplitWindowHeight,
} from "./floating-window";

describe("formula composer floating placement", () => {
  it("aligns to the anchor right edge and prefers opening below", () => {
    expect(computeFloatingPosition(
      { left: 1080, right: 1200, top: 100, bottom: 132 },
      { width: 520, height: 400 },
      { width: 1280, height: 820 },
    )).toEqual({ left: 680, top: 140 });
  });

  it("opens above when the lower space is insufficient", () => {
    expect(computeFloatingPosition(
      { left: 840, right: 960, top: 700, bottom: 732 },
      { width: 520, height: 200 },
      { width: 1024, height: 820 },
    )).toEqual({ left: 440, top: 492 });
  });

  it("clamps dragged or resized positions to every viewport edge", () => {
    const size = { width: 520, height: 400 };
    const viewport = { width: 1024, height: 700 };
    expect(clampFloatingPosition({ left: -80, top: -40 }, size, viewport)).toEqual({ left: 12, top: 12 });
    expect(clampFloatingPosition({ left: 900, top: 600 }, size, viewport)).toEqual({ left: 492, top: 288 });
  });

  it("uses the preferred width while adapting to narrow viewports", () => {
    expect(floatingWindowWidth(520, 1280)).toBe(520);
    expect(floatingWindowWidth(520, 1024)).toBe(520);
    expect(floatingWindowWidth(520, 320)).toBe(296);
  });

  it("subtracts sidebars that actually cover the graph canvas", () => {
    const viewport = { width: 1280, height: 820 };
    expect(computeVisibleGraphBounds(
      { left: 0, right: 1280, top: 52, bottom: 820 },
      [
        { left: 0, right: 232, top: 52, bottom: 820 },
        { left: 920, right: 1280, top: 52, bottom: 820 },
      ],
      viewport,
    )).toEqual({ left: 232, right: 920, top: 52, bottom: 820, width: 688, height: 768 });

    expect(computeVisibleGraphBounds(
      { left: 232, right: 920, top: 52, bottom: 820 },
      [
        { left: 0, right: 232, top: 52, bottom: 820 },
        { left: 920, right: 1280, top: 52, bottom: 820 },
      ],
      viewport,
    )).toEqual({ left: 232, right: 920, top: 52, bottom: 820, width: 688, height: 768 });
  });

  it("right-aligns formula input above and preview below inside the visible graph", () => {
    const viewport = { width: 1280, height: 820 };
    const bounds = { left: 232, right: 920, top: 52, bottom: 820, width: 688, height: 768 };
    const previewWidth = workspaceFloatingWindowWidth(520, bounds.width, viewport.width);
    const formulaWidth = workspaceFloatingWindowWidth(520, bounds.width, viewport.width);
    const windowHeight = workspaceSplitWindowHeight(bounds.height, viewport.height);
    const formula = computeWorkspaceFloatingPosition(bounds, { width: formulaWidth, height: windowHeight }, viewport, "top-right");
    const preview = computeWorkspaceFloatingPosition(bounds, { width: previewWidth, height: windowHeight }, viewport, "bottom-right");

    expect(previewWidth).toBe(520);
    expect(formulaWidth).toBe(520);
    expect(windowHeight).toBe(366);
    expect(formula).toEqual({ left: 388, top: 64 });
    expect(preview).toEqual({ left: 388, top: 442 });
    expect(preview.left + previewWidth).toBe(908);
    expect(formula.left + formulaWidth).toBe(908);
    expect(preview.top - (formula.top + windowHeight)).toBe(12);
  });

  it("splits the graph height evenly while respecting the shared maximum", () => {
    expect(workspaceSplitWindowHeight(768, 820)).toBe(366);
    expect(workspaceSplitWindowHeight(648, 700)).toBe(306);
    expect(workspaceSplitWindowHeight(1400, 1440)).toBe(600);
  });

  it("keeps a 320px usable width when the graph is narrow, unless the viewport is narrower", () => {
    expect(workspaceFloatingWindowWidth(520, 260, 1024)).toBe(320);
    expect(workspaceFloatingWindowWidth(720, 260, 1024)).toBe(320);
    expect(workspaceFloatingWindowWidth(520, 260, 320)).toBe(296);

    const bounds = { left: 12, right: 272, top: 52, bottom: 700, width: 260, height: 648 };
    expect(computeWorkspaceFloatingPosition(
      bounds,
      { width: 320, height: 400 },
      { width: 1024, height: 700 },
      "bottom-right",
    )).toEqual({ left: 12, top: 288 });
  });

  it("keeps the virtual keyboard dismiss button inside its top-right corner and the viewport", () => {
    expect(computeKeyboardDismissPosition(
      { left: 0, right: 1280, top: 500, bottom: 820 },
      { width: 1280, height: 820 },
    )).toEqual({ top: 510, right: 12 });

    expect(computeKeyboardDismissPosition(
      { left: -20, right: 1300, top: -100, bottom: 820 },
      { width: 1280, height: 820 },
    )).toEqual({ top: 12, right: 12 });

    expect(computeKeyboardDismissPosition(
      { left: 0, right: 300, top: 690, bottom: 760 },
      { width: 320, height: 700 },
    )).toEqual({ top: 648, right: 30 });
  });
});

describe("formula hover geometry", () => {
  it("flips the tooltip beside the pointer and keeps it inside the viewport", () => {
    const size = { width: 240, height: 120 };
    const viewport = { width: 800, height: 600 };
    expect(computePointerTooltipPosition({ x: 100, y: 100 }, size, viewport)).toEqual({ left: 114, top: 114 });
    expect(computePointerTooltipPosition({ x: 760, y: 560 }, size, viewport)).toEqual({ left: 506, top: 426 });
    expect(computePointerTooltipPosition({ x: 4, y: 4 }, size, viewport, 14)).toEqual({ left: 18, top: 18 });
  });

  it("hits any rectangle of a wrapped formula but not the gaps around it", () => {
    const rects = [
      { left: 20, right: 180, top: 30, bottom: 50 },
      { left: 20, right: 90, top: 50, bottom: 70 },
    ];
    expect(pointInClientRects(rects, 160, 40)).toBe(true);
    expect(pointInClientRects(rects, 60, 60)).toBe(true);
    expect(pointInClientRects(rects, 140, 60)).toBe(false);
    expect(pointInClientRects(rects, 10, 40)).toBe(false);
  });
});

describe("formula input text integration", () => {
  it("inserts an inline formula at the beginning, middle, and end", () => {
    expect(commitFormula("证明", 0, 0, "x+1", "inline").text).toBe("\\(x+1\\)证明");
    expect(commitFormula("前后", 1, 1, "a=b", "inline").text).toBe("前\\(a=b\\)后");
    expect(commitFormula("证明", 2, 2, "x^2", "inline").text).toBe("证明\\(x^2\\)");
  });

  it("replaces a selected range while preserving surrounding Chinese text and line breaks", () => {
    const result = commitFormula("第一行\n旧内容\n第三行", 4, 7, "\\frac{1}{2}", "inline");
    expect(result.text).toBe("第一行\n\\(\\frac{1}{2}\\)\n第三行");
    expect(result.selectionStart).toBe(result.selectionEnd);
  });

  it("recognizes all supported delimiters and edits the formula under the caret", () => {
    const text = "a $x$ b $$y^2$$ c \\(z\\) d \\[A\\]";
    const ranges = findFormulaRanges(text);
    expect(ranges.map((item) => item.style)).toEqual([
      "dollar-inline",
      "dollar-block",
      "paren-inline",
      "bracket-block",
    ]);
    expect(findFormulaAt(text, text.indexOf("y^2") + 1)?.inner).toBe("y^2");
    expect(commitFormula(text, text.indexOf("z") + 1, text.indexOf("z") + 1, "w", "block").text).toContain("\\(w\\)");
    expect(commitFormula(text, text.indexOf("A") + 1, text.indexOf("A") + 1, "B", "inline").text).toContain("\\[B\\]");
  });

  it("segments Chinese, line breaks, and complete formulas without changing source text", () => {
    const text = "第一行 $x+1$\n第二行 \\[A\\]，再写 \\(z\\)。";
    const segments = buildFormulaTextSegments(text);
    expect(segments.map((item) => item.text).join("")).toBe(text);
    expect(segments.filter((item) => item.formula).map((item) => item.formula?.style)).toEqual([
      "dollar-inline",
      "bracket-block",
      "paren-inline",
    ]);
  });
});

describe("calculus operator templates", () => {
  it("provides two editable fields for the limit variable and destination", () => {
    expect(CALCULUS_OPERATOR_TEMPLATES.limit.latex).toBe("\\lim_{#?\\to #?}");
    expect(CALCULUS_OPERATOR_TEMPLATES.limit.latex.match(/#\?/g)).toHaveLength(2);
  });

  it("renders sum, product, and limit previews with display-style limits", () => {
    for (const template of Object.values(CALCULUS_OPERATOR_TEMPLATES)) {
      expect(template.previewLatex).toMatch(/^\\displaystyle/);
      expect(template.latex.match(/#\?/g)).toHaveLength(2);
      const markup = katex.renderToString(template.previewLatex, { throwOnError: true });
      expect(markup).toContain("op-limits");
    }
    expect(CALCULUS_OPERATOR_TEMPLATES.sum.previewLatex).toContain("\\sum_{i=1}^{n}");
    expect(CALCULUS_OPERATOR_TEMPLATES.product.previewLatex).toContain("\\prod_{i=1}^{n}");
    expect(CALCULUS_OPERATOR_TEMPLATES.limit.previewLatex).toContain("\\lim_{x\\to 0}");
  });
});

describe("formula menu customization", () => {
  it("keeps the existing menu structure while rebinding all matrix delimiters", () => {
    interface TestMenuItem {
      id?: string;
      label: string;
      visible?: () => boolean;
      submenuClass?: string;
      columnCount?: number;
      submenu?: TestMenuItem[];
      onMenuSelect?: () => void;
    }
    const visible = () => true;
    const unchanged: TestMenuItem = { id: "copy", label: "复制", onMenuSelect: () => undefined };
    const source: TestMenuItem[] = [
      { id: "color", label: "颜色" },
      {
        label: "矩阵分隔符",
        visible,
        submenuClass: "border-submenu",
        columnCount: 5,
        submenu: [
          { id: "environment-no-border", label: "无括号" },
          { id: "environment-parentheses", label: "圆括号" },
          { id: "environment-brackets", label: "方括号" },
          { id: "environment-bar", label: "竖线" },
          { id: "environment-braces", label: "花括号" },
          { id: "background-color", label: "背景" },
        ],
      },
      unchanged,
    ];
    const selected: MatrixDelimiterEnvironment[] = [];

    const customized = customizeFormulaMenuItems(source, (environment) => selected.push(environment));
    const matrixMenu = customized[0];

    expect(customized).toHaveLength(2);
    expect(customized[1]).toBe(unchanged);
    expect(matrixMenu.visible).toBe(visible);
    expect(matrixMenu.submenuClass).toBe("border-submenu");
    expect(matrixMenu.columnCount).toBe(5);
    expect(matrixMenu.submenu).toHaveLength(5);

    for (const item of matrixMenu.submenu || []) item.onMenuSelect?.();
    expect(selected).toEqual(["matrix", "pmatrix", "bmatrix", "vmatrix", "Bmatrix"]);
  });

  it("restores the matrix selection before changing delimiters and preserves its cells", () => {
    const environments: MatrixDelimiterEnvironment[] = ["matrix", "pmatrix", "bmatrix", "vmatrix", "Bmatrix"];
    type TestSelection = { ranges: [number, number][]; direction: "forward" | "none" };
    const savedSelection: TestSelection = { ranges: [[4, 4]], direction: "forward" };

    for (const environment of environments) {
      const events: string[] = [];
      let selection: TestSelection = { ranges: [[0, 0]], direction: "none" };
      let latex = "\\begin{bmatrix}1 & 2 \\\\ 3 & 4\\end{bmatrix}";
      const field = {
        focus: () => events.push("focus"),
        get selection() { return selection; },
        set selection(value: TestSelection) {
          events.push("selection");
          selection = value;
        },
        executeCommand: ([command, nextEnvironment]: ["setEnvironment", MatrixDelimiterEnvironment]) => {
          events.push(`command:${command}:${nextEnvironment}`);
          expect(selection).toBe(savedSelection);
          latex = latex
            .replace(/\\begin\{[^}]+\}/, `\\begin{${nextEnvironment}}`)
            .replace(/\\end\{[^}]+\}/, `\\end{${nextEnvironment}}`);
          return true;
        },
        getValue: (format: "latex") => {
          expect(format).toBe("latex");
          return latex;
        },
      };

      const result = applyMatrixDelimiter(field, savedSelection, environment);

      expect(result.changed).toBe(true);
      expect(result.latex).toBe(`\\begin{${environment}}1 & 2 \\\\ 3 & 4\\end{${environment}}`);
      expect(events).toEqual(["focus", "selection", `command:setEnvironment:${environment}`]);
    }
  });
});

describe("matrix template generation", () => {
  it("generates placeholder cells for every 1–6 dimension and bracket type", () => {
    for (const environment of ["bmatrix", "pmatrix", "vmatrix"] as const) {
      for (let rows = 1; rows <= 6; rows += 1) {
        for (let columns = 1; columns <= 6; columns += 1) {
          const matrix = matrixLatex(rows, columns, environment);
          expect(matrix.match(/\\placeholder\{\}/g)).toHaveLength(rows * columns);
          expect(matrix).toContain(`\\begin{${environment}}`);
          expect(matrix).toContain(`\\end{${environment}}`);
        }
      }
    }

    expect(matrixLatex(1, 1, "bmatrix")).toBe("\\begin{bmatrix}\n\\placeholder{}\n\\end{bmatrix}");
  });

  it("rejects dimensions outside the teaching-safe 1–6 range", () => {
    expect(() => matrixLatex(0, 2)).toThrow();
    expect(() => matrixLatex(7, 2)).toThrow();
    expect(() => matrixLatex(2, 7)).toThrow();
  });
});
