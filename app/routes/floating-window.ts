export interface FloatingPosition {
  left: number;
  top: number;
}

export interface FloatingTopRightPosition {
  top: number;
  right: number;
}

export interface FloatingSize {
  width: number;
  height: number;
}

export interface FloatingViewport {
  width: number;
  height: number;
}

export interface FloatingAnchorRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface FloatingBounds extends FloatingAnchorRect {
  width: number;
  height: number;
}

export type WorkspaceFloatingPlacement = "top-right" | "bottom-right";

export interface ClientRectLike {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export const FLOATING_WINDOW_MARGIN = 12;
export const FLOATING_WINDOW_GAP = 8;

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function floatingWindowWidth(preferredWidth: number, viewportWidth: number, margin = FLOATING_WINDOW_MARGIN): number {
  return Math.max(0, Math.min(preferredWidth, viewportWidth - margin * 2));
}

export function workspaceFloatingWindowWidth(
  preferredWidth: number,
  boundsWidth: number,
  viewportWidth: number,
  minimumWidth = 320,
  margin = FLOATING_WINDOW_MARGIN,
): number {
  const viewportLimit = Math.max(0, viewportWidth - margin * 2);
  const graphWidth = Math.max(0, boundsWidth - margin * 2);
  return Math.min(preferredWidth, viewportLimit, Math.max(minimumWidth, graphWidth));
}

export function workspaceSplitWindowHeight(
  boundsHeight: number,
  viewportHeight: number,
  maximumHeight = 600,
  margin = FLOATING_WINDOW_MARGIN,
  gap = FLOATING_WINDOW_MARGIN,
): number {
  const viewportLimit = Math.max(0, viewportHeight - margin * 2);
  const splitHeight = Math.max(0, (boundsHeight - margin * 2 - gap) / 2);
  return Math.floor(Math.min(maximumHeight, viewportLimit, splitHeight));
}

export function computeVisibleGraphBounds(
  canvas: FloatingAnchorRect,
  occluders: readonly FloatingAnchorRect[],
  viewport: FloatingViewport,
): FloatingBounds {
  let left = clamp(canvas.left, 0, viewport.width);
  let right = clamp(canvas.right, left, viewport.width);
  const top = clamp(canvas.top, 0, viewport.height);
  const bottom = clamp(canvas.bottom, top, viewport.height);

  for (const rect of occluders) {
    const overlapsVertically = rect.bottom > top && rect.top < bottom;
    if (!overlapsVertically || rect.right <= left || rect.left >= right) continue;
    if (rect.left <= left) left = clamp(rect.right, left, right);
    if (rect.right >= right) right = clamp(rect.left, left, right);
  }

  return {
    left,
    right,
    top,
    bottom,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  };
}

export function computeWorkspaceFloatingPosition(
  bounds: FloatingBounds,
  size: FloatingSize,
  viewport: FloatingViewport,
  placement: WorkspaceFloatingPlacement,
  margin = FLOATING_WINDOW_MARGIN,
): FloatingPosition {
  return clampFloatingPosition({
    left: bounds.right - margin - size.width,
    top: placement === "top-right"
      ? bounds.top + margin
      : bounds.bottom - margin - size.height,
  }, size, viewport, margin);
}

export function clampFloatingPosition(
  position: FloatingPosition,
  size: FloatingSize,
  viewport: FloatingViewport,
  margin = FLOATING_WINDOW_MARGIN,
): FloatingPosition {
  const maxLeft = Math.max(margin, viewport.width - size.width - margin);
  const maxTop = Math.max(margin, viewport.height - size.height - margin);
  return {
    left: clamp(position.left, margin, maxLeft),
    top: clamp(position.top, margin, maxTop),
  };
}

export function computeFloatingPosition(
  anchor: FloatingAnchorRect,
  size: FloatingSize,
  viewport: FloatingViewport,
  margin = FLOATING_WINDOW_MARGIN,
  gap = FLOATING_WINDOW_GAP,
): FloatingPosition {
  const below = anchor.bottom + gap;
  const above = anchor.top - gap - size.height;
  const top = below + size.height <= viewport.height - margin ? below : above;
  return clampFloatingPosition(
    { left: anchor.right - size.width, top },
    size,
    viewport,
    margin,
  );
}

export function computePointerTooltipPosition(
  pointer: { x: number; y: number },
  size: FloatingSize,
  viewport: FloatingViewport,
  offset = 14,
  margin = FLOATING_WINDOW_MARGIN,
): FloatingPosition {
  const fitsRight = pointer.x + offset + size.width <= viewport.width - margin;
  const fitsBelow = pointer.y + offset + size.height <= viewport.height - margin;
  const left = fitsRight ? pointer.x + offset : pointer.x - offset - size.width;
  const top = fitsBelow ? pointer.y + offset : pointer.y - offset - size.height;
  return clampFloatingPosition({ left, top }, size, viewport, margin);
}

export function computeKeyboardDismissPosition(
  keyboard: FloatingAnchorRect,
  viewport: FloatingViewport,
  buttonSize = 40,
  inset = 10,
  margin = FLOATING_WINDOW_MARGIN,
): FloatingTopRightPosition {
  const maxTop = Math.max(margin, viewport.height - buttonSize - margin);
  const maxRight = Math.max(margin, viewport.width - buttonSize - margin);
  return {
    top: clamp(keyboard.top + inset, margin, maxTop),
    right: clamp(viewport.width - keyboard.right + inset, margin, maxRight),
  };
}

export function pointInClientRects(rects: readonly ClientRectLike[], x: number, y: number): boolean {
  return rects.some((rect) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom);
}
