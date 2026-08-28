import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MathText, SmartTitle, parseMathSegments, plainTextFromMathText } from "./math";

const matrix = String.raw`矩阵乘法：$AB=\begin{pmatrix}5&-3\\2&4\end{pmatrix}$`;

describe("mixed TeX rendering", () => {
  it("keeps matrix row separators and treats a bare environment as one formula", () => {
    const bare = String.raw`矩阵 \begin{pmatrix}1&2\\3&4\end{pmatrix}`;
    const bareMath = parseMathSegments(bare).find(segment => segment.type === "math");
    expect(bareMath?.src).toContain(String.raw`\\3`);

    const html = renderToStaticMarkup(<MathText text={matrix} />);
    expect(html).toContain("mtable");
    expect(html).not.toContain("katex-error");
  });

  it("resolves text-mode references and TeX spaces once", () => {
    expect(plainTextFromMathText(String.raw`定义~\ref{def:inverse}；推论~\ref{cor:invertible-det}`))
      .toBe("定义 inverse；推论 invertible-det");
  });

  it("uses the same parser for titles and returns a readable strict-error fallback", () => {
    const title = renderToStaticMarkup(<SmartTitle text={String.raw`A=\begin{pmatrix}1\\2\end{pmatrix}`} />);
    expect(title).toContain("mtable");

    const customMacro = renderToStaticMarkup(<MathText text={String.raw`$\customR$`} macros={{ "\\customR": "\\mathbb{R}" }} />);
    expect(customMacro).toContain("mathbb");

    const invalid = renderToStaticMarkup(<MathText text={String.raw`$\notARealCommand{1}$`} />);
    expect(invalid).toContain("notARealCommand");
    expect(invalid).toContain("monospace");
  });

  it("renders an aligned Gaussian-elimination chain with nested arrays", () => {
    const gaussian = String.raw`\[
\begin{aligned}
 &\begin{array}{ccc|c}1&1&1&6\\2&-1&1&3\\1&2&-1&3\end{array}
 \rightarrow
 \begin{array}{ccc|c}1&1&1&6\\0&-3&-1&-9\\1&2&-1&3\end{array}
 \\[1em]
 &{}\xrightarrow{R_2\leftrightarrow R_3}
 \begin{array}{ccc|c}1&1&1&6\\0&1&-2&-3\\0&-3&-1&-9\end{array}.
\end{aligned}
\]`;

    const html = renderToStaticMarkup(<MathText text={gaussian} />);
    expect(html).toContain("mtable");
    expect(html).not.toContain(String.raw`\begin{aligned}`);
    expect(html).not.toContain("monospace");
  });

  it("drops TeX layout commands before implicit math parsing", () => {
    const html = renderToStaticMarkup(
      <MathText text={String.raw`\par\noindent\textbf{定义.} $A=\begin{pmatrix}1&2\\3&4\end{pmatrix}$`} />,
    );
    expect(html).toContain("mtable");
    expect(html).not.toContain("noindent");
    expect(html).not.toContain("monospace");
  });
});
