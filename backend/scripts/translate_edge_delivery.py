import json
import os
import re
import time
from pathlib import Path

from openai import OpenAI

from pipeline.common.node import get_node_title, normalize_node_fields


def main():
    node_path = Path("test_output/126/content_output/content_node_natural.json")
    regen_path = Path("/tmp/content_edge_regen_154_tty.json")
    edge_path = Path("test_output/126/content_output/content_edge.json")
    report_path = Path("docs/natural_node_issue_report.md")

    with node_path.open("r", encoding="utf-8") as f:
        raw_nodes = json.load(f)
    nodes = [normalize_node_fields(n) for n in raw_nodes]
    id_to_title_en = {
        n["global_id"]: ((n.get("title", {}) or {}).get("english") or get_node_title(n, prefer="english"))
        for n in nodes
    }
    id_to_title_zh = {
        n["global_id"]: ((n.get("title", {}) or {}).get("chinese") or get_node_title(n))
        for n in nodes
    }

    with regen_path.open("r", encoding="utf-8") as f:
        regen_edges = json.load(f)
    print(f"regen_count {len(regen_edges)}", flush=True)

    pat_lambda = re.compile(r"符号\s*λ|符号λ|自然比较映射所对应的元素|lambda\)", re.I)

    def zh_title(gid):
        return id_to_title_zh.get(gid, gid)

    base_edges = []
    for e in regen_edges:
        reason = e.get("理由", "") or ""
        src = zh_title(e.get("出发节点"))
        dst = zh_title(e.get("到达节点"))
        if e.get("关系") == "定义依赖" and pat_lambda.search(reason):
            continue
        if src == "关于 $h_6^2$ 的 Adams 微分性质" and dst == "126维Kervaire不变量为1的带框流形存在性":
            continue
        if src == "关于 $h_6^2$ 的 Adams 微分性质" and dst == "$f$-extension 的定义":
            continue
        base_edges.append(e)

    print(f"base_count {len(base_edges)}", flush=True)
    if len(base_edges) != 131:
        raise RuntimeError(f"Expected 131 pruned edges, got {len(base_edges)}")

    client = OpenAI(
        api_key=os.environ["PDFPIPELINE_API_KEY"],
        base_url=os.environ["PDFPIPELINE_API_URL"].replace("/chat/completions", "").rstrip("/"),
        timeout=90,
    )
    model = os.environ["PDFPIPELINE_MODEL_NAME"]
    reasons = [e.get("理由", "") for e in base_edges]
    translated = []
    batch_size = 8
    total_batches = (len(reasons) + batch_size - 1) // batch_size
    for i in range(0, len(reasons), batch_size):
        batch = reasons[i : i + batch_size]
        prompt = (
            "Translate each Chinese explanation into concise, natural academic English. "
            "Preserve mathematical notation, symbols, and LaTeX exactly. "
            "Return only a JSON array of strings in the same order. "
            "Do not add markdown or commentary.\n\n" + json.dumps(batch, ensure_ascii=False)
        )
        print(f"translate batch {i // batch_size + 1}/{total_batches}", flush=True)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise translator for mathematical knowledge graph relations."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = (resp.choices[0].message.content or "").strip()
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1:
            raise RuntimeError(f"Bad translation response for batch {i}: {content[:400]}")
        arr = json.loads(content[start : end + 1])
        if len(arr) != len(batch):
            raise RuntimeError(f"Length mismatch in batch {i}: expected {len(batch)} got {len(arr)}")
        translated.extend(arr)
        time.sleep(0.2)

    relation_map = {"逻辑依赖": "logical dependency", "定义依赖": "definitional dependency"}
    converted = []
    for e, reason_en in zip(base_edges, translated):
        converted.append(
            {
                "source_node": id_to_title_en.get(e.get("出发节点"), e.get("出发节点")),
                "target_node": id_to_title_en.get(e.get("到达节点"), e.get("到达节点")),
                "relation": relation_map.get(e.get("关系"), e.get("关系")),
                "reason": reason_en,
            }
        )

    if any(not item["source_node"] or not item["target_node"] or not item["relation"] for item in converted):
        bad = [item for item in converted if not item["source_node"] or not item["target_node"] or not item["relation"]][:5]
        raise RuntimeError(f"Found invalid converted edges: {bad}")

    with edge_path.open("w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=4)

    marker = "## Edge 全英文交付调整记录"
    append = """

## Edge 全英文交付调整记录

### 调整目标
根据最终交付需求，将 [content_edge.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_edge.json:1) 全部转换为英文表达，包括：
- 节点名称改为自然语言节点的英文标题；
- `关系` 改为英文关系名；
- `理由` 全量翻译为英文。

### 调整方式
- 先从重跑关系层结果恢复 `131` 条中文基线边；
- 再以 [content_node_natural.json](/Users/clara/pdfPipeline/backend/test_output/126/content_output/content_node_natural.json:1) 的英文标题作为 node name 映射源；
- 将 `逻辑依赖` / `定义依赖` 分别转换为 `logical dependency` / `definitional dependency`；
- 使用当前项目配置的模型对边理由做逐批翻译，保留数学公式与 LaTeX 记号。

### 结果
- 当前交付版 edge 文件已改为全英文字段值；
- 节点名称、关系、理由均可直接用于英文交付或后续英文蓝图展示。
"""
    text = report_path.read_text(encoding="utf-8")
    if marker not in text:
        report_path.write_text(text + append, encoding="utf-8")

    print(json.dumps({"edge_count": len(converted), "sample": converted[:3]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
