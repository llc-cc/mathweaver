import inspect
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import extractor
import main
import relation_layer
from pipeline import orchestrator
from pipeline.stages.build_relations import stage as relation_stage
from pipeline.stages.build_relations import templates as relation_templates


def _logic_pair(
    support_text,
    dependent_text,
    *,
    support_field="conclusions",
    dependent_field="proof",
    contract_version=2,
):
    support = {
        "global_id": "support",
        "parent_global_id": "support",
        support_field: (
            [{"text": support_text, "text_normalized": support_text}]
            if support_field in {"conditions", "conclusions"}
            else support_text
        ),
    }
    dependent = {
        "global_id": "dependent",
        "parent_global_id": "dependent",
        dependent_field: (
            [{"text": dependent_text, "text_normalized": dependent_text}]
            if dependent_field in {"conditions", "conclusions"}
            else dependent_text
        ),
    }
    return {
        "pos1": support,
        "pos2": dependent,
        "candidate_id": "candidate",
        "logic_prompt_contract_version": contract_version,
    }


def _logic_result(
    criterion,
    support_text,
    dependent_text,
    *,
    support_field="conclusions",
    dependent_field="proof",
    status="accepted",
    evidence_level="direct",
    relation="逻辑依赖",
    exclusions=None,
    missing_premises=None,
):
    return {
        "契约版本": 2,
        "出发节点": "dependent",
        "到达节点": "support",
        "关系": relation,
        "依赖判据": criterion,
        "证据等级": evidence_level,
        "匹配证据": (
            [
                {
                    "A字段": support_field,
                    "A片段": support_text,
                    "B字段": dependent_field,
                    "B片段": dependent_text,
                    "作用": "前置节点支持后置节点的具体推导。",
                }
            ]
            if criterion != "none"
            else []
        ),
        "变量对应": {},
        "缺失前提": list(missing_premises or []),
        "排除检查": exclusions
        or {
            "仅主题相似": False,
            "仅定义使用": False,
            "方向冲突": False,
            "作用域冲突": False,
            "使用外部知识": False,
        },
        "发布状态": status,
        "置信度": 0.95,
        "理由": "基于两侧原文证据判定。",
    }


def test_select_relation_templates_keeps_graph_default():
    prompt, data = relation_stage._select_relation_templates("logic")
    assert prompt == relation_stage.prompt_template07
    assert data == relation_stage.data_template07

    prompt, data = relation_stage._select_relation_templates("definition", relation_mode="natural")
    assert prompt == relation_stage.prompt_template08_nl
    assert data == relation_stage.data_template08_nl


def test_select_relation_templates_uses_formalization_profile():
    prompt, data = relation_stage._select_relation_templates(
        "logic",
        relation_mode="structured",
        relation_prompt_profile="formalization",
    )
    assert prompt == relation_stage.prompt_template07_formalization
    assert data == relation_stage.data_template07_formalization

    prompt, data = relation_stage._select_relation_templates(
        "definition",
        relation_mode="natural",
        relation_prompt_profile="formalization",
    )
    assert prompt == relation_stage.prompt_template08_formalization_nl
    assert data == relation_stage.data_template08_formalization_nl
    assert '"形式化用途"' in data
    assert '"匹配证据"' in data


def test_graph_prompt_uses_multi_criterion_evidence_contract():
    prompt = relation_stage.prompt_template07
    for criterion in (
        "premise_support",
        "explicit_reference",
        "proof_step_support",
        "goal_rewrite",
        "intermediate_lemma",
        "case_bridge",
        "structural_property",
    ):
        assert criterion in prompt
    assert "只是判据之一" in prompt
    assert "derivation_context" in prompt
    assert "仅定义使用" in prompt
    assert "accepted" in prompt and "review" in prompt and "rejected" in prompt
    assert '"契约版本":2' in relation_stage.data_template07


def test_graph_logic_validator_enforces_publishable_contract():
    accepted = _logic_result(
        "premise_support",
        "P",
        "P",
        dependent_field="conditions",
    )
    assert relation_templates.validation07(accepted)

    review = _logic_result(
        "intermediate_lemma",
        "lemma P",
        "uses P",
        status="review",
        evidence_level="indirect",
    )
    assert relation_templates.validation07(review)

    rejected = _logic_result(
        "none",
        "",
        "",
        status="rejected",
        evidence_level="none",
        relation="无依赖",
        exclusions={
            "仅主题相似": True,
            "仅定义使用": False,
            "方向冲突": False,
            "作用域冲突": False,
            "使用外部知识": False,
        },
    )
    assert relation_templates.validation07(rejected)

    invalid = dict(accepted)
    invalid["匹配证据"] = []
    assert not relation_templates.validation07(invalid)

    invalid = dict(accepted)
    invalid["缺失前提"] = ["R"]
    assert not relation_templates.validation07(invalid)

    invalid = dict(accepted)
    invalid["契约版本"] = 1
    assert not relation_templates.validation07(invalid)


def test_pair_guard_anchors_evidence_and_keeps_public_edge_shape():
    pair = _logic_pair(
        "A(BC)=(AB)C",
        "则利用结合律得到 B(AB')=(BA)B'",
    )
    result = _logic_result(
        "proof_step_support",
        "A(BC)=(AB)C",
        "利用结合律",
    )
    accepted, review = relation_stage._partition_graph_logic_results(
        {"0": result},
        {"0": pair},
    )
    assert len(accepted) == 1
    assert review == []
    assert set(accepted[0]) == {"出发节点", "到达节点", "关系", "理由", "child_matches"}
    assert accepted[0]["出发节点"] == "dependent"
    assert accepted[0]["到达节点"] == "support"
    assert accepted[0]["关系"] == "逻辑依赖"
    assert "proof_step_support" in accepted[0]["理由"]

    forged = _logic_result(
        "proof_step_support",
        "不存在的结合律陈述",
        "利用结合律",
    )
    accepted, review = relation_stage._partition_graph_logic_results(
        {"0": forged},
        {"0": pair},
    )
    assert accepted == []
    assert review[0]["guard_reasons"][0].startswith("unanchored_evidence")


def test_graph_logic_cache_version_and_review_tier():
    pair = _logic_pair("lemma P", "the proof may use P")
    review_result = _logic_result(
        "intermediate_lemma",
        "lemma P",
        "the proof may use P",
        status="review",
        evidence_level="indirect",
    )
    accepted, review = relation_stage._partition_graph_logic_results(
        {"0": review_result},
        {"0": pair},
    )
    assert accepted == []
    assert review[0]["guard_reasons"] == ["model_marked_review"]
    assert relation_stage._graph_logic_result_cache_is_current(
        {"0": review_result},
        {"0": pair},
    )

    stale_pair = _logic_pair(
        "lemma P",
        "the proof may use P",
        contract_version=1,
    )
    assert not relation_stage._graph_logic_result_cache_is_current(
        {"0": review_result},
        {"0": stale_pair},
    )


def test_local_derivation_context_requires_bridge_and_rejects_foreign_proof():
    source = (
        "\\begin{theorem}A\\end{theorem}\n"
        "\\par{再次由结合律可得若干等式，因此得到以下重要结论：}\n"
        "\\begin{proposition}B\\end{proposition}"
    )
    previous_end = source.index("\\par")
    current_start = source.index("\\begin{proposition}")
    context = relation_stage._derive_local_context(source, previous_end, current_start)
    assert "再次由结合律" in context

    no_bridge = source.replace("因此得到以下重要结论", "下面讨论")
    assert relation_stage._derive_local_context(no_bridge, previous_end, current_start) == ""

    foreign_proof = (
        "\\begin{theorem}A\\end{theorem}\n"
        "\\begin{proof}由此得到以下结论。\\end{proof}\n"
        "\\begin{proposition}B\\end{proposition}"
    )
    previous_end = foreign_proof.index("\\begin{proof}")
    current_start = foreign_proof.index("\\begin{proposition}")
    assert relation_stage._derive_local_context(
        foreign_proof,
        previous_end,
        current_start,
    ) == ""


def test_relation_input_enrichment_uses_source_envelope_without_mutating_nodes():
    source = (
        "\\begin{theorem}A\\end{theorem}\n"
        "\\par{再次由结合律计算，因此得到以下结论：}\n"
        "\\begin{proposition}B\\end{proposition}"
    )
    first_start = source.index("\\begin{theorem}")
    first_end = source.index("\\par")
    second_start = source.index("\\begin{proposition}")
    second_end = len(source)
    nodes = [
        {
            "global_id": "A",
            "_source_envelope": {
                "source_span": {"start": first_start, "end": first_end},
            },
        },
        {
            "global_id": "B",
            "_source_envelope": {
                "source_span": {"start": second_start, "end": second_end},
            },
        },
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir, "source.tex")
        source_path.write_text(source, encoding="utf-8")
        enriched = relation_stage._relation_nodes_with_derivation_context(
            SimpleNamespace(file_path=str(source_path)),
            {},
            nodes,
        )
        enriched_without_previous_node = relation_stage._relation_nodes_with_derivation_context(
            SimpleNamespace(file_path=str(source_path)),
            {},
            [nodes[1]],
        )

    assert "再次由结合律" in enriched[1]["derivation_context"]
    assert "再次由结合律" in enriched_without_previous_node[0]["derivation_context"]
    assert "derivation_context" not in nodes[1]


def test_linear_algebra_logic_regression_cases():
    cases = [
        (
            _logic_pair(
                "A(BC)=(AB)C",
                "则利用结合律和单位元可得 B=B'",
                dependent_field="source_original_form",
            ),
            _logic_result(
                "proof_step_support",
                "A(BC)=(AB)C",
                "利用结合律",
                dependent_field="source_original_form",
            ),
            True,
        ),
        (
            _logic_pair(
                "A(BC)=(AB)C",
                "再次由结合律，(AB)(B^{-1}A^{-1})=I_n，因此得到以下重要结论",
                dependent_field="derivation_context",
            ),
            _logic_result(
                "proof_step_support",
                "A(BC)=(AB)C",
                "再次由结合律",
                dependent_field="derivation_context",
            ),
            True,
        ),
        (
            _logic_pair(
                "方阵 A 存在左逆当且仅当 A 存在右逆",
                "若方阵 A 存在左逆或存在右逆，则称 A 可逆",
                support_field="content",
                dependent_field="source_original_form",
            ),
            _logic_result(
                "case_bridge",
                "方阵 A 存在左逆当且仅当 A 存在右逆",
                "若方阵 A 存在左逆或存在右逆",
                support_field="content",
                dependent_field="source_original_form",
            ),
            True,
        ),
    ]
    for pair, result, expected in cases:
        accepted, review = relation_stage._partition_graph_logic_results(
            {"0": result},
            {"0": pair},
        )
        assert bool(accepted) is expected
        assert review == []

    rejected = _logic_result(
        "none",
        "",
        "",
        status="rejected",
        evidence_level="none",
        relation="无依赖",
        exclusions={
            "仅主题相似": False,
            "仅定义使用": False,
            "方向冲突": True,
            "作用域冲突": False,
            "使用外部知识": False,
        },
    )
    accepted, review = relation_stage._partition_graph_logic_results(
        {"0": rejected},
        {"0": _logic_pair("n≥m", "方阵左右逆等价")},
    )
    assert accepted == [] and review == []

    definition_only = dict(rejected)
    definition_only["排除检查"] = {
        "仅主题相似": False,
        "仅定义使用": True,
        "方向冲突": False,
        "作用域冲突": False,
        "使用外部知识": False,
    }
    accepted, review = relation_stage._partition_graph_logic_results(
        {"0": definition_only},
        {"0": _logic_pair("可逆的定义", "可逆矩阵乘积")},
    )
    assert accepted == [] and review == []


def test_invalid_relation_prompt_profile_raises():
    try:
        relation_stage._select_relation_templates("logic", relation_prompt_profile="unknown")
    except ValueError as exc:
        assert "relation_prompt_profile" in str(exc)
    else:
        raise AssertionError("Expected invalid relation_prompt_profile to raise ValueError")


def test_rule_filter_preserves_formalization_edge_fields():
    edges = [
        {
            "出发节点": "B",
            "到达节点": "A",
            "关系": "逻辑依赖",
            "理由": "A 的结论可作为 B 的前提。",
            "依赖类型": "premise_support",
            "形式化用途": "apply",
            "依赖强度": "direct",
            "匹配证据": [{"A字段": "conclusions", "B字段": "conditions"}],
            "变量对应": {"A.x": "B.x"},
            "缺失前提": [],
            "置信度": 0.91,
        }
    ]
    nodes = [{"global_id": "A"}, {"global_id": "B"}]

    filtered = relation_stage._rule_filter_relations(edges, nodes)

    assert len(filtered) == 1
    assert filtered[0]["依赖类型"] == "premise_support"
    assert filtered[0]["形式化用途"] == "apply"
    assert filtered[0]["匹配证据"] == [{"A字段": "conclusions", "B字段": "conditions"}]
    assert filtered[0]["置信度"] == 0.91


def test_explicit_relations_gain_formalization_defaults():
    nodes = [
        {
            "global_id": "A",
            "node_type": "定理",
            "label": "Theorem 1",
        },
        {
            "global_id": "B",
            "node_type": "推论",
            "reference_signals": {
                "explicit_targets": [
                    {
                        "resolved_index": 0,
                        "surface": "Theorem 1",
                        "match_mode": "label",
                    }
                ],
                "relative_references": [],
            },
        },
    ]

    relations, explicit_pairs = relation_stage.extract_explicit_relations(
        nodes,
        relation_prompt_profile="formalization",
    )

    assert explicit_pairs == {(1, 0)}
    assert len(relations) == 1
    assert relations[0]["依赖强度"] == "direct"
    assert relations[0]["形式化用途"] == "explicit_reference"
    assert relations[0]["置信度"] == 1.0


def test_public_entrypoints_accept_relation_prompt_profile():
    for func in (
        extractor.process_md,
        orchestrator.process_md,
        relation_layer.process_node_file,
        main.process_pdf_to_json,
        main.run_pipeline,
    ):
        assert "relation_prompt_profile" in inspect.signature(func).parameters


def test_public_pipeline_entrypoints_default_to_structured_edges():
    for func in (extractor.process_md, orchestrator.process_md, main.process_pdf_to_json, main.run_pipeline):
        assert inspect.signature(func).parameters["edge_output_mode"].default == "structured"


class _FakeEmbeddingOpenAI:
    calls = []
    fail_first = False
    short_response = False

    def __init__(self, **kwargs):
        self.embeddings = self

    def create(self, input, model):
        batch = list(input)
        self.__class__.calls.append(batch)
        if self.__class__.fail_first and len(self.__class__.calls) == 1:
            exc = RuntimeError("temporarily unavailable")
            exc.status_code = 503
            raise exc
        output_count = max(0, len(batch) - 1) if self.__class__.short_response else len(batch)
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(index), 1.0])
                for index in range(output_count)
            ]
        )


class _FakeHttpClient:
    calls = []

    def __init__(self, **kwargs):
        self.__class__.calls.append(kwargs)


def _with_fake_embedding_client(func):
    original_openai = relation_stage.OpenAI
    original_http_client = relation_stage.httpx.Client
    original_sleep = relation_stage.time.sleep
    _FakeEmbeddingOpenAI.calls = []
    _FakeEmbeddingOpenAI.fail_first = False
    _FakeEmbeddingOpenAI.short_response = False
    _FakeHttpClient.calls = []
    relation_stage.OpenAI = _FakeEmbeddingOpenAI
    relation_stage.httpx.Client = _FakeHttpClient
    relation_stage.time.sleep = lambda seconds: None
    try:
        return func()
    finally:
        relation_stage.OpenAI = original_openai
        relation_stage.httpx.Client = original_http_client
        relation_stage.time.sleep = original_sleep


def test_embedding_client_uses_explicit_embedding_proxy_only():
    proxy_environment = {
        "HTTP_PROXY": "http://127.0.0.1:7897",
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ALL_PROXY": "socks5://127.0.0.1:7897",
        "PDFPIPELINE_EMBEDDING_PROXY": "http://127.0.0.1:7897",
        "PDFPIPELINE_LLM_PROXY": "http://127.0.0.1:7897",
        "LLM_HTTP_PROXY": "http://127.0.0.1:7897",
        "PDFPIPELINE_PROXY": "http://127.0.0.1:7897",
        "PDFPIPELINE_AUTO_LOCAL_PROXY": "1",
    }

    def run():
        result = relation_stage.get_embedding(
            ["alpha"],
            api_key="key",
            api_url="https://example.test/v1",
            model="embed",
        )
        assert len(result) == 1
        assert _FakeHttpClient.calls == [
            {"trust_env": False, "proxy": "http://127.0.0.1:7897"}
        ]

    with patch.dict(os.environ, proxy_environment, clear=True):
        _with_fake_embedding_client(run)


def test_embedding_client_can_force_direct_mode():
    with patch.dict(
        os.environ,
        {
            "PDFPIPELINE_EMBEDDING_PROXY": "direct",
            "PDFPIPELINE_AUTO_LOCAL_PROXY": "1",
        },
        clear=True,
    ):
        _with_fake_embedding_client(
            lambda: relation_stage.get_embedding(
                ["alpha"],
                api_key="key",
                api_url="https://example.test/v1",
                model="embed",
            )
        )
    assert _FakeHttpClient.calls == [{"trust_env": False}]


def test_embedding_client_ignores_chat_and_system_proxies():
    proxy_environment = {
        "HTTP_PROXY": "http://system-proxy.test:8080",
        "HTTPS_PROXY": "http://system-proxy.test:8080",
        "ALL_PROXY": "socks5://system-proxy.test:1080",
        "PDFPIPELINE_LLM_PROXY": "http://chat-proxy.test:8080",
        "LLM_HTTP_PROXY": "http://chat-proxy.test:8080",
        "PDFPIPELINE_PROXY": "http://chat-proxy.test:8080",
        "PDFPIPELINE_AUTO_LOCAL_PROXY": "0",
    }
    with patch.dict(os.environ, proxy_environment, clear=True):
        _with_fake_embedding_client(
            lambda: relation_stage.get_embedding(
                ["alpha"],
                api_key="key",
                api_url="https://example.test/v1",
                model="embed",
            )
        )
    assert _FakeHttpClient.calls == [{"trust_env": False}]


def test_embedding_client_auto_detects_local_proxy():
    with patch.dict(os.environ, {"PDFPIPELINE_AUTO_LOCAL_PROXY": "1"}, clear=True), patch.object(
        relation_stage,
        "_local_port_is_listening",
        return_value=True,
    ):
        _with_fake_embedding_client(
            lambda: relation_stage.get_embedding(
                ["alpha"],
                api_key="key",
                api_url="https://example.test/v1",
                model="embed",
            )
        )
    assert _FakeHttpClient.calls == [
        {"trust_env": False, "proxy": "http://127.0.0.1:7897"}
    ]


def test_embedding_uses_fixed_small_batches_before_retrying():
    def run():
        result = relation_stage.get_embedding(
            [f"text-{index}" for index in range(40)],
            api_key="key",
            api_url="https://example.test/v1/chat/completions",
            model="embed",
        )
        assert len(result) == 40
        assert [len(call) for call in _FakeEmbeddingOpenAI.calls] == [16, 16, 8]

    _with_fake_embedding_client(run)


def test_embedding_retries_only_the_failed_small_batch():
    def run():
        _FakeEmbeddingOpenAI.fail_first = True
        result = relation_stage.get_embedding(
            [f"text-{index}" for index in range(18)],
            api_key="key",
            api_url="https://example.test/v1",
            model="embed",
        )
        assert len(result) == 18
        assert [len(call) for call in _FakeEmbeddingOpenAI.calls] == [16, 16, 2]

    _with_fake_embedding_client(run)


def test_embedding_preserves_empty_positions_and_fails_batch_without_length_drift():
    def empty_run():
        result = relation_stage.get_embedding(
            ["alpha", " ", None, "beta"],
            api_key="key",
            api_url="https://example.test/v1",
            model="embed",
        )
        assert len(result) == 4
        assert result[1] == []
        assert result[2] == []
        assert _FakeEmbeddingOpenAI.calls == [["alpha", "beta"]]

    _with_fake_embedding_client(empty_run)

    def mismatch_run():
        _FakeEmbeddingOpenAI.short_response = True
        result = relation_stage.get_embedding(
            ["alpha", "beta"],
            api_key="key",
            api_url="https://example.test/v1",
            model="embed",
        )
        assert len(result) == 2
        assert result == [[], []]

    _with_fake_embedding_client(mismatch_run)


if __name__ == "__main__":
    test_select_relation_templates_keeps_graph_default()
    test_select_relation_templates_uses_formalization_profile()
    test_graph_prompt_uses_multi_criterion_evidence_contract()
    test_graph_logic_validator_enforces_publishable_contract()
    test_pair_guard_anchors_evidence_and_keeps_public_edge_shape()
    test_graph_logic_cache_version_and_review_tier()
    test_local_derivation_context_requires_bridge_and_rejects_foreign_proof()
    test_relation_input_enrichment_uses_source_envelope_without_mutating_nodes()
    test_linear_algebra_logic_regression_cases()
    test_invalid_relation_prompt_profile_raises()
    test_rule_filter_preserves_formalization_edge_fields()
    test_explicit_relations_gain_formalization_defaults()
    test_public_entrypoints_accept_relation_prompt_profile()
    test_public_pipeline_entrypoints_default_to_structured_edges()
    test_embedding_client_uses_explicit_embedding_proxy_only()
    test_embedding_client_can_force_direct_mode()
    test_embedding_client_ignores_chat_and_system_proxies()
    test_embedding_client_auto_detects_local_proxy()
    test_embedding_uses_fixed_small_batches_before_retrying()
    test_embedding_retries_only_the_failed_small_batch()
    test_embedding_preserves_empty_positions_and_fails_batch_without_length_drift()
    print("relation_prompt_profile tests passed")
