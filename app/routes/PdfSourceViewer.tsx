import { useCallback, useEffect, useRef, useState } from "react";
import "./pdfjsCompat";
import pdfWorkerUrl from "./pdf.worker.compat.ts?worker&url";
import { getDocument, GlobalWorkerOptions, Util } from "pdfjs-dist/build/pdf.mjs";
import "./pdfsourceviewer.css";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type HighlightBox = { left: number; top: number; width: number; height: number };

type PdfSourceViewerProps = {
  url: string;
  token?: string;
  page: number;
  sourceStatement: string;
  searchTerms: string[];
  statementTerms: string[];
  onPageSize?: (size: { width: number; height: number }) => void;
  onLoadError?: () => void;
};

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in", "is", "of", "on", "or", "the", "then", "to", "with",
]);

function normalizedWords(value: string): string[] {
  return value
    .toLocaleLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, " ")
    .split(/\s+/)
    .filter(word => word.length >= 2 && !STOP_WORDS.has(word));
}

function sourceStatementFragments(value: string): string[] {
  return value
    .split(/\$\$[\s\S]*?\$\$|\$[^$]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\\(?:ref|eqref|cite)\s*\{[^{}]*\}/g)
    .map(fragment => fragment.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
}

function itemBox(item: any, viewport: any): HighlightBox {
  const transform = Util.transform(viewport.transform, item.transform);
  const height = Math.max(10, Math.hypot(transform[2], transform[3]));
  return {
    left: transform[4],
    top: transform[5] - height,
    width: Math.max(4, item.width * viewport.scale),
    height,
  };
}

function isStatementBoundary(item: any): boolean {
  if (!item || typeof item.str !== "string") return false;
  const text = item.str.trim();
  return /^(definition|lemma|theorem|corollary|proposition|example|exercise|remark)\b/i.test(text)
    || /^proof\.?$/i.test(text)
    || /^(?:定义|定理|引理|命题|推论|公理|例|习题|注|证明)(?:\s|[.:：。]|\d|$)/.test(text);
}

function isSectionBoundary(items: any[], index: number): boolean {
  const item = items[index];
  const text = String(item?.str ?? "").trim();
  const size = Math.hypot(item?.transform?.[2] ?? 0, item?.transform?.[3] ?? 0);
  if (!/^\d+(?:\.\d+)*$/.test(text) || size < 13) return false;
  for (let next = index + 1; next < Math.min(items.length, index + 5); next++) {
    const nextText = String(items[next]?.str ?? "").trim();
    if (!nextText) continue;
    const nextSize = Math.hypot(items[next]?.transform?.[2] ?? 0, items[next]?.transform?.[3] ?? 0);
    return nextSize >= 13 && /^[A-Z\u4e00-\u9fff]/.test(nextText);
  }
  return false;
}

function statementBox(items: any[], viewport: any, sourceStatement: string, terms: string[]): HighlightBox | null {
  const phraseCandidates = [sourceStatement, ...sourceStatementFragments(sourceStatement), ...terms]
    .map(normalizedWords)
    .filter((words, index, all) => words.length >= 2
      && words.length <= 40
      && all.findIndex(candidate => candidate.join("\u0000") === words.join("\u0000")) === index);
  let phraseRange: [number, number] | null = null;
  for (const words of phraseCandidates) {
    for (let start = 0; start < items.length && !phraseRange; start++) {
      const phraseWords: { value: string; itemIndex: number }[] = [];
      let nonEmptyItems = 0;
      for (let end = start; end < items.length && nonEmptyItems < 24; end++) {
        const currentWords = normalizedWords(String(items[end]?.str ?? ""));
        if (currentWords.length) nonEmptyItems += 1;
        currentWords.forEach(value => phraseWords.push({ value, itemIndex: end }));
        for (let offset = 0; offset + words.length <= phraseWords.length; offset++) {
          if (words.every((word, index) => phraseWords[offset + index].value === word)) {
            phraseRange = [
              phraseWords[offset].itemIndex,
              phraseWords[offset + words.length - 1].itemIndex,
            ];
            break;
          }
        }
        if (phraseRange) break;
      }
    }
    if (phraseRange) break;
  }
  if (!phraseRange) return null;

  let start = phraseRange[0];
  while (start > 0 && !isStatementBoundary(items[start])) start -= 1;
  const hasBoundary = isStatementBoundary(items[start]);
  if (!hasBoundary) start = phraseRange[0];

  let end = phraseRange[1];
  if (hasBoundary) {
    end = start + 1;
    while (end < items.length && !isStatementBoundary(items[end]) && !isSectionBoundary(items, end)) end += 1;
    end -= 1;
    while (end > start && !String(items[end]?.str ?? "").trim()) end -= 1;
    if (/^\d+$/.test(String(items[end]?.str ?? "").trim())) {
      end -= 1;
      while (end > start && !String(items[end]?.str ?? "").trim()) end -= 1;
    }
  }

  const boxes = items
    .slice(start, end + 1)
    .filter(item => item && typeof item.str === "string" && item.str.trim())
    .map(item => itemBox(item, viewport));
  if (!boxes.length) return null;
  const left = Math.min(...boxes.map(box => box.left));
  const top = Math.min(...boxes.map(box => box.top));
  const right = Math.max(...boxes.map(box => box.left + box.width));
  const bottom = Math.max(...boxes.map(box => box.top + box.height));
  return {
    left: left - 10,
    top: top - 7,
    width: right - left + 20,
    height: bottom - top + 14,
  };
}

function matchingBoxes(items: any[], viewport: any, sourceStatement: string, statementTerms: string[], terms: string[]): HighlightBox[] {
  const wholeStatement = statementBox(items, viewport, sourceStatement, [...statementTerms, ...terms]);
  if (wholeStatement) return [wholeStatement];
  const candidates = terms
    .map(term => ({ term, words: normalizedWords(term) }))
    .filter(candidate => candidate.words.length >= 2)
    .sort((a, b) => b.words.length - a.words.length);
  if (!candidates.length) return [];

  const matches: { box: HighlightBox; score: number }[] = [];
  for (const item of items) {
    if (!item || typeof item.str !== "string" || !item.str.trim()) continue;
    const itemWords = new Set(normalizedWords(item.str));
    let bestScore = 0;
    for (const candidate of candidates) {
      const score = candidate.words.filter(word => itemWords.has(word)).length;
      bestScore = Math.max(bestScore, score);
    }
    if (bestScore < 2) continue;
    const box = itemBox(item, viewport);
    matches.push({
      score: bestScore,
      box: {
        left: box.left - 2,
        top: box.top - 2,
        width: box.width + 4,
        height: box.height + 4,
      },
    });
  }
  const topScore = Math.max(...matches.map(match => match.score), 0);
  const bestBoxes = matches
    .filter(match => match.score === topScore)
    .map(match => match.box)
    .sort((a, b) => a.top - b.top || a.left - b.left);
  const merged: HighlightBox[] = [];
  for (const box of bestBoxes) {
    const previous = merged.at(-1);
    const sameLine = previous && Math.abs(previous.top - box.top) < Math.max(previous.height, box.height) * .55;
    const nearby = previous && box.left - (previous.left + previous.width) < 28;
    if (sameLine && nearby) {
      const right = Math.max(previous.left + previous.width, box.left + box.width);
      previous.left = Math.min(previous.left, box.left);
      previous.top = Math.min(previous.top, box.top);
      previous.width = right - previous.left;
      previous.height = Math.max(previous.height, box.height);
      continue;
    }
    merged.push({ ...box });
  }
  return merged;
}

export function PdfSourceViewer({ url, token, page, sourceStatement, searchTerms, statementTerms, onPageSize, onLoadError }: PdfSourceViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [boxes, setBoxes] = useState<HighlightBox[]>([]);
  const [pdf, setPdf] = useState<any>(null);
  const [pageCount, setPageCount] = useState(0);
  const [targetPage, setTargetPage] = useState(1);
  const [targetRendered, setTargetRendered] = useState(false);
  const [showTargetNote, setShowTargetNote] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: any;
    const load = async () => {
      setState("loading");
      setError("");
      setBoxes([]);
      setPdf(null);
      setPageCount(0);
      setTargetRendered(false);
      try {
        loadingTask = getDocument({
          url,
          ...(token ? { httpHeaders: { Authorization: `Bearer ${token}` } } : {}),
          // Electron's FontFace path drops glyphs from TeX math fonts.
          disableFontFace: true,
          useSystemFonts: false,
        });
        const loadedPdf = await loadingTask.promise;
        if (cancelled) return;
        setPdf(loadedPdf);
        setPageCount(loadedPdf.numPages);
        setTargetPage(Math.max(1, Math.min(page, loadedPdf.numPages)));
        setState("ready");
      } catch (cause) {
        if (!cancelled) {
          setState("error");
          setError(cause instanceof Error ? cause.message : "Unable to render PDF");
          onLoadError?.();
        }
      }
    };
    load();
    return () => {
      cancelled = true;
      loadingTask?.destroy?.();
    };
  }, [onLoadError, page, token, url]);

  const handlePageRendered = useCallback((pageNumber: number, size: { width: number; height: number }, found: HighlightBox[]) => {
    if (pageNumber !== targetPage) return;
    setBoxes(found);
    setTargetRendered(true);
    const scrollbarWidth = scrollRef.current
      ? scrollRef.current.offsetWidth - scrollRef.current.clientWidth
      : 0;
    onPageSize?.({
      width: size.width + scrollbarWidth,
      height: size.height,
    });
    requestAnimationFrame(() => {
      const scroll = scrollRef.current;
      const pageEl = pageRefs.current.get(pageNumber);
      if (!scroll || !pageEl) return;
      const first = found[0];
      scroll.scrollTop = Math.max(0, pageEl.offsetTop + (first?.top ?? 0) - 80);
      scroll.scrollLeft = Math.max(0, (first?.left ?? 0) - 80);
    });
  }, [onPageSize, targetPage]);

  useEffect(() => {
    if (state !== "ready" || !targetRendered || boxes.length > 0) {
      setShowTargetNote(false);
      return;
    }
    setShowTargetNote(true);
    const timer = window.setTimeout(() => setShowTargetNote(false), 3000);
    return () => window.clearTimeout(timer);
  }, [boxes.length, state, targetRendered]);

  const setPageElement = useCallback((pageNumber: number, el: HTMLDivElement | null) => {
    if (el) pageRefs.current.set(pageNumber, el);
    else pageRefs.current.delete(pageNumber);
  }, []);

  return (
    <div ref={scrollRef} className="gs-pdf-viewer">
      {state === "loading" && <div className="gs-pdf-viewer-state">正在渲染 PDF 原文...</div>}
      {state === "error" && <div className="gs-pdf-viewer-state error">PDF 渲染失败：{error}</div>}
      {showTargetNote && (
        <div className="gs-pdf-viewer-note">已定位到目标页，但未在该页中匹配到可高亮的原文片段。</div>
      )}
      <div className="gs-pdf-document">
        {state === "ready" && pdf && Array.from({ length: pageCount }, (_, index) => {
          const pageNumber = index + 1;
          return (
            <PdfPageCanvas
              key={pageNumber}
              pdf={pdf}
              pageNumber={pageNumber}
              isTarget={pageNumber === targetPage}
              sourceStatement={sourceStatement}
              searchTerms={searchTerms}
              statementTerms={statementTerms}
              onRendered={handlePageRendered}
              setPageElement={(el) => setPageElement(pageNumber, el)}
            />
          );
        })}
      </div>
    </div>
  );
}

function PdfPageCanvas({
  pdf,
  pageNumber,
  isTarget,
  sourceStatement,
  searchTerms,
  statementTerms,
  onRendered,
  setPageElement,
}: {
  pdf: any;
  pageNumber: number;
  isTarget: boolean;
  sourceStatement: string;
  searchTerms: string[];
  statementTerms: string[];
  onRendered: (pageNumber: number, size: { width: number; height: number }, boxes: HighlightBox[]) => void;
  setPageElement: (el: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [boxes, setBoxes] = useState<HighlightBox[]>([]);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let renderTask: any;
    const render = async () => {
      setBoxes([]);
      const pdfPage = await pdf.getPage(pageNumber);
      if (cancelled) return;
      const viewport = pdfPage.getViewport({ scale: 1.4 });
      const outputScale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
      const width = Math.ceil(viewport.width);
      const height = Math.ceil(viewport.height);
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      canvas.width = Math.ceil(viewport.width * outputScale);
      canvas.height = Math.ceil(viewport.height * outputScale);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      setSize({ width, height });
      const context = canvas.getContext("2d");
      if (!context) throw new Error("PDF canvas is unavailable");
      renderTask = pdfPage.render({
        canvasContext: context,
        viewport,
        transform: [outputScale, 0, 0, outputScale, 0, 0],
      });
      await renderTask.promise;
      if (cancelled) return;
      let found: HighlightBox[] = [];
      if (isTarget) {
        const textContent = await pdfPage.getTextContent();
        if (cancelled) return;
        found = matchingBoxes(textContent.items as any[], viewport, sourceStatement, statementTerms, searchTerms);
        setBoxes(found);
      }
      onRendered(pageNumber, { width, height }, found);
    };
    render().catch(() => {
      if (isTarget && !cancelled) onRendered(pageNumber, { width: 0, height: 0 }, []);
    });
    return () => {
      cancelled = true;
      renderTask?.cancel?.();
    };
  }, [isTarget, onRendered, pageNumber, pdf, searchTerms, sourceStatement, statementTerms]);

  return (
    <div ref={setPageElement} className="gs-pdf-page" style={size ?? undefined}>
      <canvas ref={canvasRef} />
      {boxes.map((box, index) => <div key={`${box.left}-${box.top}-${index}`} className="gs-pdf-highlight" style={box} />)}
    </div>
  );
}
