import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.stages.build_relations import stage as relation_stage
from pipeline.stages.build_relations.relation_retrieval import (
    EmbeddingRetrievalError,
    RelationCandidate,
    RelationRetrievalConfig,
    apply_rerank_results,
    build_rerank_tasks,
    retrieve_relation_candidates,
    select_final_candidates,
    tokenize_math_text,
    normalize_rerank_result,
    validate_rerank_result,
)


def _logic_node(index, *, condition="", conclusion="", proof="", parent_id=None):
    node = {
        "global_id": parent_id or f"logic-{index}",
        "parent_global_id": parent_id or f"logic-{index}",
        "sub_index": 1,
        "is_virtual_subnode": True,
        "node_type": "theorem",
        "title": {"english": f"Statement {index}", "chinese": ""},
        "content": f"If {condition}, then {conclusion}.",
        "conditions": [{"text": condition, "text_normalized": condition}] if condition else [],
        "conclusions": [{"text": conclusion, "text_normalized": conclusion}] if conclusion else [],
        "proof": proof,
        "label": f"T{index}",
    }
    return node


def _definition_node(index, term):
    return {
        "global_id": f"definition-{index}",
        "parent_global_id": f"definition-{index}",
        "sub_index": 1,
        "is_virtual_subnode": True,
        "node_type": "definition",
        "title": {"english": term, "chinese": ""},
        "content": f"A {term} is a mathematical object satisfying the common structural property.",
        "conditions": [],
        "conclusions": [{"text": f"definition of {term}", "text_normalized": f"definition of {term}"}],
        "proof": "",
        "label": f"Definition {index}",
        "reference_aliases": [term],
    }


def _sparse_config(**overrides):
    values = {"mode": "sparse_preview"}
    values.update(overrides)
    return RelationRetrievalConfig(**values)


def test_math_tokenizer_keeps_latex_identifiers_and_chinese_ngrams():
    tokens = tokenize_math_text(r"令 x 属于紧致空间 compact_space，且 \subset K")
    assert r"\subset" in tokens
    assert "compact_space" in tokens
    assert "紧致" in tokens
    assert "x" not in tokens


def test_long_distance_dependency_survives_beyond_old_top_five():
    nodes = [
        _logic_node(
            0,
            condition="the space is compact",
            conclusion="every open cover admits a finite subcover",
        )
    ]
    for index in range(1, 44):
        nodes.append(
            _logic_node(
                index,
                condition=f"unrelated assumption {index}",
                conclusion=f"unrelated algebraic conclusion {index}",
            )
        )
    nodes.append(
        _logic_node(
            44,
            condition="the open cover has a finite subcover",
            conclusion="a finite refinement exists",
        )
    )

    candidates, report = retrieve_relation_candidates(nodes, config=_sparse_config())
    selected = select_final_candidates(candidates, config=_sparse_config())

    assert any(
        item.dependent_index == 44 and item.support_index == 0 and item.relation_kind == "logic"
        for item in selected
    )
    assert report["distance_buckets"][">30"] > 0
    assert all(item.support_index < item.dependent_index for item in candidates)


def test_proof_reference_channel_protects_named_logic_support():
    support = _logic_node(0, conclusion="A(BC)=(AB)C")
    support["title"] = {
        "english": "Associativity of Matrix Multiplication",
        "chinese": "矩阵乘法结合律",
    }
    dependent = _logic_node(
        1,
        condition="A and B are invertible",
        conclusion="AB is invertible",
        proof="By Associativity of Matrix Multiplication, the inverse identity follows.",
    )

    candidates, _ = retrieve_relation_candidates(
        [support, dependent],
        config=_sparse_config(),
    )

    candidate = next(
        item
        for item in candidates
        if item.dependent_index == 1
        and item.support_index == 0
        and item.relation_kind == "logic"
    )
    assert "proof_reference" in candidate.retrieval_channels
    assert candidate.protected is True


def test_same_parent_units_and_explicit_pairs_are_excluded():
    nodes = [
        _logic_node(0, conclusion="P", parent_id="shared-parent"),
        _logic_node(1, condition="P", conclusion="Q", parent_id="shared-parent"),
        _logic_node(2, condition="Q", conclusion="R"),
    ]
    candidates, _ = retrieve_relation_candidates(
        nodes,
        config=_sparse_config(),
        excluded_pair_ids={("logic-2", "shared-parent")},
    )

    assert not any(item.dependent_index == 1 and item.support_index == 0 for item in candidates)
    assert not any(item.dependent_index == 2 and item.support_global_id == "shared-parent" for item in candidates)


def test_definition_retrieval_replaces_all_to_all_and_respects_final_cap():
    nodes = [_definition_node(index, f"concept_{index}") for index in range(25)]
    nodes.append(
        _logic_node(
            25,
            condition="concept_0 has the common structural property",
            conclusion="the target assertion follows",
        )
    )
    config = _sparse_config()

    candidates, _ = retrieve_relation_candidates(nodes, config=config)
    selected = select_final_candidates(candidates, config=config)
    target_definitions = [
        item for item in selected if item.dependent_index == 25 and item.relation_kind == "definition"
    ]

    assert 1 <= len(target_definitions) <= 10
    assert any(item.support_index == 0 for item in target_definitions)
    assert len(target_definitions) < 25


def test_final_budget_is_20_logic_plus_10_definition():
    candidates = []
    for index in range(25):
        candidates.append(
            RelationCandidate(
                candidate_id=f"logic-{index}",
                dependent_global_id="dependent",
                support_global_id=f"l-{index}",
                dependent_index=50,
                support_index=index,
                relation_kind="logic",
                rrf_score=1.0 / (index + 1),
                rerank_score=3.0,
            )
        )
    for index in range(15):
        candidates.append(
            RelationCandidate(
                candidate_id=f"definition-{index}",
                dependent_global_id="dependent",
                support_global_id=f"d-{index}",
                dependent_index=50,
                support_index=index,
                relation_kind="definition",
                rrf_score=1.0 / (index + 1),
                rerank_score=3.0,
            )
        )

    selected = select_final_candidates(candidates)

    assert len(selected) == 30
    assert sum(item.relation_kind == "logic" for item in selected) == 20
    assert sum(item.relation_kind == "definition" for item in selected) == 10


def test_unused_definition_budget_transfers_only_to_logic():
    candidates = [
        RelationCandidate(
            candidate_id=f"logic-{index}",
            dependent_global_id="dependent",
            support_global_id=f"l-{index}",
            dependent_index=50,
            support_index=index,
            relation_kind="logic",
            rrf_score=1.0,
        )
        for index in range(30)
    ]
    candidates.extend(
        RelationCandidate(
            candidate_id=f"definition-{index}",
            dependent_global_id="dependent",
            support_global_id=f"d-{index}",
            dependent_index=50,
            support_index=index,
            relation_kind="definition",
            rrf_score=1.0,
        )
        for index in range(3)
    )

    selected = select_final_candidates(candidates)

    assert sum(item.relation_kind == "definition" for item in selected) == 3
    assert sum(item.relation_kind == "logic" for item in selected) == 27


def test_hybrid_strict_rejects_any_missing_embedding():
    nodes = [
        _logic_node(0, conclusion="P"),
        _logic_node(1, condition="P", conclusion="Q"),
    ]

    try:
        retrieve_relation_candidates(
            nodes,
            config=RelationRetrievalConfig(),
            embedding_model="fake-embedding",
            embed_texts=lambda texts: [[] for _ in texts],
        )
    except EmbeddingRetrievalError as exc:
        assert exc.report["status"] == "embedding_failed"
        assert exc.report["publishable"] is False
        assert exc.report["embedding"]["failed"] > 0
    else:
        raise AssertionError("hybrid_strict must reject empty embeddings")


def test_embedding_cache_is_keyed_by_model_and_text():
    nodes = [
        _logic_node(0, conclusion="compactness gives a finite subcover"),
        _logic_node(1, condition="finite subcover", conclusion="finite refinement"),
    ]
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[float(sum(ord(char) for char in text) % 97 + 1), 1.0] for text in texts]

    with tempfile.TemporaryDirectory() as temp_dir:
        retrieve_relation_candidates(
            nodes,
            config=RelationRetrievalConfig(),
            embedding_model="fake-embedding",
            embed_texts=embed,
            output_dir=temp_dir,
        )
        first_call_count = len(calls)
        retrieve_relation_candidates(
            nodes,
            config=RelationRetrievalConfig(),
            embedding_model="fake-embedding",
            embed_texts=embed,
            output_dir=temp_dir,
        )

        assert first_call_count > 0
        assert len(calls) == first_call_count
        payload = json.loads(Path(temp_dir, "relation_embedding_cache.json").read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["vectors"]


def test_rerank_tasks_require_every_candidate_once():
    nodes = [_logic_node(0, conclusion="P"), _logic_node(1, condition="P", conclusion="Q")]
    candidate = RelationCandidate(
        candidate_id="a" * 24,
        dependent_global_id="logic-1",
        support_global_id="logic-0",
        dependent_index=1,
        support_index=0,
        relation_kind="logic",
        retrieval_channels=["bm25f"],
        rrf_score=0.1,
    )
    tasks = build_rerank_tasks([candidate], nodes, batch_size=20)
    task = next(iter(tasks.values()))

    assert validate_rerank_result(task, {"ranked": [{"candidate_id": "a" * 24, "score": 3}]})
    assert not validate_rerank_result(task, {"ranked": []})
    assert not validate_rerank_result(
        task,
        {"ranked": [{"candidate_id": "a" * 24, "score": 3}, {"candidate_id": "a" * 24, "score": 2}]},
    )


def test_logic_rerank_task_contains_source_evidence_and_contract_version():
    nodes = [_logic_node(0, conclusion="P"), _logic_node(1, condition="P", conclusion="Q")]
    nodes[1]["source_original_form"] = "By P, Q follows."
    nodes[1]["derivation_context"] = "Therefore we obtain Q."
    candidate = RelationCandidate(
        candidate_id="b" * 24,
        dependent_global_id="logic-1",
        support_global_id="logic-0",
        dependent_index=1,
        support_index=0,
        relation_kind="logic",
        rrf_score=0.1,
    )

    task = next(iter(build_rerank_tasks([candidate], nodes).values()))
    dependent = json.loads(task["dependent_json"])
    supports = json.loads(task["candidates_json"])

    assert task["logic_prompt_contract_version"] == 2
    assert dependent["source_original_form"] == "By P, Q follows."
    assert dependent["derivation_context"] == "Therefore we obtain Q."
    assert supports[0]["content"]


class _RerankLLM:
    def __init__(self):
        self.calls = 0

    def ask(self, prompt, temperature=0.7):
        del temperature
        self.calls += 1
        candidate_ids = list(dict.fromkeys(re.findall(r"[0-9a-f]{24}", prompt)))
        return json.dumps(
            {"ranked": [{"candidate_id": candidate_id, "score": 2} for candidate_id in candidate_ids]}
        )


class _PipelineLLM(_RerankLLM):
    model = "fake-chat"

    def ask(self, prompt, temperature=0.7):
        if "候选重排器" in prompt:
            return super().ask(prompt, temperature=temperature)
        self.calls += 1
        return json.dumps(
            {
                "契约版本": 2,
                "出发节点": "logic-1",
                "到达节点": "logic-0",
                "关系": "逻辑依赖",
                "依赖判据": "premise_support",
                "证据等级": "direct",
                "匹配证据": [
                    {
                        "A字段": "conclusions",
                        "A片段": "P",
                        "B字段": "conditions",
                        "B片段": "P",
                        "作用": "前置结论满足后置条件。",
                    }
                ],
                "变量对应": {},
                "缺失前提": [],
                "排除检查": {
                    "仅主题相似": False,
                    "仅定义使用": False,
                    "方向冲突": False,
                    "作用域冲突": False,
                    "使用外部知识": False,
                },
                "发布状态": "accepted",
                "置信度": 0.98,
                "理由": "前置结论 P 满足后置条件 P。",
            },
            ensure_ascii=False,
        )


def test_listwise_rerank_uses_recoverable_task_and_cache():
    nodes = [_logic_node(0, conclusion="P"), _logic_node(1, condition="P", conclusion="Q")]
    candidates, _ = retrieve_relation_candidates(nodes, config=_sparse_config())
    llm = _RerankLLM()
    with tempfile.TemporaryDirectory() as temp_dir:
        context = SimpleNamespace(
            output_dir=temp_dir,
            llm=llm,
            parser=SimpleNamespace(parse_dict=json.loads),
            num_threads=1,
            checkpoint=100,
        )
        relation_stage._execute_candidate_rerank(context, candidates, nodes, _sparse_config())
        first_calls = llm.calls
        relation_stage._execute_candidate_rerank(context, candidates, nodes, _sparse_config())

        assert first_calls > 0
        assert llm.calls == first_calls
        assert all(candidate.rerank_score == 2 for candidate in candidates)
        assert Path(temp_dir, "relation_rerank_cache.json").exists()


def test_rerank_result_repairs_one_unambiguous_candidate_id_typo():
    expected_id = "ce47b05d71d609c17d35b14a"
    typo_id = "ce47b05d71c609c17d35b14a"
    other_id = "89abcdef0123456789abcdef"
    task = {"candidate_ids": [expected_id, other_id]}
    result = {
        "ranked": [
            {"candidate_id": typo_id, "score": 3},
            {"candidate_id": other_id, "score": 1},
        ]
    }
    candidates = [
        RelationCandidate(expected_id, "dependent", "support-1", 1, 0, "logic"),
        RelationCandidate(other_id, "dependent", "support-2", 1, 0, "logic"),
    ]

    assert validate_rerank_result(task, result)
    apply_rerank_results(candidates, {"task": task}, {"task": result})
    assert [candidate.rerank_score for candidate in candidates] == [3, 1]

    ambiguous_result = {
        "ranked": [
            {"candidate_id": "ce47b05d71c609c17d35b14b", "score": 3},
            {"candidate_id": other_id, "score": 1},
        ]
    }
    assert not validate_rerank_result(task, ambiguous_result)


def test_rerank_result_repairs_one_omitted_hex_character():
    expected_id = "fa7a39555cda7aa61f855b3f"
    omitted_id = "fa7a3955cda7aa61f855b3f"
    other_id = "89abcdef0123456789abcdef"
    task = {"candidate_ids": [expected_id, other_id]}
    result = {
        "ranked": [
            {"candidate_id": omitted_id, "score": 3},
            {"candidate_id": other_id, "score": 1},
        ]
    }

    normalized = validate_rerank_result(task, result)
    repaired = normalize_rerank_result(task, result)

    assert normalized is True
    assert repaired["ranked"][0]["candidate_id"] == expected_id


def test_full_relation_stage_writes_candidates_report_and_edges():
    nodes = [_logic_node(0, conclusion="P"), _logic_node(1, condition="P", conclusion="Q")]
    original_get_embedding = relation_stage.get_embedding
    embedding_calls = []

    def fake_get_embedding(texts, api_key, api_url, model):
        embedding_calls.append((api_key, api_url, model))
        return [[float(sum(ord(char) for char in text) % 97 + 1), 1.0] for text in texts]

    relation_stage.get_embedding = fake_get_embedding
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            llm = _PipelineLLM()
            context = SimpleNamespace(
                output_dir=temp_dir,
                api_key="key",
                api_url="https://example.test/v1",
                embedding_api_key="embedding-key",
                embedding_api_url="https://embedding.example.test/v1",
                embedding_model_name="fake-embedding",
                relation_retrieval_mode="hybrid_strict",
                execution_mode="pipeline",
                llm=llm,
                model_name="fake-chat",
                parser=SimpleNamespace(parse_dict=json.loads),
                num_threads=1,
                checkpoint=100,
            )
            state = {"node_dict": {index: node for index, node in enumerate(nodes)}}

            result = relation_stage.run(context, state)

            assert len(result["edge_list"]) == 1
            assert result["edge_list"][0]["关系"] == "逻辑依赖"
            assert result["relation_retrieval_report"]["publishable"] is True
            assert result["relation_retrieval_report"]["pairwise_task_count"] == 1
            assert Path(temp_dir, "relation_candidates.json").exists()
            assert Path(temp_dir, "relation_retrieval_report.json").exists()
            assert Path(temp_dir, "relation_embedding_cache.json").exists()
            assert Path(temp_dir, "relation_rerank_cache.json").exists()
            assert embedding_calls
            assert all(
                call == ("embedding-key", "https://embedding.example.test/v1", "fake-embedding")
                for call in embedding_calls
            )
    finally:
        relation_stage.get_embedding = original_get_embedding


def test_strict_embedding_failure_does_not_update_canonical_edges():
    nodes = [_logic_node(0, conclusion="P"), _logic_node(1, condition="P", conclusion="Q")]
    state = {"node_dict": {index: node for index, node in enumerate(nodes)}, "edge_list": [{"old": True}]}
    original_get_embedding = relation_stage.get_embedding
    relation_stage.get_embedding = lambda texts, *args, **kwargs: [[] for _ in texts]
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = SimpleNamespace(
                output_dir=temp_dir,
                api_key="key",
                api_url="https://example.test/v1",
                embedding_api_key="embedding-key",
                embedding_api_url="https://embedding.example.test/v1",
                embedding_model_name="fake-embedding",
                relation_retrieval_mode="hybrid_strict",
                execution_mode="pipeline",
            )
            try:
                relation_stage.run(context, state)
            except EmbeddingRetrievalError:
                pass
            else:
                raise AssertionError("strict relation retrieval should block the stage")

            assert state["edge_list"] == [{"old": True}]
            report = json.loads(Path(temp_dir, "relation_retrieval_report.json").read_text(encoding="utf-8"))
            assert report["publishable"] is False
    finally:
        relation_stage.get_embedding = original_get_embedding


def test_invalid_rerank_partial_is_pruned_before_recovery():
    tasks = {
        "valid-task": {"candidate_ids": ["candidate-a"]},
        "invalid-task": {"candidate_ids": ["candidate-b"]},
    }
    partial = {
        "valid-task": {
            "ranked": [{"candidate_id": "candidate-a", "score": 3}],
        },
        "invalid-task": {
            "ranked": [{"candidate_id": "wrong-candidate", "score": 3}],
        },
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        partial_path = Path(temp_dir) / "partial_result_dict.json"
        partial_path.write_text(json.dumps(partial), encoding="utf-8")

        removed = relation_stage._prune_invalid_rerank_partial(
            {"run_dir": temp_dir},
            tasks,
        )
        retained = json.loads(partial_path.read_text(encoding="utf-8"))

    assert removed == 1
    assert list(retained) == ["valid-task"]


def test_unique_forward_explicit_reference_remains_a_direct_edge():
    nodes = [
        {
            **_logic_node(0, conclusion="Q"),
            "reference_signals": {
                "explicit_targets": [
                    {"resolved_index": 1, "surface": "Definition 1", "match_mode": "forward_unique"}
                ],
                "relative_references": [],
            },
        },
        _definition_node(1, "forward concept"),
    ]

    edges, pairs = relation_stage.extract_explicit_relations(nodes)

    assert pairs == {(0, 1)}
    assert len(edges) == 1
    assert edges[0]["关系"] == "定义依赖"


if __name__ == "__main__":
    test_math_tokenizer_keeps_latex_identifiers_and_chinese_ngrams()
    test_long_distance_dependency_survives_beyond_old_top_five()
    test_proof_reference_channel_protects_named_logic_support()
    test_same_parent_units_and_explicit_pairs_are_excluded()
    test_definition_retrieval_replaces_all_to_all_and_respects_final_cap()
    test_final_budget_is_20_logic_plus_10_definition()
    test_unused_definition_budget_transfers_only_to_logic()
    test_hybrid_strict_rejects_any_missing_embedding()
    test_embedding_cache_is_keyed_by_model_and_text()
    test_rerank_tasks_require_every_candidate_once()
    test_logic_rerank_task_contains_source_evidence_and_contract_version()
    test_listwise_rerank_uses_recoverable_task_and_cache()
    test_rerank_result_repairs_one_unambiguous_candidate_id_typo()
    test_rerank_result_repairs_one_omitted_hex_character()
    test_full_relation_stage_writes_candidates_report_and_edges()
    test_strict_embedding_failure_does_not_update_canonical_edges()
    test_invalid_rerank_partial_is_pruned_before_recovery()
    test_unique_forward_explicit_reference_remains_a_direct_edge()
    print("relation retrieval tests passed")
