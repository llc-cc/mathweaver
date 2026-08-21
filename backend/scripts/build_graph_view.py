import json
from pathlib import Path


NODE_PATH = Path("test_output/126/content_output/content_node_natural.json")
EDGE_PATH = Path("test_output/126/content_output/content_edge.json")
OUT_PATH = Path("test_output/126/content_output/graph_view.html")


def main():
    nodes = json.loads(NODE_PATH.read_text(encoding="utf-8"))
    edges = json.loads(EDGE_PATH.read_text(encoding="utf-8"))

    node_map = {}
    title_to_ids = {}
    html_nodes = []
    type_colors = {
        "定理": "#1d4ed8",
        "定义": "#7c3aed",
        "推论": "#0f766e",
        "性质": "#b45309",
        "命题": "#be123c",
        "例子": "#4b5563",
        "注释": "#6b7280",
    }

    title_counts = {}

    for idx, node in enumerate(nodes):
        title = node.get("title", {})
        english_title = title.get("english") if isinstance(title, dict) else str(title)
        chinese_title = title.get("chinese") if isinstance(title, dict) else str(title)
        node_type = node.get("node_type", "未分类")
        color = type_colors.get(node_type, "#334155")
        title_counts[english_title] = title_counts.get(english_title, 0) + 1
        suffix = title_counts[english_title]
        unique_id = english_title if suffix == 1 else f"{english_title} [dup {suffix}]"
        item = {
            "id": unique_id,
            "label": english_title,
            "type": node_type,
            "titleZh": chinese_title,
            "content": node.get("content", ""),
            "proof": node.get("proof", ""),
            "labelRef": node.get("label", ""),
            "color": color,
            "duplicateIndex": suffix,
            "displayLabel": english_title if suffix == 1 else f"{english_title} [{suffix}]",
            "x": 0,
            "y": 0,
            "vx": 0,
            "vy": 0,
            "index": idx,
        }
        node_map[unique_id] = item
        title_to_ids.setdefault(english_title, []).append(unique_id)
        html_nodes.append(item)

    html_edges = []
    for edge in edges:
        source = edge.get("source_node")
        target = edge.get("target_node")
        source_ids = title_to_ids.get(source, [])
        target_ids = title_to_ids.get(target, [])
        if not source_ids or not target_ids:
            continue
        for source_id in source_ids:
            for target_id in target_ids:
                html_edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "relation": edge.get("relation", ""),
                        "reason": edge.get("reason", ""),
                    }
                )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Knowledge Graph View</title>
  <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
      }},
      svg: {{
        fontCache: 'global'
      }}
    }};
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
  <style>
    :root {{
      --bg: #f6f4ef;
      --panel: rgba(255,255,255,.86);
      --ink: #14213d;
      --muted: #5b6475;
      --line: rgba(20,33,61,.18);
      --accent: #c2410c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      background:
        radial-gradient(circle at top left, rgba(194,65,12,.16), transparent 26%),
        radial-gradient(circle at bottom right, rgba(29,78,216,.12), transparent 28%),
        var(--bg);
      color: var(--ink);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: 100vh;
    }}
    .sidebar {{
      padding: 20px 18px;
      border-right: 1px solid rgba(20,33,61,.12);
      background: var(--panel);
      backdrop-filter: blur(12px);
      overflow: auto;
    }}
    .sidebar h1 {{
      margin: 0 0 10px;
      font-size: 24px;
      line-height: 1.1;
    }}
    .sidebar p {{
      color: var(--muted);
      margin: 0 0 14px;
      font-size: 14px;
      line-height: 1.5;
    }}
    .search {{
      width: 100%;
      padding: 10px 12px;
      border: 1px solid rgba(20,33,61,.16);
      border-radius: 10px;
      background: rgba(255,255,255,.92);
      color: var(--ink);
      font: inherit;
      margin-bottom: 14px;
    }}
    .stats, .legend, .detail {{
      margin-top: 16px;
      padding: 14px;
      border-radius: 14px;
      background: rgba(255,255,255,.84);
      border: 1px solid rgba(20,33,61,.1);
    }}
    .stats div, .legend div {{
      margin: 6px 0;
      font-size: 14px;
    }}
    .swatch {{
      width: 11px;
      height: 11px;
      display: inline-block;
      border-radius: 999px;
      margin-right: 8px;
      vertical-align: middle;
    }}
    .detail h2 {{
      margin: 0 0 8px;
      font-size: 18px;
      line-height: 1.3;
    }}
    .detail .meta {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .detail .body {{
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      line-height: 1.6;
      margin: 0;
      color: #243b53;
    }}
    .detail .body code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
    .canvas-wrap {{
      position: relative;
      overflow: hidden;
    }}
    canvas {{
      display: block;
      width: 100%;
      height: 100vh;
      cursor: grab;
    }}
    canvas.dragging {{
      cursor: grabbing;
    }}
    .hint {{
      position: absolute;
      right: 16px;
      top: 16px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,255,255,.84);
      border: 1px solid rgba(20,33,61,.1);
      color: var(--muted);
      font-size: 12px;
      max-width: 260px;
    }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ border-right: 0; border-bottom: 1px solid rgba(20,33,61,.12); }}
      canvas {{ height: 72vh; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1>Graph View</h1>
      <p>Local visualization for the current node and edge delivery JSON. Click a node or edge to inspect details. Search filters visible nodes by English title.</p>
      <input id="search" class="search" placeholder="Search node title...">
      <section class="stats">
        <div><strong>Nodes:</strong> {len(html_nodes)}</div>
        <div><strong>Edges:</strong> {len(html_edges)}</div>
      </section>
      <section class="legend" id="legend"></section>
      <section class="detail" id="detail">
        <h2>No selection</h2>
        <div class="meta">Click a node or an edge.</div>
        <div class="body">Node content or edge reason will appear here.</div>
      </section>
    </aside>
    <main class="canvas-wrap">
      <canvas id="graph"></canvas>
      <div class="hint">Drag nodes to rearrange. Mouse wheel zooms. Drag empty space to pan. Search dims unrelated nodes.</div>
    </main>
  </div>
  <script>
    const nodes = {json.dumps(html_nodes, ensure_ascii=False)};
    const edges = {json.dumps(html_edges, ensure_ascii=False)};
    const typeColors = {json.dumps(type_colors, ensure_ascii=False)};

    const canvas = document.getElementById("graph");
    const ctx = canvas.getContext("2d");
    const detail = document.getElementById("detail");
    const searchInput = document.getElementById("search");
    const legend = document.getElementById("legend");

    const state = {{
      scale: 1,
      offsetX: 0,
      offsetY: 0,
      draggingNode: null,
      panning: false,
      lastX: 0,
      lastY: 0,
      selectedNode: null,
      selectedEdge: null,
      filter: "",
    }};

    function resize() {{
      const ratio = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * ratio;
      canvas.height = rect.height * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }}

    function initPositions() {{
      const centerX = canvas.clientWidth / 2;
      const centerY = canvas.clientHeight / 2;
      const radius = Math.min(canvas.clientWidth, canvas.clientHeight) * 0.28;
      nodes.forEach((node, i) => {{
        const angle = (Math.PI * 2 * i) / nodes.length;
        node.x = centerX + Math.cos(angle) * radius + (Math.random() - 0.5) * 80;
        node.y = centerY + Math.sin(angle) * radius + (Math.random() - 0.5) * 80;
      }});
    }}

    function visibleNode(node) {{
      if (!state.filter) return true;
      return node.displayLabel.toLowerCase().includes(state.filter);
    }}

    function worldToScreen(x, y) {{
      return {{
        x: x * state.scale + state.offsetX,
        y: y * state.scale + state.offsetY,
      }};
    }}

    function screenToWorld(x, y) {{
      return {{
        x: (x - state.offsetX) / state.scale,
        y: (y - state.offsetY) / state.scale,
      }};
    }}

    function nodeRadius(node) {{
      return 9 + Math.min(node.label.length / 10, 10);
    }}

    function escapeHtml(text) {{
      return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }}

    function formatMathBlock(text) {{
      return escapeHtml(text || "").replace(/\\n/g, "<br>");
    }}

    function simplifyCanvasLabel(label) {{
      return String(label || "")
        .replace(/\$\$/g, "")
        .replace(/\$/g, "")
        .replace(/\\infty/g, "∞")
        .replace(/\\lambda/g, "λ")
        .replace(/\\eta/g, "η")
        .replace(/\\nu/g, "ν")
        .replace(/\\pi/g, "π")
        .replace(/\\HF/g, "HF")
        .replace(/\\to/g, "→")
        .replace(/\\_/g, "_")
        .replace(/[{{}}]/g, "");
    }}

    function typesetDetail() {{
      if (window.MathJax && window.MathJax.typesetPromise) {{
        window.MathJax.typesetPromise([detail]).catch(() => {{}});
      }}
    }}

    function simulate() {{
      const repel = 9000;
      const spring = 0.004;
      const desired = 110;

      for (let i = 0; i < nodes.length; i++) {{
        for (let j = i + 1; j < nodes.length; j++) {{
          const a = nodes[i];
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distSq = dx * dx + dy * dy + 0.01;
          const force = repel / distSq;
          const dist = Math.sqrt(distSq);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }}
      }}

      for (const edge of edges) {{
        const source = nodes.find(n => n.id === edge.source);
        const target = nodes.find(n => n.id === edge.target);
        if (!source || !target) continue;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - desired) * spring;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        source.vx += fx;
        source.vy += fy;
        target.vx -= fx;
        target.vy -= fy;
      }}

      for (const node of nodes) {{
        if (state.draggingNode === node) continue;
        node.vx *= 0.82;
        node.vy *= 0.82;
        node.x += node.vx;
        node.y += node.vy;
      }}
    }}

    function drawArrow(x1, y1, x2, y2, color) {{
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const head = 7;
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - head * Math.cos(angle - Math.PI / 6), y2 - head * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(x2 - head * Math.cos(angle + Math.PI / 6), y2 - head * Math.sin(angle + Math.PI / 6));
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
    }}

    function draw() {{
      ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);

      for (const edge of edges) {{
        const source = nodes.find(n => n.id === edge.source);
        const target = nodes.find(n => n.id === edge.target);
        if (!source || !target) continue;
        const hidden = !visibleNode(source) || !visibleNode(target);
        const s = worldToScreen(source.x, source.y);
        const t = worldToScreen(target.x, target.y);
        const color = state.selectedEdge === edge ? "rgba(194,65,12,.95)" : hidden ? "rgba(20,33,61,.05)" : "rgba(20,33,61,.18)";
        drawArrow(s.x, s.y, t.x, t.y, color);
      }}

      for (const node of nodes) {{
        const p = worldToScreen(node.x, node.y);
        const hidden = !visibleNode(node);
        const radius = nodeRadius(node);
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = hidden ? "rgba(203,213,225,.35)" : node.color;
        ctx.fill();
        ctx.lineWidth = state.selectedNode === node ? 3 : 1.2;
        ctx.strokeStyle = state.selectedNode === node ? "#c2410c" : "rgba(255,255,255,.88)";
        ctx.stroke();

        if (!hidden) {{
          ctx.fillStyle = "#0f172a";
          ctx.font = "12px Georgia, serif";
          ctx.fillText(simplifyCanvasLabel(node.displayLabel).slice(0, 56), p.x + radius + 6, p.y + 4);
        }}
      }}
    }}

    function updateDetailNode(node) {{
      detail.innerHTML = `
        <h2>${{node.label}}</h2>
        <div class="meta">${{node.type}} | ${{node.titleZh || ""}}${{node.duplicateIndex > 1 ? " | duplicate title #" + node.duplicateIndex : ""}}</div>
        <div class="body">${{formatMathBlock((node.content || "").trim() || "No content.")}}${{(node.proof || "").trim() ? "<br><br><strong>Proof.</strong><br>" + formatMathBlock(node.proof.trim()) : ""}}</div>
      `;
      typesetDetail();
    }}

    function updateDetailEdge(edge) {{
      detail.innerHTML = `
        <h2>${{edge.relation}}</h2>
        <div class="meta">${{edge.source}} → ${{edge.target}}</div>
        <div class="body">${{formatMathBlock(edge.reason || "No reason.")}}</div>
      `;
      typesetDetail();
    }}

    function hitNode(mx, my) {{
      const world = screenToWorld(mx, my);
      for (const node of nodes) {{
        const dx = world.x - node.x;
        const dy = world.y - node.y;
        if (Math.sqrt(dx * dx + dy * dy) <= nodeRadius(node) / state.scale + 2) return node;
      }}
      return null;
    }}

    function pointToSegmentDistance(px, py, x1, y1, x2, y2) {{
      const dx = x2 - x1;
      const dy = y2 - y1;
      const lenSq = dx * dx + dy * dy;
      if (!lenSq) return Math.hypot(px - x1, py - y1);
      let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
      t = Math.max(0, Math.min(1, t));
      const projX = x1 + t * dx;
      const projY = y1 + t * dy;
      return Math.hypot(px - projX, py - projY);
    }}

    function hitEdge(mx, my) {{
      for (const edge of edges) {{
        const source = nodes.find(n => n.id === edge.source);
        const target = nodes.find(n => n.id === edge.target);
        if (!source || !target) continue;
        const s = worldToScreen(source.x, source.y);
        const t = worldToScreen(target.x, target.y);
        if (pointToSegmentDistance(mx, my, s.x, s.y, t.x, t.y) < 6) return edge;
      }}
      return null;
    }}

    canvas.addEventListener("mousedown", evt => {{
      const rect = canvas.getBoundingClientRect();
      const x = evt.clientX - rect.left;
      const y = evt.clientY - rect.top;
      const node = hitNode(x, y);
      state.lastX = x;
      state.lastY = y;
      if (node) {{
        state.draggingNode = node;
        state.selectedNode = node;
        state.selectedEdge = null;
        canvas.classList.add("dragging");
        updateDetailNode(node);
      }} else {{
        const edge = hitEdge(x, y);
        if (edge) {{
          state.selectedEdge = edge;
          state.selectedNode = null;
          updateDetailEdge(edge);
        }} else {{
          state.panning = true;
          state.selectedNode = null;
          state.selectedEdge = null;
        }}
      }}
    }});

    window.addEventListener("mousemove", evt => {{
      const rect = canvas.getBoundingClientRect();
      const x = evt.clientX - rect.left;
      const y = evt.clientY - rect.top;
      if (state.draggingNode) {{
        const world = screenToWorld(x, y);
        state.draggingNode.x = world.x;
        state.draggingNode.y = world.y;
        state.draggingNode.vx = 0;
        state.draggingNode.vy = 0;
      }} else if (state.panning) {{
        state.offsetX += x - state.lastX;
        state.offsetY += y - state.lastY;
        state.lastX = x;
        state.lastY = y;
      }}
    }});

    window.addEventListener("mouseup", () => {{
      state.draggingNode = null;
      state.panning = false;
      canvas.classList.remove("dragging");
    }});

    canvas.addEventListener("wheel", evt => {{
      evt.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = evt.clientX - rect.left;
      const my = evt.clientY - rect.top;
      const worldBefore = screenToWorld(mx, my);
      const zoom = evt.deltaY < 0 ? 1.08 : 0.92;
      state.scale = Math.max(0.35, Math.min(2.8, state.scale * zoom));
      const worldAfter = screenToWorld(mx, my);
      state.offsetX += (worldAfter.x - worldBefore.x) * state.scale;
      state.offsetY += (worldAfter.y - worldBefore.y) * state.scale;
    }}, {{ passive: false }});

    searchInput.addEventListener("input", evt => {{
      state.filter = evt.target.value.trim().toLowerCase();
    }});

    function renderLegend() {{
      legend.innerHTML = "<div><strong>Node Types</strong></div>" + Object.entries(typeColors).map(([type, color]) =>
        `<div><span class="swatch" style="background:${{color}}"></span>${{type}}</div>`
      ).join("");
    }}

    function tick() {{
      simulate();
      draw();
      requestAnimationFrame(tick);
    }}

    window.addEventListener("resize", () => {{
      resize();
      draw();
    }});

    resize();
    initPositions();
    renderLegend();
    tick();
  </script>
</body>
</html>
"""

    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
