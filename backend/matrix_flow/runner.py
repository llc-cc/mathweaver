"""MatrixFlow sidecar runner and fixed-pipeline integration boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import (
    MATRIX_FLOW_PARSER_VERSION,
    MATRIX_FLOW_SCHEMA_VERSION,
    MATRIX_FLOW_VERIFIER_VERSION,
)
from .parser import parse_matrix_owner
from .verifier import verify_flows


SIDECAR_SCHEMA_VERSION = 2
SIDECAR_DIR_NAME = "_matrix_flow"
SIDECAR_MANIFEST = "manifest.json"
SIDECAR_FLOWS = "flows.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_hash(value: Any) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "original_form", "source_original_form", "content"):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, list):
        return "\n".join(item for item in (_text(entry) for entry in value) if item)
    return ""


def _source_span(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    start = value.get("start")
    end = value.get("end")
    if isinstance(start, int) and isinstance(end, int):
        return {"start": start, "end": end}
    return {}


def _sidecar_root(context) -> Path:
    stage_cache_dir = Path(
        getattr(context, "stage_cache_dir", getattr(context, "output_dir", "."))
    ).resolve()
    return stage_cache_dir.parent / SIDECAR_DIR_NAME


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _owner_fields(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract source-backed statement/proof fields without using model rewrites."""

    result = []
    wrappers = state.get("unsplit_statement_dict") or {}
    if not isinstance(wrappers, dict):
        return result
    for fallback_key, wrapper in wrappers.items():
        if not isinstance(wrapper, dict):
            continue
        node = wrapper.get("pos1") if isinstance(wrapper.get("pos1"), dict) else {}
        envelope = node.get("_source_envelope") if isinstance(node.get("_source_envelope"), dict) else {}
        global_id = str(node.get("global_id") or envelope.get("global_id") or "").strip()
        source_block_key = wrapper.get("source_block_key", wrapper.get("_orig_key", fallback_key))
        owner = {
            "global_id": global_id,
            "source_block_key": str(source_block_key),
        }
        base_span = _source_span(node.get("source_span"))
        raw_source = _text(wrapper.get("source_text"))
        source_original_form = _text(node.get("source_original_form")) or _text(envelope.get("source_original_form"))
        content_text = source_original_form or _text(envelope.get("content")) or _text(node.get("content"))
        proof_text = _text(envelope.get("proof")) or _text(node.get("proof"))
        proof_start = raw_source.find(proof_text) if raw_source and proof_text else -1
        is_tex_owner = bool(node.get("tex_env_name")) or str(node.get("source_file") or "").lower().endswith(".tex")
        if is_tex_owner:
            source_statement = content_text or (raw_source[:proof_start] if proof_start >= 0 else raw_source)
        else:
            source_statement = (
                content_text
                if content_text and (not raw_source or content_text in raw_source)
                else raw_source[:proof_start] if proof_start >= 0 else raw_source
            )
        source_proof = proof_text
        fields = (
            ("statement", source_statement, base_span),
            ("proof", source_proof, _source_span(node.get("proof_source_span")) or base_span),
        )
        for field, value, span in fields:
            if not value.strip():
                continue
            result.append({
                "owner": owner,
                "field": field,
                "source_span": span,
                "text": value,
            })
    return result


class MatrixFlowRunner:
    """Run the seven-stage Markdown sidecar without changing fixed stages."""

    def __init__(self, context):
        self.context = context
        self.root = _sidecar_root(context)
        self.manifest_path = self.root / SIDECAR_MANIFEST
        self.flows_path = self.root / SIDECAR_FLOWS

    def _cache_key(self, scopes: list[dict[str, Any]]) -> tuple[str, str]:
        source_hash = _sha256_file(self.context.file_path)
        scope_fingerprint = _json_hash([
            {
                "global_id": item["owner"].get("global_id"),
                "source_block_key": item["owner"].get("source_block_key"),
                "field": item["field"],
                "source_span": item.get("source_span") or {},
                "text_sha256": _sha256_text(item["text"]),
            }
            for item in scopes
        ])
        payload = {
            "source_sha256": source_hash,
            "owner_scopes_sha256": scope_fingerprint,
            "model_hash": os.getenv("MATRIX_FLOW_MODEL_HASH", "none"),
            "parser_version": MATRIX_FLOW_PARSER_VERSION,
            "schema_version": MATRIX_FLOW_SCHEMA_VERSION,
            "verifier_version": MATRIX_FLOW_VERIFIER_VERSION,
            "source_origin": getattr(self.context, "source_origin", "markdown"),
        }
        return _json_hash(payload), source_hash

    def _failed(self, exc: Exception, *, cache_key: str | None = None) -> dict[str, Any]:
        report = {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "status": "failed",
            "cache_key": cache_key,
            "flow_count": 0,
            "warnings": [f"MatrixFlow side pipeline failed: {exc}"],
            "stages": {},
        }
        try:
            _atomic_json(self.manifest_path, report)
        except Exception:
            # A sidecar write failure must never become a fixed-pipeline error.
            pass
        return report

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Process ensure_coverage output and persist independent artifacts."""

        scopes = _owner_fields(state)
        cache_key, source_hash = self._cache_key(scopes)
        try:
            if self.manifest_path.exists() and self.flows_path.exists():
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if manifest.get("status") == "completed" and manifest.get("cache_key") == cache_key:
                    flows = json.loads(self.flows_path.read_text(encoding="utf-8"))
                    if isinstance(flows, list):
                        report = {**manifest, "reused": True, "flow_count": len(flows)}
                        state["matrix_flow_report"] = report
                        return report

            self.root.mkdir(parents=True, exist_ok=True)
            stage_reports: dict[str, Any] = {}
            stage_reports["scope_matrix_content"] = {
                "status": "completed",
                "owner_scope_count": len(scopes),
            }
            _atomic_json(self.root / "scope_matrix_content.json", scopes)

            grouped: dict[str, dict[str, Any]] = {}
            for scope in scopes:
                global_id = str(scope["owner"].get("global_id") or "").strip()
                source_block_key = str(scope["owner"].get("source_block_key") or "")
                key = global_id or f"source-block:{source_block_key}"
                group = grouped.setdefault(key, {
                    "owner": {
                        **scope["owner"],
                        "source_span": scope.get("source_span") or {},
                    },
                    "fields": {"statement": "", "proof": ""},
                })
                group["fields"][scope["field"]] = scope["text"]

            drafts: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            parse_counts = {"strict": 0, "tolerant": 0, "rejected": 0, "transformation": 0, "named_matrix": 0}
            source_origin = getattr(self.context, "source_origin", "markdown")
            for group in grouped.values():
                parsed = parse_matrix_owner(
                    group["fields"],
                    owner=group["owner"],
                    document_hash=source_hash,
                    source_origin=source_origin,
                )
                drafts.extend(parsed["flows"])
                for item in parsed["rejected"]:
                    rejected.append({"owner": group["owner"], **item})
                for key, value in parsed["counts"].items():
                    parse_counts[key] = parse_counts.get(key, 0) + value
            stage_reports["collect_source_evidence"] = {
                "status": "completed",
                "source_kind": source_origin,
                "evidence_count": 0,
            }
            stage_reports["detect_flow_groups"] = {
                "status": "completed",
                "candidate_flow_count": len(drafts),
                "rejected_candidate_count": len(rejected),
            }
            stage_reports["parse_flow_candidates"] = {
                "status": "completed",
                **parse_counts,
            }
            _atomic_json(self.root / "candidate_flows.json", drafts)

            resolved = verify_flows(drafts)
            stage_reports["resolve_candidates"] = {
                "status": "completed",
                "resolution": "deterministic_strict_and_tolerant",
            }
            stage_reports["verify_transforms"] = {
                "status": "completed",
                "status_counts": {
                    status: sum(1 for flow in resolved if flow.get("verification", {}).get("status") == status)
                    for status in ("verified", "indeterminate", "contradicted", "structural_invalid")
                },
            }
            _atomic_json(self.flows_path, resolved)
            review_artifact = {
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "counts": parse_counts,
                "rejected_candidates": rejected,
                "flows": [
                    {
                        "id": flow.get("id"),
                        "owner": flow.get("owner"),
                        "verification": flow.get("verification"),
                        "review": flow.get("review"),
                        "node_count": len(flow.get("nodes") or []),
                        "edge_count": len(flow.get("edges") or []),
                        "role": flow.get("role", "transformation"),
                        "recovery_actions": (flow.get("source") or {}).get("recovery_actions") or [],
                    }
                    for flow in resolved
                ],
            }
            _atomic_json(self.root / "review_artifact.json", review_artifact)
            stage_reports["build_review_artifact"] = {"status": "completed"}
            stage_reports["mount_final_nodes"] = {"status": "pending_finalize_output"}
            report = {
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "status": "completed",
                "cache_key": cache_key,
                "source_sha256": source_hash,
                "flow_count": len(resolved),
                "counts": parse_counts,
                "warnings": [],
                "stages": stage_reports,
                "reused": False,
            }
            _atomic_json(self.manifest_path, report)
            state["matrix_flow_report"] = report
            return report
        except Exception as exc:
            report = self._failed(exc, cache_key=cache_key)
            state["matrix_flow_report"] = report
            state.setdefault("pipeline_warnings", []).extend(report["warnings"])
            return report

    def mount(self, state: dict[str, Any]) -> dict[str, Any]:
        """Attach safe flow JSON to sealed final nodes after finalize_output."""

        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            flows = json.loads(self.flows_path.read_text(encoding="utf-8"))
            if manifest.get("status") != "completed" or not isinstance(flows, list):
                return state
        except (OSError, ValueError, TypeError):
            return state

        by_owner: dict[str, list[dict[str, Any]]] = {}
        for flow in flows:
            if not isinstance(flow, dict):
                continue
            owner = flow.get("owner") or {}
            global_id = str(owner.get("global_id") or "").strip()
            if global_id:
                by_owner.setdefault(global_id, []).append(copy.deepcopy(flow))

        missing = []
        for node in state.get("node_list") or []:
            if not isinstance(node, dict):
                continue
            global_id = str(node.get("global_id") or "").strip()
            if global_id in by_owner:
                node["matrix_flows"] = by_owner[global_id]
        mounted_ids = {
            flow_id
            for node in state.get("node_list") or []
            for flow_id in [flow.get("id") for flow in node.get("matrix_flows", []) or []]
            if flow_id
        }
        for flow in flows:
            if isinstance(flow, dict) and flow.get("id") not in mounted_ids:
                missing.append(flow.get("id"))
        if missing:
            state.setdefault("pipeline_warnings", []).append(
                f"{len(missing)} MatrixFlow artifact(s) could not be mounted to a final node."
            )
        output_node_path = getattr(self.context, "output_node_path", None)
        if output_node_path:
            _atomic_json(Path(output_node_path), state.get("node_list") or [])
        state["node_dict"] = {
            index: node
            for index, node in enumerate(state.get("node_list") or [])
        }
        report = dict(state.get("matrix_flow_report") or manifest)
        report["stages"] = {
            **(report.get("stages") or {}),
            "mount_final_nodes": {
                "status": "completed",
                "mounted_flow_count": len(mounted_ids),
                "unmounted_flow_count": len(missing),
            },
        }
        state["matrix_flow_report"] = report
        try:
            _atomic_json(self.manifest_path, report)
        except (OSError, TypeError, ValueError) as exc:
            state.setdefault("pipeline_warnings", []).append(
                f"MatrixFlow mount report could not be persisted: {exc}"
            )
        return state

    def on_stage_ready(self, stage, _index: int, _total: int, state: dict[str, Any]) -> None:
        """Callback suitable for ``execute_fixed_pipeline``."""

        try:
            if stage.key == "ensure_coverage":
                self.run(state)
            elif stage.key == "finalize_output":
                self.mount(state)
        except Exception as exc:
            report = self._failed(exc)
            state["matrix_flow_report"] = report
            state.setdefault("pipeline_warnings", []).extend(report["warnings"])


__all__ = ["MatrixFlowRunner", "SIDECAR_DIR_NAME"]
