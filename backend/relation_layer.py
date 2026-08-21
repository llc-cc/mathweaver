import argparse
import os
import tempfile

from pipeline.common.io import read_json, write_json
from pipeline.context import PipelineContext
from pipeline.stages.build_relations import stage as build_relations_stage


DEFAULT_NUM_THREADS = 16
DEFAULT_CHECKPOINT = 200
_PLACEHOLDER_PATH = os.path.join(tempfile.gettempdir(), "pdfpipeline_relation_placeholder.md")


def process_node_file(
    input_node_path,
    output_edge_path,
    relation_mode="natural",
    relation_prompt_profile="graph",
    api_url=None,
    model_name=None,
    api_key=None,
    num_threads=DEFAULT_NUM_THREADS,
    checkpoint=DEFAULT_CHECKPOINT,
):
    node_list = read_json(input_node_path)
    if not isinstance(node_list, list):
        raise TypeError("input_node_path 必须指向 node list JSON")

    context = PipelineContext(
        file_path=_PLACEHOLDER_PATH,
        output_edge_path=output_edge_path,
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        num_threads=num_threads,
        checkpoint=checkpoint,
    )
    state = build_relations_stage.run(
        context,
        {"node_list": node_list},
        relation_mode=relation_mode,
        relation_prompt_profile=relation_prompt_profile,
    )
    write_json(output_edge_path, state["edge_list"])
    print(f"✅ Edge JSON saved to: {output_edge_path}")
    return state["edge_list"]


if __name__ == "__main__":
    cli_parser = argparse.ArgumentParser(description="基于现有 node JSON 重跑关系提取")
    cli_parser.add_argument("input_node_path", help="现有 node JSON 路径")
    cli_parser.add_argument("output_edge_path", help="输出 edge JSON 路径")
    cli_parser.add_argument("--relation-mode", choices=["structured", "natural"], default="natural")
    cli_parser.add_argument("--relation-prompt-profile", choices=["graph", "formalization"], default="graph")
    cli_parser.add_argument("--api-url", help="大模型 API 地址", default=None)
    cli_parser.add_argument("--model-name", help="模型名称", default=None)
    cli_parser.add_argument("--api-key", help="API 密钥", default=None)
    cli_parser.add_argument("--num-threads", type=int, default=DEFAULT_NUM_THREADS)
    cli_parser.add_argument("--checkpoint", type=int, default=DEFAULT_CHECKPOINT)
    args = cli_parser.parse_args()

    process_node_file(
        args.input_node_path,
        args.output_edge_path,
        relation_mode=args.relation_mode,
        relation_prompt_profile=args.relation_prompt_profile,
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
    )
