import { forwardRef, useEffect, useImperativeHandle, useRef, type ClipboardEvent, type KeyboardEvent, type MouseEvent as ReactMouseEvent } from "react";
import { MathText } from "./math";

export type DirectQuestionContentSegment =
  | { type: "text"; value: string }
  | { type: "image"; alt: string; src: string };

export type DirectQuestionEditorSelection = { start: number; end: number };

export interface DirectQuestionEditorHandle {
  focus: () => void;
  getSelectionRange: () => DirectQuestionEditorSelection;
  setSelectionRange: (start: number, end: number) => void;
}

const DIRECT_INLINE_IMAGE_PATTERN = /!\[([^\]]*)\]\((data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+=*)\)/gi;
const DIRECT_INLINE_IMAGE_MAX_BYTES = 20 * 1024 * 1024;
const DIRECT_INLINE_IMAGE_MAX_BASE64_LENGTH = Math.ceil(DIRECT_INLINE_IMAGE_MAX_BYTES / 3) * 4;

export function isDirectInlineImageDataUrl(value: string): boolean {
  const match = value.match(/^data:image\/(?:png|jpeg|webp);base64,([A-Za-z0-9+/]+=*)$/i);
  return Boolean(match && match[1].length <= DIRECT_INLINE_IMAGE_MAX_BASE64_LENGTH);
}

export function buildDirectImageMarkdown(filename: string, dataUrl: string): string {
  if (!isDirectInlineImageDataUrl(dataUrl)) throw new Error("图片数据格式无效。");
  const alt = filename.replace(/[\[\]\r\n]/g, " ").trim() || "题目图片";
  return `![${alt}](${dataUrl})`;
}

export function splitDirectQuestionContent(text: string): DirectQuestionContentSegment[] {
  const segments: DirectQuestionContentSegment[] = [];
  let cursor = 0;
  for (const match of text.matchAll(DIRECT_INLINE_IMAGE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) segments.push({ type: "text", value: text.slice(cursor, index) });
    const alt = match[1] || "题目图片";
    const src = match[2];
    if (isDirectInlineImageDataUrl(src)) segments.push({ type: "image", alt, src });
    else segments.push({ type: "text", value: match[0] });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) segments.push({ type: "text", value: text.slice(cursor) });
  return segments.length > 0 ? segments : [{ type: "text", value: text }];
}

export function serializeDirectQuestionContentSegments(segments: DirectQuestionContentSegment[]): string {
  return segments.map(segment => segment.type === "image"
    ? `![${segment.alt}](${segment.src})`
    : segment.value).join("");
}

export function findDirectQuestionImageDeletionRange(
  value: string,
  selection: DirectQuestionEditorSelection,
  key: "Backspace" | "Delete",
): DirectQuestionEditorSelection | null {
  const imageRanges: DirectQuestionEditorSelection[] = [];
  let cursor = 0;
  for (const segment of splitDirectQuestionContent(value)) {
    const serialized = segment.type === "image" ? `![${segment.alt}](${segment.src})` : segment.value;
    if (segment.type === "image") imageRanges.push({ start: cursor, end: cursor + serialized.length });
    cursor += serialized.length;
  }
  if (selection.start !== selection.end) {
    return imageRanges.some(range => range.start < selection.end && range.end > selection.start)
      ? { start: selection.start, end: selection.end }
      : null;
  }
  if (key === "Backspace") return imageRanges.find(range => range.end === selection.start) || null;
  return imageRanges.find(range => range.start === selection.end) || null;
}

export function insertDirectTextAtSelection(text: string, start: number, end: number, insertion: string) {
  const safeStart = Math.max(0, Math.min(start, text.length));
  const safeEnd = Math.max(safeStart, Math.min(end, text.length));
  return {
    text: `${text.slice(0, safeStart)}${insertion}${text.slice(safeEnd)}`,
    selectionStart: safeStart + insertion.length,
    selectionEnd: safeStart + insertion.length,
  };
}

const DIRECT_EDITOR_BLOCK_TAGS = new Set(["ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DIV", "DL", "DT", "DD", "FIELDSET", "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER", "HR", "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT", "TH", "THEAD", "TR", "UL"]);

function isDirectEditorBlock(element: Element) {
  return DIRECT_EDITOR_BLOCK_TAGS.has(element.tagName);
}

function directEditorImageMarker(element: Element): string | null {
  if (element.tagName !== "IMG") return null;
  const src = element.getAttribute("src") || "";
  if (!isDirectInlineImageDataUrl(src)) return null;
  const alt = element.getAttribute("data-direct-image-alt") ?? element.getAttribute("alt") ?? "题目图片";
  return `![${alt}](${src})`;
}

function serializeDirectEditorNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const element = node as Element;
  const image = directEditorImageMarker(element);
  if (image) return image;
  if (element.tagName === "BR") return "\n";
  const children = Array.from(element.childNodes).map(serializeDirectEditorNode).join("");
  if (!isDirectEditorBlock(element)) return children;
  // Browsers represent an empty Enter-created line as <div><br /></div>.
  // The <br> already represents that line, so do not count it twice.
  return `${children === "\n" ? "" : children}\n`;
}

export function serializeDirectQuestionEditor(root: HTMLElement): string {
  const serialized = Array.from(root.childNodes).map(serializeDirectEditorNode).join("");
  return serialized.endsWith("\n") ? serialized.slice(0, -1) : serialized;
}

function directEditorNodeLength(node: Node) {
  return serializeDirectEditorNode(node).length;
}

function directEditorOffsetAtBoundary(root: HTMLElement, container: Node | null, offset: number): number | null {
  if (!container || (container !== root && !root.contains(container))) return null;
  let total = container.nodeType === Node.TEXT_NODE
    ? Math.max(0, Math.min(offset, container.nodeValue?.length || 0))
    : Array.from(container.childNodes).slice(0, Math.max(0, Math.min(offset, container.childNodes.length))).reduce((sum, node) => sum + directEditorNodeLength(node), 0);
  let current: Node | null = container;
  while (current && current !== root) {
    const parent: Node | null = current.parentNode;
    if (!parent) return null;
    const index = Array.prototype.indexOf.call(parent.childNodes, current) as number;
    total += Array.from(parent.childNodes).slice(0, index).reduce<number>((sum, node) => sum + directEditorNodeLength(node), 0);
    current = parent;
  }
  return Math.max(0, Math.min(total, serializeDirectQuestionEditor(root).length));
}

function directEditorSelection(root: HTMLElement, fallback: DirectQuestionEditorSelection): DirectQuestionEditorSelection {
  if (typeof window === "undefined") return fallback;
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return fallback;
  const anchor = directEditorOffsetAtBoundary(root, selection.anchorNode, selection.anchorOffset);
  const focus = directEditorOffsetAtBoundary(root, selection.focusNode, selection.focusOffset);
  if (anchor === null || focus === null) return fallback;
  return anchor <= focus ? { start: anchor, end: focus } : { start: focus, end: anchor };
}

type DirectEditorDomBoundary = { container: Node; offset: number };

function directEditorBoundaryWithin(node: Node, offset: number, parent: Node, index: number): DirectEditorDomBoundary {
  if (node.nodeType === Node.TEXT_NODE) {
    return { container: node, offset: Math.max(0, Math.min(offset, node.nodeValue?.length || 0)) };
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return { container: parent, offset: index + (offset > 0 ? 1 : 0) };
  const element = node as Element;
  if (directEditorImageMarker(element) || element.tagName === "BR") {
    return { container: parent, offset: index + (offset > 0 ? 1 : 0) };
  }
  const contentLength = Array.from(element.childNodes).reduce((sum, child) => sum + directEditorNodeLength(child), 0);
  if (isDirectEditorBlock(element) && offset >= contentLength) {
    return { container: parent, offset: index + 1 };
  }
  const children = Array.from(element.childNodes);
  let cursor = 0;
  for (let childIndex = 0; childIndex < children.length; childIndex += 1) {
    const child = children[childIndex];
    const length = directEditorNodeLength(child);
    if (offset <= cursor + length) return directEditorBoundaryWithin(child, offset - cursor, element, childIndex);
    cursor += length;
  }
  return { container: element, offset: children.length };
}

function directEditorBoundaryAt(root: HTMLElement, offset: number): DirectEditorDomBoundary {
  const total = serializeDirectQuestionEditor(root).length;
  const target = Math.max(0, Math.min(offset, total));
  const children = Array.from(root.childNodes);
  let cursor = 0;
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    const length = directEditorNodeLength(child);
    if (target <= cursor + length) return directEditorBoundaryWithin(child, target - cursor, root, index);
    cursor += length;
  }
  return { container: root, offset: children.length };
}

interface DirectQuestionEditorProps {
  value: string;
  onChange: (value: string, selection: DirectQuestionEditorSelection) => void;
  onSelectionChange?: (selection: DirectQuestionEditorSelection) => void;
  className?: string;
  placeholder?: string;
  ariaLabel: string;
}

export const DirectQuestionEditor = forwardRef<DirectQuestionEditorHandle, DirectQuestionEditorProps>(function DirectQuestionEditor({ value, onChange, onSelectionChange, className = "", placeholder, ariaLabel }, ref) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const lastValueRef = useRef(value);
  const selectionRef = useRef<DirectQuestionEditorSelection>({ start: value.length, end: value.length });

  const readSelection = () => {
    const root = rootRef.current;
    if (!root) return selectionRef.current;
    return directEditorSelection(root, selectionRef.current);
  };
  const notifySelection = () => {
    const next = readSelection();
    selectionRef.current = next;
    onSelectionChange?.(next);
    return next;
  };
  const restoreSelection = (start: number, end: number) => {
    const root = rootRef.current;
    if (!root || typeof window === "undefined") return;
    const selection = window.getSelection();
    if (!selection) return;
    try {
      const range = document.createRange();
      const startBoundary = directEditorBoundaryAt(root, start);
      const endBoundary = directEditorBoundaryAt(root, end);
      range.setStart(startBoundary.container, startBoundary.offset);
      range.setEnd(endBoundary.container, endBoundary.offset);
      selection.removeAllRanges();
      selection.addRange(range);
    } catch {
      // DOM selection can briefly point at a node removed by the browser while
      // an image is being deleted. Keep the editor usable instead of bubbling
      // a Range exception into the route error boundary.
      try {
        root.focus();
        const fallback = document.createRange();
        fallback.selectNodeContents(root);
        fallback.collapse(false);
        selection.removeAllRanges();
        selection.addRange(fallback);
      } catch {
        // A detached editor is harmless; its next mount will restore state.
      }
    }
  };
  const syncFromDom = () => {
    const root = rootRef.current;
    if (!root) return;
    const nextSelection = notifySelection();
    const nextValue = serializeDirectQuestionEditor(root);
    selectionRef.current = nextSelection;
    lastValueRef.current = nextValue;
    onChange(nextValue, nextSelection);
  };

  useImperativeHandle(ref, () => ({
    focus: () => rootRef.current?.focus(),
    getSelectionRange: readSelection,
    setSelectionRange: (start, end) => {
      selectionRef.current = { start, end };
      requestAnimationFrame(() => restoreSelection(start, end));
    },
  }), []);

  useEffect(() => {
    if (lastValueRef.current === value) return;
    lastValueRef.current = value;
    requestAnimationFrame(() => restoreSelection(selectionRef.current.start, selectionRef.current.end));
  }, [value]);

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    if (!text || typeof window === "undefined") return;
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || !rootRef.current?.contains(selection.anchorNode)) return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const textNode = document.createTextNode(text);
    range.insertNode(textNode);
    range.setStartAfter(textNode);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    syncFromDom();
  };

  const selectImage = (event: ReactMouseEvent<HTMLImageElement>) => {
    const root = rootRef.current;
    const marker = directEditorImageMarker(event.currentTarget);
    const start = root ? directEditorOffsetAtBoundary(root, event.currentTarget, 0) : null;
    if (!root || !marker || start === null) return;
    event.preventDefault();
    event.stopPropagation();
    selectionRef.current = { start, end: start + marker.length };
    restoreSelection(start, start + marker.length);
    onSelectionChange?.(selectionRef.current);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.ctrlKey || event.metaKey) && ["b", "i", "u"].includes(event.key.toLowerCase())) event.preventDefault();
    if (event.key !== "Backspace" && event.key !== "Delete") return;
    const deletion = findDirectQuestionImageDeletionRange(value, readSelection(), event.key);
    if (!deletion) return;
    event.preventDefault();
    const result = insertDirectTextAtSelection(value, deletion.start, deletion.end, "");
    selectionRef.current = { start: result.selectionStart, end: result.selectionEnd };
    lastValueRef.current = result.text;
    onSelectionChange?.(selectionRef.current);
    onChange(result.text, selectionRef.current);
    requestAnimationFrame(() => restoreSelection(result.selectionStart, result.selectionEnd));
  };

  return (
    <div
      ref={rootRef}
      className={`direct-content-editor ${className}`.trim()}
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-label={ariaLabel}
      aria-multiline="true"
      aria-placeholder={placeholder}
      data-empty={value.length === 0 ? "true" : "false"}
      onInput={syncFromDom}
      onFocus={notifySelection}
      onClick={notifySelection}
      onKeyUp={notifySelection}
      onMouseUp={notifySelection}
      onBlur={notifySelection}
      onPaste={handlePaste}
      onKeyDown={handleKeyDown}
    >
      {splitDirectQuestionContent(value).map((segment, index) => segment.type === "image"
        ? <img key={`${index}-${segment.src.slice(-12)}`} className="direct-editor-image" src={segment.src} alt={segment.alt} data-direct-image-alt={segment.alt} contentEditable={false} draggable={false} onClick={selectImage} />
        : <span key={index}>{segment.value}</span>)}
    </div>
  );
});

DirectQuestionEditor.displayName = "DirectQuestionEditor";

export function DirectQuestionContent({ text, className }: { text: string; className?: string }) {
  return (
    <span className={className}>
      {splitDirectQuestionContent(text).map((segment, index) => segment.type === "image"
        ? <img key={index} className="direct-question-image" src={segment.src} alt={segment.alt} loading="lazy" />
        : <MathText key={index} text={segment.value} />)}
    </span>
  );
}
