import os
import copy
import re
import socket
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np
from openai import OpenAI

from ...common.io import read_json, save_stage_json
from ...common.llm_task import run_multiprocess_task
from ...common.node import (
    assemble_statement_text,
    build_match_unit_dict,
    get_node_content,
    get_node_field_texts,
    get_node_formal_content,
    get_node_label,
    get_node_node_type,
    get_node_proof,
    get_node_title,
    is_definition_node_type,
    is_relation_statement_node_type,
    match_unit_summary,
    normalize_latex_backslashes,
)
from ...common.stage_recovery import (
    latest_unresolved_failure_report as _latest_unresolved_failure_report,
    rerun_unresolved_task_report,
    run_recoverable_task,
    stage_runs_root,
    write_failure_report,
)
from .relation_retrieval import (
    EmbeddingRetrievalError,
    LOGIC_PROMPT_CONTRACT_VERSION,
    RelationRetrievalConfig,
    apply_rerank_results,
    build_entity_pairs,
    build_rerank_tasks,
    finalize_retrieval_report,
    retrieve_relation_candidates,
    select_final_candidates,
    validate_rerank_result,
)
from .templates import (
    correction_prompt07,
    correction_prompt07_formalization,
    correction_prompt08,
    data_template07,
    data_template07_formalization,
    data_template07_formalization_nl,
    data_template07_nl,
    data_template08,
    data_template08_formalization,
    data_template08_formalization_nl,
    data_template08_nl,
    prompt_template07,
    prompt_template07_formalization,
    prompt_template07_formalization_nl,
    prompt_template07_nl,
    prompt_template08,
    prompt_template08_formalization,
    prompt_template08_formalization_nl,
    prompt_template08_nl,
    validation07,
    validation07_formalization,
    validation08,
)


RELATION_PROMPT_PROFILES = {"graph", "formalization"}
RELATION_MODES = {"structured", "natural"}
STAGE_NAME = "build_relations"
LOGIC_RELATION_TASK_STAGE = "build_relations_logic"
DEFINITION_RELATION_TASK_STAGE = "build_relations_definition"
RELATION_RERANK_TASK_STAGE = "build_relations_rerank"
DEFAULT_EMBEDDING_BATCH_SIZE = 16
DERIVATION_CONTEXT_MAX_CHARS = 2000
DERIVATION_BRIDGE_MAX_TRAILING_CHARS = 256
_DERIVATION_BRIDGE_RE = re.compile(
    r"(?:因此|从而|故而|故|于是|由此|得到以下(?:重要)?结论|"
    r"therefore|hence|thus|we\s+obtain)",
    flags=re.IGNORECASE,
)
_TEX_PARAGRAPH_START_RE = re.compile(r"\\par\s*\{", flags=re.IGNORECASE)
_STATEMENT_ENV_RE = re.compile(
    r"\\begin\s*\{\s*(?P<env>theorem|thm|lemma|lem|proposition|prop|corollary|cor|"
    r"definition|defn|def|axiom|claim|property|remark|example|exercise|exer|"
    r"conjecture|problem|observation|fact|thmdef|propdef)\s*\}",
    flags=re.IGNORECASE,
)
_PROOF_ENV_RE = re.compile(r"\\begin\s*\{\s*proof\s*\}", flags=re.IGNORECASE)


RERANK_DATA_TEMPLATE = '''
{
  "ranked": [
    {"candidate_id": "候选ID", "score": 0}
  ]
}
'''

RERANK_PROMPT_TEMPLATE = '''
你是一名数学依赖候选重排器。你的任务只是给候选排序，不得直接创建知识图谱边。

后置节点：
{dependent_json}

候选关系类型：{relation_kind}

候选前置节点：
{candidates_json}

请对每个候选评 0 到 3 的整数分：

当候选关系类型为 logic 时：
- 3：存在显式引用、明确证明步骤、直接前提支持或目标改写；
- 2：存在可信的中间引理、case bridge 或同一对象的结构性质；
- 1：只有间接证据，但值得进入后续审查层；
- 0：只有主题相似、仅定义使用、方向相反、作用域不兼容或需要外部知识。

当候选关系类型为 definition 时：
- 3：前置节点直接定义了后置节点实际使用的概念；
- 2：存在明确的别名、参数化定义或结构类型使用；
- 1：有弱定义证据，值得交给后续逐对判定；
- 0：只有主题相似。

必须原样返回输入中的每一个 candidate_id，恰好一次，并按 score 从高到低排列。
只输出 JSON：
{data_template}
'''

RERANK_CORRECTION_TEMPLATE = '''
下面的候选重排结果格式错误：
{answer}

原候选列表：
{candidates_json}

请重新输出合法 JSON。必须包含输入中的每一个 candidate_id，恰好一次，score 必须是 0 到 3：
{data_template}
'''


def _persistent_relation_cache_dir(context):
    if getattr(context, "cache_policy", "legacy") == "minimal":
        path = Path(context.checkpoint_root) / STAGE_NAME / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(context.output_dir)


def _coerce_source_span(value):
    if not isinstance(value, dict):
        return None
    try:
        start = int(value.get("start"))
        end = int(value.get("end"))
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return {"start": start, "end": end}


def _source_envelope(node):
    envelope = node.get("_source_envelope") if isinstance(node, dict) else None
    return envelope if isinstance(envelope, dict) else {}


def _source_block_span_lookup(state):
    lookup = {}
    problem_dict = state.get("problem_dict") if isinstance(state, dict) else None
    if not isinstance(problem_dict, dict):
        return lookup
    for key, block in problem_dict.items():
        if not isinstance(block, dict):
            continue
        span = _coerce_source_span(block.get("source_span"))
        if span is None:
            continue
        for candidate in (
            key,
            block.get("source_block_key"),
            block.get("block_id"),
        ):
            if candidate is not None and str(candidate).strip():
                lookup[str(candidate)] = span
    return lookup


def _locate_node_text_span(source_text, node):
    if not isinstance(source_text, str) or not source_text or not isinstance(node, dict):
        return None
    original = get_node_content(node)
    if not isinstance(original, str) or not original.strip():
        return None
    start = source_text.find(original)
    if start < 0 or source_text.rfind(original) != start:
        return None
    end = start + len(original)

    for match in reversed(list(_STATEMENT_ENV_RE.finditer(source_text[: start + 1]))):
        env_name = match.group("env")
        closing = re.compile(
            rf"\\end\s*\{{\s*{re.escape(env_name)}\s*\}}",
            flags=re.IGNORECASE,
        ).search(source_text, match.end())
        if closing is None:
            continue
        if closing.start() < end:
            break
        return {"start": match.start(), "end": closing.end()}
    return {"start": start, "end": end}


def _node_source_span(node, block_lookup=None, source_text=""):
    if not isinstance(node, dict):
        return None
    envelope = _source_envelope(node)
    for candidate in (
        envelope.get("source_span"),
        node.get("source_span"),
    ):
        span = _coerce_source_span(candidate)
        if span is not None:
            return span
    source_block_key = envelope.get("source_block_key") or node.get("source_block_key")
    if source_block_key is not None and isinstance(block_lookup, dict):
        span = block_lookup.get(str(source_block_key))
        if span is not None:
            return span
    return _locate_node_text_span(source_text, node)


def _strip_source_comments(text):
    return re.sub(r"(?m)(?<!\\)%[^\r\n]*", "", str(text or ""))


def _last_source_paragraph(text):
    text = str(text or "")
    matches = list(_TEX_PARAGRAPH_START_RE.finditer(text))
    if matches:
        return text[matches[-1].start():]
    chunks = [chunk for chunk in re.split(r"(?:\r?\n)\s*(?:\r?\n)+", text) if chunk.strip()]
    return chunks[-1] if chunks else ""


def _derive_local_context(source_text, previous_end, current_start):
    if not isinstance(source_text, str) or not source_text:
        return ""
    if previous_end is None or current_start is None:
        return ""
    try:
        previous_end = int(previous_end)
        current_start = int(current_start)
    except (TypeError, ValueError):
        return ""
    if previous_end < 0 or current_start <= previous_end or current_start > len(source_text):
        return ""

    between = source_text[previous_end:current_start]
    if not between.strip():
        return ""
    candidate = _last_source_paragraph(between[-(DERIVATION_CONTEXT_MAX_CHARS * 2):])
    candidate = candidate[-DERIVATION_CONTEXT_MAX_CHARS:]
    uncommented = _strip_source_comments(candidate)
    if _STATEMENT_ENV_RE.search(uncommented) or _PROOF_ENV_RE.search(uncommented):
        return ""
    normalized = re.sub(r"\s+", " ", uncommented).strip()
    if not normalized:
        return ""
    bridge_matches = list(_DERIVATION_BRIDGE_RE.finditer(normalized))
    if not bridge_matches:
        return ""
    trailing = normalized[bridge_matches[-1].end():]
    if len(trailing) > DERIVATION_BRIDGE_MAX_TRAILING_CHARS:
        return ""
    return candidate.strip()


def _relation_nodes_with_derivation_context(context, state, nodes):
    enriched = [copy.deepcopy(node) for node in nodes]
    source_path = Path(getattr(context, "file_path", "") or "")
    try:
        source_text = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    except (OSError, UnicodeError):
        source_text = ""
    if not source_text:
        return enriched

    block_lookup = _source_block_span_lookup(state)
    resolved = [
        (index, _node_source_span(node, block_lookup, source_text))
        for index, node in enumerate(enriched)
    ]
    for index, span in resolved:
        if span is None or enriched[index].get("derivation_context"):
            continue
        previous_end = max(
            (
                other_span["end"]
                for other_index, other_span in resolved
                if other_index != index
                and other_span is not None
                and other_span["end"] <= span["start"]
            ),
            default=max(
                0,
                span["start"] - (DERIVATION_CONTEXT_MAX_CHARS * 2),
            ),
        )
        context_text = _derive_local_context(source_text, previous_end, span["start"])
        if context_text:
            enriched[index]["derivation_context"] = context_text
    return enriched


def _validate_rerank_shape(value):
    if not isinstance(value, dict) or not isinstance(value.get("ranked"), list):
        return False
    for item in value["ranked"]:
        if not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str):
            return False
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            return False
        if score < 0 or score > 3:
            return False
    return True


class RelationRerankError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _env_positive_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_nonnegative_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _env_positive_float(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _embedding_http_timeout():
    explicit = os.getenv("EMBEDDING_HTTP_TIMEOUT")
    if explicit is not None:
        return _env_positive_float("EMBEDDING_HTTP_TIMEOUT", 30.0)
    return min(_env_positive_float("LLM_HTTP_TIMEOUT", 30.0), 30.0)


def _local_port_is_listening(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _resolve_embedding_proxy():
    explicit_proxy = os.getenv("PDFPIPELINE_EMBEDDING_PROXY")
    if explicit_proxy is None:
        explicit_proxy = os.getenv("EMBEDDING_HTTP_PROXY")
    explicit_proxy = (explicit_proxy or "").strip()
    if explicit_proxy.lower() in {"direct", "none", "off", "false", "0"}:
        return None
    if explicit_proxy:
        return explicit_proxy if "://" in explicit_proxy else "http://" + explicit_proxy

    auto_local = os.getenv("PDFPIPELINE_AUTO_LOCAL_PROXY", "1").strip().lower()
    if auto_local not in {"0", "false", "off"} and _local_port_is_listening("127.0.0.1", 7897):
        return "http://127.0.0.1:7897"
    return None


def _normalize_relation_prompt_profile(relation_prompt_profile):
    profile = (relation_prompt_profile or "graph").strip().lower()
    if profile not in RELATION_PROMPT_PROFILES:
        raise ValueError("relation_prompt_profile only supports graph / formalization")
    return profile


def _normalize_relation_mode(relation_mode):
    mode = (relation_mode or "structured").strip().lower()
    if mode not in RELATION_MODES:
        raise ValueError("relation_mode only supports structured / natural")
    return mode


def _select_relation_templates(relation_kind, relation_mode="structured", relation_prompt_profile="graph"):
    mode = _normalize_relation_mode(relation_mode)
    profile = _normalize_relation_prompt_profile(relation_prompt_profile)
    is_natural_mode = mode == "natural"

    if relation_kind == "logic":
        if profile == "formalization":
            return (
                prompt_template07_formalization_nl if is_natural_mode else prompt_template07_formalization,
                data_template07_formalization_nl if is_natural_mode else data_template07_formalization,
            )
        return (
            prompt_template07_nl if is_natural_mode else prompt_template07,
            data_template07_nl if is_natural_mode else data_template07,
        )

    if relation_kind == "definition":
        if profile == "formalization":
            return (
                prompt_template08_formalization_nl if is_natural_mode else prompt_template08_formalization,
                data_template08_formalization_nl if is_natural_mode else data_template08_formalization,
            )
        return (
            prompt_template08_nl if is_natural_mode else prompt_template08,
            data_template08_nl if is_natural_mode else data_template08,
        )

    raise ValueError("relation_kind only supports logic / definition")


def get_embedding(
    text,
    api_key,
    api_url,
    model,
    raise_on_failure=False,
    timeout_seconds=None,
    max_retries_override=None,
):
    batch_size = _env_positive_int("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)
    max_retries = (
        _env_nonnegative_int("EMBEDDING_MAX_RETRIES", 1)
        if max_retries_override is None
        else max(0, int(max_retries_override))
    )
    max_failed_batches = _env_positive_int("EMBEDDING_MAX_FAILED_BATCHES", 1)
    initial_backoff = 1.0
    request_timeout = (
        _embedding_http_timeout()
        if timeout_seconds is None
        else max(0.1, float(timeout_seconds))
    )

    texts = [text] if isinstance(text, str) else list(text)
    if not texts:
        return []

    empty_mask = []
    cleaned_texts = []
    for t in texts:
        is_empty = (t is None) or (not str(t).strip())
        empty_mask.append(is_empty)
        if not is_empty:
            cleaned_texts.append(str(t))

    base_url = api_url
    if "/chat/completions" in base_url:
        base_url = base_url.split("/chat/completions")[0]
    base_url = base_url.rstrip("/")

    proxy = _resolve_embedding_proxy()
    http_client_kwargs = {"trust_env": False}
    if proxy:
        http_client_kwargs["proxy"] = proxy
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=request_timeout,
        max_retries=0,
        http_client=httpx.Client(**http_client_kwargs),
    )

    def is_retryable_embedding_error(exc):
        status_code = getattr(exc, "status_code", None)
        if status_code in {408, 409, 429, 500, 502, 503, 504}:
            return True
        message = str(exc).lower()
        return any(
            marker in message
            for marker in [
                "internal_error",
                "server error",
                "rate limit",
                "too many requests",
                "timeout",
                "timed out",
                "connection",
                "temporarily unavailable",
                "service unavailable",
                "gateway",
            ]
        )

    stats = {
        "batch_size": batch_size,
        "requested_batches": 0,
        "retried_batches": 0,
        "succeeded": 0,
        "failed": 0,
        "failed_batches": 0,
        "degraded": False,
    }
    print(
        "Embedding 配置: "
        f"model={model}, base_url={base_url}, timeout={request_timeout:g}s, "
        f"batch_size={batch_size}, max_retries={max_retries}, "
        f"max_failed_batches={max_failed_batches}, proxy={proxy or 'direct'}"
    )

    def request_embedding_batch(batch_texts):
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                response = client.embeddings.create(input=batch_texts, model=model)
                batch_embeddings = [item.embedding for item in response.data]
                if len(batch_embeddings) != len(batch_texts):
                    raise RuntimeError(
                        f"Embedding result length mismatch: expected {len(batch_texts)}, got {len(batch_embeddings)}"
                    )
                return batch_embeddings
            except Exception as exc:
                last_exc = exc
                if not is_retryable_embedding_error(exc) or attempt >= max_retries:
                    break
                stats["retried_batches"] += 1
                time.sleep(initial_backoff * (2 ** attempt))
        raise last_exc

    def embed_batch(batch_texts, batch_index, total_batches):
        if not batch_texts:
            return []
        stats["requested_batches"] += 1
        print(f"Embedding 批次 {batch_index}/{total_batches}: 请求 {len(batch_texts)} 条文本...")
        try:
            batch_embeddings = request_embedding_batch(batch_texts)
            stats["succeeded"] += len(batch_texts)
            print(f"Embedding 批次 {batch_index}/{total_batches}: 成功。")
            return batch_embeddings
        except Exception as exc:
            if raise_on_failure:
                raise
            stats["failed"] += len(batch_texts)
            stats["failed_batches"] += 1
            stats["degraded"] = True
            print(f"Embedding 批次 {batch_index}/{total_batches}: 失败，已降级为空向量。原因: {exc}")
            return [[] for _ in batch_texts]

    embeddings = []
    total_batches = (len(cleaned_texts) + batch_size - 1) // batch_size
    for batch_index, start in enumerate(range(0, len(cleaned_texts), batch_size), start=1):
        if stats["failed_batches"] >= max_failed_batches:
            remaining = len(cleaned_texts) - start
            stats["failed"] += remaining
            stats["degraded"] = True
            print(
                "Embedding 熔断: "
                f"已有 {stats['failed_batches']} 个批次失败，"
                f"剩余 {remaining} 条文本直接降级为空向量。"
            )
            embeddings.extend([[] for _ in range(remaining)])
            break
        embeddings.extend(embed_batch(cleaned_texts[start:start + batch_size], batch_index, total_batches))

    if len(embeddings) < len(cleaned_texts):
        stats["failed"] += len(cleaned_texts) - len(embeddings)
        stats["degraded"] = True
        embeddings.extend([[] for _ in range(len(cleaned_texts) - len(embeddings))])
    elif len(embeddings) > len(cleaned_texts):
        embeddings = embeddings[:len(cleaned_texts)]

    result = []
    non_empty_idx = 0
    for is_empty in empty_mask:
        if is_empty:
            result.append([])
        else:
            result.append(embeddings[non_empty_idx] if non_empty_idx < len(embeddings) else [])
            non_empty_idx += 1

    empty_count = sum(1 for is_empty in empty_mask if is_empty)
    print(
        "Embedding 摘要: "
        f"请求={len(texts)}, 非空={len(cleaned_texts)}, 空={empty_count}, "
        f"批大小={stats['batch_size']}, 批次数={stats['requested_batches']}, "
        f"成功={stats['succeeded']}, 失败={stats['failed']}, "
        f"失败批次={stats['failed_batches']}, 重试批次={stats['retried_batches']}, "
        f"降级={'是' if stats['degraded'] else '否'}"
    )
    if stats["failed"] > 0:
        print(f"Embedding 警告: {stats['failed']} 个非空文本失败，已替换为空向量。")
    return result


def cosine_similarity(v1, v2):
    if not v1 or not v2:
        return 0.0
    vec1 = np.array(v1)
    vec2 = np.array(v2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))


def _batch_embed_nested_texts(nested_texts, api_key, api_url, model):
    flat_texts = []
    spans = []
    for texts in nested_texts:
        start = len(flat_texts)
        flat_texts.extend(texts)
        spans.append((start, len(texts)))
    if not flat_texts:
        return [[] for _ in nested_texts]
    flat_embeddings = get_embedding(flat_texts, api_key, api_url, model)
    result = []
    for start, length in spans:
        result.append(flat_embeddings[start:start + length])
    return result


def _calculate_condition_coverage_score(conclusion_embeddings, condition_embeddings, threshold=0.70):
    if not conclusion_embeddings or not condition_embeddings:
        return 0.0
    covered_count = 0
    for condition_embedding in condition_embeddings:
        if not condition_embedding:
            continue
        matched = False
        for conclusion_embedding in conclusion_embeddings:
            if not conclusion_embedding:
                continue
            if cosine_similarity(conclusion_embedding, condition_embedding) > threshold:
                matched = True
                break
        if matched:
            covered_count += 1
    return covered_count / len(condition_embeddings)


def _add_explicit_formalization_metadata(edge):
    edge = dict(edge)
    edge.setdefault("依赖类型", "explicit_reference")
    edge.setdefault("形式化用途", "explicit_reference")
    edge.setdefault("依赖强度", "direct")
    edge.setdefault("匹配证据", [])
    edge.setdefault("变量对应", {})
    edge.setdefault("缺失前提", [])
    edge.setdefault("置信度", 1.0)
    return edge


def _relation_node_id(node):
    if not isinstance(node, dict):
        return ""
    return str(node.get("parent_global_id") or node.get("global_id") or "").strip()


def _child_match(source_unit, target_unit, reason, match_kind="llm_relation"):
    source = match_unit_summary(source_unit)
    target = match_unit_summary(target_unit)
    return {
        "match_kind": match_kind,
        "source_parent_global_id": source.get("parent_global_id"),
        "source_sub_index": source.get("sub_index"),
        "source_is_virtual": source.get("is_virtual_subnode"),
        "source_conclusion": source.get("conclusion"),
        "target_parent_global_id": target.get("parent_global_id"),
        "target_sub_index": target.get("sub_index"),
        "target_is_virtual": target.get("is_virtual_subnode"),
        "target_conclusion": target.get("conclusion"),
        "reason": reason,
    }


def _annotate_relation_from_pair(edge, pair, match_kind="llm_relation"):
    if not isinstance(edge, dict) or not isinstance(pair, dict):
        return edge
    source_unit = pair.get("pos2") or {}
    target_unit = pair.get("pos1") or {}
    reason = str(edge.get("理由", "")).strip()
    annotated = dict(edge)
    annotated["出发节点"] = _relation_node_id(source_unit)
    annotated["到达节点"] = _relation_node_id(target_unit)
    annotated["child_matches"] = [_child_match(source_unit, target_unit, reason, match_kind=match_kind)]
    return annotated


def _annotate_explicit_parent_edge(edge):
    if not isinstance(edge, dict):
        return edge
    start = str(edge.get("出发节点", "")).strip()
    end = str(edge.get("到达节点", "")).strip()
    reason = str(edge.get("理由", "")).strip()
    annotated = dict(edge)
    annotated["child_matches"] = [
        {
            "match_kind": "explicit_reference",
            "source_parent_global_id": start,
            "source_sub_index": None,
            "source_is_virtual": True,
            "source_conclusion": "",
            "target_parent_global_id": end,
            "target_sub_index": None,
            "target_is_virtual": True,
            "target_conclusion": "",
            "reason": reason,
        }
    ]
    return annotated


def _relation_result_list_with_child_matches(relation_dict, pair_dict, match_kind):
    result = []
    for key, edge in (relation_dict or {}).items():
        if not isinstance(edge, dict):
            continue
        pair = (pair_dict or {}).get(key) or (pair_dict or {}).get(str(key))
        if pair is None:
            try:
                pair = (pair_dict or {}).get(int(str(key)))
            except (TypeError, ValueError):
                pair = None
        result.append(_annotate_relation_from_pair(edge, pair, match_kind=match_kind))
    return result


def _pair_for_task_key(pair_dict, key):
    pair = (pair_dict or {}).get(key) or (pair_dict or {}).get(str(key))
    if pair is not None:
        return pair
    try:
        return (pair_dict or {}).get(int(str(key)))
    except (TypeError, ValueError):
        return None


def _flatten_evidence_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        preferred = [
            value.get(key)
            for key in (
                "text_normalized",
                "text",
                "original_form",
                "formal_statement_core",
                "chinese",
                "english",
            )
            if isinstance(value.get(key), str) and value.get(key).strip()
        ]
        if preferred:
            return "\n".join(preferred)
        return "\n".join(_flatten_evidence_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_flatten_evidence_value(item) for item in value)
    return ""


def _normalized_evidence_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _evidence_is_anchored(pair, evidence_item):
    if not isinstance(pair, dict) or not isinstance(evidence_item, dict):
        return False
    support = pair.get("pos1")
    dependent = pair.get("pos2")
    if not isinstance(support, dict) or not isinstance(dependent, dict):
        return False
    a_field = evidence_item.get("A字段")
    b_field = evidence_item.get("B字段")
    a_snippet = _normalized_evidence_text(evidence_item.get("A片段"))
    b_snippet = _normalized_evidence_text(evidence_item.get("B片段"))
    if not a_snippet or not b_snippet:
        return False
    a_source = _normalized_evidence_text(_flatten_evidence_value(support.get(a_field)))
    b_source = _normalized_evidence_text(_flatten_evidence_value(dependent.get(b_field)))
    return bool(a_source and b_source and a_snippet in a_source and b_snippet in b_source)


def _short_evidence(value, limit=96):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _logic_review_record(task_key, pair, result, guard_reasons):
    support = pair.get("pos1") if isinstance(pair, dict) else {}
    dependent = pair.get("pos2") if isinstance(pair, dict) else {}
    return {
        "task_key": str(task_key),
        "candidate_id": pair.get("candidate_id", "") if isinstance(pair, dict) else "",
        "dependent_global_id": _relation_node_id(dependent),
        "support_global_id": _relation_node_id(support),
        "publication_status": "review",
        "guard_reasons": list(guard_reasons or []),
        "result": copy.deepcopy(result) if isinstance(result, dict) else result,
    }


def _partition_graph_logic_results(relation_dict, pair_dict):
    accepted = []
    review = []
    for key, result in (relation_dict or {}).items():
        pair = _pair_for_task_key(pair_dict, key)
        if not isinstance(pair, dict):
            review.append(_logic_review_record(key, {}, result, ["missing_pair_input"]))
            continue
        if pair.get("logic_prompt_contract_version") != LOGIC_PROMPT_CONTRACT_VERSION:
            review.append(_logic_review_record(key, pair, result, ["stale_pair_contract"]))
            continue
        if not validation07(result):
            review.append(_logic_review_record(key, pair, result, ["invalid_result_contract"]))
            continue

        publication_status = result.get("发布状态")
        if publication_status == "rejected":
            continue
        if publication_status == "review":
            review.append(_logic_review_record(key, pair, result, ["model_marked_review"]))
            continue

        evidence = result.get("匹配证据") or []
        unanchored = [
            index
            for index, item in enumerate(evidence)
            if not _evidence_is_anchored(pair, item)
        ]
        if unanchored:
            review.append(
                _logic_review_record(
                    key,
                    pair,
                    result,
                    [f"unanchored_evidence:{','.join(str(index) for index in unanchored)}"],
                )
            )
            continue

        first_evidence = evidence[0]
        criterion = result.get("依赖判据")
        reason = (
            f"{criterion}: A 的“{_short_evidence(first_evidence.get('A片段'))}”"
            f"支持 B 的“{_short_evidence(first_evidence.get('B片段'))}”。"
        )
        public_edge = {
            "出发节点": _relation_node_id(pair.get("pos2") or {}),
            "到达节点": _relation_node_id(pair.get("pos1") or {}),
            "关系": "逻辑依赖",
            "理由": reason,
        }
        accepted.append(
            _annotate_relation_from_pair(public_edge, pair, match_kind="logic_relation")
        )
    return accepted, review


def _graph_logic_result_cache_is_current(relation_dict, pair_dict):
    expected_keys = {str(key) for key in (pair_dict or {})}
    actual = {
        str(key): value
        for key, value in (relation_dict or {}).items()
    }
    if set(actual) != expected_keys:
        return False
    for key in expected_keys:
        pair = _pair_for_task_key(pair_dict, key)
        if (
            not isinstance(pair, dict)
            or pair.get("logic_prompt_contract_version") != LOGIC_PROMPT_CONTRACT_VERSION
            or not validation07(actual[key])
        ):
            return False
    return True


def _save_logic_review_candidates(context, records):
    save_stage_json(
        str(_persistent_relation_cache_dir(context)),
        "logic_relation_review_candidates.json",
        list(records or []),
        "Logic relation review candidates",
    )


def _run_relation_tasks(context, index_dict, *, relation_kind, relation_mode, relation_prompt_profile, checkpoint_dir):
    prompt, data = _select_relation_templates(
        relation_kind,
        relation_mode=relation_mode,
        relation_prompt_profile=relation_prompt_profile,
    )
    if relation_kind == "logic":
        is_formalization = (
            _normalize_relation_prompt_profile(relation_prompt_profile) == "formalization"
        )
        correction = (
            correction_prompt07_formalization
            if is_formalization
            else correction_prompt07
        )
        validator = validation07_formalization if is_formalization else validation07
    else:
        correction = correction_prompt08
        validator = validation08
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=data,
        prompt_template=prompt,
        correction_template=correction,
        validator=validator,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def _run_rerank_tasks(context, index_dict, checkpoint_dir):
    return run_multiprocess_task(
        llm=context.llm,
        parse_method=context.parser.parse_dict,
        data_template=RERANK_DATA_TEMPLATE,
        prompt_template=RERANK_PROMPT_TEMPLATE,
        correction_template=RERANK_CORRECTION_TEMPLATE,
        validator=_validate_rerank_shape,
        index_dict=index_dict,
        num_threads=context.num_threads,
        checkpoint=context.checkpoint,
        checkpoint_dir=checkpoint_dir,
    )


def _rerank_cache_path(context):
    return _persistent_relation_cache_dir(context) / "relation_rerank_cache.json"


def _rerank_model_name(context):
    return str(getattr(context, "model_name", None) or getattr(context.llm, "model", "") or "")


def _load_rerank_cache(context):
    path = _rerank_cache_path(context)
    if not path.exists():
        return {}
    try:
        payload = read_json(str(path))
    except Exception:
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 2
        or payload.get("model") != _rerank_model_name(context)
        or payload.get("logic_prompt_contract_version") != LOGIC_PROMPT_CONTRACT_VERSION
    ):
        return {}
    return payload.get("results") if isinstance(payload.get("results"), dict) else {}


def _save_rerank_cache(context, results):
    save_stage_json(
        str(_persistent_relation_cache_dir(context)),
        "relation_rerank_cache.json",
        {
            "schema_version": 2,
            "model": _rerank_model_name(context),
            "logic_prompt_contract_version": LOGIC_PROMPT_CONTRACT_VERSION,
            "results": results,
        },
        "Relation rerank cache",
    )


def _execute_candidate_rerank(context, candidates, node_list, config, *, resume=False):
    tasks = build_rerank_tasks(
        candidates,
        node_list,
        batch_size=config.rerank_batch_size,
        logic_prompt_contract_version=LOGIC_PROMPT_CONTRACT_VERSION,
    )
    if not tasks:
        return candidates, {"task_count": 0, "cache_hits": 0, "executed_tasks": 0}

    cache = _load_rerank_cache(context)
    results = {}
    for task_key, task in tasks.items():
        cached = cache.get(task_key)
        if validate_rerank_result(task, cached):
            results[task_key] = cached

    missing_tasks = {key: value for key, value in tasks.items() if key not in results}
    rerank_report = {
        "task_count": len(tasks),
        "cache_hits": len(results),
        "executed_tasks": len(missing_tasks),
    }
    run_dir = None
    failure_report = None

    if missing_tasks:
        unresolved = _latest_unresolved_failure_report(context, RELATION_RERANK_TASK_STAGE) if resume else None
        can_resume = False
        if unresolved is not None:
            expected = set(unresolved["report"].get("expected_task_keys") or [])
            can_resume = bool(expected) and expected == set(missing_tasks)
        if can_resume:
            partial, failure_report, run_dir = rerun_unresolved_task_report(
                context,
                stage_name=RELATION_RERANK_TASK_STAGE,
                task_runner=lambda index_dict, checkpoint_dir: _run_rerank_tasks(
                    context, index_dict, checkpoint_dir
                ),
                max_rounds=2,
            )
        else:
            partial, failure_report, run_dir = run_recoverable_task(
                context,
                stage_name=RELATION_RERANK_TASK_STAGE,
                input_dict=missing_tasks,
                task_runner=lambda index_dict, checkpoint_dir: _run_rerank_tasks(
                    context, index_dict, checkpoint_dir
                ),
            )

        valid_partial = {}
        for task_key, result in (partial or {}).items():
            task = tasks.get(str(task_key))
            if task is not None and validate_rerank_result(task, result):
                valid_partial[str(task_key)] = result
        results.update(valid_partial)

        unresolved_keys = [key for key in missing_tasks if key not in valid_partial]
        if unresolved_keys:
            failure_report = write_failure_report(
                run_dir,
                run_dir.name,
                RELATION_RERANK_TASK_STAGE,
                list(missing_tasks),
                valid_partial,
                attempts=(failure_report or {}).get("attempt_rounds") or 1,
                canonical_updated=False,
            )
            report = {
                **failure_report,
                "stage": STAGE_NAME,
                "task_stage": RELATION_RERANK_TASK_STAGE,
            }
            raise RelationRerankError(
                f"relation candidate rerank left {len(unresolved_keys)} unresolved tasks",
                report=report,
            )

    cache.update(results)
    _save_rerank_cache(context, cache)
    apply_rerank_results(candidates, tasks, results)
    if any(candidate.rerank_score is None for candidate in candidates):
        raise RelationRerankError(
            "relation candidate rerank did not score every candidate",
            report={"stage": STAGE_NAME, "task_stage": RELATION_RERANK_TASK_STAGE, **rerank_report},
        )
    return candidates, rerank_report


def latest_unresolved_failure_report(context):
    reports = []
    for task_stage in (RELATION_RERANK_TASK_STAGE, LOGIC_RELATION_TASK_STAGE, DEFINITION_RELATION_TASK_STAGE):
        latest = _latest_unresolved_failure_report(context, task_stage)
        if latest is not None:
            report = dict(latest["report"])
            report["stage"] = STAGE_NAME
            report["task_stage"] = task_stage
            latest["report"] = report
            try:
                modified = Path(latest["path"]).stat().st_mtime
            except OSError:
                modified = 0
            reports.append((modified, latest))
    return max(reports, key=lambda item: item[0])[1] if reports else None


def _latest_resolved_partial_result(context, task_stage):
    reports = []
    for report_path in stage_runs_root(context, task_stage).glob("*/failure_report.json"):
        try:
            report = read_json(str(report_path))
        except Exception:
            continue
        if not isinstance(report, dict) or report.get("status") != "resolved":
            continue
        partial_path = report.get("partial_result_dict_path")
        if not partial_path or not os.path.exists(partial_path):
            continue
        reports.append((report_path.stat().st_mtime, partial_path))
    if not reports:
        return {}
    _, partial_path = max(reports, key=lambda item: item[0])
    result = read_json(partial_path)
    return result if isinstance(result, dict) else {}


def extract_explicit_relations(node_list, text_mode="structured", relation_prompt_profile="graph"):
    relation_prompt_profile = _normalize_relation_prompt_profile(relation_prompt_profile)
    explicit_relations_list = []
    explicit_pairs = set()

    for j, node_later in enumerate(node_list):
        signals = node_later.get("reference_signals") if isinstance(node_later, dict) else None
        if not isinstance(signals, dict):
            continue

        hits = []
        hits.extend(signals.get("explicit_targets") or [])
        hits.extend(signals.get("relative_references") or [])

        for hit in hits:
            i = hit.get("resolved_index")
            if not isinstance(i, int) or i < 0 or i >= len(node_list) or i == j:
                continue
            if (j, i) in explicit_pairs:
                continue
            node_earlier = node_list[i]
            node_type_earlier = (get_node_node_type(node_earlier) or "").strip()
            if not is_relation_statement_node_type(node_type_earlier) and not is_definition_node_type(node_type_earlier):
                continue

            relation = "定义依赖" if is_definition_node_type(node_type_earlier) else "逻辑依赖"
            surface = hit.get("surface") or get_node_label(node_earlier).strip()
            match_mode = hit.get("match_mode") or ""
            edge = {
                "出发节点": node_later.get("global_id"),
                "到达节点": node_earlier.get("global_id"),
                "关系": relation,
                "理由": f"正则匹配：后置节点中显式引用“{surface}”（{match_mode}）",
            }
            if relation_prompt_profile == "formalization":
                edge = _add_explicit_formalization_metadata(edge)
            explicit_relations_list.append(edge)
            explicit_pairs.add((j, i))

    print(f"正则匹配提取到的显式关系数: {len(explicit_relations_list)}")
    return explicit_relations_list, explicit_pairs


def create_entity_pairs_legacy(
    node_list,
    api_key=None,
    api_url=None,
    embedding_model=None,
    use_keyword_filter=True,
    exclude_pairs=None,
    pair_mode="structured",
):
    proof_entity_pairs = {}
    definition_entity_pairs = {}
    proof_counter = 0
    definition_counter = 0
    semantic_topk = {}
    semantic_top_k = 5
    coverage_top_k = 5
    coverage_threshold = 0.70
    excluded_pairs = exclude_pairs or set()

    node_condition_embeddings = [[] for _ in node_list]
    node_conclusion_embeddings = [[] for _ in node_list]
    use_structured_mode = pair_mode == "structured"
    use_semantic = bool(api_key and api_url and embedding_model)

    if use_semantic:
        print("正在计算节点向量以进行语义筛选...")
        texts = [
            (
                f"{get_node_title(node)} {assemble_statement_text(node)[:200]}"
                if use_structured_mode
                else f"{get_node_content(node)[:260]} {get_node_proof(node)[:120]}"
            )
            for node in node_list
        ]
        embeddings = get_embedding(texts, api_key, api_url, embedding_model)
        for idx in range(len(node_list)):
            emb_idx = embeddings[idx]
            if not emb_idx:
                semantic_topk[idx] = set()
                continue
            sim_items = []
            for other_idx in range(len(node_list)):
                if other_idx == idx:
                    continue
                emb_other = embeddings[other_idx]
                if not emb_other:
                    continue
                sim_items.append((cosine_similarity(emb_idx, emb_other), other_idx))
            sim_items.sort(key=lambda x: x[0], reverse=True)
            semantic_topk[idx] = {item[1] for item in sim_items[:semantic_top_k]}

        if use_structured_mode:
            print("正在批量计算 conditions / conclusions 向量以进行覆盖率筛选...")
            node_condition_texts = [get_node_field_texts(node, "conditions") for node in node_list]
            node_conclusion_texts = [get_node_field_texts(node, "conclusions") for node in node_list]
            batched = _batch_embed_nested_texts(node_condition_texts + node_conclusion_texts, api_key, api_url, embedding_model)
            split_idx = len(node_list)
            node_condition_embeddings = batched[:split_idx]
            node_conclusion_embeddings = batched[split_idx:]

    def normalize_text_items(items):
        if not isinstance(items, list):
            return []
        return [
            {"text": item.get("text", ""), "text_normalized": item.get("text_normalized", item.get("text", ""))}
            for item in items
            if isinstance(item, dict)
        ]

    def build_full_pair_side(node):
        subject = node.get("subject")
        if isinstance(subject, dict):
            subject_value = subject.get("text_normalized") or subject.get("text") or []
        elif isinstance(subject, list):
            subject_value = subject
        else:
            subject_value = []

        context = node.get("context")
        if isinstance(context, dict):
            context_value = context.get("text_normalized") or context.get("text") or []
        elif isinstance(context, list):
            context_value = context
        else:
            context_value = []

        proof = node.get("proof")
        if isinstance(proof, dict):
            proof_value = proof.get("text_normalized") or proof.get("text") or ""
        elif isinstance(proof, str):
            proof_value = proof
        else:
            proof_value = ""

        return {
            "global_id": node.get("global_id", ""),
            "parent_global_id": node.get("parent_global_id", node.get("global_id", "")),
            "sub_index": node.get("sub_index"),
            "is_virtual_subnode": node.get("is_virtual_subnode", True),
            "node_type": get_node_node_type(node),
            "title": node.get("title", {}),
            "label": get_node_label(node),
            "content": get_node_formal_content(node),
            "subject": subject_value,
            "context": context_value,
            "conditions": normalize_text_items(node.get("conditions")),
            "conclusions": normalize_text_items(node.get("conclusions")),
            "proof": proof_value,
        }

    def build_definition_pair_side(node):
        proof = node.get("proof")
        if isinstance(proof, dict):
            proof_value = proof.get("text_normalized") or proof.get("text") or ""
        elif isinstance(proof, str):
            proof_value = proof
        else:
            proof_value = ""
        return {
            "global_id": node.get("global_id", ""),
            "parent_global_id": node.get("parent_global_id", node.get("global_id", "")),
            "sub_index": node.get("sub_index"),
            "is_virtual_subnode": node.get("is_virtual_subnode", True),
            "node_type": get_node_node_type(node),
            "title": node.get("title", {}),
            "content": get_node_formal_content(node),
            "proof": proof_value,
            "label": get_node_label(node),
        }

    def build_natural_pair_side(node):
        proof = node.get("proof")
        if isinstance(proof, dict):
            proof_value = proof.get("text") or ""
        elif isinstance(proof, str):
            proof_value = proof
        else:
            proof_value = ""

        return {
            "global_id": node.get("global_id", ""),
            "parent_global_id": node.get("parent_global_id", node.get("global_id", "")),
            "sub_index": node.get("sub_index"),
            "is_virtual_subnode": node.get("is_virtual_subnode", True),
            "node_type": get_node_node_type(node),
            "content": get_node_content(node),
            "proof": proof_value,
        }

    for j in range(len(node_list)):
        node_later = node_list[j]
        content_later = (get_node_content(node_later) if not use_structured_mode else assemble_statement_text(node_later)) or ""
        if not content_later:
            continue

        node_type_later = (get_node_node_type(node_later) or "").strip()
        definition_candidate_indices = set()
        proof_candidate_indices = set()
        rule4_score_items = []
        condition_embeddings_later = node_condition_embeddings[j] if use_semantic else []

        for i in range(j):
            if (j, i) in excluded_pairs:
                continue
            node_earlier = node_list[i]
            if _relation_node_id(node_earlier) and _relation_node_id(node_earlier) == _relation_node_id(node_later):
                continue
            title_earlier = get_node_title(node_earlier)
            label_earlier = get_node_label(node_earlier)
            node_type_earlier = (get_node_node_type(node_earlier) or "").strip()

            if is_definition_node_type(node_type_earlier):
                definition_candidate_indices.add(i)
                continue

            should_add = False
            if j - i <= 5:
                should_add = True
            if not should_add and use_keyword_filter:
                if label_earlier and len(label_earlier.strip()) > 1 and label_earlier in content_later:
                    should_add = True
                elif use_structured_mode and title_earlier and len(title_earlier.strip()) > 3 and title_earlier in content_later:
                    should_add = True
            if not should_add and use_semantic:
                if i in semantic_topk.get(j, set()) or j in semantic_topk.get(i, set()):
                    should_add = True
            if should_add:
                proof_candidate_indices.add(i)

            if (
                use_structured_mode
                and use_semantic
                and is_relation_statement_node_type(node_type_earlier)
                and is_relation_statement_node_type(node_type_later)
                and condition_embeddings_later
            ):
                conclusion_embeddings = node_conclusion_embeddings[i]
                if conclusion_embeddings:
                    score = _calculate_condition_coverage_score(
                        conclusion_embeddings,
                        condition_embeddings_later,
                        threshold=coverage_threshold,
                    )
                    if score > 0:
                        rule4_score_items.append((score, j - i, i))

        if rule4_score_items:
            rule4_score_items.sort(key=lambda x: (-x[0], x[1], x[2]))
            proof_candidate_indices.update(item[2] for item in rule4_score_items[:coverage_top_k])

        for i in range(j):
            if i in definition_candidate_indices:
                node_earlier = node_list[i]
                pos_builder = build_definition_pair_side if use_structured_mode else build_natural_pair_side
                definition_entity_pairs[definition_counter] = {
                    "pos1": pos_builder(node_earlier),
                    "pos2": pos_builder(node_later),
                }
                definition_counter += 1
                continue

            if i not in proof_candidate_indices:
                continue

            node_earlier = node_list[i]
            node_type_earlier = (get_node_node_type(node_earlier) or "").strip()
            if is_relation_statement_node_type(node_type_earlier) and is_relation_statement_node_type(node_type_later):
                pos_builder = build_full_pair_side if use_structured_mode else build_natural_pair_side
                proof_entity_pairs[proof_counter] = {
                    "pos1": pos_builder(node_earlier),
                    "pos2": pos_builder(node_later),
                }
                proof_counter += 1

    total_possible = len(node_list) * (len(node_list) - 1) // 2
    print(
        f"Generated proof pairs: {proof_counter}, definition pairs: {definition_counter} "
        f"(Filtered from {total_possible} possibilities)."
    )
    return proof_entity_pairs, definition_entity_pairs


def create_entity_pairs(
    node_list,
    api_key=None,
    api_url=None,
    embedding_model=None,
    use_keyword_filter=True,
    exclude_pairs=None,
    pair_mode="structured",
    *,
    predicate_entries=None,
    output_dir=None,
    retrieval_config=None,
):
    """Compatibility wrapper around the multi-channel candidate retriever.

    The fixed pipeline uses the two-phase path below so candidates can be
    listwise-reranked.  Direct callers receive deterministic RRF selection.
    """
    del use_keyword_filter
    config = retrieval_config or RelationRetrievalConfig(
        mode="hybrid_strict" if api_key and api_url and embedding_model else "sparse_preview"
    )
    excluded_pair_ids = set()
    for dependent_index, support_index in exclude_pairs or set():
        if not (0 <= dependent_index < len(node_list) and 0 <= support_index < len(node_list)):
            continue
        excluded_pair_ids.add(
            (_relation_node_id(node_list[dependent_index]), _relation_node_id(node_list[support_index]))
        )
    embedder = None
    if config.mode == "hybrid_strict":
        embedder = lambda texts: get_embedding(texts, api_key, api_url, embedding_model)
    candidates, _ = retrieve_relation_candidates(
        node_list,
        config=config,
        predicate_entries=predicate_entries,
        excluded_pair_ids=excluded_pair_ids,
        pair_mode=pair_mode,
        embedding_model=embedding_model,
        embed_texts=embedder,
        output_dir=output_dir,
    )
    selected = select_final_candidates(candidates, config=config)
    return build_entity_pairs(selected, node_list, pair_mode=pair_mode)


def merge_relations_lists(proof_relations_list, definition_relations_list):
    merged_by_key = {}
    for relations in (proof_relations_list, definition_relations_list):
        for obj in relations:
            if not isinstance(obj, dict):
                continue
            relation = str(obj.get("关系", "")).strip()
            if relation in {"无依赖", "", "None", "null"}:
                continue
            start_node = str(obj.get("出发节点", "")).strip()
            end_node = str(obj.get("到达节点", "")).strip()
            reason = str(obj.get("理由", "")).strip()
            dedup_key = (start_node, end_node, relation)
            if not start_node or not end_node:
                continue
            edge = merged_by_key.get(dedup_key)
            if edge is None:
                edge = dict(obj)
                edge["出发节点"] = start_node
                edge["到达节点"] = end_node
                edge["关系"] = relation
                edge["理由"] = reason
                edge["child_matches"] = []
                merged_by_key[dedup_key] = edge
            elif reason and reason not in str(edge.get("理由", "")):
                existing_reason = str(edge.get("理由", "")).strip()
                edge["理由"] = f"{existing_reason}; {reason}" if existing_reason else reason

            for match in obj.get("child_matches") or []:
                if isinstance(match, dict) and match not in edge["child_matches"]:
                    edge["child_matches"].append(match)
    return list(merged_by_key.values())


def _reason_negates_dependency(reason):
    if not isinstance(reason, str):
        return False
    normalized = " ".join(reason.strip().lower().split())
    negative_markers = [
        "不依赖",
        "无依赖",
        "不支持",
        "does not depend",
        "do not depend",
        "not depend",
        "no dependency",
        "does not support",
        "not support",
    ]
    return any(marker in normalized for marker in negative_markers)


def _edge_priority(edge):
    relation = str(edge.get("关系", "")).strip()
    reason = str(edge.get("理由", "")).strip()
    score = 0
    if reason.startswith("正则匹配："):
        score += 4
    if relation == "定义依赖":
        score += 2
    elif relation == "逻辑依赖":
        score += 1
    if reason:
        score += min(len(reason), 120) / 1000.0
    return score


def _rule_filter_relations(relations_list, node_list):
    order_map = {}
    for idx, node in enumerate(node_list):
        global_id = str(node.get("global_id", "")).strip()
        if global_id:
            order_map[global_id] = idx

    filtered = []
    for edge in relations_list:
        start_node = str(edge.get("出发节点", "")).strip()
        end_node = str(edge.get("到达节点", "")).strip()
        relation = str(edge.get("关系", "")).strip()
        reason = str(edge.get("理由", "")).strip()

        if not start_node or not end_node or start_node == end_node:
            continue
        if relation not in {"逻辑依赖", "定义依赖"}:
            continue
        if _reason_negates_dependency(reason):
            continue
        normalized_edge = dict(edge)
        normalized_edge["出发节点"] = start_node
        normalized_edge["到达节点"] = end_node
        normalized_edge["关系"] = relation
        normalized_edge["理由"] = reason
        filtered.append(normalized_edge)

    by_pair = {}
    for edge in filtered:
        key = (edge["出发节点"], edge["到达节点"])
        existing = by_pair.get(key)
        if existing is None or _edge_priority(edge) > _edge_priority(existing):
            by_pair[key] = edge

    resolved = dict(by_pair)
    visited = set()
    for start_node, end_node in list(by_pair.keys()):
        pair_key = tuple(sorted((start_node, end_node)))
        if pair_key in visited:
            continue
        visited.add(pair_key)
        forward = by_pair.get((start_node, end_node))
        backward = by_pair.get((end_node, start_node))
        if not forward or not backward:
            continue

        forward_score = _edge_priority(forward)
        backward_score = _edge_priority(backward)
        if forward_score == backward_score:
            forward_order = order_map.get(forward["出发节点"], -1) - order_map.get(forward["到达节点"], -1)
            backward_order = order_map.get(backward["出发节点"], -1) - order_map.get(backward["到达节点"], -1)
            if forward_order >= backward_order:
                resolved.pop((backward["出发节点"], backward["到达节点"]), None)
            else:
                resolved.pop((forward["出发节点"], forward["到达节点"]), None)
        elif forward_score > backward_score:
            resolved.pop((backward["出发节点"], backward["到达节点"]), None)
        else:
            resolved.pop((forward["出发节点"], forward["到达节点"]), None)

    return list(resolved.values())


def run(context, state, relation_mode="structured", relation_prompt_profile="graph"):
    try:
        inputs = _build_relation_inputs(
            context,
            state,
            relation_mode,
            relation_prompt_profile,
        )
    except (EmbeddingRetrievalError, RelationRerankError) as exc:
        state["build_relations_stage_run"] = dict(exc.report or {})
        if getattr(context, "execution_mode", "pipeline") != "pipeline":
            return state
        raise

    relation_mode = inputs["relation_mode"]
    relation_prompt_profile = inputs["relation_prompt_profile"]
    parent_node_list = inputs["parent_node_list"]
    node_dict = inputs["node_dict"]
    explicit_relations_list = inputs["explicit_relations_list"]
    proof_entity_pairs = inputs["proof_entity_pairs"]
    definition_entity_pairs = inputs["definition_entity_pairs"]
    state["relation_candidates"] = inputs["relation_candidates"]
    state["relation_retrieval_report"] = inputs["relation_retrieval_report"]

    if proof_entity_pairs:
        proof_relation, proof_failure_report, proof_run_dir = run_recoverable_task(
            context,
            stage_name=LOGIC_RELATION_TASK_STAGE,
            input_dict=proof_entity_pairs,
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="logic",
                relation_mode=relation_mode,
                relation_prompt_profile=relation_prompt_profile,
                checkpoint_dir=checkpoint_dir,
            ),
        )
        if proof_failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**proof_failure_report, "stage": STAGE_NAME, "task_stage": LOGIC_RELATION_TASK_STAGE}
            if getattr(context, "execution_mode", "pipeline") != "pipeline":
                return state
        if relation_prompt_profile == "graph":
            proof_relations_list, logic_review_candidates = _partition_graph_logic_results(
                proof_relation,
                proof_entity_pairs,
            )
        else:
            proof_relations_list = _relation_result_list_with_child_matches(
                proof_relation,
                proof_entity_pairs,
                "logic_relation",
            )
            logic_review_candidates = []
    else:
        proof_relations_list = []
        logic_review_candidates = []

    if definition_entity_pairs:
        definition_relation, definition_failure_report, definition_run_dir = run_recoverable_task(
            context,
            stage_name=DEFINITION_RELATION_TASK_STAGE,
            input_dict=definition_entity_pairs,
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="definition",
                relation_mode=relation_mode,
                relation_prompt_profile=relation_prompt_profile,
                checkpoint_dir=checkpoint_dir,
            ),
        )
        if definition_failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**definition_failure_report, "stage": STAGE_NAME, "task_stage": DEFINITION_RELATION_TASK_STAGE}
            if getattr(context, "execution_mode", "pipeline") != "pipeline":
                return state
        definition_relations_list = _relation_result_list_with_child_matches(
            definition_relation,
            definition_entity_pairs,
            "definition_relation",
        )
    else:
        definition_relations_list = []

    relations_list = merge_relations_lists(proof_relations_list, definition_relations_list + explicit_relations_list)
    relations_list = _rule_filter_relations(relations_list, parent_node_list)
    _save_logic_review_candidates(context, logic_review_candidates)
    if proof_entity_pairs:
        write_failure_report(
            proof_run_dir,
            proof_run_dir.name,
            LOGIC_RELATION_TASK_STAGE,
            [str(key) for key in proof_entity_pairs.keys()],
            proof_relation,
            attempts=proof_failure_report.get("attempt_rounds") or 1,
            canonical_updated=True,
        )
    if definition_entity_pairs:
        write_failure_report(
            definition_run_dir,
            definition_run_dir.name,
            DEFINITION_RELATION_TASK_STAGE,
            [str(key) for key in definition_entity_pairs.keys()],
            definition_relation,
            attempts=definition_failure_report.get("attempt_rounds") or 1,
            canonical_updated=True,
        )
    state["node_dict"] = node_dict
    state["node_list"] = parent_node_list
    state["logic_relation_review_candidates"] = logic_review_candidates
    state["edge_list"] = normalize_latex_backslashes(relations_list)
    return state


def _relation_retrieval_config(context):
    configured = getattr(context, "relation_retrieval_config", None)
    if isinstance(configured, RelationRetrievalConfig):
        return configured.validate()
    if isinstance(configured, dict):
        return RelationRetrievalConfig(**configured).validate()
    return RelationRetrievalConfig(
        mode=getattr(context, "relation_retrieval_mode", "hybrid_strict")
    ).validate()


def _build_relation_inputs(
    context,
    state,
    relation_mode,
    relation_prompt_profile,
    *,
    rerank_resume=False,
):
    source_node_list = state.get("node_list") or list(state["node_dict"].values())
    relation_mode = _normalize_relation_mode(relation_mode)
    relation_prompt_profile = _normalize_relation_prompt_profile(relation_prompt_profile)
    is_natural_mode = relation_mode == "natural"
    parent_node_list = list(source_node_list)
    relation_parent_node_list = _relation_nodes_with_derivation_context(
        context,
        state,
        parent_node_list,
    )
    match_unit_dict = build_match_unit_dict(relation_parent_node_list)
    node_list = list(match_unit_dict.values())
    node_dict = {idx: node for idx, node in enumerate(parent_node_list)}
    explicit_relations_list, explicit_pairs = extract_explicit_relations(
        parent_node_list,
        text_mode="natural" if is_natural_mode else "structured",
        relation_prompt_profile=relation_prompt_profile,
    )
    explicit_relations_list = [_annotate_explicit_parent_edge(edge) for edge in explicit_relations_list]

    excluded_pair_ids = set()
    explicit_supports = defaultdict(set)
    for dependent_index, support_index in explicit_pairs:
        if not (
            0 <= dependent_index < len(parent_node_list)
            and 0 <= support_index < len(parent_node_list)
        ):
            continue
        dependent_id = _relation_node_id(parent_node_list[dependent_index])
        support_id = _relation_node_id(parent_node_list[support_index])
        if dependent_id and support_id:
            excluded_pair_ids.add((dependent_id, support_id))
            explicit_supports[dependent_id].add(support_id)

    config = _relation_retrieval_config(context)
    embedder = None
    embedding_api_key = getattr(context, "embedding_api_key", None)
    embedding_api_url = getattr(context, "embedding_api_url", None)
    if embedding_api_key and embedding_api_url and context.embedding_model_name:
        embedder = lambda texts: get_embedding(
            texts,
            embedding_api_key,
            embedding_api_url,
            context.embedding_model_name,
        )
    try:
        candidates, retrieval_report = retrieve_relation_candidates(
            node_list,
            config=config,
            predicate_entries=state.get("predicate_entry_list") or [],
            excluded_pair_ids=excluded_pair_ids,
            explicit_supports=explicit_supports,
            pair_mode="natural" if is_natural_mode else "structured",
            embedding_model=context.embedding_model_name,
            embed_texts=embedder,
            output_dir=str(_persistent_relation_cache_dir(context)),
        )
    except EmbeddingRetrievalError as exc:
        failure_report = dict(exc.report or {})
        failure_report.setdefault("status", "embedding_failed")
        failure_report["publishable"] = False
        failure_report["stage"] = STAGE_NAME
        failure_report["task_stage"] = "relation_retrieval"
        save_stage_json(
            str(_persistent_relation_cache_dir(context)),
            "relation_retrieval_report.json",
            failure_report,
            "Relation retrieval failure report",
        )
        exc.report = failure_report
        raise

    candidates, rerank_report = _execute_candidate_rerank(
        context,
        candidates,
        node_list,
        config,
        resume=rerank_resume,
    )
    selected_candidates = select_final_candidates(candidates, config=config)
    retrieval_report = finalize_retrieval_report(
        retrieval_report,
        candidates,
        selected_candidates,
    )
    retrieval_report["rerank"] = rerank_report
    proof_entity_pairs, definition_entity_pairs = build_entity_pairs(
        selected_candidates,
        node_list,
        pair_mode="natural" if is_natural_mode else "structured",
        logic_prompt_contract_version=LOGIC_PROMPT_CONTRACT_VERSION,
    )
    retrieval_report["pairwise_logic_task_count"] = len(proof_entity_pairs)
    retrieval_report["pairwise_definition_task_count"] = len(definition_entity_pairs)
    retrieval_report["pairwise_task_count"] = len(proof_entity_pairs) + len(definition_entity_pairs)
    serialized_candidates = [candidate.to_dict() for candidate in candidates]
    save_stage_json(
        context.output_dir,
        "relation_candidates.json",
        serialized_candidates,
        "Relation candidates",
    )
    save_stage_json(
        context.output_dir,
        "relation_retrieval_report.json",
        retrieval_report,
        "Relation retrieval report",
    )
    return {
        "relation_mode": relation_mode,
        "relation_prompt_profile": relation_prompt_profile,
        "parent_node_list": parent_node_list,
        "node_dict": node_dict,
        "explicit_relations_list": explicit_relations_list,
        "proof_entity_pairs": proof_entity_pairs,
        "definition_entity_pairs": definition_entity_pairs,
        "relation_candidates": serialized_candidates,
        "relation_retrieval_report": retrieval_report,
    }


def rerun_failed_tasks(context, state, max_rounds=2, relation_mode="structured", relation_prompt_profile="graph"):
    unresolved = latest_unresolved_failure_report(context)
    if unresolved is None:
        raise RuntimeError("No unresolved failure report for build_relations")
    task_stage = unresolved["report"].get("task_stage")
    try:
        inputs = _build_relation_inputs(
            context,
            state,
            relation_mode,
            relation_prompt_profile,
            rerank_resume=task_stage == RELATION_RERANK_TASK_STAGE,
        )
    except (EmbeddingRetrievalError, RelationRerankError) as exc:
        state["build_relations_stage_run"] = dict(exc.report or {})
        return state, state["build_relations_stage_run"]

    proof_relation = _latest_resolved_partial_result(context, LOGIC_RELATION_TASK_STAGE)
    definition_relation = _latest_resolved_partial_result(context, DEFINITION_RELATION_TASK_STAGE)
    if (
        inputs["relation_prompt_profile"] == "graph"
        and inputs["proof_entity_pairs"]
        and not _graph_logic_result_cache_is_current(
            proof_relation,
            inputs["proof_entity_pairs"],
        )
    ):
        proof_relation = {}

    if task_stage == LOGIC_RELATION_TASK_STAGE:
        proof_relation, failure_report, proof_run_dir = rerun_unresolved_task_report(
            context,
            stage_name=LOGIC_RELATION_TASK_STAGE,
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="logic",
                relation_mode=inputs["relation_mode"],
                relation_prompt_profile=inputs["relation_prompt_profile"],
                checkpoint_dir=checkpoint_dir,
            ),
            max_rounds=max_rounds,
        )
        if failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**failure_report, "stage": STAGE_NAME, "task_stage": task_stage}
            return state, state["build_relations_stage_run"]
        write_failure_report(
            proof_run_dir,
            proof_run_dir.name,
            LOGIC_RELATION_TASK_STAGE,
            [str(key) for key in inputs["proof_entity_pairs"].keys()],
            proof_relation,
            attempts=failure_report.get("attempt_rounds") or 1,
            canonical_updated=True,
        )
    elif task_stage == DEFINITION_RELATION_TASK_STAGE:
        definition_relation, failure_report, definition_run_dir = rerun_unresolved_task_report(
            context,
            stage_name=DEFINITION_RELATION_TASK_STAGE,
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="definition",
                relation_mode=inputs["relation_mode"],
                relation_prompt_profile=inputs["relation_prompt_profile"],
                checkpoint_dir=checkpoint_dir,
            ),
            max_rounds=max_rounds,
        )
        if failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**failure_report, "stage": STAGE_NAME, "task_stage": task_stage}
            return state, state["build_relations_stage_run"]
        write_failure_report(
            definition_run_dir,
            definition_run_dir.name,
            DEFINITION_RELATION_TASK_STAGE,
            [str(key) for key in inputs["definition_entity_pairs"].keys()],
            definition_relation,
            attempts=failure_report.get("attempt_rounds") or 1,
            canonical_updated=True,
        )
    elif task_stage == RELATION_RERANK_TASK_STAGE:
        pass
    else:
        raise RuntimeError(f"Unknown build_relations task stage: {task_stage}")

    if inputs["proof_entity_pairs"] and not proof_relation:
        proof_relation, proof_failure_report, _ = run_recoverable_task(
            context,
            stage_name=LOGIC_RELATION_TASK_STAGE,
            input_dict=inputs["proof_entity_pairs"],
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="logic",
                relation_mode=inputs["relation_mode"],
                relation_prompt_profile=inputs["relation_prompt_profile"],
                checkpoint_dir=checkpoint_dir,
            ),
        )
        if proof_failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**proof_failure_report, "stage": STAGE_NAME, "task_stage": LOGIC_RELATION_TASK_STAGE}
            return state, state["build_relations_stage_run"]
    if inputs["definition_entity_pairs"] and not definition_relation:
        definition_relation, definition_failure_report, _ = run_recoverable_task(
            context,
            stage_name=DEFINITION_RELATION_TASK_STAGE,
            input_dict=inputs["definition_entity_pairs"],
            task_runner=lambda index_dict, checkpoint_dir: _run_relation_tasks(
                context,
                index_dict,
                relation_kind="definition",
                relation_mode=inputs["relation_mode"],
                relation_prompt_profile=inputs["relation_prompt_profile"],
                checkpoint_dir=checkpoint_dir,
            ),
        )
        if definition_failure_report.get("status") != "resolved":
            state["build_relations_stage_run"] = {**definition_failure_report, "stage": STAGE_NAME, "task_stage": DEFINITION_RELATION_TASK_STAGE}
            return state, state["build_relations_stage_run"]

    if inputs["relation_prompt_profile"] == "graph":
        proof_relations_list, logic_review_candidates = _partition_graph_logic_results(
            proof_relation,
            inputs["proof_entity_pairs"],
        )
    else:
        proof_relations_list = _relation_result_list_with_child_matches(
            proof_relation,
            inputs["proof_entity_pairs"],
            "logic_relation",
        )
        logic_review_candidates = []
    definition_relations_list = _relation_result_list_with_child_matches(
        definition_relation,
        inputs["definition_entity_pairs"],
        "definition_relation",
    )
    relations_list = merge_relations_lists(
        proof_relations_list,
        definition_relations_list + inputs["explicit_relations_list"],
    )
    relations_list = _rule_filter_relations(relations_list, inputs["parent_node_list"])
    _save_logic_review_candidates(context, logic_review_candidates)
    state["node_dict"] = inputs["node_dict"]
    state["node_list"] = inputs["parent_node_list"]
    state["relation_candidates"] = inputs["relation_candidates"]
    state["relation_retrieval_report"] = inputs["relation_retrieval_report"]
    state["logic_relation_review_candidates"] = logic_review_candidates
    state["edge_list"] = normalize_latex_backslashes(relations_list)
    return state, {**unresolved["report"], "status": "resolved", "canonical_updated": True}

