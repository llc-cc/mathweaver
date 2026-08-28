# GraphStudio — natural-language graph mode redesign

Status handoff + design notes. Branch: `studio-redesign`.

## What this is
A **new, separate** result experience ("GraphStudio") for the natural-language
knowledge graph, added **alongside** the original `ResultScreen` (now "经典版")
so nothing in the classic path is broken. A header toggle switches between them
(`experience` state in `home.tsx`, persisted to `localStorage["mathgraph.experience"]`,
default `"studio"`).

## Files
- `app/routes/studio-graph.ts` — pure logic (no React): salience, level-of-detail,
  4 layout engines, authoritative anchor index, edge taxonomy, settings persistence.
- `app/routes/graphstudio.css` — design system, light + dark themes.
- `app/routes/GraphStudio.tsx` — the component (top bar, left rail, canvas, right panel).
- `app/routes/home.tsx` — wiring: import, `experience` toggle, `StudioWrapper`,
  `onShowStudio` button on the classic header, dev fixture loader effect.
- `app/routes/markdown.tsx` — exported `MdBlock` type (reused by anchor index).

## Implemented (verified visually on Evans5 + a 46-node graph)
1. **Claude-leaning UI + dark mode** — warm paper neutrals, serif display titles,
   hairline borders; `theme: light|dark|auto`, toggle in top bar, persisted.
2. **Salience + level-of-detail** — `computeSalience` (degree + type weight +
   numbered) drives size; a **信息密度** slider picks the top fraction of nodes to
   label; minor nodes render as small colored dots so dense book graphs stay clean.
3. **Several user-selectable orderings** — `阅读顺序` (serpentine doc order),
   `类型泳道` (per-type lanes, doc-rank x), `依赖层次` (depth DAG with lane labels),
   `关系网络` (force). Picker in the top bar; default in settings.
4. **Search replaces the redundant "graph+node" layout** — `/` or the search box;
   Enter focuses the top hit.
5. **Robust jump + recall** — `buildAnchorIndex` rebuilds the markdown block list and
   maps node→block by normalized `source_text` inclusion, with label / math-title /
   surface_anchor / content-fingerprint fallbacks. Coverage % shown in the rail
   (**100% on Evans5**). Click node → reading panel scrolls + flashes; click source
   block → selects node.
6. **Typed dependency edges** — `classifyEdge` buckets relations into a taxonomy
   (推导/使用/特化/推广/等价/定义引用/反例/举例/相关); edges colored by kind with a
   legend; labels are NOT drawn on canvas (space) — instead revealed on hover/edge-
   select, and the node drawer lists typed dependencies (in/out) explicitly.
7. **Personalization** — settings popover (theme, focus-dim, curved edges, default
   layout), persisted via `loadStudioSettings/saveStudioSettings`.
8. **Focus mode** — selecting a node dims nodes/edges outside its 1-hop neighborhood.
9. **Zoom cluster** (＋/−/fit) replaces the old pixel-height slider; fit is clamped
   so labels stay legible on dense graphs.

## How to test locally
Dev fixture loader (localhost only): `/workspace?fixture=evans5` or `?fixture=k126`.
Build fixtures with `python3 /tmp/make_fixture.py NODE.json EDGE.json MD.md OUT.json NAME`
from a finished `backend/test_output/<run>/{node,edge}.json`. Fixtures live in
`public/fixtures/` (gitignored — may contain copyrighted source text).

## Remaining / nice-to-have
- [ ] Backend: emit an explicit `source_block_id` per node so anchor mapping is 100%
      by construction (current frontend matching already hits ~100% on Evans5).
- [ ] Backend recall: extend `ensure_coverage` to catch un-numbered inline definitions
      ("称…为/记作", "We define/denote …") and surface a recall report in the UI.
- [ ] Optional: path-highlight between two selected nodes; minimap for large graphs.
- [ ] Security follow-ups — see SECURITY_REVIEW.md.

## Constraints for a cloud (no-key) resume
Do NOT run the Python backend / dev server (no API keys in cloud). Continue frontend
code + types + docs only. Run `npm run typecheck` (note: pre-existing unrelated errors
in api.ts/root.tsx/TianMing/landing.tsx — only ensure no NEW errors in studio/home).
