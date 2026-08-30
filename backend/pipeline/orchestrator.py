import os
from dataclasses import dataclass, replace
from typing import Callable

from .context import PipelineContext
from .common.io import write_json
from .common.pipeline_cache import PipelineStageCache
from .common.stage_recovery import recover_failed_stage_tasks, unresolved_report_path
from matrix_flow.runner import MatrixFlowRunner
from .stages.analysis import stage as analysis_stage
from .stages.build_relations import stage as build_relations_stage
from .stages.clean_nodes import stage as clean_nodes_stage
from .stages.correct_text import stage as correct_text_stage
from .stages.compile_logic_form import stage as compile_logic_form_stage
from .stages.ensure_coverage import stage as ensure_coverage_stage
from .stages.extract_logic_tuples import stage as extract_logic_tuples_stage
from .stages.extract_references import stage as extract_references_stage
from .stages.normalize_predicates import stage as normalize_predicates_stage
from .stages.extract_statements import stage as extract_statements_stage
from .stages.finalize_output import stage as finalize_output_stage
from .stages.generate_titles import stage as generate_titles_stage
from .stages.repair import stage as repair_stage
from .stages.repair_lite import stage as repair_lite_stage
from .stages.segment_blocks import stage as segment_blocks_stage
from .stages.split_nodes import stage as split_nodes_stage


StageRunner = Callable[[PipelineContext, dict], dict]
StageCallback = Callable[["FixedStage", int, int, dict], None]


@dataclass(frozen=True)
class FixedStage:
    key: str
    label: str
    runner: StageRunner
    requires: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    nonempty: tuple[str, ...] = ()
    recovery_adapter: object | None = None


def _assert_state_keys(stage: FixedStage, state: dict) -> None:
    missing_inputs = [key for key in stage.requires if key not in state]
    if missing_inputs:
        raise RuntimeError(
            f"Stage {stage.key} is missing required input state: {', '.join(missing_inputs)}"
        )

    missing_outputs = [key for key in stage.produces if key not in state]
    empty_outputs = []
    for key in stage.nonempty:
        value = state.get(key)
        if value is None or value == "" or (isinstance(value, (dict, list, tuple)) and not value):
            empty_outputs.append(key)
    if missing_outputs or empty_outputs:
        details = []
        if missing_outputs:
            details.append(f"missing: {', '.join(missing_outputs)}")
        if empty_outputs:
            details.append(f"empty: {', '.join(empty_outputs)}")
        raise RuntimeError(
            f"Stage {stage.key} did not produce required downstream state ({'; '.join(details)}). "
            "Check the stage cache and failure report for the upstream failure."
        )


def _relation_runner(edge_output_mode: str, relation_prompt_profile: str) -> StageRunner:
    mode = (edge_output_mode or "structured").strip().lower()
    if mode not in {"structured", "natural", "both"}:
        raise ValueError("edge_output_mode only supports structured / natural / both")

    prompt_profile = (relation_prompt_profile or "graph").strip().lower()
    if prompt_profile not in {"graph", "formalization"}:
        raise ValueError("relation_prompt_profile only supports graph / formalization")

    def run_branch(context, input_state, relation_mode):
        baseline = unresolved_report_path(build_relations_stage, context)
        baseline_report = (
            build_relations_stage.latest_unresolved_failure_report(context)
            if baseline
            else None
        )
        baseline_payload = (
            baseline_report.get("report")
            if isinstance(baseline_report, dict)
            and isinstance(baseline_report.get("report"), dict)
            else {}
        )
        baseline_updated_at = baseline_payload.get("updated_at")
        previous_mode = getattr(context, "execution_mode", "pipeline")
        try:
            context.execution_mode = "agent"
            branch_state = build_relations_stage.run(
                context,
                dict(input_state),
                relation_mode=relation_mode,
                relation_prompt_profile=prompt_profile,
            )
        finally:
            context.execution_mode = previous_mode
        return recover_failed_stage_tasks(
            context,
            branch_state,
            build_relations_stage,
            baseline_report_path=baseline,
            baseline_report_updated_at=baseline_updated_at,
            max_rounds=2,
            rerun_kwargs={
                "relation_mode": relation_mode,
                "relation_prompt_profile": prompt_profile,
            },
        )

    def run(context, state):
        relation_input_state = dict(state)
        if mode in {"structured", "natural"}:
            return run_branch(context, relation_input_state, mode)

        structured_state = run_branch(context, relation_input_state, "structured")
        natural_state = run_branch(context, relation_input_state, "natural")
        structured_state["edge_list_structured"] = structured_state["edge_list"]
        structured_state["edge_list_natural"] = natural_state["edge_list"]

        if context.output_edge_path:
            edge_abs = os.path.abspath(context.output_edge_path)
            edge_dir = os.path.dirname(edge_abs)
            edge_name = os.path.basename(edge_abs)
            edge_stem, edge_ext = os.path.splitext(edge_name)
            natural_edge_path = os.path.join(edge_dir, f"{edge_stem}_natural{edge_ext or '.json'}")
            write_json(natural_edge_path, natural_state["edge_list"])
            print(f"Edge JSON (natural mode) saved to: {natural_edge_path}")
        return structured_state

    return run


def build_fixed_stage_plan(
    *,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    experimental_logic_ir=False,
) -> tuple[FixedStage, ...]:
    relation_runner = _relation_runner(edge_output_mode, relation_prompt_profile)
    stages = [
        FixedStage(
            "correct_text",
            "原文内容校正",
            correct_text_stage.run,
            produces=("chopped_text_dict", "corrected_text", "correct_text_report"),
            nonempty=("chopped_text_dict", "corrected_text"),
            recovery_adapter=correct_text_stage,
        ),
        FixedStage(
            "segment_blocks",
            "文档结构识别",
            segment_blocks_stage.run,
            requires=("chopped_text_dict",),
            produces=("problem_dict", "segment_blocks_report"),
            nonempty=("problem_dict",),
            recovery_adapter=segment_blocks_stage,
        ),
        FixedStage(
            "extract_statements",
            "数学知识提取",
            extract_statements_stage.run,
            requires=("problem_dict",),
            consumes=("segment_blocks_report", "tex_document_model"),
            produces=("unsplit_statement_dict",),
            nonempty=("unsplit_statement_dict",),
            recovery_adapter=extract_statements_stage,
        ),
        FixedStage(
            "ensure_coverage",
            "遗漏知识补全",
            ensure_coverage_stage.run,
            requires=("corrected_text", "problem_dict", "segment_blocks_report", "unsplit_statement_dict"),
            consumes=("extract_statements_report", "tex_extract_statements_report", "tex_document_model"),
            produces=("unsplit_statement_dict", "ensure_coverage_report"),
            nonempty=("unsplit_statement_dict", "ensure_coverage_report"),
            recovery_adapter=ensure_coverage_stage,
        ),
        FixedStage(
            "clean_nodes",
            "无效内容清理",
            clean_nodes_stage.run,
            requires=("unsplit_statement_dict",),
            produces=("unsplit_statement_dict", "node_cleaning_report"),
            nonempty=("unsplit_statement_dict", "node_cleaning_report"),
            recovery_adapter=clean_nodes_stage,
        ),
        FixedStage(
            "split_nodes",
            "复合知识拆分",
            split_nodes_stage.run,
            requires=("unsplit_statement_dict",),
            produces=("statement_without_title_dict",),
            nonempty=("statement_without_title_dict",),
            recovery_adapter=split_nodes_stage,
        ),
        FixedStage(
            "generate_titles",
            "知识标题生成",
            generate_titles_stage.run,
            requires=("statement_without_title_dict",),
            produces=("structured_input_dict", "definition_axiom_dict"),
            recovery_adapter=generate_titles_stage,
        ),
        FixedStage(
            "extract_logic_tuples",
            "知识要素结构化",
            extract_logic_tuples_stage.run,
            requires=("structured_input_dict", "definition_axiom_dict"),
            produces=("node_dict", "node_list"),
            nonempty=("node_dict", "node_list"),
            recovery_adapter=extract_logic_tuples_stage,
        ),
        FixedStage(
            "analysis",
            "语义信息补充",
            analysis_stage.run,
            requires=("node_dict", "node_list"),
            produces=("node_dict", "node_list", "analysis_stage_run"),
            nonempty=("node_dict", "node_list"),
            recovery_adapter=analysis_stage,
        ),
        FixedStage(
            "repair",
            "知识结构修复",
            repair_stage.run,
            requires=("node_dict", "node_list"),
            produces=("node_dict", "node_list", "repair_stage_run"),
            nonempty=("node_dict", "node_list"),
            recovery_adapter=repair_stage,
        ),
        FixedStage(
            "extract_references",
            "文内引用识别",
            extract_references_stage.run,
            requires=("node_dict", "node_list"),
            produces=("node_dict", "node_list"),
            nonempty=("node_dict", "node_list"),
        ),
        FixedStage(
            "repair_lite",
            "引用结果校正",
            repair_lite_stage.run,
            requires=("node_dict", "node_list"),
            produces=("node_dict", "node_list"),
            nonempty=("node_dict", "node_list"),
        ),
    ]
    if experimental_logic_ir:
        stages.extend(
            [
                FixedStage(
                    "compile_logic_form",
                    "实验旁路：谓词树生成",
                    compile_logic_form_stage.run,
                    requires=("node_dict",),
                    produces=("node_dict", "node_list", "logic_form_local_dict"),
                    nonempty=("node_dict", "node_list"),
                    recovery_adapter=compile_logic_form_stage,
                ),
                FixedStage(
                    "normalize_predicates",
                    "实验旁路：谓词归一化",
                    normalize_predicates_stage.run,
                    requires=("node_dict", "logic_form_local_dict"),
                    produces=("node_dict", "node_list"),
                    nonempty=("node_dict", "node_list"),
                    recovery_adapter=normalize_predicates_stage,
                ),
            ]
        )
    stages.extend(
        [
            FixedStage(
                "build_relations",
                "知识关系提取",
                relation_runner,
                requires=("node_dict", "node_list"),
                produces=("edge_list",),
            ),
            FixedStage(
                "finalize_output",
                "图谱结果生成",
                finalize_output_stage.run,
                requires=("node_list", "edge_list"),
                consumes=("degraded_stage_runs",),
                produces=("node_list", "edge_list"),
                nonempty=("node_list",),
            ),
        ]
    )
    return tuple(stages)


def _legacy_sixteen_stage_plan(
    *,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
) -> tuple[FixedStage, ...]:
    """Return the exact plan contract used before logic IR became experimental."""
    return tuple(
        replace(stage, consumes=("predicate_entry_list",))
        if stage.key == "analysis"
        else stage
        for stage in build_fixed_stage_plan(
            edge_output_mode=edge_output_mode,
            relation_prompt_profile=relation_prompt_profile,
            experimental_logic_ir=True,
        )
    )


FIXED_STAGE_DEFS = tuple(
    (stage.key, stage.label)
    for stage in build_fixed_stage_plan()
)


def execute_fixed_pipeline(
    context,
    state=None,
    *,
    start_stage=None,
    resume_from_cache=False,
    edge_output_mode="structured",
    relation_prompt_profile="graph",
    experimental_logic_ir=False,
    stop_stage=None,
    on_stage_start: StageCallback | None = None,
    on_stage_ready: StageCallback | None = None,
    on_stage_complete: StageCallback | None = None,
):
    if resume_from_cache and (state is not None or start_stage is not None):
        raise ValueError("resume_from_cache cannot be combined with state or start_stage")
    state = dict(state or {})
    plan = build_fixed_stage_plan(
        edge_output_mode=edge_output_mode,
        relation_prompt_profile=relation_prompt_profile,
        experimental_logic_ir=experimental_logic_ir,
    )
    cache = None
    start_index = 0
    if getattr(context, "cache_policy", "legacy") == "minimal":
        cache = PipelineStageCache(
            context,
            plan,
            options={
                "edge_output_mode": edge_output_mode,
                "relation_prompt_profile": relation_prompt_profile,
                "source_format": getattr(context, "source_format", "auto"),
                "source_origin": getattr(context, "source_origin", "markdown"),
            },
        )
        plan_migrations = ()
        if resume_from_cache and not experimental_logic_ir:
            legacy_plan = _legacy_sixteen_stage_plan(
                edge_output_mode=edge_output_mode,
                relation_prompt_profile=relation_prompt_profile,
            )
            shared_stage_keys = tuple(
                stage.key
                for stage in plan
                if stage.key not in {"build_relations", "finalize_output"}
            )
            plan_migrations = (
                {
                    "legacy_plan": legacy_plan,
                    "shared_stage_keys": shared_stage_keys,
                    "reason": "logic_ir_stages_moved_to_experimental_sidecar",
                },
            )
        cache.initialize(
            resume=resume_from_cache,
            plan_migrations=plan_migrations,
        )
        if resume_from_cache:
            state, start_index, _completed = cache.load_resume_state(_assert_state_keys)
            context.resume_task_checkpoints = True
    if start_stage is not None:
        stage_keys = [stage.key for stage in plan]
        if start_stage not in stage_keys:
            raise ValueError(f"Unknown fixed pipeline start stage: {start_stage}")
        start_index = stage_keys.index(start_stage)
    total = len(plan)
    stop_index = total - 1
    if stop_stage is not None:
        stage_keys = [stage.key for stage in plan]
        if stop_stage not in stage_keys:
            raise ValueError(f"Unknown fixed pipeline stop stage: {stop_stage}")
        stop_index = stage_keys.index(stop_stage)
        if stop_index < start_index:
            raise ValueError("stop_stage cannot be earlier than start_stage")
    try:
        for index in range(start_index, stop_index + 1):
            stage = plan[index]
            context.current_stage_key = stage.key
            _assert_state_keys(
                FixedStage(stage.key, stage.label, stage.runner, requires=stage.requires),
                state,
            )
            before_fingerprints = cache.capture_fingerprints(state) if cache else None
            if cache:
                cache.write_stage_input(index, stage, state)
            if on_stage_start:
                on_stage_start(stage, index, total, state)
            baseline_report_path = (
                None
                if getattr(context, "resume_task_checkpoints", False)
                else unresolved_report_path(stage.recovery_adapter, context)
            )
            state = stage.runner(context, state)
            if not isinstance(state, dict):
                raise TypeError(f"Stage {stage.key} must return a state dict")
            state = recover_failed_stage_tasks(
                context,
                state,
                stage.recovery_adapter,
                baseline_report_path=baseline_report_path,
                max_rounds=2,
            )
            _assert_state_keys(stage, state)
            if on_stage_ready:
                on_stage_ready(stage, index, total, state)
            if cache:
                cache.write_stage_output(index, stage, before_fingerprints, state)
                cache.cleanup_work_dir()
            if on_stage_complete:
                on_stage_complete(stage, index, total, state)
        if cache:
            cache.mark_status("done")
            cache.cleanup_work_dir()
        context.current_stage_key = None
        return state
    except BaseException:
        if cache:
            cache.mark_status("error")
            cache.cleanup_work_dir()
        raise


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
    print(f"{file_path} is processing...")
    # Compatibility parameter: the fixed pipeline now always runs analysis and repair.
    _ = enable_analysis
    context = PipelineContext(
        file_path=file_path,
        output_node_path=output_node_path,
        output_edge_path=output_edge_path,
        output_natural_node_path=output_natural_node_path,
        api_url=api_url,
        model_name=model_name,
        api_key=api_key,
        enable_analysis=True,
        enable_math_disambiguation=enable_math_disambiguation,
        ambiguity_table=ambiguity_table,
        source_format=source_format,
        source_origin=source_origin,
        **({"embedding_model_name": embedding_model_name} if embedding_model_name else {}),
    )
    matrix_flow_runner = MatrixFlowRunner(context)
    state = execute_fixed_pipeline(
        context,
        edge_output_mode=edge_output_mode,
        relation_prompt_profile=relation_prompt_profile,
        experimental_logic_ir=experimental_logic_ir,
        on_stage_ready=matrix_flow_runner.on_stage_ready,
    )
    return state["node_list"], state["edge_list"]


__all__ = [
    "FIXED_STAGE_DEFS",
    "FixedStage",
    "build_fixed_stage_plan",
    "execute_fixed_pipeline",
    "process_md",
]
