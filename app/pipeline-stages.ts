export type PipelineStageDef = readonly [key: string, label: string];

export const MAIN_PIPELINE_STAGE_DEFS = [
  ["correct_text", "原文内容校正"],
  ["segment_blocks", "文档结构识别"],
  ["extract_statements", "数学知识提取"],
  ["ensure_coverage", "遗漏知识补全"],
  ["clean_nodes", "无效内容清理"],
  ["split_nodes", "复合知识拆分"],
  ["generate_titles", "知识标题生成"],
  ["extract_logic_tuples", "知识要素结构化"],
  ["analysis", "语义信息补充"],
  ["repair", "知识结构修复"],
  ["extract_references", "文内引用识别"],
  ["repair_lite", "引用结果校正"],
  ["build_relations", "知识关系提取"],
  ["finalize_output", "图谱结果生成"],
] as const satisfies readonly PipelineStageDef[];

export const MAIN_PIPELINE_STAGE_COUNT = MAIN_PIPELINE_STAGE_DEFS.length;

const STAGE_LABELS: Readonly<Record<string, string>> = {
  ...Object.fromEntries(MAIN_PIPELINE_STAGE_DEFS),
  compile_logic_form: "实验旁路：谓词树生成",
  normalize_predicates: "实验旁路：谓词归一化",
};

export function pipelineStageLabel(
  stage: string | null | undefined,
  fallbackLabel?: string | null,
): string {
  if (!stage) return fallbackLabel || "准备中…";
  return STAGE_LABELS[stage] || fallbackLabel || stage;
}
