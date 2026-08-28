import copy
import os

from ...common.io import build_default_analysis_output_path, read_json, save_stage_json, write_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import merge_node_with_source_envelope, structured_parent_view
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    write_failure_report,
)
from .templates import correction_prompt09, data_template09, prompt_template09, validation09

STAGE_NAME = "analysis"

def wrap_nodes_for_analysis(node_list):
    return {i: {"pos1": structured_parent_view(node)} for i, node in enumerate(node_list)}


def normalize_analysis_result(result):
    if not isinstance(result, dict):
        return {"analysis_layer": {}, "repair_suggestion": {}}
    return {
        "analysis_layer": result.get("analysis_layer", {}) or {},
        "repair_suggestion": result.get("repair_suggestion", {}) or {},
    }


def _analysis_result_for_key(analysis_dict, key):
    if not isinstance(analysis_dict, dict):
        return None
    for candidate in (key, str(key)):
        if candidate in analysis_dict:
            return analysis_dict[candidate]
    return None


def attach_analysis_back(node_list, analysis_dict):
    new_nodes = []
    for i, source_node in enumerate(node_list):
        raw_result = _analysis_result_for_key(analysis_dict, i)
        result = normalize_analysis_result(raw_result)
        derived = copy.deepcopy(raw_result) if isinstance(raw_result, dict) else {}
        derived.update(
            {
                "analysis_layer": result["analysis_layer"],
                "repair_suggestion": result["repair_suggestion"],
                "analysis_status": (
                    "completed" if validation09(raw_result) else "failed"
                ),
            }
        )
        node, audit = merge_node_with_source_envelope(
            source_node,
            derived,
            stage_name=STAGE_NAME,
            allowed_fields={
                "analysis_layer",
                "repair_suggestion",
                "analysis_status",
            },
        )
        statuses = dict(node.get("_derivation_status") or {})
        if validation09(raw_result):
            statuses.pop(STAGE_NAME, None)
        else:
            statuses[STAGE_NAME] = {
                "status": "degraded",
                "reason": "unresolved_model_task",
                "task_key": str(i),
            }
        if statuses:
            node["_derivation_status"] = statuses
        else:
            node.pop("_derivation_status", None)
        audits = list(node.get("_source_merge_audits") or [])
        audits.append(audit)
        node["_source_merge_audits"] = audits
        new_nodes.append(node)
    return new_nodes


def sync_node_dict_from_list(node_dict, node_list):
    if not isinstance(node_dict, dict):
        return {i: node for i, node in enumerate(node_list)}

    new_dict = {}
    keys = list(node_dict.keys())
    for index, node in enumerate(node_list):
        key = keys[index] if index < len(keys) else index
        new_dict[key] = node
    return new_dict


def run_analysis_layer(
    node_list,
    *,
    llm,
    parser,
    num_threads,
    checkpoint,
    checkpoint_dir=None,
    debug_output_dir=None,
):
    if not isinstance(node_list, list):
        raise TypeError("node_list 必须是 list")
    if not node_list:
        return []

    analysis_input = wrap_nodes_for_analysis(node_list)
    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            "analysis_input.json",
            analysis_input,
            "Analysis input",
        )

    analysis_result = run_multiprocess_task(
        llm=llm,
        parse_method=parser.parse_dict,
        data_template=data_template09,
        prompt_template=prompt_template09,
        correction_template=correction_prompt09,
        validator=validation09,
        index_dict=analysis_input,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=checkpoint_dir,
    )

    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            "analysis_result.json",
            analysis_result,
            "Analysis result",
        )

    completed_count = len(analysis_result)
    total_count = len(node_list)
    missing_indices = [i for i in range(total_count) if i not in analysis_result]

    if debug_output_dir:
        save_stage_json(
            debug_output_dir,
            "analysis_missing_indices.json",
            missing_indices,
            "Analysis missing indices",
        )

    print(f"🧪 Analysis 阶段结果: 成功 {completed_count}/{total_count}")
    if missing_indices:
        preview = missing_indices[:10]
        suffix = " ..." if len(missing_indices) > 10 else ""
        print(f"⚠️ Analysis 缺失索引: {preview}{suffix}")

    if total_count > 0 and completed_count == 0:
        raise RuntimeError(
            "Analysis 阶段没有返回任何有效结果。"
            f" 调试文件已写入: {debug_output_dir or checkpoint_dir or '当前目录'}"
        )

    return attach_analysis_back(node_list, analysis_result)


def _run_analysis_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data_template09,
        prompt_template=prompt_template09,
        correction_template=correction_prompt09,
        validator=validation09,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def latest_unresolved_failure_report(context):
    return _latest_unresolved_failure_report(context, STAGE_NAME)


def run(context, state):
    checkpoint_dir = os.path.join(context.output_dir, "analysis_checkpoint")
    debug_output_dir = os.path.join(context.output_dir, "analysis_debug")
    analysis_input = wrap_nodes_for_analysis(state["node_list"])
    save_stage_json(debug_output_dir, "analysis_input.json", analysis_input, "Analysis input")
    analysis_result, failure_report, run_dir = run_recoverable_task(
        context,
        stage_name=STAGE_NAME,
        input_dict=analysis_input,
        task_runner=lambda index_dict, checkpoint_dir: _run_analysis_tasks(context, index_dict, checkpoint_dir),
    )
    save_stage_json(debug_output_dir, "analysis_result.json", analysis_result, "Analysis result")
    if failure_report.get("status") != "resolved":
        state["analysis_stage_run"] = failure_report
        if getattr(context, "execution_mode", "pipeline") != "pipeline":
            return state
    node_list = attach_analysis_back(state["node_list"], analysis_result)
    failure_report = write_failure_report(
        run_dir,
        run_dir.name,
        STAGE_NAME,
        [str(key) for key in analysis_input.keys()],
        analysis_result,
        attempts=1,
        canonical_updated=True,
    )
    state["analysis_stage_run"] = failure_report
    state["node_list"] = node_list
    state["node_dict"] = sync_node_dict_from_list(state.get("node_dict"), node_list)
    return state


def rerun_failed_tasks(context, state, max_rounds=2):
    analysis_input = wrap_nodes_for_analysis(state["node_list"])
    analysis_result, failure_report, run_dir = rerun_unresolved_task_report(
        context,
        stage_name=STAGE_NAME,
        task_runner=lambda index_dict, checkpoint_dir: _run_analysis_tasks(context, index_dict, checkpoint_dir),
        max_rounds=max_rounds,
    )
    if failure_report.get("status") != "resolved":
        state["analysis_stage_run"] = failure_report
        return state, failure_report
    node_list = attach_analysis_back(state["node_list"], analysis_result)
    failure_report = write_failure_report(
        run_dir,
        run_dir.name,
        STAGE_NAME,
        [str(key) for key in analysis_input.keys()],
        analysis_result,
        attempts=failure_report.get("attempt_rounds") or 1,
        canonical_updated=True,
    )
    state["analysis_stage_run"] = failure_report
    state["node_list"] = node_list
    state["node_dict"] = sync_node_dict_from_list(state.get("node_dict"), node_list)
    return state, {**failure_report, "status": "resolved", "canonical_updated": True}


def process_node_file(
    input_node_path,
    *,
    llm,
    parser,
    num_threads,
    checkpoint,
    output_node_path=None,
):
    input_node_path = os.path.abspath(input_node_path)
    node_list = read_json(input_node_path)
    checkpoint_dir = os.path.join(os.path.dirname(input_node_path), "analysis_checkpoint")
    debug_output_dir = os.path.join(os.path.dirname(input_node_path), "analysis_debug")
    enhanced_nodes = run_analysis_layer(
        node_list,
        llm=llm,
        parser=parser,
        num_threads=num_threads,
        checkpoint=checkpoint,
        checkpoint_dir=checkpoint_dir,
        debug_output_dir=debug_output_dir,
    )
    if output_node_path:
        write_json(output_node_path, enhanced_nodes)
        print(f"✅ Analysis node JSON saved to: {output_node_path}")
    return enhanced_nodes


__all__ = [
    "attach_analysis_back",
    "build_default_analysis_output_path",
    "process_node_file",
    "run_analysis_layer",
]
