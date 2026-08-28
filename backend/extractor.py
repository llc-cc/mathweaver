import os

from pipeline.orchestrator import process_md as _process_md


def process_md(
    file_path,
    output_node_path=None,
    output_edge_path=None,
    output_natural_node_path=None,
    api_url=None,
    model_name=None,
    api_key=None,
    enable_analysis=False,
    enable_math_disambiguation=True,
    ambiguity_table=None,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    source_format="auto",
    source_origin="markdown",
    embedding_model_name=None,
    experimental_logic_ir=False,
):
    return _process_md(
        file_path,
        output_node_path=output_node_path,
        output_edge_path=output_edge_path,
        output_natural_node_path=output_natural_node_path,
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        enable_analysis=enable_analysis,
        enable_math_disambiguation=enable_math_disambiguation,
        ambiguity_table=ambiguity_table,
        edge_output_mode=edge_output_mode,
        relation_prompt_profile=relation_prompt_profile,
        source_format=source_format,
        source_origin=source_origin,
        embedding_model_name=embedding_model_name,
        experimental_logic_ir=experimental_logic_ir,
    )


if __name__ == "__main__":
    from pipeline.config import load_env_file, resolve_llm_config

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    INPUT_PATH = os.path.join(BASE_DIR, "books", "test.tex")
    TEST_NODE_OUT = os.path.join(BASE_DIR, "test_output", "test_1", "TEST_NODE_OUT.json")
    TEST_EDGE_OUT = os.path.join(BASE_DIR, "test_output", "test_1", "TEST_EDGE_OUT.json")
    ENABLE_ANALYSIS = True
    # RELATION_PROMPT_PROFILE = "formalization"
    RELATION_PROMPT_PROFILE = "graph"

    if os.path.exists(INPUT_PATH):
        load_env_file()
        resolved = resolve_llm_config()
        print("--- 开始测试运行 ---")
        print(f"Analysis stage: {'开启' if ENABLE_ANALYSIS else '关闭'}")
        print(f"Relation prompt profile: {RELATION_PROMPT_PROFILE}")
        process_md(
            INPUT_PATH,
            TEST_NODE_OUT,
            TEST_EDGE_OUT,
            api_url=resolved.api_url,
            model_name=resolved.model_name,
            api_key=resolved.api_key,
            enable_analysis=ENABLE_ANALYSIS,
            relation_prompt_profile=RELATION_PROMPT_PROFILE,
        )
        print("--- 测试结束 ---")
    else:
        print(f"测试文件不存在: {INPUT_PATH}，请检查路径。")
