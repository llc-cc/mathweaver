import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";
import { Network, type Edge, type Node, type Options } from "vis-network";
import {
  ArrowRight,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  CircleDot,
  FileText,
  GraduationCap,
  History,
  Lightbulb,
  ListChecks,
  Network as NetworkIcon,
  ScanText,
  Search,
  Sparkles,
} from "lucide-react";
import "./landing.css";

export function meta() {
  return [
    { title: "绎理 MathWeaver｜面向学生的数学知识图谱学习平台" },
    {
      name: "description",
      content:
        "在课程知识图谱中探索依赖关系、定位教材原文、完成证明学习与理解考核，并持续获得学习反馈。",
    },
  ];
}

type DemoNode = {
  id: number;
  type: string;
  title: string;
  statement: string;
};

type DemoEdge = {
  id: number;
  from: number;
  to: number;
  label: string;
  description: string;
};

type DemoRelation = {
  edge: DemoEdge;
  node: DemoNode;
  direction: "dependency" | "dependent";
};

type GraphLayout = "force" | "reading" | "swimlane" | "dag";

const DEMO_NODES: DemoNode[] = [
  { id: 1, type: "定义", title: "向量空间", statement: "在同一数域上配备向量加法与数乘，并满足八条基本运算公理的集合。" },
  { id: 2, type: "定义", title: "子空间", statement: "向量空间的非空子集若对向量加法与数乘封闭，则称为该空间的子空间。" },
  { id: 3, type: "定义", title: "线性组合与张成", statement: "向量组的线性组合是其有限数乘之和；所有线性组合构成该向量组的张成空间。" },
  { id: 4, type: "定义", title: "线性相关与无关", statement: "仅当系数全为零时线性组合才等于零的向量组称为线性无关组。" },
  { id: 5, type: "定义", title: "基与维数", statement: "既线性无关又张成整个空间的向量组称为基；有限基所含向量个数称为维数。" },
  { id: 6, type: "引理", title: "Steinitz 交换引理", statement: "有限生成空间中的任一线性无关组，都可用来替换某些生成向量而仍保持张成。" },
  { id: 7, type: "定理", title: "基扩张定理", statement: "有限维向量空间中的任一线性无关组都可以扩张为一组基。" },
  { id: 8, type: "推论", title: "满维无关组构成基", statement: "n维向量空间中任意含n个向量的线性无关组都是一组基。" },
  { id: 9, type: "定义", title: "线性映射", statement: "保持向量加法与数乘运算的映射称为线性映射。" },
  { id: 10, type: "定义", title: "核与像", statement: "线性映射把零向量作为像的向量组成核；所有输出向量组成像。" },
  { id: 11, type: "定理", title: "秩-零化度定理", statement: "若V为有限维空间且T: V → W线性，则dim V = dim ker T + dim im T。" },
  { id: 12, type: "命题", title: "单射与核的判别", statement: "线性映射T是单射，当且仅当ker T只含零向量。" },
  { id: 13, type: "定义", title: "线性映射的矩阵", statement: "选定定义域与值域的基后，线性映射由作用在基向量上的坐标列唯一表示。" },
  { id: 14, type: "定义", title: "矩阵的秩", statement: "矩阵列空间的维数称为矩阵的秩，并等于对应线性映射像空间的维数。" },
  { id: 15, type: "引理", title: "初等变换保持秩", statement: "对矩阵实施任意一次初等行变换或初等列变换都不改变矩阵的秩。" },
  { id: 16, type: "命题", title: "可逆矩阵的等价条件", statement: "n阶矩阵可逆，当且仅当其秩为n；这也等价于对应线性映射为双射。" },
  { id: 17, type: "定义", title: "特征值与特征向量", statement: "若存在非零向量v使Av = λv，则λ是A的特征值，v是对应特征向量。" },
  { id: 18, type: "定义", title: "特征多项式", statement: "多项式det(λI - A)称为矩阵A的特征多项式，其根是A的特征值。" },
  { id: 19, type: "定理", title: "不同特征值对应向量无关", statement: "属于两两不同特征值的非零特征向量必定线性无关。" },
  { id: 20, type: "推论", title: "特征值互异时可对角化", statement: "n阶矩阵若有n个互不相同的特征值，则它相似于一个对角矩阵。" },
];

const DEMO_EDGES: DemoEdge[] = [
  { id: 1, from: 1, to: 2, label: "定义依赖", description: "子空间沿用向量空间中的加法、数乘及其运算公理。" },
  { id: 2, from: 1, to: 3, label: "推导", description: "线性组合的运算发生在给定数域上的向量空间中。" },
  { id: 3, from: 3, to: 4, label: "定义依赖", description: "线性相关性通过等于零向量的线性组合来刻画。" },
  { id: 4, from: 3, to: 5, label: "定义依赖", description: "一组基首先需要张成整个向量空间。" },
  { id: 5, from: 4, to: 5, label: "定义依赖", description: "一组基还必须满足线性无关性。" },
  { id: 6, from: 3, to: 6, label: "推导", description: "交换引理比较线性无关组与有限生成组的张成能力。" },
  { id: 7, from: 4, to: 6, label: "推导", description: "交换引理以待交换向量组线性无关为前提。" },
  { id: 8, from: 6, to: 7, label: "推导", description: "反复应用交换引理，可以把线性无关组补充成一组基。" },
  { id: 9, from: 5, to: 8, label: "推导", description: "该结论比较无关向量个数与空间基的向量个数。" },
  { id: 10, from: 7, to: 8, label: "推导", description: "满维无关组无需再添加向量，其基扩张只能是自身。" },
  { id: 11, from: 1, to: 9, label: "定义依赖", description: "线性映射保持向量空间中的加法与数乘结构。" },
  { id: 12, from: 9, to: 10, label: "定义依赖", description: "核与像都由给定线性映射的输入和输出确定。" },
  { id: 13, from: 5, to: 11, label: "推导", description: "秩-零化度定理使用有限维空间及子空间的维数。" },
  { id: 14, from: 10, to: 11, label: "推导", description: "定理把定义域维数分解为核维数与像维数之和。" },
  { id: 15, from: 10, to: 12, label: "推导", description: "核中是否存在非零向量，恰好决定线性映射能否保持输入的唯一性。" },
  { id: 16, from: 5, to: 13, label: "推导", description: "线性映射的矩阵表示需要先为定义域和值域选定基。" },
  { id: 17, from: 9, to: 13, label: "定义依赖", description: "矩阵的各列记录线性映射作用在定义域基向量上的坐标。" },
  { id: 18, from: 13, to: 14, label: "定义依赖", description: "矩阵的列空间对应于线性映射在所选基下的像空间。" },
  { id: 19, from: 14, to: 15, label: "推导", description: "初等变换保持行空间或列空间的维数，因此不改变秩。" },
  { id: 20, from: 14, to: 16, label: "推导", description: "满秩是方阵可逆的一个核心等价条件。" },
  { id: 21, from: 13, to: 17, label: "推导", description: "特征值方程研究线性变换在同一组基下的方阵表示。" },
  { id: 22, from: 17, to: 18, label: "定义依赖", description: "特征多项式的根刻画特征值方程存在非零解的参数。" },
  { id: 23, from: 4, to: 19, label: "推导", description: "定理的结论与证明均建立在线性无关性的定义之上。" },
  { id: 24, from: 17, to: 19, label: "推导", description: "两两不同的特征值是对应特征向量线性无关的关键条件。" },
  { id: 25, from: 5, to: 20, label: "推导", description: "n个线性无关特征向量在n维空间中构成一组基。" },
  { id: 26, from: 19, to: 20, label: "推导", description: "n个互异特征值给出n个线性无关特征向量，从而形成特征向量基。" },
];

const TYPE_COLORS: Record<string, { border: string; bg: string }> = {
  定义: { border: "#7d756a", bg: "#f7f5f1" },
  引理: { border: "#1e5aa8", bg: "#eaf1fa" },
  定理: { border: "#2f7d56", bg: "#ecf6f0" },
  推论: { border: "#7655a6", bg: "#f4eff9" },
  命题: { border: "#b08542", bg: "#f8efdf" },
};

const RELATION_COLORS: Record<string, string> = {
  定义依赖: "#6f7f95",
  推导: "#4f78aa",
};

const ALL_NODE_IDS = DEMO_NODES.map((node) => node.id);
const PATH_NODE_IDS = [4, 3, 5, 6, 7];

function useVisibleLoop(stepCount: number, intervalMs: number, initialStep = 0) {
  const ref = useRef<HTMLDivElement>(null);
  const resumeTimer = useRef<number | null>(null);
  const [step, setStep] = useState(initialStep);
  const [visible, setVisible] = useState(false);
  const [manual, setManual] = useState(false);
  const [reduced, setReduced] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting && entry.intersectionRatio >= 0.35),
      { threshold: [0, 0.35, 0.65] },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onVisibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (reduced) {
      setStep(stepCount - 1);
      return;
    }
    if (!visible || manual || !pageVisible) return;
    const timer = window.setInterval(
      () => setStep((value) => (value + 1) % stepCount),
      intervalMs,
    );
    return () => window.clearInterval(timer);
  }, [intervalMs, manual, pageVisible, reduced, stepCount, visible]);

  useEffect(
    () => () => {
      if (resumeTimer.current !== null) window.clearTimeout(resumeTimer.current);
    },
    [],
  );

  const interact = useCallback((nextStep?: number) => {
    if (typeof nextStep === "number") {
      setStep(Math.max(0, Math.min(stepCount - 1, nextStep)));
    }
    setManual(true);
    if (resumeTimer.current !== null) window.clearTimeout(resumeTimer.current);
    resumeTimer.current = window.setTimeout(() => setManual(false), 8000);
  }, [stepCount]);

  return { ref, step, setStep, interact, active: visible && !manual && !reduced && pageVisible };
}

function GraphNetwork({
  nodeIds = ALL_NODE_IDS,
  focusId,
  layout = "force",
  showLabels = false,
  active = true,
  onSelect,
  className = "",
}: {
  nodeIds?: number[];
  focusId?: number;
  layout?: GraphLayout;
  showLabels?: boolean;
  active?: boolean;
  onSelect?: (id: number) => void;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const onSelectRef = useRef(onSelect);
  const nodeKey = nodeIds.join(",");

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!containerRef.current) return;
    const allowed = new Set(nodeIds);
    const nodes: Node[] = DEMO_NODES.filter((node) => allowed.has(node.id)).map((node) => {
      const color = TYPE_COLORS[node.type] ?? TYPE_COLORS.定义;
      return {
        id: node.id,
        label: node.title,
        shape: node.type === "定理" ? "ellipse" : "box",
        color: {
          background: color.bg,
          border: color.border,
          highlight: { background: "#ffffff", border: "#1e5aa8" },
        },
        borderWidth: 1.5,
        font: { color: "#1c1b19", size: 12, face: "Inter, sans-serif" },
        margin: { top: 9, right: 10, bottom: 9, left: 10 },
      };
    });
    const edges: Edge[] = DEMO_EDGES.filter(
      (edge) => allowed.has(edge.from) && allowed.has(edge.to),
    ).map((edge) => ({
      id: edge.id,
      from: edge.from,
      to: edge.to,
      label: showLabels ? edge.label : "",
      arrows: { to: { enabled: true, scaleFactor: 0.48 } },
      color: {
        color: RELATION_COLORS[edge.label] ?? "#b9b1a6",
        highlight: "#1e5aa8",
        opacity: 0.62,
      },
      font: { size: 9, color: "#6b6864", strokeWidth: 4, strokeColor: "#faf9f7" },
      width: 1.15,
    }));

    const hierarchical = layout !== "force";
    const options: Options = {
      autoResize: true,
      layout: hierarchical
        ? {
            hierarchical: {
              enabled: true,
              direction: layout === "swimlane" ? "LR" : "UD",
              sortMethod: "directed",
              levelSeparation: layout === "reading" ? 92 : 112,
              nodeSpacing: layout === "swimlane" ? 140 : 128,
              treeSpacing: 160,
              blockShifting: true,
              edgeMinimization: true,
              parentCentralization: true,
            },
          }
        : { randomSeed: 17, improvedLayout: true },
      physics: hierarchical
        ? { enabled: false }
        : {
            solver: "forceAtlas2Based",
            forceAtlas2Based: {
              gravitationalConstant: -42,
              centralGravity: 0.025,
              springLength: 118,
              springConstant: 0.07,
              avoidOverlap: 0.75,
              damping: 0.4,
            },
            stabilization: { iterations: 190, fit: true },
            minVelocity: 0.35,
            maxVelocity: 14,
          },
      interaction: {
        hover: true,
        dragNodes: true,
        dragView: true,
        zoomView: true,
        tooltipDelay: 120,
      },
      edges: {
        smooth: hierarchical
          ? { enabled: true, type: "cubicBezier", forceDirection: layout === "swimlane" ? "horizontal" : "vertical", roundness: 0.42 }
          : { enabled: true, type: "dynamic", roundness: 0.38 },
      },
    };

    const network = new Network(containerRef.current, { nodes, edges }, options);
    networkRef.current = network;
    const onClick = (params: { nodes: Array<string | number> }) => {
      if (params.nodes.length) onSelectRef.current?.(Number(params.nodes[0]));
    };
    network.on("click", onClick);
    network.on("hoverNode", () => {
      if (containerRef.current) containerRef.current.style.cursor = "pointer";
    });
    network.on("blurNode", () => {
      if (containerRef.current) containerRef.current.style.cursor = "grab";
    });

    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [layout, nodeKey, showLabels]);

  useEffect(() => {
    const network = networkRef.current;
    if (!network || !focusId || !nodeIds.includes(focusId)) return;
    network.selectNodes([focusId]);
    network.focus(focusId, {
      scale: layout === "force" ? 1.02 : 0.88,
      animation: { duration: 520, easingFunction: "easeInOutQuad" },
    });
  }, [focusId, layout, nodeKey, nodeIds]);

  useEffect(() => {
    if (!active || layout !== "force") return;
    const timer = window.setInterval(() => {
      const network = networkRef.current;
      if (!network) return;
      const positions = network.getPositions();
      for (const id of nodeIds) {
        const position = positions[id];
        if (!position) continue;
        const phase = ((id * 17) % 9) - 4;
        network.moveNode(id, position.x + phase * 0.65, position.y - phase * 0.42);
      }
      network.startSimulation();
    }, 2600);
    return () => window.clearInterval(timer);
  }, [active, layout, nodeIds, nodeKey]);

  return <div ref={containerRef} className={`brief-graph-canvas ${className}`} aria-label="高等代数知识图谱" />;
}

function DemoGraph({ compact = false }: { compact?: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const pendingFocusRef = useRef<number | null>(null);
  const [selected, setSelected] = useState<DemoNode | DemoEdge>(DEMO_NODES[10]);
  const [selectedKind, setSelectedKind] = useState<"node" | "edge">("node");
  const [filter, setFilter] = useState("全部");
  const [heroCard, setHeroCard] = useState<{ node: DemoNode; x: number; y: number } | null>(null);

  const selectDemoNode = (node: DemoNode) => {
    pendingFocusRef.current = node.id;
    if (filter !== "全部" && filter !== node.type) setFilter("全部");
    setSelected(node);
    setSelectedKind("node");
  };

  useEffect(() => {
    if (!containerRef.current) return;
    const visibleNodes = filter === "全部" ? DEMO_NODES : DEMO_NODES.filter((node) => node.type === filter);
    const visibleIds = new Set(visibleNodes.map((node) => node.id));
    const visibleEdges = DEMO_EDGES.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));
    const nodes: Node[] = visibleNodes.map((node) => {
      const color = TYPE_COLORS[node.type] ?? TYPE_COLORS["定义"];
      return {
        id: node.id,
        label: node.title,
        shape: node.type === "定理" ? "ellipse" : "box",
        color: { background: color.bg, border: color.border, highlight: { background: "#ffffff", border: "#1c1b19" } },
        borderWidth: 1.5,
        font: { color: "#1c1b19", size: compact ? 13 : 13, face: "Inter" },
        margin: { top: compact ? 8 : 11, right: compact ? 8 : 11, bottom: compact ? 8 : 11, left: compact ? 8 : 11 },
      };
    });
    const edges: Edge[] = visibleEdges.map((edge) => ({
      id: edge.id,
      from: edge.from,
      to: edge.to,
      label: compact ? "" : edge.label,
      arrows: "to",
      color: { color: "#c8c0b4", highlight: "#1e5aa8" },
      font: { size: 11, color: "#6b6864", strokeWidth: 3, strokeColor: "#ffffff" },
      width: 1.2,
    }));
    const options: Options = {
      autoResize: true,
      layout: compact ? { randomSeed: 17 } : {
        hierarchical: {
          enabled: true,
          direction: "UD",
          sortMethod: "directed",
          levelSeparation: 112,
          nodeSpacing: 150,
          treeSpacing: 180,
          blockShifting: true,
          edgeMinimization: true,
          parentCentralization: true,
        },
      },
      physics: compact ? {
        solver: "forceAtlas2Based",
        forceAtlas2Based: { gravitationalConstant: -42, centralGravity: 0.03, springLength: 120, avoidOverlap: 0.8, damping: 0.385 },
        stabilization: { iterations: 220 },
        minVelocity: 0.6,
        maxVelocity: 16,
      } : { enabled: false },
      interaction: { hover: true, zoomView: !compact, dragView: !compact },
      edges: { smooth: compact
        ? { enabled: true, type: "dynamic", roundness: 0.4 }
        : { enabled: true, type: "cubicBezier", forceDirection: "vertical", roundness: 0.42 } },
    };
    networkRef.current?.destroy();
    const network = new Network(containerRef.current, { nodes, edges }, options);
    networkRef.current = network;
    const mobileFocusFrame = !compact && containerRef.current.clientWidth < 600
      ? window.requestAnimationFrame(() => {
        network.moveTo({
          position: network.getViewPosition(),
          scale: Math.min(network.getScale() * 1.8, 0.7),
        });
      })
      : 0;
    network.on("click", (params) => {
      if (compact) {
        if (params.nodes.length) {
          const node = DEMO_NODES.find((item) => item.id === Number(params.nodes[0]));
          if (node) {
            const dom = network.canvasToDOM(network.getPositions([node.id])[node.id]);
            setHeroCard({ node, x: dom.x, y: dom.y });
          }
        } else {
          setHeroCard(null);
        }
        return;
      }
      if (params.nodes.length) {
        const node = DEMO_NODES.find((item) => item.id === Number(params.nodes[0]));
        if (node) { setSelected(node); setSelectedKind("node"); }
      } else if (params.edges.length) {
        const edge = DEMO_EDGES.find((item) => item.id === Number(params.edges[0]));
        if (edge) { setSelected(edge); setSelectedKind("edge"); }
      }
    });

    if (!compact) {
      // 限制缩放上下界，避免误操作缩放到无法恢复
      const MIN_SCALE = containerRef.current.clientWidth < 600 ? 0.25 : 0.55;
      const MAX_SCALE = 2.2;
      network.on("zoom", () => {
        const s = network.getScale();
        if (s < MIN_SCALE) network.moveTo({ scale: MIN_SCALE });
        else if (s > MAX_SCALE) network.moveTo({ scale: MAX_SCALE });
      });
      return () => {
        if (mobileFocusFrame) window.cancelAnimationFrame(mobileFocusFrame);
        network.destroy();
      };
    }

    // D · 图谱涌现：散开 → 弹性归位的入场动画
    const container = containerRef.current!;
    network.once("stabilized", () => {
      for (const node of visibleNodes) {
        network.moveNode(node.id, (Math.random() - 0.5) * 1100, (Math.random() - 0.5) * 720);
      }
      network.moveTo({ scale: 1.45 });
      network.startSimulation();
    });

    // A · 活体图谱：鼠标弹性力场 + 持续轻微漂浮
    let raf = 0;
    const repel = (clientX: number, clientY: number) => {
      const rect = container.getBoundingClientRect();
      const cursor = network.DOMtoCanvas({ x: clientX - rect.left, y: clientY - rect.top });
      const positions = network.getPositions();
      const R = 135;
      for (const idStr of Object.keys(positions)) {
        const id = Number(idStr);
        const p = positions[id];
        const dx = p.x - cursor.x, dy = p.y - cursor.y;
        const dist = Math.hypot(dx, dy) || 1;
        if (dist < R) {
          const force = (R - dist) * 0.38;
          network.moveNode(id, p.x + (dx / dist) * force, p.y + (dy / dist) * force);
        }
      }
      network.startSimulation();
    };
    const onMove = (e: MouseEvent) => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; repel(e.clientX, e.clientY); });
    };
    container.addEventListener("mousemove", onMove);
    const breathe = window.setInterval(() => {
      const positions = network.getPositions();
      for (const idStr of Object.keys(positions)) {
        const id = Number(idStr);
        const p = positions[id];
        network.moveNode(id, p.x + (Math.random() - 0.5) * 7, p.y + (Math.random() - 0.5) * 7);
      }
      network.startSimulation();
    }, 2600);
    const onHover = () => { container.style.cursor = "pointer"; };
    const onBlur = () => { container.style.cursor = "default"; };
    network.on("hoverNode", onHover);
    network.on("blurNode", onBlur);

    return () => {
      window.clearInterval(breathe);
      if (raf) cancelAnimationFrame(raf);
      container.removeEventListener("mousemove", onMove);
      network.destroy();
    };
  }, [compact, filter]);

  useEffect(() => {
    if (compact || selectedKind !== "node") return;
    const node = selected as DemoNode;
    if (filter !== "全部" && filter !== node.type) return;
    const frame = window.requestAnimationFrame(() => {
      const network = networkRef.current;
      if (!network) return;
      network.selectNodes([node.id]);
      if (pendingFocusRef.current === node.id) {
        network.focus(node.id, { scale: 1.08, animation: { duration: 360, easingFunction: "easeInOutQuad" } });
        pendingFocusRef.current = null;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [compact, filter, selected, selectedKind]);

  if (compact) return (
    <div className="lp-hero-graph">
      <div ref={containerRef} className="lp-hero-canvas" aria-label="数学知识图谱示例" />
      {heroCard && (
        <div className="lp-hero-card" style={{ left: heroCard.x, top: heroCard.y }}>
          <span className="lp-node-type" style={{ borderColor: TYPE_COLORS[heroCard.node.type]?.border, color: TYPE_COLORS[heroCard.node.type]?.border }}>{heroCard.node.type}</span>
          <strong>{heroCard.node.title}</strong>
          <p>{heroCard.node.statement}</p>
        </div>
      )}
    </div>
  );

  const nodeSelected = selectedKind === "node" ? selected as DemoNode : null;
  const edgeSelected = selectedKind === "edge" ? selected as DemoEdge : null;
  const selectedRelations = nodeSelected ? DEMO_EDGES.flatMap<DemoRelation>((edge) => {
    if (edge.to === nodeSelected.id) {
      const node = DEMO_NODES.find((item) => item.id === edge.from);
      return node ? [{ edge, node, direction: "dependency" }] : [];
    }
    if (edge.from === nodeSelected.id) {
      const node = DEMO_NODES.find((item) => item.id === edge.to);
      return node ? [{ edge, node, direction: "dependent" }] : [];
    }
    return [];
  }) : [];
  return (
    <div className="lp-demo-shell">
      <div className="lp-demo-toolbar">
        <div>
          <span className="lp-kicker">Interactive Demo</span>
          <strong>本科高等代数知识图谱</strong>
        </div>
        <div className="lp-filter-row">
          {["全部", ...Object.keys(TYPE_COLORS)].map((type) => (
            <button key={type} className={filter === type ? "active" : ""} onClick={() => setFilter(type)}>{type}</button>
          ))}
        </div>
      </div>
      <div className="lp-demo-main">
        <div ref={containerRef} className="lp-demo-graph" />
        <aside className="lp-demo-detail" aria-live="polite">
          {nodeSelected && <>
            <span className="lp-node-type lp-demo-node-type" style={{ background: TYPE_COLORS[nodeSelected.type]?.border, borderColor: TYPE_COLORS[nodeSelected.type]?.border }}>{nodeSelected.type}</span>
            <h3>{nodeSelected.title}</h3>
            <section className="lp-demo-detail-section">
              <span className="lp-demo-detail-label">陈述</span>
              <p>{nodeSelected.statement}</p>
            </section>
            {selectedRelations.length > 0 && <>
              <div className="lp-demo-detail-separator" />
              <div className="lp-demo-detail-label">依赖关系（{selectedRelations.length}）</div>
              <div className="lp-demo-relation-list">
                {selectedRelations.map(({ edge, node, direction }) => (
                  <button
                    key={edge.id}
                    type="button"
                    className="lp-demo-relation"
                    onClick={() => selectDemoNode(node)}
                    aria-label={`${edge.label} ${node.title} ${direction === "dependency" ? "依赖" : "被依赖"}`}
                  >
                    <span className="lp-demo-relation-kind" style={{ background: RELATION_COLORS[edge.label] ?? "#6f7f95" }}>{edge.label}</span>
                    <span className="lp-demo-relation-title">{node.title}</span>
                    <span className="lp-demo-relation-direction">{direction === "dependency" ? "← 依赖" : "被依赖 →"}</span>
                  </button>
                ))}
              </div>
            </>}
          </>}
          {edgeSelected && <>
            <span className="lp-node-type">逻辑关系</span>
            <h3>{edgeSelected.label}</h3>
            <p>{edgeSelected.description}</p>
            <div className="lp-edge-flow">
              <span>{DEMO_NODES.find((node) => node.id === edgeSelected.from)?.title}</span>
              <ArrowRight size={15} />
              <span>{DEMO_NODES.find((node) => node.id === edgeSelected.to)?.title}</span>
            </div>
          </>}
          <button className="lp-focus-button" onClick={() => networkRef.current?.fit({ animation: true })}><Search size={14} />重新聚焦图谱</button>
        </aside>
      </div>
    </div>
  );
}

function SectionHeading({
  number,
  kicker,
  title,
  lead,
}: {
  number: string;
  kicker: string;
  title: string;
  lead?: string;
}) {
  const titleLines = title.split("\n");

  return (
    <header className="brief-section-heading">
      <div className="brief-heading-overline">
        <div className="brief-heading-meta">
          <span className="brief-section-number">{number}</span>
          <span className="brief-kicker">{kicker}</span>
        </div>
      </div>
      <h2>
        {titleLines.map((line, index) => (
          <span className="brief-title-segment" key={`${line}-${index}`}>
            {line}
          </span>
        ))}
      </h2>
      {lead && <p>{lead}</p>}
    </header>
  );
}

function ProductWindow({
  title,
  meta,
  children,
  className = "",
}: {
  title: string;
  meta?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`brief-window ${className}`}>
      <div className="brief-window-bar">
        <span className="brief-window-dots" aria-hidden="true"><i /><i /><i /></span>
        <strong>{title}</strong>
        {meta && <span>{meta}</span>}
      </div>
      {children}
    </div>
  );
}

function HeroParticles() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let width = 0;
    let height = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const mouse = { x: -9999, y: -9999 };
    type Particle = { x: number; y: number; vx: number; vy: number; glyph?: string };
    let particles: Particle[] = [];
    let frame = 0;
    let visible = true;
    const glyphs = ["∫", "∑", "∂", "∇", "∈", "∀", "≤", "⊂", "∞", "π"];

    const build = () => {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.min(72, Math.round((width * height) / 15000));
      particles = Array.from({ length: count }, (_, index) => ({
        x: ((index * 83) % 97) / 97 * width,
        y: ((index * 47) % 89) / 89 * height,
        vx: ((index % 5) - 2) * 0.07,
        vy: (((index + 2) % 7) - 3) * 0.045,
        glyph: index % 9 === 0 ? glyphs[(index / 9) % glyphs.length | 0] : undefined,
      }));
    };

    const draw = () => {
      frame = requestAnimationFrame(draw);
      if (!visible || document.hidden) return;
      context.clearRect(0, 0, width, height);
      const link = 132;
      particles.forEach((particle, index) => {
        particle.x += particle.vx;
        particle.y += particle.vy;
        if (particle.x < 0 || particle.x > width) particle.vx *= -1;
        if (particle.y < 0 || particle.y > height) particle.vy *= -1;
        const mouseDistance = Math.hypot(particle.x - mouse.x, particle.y - mouse.y);
        if (mouseDistance < 150) {
          particle.x += ((particle.x - mouse.x) / (mouseDistance || 1)) * 0.55;
          particle.y += ((particle.y - mouse.y) / (mouseDistance || 1)) * 0.55;
        }
        for (let other = index + 1; other < particles.length; other += 1) {
          const dx = particle.x - particles[other].x;
          const dy = particle.y - particles[other].y;
          const distance = Math.hypot(dx, dy);
          if (distance < link) {
            context.strokeStyle = `rgba(30,90,168,${(1 - distance / link) * 0.14})`;
            context.beginPath();
            context.moveTo(particle.x, particle.y);
            context.lineTo(particles[other].x, particles[other].y);
            context.stroke();
          }
        }
        if (particle.glyph) {
          context.font = "16px Georgia, serif";
          context.fillStyle = "rgba(176,133,66,.42)";
          context.fillText(particle.glyph, particle.x, particle.y);
        } else {
          context.beginPath();
          context.arc(particle.x, particle.y, 1.6, 0, Math.PI * 2);
          context.fillStyle = "rgba(30,90,168,.3)";
          context.fill();
        }
      });
    };

    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
    });
    observer.observe(canvas);
    const onMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = event.clientX - rect.left;
      mouse.y = event.clientY - rect.top;
    };
    const onLeave = () => {
      mouse.x = -9999;
      mouse.y = -9999;
    };
    build();
    draw();
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    window.addEventListener("resize", build);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("resize", build);
    };
  }, []);
  return <canvas ref={canvasRef} className="brief-hero-particles" aria-hidden="true" />;
}

function HeroSection() {
  return (
    <section className="brief-hero" id="top">
      <HeroParticles />
      <DemoGraph compact />
      <div className="brief-hero-wash" />
      <div className="brief-hero-inner">
        <div className="brief-hero-copy">
          <div className="brief-hero-brand">
            <img src="/mathweaver-icon.png" alt="MathWeaver图标" />
            <div><strong>绎理</strong><span>MathWeaver · SJTU-AI4Math</span></div>
          </div>
          <span className="brief-eyebrow"><Sparkles size={15} /> 面向数学学习的知识图谱平台</span>
          <h1>沿着知识脉络，<br />把数学真正学懂</h1>
          <p>
            从课程图谱出发，理解前置关系、回到教材原文，<br className="brief-hero-copy-break" />
            完成证明与理解考核，让每一次学习都有路径、有反馈、有积累。
          </p>
          <div className="brief-hero-keywords" aria-label="学习能力">
            <div><strong>图谱探索</strong></div>
            <div><strong>学习路径</strong></div>
            <div><strong>AI证明辅导</strong></div>
          </div>
        </div>
      </div>
    </section>
  );
}



function StudioSection() {
  const loop = useVisibleLoop(5, 1900);
  const sequence = [6, 6, 7, 7, 6];
  const autoLayout: GraphLayout = loop.step === 3 ? "dag" : "force";
  const [selectedId, setSelectedId] = useState(6);
  const [layout, setLayout] = useState<GraphLayout>("force");
  const [activeTypes, setActiveTypes] = useState(() => new Set(Object.keys(TYPE_COLORS)));
  useEffect(() => {
    setSelectedId(sequence[loop.step]);
    setLayout(autoLayout);
  }, [autoLayout, loop.step]);
  const visibleIds = useMemo(
    () => DEMO_NODES.filter((node) => activeTypes.has(node.type)).map((node) => node.id),
    [activeTypes],
  );
  const selected = DEMO_NODES.find((node) => node.id === selectedId) ?? DEMO_NODES[5];
  const relations = DEMO_EDGES.flatMap((edge) => {
    if (edge.to === selectedId) {
      const node = DEMO_NODES.find((item) => item.id === edge.from);
      return node ? [{ edge, node, direction: "依赖" }] : [];
    }
    if (edge.from === selectedId) {
      const node = DEMO_NODES.find((item) => item.id === edge.to);
      return node ? [{ edge, node, direction: "被依赖" }] : [];
    }
    return [];
  }).slice(0, 5);

  const chooseLayout = (next: GraphLayout) => {
    setLayout(next);
    loop.interact();
  };
  const chooseNode = (id: number) => {
    setSelectedId(id);
    loop.interact();
  };
  const toggleType = (type: string) => {
    setActiveTypes((current) => {
      const next = new Set(current);
      if (next.has(type) && next.size > 1) next.delete(type);
      else next.add(type);
      return next;
    });
    loop.interact();
  };
  return (
    <section className="brief-section brief-studio-section" id="graph" ref={loop.ref}>
      <SectionHeading
        number="01"
        kicker="图谱探索"
        title="从一张图开始，主动探索知识之间的联系"
        lead="点击节点查看陈述和依赖，切换布局观察不同结构，把教材中的线性顺序变成可以自由探索的知识网络。"
      />
      <div className="brief-studio">
        <div className="brief-studio-topbar">
          <img src="/mathweaver-icon.png" alt="" />
          <strong>高等代数·线性空间</strong>
          <span>课程图谱</span>
          <b>20节点 · 26关系</b>
          <label><Search size={14} /><input value={loop.step === 0 ? "Steinitz交换引理" : ""} readOnly placeholder="搜索概念 /" /></label>
          <div className="brief-layout-switch">
            {([
              ["reading", "阅读顺序"],
              ["swimlane", "类型泳道"],
              ["dag", "依赖层次"],
              ["force", "关系网络"],
            ] as Array<[GraphLayout, string]>).map(([key, label]) => (
              <button className={layout === key ? "active" : ""} onClick={() => chooseLayout(key)} key={key}>{label}</button>
            ))}
          </div>
        </div>
        <div className="brief-studio-body">
          <aside className="brief-studio-rail">
            <span className="brief-panel-label">节点类型</span>
            {Object.entries(TYPE_COLORS).map(([type, color]) => (
              <button className={activeTypes.has(type) ? "" : "off"} onClick={() => toggleType(type)} key={type}>
                <i style={{ background: color.border }} />{type}<small>{DEMO_NODES.filter((node) => node.type === type).length}</small>
              </button>
            ))}
            <span className="brief-panel-label relation">关系图例</span>
            {Object.entries(RELATION_COLORS).map(([label, color]) => (
              <div className="brief-relation-key" key={label}><i style={{ background: color }} />{label}</div>
            ))}
          </aside>
          <div className="brief-studio-canvas">
            <GraphNetwork
              nodeIds={visibleIds}
              focusId={selectedId}
              layout={layout}
              showLabels={layout === "dag"}
              active={loop.active}
              onSelect={chooseNode}
            />
            <span className="brief-canvas-tip">拖动画布 · 滚轮缩放 · 点击节点查看详情</span>
          </div>
          <aside className="brief-studio-detail">
            <span className="brief-node-type" style={{ background: TYPE_COLORS[selected.type]?.border }}>{selected.type}</span>
            <h3>{selected.title}</h3>
            <small>{selected.id === 6 ? "Steinitz exchange lemma" : "Basis extension theorem"}</small>
            <div className="brief-detail-block"><b>陈述</b><p>{selected.statement}</p></div>
            <div className="brief-detail-block"><b>依赖关系（{relations.length}）</b>
              <div className="brief-dependency-list">
                {relations.map(({ edge, node, direction }) => (
                  <button onClick={() => chooseNode(node.id)} key={`${edge.id}-${node.id}`}>
                    <span style={{ background: RELATION_COLORS[edge.label] }}>{edge.label}</span>
                    <strong>{node.title}</strong><small>{direction} →</small>
                  </button>
                ))}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}

const SOURCE_NODES = [
  { id: 1, page: 68, section: "3.1 向量空间" },
  { id: 2, page: 72, section: "3.1 向量空间" },
  { id: 3, page: 76, section: "3.2 线性组合与张成" },
  { id: 4, page: 82, section: "3.2 线性相关与线性无关" },
  { id: 5, page: 91, section: "3.4 基与维数" },
  { id: 6, page: 96, section: "3.4 基与维数" },
  { id: 7, page: 99, section: "3.4 基与维数" },
  { id: 8, page: 102, section: "3.4 基与维数" },
  { id: 9, page: 116, section: "4.1 线性映射" },
  { id: 10, page: 121, section: "4.1 线性映射的核与像" },
].map((source) => ({
  ...DEMO_NODES.find((node) => node.id === source.id)!,
  ...source,
}));

const PROOF_DRAFTS = [
  "设 v₁,…,vₘ 线性无关。因为 m≤n，所以可以添加 n−m 个向量得到一组基。",
  "设 v₁,…,vₘ 线性无关。选择 V 的一组基 B 作为生成组，准备应用 Steinitz 交换引理。",
  "设 v₁,…,vₘ 线性无关。选择 V 的一组基 B 作为生成组。应用 Steinitz 交换引理，用 v₁,…,vₘ 替换 B 中的 m 个向量，得到仍张成 V 的新向量组。还需说明其线性无关性。",
];

const PATH_ITEMS = [
  ["线性无关", "判断起点"],
  ["线性组合与张成", "理解生成能力"],
  ["基与维数", "连接两个条件"],
  ["Steinitz交换引理", "关键证明工具"],
  ["基扩张定理", "学习目标"],
];


function SourceProofSection() {
  const loop = useVisibleLoop(6, 1750);
  const sourceChoices = SOURCE_NODES.filter((node) => [5, 6, 7].includes(node.id));
  const [selectedId, setSelectedId] = useState(6);
  const [action, setAction] = useState<"hint" | "check" | "summary">("hint");
  const [proof, setProof] = useState(PROOF_DRAFTS[0]);
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    if (loop.step === 0) { setSelectedId(6); setAction("hint"); setProof(PROOF_DRAFTS[0]); setHistoryOpen(false); }
    if (loop.step === 1) setAction("hint");
    if (loop.step === 2) setAction("check");
    if (loop.step === 3) setProof(PROOF_DRAFTS[1]);
    if (loop.step === 4) { setAction("summary"); setProof(PROOF_DRAFTS[2]); }
    if (loop.step === 5) { setSelectedId(7); setHistoryOpen(false); }
  }, [loop.step]);

  const selected = SOURCE_NODES.find((node) => node.id === selectedId) ?? SOURCE_NODES[5];
  const feedback = {
    hint: ["下一步提示", "先选择空间的一组已有基，再考虑怎样把给定向量换入这组基。"],
    check: ["发现一处跳步", "m≤n只比较数量，不能保证补充向量存在；这里需要引用 Steinitz 交换引理。"],
    summary: ["思路已经成形", "最后说明新向量组既张成 V 又线性无关，就能完成基扩张定理的证明。"],
  }[action];
  const chooseNode = (id: number) => {
    setSelectedId(id);
    loop.interact();
  };
  const chooseAction = (next: typeof action) => {
    setAction(next);
    loop.interact();
  };

  return (
    <section className="brief-section brief-node-study-section" id="node-study" ref={loop.ref}>
      <SectionHeading
        number="02"
        kicker="节点学习"
        title="回到教材原文，在 AI 提示下完成自己的证明"
        lead="每个知识节点都保留教材出处；阅读原文后可以直接写下证明，由 AI 提示下一步、检查跳步并保存修改历史。"
      />
      <ProductWindow title="高等代数 · 节点学习" meta="原文与证明同步保存" className={`brief-study-window ${historyOpen ? "history-open" : ""}`}>
        <div className="brief-study-syncbar">
          <span><NetworkIcon size={14} /> 当前学习节点</span>
          {sourceChoices.map((node) => (
            <button type="button" className={node.id === selectedId ? "active" : ""} onClick={() => chooseNode(node.id)} key={node.id}>{node.title}</button>
          ))}
          <b>第 {selected.page} 页</b>
        </div>
        <div className="brief-study-grid">
          <article className="brief-study-source">
            <div className="brief-source-panel-head"><span><BookOpen size={13} /> 教材原文</span><strong>{selected.title}</strong><small>{selected.section}</small></div>
            <div className="brief-paper">
              <div><span>高等代数</span><b>{selected.page}</b></div>
              <h4>{selected.title}</h4>
              <p>在向量空间、张成与线性无关定义的基础上，我们得到如下结论。</p>
              <p className="hit">{selected.statement}</p>
              <p>该结论连接了前置定义与后续证明，可以随时返回图谱查看依赖关系。</p>
            </div>
          </article>
          <div className="brief-proof-main">
            <div className="brief-proof-context">
              <span>当前命题</span>
              <strong>线性无关向量组可以扩张为一组基</strong>
              <p>设 V 为 n 维向量空间，v₁,…,vₘ 线性无关。证明它们可以扩张为 V 的一组基。</p>
            </div>
            <label className="brief-proof-editor">
              <span>我的证明 <small>已自动保存</small></span>
              <textarea aria-label="我的证明" value={proof} onChange={(event) => { setProof(event.target.value); loop.interact(); }} />
            </label>
            <div className="brief-proof-version"><span>草稿版本 v3</span><button type="button" onClick={() => { setHistoryOpen(!historyOpen); loop.interact(); }}><History size={14} /> 历史版本</button></div>
          </div>
          <aside className="brief-ai-panel">
            <span className="brief-panel-label"><Sparkles size={14} /> AI 证明辅导</span>
            <div className="brief-proof-actions">
              <button type="button" className={action === "hint" ? "active" : ""} onClick={() => chooseAction("hint")}><Lightbulb size={14} /> 提示</button>
              <button type="button" className={action === "check" ? "active" : ""} onClick={() => chooseAction("check")}><CheckCircle2 size={14} /> 检查</button>
              <button type="button" className={action === "summary" ? "active" : ""} onClick={() => chooseAction("summary")}><ListChecks size={14} /> 总结</button>
            </div>
            <div className={`brief-ai-response ${action}`}><strong>{feedback[0]}</strong><p>{feedback[1]}</p></div>
            <div className="brief-ai-progress">
              <span><i className="done" /> 已识别证明目标</span>
              <span><i className="done" /> 已选择关键引理</span>
              <span><i className={loop.step >= 4 ? "done" : ""} /> 补充线性无关性论证</span>
            </div>
            <small className="brief-proof-disclaimer">AI 提供学习提示，证明仍由你完成</small>
          </aside>
        </div>
        <div className="brief-history-drawer">
          <div><strong>版本历史</strong><button type="button" onClick={() => setHistoryOpen(false)}>×</button></div>
          {["初稿 · 存在跳步", "AI 提示 · 引入交换引理", "第二稿 · 完成替换", "当前稿 · 补充结论"].map((item, index) => (
            <button type="button" key={item} onClick={() => { setProof(PROOF_DRAFTS[Math.min(index, 2)]); loop.interact(); }}><i>{index + 1}</i><span>{item}<small>{index === 3 ? "刚刚" : `${8 - index * 2} 分钟前`}</small></span></button>
          ))}
        </div>
      </ProductWindow>
    </section>
  );
}

const STUDENT_PATH_EXPLANATIONS = [
  "先确认给定向量组为什么线性无关，这是证明的起点。",
  "理解张成，才能判断替换后的向量组是否仍覆盖整个空间。",
  "基把线性无关与张成连接起来，是本次学习路径的中间桥梁。",
  "交换引理提供了把目标向量换入已有基的合法步骤。",
  "完成前置节点后，就可以回到目标节点组织完整证明。",
];

const STUDENT_ASSESSMENT_QUESTIONS = [
  ["条件变化", "如果生成组 S′ 只张成子空间 W，原结论应怎样修改？"],
  ["数值理解", "若 dim V=8，已有 3 个线性无关向量，至少还需添加几个向量？"],
  ["证明细节", "为什么不能仅由 m≤n 推出补充向量一定存在？"],
  ["概念辨析", "张成 V 与线性无关在“构成一组基”中分别起什么作用？"],
];

function LearningWorkspaceSection() {
  const loop = useVisibleLoop(7, 1700);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [ocrReady, setOcrReady] = useState(false);
  useEffect(() => {
    setSelectedIndex(Math.min(4, loop.step));
    setQuestionIndex(loop.step % STUDENT_ASSESSMENT_QUESTIONS.length);
    setOcrReady(loop.step >= 5);
  }, [loop.step]);
  const selectedId = PATH_NODE_IDS[selectedIndex];
  const answer = questionIndex === 1
    ? "还需要添加 5 个向量；但这些向量不能任意选择，还要保持线性无关。"
    : "结论应限制在 W 中，并先确认原来的线性无关向量都属于 W。";

  return (
    <section className="brief-section brief-learning-space-section" id="learning-space" ref={loop.ref}>
      <SectionHeading
        number="03"
        kicker="学习空间"
        title="按前置关系学习，并用节点考核确认理解"
        lead="课程任务把目标拆成可以逐项完成的学习路径；每学完一个节点，再用教师审核过的理解考核确认自己是否真正掌握。"
      />
      <ProductWindow title="基扩张定理 · 我的学习任务" meta="学习进度 3 / 5" className="brief-learning-window">
        <div className="brief-learning-demo-grid">
          <div className="brief-learning-route-panel">
            <div className="brief-learning-summary-row"><span><GraduationCap size={15} /> 学习步骤</span><b>{selectedIndex + 1} / {PATH_ITEMS.length}</b></div>
            <div className="brief-learning-progress"><i style={{ width: `${((selectedIndex + 1) / PATH_ITEMS.length) * 100}%` }} /></div>
            <div className="brief-learning-route-body">
              <div className="brief-learning-graph">
                <GraphNetwork nodeIds={PATH_NODE_IDS} focusId={selectedId} layout="dag" active={loop.active} onSelect={(id) => {
                  const nextIndex = PATH_NODE_IDS.indexOf(id);
                  if (nextIndex >= 0) setSelectedIndex(nextIndex);
                  loop.interact();
                }} />
              </div>
              <div className="brief-path-list">
                {PATH_ITEMS.map(([title, role], index) => (
                  <button type="button" className={`${selectedIndex === index ? "active" : ""} ${index < selectedIndex ? "done" : ""}`} onClick={() => { setSelectedIndex(index); loop.interact(); }} key={title}>
                    <i>{index < selectedIndex ? "✓" : index + 1}</i><span><strong>{title}</strong><small>{role}</small></span>
                  </button>
                ))}
              </div>
            </div>
            <div className="brief-learning-rationale"><CircleDot size={15} /><div><strong>为什么需要这个节点？</strong><p>{STUDENT_PATH_EXPLANATIONS[selectedIndex]}</p></div></div>
          </div>
          <div className="brief-learning-assessment">
            <div className="brief-assessment-head"><span><BrainCircuit size={15} /> 节点理解考核</span><b><CheckCircle2 size={13} /> 答案已自动保存</b></div>
            <div className="brief-assessment-progress-row"><span>第 {questionIndex + 1} / {STUDENT_ASSESSMENT_QUESTIONS.length} 题</span><i><em style={{ width: `${((questionIndex + 1) / STUDENT_ASSESSMENT_QUESTIONS.length) * 100}%` }} /></i></div>
            <div className="brief-exam-tabs">
              {STUDENT_ASSESSMENT_QUESTIONS.map(([label], index) => (
                <button type="button" className={questionIndex === index ? "active" : ""} onClick={() => { setQuestionIndex(index); loop.interact(); }} key={label}>{label}</button>
              ))}
            </div>
            <span className="brief-question-label">衍生问题 · {STUDENT_ASSESSMENT_QUESTIONS[questionIndex][0]}</span>
            <h3>{STUDENT_ASSESSMENT_QUESTIONS[questionIndex][1]}</h3>
            <label className="brief-answer-box"><span>我的作答</span><textarea aria-label="理解考核作答" value={answer} readOnly /></label>
            <div className="brief-answer-tools">
              <button type="button"><FileText size={13} /> 公式与文本</button>
              <button type="button" className={ocrReady ? "active" : ""} onClick={() => { setOcrReady(true); loop.interact(); }}><ScanText size={13} /> PDF / 图片手稿 OCR</button>
            </div>
            {ocrReady && <div className="brief-ocr-note"><CheckCircle2 size={14} /><span><strong>手稿已识别</strong><small>识别结果可继续编辑后提交</small></span></div>}
            <div className="brief-followup visible"><Sparkles size={15} /><div><strong>继续想一想</strong><p>除了得到数量，还需要说明怎样选择这些向量才不会破坏线性无关性。</p></div></div>
          </div>
        </div>
      </ProductWindow>
    </section>
  );
}

const STUDENT_LEARNING_CONTEXT = [
  ["current", "当前目标", "完成基扩张定理的完整证明"],
  ["mastered", "已掌握", "能够区分线性无关与张成"],
  ["gap", "还需解决", "说明替换后为什么仍线性无关"],
  ["review", "相关知识复习", "基与维数 · Steinitz 交换引理"],
  ["next", "下一步建议", "结合向量个数与张成性完成结论"],
];

const STUDENT_GRADE_FEEDBACK = [
  ["线性无关", "掌握稳定", "定义使用准确，能够说明零线性组合的系数必须全为 0。", 92],
  ["张成", "需要巩固", "已经识别张成目标，还需明确为什么所选向量覆盖整个空间。", 74],
  ["Steinitz 交换引理", "完成修正", "根据反馈补充了交换引理的适用条件和替换对象。", 86],
  ["基扩张定理", "证明完整", "关键引理、替换过程和最终结论已经连成完整论证。", 84],
] as const;

function LearningProgressSection() {
  const loop = useVisibleLoop(STUDENT_GRADE_FEEDBACK.length, 1800);
  const [selected, setSelected] = useState(0);
  useEffect(() => setSelected(loop.step), [loop.step]);
  const feedback = STUDENT_GRADE_FEEDBACK[selected];

  return (
    <section className="brief-section brief-learning-space-section" id="progress" ref={loop.ref}>
      <SectionHeading
        number="04"
        kicker="学习空间"
        title="每次学习都有记录，反馈会沉淀为下一步"
        lead="重新进入课程时，平台会恢复当前目标和未解决问题；提交任务后，可以查看教师审核过的逐题反馈与知识节点得分。"
      />
      <ProductWindow title="高等代数 · 我的学习情况" meta="最近更新：刚刚" className="brief-progress-window">
        <div className="brief-progress-grid">
          <div className="brief-learning-context-panel">
            <div className="brief-progress-panel-head"><BrainCircuit size={16} /><span><strong>连续学习记录</strong><small>从上次进度继续</small></span><b>已恢复</b></div>
            <div className="brief-learning-context-list">
              {STUDENT_LEARNING_CONTEXT.map(([kind, title, text]) => (
                <div className={kind} key={kind}><i>{kind === "mastered" ? <CheckCircle2 size={14} /> : <CircleDot size={14} />}</i><span><b>{title}</b><p>{text}</p></span></div>
              ))}
            </div>
            <p className="brief-context-note">学习记录来自你的作答、证明草稿和已处理反馈；相关知识提醒不会直接被当作错误结论。</p>
          </div>
          <div className="brief-student-report">
            <div className="brief-student-report-head">
              <div><span>教师已发布</span><strong>作业成绩报告</strong><small>逐题反馈 · 节点掌握情况</small></div>
              <div><b>84</b><span>/ 100</span></div>
            </div>
            <div className="brief-report-node-list">
              {STUDENT_GRADE_FEEDBACK.map(([title, state, , value], index) => (
                <button type="button" className={selected === index ? "active" : ""} onClick={() => { setSelected(index); loop.interact(); }} key={title}>
                  <span><strong>{title}</strong><small>{state}</small></span><i><em style={{ width: `${value}%` }} /></i><b>{value}%</b>
                </button>
              ))}
            </div>
            <div className="brief-student-feedback"><span>教师审核后的反馈</span><strong>{feedback[0]} · {feedback[1]}</strong><p>{feedback[2]}</p><small>你可以从该节点重新进入原文、证明草稿或理解考核。</small></div>
          </div>
        </div>
      </ProductWindow>
    </section>
  );
}

function BriefFooter() {
  return (
    <footer className="brief-footer">
      <div className="brief-footer-inner">
        <div className="brief-footer-top">
          <div className="brief-footer-brand">
            <strong>绎理 · MathWeaver</strong>
            <span>面向学生的数学知识图谱学习平台</span>
          </div>
          <nav className="brief-footer-nav" aria-label="页脚导航">
            <a href="#top">返回顶部</a>
            <a href="#graph">图谱探索</a>
            <a href="#node-study">节点学习</a>
            <a href="#learning-space">学习空间</a>
          </nav>
        </div>
        <div className="brief-footer-divider" />
        <div className="brief-footer-bottom">
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">粤ICP备2026074225号-3</a>
        </div>
      </div>
    </footer>
  );
}

export default function Landing() {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    const update = () => {
      const maximum = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(maximum > 0 ? Math.min(100, Math.max(0, window.scrollY / maximum * 100)) : 0);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  return (
    <main className="brief-root">
      <nav className="brief-nav">
        <a href="#top" className="brief-brand">
          <img src="/mathweaver-icon.png" alt="" />
          <span><strong>绎理</strong><b>MathWeaver</b><small>SJTU-AI4Math</small></span>
        </a>
        <div className="brief-nav-links">
          <a href="#graph">图谱探索</a>
          <a href="#node-study">节点学习</a>
          <a href="#learning-space">学习空间</a>
        </div>
        <Link to="/workspace?edu=hub" className="brief-nav-cta">进入学习空间 <ArrowRight size={15} /></Link>
        <div className="brief-reading-progress"><i style={{ width: `${progress}%` }} /></div>
      </nav>

      <HeroSection />
      <StudioSection />
      <SourceProofSection />
      <LearningWorkspaceSection />
      <LearningProgressSection />
      <BriefFooter />
    </main>
  );
}
