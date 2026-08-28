import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type React from "react";
import { createPortal } from "react-dom";
import { GripHorizontal, X } from "lucide-react";
import {
  clampFloatingPosition,
  computeVisibleGraphBounds,
  computeWorkspaceFloatingPosition,
  workspaceFloatingWindowWidth,
  workspaceSplitWindowHeight,
  type FloatingPosition,
  type WorkspaceFloatingPlacement,
} from "./floating-window";

interface FloatingWorkspaceWindowProps {
  anchorElement: HTMLElement | null;
  title: string;
  subtitle: string;
  ariaLabel: string;
  className?: string;
  preferredWidth: number;
  preferredHeight?: number;
  maxHeight?: number;
  minimumWidth?: number;
  splitGraphHeight?: boolean;
  placement: WorkspaceFloatingPlacement;
  active: boolean;
  onActivate: () => void;
  onClose: () => void;
  children: React.ReactNode;
}

export function FloatingWorkspaceWindow({
  anchorElement,
  title,
  subtitle,
  ariaLabel,
  className = "",
  preferredWidth,
  preferredHeight,
  maxHeight = 600,
  minimumWidth = 320,
  splitGraphHeight = false,
  placement,
  active,
  onActivate,
  onClose,
  children,
}: FloatingWorkspaceWindowProps) {
  const windowRef = useRef<HTMLElement | null>(null);
  const positionRef = useRef<FloatingPosition>({ left: 12, top: 12 });
  const dragCleanupRef = useRef<((commit?: boolean) => void) | null>(null);
  const manuallyPositionedRef = useRef(false);
  const [position, setPosition] = useState<FloatingPosition>(positionRef.current);
  const [windowWidth, setWindowWidth] = useState(preferredWidth);
  const [windowHeight, setWindowHeight] = useState<number | null>(preferredHeight ?? null);
  const [positionReady, setPositionReady] = useState(false);
  const [dragging, setDragging] = useState(false);
  const titleId = useId();

  const applyPosition = useCallback((next: FloatingPosition) => {
    if (positionRef.current.left === next.left && positionRef.current.top === next.top) return;
    positionRef.current = next;
    setPosition(next);
  }, []);

  const reposition = useCallback(() => {
    const floating = windowRef.current;
    if (!floating) return;
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    if (manuallyPositionedRef.current) {
      applyPosition(clampFloatingPosition(
        positionRef.current,
        { width: floating.offsetWidth, height: floating.offsetHeight },
        viewport,
      ));
      setPositionReady(true);
      return;
    }

    const graphRoot = anchorElement?.closest(".gs-root");
    const canvas = graphRoot?.querySelector(".gs-canvas-wrap") as HTMLElement | null;
    const canvasRect = canvas?.getBoundingClientRect() || {
      left: 0,
      right: viewport.width,
      top: 0,
      bottom: viewport.height,
    };
    const occluders = graphRoot
      ? Array.from(graphRoot.querySelectorAll<HTMLElement>(".gs-rail:not(.collapsed), .gs-panel"))
        .filter((element) => element.offsetWidth > 0 && element.offsetHeight > 0)
        .map((element) => element.getBoundingClientRect())
      : [];
    const bounds = computeVisibleGraphBounds(canvasRect, occluders, viewport);
    const width = workspaceFloatingWindowWidth(
      preferredWidth,
      bounds.width,
      viewport.width,
      minimumWidth,
    );
    floating.style.width = `${width}px`;
    setWindowWidth(width);
    if (splitGraphHeight) {
      const height = workspaceSplitWindowHeight(bounds.height, viewport.height, maxHeight);
      floating.style.height = `${height}px`;
      setWindowHeight(height);
    }
    applyPosition(computeWorkspaceFloatingPosition(
      bounds,
      { width: floating.offsetWidth, height: floating.offsetHeight },
      viewport,
      placement,
    ));
    setPositionReady(true);
  }, [anchorElement, applyPosition, maxHeight, minimumWidth, placement, preferredWidth, splitGraphHeight]);

  useLayoutEffect(() => {
    reposition();
  }, [reposition]);

  useEffect(() => {
    let animationFrame = 0;
    const scheduleReposition = () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = 0;
        reposition();
      });
    };
    const graphRoot = anchorElement?.closest(".gs-root");
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scheduleReposition);
    const observeLayoutElements = () => {
      if (!resizeObserver) return;
      if (windowRef.current) resizeObserver.observe(windowRef.current);
      const canvas = graphRoot?.querySelector(".gs-canvas-wrap");
      if (canvas) resizeObserver.observe(canvas);
      graphRoot?.querySelectorAll(".gs-rail, .gs-panel").forEach((element) => resizeObserver.observe(element));
    };
    observeLayoutElements();
    const graphBody = graphRoot?.querySelector(".gs-body");
    const mutationObserver = typeof MutationObserver === "undefined" || !graphBody
      ? null
      : new MutationObserver(() => {
        observeLayoutElements();
        scheduleReposition();
      });
    if (mutationObserver && graphBody) mutationObserver.observe(graphBody, { childList: true });
    window.addEventListener("resize", scheduleReposition);
    return () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      window.removeEventListener("resize", scheduleReposition);
    };
  }, [anchorElement, reposition]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!active || event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [active, onClose]);

  useEffect(() => () => dragCleanupRef.current?.(false), []);

  const handleDragStart = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest("button")) return;
    const floating = windowRef.current;
    if (!floating) return;
    event.preventDefault();
    dragCleanupRef.current?.(false);
    const rect = floating.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = rect.left;
    const startTop = rect.top;
    let next = { left: startLeft, top: startTop };
    let animationFrame = 0;
    let active = true;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "grabbing";
    document.body.style.userSelect = "none";
    setDragging(true);

    const paint = () => {
      floating.style.left = `${next.left}px`;
      floating.style.top = `${next.top}px`;
      animationFrame = 0;
    };
    const onMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const deltaY = moveEvent.clientY - startY;
      if (Math.abs(deltaX) + Math.abs(deltaY) > 2) manuallyPositionedRef.current = true;
      next = clampFloatingPosition(
        { left: startLeft + deltaX, top: startTop + deltaY },
        { width: rect.width, height: rect.height },
        { width: window.innerWidth, height: window.innerHeight },
      );
      if (!animationFrame) animationFrame = window.requestAnimationFrame(paint);
    };
    const cleanup = (commit = true) => {
      if (!active) return;
      active = false;
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      if (commit) {
        paint();
        applyPosition(next);
        setDragging(false);
      }
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      dragCleanupRef.current = null;
    };
    const onUp = () => cleanup(true);
    dragCleanupRef.current = cleanup;
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const portalRoot = typeof document === "undefined"
    ? null
    : (anchorElement?.closest(".gs-root") as HTMLElement | null) || document.body;
  if (!portalRoot) return null;

  const floatingStyle = {
    left: position.left,
    top: position.top,
    visibility: positionReady ? "visible" : "hidden",
    width: `min(${windowWidth}px, calc(100vw - 24px))`,
    height: windowHeight !== null ? `min(${windowHeight}px, calc(100vh - 24px))` : undefined,
    maxHeight: `min(${maxHeight}px, calc(100vh - 24px))`,
    zIndex: active ? 71 : 70,
  } as React.CSSProperties;

  return createPortal(
    <section
      ref={windowRef}
      className={`proof-ws-floating-window ${className}${dragging ? " dragging" : ""}`}
      role="dialog"
      aria-modal="false"
      aria-label={ariaLabel}
      aria-labelledby={titleId}
      style={floatingStyle}
      onPointerDownCapture={onActivate}
      onFocusCapture={onActivate}
    >
      <div className="proof-ws-floating-head" onMouseDown={handleDragStart}>
        <div className="proof-ws-floating-copy">
          <GripHorizontal size={16} aria-hidden="true" />
          <div>
            <strong id={titleId}>{title}</strong>
            <span>{subtitle}</span>
          </div>
        </div>
        <button type="button" className="proof-ws-floating-close" onClick={onClose} aria-label={`关闭${title}`} title="关闭">
          <X size={15} />
        </button>
      </div>
      {children}
    </section>,
    portalRoot,
  );
}
