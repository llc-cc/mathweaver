import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { ArrowRight, CheckCircle2, Circle, GitBranch, Loader2, Map as MapIcon, RefreshCw, RotateCcw, Sparkles, Trophy, X } from "lucide-react";
import type { CourseGraphSummary, EducationAssignment, EducationSnapshot } from "./education";
import {
  chooseAchievementAtlasGraphForCourse,
  deriveAchievementAtlasEdgeState,
  deriveAchievementAtlasNodeStates,
  type AchievementAtlasGraphSelection,
  type AchievementAtlasNodeState,
  type EducationAchievement,
} from "./education-game";
import {
  buildAchievementAtlasNetworkLayout,
  buildAchievementAtlasRouteCurve,
  deriveAchievementAtlasFocus,
  type AchievementAtlasLandmarkKind,
} from "./education-atlas-layout";

interface EducationAchievementAtlasProps {
  courseGraphs: CourseGraphSummary[];
  assignments: EducationAssignment[];
  initialSelection: AchievementAtlasGraphSelection | null;
  achievements: EducationAchievement[];
  loadCourseGraphSnapshot: (snapshotId: string) => Promise<EducationSnapshot>;
  onOpenCourseGraph: (snapshotId: string) => void;
  onClose: () => void;
}

function atlasNodeTitle(node: EducationSnapshot["nodes"][number]) {
  return node.title_zh || node.title_en || node.label || `节点 ${node.id}`;
}

function atlasStateLabel(state: AchievementAtlasNodeState) {
  if (state === "mastered") return "已掌握";
  if (state === "needs_review") return "待复习";
  return "未学习";
}

function AtlasStateIcon({ state }: { state: AchievementAtlasNodeState }) {
  if (state === "mastered") return <CheckCircle2 size={16} />;
  if (state === "needs_review") return <RotateCcw size={16} />;
  return <Circle size={14} />;
}

function AtlasLandmarkArtwork({ kind }: { kind: AchievementAtlasLandmarkKind }) {
  return (
    <svg className="edu-atlas-landmark-art" viewBox="0 0 118 94" aria-hidden="true">
      <ellipse className="edu-atlas-landmark-water-shadow" cx="59" cy="71" rx="55" ry="20" />
      <ellipse className="edu-atlas-landmark-water" cx="59" cy="67" rx="54" ry="22" />
      <path className="edu-atlas-landmark-water-ring" d="M12 68c8 10 25 16 47 16 23 0 40-6 48-16" />
      <path className="edu-atlas-landmark-shore-shadow" d="M16 68c8-18 23-28 43-28 22 0 38 10 44 28-10 10-26 15-44 15-20 0-36-5-43-15Z" />
      <path className="edu-atlas-landmark-shore" d="M16 64c8-17 23-27 43-27 22 0 38 10 44 27-10 10-26 15-44 15-20 0-36-5-43-15Z" />
      <path className="edu-atlas-landmark-grass" d="M24 61c8-14 21-21 35-21 16 0 29 7 36 21-9 7-22 11-36 11-15 0-28-4-35-11Z" />
      <path className="edu-atlas-landmark-grass-highlight" d="M30 56c8-8 17-12 28-12 12 0 22 4 30 12-10-4-18-5-29-5-10 0-19 1-29 5Z" />
      <path className="edu-atlas-landmark-ground-detail" d="M29 62l4-5m1 6 4-7m44 6 3-5m2 6 4-6" />
      {kind === "lighthouse" && <>
        <path className="edu-atlas-lighthouse-beam secondary" d="M59 17 17 30l40-5 43 6-41-14Z" />
        <path className="edu-atlas-lighthouse-beam" d="M59 13 25 23l32-3 35 5-33-12Z" />
        <path className="edu-atlas-lighthouse-platform" d="M45 58h29l-3 5H48l-3-5Z" />
        <path className="edu-atlas-lighthouse-tower" d="M48 58 52 27h14l5 31H48Z" />
        <path className="edu-atlas-lighthouse-stripe" d="m50 46 19-2 1 7-21 2 1-7Z" />
        <path className="edu-atlas-lighthouse-door" d="M56 49h7v9h-7Z" />
        <path className="edu-atlas-lighthouse-balcony" d="M49 27h20M51 24h16" />
        <rect className="edu-atlas-lighthouse-lantern" x="53" y="17" width="12" height="8" rx="2" />
        <path className="edu-atlas-lighthouse-roof" d="m50 17 9-9 9 9H50Z" />
        <circle className="edu-atlas-lighthouse-light" cx="59" cy="21" r="2.8" />
      </>}
      {kind === "monument" && <>
        <path className="edu-atlas-monument-base-shadow" d="M32 62h54l-6 5H38l-6-5Z" />
        <path className="edu-atlas-monument-base" d="M33 60h52l-6-10H40l-7 10Z" />
        <path className="edu-atlas-monument-step" d="M39 51h40v4H39Z" />
        <path className="edu-atlas-monument-stone" d="M43 51V22c0-6 5-10 16-10s16 4 16 10v29H43Z" />
        <path className="edu-atlas-monument-facet" d="M47 48V23c0-4 3-6 8-7l-2 32h-6Z" />
        <path className="edu-atlas-monument-cap" d="M41 23h36l-4-6H45l-4 6Z" />
        <path className="edu-atlas-monument-mark" d="M53 29h12m-12 7h12m-9 7h6" />
      </>}
      {kind === "highland" && <>
        <path className="edu-atlas-highland-back" d="m24 59 20-28 12 15 10-18 27 31H24Z" />
        <path className="edu-atlas-highland-snow back" d="m66 28 8 10 4-4 8 10-6-4-5 4-9-16Z" />
        <path className="edu-atlas-highland-mid" d="m31 61 23-34 19 34H31Z" />
        <path className="edu-atlas-highland-snow" d="m54 27 7 11 4-4 5 7-5-3-4 4-7-15Z" />
        <path className="edu-atlas-highland-front" d="m48 62 20-25 22 25H48Z" />
        <path className="edu-atlas-highland-ridge" d="m68 37 4 13 5-5 7 10" />
        <path className="edu-atlas-highland-pine" d="M31 61v-12m-5 7 5-9 5 9m-8-4 3-6 3 6M91 62v-10m-4 6 4-8 4 8" />
      </>}
      {kind === "camp" && <>
        <path className="edu-atlas-camp-pole" d="M76 18v43" />
        <path className="edu-atlas-camp-flag" d="M76 19c7-5 13 4 20-1v12c-7 5-13-4-20 1V19Z" />
        <path className="edu-atlas-camp-rope" d="m32 61 25-35 29 35M57 26v35" />
        <path className="edu-atlas-camp-tent-shadow" d="m29 62 28-37 31 37H29Z" />
        <path className="edu-atlas-camp-tent" d="m33 59 24-33 27 33H33Z" />
        <path className="edu-atlas-camp-tent-panel" d="m57 26 4 33H47l10-33Z" />
        <path className="edu-atlas-camp-tent-line" d="M44 48h27" />
        <path className="edu-atlas-camp-log" d="m76 62 14-6m-13 0 13 7" />
        <path className="edu-atlas-camp-fire outer" d="M80 59c-5-5 1-12 4-17 7 7 10 13 4 18-2 3-6 3-8-1Z" />
        <path className="edu-atlas-camp-fire" d="M83 59c-2-3 1-7 3-10 3 4 4 7 1 10-1 2-3 2-4 0Z" />
      </>}
      {kind === "reef" && <>
        <path className="edu-atlas-reef-rock one" d="m25 63 10-25 13 5 6 20H25Z" />
        <path className="edu-atlas-reef-facet one" d="m35 38 4 22-10 3 6-25Z" />
        <path className="edu-atlas-reef-rock two" d="m50 64 14-36 20 19-5 17H50Z" />
        <path className="edu-atlas-reef-facet two" d="m64 28 3 28-12 8 9-36Z" />
        <path className="edu-atlas-reef-rock three" d="m78 64 9-22 13 22H78Z" />
        <path className="edu-atlas-reef-wave back" d="M18 68c7-5 13 5 21 0 8-5 15 5 23 0 8-5 14 5 24 0 7-4 12 3 18 1" />
        <path className="edu-atlas-reef-wave" d="M24 74c7-5 13 5 21 0 8-5 15 5 23 0 8-5 14 5 24 0" />
      </>}
      {kind === "island" && <>
        <path className="edu-atlas-island-tree-trunk" d="m51 60 7-1 2-25h-6l-3 26Z" />
        <path className="edu-atlas-island-tree-branch" d="m57 43-9-8m10 4 9-10" />
        <circle className="edu-atlas-island-tree-leaf back" cx="46" cy="34" r="12" />
        <circle className="edu-atlas-island-tree-leaf back" cx="66" cy="31" r="13" />
        <circle className="edu-atlas-island-tree-leaf" cx="57" cy="28" r="15" />
        <circle className="edu-atlas-island-tree-highlight" cx="52" cy="23" r="5" />
        <path className="edu-atlas-island-bush" d="M27 61c-1-8 9-11 13-5 5-7 15-1 12 6H27Z" />
        <path className="edu-atlas-island-stone" d="m76 62 7-13 10 13H76Z" />
        <circle className="edu-atlas-island-flower" cx="38" cy="57" r="2" />
        <circle className="edu-atlas-island-flower second" cx="72" cy="57" r="1.7" />
      </>}
    </svg>
  );
}

export function EducationAchievementAtlas({
  courseGraphs,
  assignments,
  initialSelection,
  achievements,
  loadCourseGraphSnapshot,
  onOpenCourseGraph,
  onClose,
}: EducationAchievementAtlasProps) {
  const [selectedGraphId, setSelectedGraphId] = useState(initialSelection?.graph.id || courseGraphs[0]?.id || "");
  const [snapshot, setSnapshot] = useState<EducationSnapshot | null>(null);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [loadError, setLoadError] = useState("");
  const [retryVersion, setRetryVersion] = useState(0);
  const [focusedNodeId, setFocusedNodeId] = useState<number | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const cacheRef = useRef(new Map<string, EducationSnapshot>());
  const panSessionRef = useRef<{ pointerId: number; clientX: number; clientY: number; scrollLeft: number; scrollTop: number } | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const mapScrollRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const selection = useMemo(() => {
    const graph = courseGraphs.find(item => item.id === selectedGraphId) || initialSelection?.graph || courseGraphs[0];
    return graph ? chooseAchievementAtlasGraphForCourse(graph, assignments) : null;
  }, [assignments, courseGraphs, initialSelection, selectedGraphId]);

  useEffect(() => {
    if (selectedGraphId && courseGraphs.some(graph => graph.id === selectedGraphId)) return;
    setSelectedGraphId(initialSelection?.graph.id || courseGraphs[0]?.id || "");
  }, [courseGraphs, initialSelection, selectedGraphId]);

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    return () => { returnFocusRef.current?.focus(); };
  }, []);

  useEffect(() => {
    if (!selection) {
      setSnapshot(null);
      setLoadState("idle");
      setLoadError("");
      return;
    }
    const cached = cacheRef.current.get(selection.snapshotId);
    if (cached) {
      setSnapshot(cached);
      setLoadState("ready");
      setLoadError("");
      return;
    }
    let cancelled = false;
    setFocusedNodeId(null);
    setSnapshot(null);
    setLoadState("loading");
    setLoadError("");
    loadCourseGraphSnapshot(selection.snapshotId).then(value => {
      if (cancelled) return;
      cacheRef.current.set(selection.snapshotId, value);
      setSnapshot(value);
      setLoadState("ready");
    }).catch(cause => {
      if (cancelled) return;
      setLoadState("error");
      setLoadError(cause instanceof Error ? cause.message : "课程图谱加载失败");
    });
    return () => { cancelled = true; };
  }, [loadCourseGraphSnapshot, retryVersion, selection]);

  const nodeStates = useMemo(() => snapshot && selection
    ? deriveAchievementAtlasNodeStates(snapshot.nodes, assignments, selection.snapshotId)
    : {}, [assignments, selection, snapshot]);
  const layout = useMemo(() => snapshot ? buildAchievementAtlasNetworkLayout(snapshot.nodes, snapshot.edges) : null, [snapshot]);
  const focus = useMemo(() => focusedNodeId !== null && layout ? deriveAchievementAtlasFocus(focusedNodeId, layout) : null, [focusedNodeId, layout]);

  useEffect(() => {
    const scroll = mapScrollRef.current;
    if (!scroll || !layout) return;
    const frame = window.requestAnimationFrame(() => {
      scroll.scrollLeft = Math.max(0, (layout.width - scroll.clientWidth) / 2);
      scroll.scrollTop = Math.max(0, (layout.height - scroll.clientHeight) / 2);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [layout, selection?.snapshotId]);

  const counts = useMemo(() => Object.values(nodeStates).reduce((result, state) => {
    result[state] += 1;
    return result;
  }, { mastered: 0, needs_review: 0, unlearned: 0 }), [nodeStates]);
  const unlockedAchievementCount = achievements.filter(achievement => achievement.unlocked).length;

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const retry = () => {
    if (selection) cacheRef.current.delete(selection.snapshotId);
    setRetryVersion(value => value + 1);
  };

  const openProfessionalGraph = () => {
    if (!selection) return;
    const snapshotId = selection.snapshotId;
    onClose();
    onOpenCourseGraph(snapshotId);
  };

  const handleMapPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (event.target instanceof Element && event.target.closest(".edu-atlas-landmark")) return;
    const scroll = event.currentTarget;
    panSessionRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      scrollLeft: scroll.scrollLeft,
      scrollTop: scroll.scrollTop,
    };
    scroll.setPointerCapture(event.pointerId);
    setIsPanning(true);
    event.preventDefault();
  };

  const handleMapPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSessionRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    event.currentTarget.scrollLeft = session.scrollLeft - (event.clientX - session.clientX);
    event.currentTarget.scrollTop = session.scrollTop - (event.clientY - session.clientY);
    event.preventDefault();
  };

  const finishMapPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSessionRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    panSessionRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setIsPanning(false);
  };

  return (
    <div className="edu-adventure-modal-backdrop edu-atlas-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        ref={dialogRef}
        className="edu-atlas-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edu-atlas-title"
        onKeyDown={handleDialogKeyDown}
      >
        <header className="edu-atlas-header">
          <div className="edu-atlas-heading">
            <span className="edu-atlas-heading-icon"><MapIcon size={21} /></span>
            <div><span className="edu-kicker">学习成果图鉴</span><h2 id="edu-atlas-title">{selection?.graph.filename || "课程成果地图"}</h2><small>用岛屿地标保留完整关系网络，点亮已经掌握和需要回看的知识路径。</small></div>
          </div>
          <div className="edu-atlas-header-actions">
            {courseGraphs.length > 1 && (
              <label className="edu-atlas-graph-select">
                <span>课程图谱</span>
                <select value={selectedGraphId} onChange={event => setSelectedGraphId(event.target.value)}>
                  {courseGraphs.map(graph => <option value={graph.id} key={graph.id}>{graph.filename}</option>)}
                </select>
              </label>
            )}
            <button type="button" className="edu-button ghost edu-atlas-professional-button" disabled={!selection} onClick={openProfessionalGraph}><GitBranch size={14} />打开专业图谱<ArrowRight size={13} /></button>
            <button ref={closeButtonRef} type="button" className="edu-icon-button" onClick={onClose} aria-label="关闭成果图鉴"><X size={17} /></button>
          </div>
        </header>

        <div className="edu-atlas-summary" aria-label="成果图鉴统计">
          <div className="edu-atlas-summary-item mastered"><CheckCircle2 size={17} /><span><strong>{counts.mastered}</strong><small>已掌握</small></span></div>
          <div className="edu-atlas-summary-item needs-review"><RotateCcw size={17} /><span><strong>{counts.needs_review}</strong><small>待复习</small></span></div>
          <div className="edu-atlas-summary-item unlearned"><Circle size={15} /><span><strong>{counts.unlearned}</strong><small>未学习</small></span></div>
          <div className="edu-atlas-summary-item achievements"><Trophy size={17} /><span><strong>{unlockedAchievementCount}/{achievements.length}</strong><small>探索成就</small></span></div>
          <div className="edu-atlas-legend" aria-label="地图图例">
            <span className="mastered"><i />已掌握</span><span className="needs-review"><i />待复习</span><span className="unlearned"><i />未学习</span>
          </div>
          <section className="edu-atlas-achievements" aria-labelledby="edu-atlas-achievements-title">
            <header>
              <div><Trophy size={15} /><strong id="edu-atlas-achievements-title">探索成就</strong></div>
              <span>{unlockedAchievementCount} / {achievements.length} 已解锁</span>
            </header>
            <div className="edu-atlas-achievement-list" role="list">
              {achievements.map(achievement => (
                <article
                  className={`edu-atlas-achievement${achievement.unlocked ? " unlocked" : " locked"}`}
                  role="listitem"
                  aria-label={`${achievement.title}，${achievement.unlocked ? "已解锁" : "待解锁"}`}
                  key={achievement.key}
                >
                  <span className="edu-atlas-achievement-medal">{achievement.unlocked ? <CheckCircle2 size={17} /> : <Circle size={15} />}</span>
                  <span className="edu-atlas-achievement-copy"><strong>{achievement.title}</strong><small>{achievement.description}</small></span>
                  <span className="edu-atlas-achievement-state">{achievement.unlocked ? "已解锁" : "待解锁"}</span>
                </article>
              ))}
            </div>
          </section>
        </div>

        <div className="edu-atlas-content">
          {!selection && <div className="edu-atlas-empty"><MapIcon size={31} /><strong>暂无课程图谱</strong><span>教师加入课程图谱后，这里会生成你的成果地图。</span></div>}
          {selection && loadState === "loading" && <div className="edu-atlas-empty" aria-live="polite"><Loader2 className="edu-spin" size={27} /><strong>正在绘制成果地图…</strong><span>正在加载课程节点、关系与学习状态。</span></div>}
          {selection && loadState === "error" && <div className="edu-atlas-empty error" role="alert"><RefreshCw size={27} /><strong>成果地图暂时无法加载</strong><span>{loadError}</span><button type="button" className="edu-button secondary" onClick={retry}>重新加载</button></div>}
          {selection && loadState === "ready" && snapshot && !snapshot.nodes.length && <div className="edu-atlas-empty"><MapIcon size={31} /><strong>这张图谱还没有节点</strong><span>课程图谱补充知识节点后，成果地标会显示在这里。</span></div>}
          {selection && loadState === "ready" && snapshot && snapshot.nodes.length > 0 && layout && (
            <>
              <span id="edu-atlas-pan-hint" className="edu-atlas-pan-hint">{"\u62d6\u52a8\u753b\u5e03\u8c03\u6574\u89c6\u89d2"}</span>
              <div
                ref={mapScrollRef}
                className={`edu-atlas-map-scroll${isPanning ? " is-panning" : ""}`}
                role="region"
                tabIndex={0}
                aria-label={`${snapshot.filename} 成果地图`}
                aria-describedby="edu-atlas-pan-hint"
                onPointerDown={handleMapPointerDown}
                onPointerMove={handleMapPointerMove}
                onPointerUp={finishMapPan}
                onPointerCancel={finishMapPan}
                onLostPointerCapture={() => { panSessionRef.current = null; setIsPanning(false); }}
              >
              <div className={`edu-atlas-map-canvas${snapshot.nodes.length > 250 ? " dense" : ""}`} style={{ width: layout.width, height: layout.height }}>
                <svg className="edu-atlas-map-art" width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-hidden="true">
                  <defs>
                    <filter id="edu-atlas-soft-shadow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="8" /></filter>
                  </defs>
                  <g className="edu-atlas-background-art">
                    {Array.from({ length: Math.max(7, Math.min(17, Math.ceil(snapshot.nodes.length / 4))) }, (_, index) => {
                      const x = 82 + (index * 197) % Math.max(240, layout.width - 120);
                      const y = 72 + (index * 131) % Math.max(200, layout.height - 110);
                      return <ellipse className="edu-atlas-terrain" cx={x} cy={y} rx={42 + index % 3 * 15} ry={18 + index % 2 * 11} key={`terrain-${index}`} />;
                    })}
                    {Array.from({ length: 6 }, (_, index) => <path className="edu-atlas-water-ripple" d={`M ${90 + index * 242} ${88 + index % 3 * 168} q 20 -12 40 0 q 20 12 40 0`} key={`ripple-${index}`} />)}
                  </g>
                  {layout.edges.map(item => {
                    const from = layout.positions[item.edge.from];
                    const to = layout.positions[item.edge.to];
                    if (!from || !to) return null;
                    const curve = buildAchievementAtlasRouteCurve(from, to, item.key);
                    const state = deriveAchievementAtlasEdgeState(item.edge, nodeStates);
                    const emphasis = !focus ? "" : focus.edgeKeys.has(item.key) ? "is-focus" : "is-muted";
                    return (
                      <g className={`edu-atlas-route ${item.routeKind} ${state} ${emphasis}`} key={item.key}>
                        <path className="edu-atlas-route-bed" d={curve.path} />
                        <path className="edu-atlas-route-texture" d={curve.path} />
                        <path className="edu-atlas-route-status" d={curve.path} />
                        <path className="edu-atlas-route-arrow" d="M -5 -3.2 L 5 0 L -5 3.2 Z" transform={`translate(${curve.arrow.x} ${curve.arrow.y}) rotate(${curve.arrow.angle})`} />
                      </g>
                    );
                  })}
                </svg>
                <div className="edu-atlas-landmarks" role="list" aria-label="知识节点成果状态">
                  {snapshot.nodes.map(node => {
                    const position = layout.positions[node.id];
                    if (!position) return null;
                    const state = nodeStates[node.id] || "unlearned";
                    const title = atlasNodeTitle(node);
                    const kind = layout.landmarkKinds[node.id] || "island";
                    const isMuted = Boolean(focus && !focus.nodeIds.has(node.id));
                    const isFocused = Boolean(focus && focus.nodeIds.has(node.id));
                    const relationshipCount = layout.neighborIdsByNode[node.id]?.length || 0;
                    return (
                      <article
                        className={`edu-atlas-landmark ${kind} ${state}${isMuted ? " is-muted" : ""}${isFocused ? " is-focus" : ""}`}
                        style={{ left: position.x, top: position.y }}
                        role="listitem"
                        tabIndex={0}
                        aria-label={`${title}，${node.node_type || "知识节点"}，${atlasStateLabel(state)}，${relationshipCount} 个直接关联节点`}
                        key={node.id}
                        title={title}
                        onMouseEnter={() => setFocusedNodeId(node.id)}
                        onMouseLeave={() => setFocusedNodeId(current => current === node.id ? null : current)}
                        onFocus={() => setFocusedNodeId(node.id)}
                        onBlur={() => setFocusedNodeId(current => current === node.id ? null : current)}
                      >
                        <span className="edu-atlas-landmark-scene"><AtlasLandmarkArtwork kind={kind} /></span>
                        <span className="edu-atlas-landmark-state"><AtlasStateIcon state={state} /></span>
                        <span className="edu-atlas-landmark-copy"><strong>{title}</strong><small>{node.node_type || "知识节点"} · {atlasStateLabel(state)}</small></span>
                        {state === "mastered" && <Sparkles className="edu-atlas-landmark-sparkle" size={14} aria-hidden="true" />}
                      </article>
                    );
                  })}
                </div>
              </div>
            </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
