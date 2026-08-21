import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Network, type Edge, type Node, type Options } from "vis-network";
import {
  ArrowLeft, ArrowRight, BookOpen, CheckCircle2, ChevronDown, CircleDot,
  FileCode2, FileText, GitBranch, Lightbulb, ListChecks,
  MessageSquareText, Network as NetworkIcon, Play, ScanText,
  Search, Sparkles, Upload,
} from "lucide-react";
import "./landing.css";

export function meta() {
  return [
    { title: "MathWeaver 绎理 | 从数学文档到知识图谱" },
    { name: "description", content: "MathWeaver（绎理）从数学文档中抽取数学实体、识别逻辑依赖关系，并构建可交互的数学知识图谱。" },
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

const DEMO_NODES: DemoNode[] = [
  { id: 1, type: "定义", title: "向量空间", statement: "在同一数域上配备向量加法与数乘，并满足八条基本运算公理的集合。" },
  { id: 2, type: "定义", title: "子空间", statement: "向量空间的非空子集若对向量加法与数乘封闭，则称为该空间的子空间。" },
  { id: 3, type: "定义", title: "线性组合与张成", statement: "向量组的线性组合是其有限数乘之和；所有线性组合构成该向量组的张成空间。" },
  { id: 4, type: "定义", title: "线性相关与无关", statement: "仅当系数全为零时线性组合才等于零的向量组称为线性无关组。" },
  { id: 5, type: "定义", title: "基与维数", statement: "既线性无关又张成整个空间的向量组称为基；有限基所含向量个数称为维数。" },
  { id: 6, type: "引理", title: "Steinitz 交换引理", statement: "有限生成空间中的任一线性无关组，都可用来替换某些生成向量而仍保持张成。" },
  { id: 7, type: "定理", title: "基扩张定理", statement: "有限维向量空间中的任一线性无关组都可以扩张为一组基。" },
  { id: 8, type: "推论", title: "满维无关组构成基", statement: "n 维向量空间中任意含 n 个向量的线性无关组都是一组基。" },
  { id: 9, type: "定义", title: "线性映射", statement: "保持向量加法与数乘运算的映射称为线性映射。" },
  { id: 10, type: "定义", title: "核与像", statement: "线性映射把零向量作为像的向量组成核；所有输出向量组成像。" },
  { id: 11, type: "定理", title: "秩-零化度定理", statement: "若 V 为有限维空间且 T: V → W 线性，则 dim V = dim ker T + dim im T。" },
  { id: 12, type: "命题", title: "单射与核的判别", statement: "线性映射 T 是单射，当且仅当 ker T 只含零向量。" },
  { id: 13, type: "定义", title: "线性映射的矩阵", statement: "选定定义域与值域的基后，线性映射由作用在基向量上的坐标列唯一表示。" },
  { id: 14, type: "定义", title: "矩阵的秩", statement: "矩阵列空间的维数称为矩阵的秩，并等于对应线性映射像空间的维数。" },
  { id: 15, type: "引理", title: "初等变换保持秩", statement: "对矩阵实施任意一次初等行变换或初等列变换都不改变矩阵的秩。" },
  { id: 16, type: "命题", title: "可逆矩阵的等价条件", statement: "n 阶矩阵可逆，当且仅当其秩为 n；这也等价于对应线性映射为双射。" },
  { id: 17, type: "定义", title: "特征值与特征向量", statement: "若存在非零向量 v 使 Av = λv，则 λ 是 A 的特征值，v 是对应特征向量。" },
  { id: 18, type: "定义", title: "特征多项式", statement: "多项式 det(λI - A) 称为矩阵 A 的特征多项式，其根是 A 的特征值。" },
  { id: 19, type: "定理", title: "不同特征值对应向量无关", statement: "属于两两不同特征值的非零特征向量必定线性无关。" },
  { id: 20, type: "推论", title: "特征值互异时可对角化", statement: "n 阶矩阵若有 n 个互不相同的特征值，则它相似于一个对角矩阵。" },
];

const DEMO_EDGES: DemoEdge[] = [
  { id: 1, from: 1, to: 2, label: "定义依赖", description: "子空间沿用向量空间中的加法、数乘及其运算公理。" },
  { id: 2, from: 1, to: 3, label: "类型支撑", description: "线性组合的运算发生在给定数域上的向量空间中。" },
  { id: 3, from: 3, to: 4, label: "定义依赖", description: "线性相关性通过等于零向量的线性组合来刻画。" },
  { id: 4, from: 3, to: 5, label: "定义依赖", description: "一组基首先需要张成整个向量空间。" },
  { id: 5, from: 4, to: 5, label: "定义依赖", description: "一组基还必须满足线性无关性。" },
  { id: 6, from: 3, to: 6, label: "条件支撑", description: "交换引理比较线性无关组与有限生成组的张成能力。" },
  { id: 7, from: 4, to: 6, label: "条件支撑", description: "交换引理以待交换向量组线性无关为前提。" },
  { id: 8, from: 6, to: 7, label: "证明支撑", description: "反复应用交换引理，可以把线性无关组补充成一组基。" },
  { id: 9, from: 5, to: 8, label: "类型支撑", description: "该结论比较无关向量个数与空间基的向量个数。" },
  { id: 10, from: 7, to: 8, label: "直接推论", description: "满维无关组无需再添加向量，其基扩张只能是自身。" },
  { id: 11, from: 1, to: 9, label: "定义依赖", description: "线性映射保持向量空间中的加法与数乘结构。" },
  { id: 12, from: 9, to: 10, label: "定义依赖", description: "核与像都由给定线性映射的输入和输出确定。" },
  { id: 13, from: 5, to: 11, label: "条件支撑", description: "秩-零化度定理使用有限维空间及子空间的维数。" },
  { id: 14, from: 10, to: 11, label: "逻辑依赖", description: "定理把定义域维数分解为核维数与像维数之和。" },
  { id: 15, from: 10, to: 12, label: "逻辑依赖", description: "核中是否存在非零向量，恰好决定线性映射能否保持输入的唯一性。" },
  { id: 16, from: 5, to: 13, label: "类型支撑", description: "线性映射的矩阵表示需要先为定义域和值域选定基。" },
  { id: 17, from: 9, to: 13, label: "定义依赖", description: "矩阵的各列记录线性映射作用在定义域基向量上的坐标。" },
  { id: 18, from: 13, to: 14, label: "定义依赖", description: "矩阵的列空间对应于线性映射在所选基下的像空间。" },
  { id: 19, from: 14, to: 15, label: "证明支撑", description: "初等变换保持行空间或列空间的维数，因此不改变秩。" },
  { id: 20, from: 14, to: 16, label: "条件支撑", description: "满秩是方阵可逆的一个核心等价条件。" },
  { id: 21, from: 13, to: 17, label: "类型支撑", description: "特征值方程研究线性变换在同一组基下的方阵表示。" },
  { id: 22, from: 17, to: 18, label: "定义依赖", description: "特征多项式的根刻画特征值方程存在非零解的参数。" },
  { id: 23, from: 4, to: 19, label: "证明支撑", description: "定理的结论与证明均建立在线性无关性的定义之上。" },
  { id: 24, from: 17, to: 19, label: "条件支撑", description: "两两不同的特征值是对应特征向量线性无关的关键条件。" },
  { id: 25, from: 5, to: 20, label: "类型支撑", description: "n 个线性无关特征向量在 n 维空间中构成一组基。" },
  { id: 26, from: 19, to: 20, label: "直接推论", description: "n 个互异特征值给出 n 个线性无关特征向量，从而形成特征向量基。" },
];

const TYPE_COLORS: Record<string, { border: string; bg: string }> = {
  "定义": { border: "#7d756a", bg: "#f7f5f1" },
  "引理": { border: "#1e5aa8", bg: "#eaf1fa" },
  "定理": { border: "#2f7d56", bg: "#ecf6f0" },
  "推论": { border: "#7655a6", bg: "#f4eff9" },
  "命题": { border: "#b08542", bg: "#f8efdf" },
};

const RELATION_COLORS: Record<string, string> = {
  "定义依赖": "#6f7f95",
  "逻辑依赖": "#4f78aa",
  "证明支撑": "#3f8064",
  "条件支撑": "#a57431",
  "直接推论": "#7655a6",
  "类型支撑": "#77706a",
};

const STAGES = [
  ["文档理解", "校正文档结构，识别数学段落与命题边界"],
  ["实体抽取", "抽取定义、定理、引理、推论等数学节点"],
  ["逻辑分析", "解析条件、结论、证明和语义角色"],
  ["关系识别", "建立定义依赖、证明支撑和逻辑依赖关系"],
  ["质量检查", "检查中间产物并修复缺失或异常结果"],
  ["图谱构建", "生成可筛选、可追踪、可导出的知识图谱"],
];

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

const PARTICLE_GLYPHS = ["∫", "∑", "∂", "∇", "∈", "∀", "≤", "⊂", "∞", "π"];

function HeroParticles() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    type P = { x: number; y: number; vx: number; vy: number; glyph?: string };
    let pts: P[] = [];
    const mouse = { x: -9999, y: -9999 };

    const build = () => {
      const rect = canvas.getBoundingClientRect();
      w = rect.width; h = rect.height;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.min(78, Math.round((w * h) / 14000));
      pts = Array.from({ length: count }, (_, i) => ({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.28, vy: (Math.random() - 0.5) * 0.28,
        glyph: i % 9 === 0 ? PARTICLE_GLYPHS[(i / 9) % PARTICLE_GLYPHS.length | 0] : undefined,
      }));
    };
    build();

    let raf = 0;
    const tick = () => {
      ctx.clearRect(0, 0, w, h);
      const LINK = 132;
      for (const p of pts) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
        const mdx = p.x - mouse.x, mdy = p.y - mouse.y;
        const md = Math.hypot(mdx, mdy);
        if (md < 150) { p.x += (mdx / (md || 1)) * 0.6; p.y += (mdy / (md || 1)) * 0.6; }
      }
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
          const d = Math.hypot(dx, dy);
          if (d < LINK) {
            ctx.strokeStyle = `rgba(30,90,168,${(1 - d / LINK) * 0.16})`;
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
          }
        }
        const near = Math.hypot(pts[i].x - mouse.x, pts[i].y - mouse.y) < 150;
        const glyph = pts[i].glyph;
        if (glyph) {
          ctx.font = "16px 'Source Serif 4', Georgia, serif";
          ctx.fillStyle = near ? "rgba(176,133,66,.7)" : "rgba(176,133,66,.42)";
          ctx.fillText(glyph, pts[i].x, pts[i].y);
        } else {
          ctx.beginPath();
          ctx.arc(pts[i].x, pts[i].y, near ? 2.4 : 1.7, 0, Math.PI * 2);
          ctx.fillStyle = near ? "rgba(30,90,168,.6)" : "rgba(30,90,168,.32)";
          ctx.fill();
        }
      }
      raf = requestAnimationFrame(tick);
    };
    tick();

    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left; mouse.y = e.clientY - rect.top;
    };
    const onLeave = () => { mouse.x = -9999; mouse.y = -9999; };
    const onResize = () => build();
    window.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("resize", onResize);
    };
  }, []);
  return <canvas ref={canvasRef} className="lp-hero-particles" aria-hidden="true" />;
}

type LearningNode = {
  id: string;
  type: string;
  title: string;
  section: string;
  statement: string;
  x: number;
  y: number;
  source: {
    document: string;
    chapter: string;
    page: number;
    lead: string;
    tail: string;
  };
};

type LearningEdge = {
  id: string;
  from: string;
  to: string;
  path: string;
  relation: string;
  color: string;
};

type LearningRelation = {
  edge: LearningEdge;
  node: LearningNode;
  direction: "dependency" | "dependent";
};

const LEARNING_NODES: LearningNode[] = [
  {
    id: "poincare",
    type: "定理",
    title: "Poincaré 不等式",
    section: "定理 3.2 · Poincaré 不等式",
    statement: "设 Ω 为有界区域。对任意零边界函数 u，存在常数 C，使得 ‖u‖ ≤ C‖∇u‖。",
    x: 17,
    y: 22,
    source: {
      document: "椭圆偏微分方程导论",
      chapter: "第 3 章 · Sobolev 空间",
      page: 31,
      lead: "在零边界条件下，函数本身的大小可以由梯度控制。",
      tail: "这一估计将在椭圆方程的先验估计中反复使用。",
    },
  },
  {
    id: "energy",
    type: "命题",
    title: "弱解的能量估计",
    section: "命题 4.1 · 先验能量估计",
    statement: "取测试函数 v = u，并结合 Poincaré 不等式，可由数据项控制弱解的能量范数。",
    x: 35,
    y: 51,
    source: {
      document: "椭圆偏微分方程导论",
      chapter: "第 4 章 · 椭圆方程的弱解",
      page: 42,
      lead: "在前述函数空间与弱导数定义的基础上，我们考虑如下结论。",
      tail: "该结论将在后续存在性与正则性分析中作为关键依赖使用。",
    },
  },
  {
    id: "existence",
    type: "推论",
    title: "弱解存在唯一性",
    section: "推论 4.3 · 弱解存在唯一性",
    statement: "双线性型满足连续性与强制性，因此由 Lax–Milgram 定理可得弱解存在且唯一。",
    x: 17,
    y: 80,
    source: {
      document: "椭圆偏微分方程导论",
      chapter: "第 4 章 · 椭圆方程的弱解",
      page: 47,
      lead: "结合前面的能量估计，可以验证双线性型的连续性与强制性。",
      tail: "由此得到弱解问题适定性的基本结论。",
    },
  },
];

const LEARNING_EDGES: LearningEdge[] = [
  { id: "poincare-energy", from: "poincare", to: "energy", path: "M 20 25 C 24 31 29 39 33 46", relation: "证明支撑", color: "#3478bd" },
  { id: "energy-existence", from: "energy", to: "existence", path: "M 33 56 C 29 63 24 71 20 76", relation: "逻辑依赖", color: "#8a67b8" },
];

type ProofAssistAction = "hint" | "check" | "summarize";

const PROOF_ASSIST: Record<ProofAssistAction, { label: string; response: string }> = {
  hint: {
    label: "提示",
    response: "先说明双线性型的强制性。可以尝试用 Poincaré 不等式，把 ‖u‖ 控制到 ‖∇u‖ 上。",
  },
  check: {
    label: "检查",
    response: "当前思路已经找到关键估计，但还需要明确测试函数的取法，并写出常数与边界条件如何进入不等式。",
  },
  summarize: {
    label: "总结",
    response: "证明路线是：选择测试函数，建立能量估计，验证连续性与强制性，最后应用 Lax–Milgram 定理。",
  },
};

function LearningShowcase() {
  const [selectedId, setSelectedId] = useState("energy");
  const [viewMode, setViewMode] = useState<"node" | "source">("node");
  const [proof, setProof] = useState("取测试函数 v = u。由弱形式得到能量项，接下来需要控制右端的数据项。");
  const [assistAction, setAssistAction] = useState<ProofAssistAction>("hint");
  const popoverRef = useRef<HTMLElement>(null);
  const selectedNode = LEARNING_NODES.find((node) => node.id === selectedId) ?? LEARNING_NODES[1];
  const assist = PROOF_ASSIST[assistAction];
  const selectedRelations = LEARNING_EDGES.flatMap<LearningRelation>((edge) => {
    if (edge.to === selectedId) {
      const node = LEARNING_NODES.find((item) => item.id === edge.from);
      return node ? [{ edge, node, direction: "dependency" }] : [];
    }
    if (edge.from === selectedId) {
      const node = LEARNING_NODES.find((item) => item.id === edge.to);
      return node ? [{ edge, node, direction: "dependent" }] : [];
    }
    return [];
  });
  const isRelated = (id: string) => LEARNING_EDGES.some((edge) => (
    (edge.from === selectedId && edge.to === id) || (edge.to === selectedId && edge.from === id)
  ));

  const selectNode = (id: string) => {
    setSelectedId(id);
    setViewMode("node");
    window.requestAnimationFrame(() => popoverRef.current?.focus());
  };

  const toggleSource = () => {
    setViewMode((mode) => mode === "node" ? "source" : "node");
    window.requestAnimationFrame(() => popoverRef.current?.focus());
  };

  return (
    <div className="lp-learning-grid">
      <article className="lp-learning-tool">
        <div className="lp-tool-head">
          <div><BookOpen size={18} /><span>图谱与原文联动</span></div>
          <small>交互预览</small>
        </div>
        <div className="lp-source-demo">
          <div className="lp-study-graph" aria-label="知识图谱与原文定位示例">
            <div className="lp-study-canvas-label"><NetworkIcon size={14} /><span>知识依赖</span></div>
            <svg className="lp-study-links lp-study-links-desktop" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <marker id="lp-study-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" /></marker>
                <marker id="lp-study-arrow-active" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" /></marker>
              </defs>
              {LEARNING_EDGES.map((edge) => (
                <path key={edge.id} className={edge.from === selectedId || edge.to === selectedId ? "active" : ""} d={edge.path} />
              ))}
            </svg>
            <svg className="lp-study-links lp-study-links-mobile" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <marker id="lp-study-arrow-mobile" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" /></marker>
                <marker id="lp-study-arrow-mobile-active" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" /></marker>
              </defs>
              <path className={selectedId === "poincare" || selectedId === "energy" ? "active" : ""} d="M 29 29 C 42 31 57 39 67 47" />
              <path className={selectedId === "energy" || selectedId === "existence" ? "active" : ""} d="M 67 56 C 57 65 44 75 32 82" />
            </svg>
            {LEARNING_NODES.map((node) => (
              <button
                key={node.id}
                data-node={node.id}
                className={`lp-study-node ${selectedId === node.id ? "active" : ""} ${isRelated(node.id) ? "related" : ""}`}
                style={{ left: `${node.x}%`, top: `${node.y}%` }}
                onClick={() => selectNode(node.id)}
                aria-pressed={selectedId === node.id}
              >
                <span>{node.type}</span>
                <strong>{node.title}</strong>
              </button>
            ))}
            <section
              ref={popoverRef}
              className={`lp-link-popover ${viewMode}`}
              tabIndex={-1}
              aria-live="polite"
              aria-label={viewMode === "node" ? `${selectedNode.title}节点详情` : `${selectedNode.title}原文定位`}
            >
              {viewMode === "node" ? (
                <>
                  <div className="lp-link-popover-head">
                    <div><span>{selectedNode.type}</span><strong>{selectedNode.title}</strong></div>
                    <small>原文已定位</small>
                  </div>
                  <button className="lp-link-toggle lp-node-jump" onClick={toggleSource}><BookOpen size={14} />跳转到原文</button>
                  <div className="lp-node-statement">
                    <span>陈述</span>
                    <p>{selectedNode.statement}</p>
                  </div>
                  <div className="lp-node-separator" />
                  <div className="lp-node-relations">
                    <span>依赖关系（{selectedRelations.length}）</span>
                    {selectedRelations.map(({ edge, node, direction }) => (
                      <button key={edge.id} type="button" className="lp-relation-row" onClick={() => selectNode(node.id)}>
                        <span className="lp-relation-kind" style={{ background: edge.color }}>{edge.relation}</span>
                        <span className="lp-relation-title">{node.title}</span>
                        <span className="lp-relation-dir">{direction === "dependency" ? "← 依赖" : "被依赖 →"}</span>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <div className="lp-link-popover-head lp-source-head">
                    <div><span>原文定位</span><strong>{selectedNode.source.document}</strong></div>
                    <small>第 {selectedNode.source.page} 页</small>
                  </div>
                  <div className="lp-source-paper">
                    <div className="lp-paper-meta"><span>{selectedNode.source.chapter}</span><span>{selectedNode.source.page}</span></div>
                    <h4>{selectedNode.section}</h4>
                    <p>{selectedNode.source.lead}</p>
                    <p className="lp-source-hit">{selectedNode.statement}</p>
                    <p>{selectedNode.source.tail}</p>
                  </div>
                  <button className="lp-link-toggle lp-link-back" onClick={toggleSource}><ArrowLeft size={14} />返回节点</button>
                </>
              )}
            </section>
          </div>
        </div>
      </article>

      <article className="lp-learning-tool lp-proof-tool">
        <div className="lp-tool-head">
          <div><MessageSquareText size={18} /><span>证明学习工作区</span></div>
          <small>交互预览</small>
        </div>
        <div className="lp-proof-context">
          <span>当前命题</span>
          <strong>弱解存在唯一性</strong>
          <p>验证双线性型的连续性与强制性，并说明如何推出唯一弱解。</p>
        </div>
        <label className="lp-proof-editor">
          <span>我的证明</span>
          <textarea value={proof} onChange={(event) => setProof(event.target.value)} />
        </label>
        <div className="lp-proof-actions" aria-label="AI 证明辅助示例">
          <button className={assistAction === "hint" ? "active" : ""} onClick={() => setAssistAction("hint")} aria-pressed={assistAction === "hint"}><Lightbulb size={14} />提示</button>
          <button className={assistAction === "check" ? "active" : ""} onClick={() => setAssistAction("check")} aria-pressed={assistAction === "check"}><CheckCircle2 size={14} />检查</button>
          <button className={assistAction === "summarize" ? "active" : ""} onClick={() => setAssistAction("summarize")} aria-pressed={assistAction === "summarize"}><ListChecks size={14} />总结</button>
        </div>
        <div className="lp-ai-response" aria-live="polite">
          <div><Sparkles size={14} /><strong>AI {assist.label}</strong></div>
          <p>{assist.response}</p>
        </div>
      </article>
    </div>
  );
}

export default function Landing() {
  return (
    <main className="lp-root">
      <nav className="lp-nav">
        <a href="#top" className="lp-brand">
          <img src="/mathweaver-icon.png" alt="" aria-hidden="true" />
          <span className="lp-brand-copy"><strong>绎理</strong><span className="lp-brand-affiliation">SJTU-AI4Math</span></span>
        </a>
        <div className="lp-nav-links">
          <a href="#technology">技术路线</a>
          <a href="#inputs">输入处理</a>
          <a href="#demo">图谱 Demo</a>
          <a href="#learning">辅助学习</a>
          <Link to="/workspace?guide=api-setup&step=intro">使用指南</Link>
        </div>
        <Link to="/workspace" className="lp-nav-cta">立即体验 <ArrowRight size={15} /></Link>
      </nav>

      <section className="lp-hero" id="top">
        <HeroParticles />
        <DemoGraph compact />
        <div className="lp-hero-wash" />
        <div className="lp-hero-copy">
          <span className="lp-eyebrow"><Sparkles size={14} /> 面向数学文献的结构化理解系统</span>
          <div className="lp-hero-lockup">
            <img src="/mathweaver-icon.png" alt="" aria-hidden="true" />
            <div className="lp-hero-name">
              <h1>绎理</h1>
              <span>MathWeaver</span>
            </div>
          </div>
          <p>从数学文档中抽取实体、识别逻辑依赖，并构建可追踪、可探索的数学知识图谱。</p>
          <div className="lp-hero-actions">
            <Link to="/workspace" className="lp-primary-cta"><Play size={16} fill="currentColor" />立即体验</Link>
            <a href="#demo" className="lp-secondary-cta">查看交互 Demo <ChevronDown size={16} /></a>
          </div>
          <div className="lp-hero-metrics">
            <div><strong>自动生成</strong><span>从数学文档构建知识图谱</span></div>
            <div><strong>多格式输入</strong><span>PDF、TeX、Markdown 与纯文本</span></div>
            <div><strong>可交互</strong><span>探索节点、关系与图谱结构</span></div>
          </div>
        </div>
      </section>

      <section className="lp-intro lp-section">
        <div className="lp-section-heading">
          <span className="lp-kicker">Research Goal</span>
          <h2>让数学文献中的知识结构真正可追踪</h2>
          <p>传统数学文档以线性文本承载复杂的定义、命题和证明依赖。MathWeaver 将这些隐含结构转换为机器可读、用户可探索的知识网络。</p>
        </div>
        <div className="lp-goal-grid">
          {[
            [FileText, "理解数学文档", "保留公式与语义上下文，识别命题边界、条件、结论和证明。"],
            [CircleDot, "抽取数学实体", "将定义、定理、引理、推论、命题等内容标准化为结构化节点。"],
            [GitBranch, "识别逻辑依赖", "建立定义依赖、证明支撑、直接推论等可解释关系。"],
            [NetworkIcon, "构建知识图谱", "支持筛选、布局切换、原文联动、历史保存与独立导出。"],
          ].map(([Icon, title, text]) => {
            const FeatureIcon = Icon as typeof FileText;
            return <article key={title as string}><FeatureIcon size={20} /><h3>{title as string}</h3><p>{text as string}</p></article>;
          })}
        </div>
      </section>

      <section className="lp-technology lp-section" id="technology">
        <div className="lp-section-heading lp-heading-left">
          <span className="lp-kicker">Core Technology</span>
          <h2>从原始文档到可视化图谱</h2>
          <p>系统以阶段化 Pipeline 为基础，将文本处理、语义抽取、关系判断与质量检查连接为完整流程。</p>
        </div>
        <div className="lp-stage-grid">
          {STAGES.map(([title, text], index) => (
            <article key={title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div><h3>{title}</h3><p>{text}</p></div>
              {index < STAGES.length - 1 && <ArrowRight size={16} />}
            </article>
          ))}
        </div>
      </section>

      <section className="lp-inputs lp-section" id="inputs">
        <div className="lp-section-heading">
          <span className="lp-kicker">Adaptive Input Processing</span>
          <h2>按文档结构选择抽取策略</h2>
          <p>PDF 经 OCR 转为 Markdown，原生 Markdown 可跳过 OCR 直接进入 LLM 节点抽取；LaTeX 通过规则读取数学环境与显式引用，三种输入最终汇入统一的关系提取流程。</p>
        </div>

        <div className="lp-dual-input-flow">
          <div className="lp-input-branches">
            <article className="lp-input-branch lp-text-branch">
              <div className="lp-branch-head">
                <span className="lp-branch-icon"><ScanText size={22} /></span>
                <div><small>文本路径</small><h3>PDF / Markdown 输入</h3></div>
                <span className="lp-branch-tag">LLM 抽取</span>
              </div>

              <div className="lp-path-flow lp-text-flow">
                <div className="lp-flow-input-row">
                  <div className="lp-branch-node lp-input-type"><ScanText size={18} /><span><strong>PDF</strong><small>.pdf</small></span></div>
                  <span className="lp-input-divider" aria-hidden="true">/</span>
                  <div className="lp-branch-node lp-input-type"><FileText size={18} /><span><strong>Markdown</strong><small>.md · .txt · 粘贴文本</small></span></div>
                </div>

                <div className="lp-stage-link lp-dual-stage-link" aria-hidden="true">
                  <svg className="lp-link-path" viewBox="0 0 100 30" preserveAspectRatio="none">
                    <path d="M23.5 0V21" />
                    <path d="M76.5 0V21" />
                  </svg>
                  <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-dual-arrow-left" size={16} />
                  <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-dual-arrow-right" size={16} />
                </div>

                <div className="lp-flow-process-row">
                  <div className="lp-branch-process lp-text-process"><ScanText size={18} /><span><strong>OCR 转 Markdown</strong><small>识别正文、公式与结构</small></span></div>
                  <div className="lp-inline-stage-link" aria-hidden="true">
                    <svg className="lp-link-path" viewBox="0 0 32 72" preserveAspectRatio="none"><path d="M0 36H21" /></svg>
                    <ArrowRight className="lp-link-arrow lp-link-arrow-horizontal" size={16} />
                  </div>
                  <div className="lp-branch-process lp-text-process"><Sparkles size={18} /><span><strong>LLM 节点识别</strong><small>提取定义、定理、引理与证明</small></span></div>
                </div>

                <div className="lp-stage-link lp-text-output-link" aria-hidden="true">
                  <svg className="lp-link-path" viewBox="0 0 100 36" preserveAspectRatio="none"><path d="M76.5 0V16H50V27" /></svg>
                  <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-output-arrow" size={16} />
                </div>

                <div className="lp-branch-result lp-output-node"><CircleDot size={18} /><span><small>路径输出</small><strong>数学节点</strong></span></div>
              </div>
            </article>

            <article className="lp-input-branch lp-latex-branch">
              <div className="lp-branch-head">
                <span className="lp-branch-icon"><FileCode2 size={22} /></span>
                <div><small>结构路径</small><h3>LaTeX 输入</h3></div>
                <span className="lp-branch-tag">规则解析</span>
              </div>

              <div className="lp-path-flow lp-latex-flow">
                <div className="lp-branch-node lp-latex-stage lp-latex-input"><FileCode2 size={18} /><span><strong>LaTeX 源码</strong><small>.tex</small></span></div>

                <div className="lp-stage-link lp-single-stage-link lp-single-input-link" aria-hidden="true">
                  <svg className="lp-link-path" viewBox="0 0 100 30" preserveAspectRatio="none"><path d="M50 0V21" /></svg>
                  <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-output-arrow" size={16} />
                </div>

                <div className="lp-branch-process lp-latex-stage lp-rule-process"><FileCode2 size={18} /><span><strong>规则解析</strong><small>匹配数学环境、label / ref</small></span></div>

                <div className="lp-stage-link lp-single-stage-link lp-single-output-link" aria-hidden="true">
                  <svg className="lp-link-path" viewBox="0 0 100 36" preserveAspectRatio="none"><path d="M50 0V27" /></svg>
                  <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-output-arrow" size={16} />
                </div>

                <div className="lp-branch-result lp-output-node lp-latex-result"><GitBranch size={18} /><span><small>路径输出</small><strong>数学节点 + 显式关系</strong></span></div>
              </div>
            </article>
          </div>

          <div className="lp-branch-converge" aria-hidden="true">
            <svg className="lp-link-path" viewBox="0 0 100 58" preserveAspectRatio="none">
              <path className="lp-converge-desktop" d="M24.5 0V24H50" />
              <path className="lp-converge-desktop" d="M75.5 0V24H50" />
              <path className="lp-converge-stem" d="M50 24V49" />
              <path className="lp-converge-mobile" d="M50 0V49" />
            </svg>
            <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-output-arrow" size={16} />
          </div>

          <div className="lp-shared-pipeline">
            <div className="lp-shared-relation" role="group" aria-label="关系提取">
              <span className="lp-shared-relation-icon"><GitBranch size={22} /></span>
              <div><small>后续阶段</small><strong>关系提取</strong><p>基于当前文档路径，继续识别节点间的逻辑关系</p></div>
            </div>

            <div className="lp-shared-stage-link" aria-hidden="true">
              <svg className="lp-link-path" viewBox="0 0 100 42" preserveAspectRatio="none"><path d="M50 0V33" /></svg>
              <ArrowRight className="lp-link-arrow lp-link-arrow-down lp-output-arrow" size={16} />
            </div>

            <div className="lp-shared-relation lp-graph-build" role="group" aria-label="图谱构建">
              <span className="lp-shared-relation-icon"><NetworkIcon size={22} /></span>
              <div><small>最终阶段</small><strong>图谱构建</strong><p>将数学节点与关系组织为可追踪、可探索的知识图谱</p></div>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-demo-section lp-section" id="demo">
        <div className="lp-section-heading lp-heading-left">
          <span className="lp-kicker">Live Result</span>
          <h2>体验探索图谱结果</h2>
          <p>以本科高等代数为例，点击节点与关系查看结构化内容；筛选不同数学实体类型，理解概念与结论之间的依赖。</p>
        </div>
        <DemoGraph />
        <div className="lp-report-band">
          <div><span>节点</span><strong>{DEMO_NODES.length}</strong><small>定义、定理、引理、推论与命题</small></div>
          <div><span>逻辑关系</span><strong>{DEMO_EDGES.length}</strong><small>定义依赖、条件支撑与直接推论</small></div>
          <div><span>处理阶段</span><strong>6 / 6</strong><small>文档理解至图谱构建全部完成</small></div>
          <div><span>结果状态</span><strong className="lp-ok">可用</strong><small>节点与边均通过结构检查</small></div>
        </div>
      </section>

      <section className="lp-learning lp-section" id="learning">
        <div className="lp-section-heading lp-heading-left">
          <span className="lp-kicker">Learning with MathWeaver</span>
          <h2>沿着知识关系回到原文，<br />在推理中完成证明</h2>
          <p>从图谱中的定义与命题追溯教材上下文，在同一知识背景下写下证明草稿，并获得针对当前思路的提示、检查与总结。</p>
        </div>
        <LearningShowcase />
      </section>

      <section className="lp-final-cta">
        <div>
          <span className="lp-kicker">Start Building</span>
          <h2>把下一份数学文档，变成可探索的知识图谱</h2>
          <p>上传数学文档，让 MathWeaver 完成结构提取、原文联动与图谱探索。</p>
        </div>
        <Link to="/workspace" className="lp-primary-cta"><Upload size={16} />进入 MathWeaver 工作台</Link>
      </section>

      <footer className="lp-footer">
        <div className="lp-footer-main">
          <div className="lp-footer-brand">
            <strong>绎理 · MathWeaver</strong>
            <span>数学文档知识图谱构建与可视化系统</span>
          </div>
          <div className="lp-footer-links">
            <a href="#technology">技术路线</a>
            <a href="#inputs">输入处理</a>
            <a href="#demo">图谱 Demo</a>
            <a href="#learning">辅助学习</a>
            <Link to="/workspace?guide=api-setup&step=intro">使用指南</Link>
            <Link to="/workspace">工作台</Link>
          </div>
        </div>
        <div className="lp-footer-legal">
          <span>© {new Date().getFullYear()} 上海交通大学 AI4Math 课题组 · 保留所有权利</span>
          <span>本项目仅供学术研究与教学用途</span>
        </div>
      </footer>
    </main>
  );
}
