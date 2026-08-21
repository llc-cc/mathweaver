export type NodeTypeLanguage = "zh" | "en" | "bilingual";

interface NodeTypeName {
  zh: string;
  en: string;
}

const NODE_TYPE_NAMES: NodeTypeName[] = [
  { zh: "公理", en: "Axiom" },
  { zh: "定义", en: "Definition" },
  { zh: "性质", en: "Property" },
  { zh: "定理", en: "Theorem" },
  { zh: "引理", en: "Lemma" },
  { zh: "命题", en: "Proposition" },
  { zh: "推论", en: "Corollary" },
  { zh: "断言", en: "Claim" },
  { zh: "猜想", en: "Conjecture" },
  { zh: "事实", en: "Fact" },
  { zh: "观察", en: "Observation" },
  { zh: "例子", en: "Example" },
  { zh: "反例", en: "Counterexample" },
  { zh: "习题", en: "Exercise" },
  { zh: "问题", en: "Problem" },
  { zh: "注释", en: "Remark" },
  { zh: "记号", en: "Notation" },
  { zh: "证明", en: "Proof" },
];

const NODE_TYPE_NAME_BY_ALIAS = new Map<string, NodeTypeName>();
for (const name of NODE_TYPE_NAMES) {
  NODE_TYPE_NAME_BY_ALIAS.set(name.zh, name);
  NODE_TYPE_NAME_BY_ALIAS.set(name.en.toLowerCase(), name);
}
NODE_TYPE_NAME_BY_ALIAS.set("例", NODE_TYPE_NAME_BY_ALIAS.get("例子")!);
NODE_TYPE_NAME_BY_ALIAS.set("练习", NODE_TYPE_NAME_BY_ALIAS.get("习题")!);
NODE_TYPE_NAME_BY_ALIAS.set("注", NODE_TYPE_NAME_BY_ALIAS.get("注释")!);
NODE_TYPE_NAME_BY_ALIAS.set("注记", NODE_TYPE_NAME_BY_ALIAS.get("注释")!);
NODE_TYPE_NAME_BY_ALIAS.set("备注", NODE_TYPE_NAME_BY_ALIAS.get("注释")!);
NODE_TYPE_NAME_BY_ALIAS.set("note", NODE_TYPE_NAME_BY_ALIAS.get("注释")!);

export function nodeTypeLabel(nodeType: string, language: NodeTypeLanguage): string {
  const trimmed = nodeType.trim();
  const name = NODE_TYPE_NAME_BY_ALIAS.get(trimmed)
    ?? NODE_TYPE_NAME_BY_ALIAS.get(trimmed.toLowerCase());
  if (!name) return trimmed;
  return language === "en" ? name.en : name.zh;
}
