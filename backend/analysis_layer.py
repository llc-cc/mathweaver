import argparse
import tempfile
import os

from pipeline.common.io import build_default_analysis_output_path
from pipeline.context import PipelineContext
from pipeline.stages.analysis.stage import process_node_file as _process_node_file
from pipeline.stages.analysis.stage import run_analysis_layer as _run_analysis_layer


DEFAULT_NUM_THREADS = 16
DEFAULT_CHECKPOINT = 200

# 用临时占位路径构造 context（仅需 LLM 实例，不输出中间文件）
_PLACEHOLDER_PATH = os.path.join(tempfile.gettempdir(), "pdfpipeline_placeholder.md")


def run_analysis_layer(
    node_list,
    api_url=None,
    model_name=None,
    api_key=None,
    num_threads=DEFAULT_NUM_THREADS,
    checkpoint=DEFAULT_CHECKPOINT,
    checkpoint_dir=None,
):
    context = PipelineContext(
        file_path=_PLACEHOLDER_PATH,
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        num_threads=num_threads,
        checkpoint=checkpoint,
    )
    return _run_analysis_layer(
        node_list,
        llm=context.llm,
        parser=context.parser,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def process_node_file(
    input_node_path,
    output_node_path=None,
    api_url=None,
    model_name=None,
    api_key=None,
    num_threads=DEFAULT_NUM_THREADS,
    checkpoint=DEFAULT_CHECKPOINT,
):
    context = PipelineContext(
        file_path=input_node_path,
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        num_threads=num_threads,
        checkpoint=checkpoint,
    )
    return _process_node_file(
        input_node_path,
        llm=context.llm,
        parser=context.parser,
        num_threads=num_threads,
        checkpoint=checkpoint,
        output_node_path=output_node_path,
    )


if __name__ == "__main__":
    cli_parser = argparse.ArgumentParser(description="Node analysis layer 后处理脚本")
    cli_parser.add_argument("input_node_path", help="Extractor 产出的 node JSON 路径")
    cli_parser.add_argument("--output-node-path", default=None, help="增强后的 node JSON 输出路径，默认在同目录生成 *_analysis.json")
    cli_parser.add_argument("--api-url", help="大模型 API 地址", default=None)
    cli_parser.add_argument("--model-name", help="模型名称", default=None)
    cli_parser.add_argument("--api-key", help="API 密钥", default=None)
    cli_parser.add_argument("--num-threads", type=int, default=DEFAULT_NUM_THREADS)
    cli_parser.add_argument("--checkpoint", type=int, default=DEFAULT_CHECKPOINT)
    args = cli_parser.parse_args()

    output_node_path = args.output_node_path or build_default_analysis_output_path(args.input_node_path)
    process_node_file(
        args.input_node_path,
        output_node_path=output_node_path,
        api_url=args.api_url,
        model_name=args.model_name,
        api_key=args.api_key,
        num_threads=args.num_threads,
        checkpoint=args.checkpoint,
    )
