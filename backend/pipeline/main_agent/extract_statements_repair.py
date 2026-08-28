import copy
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..common.claude_cli_engine import ClaudeCliEngine
from ..common.io import write_json
from ..stages.extract_statements.stage import extract_nonempty_blocks, repair_missing_labels_from_problem_dict
from .control import _agent_state_dir, _read_json_optional


REPAIR_ROOT = "repair_candidates"
REPAIR_HISTORY_FILENAME = "repair_history.json"
REPAIR_BACKUP_DIRNAME = "cache_backups"
LOCAL_CONTEXT_RADIUS = 3
LOCAL_CONTEXT_CLUSTER_GAP = 8
NODE_FIELDS = ("node_type", "content", "proof", "label")
ALLOWED_NODE_TYPES = (
    "公理", "定义", "性质", "定理", "例子", "引理", "推论", "反例", "习题", "注释",
    "axiom", "definition", "property", "theorem", "example", "lemma", "corollary",
    "counterexample", "exercise", "remark",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _safe_sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _clean_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value.replace('r"""', "").replace('"""', "")


def _normalized_anchor(value):
    return re.sub(r"\s+", "", _clean_text(value)).lower()


def _stable_fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _node_payload(wrapper):
    if not isinstance(wrapper, dict):
        return None
    if any(key in wrapper for key in NODE_FIELDS):
        return wrapper
    node = wrapper.get("pos1")
    return node if isinstance(node, dict) else None


class ExtractStatementsRepair:
    def __init__(self, context):
        self.context = context
        self.cache_dir = Path(context.output_dir)
        self.root = _agent_state_dir(context) / REPAIR_ROOT / "extract_statements"
        self.root.mkdir(parents=True, exist_ok=True)

    def locate_context(self, repair_intent):
        intent = self._validate_intent(repair_intent)
        intent_fingerprint = self.intent_fingerprint(intent)
        request_fingerprint = self.request_fingerprint(intent)
        if intent.get("force_new_attempt") is not True:
            existing = self._find_by_request_fingerprint(request_fingerprint)
            if existing is not None:
                repair_dir = Path(existing["repair_dir"])
                source_packet = self._required_json(repair_dir / "source_packet.json")
                return {
                    "command": "locate-repair-context",
                    "repair_id": existing["repair_id"],
                    "repair_dir": str(repair_dir),
                    "source_packet_path": str(repair_dir / "source_packet.json"),
                    "source_packet": source_packet,
                    "intent_fingerprint": intent_fingerprint,
                    "request_fingerprint": request_fingerprint,
                    "reused_existing": True,
                }
        repair_id = self._new_repair_id()
        repair_dir = self.root / repair_id
        repair_dir.mkdir(parents=True, exist_ok=False)
        source_packet = self._build_source_packet(intent, repair_id)
        write_json(str(repair_dir / "repair_intent.json"), intent)
        write_json(str(repair_dir / "source_packet.json"), source_packet)
        self._write_report(
            repair_dir,
            {
                "repair_id": repair_id,
                "status": "context_located",
                "intent_fingerprint": intent_fingerprint,
                "request_fingerprint": request_fingerprint,
                "engine": None,
                "source_block_key": intent["source_block_key"],
                "context_source": source_packet["context_source"],
                "context_count": len(source_packet["contexts"]),
                "requires_main_agent_confirmation": source_packet["requires_main_agent_confirmation"],
            },
        )
        return {
            "command": "locate-repair-context",
            "repair_id": repair_id,
            "repair_dir": str(repair_dir),
            "source_packet_path": str(repair_dir / "source_packet.json"),
            "source_packet": source_packet,
            "intent_fingerprint": intent_fingerprint,
            "request_fingerprint": request_fingerprint,
            "reused_existing": False,
        }

    def build_prompt(self, repair_id):
        repair_dir = self._repair_dir(repair_id)
        source_packet = self._required_json(repair_dir / "source_packet.json")
        prompts = []
        for index, context in enumerate(source_packet["contexts"], start=1):
            prompt = self._render_prompt(source_packet, context)
            prompt_path = repair_dir / f"repair_prompt_{index:04d}.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            prompts.append(
                {
                    "context_id": context["context_id"],
                    "path": str(prompt_path),
                    "chars": len(prompt),
                }
            )
        combined = "\n\n".join(
            f"# Context {item['context_id']}\n\n{Path(item['path']).read_text(encoding='utf-8')}"
            for item in prompts
        )
        (repair_dir / "repair_prompt.md").write_text(combined, encoding="utf-8")
        write_json(str(repair_dir / "repair_prompts.json"), {"repair_id": repair_id, "prompts": prompts})
        return {
            "command": "build-repair-prompt",
            "repair_id": repair_id,
            "repair_prompt_path": str(repair_dir / "repair_prompt.md"),
            "prompts": prompts,
        }

    def rerun(self, repair_intent):
        located = self.locate_context(repair_intent)
        repair_id = located["repair_id"]
        repair_dir = self._repair_dir(repair_id)
        existing_report = _read_json_optional(repair_dir / "repair_report.json", {})
        if isinstance(existing_report, dict) and existing_report.get("status") in {"candidate_generated", "applied"}:
            candidate_path = repair_dir / "candidate_unsplit_statement_dict.json"
            candidate = _read_json_optional(candidate_path, {})
            return {
                "command": "rerun-extract-statements",
                "repair_id": repair_id,
                "repair_dir": str(repair_dir),
                "engine": existing_report.get("engine"),
                "candidate_node_count": len(candidate) if isinstance(candidate, dict) else 0,
                "candidate_path": str(candidate_path),
                "review_packet_manifest": str(repair_dir / "candidate_review_packet" / "manifest.json"),
                "report_path": str(repair_dir / "repair_report.json"),
                "intent_fingerprint": located["intent_fingerprint"],
                "request_fingerprint": located["request_fingerprint"],
                "reused_existing": True,
            }
        source_packet = self._required_json(repair_dir / "source_packet.json")
        if (
            source_packet["requires_main_agent_confirmation"]
            and source_packet["repair_intent"].get("allow_full_problem_block_fallback") is not True
        ):
            raise ValueError(
                "Localized repair context could not be found. "
                "Review source_packet.json and explicitly set allow_full_problem_block_fallback=true to use the full problem block."
            )
        prompt_result = self.build_prompt(repair_id)
        raw_by_context = {}

        for index, prompt_fact in enumerate(prompt_result["prompts"], start=1):
            prompt = Path(prompt_fact["path"]).read_text(encoding="utf-8")
            raw_by_context[prompt_fact["context_id"]] = self._execute_prompt(
                prompt,
                repair_dir / "engine_runs" / f"context_{index:04d}",
            )

        write_json(str(repair_dir / "candidate_raw.json"), raw_by_context)
        candidate = self._normalize_candidate(raw_by_context, source_packet["source_block_key"])
        localized_source_text = "\n".join(
            str(context.get("localized_source_text") or "")
            for context in source_packet.get("contexts", [])
        ).strip()
        problem_dict = {
            source_packet["source_block_key"]: {"pos1": localized_source_text}
        } if localized_source_text else _read_json_optional(self.cache_dir / "problem_dict.json", {})
        candidate, repaired_count = repair_missing_labels_from_problem_dict(candidate, problem_dict)
        write_json(str(repair_dir / "candidate_unsplit_statement_dict.json"), candidate)
        review = self.build_candidate_review_packet(repair_id)
        self._write_report(
            repair_dir,
            {
                "repair_id": repair_id,
                "status": "candidate_generated",
                "intent_fingerprint": located["intent_fingerprint"],
                "request_fingerprint": located["request_fingerprint"],
                "engine": getattr(self.context, "llm_engine", "api"),
                "source_block_key": source_packet["source_block_key"],
                "context_source": source_packet["context_source"],
                "context_count": len(source_packet["contexts"]),
                "candidate_node_count": len(candidate),
                "label_repair_count": repaired_count,
                "requires_main_agent_confirmation": source_packet["requires_main_agent_confirmation"],
            },
        )
        return {
            "command": "rerun-extract-statements",
            "repair_id": repair_id,
            "repair_dir": str(repair_dir),
            "engine": getattr(self.context, "llm_engine", "api"),
            "candidate_node_count": len(candidate),
            "candidate_path": str(repair_dir / "candidate_unsplit_statement_dict.json"),
            "review_packet_manifest": review["manifest"]["manifest_path"],
            "report_path": str(repair_dir / "repair_report.json"),
            "intent_fingerprint": located["intent_fingerprint"],
            "request_fingerprint": located["request_fingerprint"],
            "reused_existing": False,
        }

    def build_candidate_review_packet(self, repair_id):
        repair_dir = self._repair_dir(repair_id)
        source_packet = self._required_json(repair_dir / "source_packet.json")
        candidate = self._required_json(repair_dir / "candidate_unsplit_statement_dict.json")
        packet_dir = repair_dir / "candidate_review_packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for context in source_packet["contexts"]:
            records.append(
                {
                    "context_id": context["context_id"],
                    "matched_anchors": context["matched_anchors"],
                    "localized_source_text": context["localized_source_text"],
                    "current_extracted_nodes": source_packet["current_extracted_nodes"],
                    "candidate_nodes": [
                        {"node_index": str(key), **(_node_payload(wrapper) or {})}
                        for key, wrapper in sorted(candidate.items(), key=lambda item: _safe_sort_key(item[0]))
                    ],
                }
            )
        chunk_path = packet_dir / "chunk_0001.json"
        write_json(
            str(chunk_path),
            {
                "schema_version": 1,
                "packet_type": "extract_statements_repair_candidate_review",
                "repair_id": repair_id,
                "source_block_key": source_packet["source_block_key"],
                "records": records,
            },
        )
        manifest = {
            "schema_version": 1,
            "packet_type": "extract_statements_repair_candidate_review",
            "stage": "extract_statements",
            "repair_id": repair_id,
            "generated_at": utc_now(),
            "source_block_key": source_packet["source_block_key"],
            "candidate_node_count": len(candidate),
            "manifest_path": str(packet_dir / "manifest.json"),
            "chunks": [{"path": str(chunk_path), "record_count": len(records)}],
        }
        write_json(manifest["manifest_path"], manifest)
        return {"command": "build-candidate-review-packet", "repair_id": repair_id, "manifest": manifest}

    def apply(self, repair_id, decision):
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            raise ValueError("apply-repair requires a decision with approved=true")
        if decision.get("stage") != "extract_statements" or decision.get("decision") != "apply_repair":
            raise ValueError("apply-repair requires stage=extract_statements and decision=apply_repair")
        if str(decision.get("repair_id")) != str(repair_id):
            raise ValueError("apply decision repair_id does not match requested repair_id")
        repair_dir = self._repair_dir(repair_id)
        source_packet = self._required_json(repair_dir / "source_packet.json")
        candidate = self._required_json(repair_dir / "candidate_unsplit_statement_dict.json")
        canonical_path = self.cache_dir / "unsplit_statement_dict.json"
        canonical = _read_json_optional(canonical_path, {})
        if not isinstance(canonical, dict):
            canonical = {}

        source_key = str(source_packet["source_block_key"])
        intent_affected = (source_packet.get("repair_intent") or {}).get("affected_node_indices", [])
        affected = {
            str(item)
            for item in (decision.get("affected_node_indices") or intent_affected)
        }
        approve_append_unlabeled = decision.get("approve_append_unlabeled") is True
        candidate_nodes = [copy.deepcopy(wrapper) for _, wrapper in sorted(candidate.items(), key=lambda item: _safe_sort_key(item[0]))]
        candidate_labels = {
            str((_node_payload(wrapper) or {}).get("label", "")).strip()
            for wrapper in candidate_nodes
            if str((_node_payload(wrapper) or {}).get("label", "")).strip()
        }
        unlabeled = [wrapper for wrapper in candidate_nodes if not str((_node_payload(wrapper) or {}).get("label", "")).strip()]
        if unlabeled and not affected and not approve_append_unlabeled:
            raise ValueError("Unlabeled repair candidates require affected_node_indices or approve_append_unlabeled=true")

        kept = []
        removed = []
        candidate_inserted = False
        for key, wrapper in sorted(canonical.items(), key=lambda item: _safe_sort_key(item[0])):
            orig_key = str(wrapper.get("_orig_key")) if isinstance(wrapper, dict) else ""
            label = str((_node_payload(wrapper) or {}).get("label", "")).strip()
            should_remove = orig_key == source_key and (
                (label and label in candidate_labels) or str(key) in affected
            )
            if should_remove:
                removed.append(str(key))
                if not candidate_inserted:
                    kept.extend(copy.deepcopy(candidate_nodes))
                    candidate_inserted = True
            else:
                kept.append(copy.deepcopy(wrapper))
        if not candidate_inserted:
            kept.extend(candidate_nodes)
        merged = {index: wrapper for index, wrapper in enumerate(kept)}

        backup_dir = _agent_state_dir(self.context) / REPAIR_BACKUP_DIRNAME / "extract_statements"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{repair_id}_unsplit_statement_dict.json"
        write_json(str(backup_path), canonical)
        write_json(str(canonical_path), merged)
        history_path = _agent_state_dir(self.context) / REPAIR_HISTORY_FILENAME
        history = _read_json_optional(history_path, [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "applied_at": utc_now(),
                "repair_id": repair_id,
                "stage": "extract_statements",
                "source_block_key": source_key,
                "removed_node_indices": removed,
                "candidate_node_count": len(candidate_nodes),
                "result_node_count": len(merged),
                "backup_path": str(backup_path),
                "decision": decision,
                "downstream_cache_status": "stale_after_extract_statements_repair",
            }
        )
        write_json(str(history_path), history)
        self._write_report(repair_dir, {"repair_id": repair_id, "status": "applied", "decision": decision})
        return {
            "command": "apply-repair",
            "repair_id": repair_id,
            "canonical_path": str(canonical_path),
            "backup_path": str(backup_path),
            "removed_node_indices": removed,
            "candidate_node_count": len(candidate_nodes),
            "result_node_count": len(merged),
            "downstream_cache_status": "stale_after_extract_statements_repair",
        }

    def list_candidates(self):
        candidates = []
        for repair_dir in sorted(self.root.glob("repair_*")):
            report = _read_json_optional(repair_dir / "repair_report.json", {})
            candidates.append({"repair_id": repair_dir.name, "repair_dir": str(repair_dir), "report": report})
        return candidates

    def intent_fingerprint(self, repair_intent):
        intent = self._validate_intent(repair_intent)
        stable_intent = {key: value for key, value in intent.items() if key != "force_new_attempt"}
        return _stable_fingerprint(stable_intent)

    def request_fingerprint(self, repair_intent):
        return _stable_fingerprint(
            {
                "intent_fingerprint": self.intent_fingerprint(repair_intent),
                "llm_engine": getattr(self.context, "llm_engine", "api"),
            }
        )

    def _build_source_packet(self, intent, repair_id):
        source_key = intent["source_block_key"]
        canonical = _read_json_optional(self.cache_dir / "unsplit_statement_dict.json", {})
        current_nodes = [
            {"node_index": str(key), **(_node_payload(wrapper) or {})}
            for key, wrapper in sorted((canonical or {}).items(), key=lambda item: _safe_sort_key(item[0]))
            if isinstance(wrapper, dict) and str(wrapper.get("_orig_key")) == source_key
        ]
        slice_text = intent.get("slice_text")
        if isinstance(slice_text, str) and slice_text.strip():
            contexts = [
                {
                    "context_id": "context_0001",
                    "context_source": "repair_intent.slice_text",
                    "matched_anchors": [],
                    "positions": [],
                    "localized_source_text": slice_text,
                }
            ]
            source = "repair_intent.slice_text"
            requires_confirmation = False
        else:
            contexts = self._locate_in_corrected_text(intent)
            source = "corrected_text_dict"
            requires_confirmation = False
            if not contexts:
                problem_dict = _read_json_optional(self.cache_dir / "problem_dict.json", {})
                entry = (problem_dict or {}).get(source_key)
                if entry is None:
                    try:
                        entry = (problem_dict or {}).get(int(source_key))
                    except ValueError:
                        entry = None
                contexts = [
                    {
                        "context_id": "context_0001",
                        "context_source": "problem_dict_fallback",
                        "matched_anchors": [],
                        "positions": [],
                        "localized_source_text": _clean_text((entry or {}).get("pos1") if isinstance(entry, dict) else entry),
                    }
                ]
                source = "problem_dict_fallback"
                requires_confirmation = True
        return {
            "schema_version": 1,
            "repair_id": repair_id,
            "stage": "extract_statements",
            "source_block_key": source_key,
            "context_source": source,
            "requires_main_agent_confirmation": requires_confirmation,
            "repair_intent": intent,
            "current_extracted_nodes": current_nodes,
            "contexts": contexts,
        }

    def _locate_in_corrected_text(self, intent):
        corrected = _read_json_optional(self.cache_dir / "corrected_text_dict.json", {})
        units = []
        for top_key, wrapper in sorted((corrected or {}).items(), key=lambda item: _safe_sort_key(item[0])):
            pos = wrapper.get("pos1", {}) if isinstance(wrapper, dict) else {}
            if isinstance(pos, dict):
                for sub_key, text in sorted(pos.items(), key=lambda item: _safe_sort_key(item[0])):
                    units.append({"top_key": str(top_key), "sub_key": str(sub_key), "text": _clean_text(text)})
            elif pos:
                units.append({"top_key": str(top_key), "sub_key": "0", "text": _clean_text(pos)})
        if not units:
            return []
        normalized_units = [_normalized_anchor(unit["text"]) for unit in units]
        stream = ""
        ranges = []
        for index, value in enumerate(normalized_units):
            start = len(stream)
            stream += value
            ranges.append((start, len(stream), index))
        anchors = [item for item in (intent.get("anchor_texts") or []) + (intent.get("expected_labels") or []) if str(item).strip()]
        hits = []
        for anchor in anchors:
            normalized = _normalized_anchor(anchor)
            if not normalized:
                continue
            offset = 0
            while True:
                found = stream.find(normalized, offset)
                if found < 0:
                    break
                hit_index = next((index for start, end, index in ranges if start <= found < end), len(units) - 1)
                hits.append((hit_index, str(anchor)))
                offset = found + max(1, len(normalized))
        if not hits:
            return []
        hit_indices = sorted({index for index, _ in hits})
        clusters = [[hit_indices[0]]]
        for index in hit_indices[1:]:
            if index - clusters[-1][-1] <= LOCAL_CONTEXT_CLUSTER_GAP:
                clusters[-1].append(index)
            else:
                clusters.append([index])
        contexts = []
        for context_index, cluster in enumerate(clusters, start=1):
            start = max(0, min(cluster) - LOCAL_CONTEXT_RADIUS)
            end = min(len(units), max(cluster) + LOCAL_CONTEXT_RADIUS + 1)
            matched = sorted({anchor for index, anchor in hits if index in cluster})
            contexts.append(
                {
                    "context_id": f"context_{context_index:04d}",
                    "context_source": "corrected_text_dict",
                    "matched_anchors": matched,
                    "positions": [
                        {"top_key": units[index]["top_key"], "sub_key": units[index]["sub_key"]}
                        for index in range(start, end)
                    ],
                    "localized_source_text": "".join(unit["text"] for unit in units[start:end]),
                }
            )
        return contexts

    def _render_prompt(self, source_packet, context):
        intent_json = json.dumps(source_packet["repair_intent"], ensure_ascii=False, indent=2)
        current_json = json.dumps(source_packet["current_extracted_nodes"], ensure_ascii=False, indent=2)
        allowed = ", ".join(ALLOWED_NODE_TYPES)
        return f"""# extract_statements repair run

This is a repair run for source_block_key `{source_packet['source_block_key']}`.
The localized_source_text below is the only trusted extraction evidence.

Extract every mathematical logic unit supported by localized_source_text.
Return the complete repaired node set for this localized context, not only the missing or changed nodes.
Use repair_intent only to identify likely problems. Do not invent nodes or labels that are not supported by localized_source_text.

Rules:
- Return only a valid JSON object whose keys are local indices starting from "0".
- Every value must contain exactly: node_type, content, proof, label.
- Allowed node_type values: {allowed}.
- content must preserve the complete mathematical statement, including assumptions and conclusion.
- proof must only contain the explicit proof belonging to the current node; otherwise use "".
- label must come from the current logical unit in the source; otherwise use "".
- Preserve LaTeX expressions, commands, environments, and math delimiters exactly as source text.
- Do not translate the source text.

Repair intent:
{intent_json}

Current extracted nodes for comparison:
{current_json}

Matched anchors:
{json.dumps(context['matched_anchors'], ensure_ascii=False)}

localized_source_text:
<localized_source_text>
{context['localized_source_text']}
</localized_source_text>
"""

    def _execute_prompt(self, prompt, run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        engine = getattr(self.context, "llm_engine", "api")
        if engine == "api":
            (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
            answer = self.context.llm.ask(prompt)
            (run_dir / "raw_stdout.txt").write_text(answer, encoding="utf-8")
            write_json(
                str(run_dir / "run_meta.json"),
                {
                    "schema_version": 1,
                    "stage": "extract_statements_repair",
                    "engine": "api",
                    "status": "ok",
                    "updated_at": utc_now(),
                },
            )
        elif engine == "claude_cli":
            claude = ClaudeCliEngine(
                stage_name="extract_statements_repair",
                output_dir=self.context.output_dir,
                command=getattr(self.context, "claude_command", "claude"),
                model=getattr(self.context, "claude_model", None),
                agent=getattr(self.context, "claude_agent", None),
                timeout_seconds=getattr(self.context, "claude_timeout_seconds", 900),
                max_retries=getattr(self.context, "claude_max_retries", 1),
            )
            answer = claude.run_prompt(prompt, run_dir=run_dir)
        else:
            raise ValueError(f"Unknown LLM engine: {engine}")
        parsed = self.context.parser.parse_dict(answer)
        write_json(str(run_dir / "candidate_output.json"), parsed)
        return parsed

    def _normalize_candidate(self, raw_by_context, source_key):
        merged = {}
        seen = set()
        label_to_index = {}
        next_index = 0
        for _, raw in raw_by_context.items():
            normalized = extract_nonempty_blocks(raw if isinstance(raw, dict) else {})
            for _, wrapper in sorted(normalized.items(), key=lambda item: _safe_sort_key(item[0])):
                node = _node_payload(wrapper) or {}
                if not isinstance(node, dict) or not str(node.get("content", "")).strip():
                    continue
                clean_node = {field: node.get(field, "") for field in NODE_FIELDS}
                fingerprint = (
                    str(clean_node.get("label", "")).strip(),
                    str(clean_node.get("content", "")).strip(),
                    str(clean_node.get("proof", "")).strip(),
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                label = str(clean_node.get("label", "")).strip()
                if label and label in label_to_index:
                    existing_index = label_to_index[label]
                    existing = _node_payload(merged[existing_index]) or {}
                    existing_score = len(str(existing.get("content", ""))) + len(str(existing.get("proof", "")))
                    candidate_score = len(str(clean_node.get("content", ""))) + len(str(clean_node.get("proof", "")))
                    if candidate_score > existing_score:
                        merged[existing_index] = {"pos1": clean_node, "_orig_key": source_key}
                    continue
                merged[next_index] = {"pos1": clean_node, "_orig_key": source_key}
                if label:
                    label_to_index[label] = next_index
                next_index += 1
        return merged

    def _validate_intent(self, intent):
        if not isinstance(intent, dict):
            raise ValueError("repair_intent must be a JSON object")
        if intent.get("stage") != "extract_statements":
            raise ValueError("repair_intent.stage must be extract_statements")
        if intent.get("source_block_key") is None:
            raise ValueError("repair_intent.source_block_key is required")
        result = copy.deepcopy(intent)
        result["source_block_key"] = str(result["source_block_key"])
        result.setdefault("context_policy", "localized_window")
        return result

    def _new_repair_id(self):
        return f"repair_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{uuid.uuid4().hex[:8]}"

    def _find_by_request_fingerprint(self, request_fingerprint):
        for item in reversed(self.list_candidates()):
            if (item.get("report") or {}).get("request_fingerprint") == request_fingerprint:
                return item
        return None

    def _repair_dir(self, repair_id):
        repair_dir = self.root / str(repair_id)
        if not repair_dir.exists():
            raise ValueError(f"Unknown repair_id: {repair_id}")
        return repair_dir

    def _required_json(self, path):
        data = _read_json_optional(path, None)
        if data is None:
            raise ValueError(f"Required repair artifact is missing or invalid: {path}")
        return data

    def _write_report(self, repair_dir, update):
        path = repair_dir / "repair_report.json"
        report = _read_json_optional(path, {})
        if not isinstance(report, dict):
            report = {}
        report.update(update)
        report["updated_at"] = utc_now()
        write_json(str(path), report)
