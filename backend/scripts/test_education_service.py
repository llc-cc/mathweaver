import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from education_service import (
    ASSESSMENT_CORRECTION_PROMPT,
    ASSESSMENT_QUESTION_KINDS,
    ASSESSMENT_SINGLE_CORRECTION_PROMPT,
    PATH_CORRECTION_PROMPT,
    PATH_DATA_TEMPLATE,
    PATH_PROMPT,
    assessment_category,
    build_assessment_tasks,
    build_learning_path,
    merge_ai_path,
    run_structured_education_tasks,
    validate_assessment_result,
    validate_single_assessment_result,
    validate_path_result,
    validate_proof_context_rebuild_result,
)


def _node(node_id, index=None):
    return {
        "id": node_id,
        "title_zh": f"节点 {node_id}",
        "node_index_in_doc": node_id if index is None else index,
    }


def _dependency(source, target, label="依赖"):
    return {"from": source, "to": target, "label": label, "description": "前置知识"}


def _assessment_result(category):
    return {
        "category": category,
        "questions": [
            {
                "kind": kind,
                "question": f"{kind} question",
                "focus": f"{kind} focus",
                "expectedPoints": [f"{kind} point"],
                "referenceAnswer": "reference answer",
            }
            for kind in ASSESSMENT_QUESTION_KINDS[category]
        ],
    }


class EducationServiceTests(unittest.TestCase):
    def test_assessment_categories_and_task_context_follow_node_types(self):
        nodes = [
            {**_node(1), "node_type": "定义", "content": "定义内容"},
            {**_node(2), "node_type": "定理", "content": "定理陈述", "conditions": ["条件"], "conclusions": ["结论"], "proof": "证明细节"},
            {**_node(3), "node_type": "例", "content": "例子内容"},
        ]
        path = {
            "steps": [
                {"nodeId": 1, "role": "prerequisite", "rationale": "定义基础"},
                {"nodeId": 2, "role": "target", "rationale": "最终目标"},
                {"nodeId": 3, "role": "prerequisite", "rationale": "迁移练习"},
            ],
            "edges": [{"from": 1, "to": 2}],
        }

        tasks = build_assessment_tasks(nodes, path)

        self.assertEqual(assessment_category("definition"), "definition")
        self.assertEqual(assessment_category("定理"), "theorem")
        self.assertEqual(assessment_category("example"), "general")
        self.assertEqual(tasks["1"]["requiredKinds"], list(ASSESSMENT_QUESTION_KINDS["definition"]))
        self.assertEqual(tasks["2"]["requiredKinds"], list(ASSESSMENT_QUESTION_KINDS["theorem"]))
        self.assertEqual(tasks["3"]["requiredKinds"], list(ASSESSMENT_QUESTION_KINDS["general"]))
        self.assertEqual(tasks["2"]["node"]["proof"], "证明细节")
        self.assertEqual(tasks["2"]["pathContext"]["prerequisites"], ["节点 1"])
        self.assertEqual(tasks["1"]["pathContext"]["successors"], ["节点 2"])

    def test_assessment_validator_requires_four_distinct_expected_kinds(self):
        valid = _assessment_result("theorem")
        self.assertTrue(validate_assessment_result(valid))

        missing = {**valid, "questions": valid["questions"][:-1]}
        duplicate_kind = {
            **valid,
            "questions": [*valid["questions"][:-1], {**valid["questions"][0], "question": "another"}],
        }
        duplicate_text = {
            **valid,
            "questions": [
                valid["questions"][0],
                {**valid["questions"][1], "question": "  WEAKEN_condition QUESTION  "},
                *valid["questions"][2:],
            ],
        }
        empty_focus = {
            **valid,
            "questions": [{**valid["questions"][0], "focus": ""}, *valid["questions"][1:]],
        }

        self.assertFalse(validate_assessment_result(missing))
        self.assertFalse(validate_assessment_result(duplicate_kind))
        self.assertFalse(validate_assessment_result(duplicate_text))
        self.assertFalse(validate_assessment_result(empty_focus))

    def test_single_assessment_validator_locks_the_requested_kind(self):
        result = {
            "category": "definition",
            "requiredKind": "motivation",
            "question": {
                "kind": "motivation",
                "question": "为什么引入这个定义？",
                "focus": "定义动机",
                "expectedPoints": ["说明待解决的问题"],
                "referenceAnswer": "reference answer",
            },
        }
        self.assertTrue(validate_single_assessment_result(result))
        result["question"]["kind"] = "application"
        self.assertFalse(validate_single_assessment_result(result))

    def test_assessment_batches_and_single_regeneration_use_the_same_wrapper(self):
        context = SimpleNamespace(
            llm=Mock(), parser=SimpleNamespace(parse_dict=Mock()), num_threads=8,
            llm_engine="api", claude_command="claude", claude_model=None,
            claude_agent=None, claude_batch_size=2, claude_timeout_seconds=30,
            claude_max_retries=1,
        )
        batch = {"1": _assessment_result("theorem"), "2": _assessment_result("definition")}
        single = {
            "q1": {
                "category": "theorem",
                "requiredKind": "proof_detail",
                "question": batch["1"]["questions"][3],
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("education_service.run_multiprocess_task", side_effect=[batch, single]) as runner:
                run_structured_education_tasks(
                    context=context,
                    tasks={"1": {"category": "theorem"}, "2": {"category": "definition"}},
                    task_kind="assessment",
                    checkpoint_dir=Path(temp_dir) / "assessment",
                )
                run_structured_education_tasks(
                    context=context,
                    tasks={"q1": {"category": "theorem", "requiredKind": "proof_detail"}},
                    task_kind="assessment_question",
                    checkpoint_dir=Path(temp_dir) / "question",
                )

        batch_call, single_call = runner.call_args_list
        self.assertEqual(batch_call.kwargs["stage_name"], "education_assessment")
        self.assertEqual(batch_call.kwargs["num_threads"], 2)
        self.assertTrue(batch_call.kwargs["validator"](batch["1"]))
        self.assertIs(batch_call.kwargs["correction_template"], ASSESSMENT_CORRECTION_PROMPT)
        self.assertIn("payload.category", ASSESSMENT_CORRECTION_PROMPT)
        self.assertEqual(single_call.kwargs["stage_name"], "education_assessment_question")
        self.assertEqual(single_call.kwargs["num_threads"], 1)
        self.assertTrue(single_call.kwargs["validator"](single["q1"]))
        self.assertIs(single_call.kwargs["correction_template"], ASSESSMENT_SINGLE_CORRECTION_PROMPT)
        self.assertIn("payload.requiredKind", ASSESSMENT_SINGLE_CORRECTION_PROMPT)

    def test_deterministic_rationale_is_teaching_focused_and_ignores_edge_text(self):
        nodes = [
            {"id": 1, "title_zh": "极限定义", "node_index_in_doc": 1},
            {"id": 2, "title_zh": "连续函数", "node_index_in_doc": 2},
            {"id": 3, "title_zh": "最终定理", "node_index_in_doc": 3},
        ]
        internal_description = "正则匹配：后置节点中显式引用 \\ref{node:N01}"
        edges = [
            {"from": 3, "to": 2, "label": "依赖", "description": internal_description},
            {"from": 2, "to": 1, "label": "依赖", "description": internal_description},
        ]

        path = build_learning_path(nodes, edges, 3)

        rationales = [step["rationale"] for step in path["steps"]]
        self.assertTrue(all(rationales))
        for rationale in rationales:
            self.assertNotIn("正则匹配", rationale)
            self.assertNotIn("后置节点", rationale)
            self.assertNotIn("显式引用", rationale)
            self.assertNotIn("\\ref{node:", rationale)
            self.assertNotIn("nodeId", rationale)
        self.assertIn("连续函数", rationales[0])
        self.assertIn("最终定理", rationales[0])
        self.assertIn("最终目标", rationales[-1])
        self.assertNotEqual(rationales[-1], "本次学习目标")

    def test_path_prompt_and_correction_require_student_facing_rationale(self):
        self.assertIn("作用和位置", PATH_PROMPT)
        self.assertIn("学习它的必要性", PATH_PROMPT)
        self.assertIn("不得直接复制边的 label 或 description", PATH_PROMPT)
        self.assertIn("正则匹配", PATH_PROMPT)
        self.assertIn("最终整合", PATH_PROMPT)
        self.assertIn("后续知识的基础", PATH_DATA_TEMPLATE)
        self.assertIn("重新输出", PATH_CORRECTION_PROMPT)
        self.assertIn("rationale", PATH_CORRECTION_PROMPT)

    def test_path_validator_rejects_empty_or_internal_rationale(self):
        valid = {"summary": "ok", "steps": [{"nodeId": 1, "rationale": "该节点是后续学习的基础。掌握它能帮助你完成最终目标。"}]}
        empty = {"summary": "ok", "steps": [{"nodeId": 1, "rationale": "  "}]}
        internal = {"summary": "ok", "steps": [{"nodeId": 1, "rationale": "正则匹配得到该节点。"}]}
        tex_reference = {"summary": "ok", "steps": [{"nodeId": 1, "rationale": "请先看 \\ref{node:N01}。"}]}

        self.assertTrue(validate_path_result(valid))
        self.assertFalse(validate_path_result(empty))
        self.assertFalse(validate_path_result(internal))
        self.assertFalse(validate_path_result(tex_reference))

    def test_learning_path_reverses_stored_edges_and_puts_target_last(self):
        nodes = [_node(1, 1), _node(2, 2), _node(3, 3), _node(4, 4), _node(99, 0)]
        edges = [
            _dependency(4, 2),
            _dependency(4, 3),
            _dependency(2, 1),
            _dependency(3, 1),
            {"from": 4, "to": 99, "label": "举例", "description": "拓展"},
            {"from": 4, "to": 99, "label": "无依赖", "description": "证明中未使用该节点"},
            _dependency(4, 2),
        ]

        path = build_learning_path(nodes, edges, 4)

        self.assertEqual([step["nodeId"] for step in path["steps"]], [1, 2, 3, 4])
        self.assertEqual(path["steps"][-1]["role"], "target")
        self.assertTrue(all(step["required"] for step in path["steps"]))
        self.assertTrue(path["steps"][-1]["required"])
        self.assertEqual(
            {(edge["from"], edge["to"]) for edge in path["edges"]},
            {(1, 2), (1, 3), (2, 4), (3, 4)},
        )
        self.assertNotIn(99, path["candidateNodeIds"])

    def test_learning_path_groups_cycles_and_keeps_target_last(self):
        path = build_learning_path(
            [_node(1), _node(2), _node(3)],
            [_dependency(3, 2), _dependency(2, 1), _dependency(1, 2)],
            3,
        )

        self.assertTrue(path["hasCycles"])
        self.assertEqual(path["steps"][-1]["nodeId"], 3)
        self.assertEqual(
            {step["nodeId"] for step in path["steps"] if step["cycle"]},
            {1, 2},
        )

    def test_merge_rejects_unknown_ids_and_illegal_dependency_order(self):
        deterministic = build_learning_path(
            [_node(1), _node(2), _node(3)],
            [_dependency(3, 2), _dependency(2, 1)],
            3,
        )
        unknown = {
            "summary": "bad",
            "steps": [
                {"nodeId": 1, "rationale": "a"},
                {"nodeId": 999, "rationale": "b"},
                {"nodeId": 3, "rationale": "c"},
            ],
        }
        illegal = {
            "summary": "bad order",
            "steps": [
                {"nodeId": 2, "rationale": "b"},
                {"nodeId": 1, "rationale": "a"},
                {"nodeId": 3, "rationale": "c"},
            ],
        }

        self.assertIs(merge_ai_path(deterministic, unknown), deterministic)
        self.assertIs(merge_ai_path(deterministic, illegal), deterministic)

    def test_merge_preserves_required_steps(self):
        deterministic = build_learning_path([_node(1), _node(2)], [_dependency(2, 1)], 2)
        deterministic["steps"][0]["required"] = True
        ai_result = {
            "summary": "ok",
            "steps": [
                {"nodeId": 1, "required": False, "rationale": "先学一"},
                {"nodeId": 2, "required": False, "rationale": "目标"},
            ],
        }

        merged = merge_ai_path(deterministic, ai_result)

        self.assertEqual([step["required"] for step in merged["steps"]], [True, True])

    def test_single_task_uses_wrapper_and_context_parser(self):
        parser = SimpleNamespace(parse_dict=Mock(name="parse_dict"))
        context = SimpleNamespace(
            llm=Mock(name="llm"),
            parser=parser,
            num_threads=8,
            llm_engine="api",
            claude_command="claude",
            claude_model=None,
            claude_agent=None,
            claude_batch_size=1,
            claude_timeout_seconds=30,
            claude_max_retries=1,
        )
        result = {"one": {"summary": "ok", "steps": [{"nodeId": 1, "rationale": "目标"}]}}

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "checkpoint"
            with patch("education_service.run_multiprocess_task", return_value=result) as runner:
                actual = run_structured_education_tasks(
                    context=context,
                    tasks={"one": {"allowedNodeIds": [1]}},
                    task_kind="path",
                    checkpoint_dir=checkpoint,
                )

            self.assertEqual(actual, result)
            self.assertIs(runner.call_args.kwargs["llm"], context.llm)
            self.assertIs(runner.call_args.kwargs["parse_method"], parser.parse_dict)
            self.assertEqual(runner.call_args.kwargs["num_threads"], 1)
            self.assertIn(temp_dir, runner.call_args.kwargs["checkpoint_dir"])
            self.assertIs(runner.call_args.kwargs["correction_template"], PATH_CORRECTION_PROMPT)
            self.assertTrue(runner.call_args.kwargs["validator"](result["one"]))

    def test_batch_tasks_use_the_same_wrapper_and_worker_limit(self):
        context = SimpleNamespace(
            llm=Mock(), parser=SimpleNamespace(parse_dict=Mock()), num_threads=8,
            llm_engine="api", claude_command="claude", claude_model=None,
            claude_agent=None, claude_batch_size=2, claude_timeout_seconds=30,
            claude_max_retries=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("education_service.run_multiprocess_task", return_value={}) as runner:
                run_structured_education_tasks(
                    context=context,
                    tasks={"one": {"allowedNodeIds": [1]}, "two": {"allowedNodeIds": [2]}},
                    task_kind="personalize",
                    checkpoint_dir=Path(temp_dir) / "checkpoint",
                )
        self.assertEqual(runner.call_args.kwargs["num_threads"], 2)
        self.assertEqual(set(runner.call_args.kwargs["index_dict"]), {"one", "two"})

    def test_pending_proof_context_rebuild_uses_structured_batch_validation(self):
        context = SimpleNamespace(
            llm=Mock(), parser=SimpleNamespace(parse_dict=Mock()), num_threads=4,
            llm_engine="api", claude_command="claude", claude_model=None,
            claude_agent=None, claude_batch_size=2, claude_timeout_seconds=30,
            claude_max_retries=1,
        )
        result = {"interaction-1": {"learningDelta": [{
            "kind": "gap", "claim": "缺少条件检查", "confidence": 0.8,
            "severity": "medium", "relatedNodeIds": [1],
        }]}}
        self.assertTrue(validate_proof_context_rebuild_result(result["interaction-1"]))
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("education_service.run_multiprocess_task", return_value=result) as runner:
                actual = run_structured_education_tasks(
                    context=context,
                    tasks={"interaction-1": {"allowedNodeIds": [1]}},
                    task_kind="proof_context_rebuild",
                    checkpoint_dir=Path(temp_dir) / "checkpoint",
                )
        self.assertEqual(actual, result)
        self.assertTrue(runner.call_args.kwargs["validator"](result["interaction-1"]))

    def test_wrapper_failure_is_propagated_for_deterministic_fallback(self):
        context = SimpleNamespace(
            llm=Mock(), parser=SimpleNamespace(parse_dict=Mock()), num_threads=1,
            llm_engine="api", claude_command="claude", claude_model=None,
            claude_agent=None, claude_batch_size=1, claude_timeout_seconds=1,
            claude_max_retries=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("education_service.run_multiprocess_task", side_effect=TimeoutError("timeout")):
                with self.assertRaises(TimeoutError):
                    run_structured_education_tasks(
                        context=context,
                        tasks={"one": {"allowedNodeIds": [1]}},
                        task_kind="path",
                        checkpoint_dir=Path(temp_dir) / "checkpoint",
                    )

    def test_service_does_not_introduce_parallel_or_llm_clients(self):
        source = (BACKEND_ROOT / "education_service.py").read_text(encoding="utf-8")
        self.assertNotIn("ThreadPoolExecutor", source)
        self.assertNotIn("asyncio.gather", source)
        self.assertNotIn("SimpleLLM", source)


if __name__ == "__main__":
    unittest.main()
