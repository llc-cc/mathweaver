import { describe, expect, it } from "vitest";
import type { GraphEdge } from "./home";
import {
  ACTIVE_EDGE_KINDS,
  EDGE_KINDS,
  classifyEdge,
  classifyVisibleEdge,
} from "./studio-graph";

const edge = (label: string, description = ""): GraphEdge => ({
  from: 2,
  to: 1,
  label,
  description,
  strength: "",
});

describe("GraphStudio visible edge taxonomy", () => {
  it("uses the backend logical label instead of equivalent words in a description", () => {
    const logical = edge("逻辑依赖", "结论与后置节点等价");
    expect(classifyVisibleEdge(logical)).toBe("derives");
    // The complete legacy classifier remains available for future taxonomy
    // restoration; only the current visible classifier is label-only.
    expect(classifyEdge(logical)).toBe("equivalent");
  });

  it("maps definition dependency labels to the independent visible kind", () => {
    expect(classifyVisibleEdge(edge("定义依赖", "说明中含等价"))).toBe("defines");
    expect(classifyVisibleEdge(edge("definitional dependency"))).toBe("defines");
  });

  it("collapses hidden legacy/import labels without dropping their edges", () => {
    expect(classifyVisibleEdge(edge("related"))).toBe("derives");
    expect(ACTIVE_EDGE_KINDS).toEqual(["derives", "defines"]);
    expect(EDGE_KINDS.equivalent.label).toBe("等价");
  });
});
