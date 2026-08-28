import { describe, expect, it } from "vitest";
import { nodeStatementText, repairLegacySourceStatement } from "./source-matching";

const canonical = String.raw`设 $m,n$ 为正整数。\[\begin{pmatrix}
a_{11}&a_{12}&\cdots&a_{1n}\\
a_{21}&a_{22}&\cdots&a_{2n}\\
\vdots&\vdots&\ddots&\vdots\\
a_{m1}&a_{m2}&\cdots&a_{mn}
\end{pmatrix}\]`;

const damaged = String.raw`设 $m,n$ 为正整数。\[\begin{pmatrix}
a_{11}&a_{12}&\cdots&a_{1n}\
a_{21}&a_{22}&\cdots&a_{2n}\
\vdots&\vdots&\ddots&\vdots\
a_{m1}&a_{m2}&\cdots&a_{mn}
\end{pmatrix}\]`;

describe("legacy source statement matrix compatibility", () => {
  it("restores rows before letters and TeX commands", () => {
    expect(repairLegacySourceStatement(damaged, canonical)).toBe(canonical);
    expect(nodeStatementText({ content: canonical, source_statement: damaged })).toBe(canonical);
  });

  it("does not guess when the content source is ambiguous or unrelated", () => {
    const ambiguous = String.raw`\begin{pmatrix}a&b\\c&d\end{pmatrix}
\begin{pmatrix}a & b\\c & d\end{pmatrix}`;
    const damagedSingle = String.raw`\begin{pmatrix}a&b\c&d\end{pmatrix}`;
    expect(repairLegacySourceStatement(damagedSingle, ambiguous)).toBe(damagedSingle);
    expect(repairLegacySourceStatement(String.raw`x\ y`, canonical)).toBe(String.raw`x\ y`);
  });
});
