import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { computePointerTooltipPosition, type FloatingPosition } from "./floating-window";
import type { FormulaMatch } from "./formula-input";
import { MathText, type LatexMacros } from "./math";

interface FormulaHoverPreviewProps {
  anchorElement: HTMLElement | null;
  formula: FormulaMatch;
  clientX: number;
  clientY: number;
  macros?: LatexMacros;
}

export function FormulaHoverPreview({ anchorElement, formula, clientX, clientY, macros }: FormulaHoverPreviewProps) {
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<FloatingPosition>({ left: clientX + 14, top: clientY + 14 });
  const [ready, setReady] = useState(false);

  const updatePosition = useCallback(() => {
    const tooltip = tooltipRef.current;
    if (!tooltip) return;
    setPosition(computePointerTooltipPosition(
      { x: clientX, y: clientY },
      { width: tooltip.offsetWidth, height: tooltip.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
    ));
    setReady(true);
  }, [clientX, clientY]);

  useLayoutEffect(updatePosition, [updatePosition, formula.inner]);

  useEffect(() => {
    window.addEventListener("resize", updatePosition);
    return () => window.removeEventListener("resize", updatePosition);
  }, [updatePosition]);

  if (!formula.inner.trim() || typeof document === "undefined") return null;
  const portalRoot = (anchorElement?.closest(".gs-root") as HTMLElement | null) || document.body;
  const text = formula.presentation === "block" ? `\\[${formula.inner}\\]` : `\\(${formula.inner}\\)`;

  return createPortal(
    <div
      ref={tooltipRef}
      className="proof-ws-formula-tooltip"
      role="tooltip"
      style={{ left: position.left, top: position.top, visibility: ready ? "visible" : "hidden" }}
    >
      <MathText text={text} macros={macros} />
    </div>,
    portalRoot,
  );
}
