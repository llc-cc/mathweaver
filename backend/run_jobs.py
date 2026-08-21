import argparse
import json
import os
import sys

from pipeline.config import load_env_file, resolve_bool, resolve_llm_config


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "run_jobs_config.json")


def load_config(config_path):
    config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 JSON object")
    return data, config_path


def resolve_input_paths(inputs, config_dir):
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("配置文件中的 inputs 必须是非空列表")

    resolved = []
    for item in inputs:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("inputs 中每一项都必须是非空字符串")
        if os.path.isabs(item):
            resolved.append(item)
        else:
            resolved.append(os.path.abspath(os.path.join(config_dir, item)))
    return resolved


def main():
    parser = argparse.ArgumentParser(description="批量运行 PDF/Markdown 提取入口")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="任务配置 JSON 路径")
    parser.add_argument("--env-file", default=None, help="环境变量文件路径，默认读取 backend/.env")
    parser.add_argument(
        "--edge-output-mode",
        choices=["structured", "natural", "both"],
        default=None,
        help="覆盖配置文件中的边输出模式",
    )
    parser.add_argument(
        "--relation-prompt-profile",
        choices=["graph", "formalization"],
        default=None,
        help="覆盖配置文件中的关系提示词版本",
    )
    args = parser.parse_args()

    from main import run_pipeline

    env_loaded = load_env_file(args.env_file)
    if env_loaded:
        print(f"📦 已加载环境变量文件: {env_loaded}")
    else:
        print("📦 未找到 .env，继续使用当前 shell 环境变量")

    config, config_path = load_config(args.config)
    config_dir = os.path.dirname(config_path)

    inputs = resolve_input_paths(config.get("inputs"), config_dir)
    output_root = config.get("output_root")
    if isinstance(output_root, str) and output_root.strip():
        if not os.path.isabs(output_root):
            output_root = os.path.abspath(os.path.join(config_dir, output_root))
    else:
        output_root = None

    enable_analysis = resolve_bool(config.get("enable_analysis"), default=False)
    edge_output_mode = str(config.get("edge_output_mode") or "both").strip().lower()
    if args.edge_output_mode:
        edge_output_mode = args.edge_output_mode
    if edge_output_mode not in {"structured", "natural", "both"}:
        raise ValueError("edge_output_mode 仅支持: structured / natural / both")

    relation_prompt_profile = str(config.get("relation_prompt_profile") or "graph").strip().lower()
    if args.relation_prompt_profile:
        relation_prompt_profile = args.relation_prompt_profile
    if relation_prompt_profile not in {"graph", "formalization"}:
        raise ValueError("relation_prompt_profile 仅支持: graph / formalization")

    resolved_llm = resolve_llm_config(
        api_url=config.get("api_url"),
        model_name=config.get("model_name"),
        api_key=config.get("api_key"),
    )

    if not (resolved_llm.api_url and resolved_llm.model_name and resolved_llm.api_key):
        raise ValueError(
            "LLM 配置不完整。请在环境变量或配置文件中提供 api_url / model_name / api_key。"
        )

    print("=" * 60)
    print("🚀 批量任务开始")
    print(f"📄 输入文件数: {len(inputs)}")
    print(f"📂 输出根目录: {output_root or '跟随输入文件目录'}")
    print(f"🧠 Analysis: {'开启' if enable_analysis else '关闭'}")
    print(f"🔗 Edge输出模式: {edge_output_mode}")
    print(f"🧩 Relation提示词: {relation_prompt_profile}")
    print(f"🤖 Model: {resolved_llm.model_name}")
    print("=" * 60)

    failures = []
    for index, input_path in enumerate(inputs, start=1):
        print(f"\n[{index}/{len(inputs)}] 处理: {input_path}")
        if not os.path.exists(input_path):
            print(f"❌ 文件不存在: {input_path}")
            failures.append((input_path, "文件不存在"))
            continue

        result = run_pipeline(
            input_path,
            api_url=resolved_llm.api_url,
            model_name=resolved_llm.model_name,
            api_key=resolved_llm.api_key,
            enable_analysis=enable_analysis,
            output_root_dir=output_root,
            edge_output_mode=edge_output_mode,
            relation_prompt_profile=relation_prompt_profile,
            return_data=False,
        )
        if not result:
            failures.append((input_path, "流程失败"))

    print("\n" + "=" * 60)
    if failures:
        print("⚠️ 部分任务失败：")
        for path, reason in failures:
            print(f"- {path}: {reason}")
        print("=" * 60)
        sys.exit(1)

    print("✅ 全部任务完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
