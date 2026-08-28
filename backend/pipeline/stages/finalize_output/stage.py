from ...common.io import save_stage_json, write_json
from ...common.node import (
    SOURCE_ENVELOPE_KEY,
    SOURCE_ENVELOPE_SCHEMA_VERSION,
    compute_global_id_from_source,
    get_node_label,
    get_node_node_type,
    get_node_source_original_text,
    merge_node_with_source_envelope,
    normalize_source_text_for_id,
)


EDGE_START_KEYS = ("出发节点", "from", "source", "source_id")
EDGE_END_KEYS = ("到达节点", "to", "target", "target_id")


# These fields support upstream analysis, repair, reference matching, or logic
# compilation. Their useful results have already been folded into the node's
# canonical fields by the time finalize_output runs.
FINAL_OUTPUT_TRANSIENT_NODE_KEYS = frozenset(
    {
        "_orig_key",
        "_reorder_id",
        "_derivation_status",
        "_source_merge_audits",
        SOURCE_ENVELOPE_KEY,
        "analysis_layer",
        "analysis_status",
        "formalization_guidance",
        "locator",
        "logic_ast_local",
        "predicate_entries",
        "reference_aliases",
        "reference_signals",
        "repair_log",
        "repair_status",
        "repair_suggestion",
        "statement_form_before_repair",
        "statement_form_repair",
        "statement_form_repair_evidence",
        "subnode_specs",
        "surface_anchor",
        "coverage_recovered",
        "coverage_source_only",
    }
)


def _first_present(data, keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return ""


def validate_and_deduplicate_nodes(node_list):
    kept_nodes = []
    seen = {}
    duplicate_records = []

    for index, node in enumerate(node_list or []):
        if not isinstance(node, dict):
            raise ValueError(f"Final node at index {index} is not an object")

        expected_global_id = compute_global_id_from_source(node)
        actual_global_id = str(node.get("global_id") or "").strip()
        if actual_global_id != expected_global_id:
            raise ValueError(
                "Final node global_id does not match its authoritative source text: "
                f"index={index}, label={get_node_label(node)!r}, "
                f"expected={expected_global_id}, actual={actual_global_id or '<missing>'}"
            )

        normalized_source = normalize_source_text_for_id(get_node_source_original_text(node))
        previous = seen.get(actual_global_id)
        if previous is None:
            kept_index = len(kept_nodes)
            kept_nodes.append(node)
            seen[actual_global_id] = {
                "input_index": index,
                "kept_index": kept_index,
                "normalized_source": normalized_source,
                "node": node,
            }
            continue

        if previous["normalized_source"] != normalized_source:
            raise RuntimeError(
                "MD5 collision detected for different authoritative source texts: "
                f"global_id={actual_global_id}, first_index={previous['input_index']}, "
                f"second_index={index}"
            )

        kept_node = previous["node"]
        duplicate_records.append(
            {
                "global_id": actual_global_id,
                "kept_input_index": previous["input_index"],
                "kept_label": get_node_label(kept_node),
                "kept_node_type": get_node_node_type(kept_node),
                "dropped_input_index": index,
                "dropped_label": get_node_label(node),
                "dropped_node_type": get_node_node_type(node),
            }
        )

    report = {
        "schema_version": 1,
        "input_node_count": len(node_list or []),
        "output_node_count": len(kept_nodes),
        "duplicate_node_count": len(duplicate_records),
        "duplicates": duplicate_records,
    }
    return kept_nodes, report


def validate_edge_endpoints(edge_list, node_list):
    node_ids = {str(node.get("global_id") or "").strip() for node in node_list}
    for index, edge in enumerate(edge_list or []):
        if not isinstance(edge, dict):
            raise ValueError(f"Final edge at index {index} is not an object")
        start = str(_first_present(edge, EDGE_START_KEYS)).strip()
        end = str(_first_present(edge, EDGE_END_KEYS)).strip()
        if not start or not end:
            raise ValueError(f"Final edge at index {index} is missing an endpoint")
        if start not in node_ids or end not in node_ids:
            raise ValueError(
                "Final edge references an unknown global_id: "
                f"index={index}, start={start!r}, end={end!r}"
            )


def build_source_envelope_quality_report(node_list, degraded_stage_runs=None):
    degraded_nodes = []
    ignored_model_fields = []
    source_only_nodes = []
    envelope_count = 0

    for index, node in enumerate(node_list or []):
        if not isinstance(node, dict):
            raise ValueError(f"Final node at index {index} is not an object")
        merge_node_with_source_envelope(
            node,
            {},
            stage_name="finalize_output",
            allowed_fields=(),
        )
        envelope = node.get(SOURCE_ENVELOPE_KEY)
        if not isinstance(envelope, dict):
            raise ValueError(
                "Final node is missing source envelope; rerun from "
                f"extract_statements: index={index}"
            )
        if envelope.get("schema_version") != SOURCE_ENVELOPE_SCHEMA_VERSION:
            raise ValueError(
                "Final node has an unsupported source envelope schema: "
                f"index={index}, schema={envelope.get('schema_version')!r}"
            )

        envelope_node = {
            field_name: envelope.get(field_name)
            for field_name in (
                "node_type",
                "content",
                "source_original_form",
                "proof",
                "label",
            )
            if envelope.get(field_name) is not None
        }
        expected_global_id = compute_global_id_from_source(envelope_node)
        if envelope.get("global_id") != expected_global_id:
            raise ValueError(
                "Final source envelope global_id mismatch: "
                f"index={index}, expected={expected_global_id}, "
                f"actual={envelope.get('global_id')!r}"
            )
        if node.get("global_id") != expected_global_id:
            raise ValueError(
                "Final node no longer matches its source envelope: "
                f"index={index}, global_id={node.get('global_id')!r}"
            )
        envelope_count += 1

        global_id = str(node.get("global_id") or "")
        derivation_status = node.get("_derivation_status")
        if isinstance(derivation_status, dict):
            for stage_name, status in derivation_status.items():
                if not isinstance(status, dict) or status.get("status") != "degraded":
                    continue
                degraded_nodes.append(
                    {
                        "index": index,
                        "global_id": global_id,
                        "stage": str(stage_name),
                        "task_key": status.get("task_key"),
                        "reason": status.get("reason", "unresolved_model_task"),
                    }
                )

        for audit in node.get("_source_merge_audits") or []:
            if not isinstance(audit, dict) or not audit.get("ignored_fields"):
                continue
            ignored_model_fields.append(
                {
                    "index": index,
                    "global_id": global_id,
                    "stage": str(audit.get("stage") or "unknown"),
                    "fields": sorted(str(field) for field in audit["ignored_fields"]),
                }
            )

        if node.get("coverage_source_only"):
            source_only_nodes.append(
                {
                    "index": index,
                    "global_id": global_id,
                    "label": get_node_label(node),
                }
            )

    degraded_runs = {
        str(stage): report
        for stage, report in (degraded_stage_runs or {}).items()
        if isinstance(report, dict)
    }
    status = (
        "degraded"
        if degraded_runs or degraded_nodes or source_only_nodes or ignored_model_fields
        else "ok"
    )
    warnings = [
        (
            f"Stage {stage} completed in degraded mode with "
            f"{int(report.get('failed_task_count') or 0)} unresolved task(s)."
        )
        for stage, report in sorted(degraded_runs.items())
    ]
    if source_only_nodes:
        warnings.append(
            f"{len(source_only_nodes)} coverage candidate(s) were retained as source-only nodes."
        )
    if ignored_model_fields:
        ignored_count = sum(len(item["fields"]) for item in ignored_model_fields)
        warnings.append(
            f"Ignored {ignored_count} protected field(s) returned by model stages."
        )

    report = {
        "schema_version": 1,
        "status": status,
        "node_count": len(node_list or []),
        "validated_envelope_count": envelope_count,
        "degraded_stage_count": len(degraded_runs),
        "degraded_node_count": len(
            {
                (item["index"], item["global_id"])
                for item in degraded_nodes
            }
        ),
        "degraded_derivation_count": len(degraded_nodes),
        "source_only_node_count": len(source_only_nodes),
        "ignored_protected_field_count": sum(
            len(item["fields"]) for item in ignored_model_fields
        ),
        "degraded_stage_runs": degraded_runs,
        "degraded_nodes": degraded_nodes,
        "source_only_nodes": source_only_nodes,
        "ignored_model_fields": ignored_model_fields,
        "blocking_issues": [],
        "warnings": warnings,
    }
    return report


def clean_node_for_final_output(node):
    """Remove stage-only fields from a final node and its materialized subnodes."""
    for key in FINAL_OUTPUT_TRANSIENT_NODE_KEYS:
        node.pop(key, None)

    sub_nodes = node.get("sub_nodes")
    if isinstance(sub_nodes, list):
        for sub_node in sub_nodes:
            if isinstance(sub_node, dict):
                clean_node_for_final_output(sub_node)
    return node


def run(context, state):
    restored_node_list = []
    for node in state["node_list"]:
        restored, _ = merge_node_with_source_envelope(
            node,
            {},
            stage_name="finalize_output",
            allowed_fields=(),
        )
        restored_node_list.append(restored)
    node_list, dedup_report = validate_and_deduplicate_nodes(restored_node_list)
    edge_list = state["edge_list"]
    validate_edge_endpoints(edge_list, node_list)
    quality_report = build_source_envelope_quality_report(
        node_list,
        state.get("degraded_stage_runs"),
    )

    save_stage_json(
        context.output_dir,
        "global_id_dedup_report.json",
        dedup_report,
        "Global ID deduplication report",
    )
    save_stage_json(
        context.output_dir,
        "source_envelope_quality_report.json",
        quality_report,
        "Source envelope quality report",
    )

    for node in node_list:
        clean_node_for_final_output(node)

    if context.output_node_path:
        write_json(context.output_node_path, node_list)
        print(f"✅ Node JSON saved to: {context.output_node_path}")

    if context.output_edge_path:
        write_json(context.output_edge_path, edge_list)
        print(f"✅ Edge JSON saved to: {context.output_edge_path}")

    state["node_list"] = node_list
    state["node_dict"] = {index: node for index, node in enumerate(node_list)}
    state["edge_list"] = edge_list
    state["source_envelope_quality_report"] = quality_report
    state["pipeline_warnings"] = list(quality_report["warnings"])
    state["quality_summary"] = {
        "status": quality_report["status"],
        "degraded_stage_count": quality_report["degraded_stage_count"],
        "degraded_node_count": quality_report["degraded_node_count"],
        "ignored_protected_field_count": quality_report[
            "ignored_protected_field_count"
        ],
    }
    return state
