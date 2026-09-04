import base64
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import api_v2
import education_gamification
import student_context
from education_service import validate_direct_scoring_standard_result
from integrations.neo4j_handler import get_graph_store, reset_graph_store
from storage.database import reset_engine
from scripts.migrate_storage import TARGET_TABLE_ORDER


NODES = [
    {"id": 1, "title_zh": "线性无关", "content": "向量组线性无关", "node_index_in_doc": 1},
    {"id": 2, "title_zh": "基与维数", "content": "基与维数", "node_index_in_doc": 2},
    {"id": 3, "title_zh": "基扩张定理", "content": "基扩张定理", "node_index_in_doc": 3},
]
EDGES = [
    {"from": 3, "to": 2, "label": "依赖", "description": "需要基与维数"},
    {"from": 2, "to": 1, "label": "定义引用", "description": "引用线性无关"},
]
ZERO_NODES = [
    {"id": 0, "title_zh": "基础定义", "content": "基础定义", "node_index_in_doc": 0},
    {"id": 1, "title_zh": "最终结论", "content": "最终结论", "node_index_in_doc": 1},
]
ZERO_EDGES = [
    {"from": 1, "to": 0, "label": "依赖", "description": "需要基础定义"},
]


def _assessment_result(category="general", prefix="generated"):
    return {
        "category": category,
        "questions": [
            {
                "kind": kind,
                "question": f"{prefix} {kind}",
                "focus": f"focus {kind}",
                "expectedPoints": [f"expected {kind}"],
                "referenceAnswer": "reference answer",
            }
            for kind in api_v2.ASSESSMENT_QUESTION_KINDS[category]
        ],
    }


class EducationApiTests(unittest.TestCase):
    TEACHER_EMAIL = "2353877811@qq.com"
    TEACHER_PASSWORD = "x2353877811"

    def setUp(self):
        if os.environ.get("MATHWEAVER_INTEGRATION_TESTS") != "1":
            self.skipTest("set MATHWEAVER_INTEGRATION_TESTS=1 for Docker MySQL/Neo4j tests")
        database_url = os.environ.get("MATHWEAVER_TEST_DATABASE_URL", "").strip()
        neo4j_uri = os.environ.get("MATHWEAVER_TEST_NEO4J_URI", "").strip()
        neo4j_password_file = os.environ.get("MATHWEAVER_TEST_NEO4J_PASSWORD_FILE", "").strip()
        if not database_url or not neo4j_uri or not neo4j_password_file:
            self.skipTest("test MySQL/Neo4j credentials are not configured")

        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp_dir.name)
        data_key_file = root / "data-key.txt"
        data_key_file.write_text(
            base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("="),
            encoding="utf-8",
        )
        self.previous = (
            api_v2._DATA_ROOT,
            api_v2._SOURCE_PDF_ROOT,
            api_v2._EDUCATION_ROOT,
            api_v2._EDUCATION_SNAPSHOT_ROOT,
            api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
        )
        api_v2._DATA_ROOT = root
        api_v2._SOURCE_PDF_ROOT = root / "uploads" / "source_pdfs"
        api_v2._EDUCATION_ROOT = root / "education"
        api_v2._EDUCATION_SNAPSHOT_ROOT = root / "education" / "snapshots"
        api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT = root / "education" / "assignment_sources"
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": database_url,
                "NEO4J_URI": neo4j_uri,
                "NEO4J_USER": os.environ.get("MATHWEAVER_TEST_NEO4J_USER", "neo4j"),
                "NEO4J_PASSWORD_FILE": neo4j_password_file,
                "MATHWEAVER_DATA_KEY_FILE": str(data_key_file),
                "MATHGRAPH_DATA_DIR": str(root),
                "MATHWEAVER_EDU_ENABLED": "1",
                "MATHWEAVER_EDU_AI_DAILY_LIMIT": "50",
                "MATHWEAVER_EDU_LLM_API_URL": "",
                "MATHWEAVER_EDU_LLM_MODEL": "",
                "MATHWEAVER_EDU_LLM_API_KEY": "",
                "PDFPIPELINE_API_URL": "",
                "PDFPIPELINE_MODEL_NAME": "",
                "PDFPIPELINE_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        )
        self.env.start()
        reset_engine()
        reset_graph_store()
        with api_v2.connect_database() as connection:
            connection.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in reversed((*TARGET_TABLE_ORDER, "education_course_graph_order", "graph_registry")):
                connection.execute(f"DELETE FROM `{table}`")
            connection.execute("SET FOREIGN_KEY_CHECKS = 1")
        store = get_graph_store()
        with store.driver.session(database=store.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
        api_v2._jobs.clear()
        api_v2._job_runtimes.clear()
        with api_v2.connect_database() as connection:
            connection.execute(
                "INSERT INTO users (email, password_hash, created_at, can_teach) VALUES (?, ?, ?, 1)",
                (
                    self.TEACHER_EMAIL,
                    api_v2.generate_password_hash(self.TEACHER_PASSWORD),
                    datetime.utcnow().isoformat(),
                ),
            )
        api_v2.app.config.update(TESTING=True)
        self.client = api_v2.app.test_client()
        self.teacher = self._login(self.TEACHER_EMAIL, self.TEACHER_PASSWORD, "teacher")
        self.student = self._register("student@example.com")
        self.outsider = self._register("outside@example.com")

    def tearDown(self):
        if not hasattr(self, "env"):
            return
        reset_graph_store()
        reset_engine()
        self.env.stop()
        (
            api_v2._DATA_ROOT,
            api_v2._SOURCE_PDF_ROOT,
            api_v2._EDUCATION_ROOT,
            api_v2._EDUCATION_SNAPSHOT_ROOT,
            api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT,
        ) = self.previous
        self.temp_dir.cleanup()

    def _register(self, email):
        response = self.client.post(
            "/api/v2/auth/register",
            json={"email": email, "password": "secret12", "educationRole": "student"},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["token"]

    def _login(self, email, password, role):
        response = self.client.post(
            "/api/v2/auth/login",
            json={"email": email, "password": password, "educationRole": role},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["token"]

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def _save_llm_config(self, token, *, api_url="https://user.example.test/v1", model_name="user-model", api_key="user-key"):
        response = self.client.put(
            "/api/v2/settings",
            json={
                "configs": [
                    {"name": "备用", "api_url": "https://unused.example.test/v1", "model_name": "unused", "api_key": "unused-key"},
                    {"name": "当前", "api_url": api_url, "model_name": model_name, "api_key": api_key},
                ],
                "active_index": 1,
            },
            headers=self._headers(token),
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_education_llm_config_prefers_system_and_falls_back_to_active_user(self):
        self._save_llm_config(self.teacher)
        with api_v2.connect_database() as connection:
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)
            ).fetchone()[0]

        with api_v2.app.app_context():
            user_config = api_v2._education_llm_config(teacher_id)
        self.assertEqual(user_config["api_url"], "https://user.example.test/v1")
        self.assertEqual(user_config["model_name"], "user-model")
        self.assertEqual(user_config["api_key"], "user-key")

        partial_dedicated = {
            "MATHWEAVER_EDU_LLM_API_URL": "https://partial.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "",
            "MATHWEAVER_EDU_LLM_API_KEY": "",
        }
        with patch.dict(os.environ, partial_dedicated, clear=False):
            with api_v2.app.app_context():
                partial_config = api_v2._education_llm_config(teacher_id)
        self.assertEqual(partial_config["api_url"], "https://user.example.test/v1")

        generic = {
            "PDFPIPELINE_API_URL": "https://generic.example.test/v1",
            "PDFPIPELINE_MODEL_NAME": "generic-model",
            "PDFPIPELINE_API_KEY": "generic-key",
            "MATHWEAVER_EDU_LLM_API_URL": "https://partial.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "",
            "MATHWEAVER_EDU_LLM_API_KEY": "",
        }
        with patch.dict(os.environ, generic, clear=False):
            with api_v2.app.app_context():
                generic_config = api_v2._education_llm_config(teacher_id)
        self.assertEqual(generic_config["api_url"], "https://generic.example.test/v1")

        dedicated = {
            **generic,
            "MATHWEAVER_EDU_LLM_API_URL": "https://education.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "education-model",
            "MATHWEAVER_EDU_LLM_API_KEY": "education-key",
        }
        with patch.dict(os.environ, dedicated, clear=False):
            with api_v2.app.app_context():
                dedicated_config = api_v2._education_llm_config(teacher_id)
        self.assertEqual(dedicated_config["api_url"], "https://education.example.test/v1")
        self.assertEqual(dedicated_config["model_name"], "education-model")

        status = self.client.get("/api/v2/edu/status", headers=self._headers(self.teacher))
        self.assertEqual(status.status_code, 200, status.get_json())
        self.assertTrue(status.get_json()["aiAvailable"])
        self.assertNotIn("user-key", json.dumps(status.get_json()))

        with patch.object(api_v2, "create_education_context", return_value=object()) as create_context:
            with patch.object(api_v2, "run_structured_education_tasks", return_value={"probe": {"ok": True}}):
                with api_v2.app.app_context():
                    result = api_v2._education_ai_tasks(
                        user_id=teacher_id,
                        task_id="config-fallback-probe",
                        task_kind="assessment",
                        tasks={"probe": {}},
                        scope="tests/config-fallback",
                    )
        self.assertEqual(result, {"probe": {"ok": True}})
        self.assertEqual(create_context.call_args.args[1]["api_key"], "user-key")

        with patch.object(api_v2, "create_education_context", return_value=object()):
            with patch.object(
                api_v2,
                "run_structured_education_tasks",
                side_effect=RuntimeError("provider rejected user-key"),
            ):
                with api_v2.app.app_context():
                    with self.assertRaises(RuntimeError) as failure:
                        api_v2._education_ai_tasks(
                            user_id=teacher_id,
                            task_id="config-redaction-probe",
                            task_kind="assessment",
                            tasks={"probe": {}},
                            scope="tests/config-redaction",
                        )
        self.assertNotIn("user-key", str(failure.exception))
        with api_v2.connect_database() as connection:
            stored_error = connection.execute(
                "SELECT error FROM education_ai_tasks WHERE task_key = 'config-redaction-probe'"
            ).fetchone()[0]
        self.assertNotIn("user-key", stored_error)
        self.assertIn("[redacted]", stored_error)

    def test_direct_question_standard_generation_is_single_teacher_task(self):
        generated = {
            "referenceAnswer": "先列出已知条件，再给出结论。",
            "focus": "检查学生是否说明了条件与结论之间的关系。",
            "expectedPoints": ["正确识别条件", "给出关键推导", "明确最终结论"],
        }
        with patch.object(api_v2, "_education_ai_task", return_value=generated) as task:
            response = self.client.post(
                "/api/v2/edu/direct-questions/generate-standard",
                json={"question": "设向量组线性无关，说明其性质。"},
                headers=self._headers(self.teacher),
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), generated)
        task.assert_called_once()
        kwargs = task.call_args.kwargs
        self.assertEqual(kwargs["task_kind"], "direct_scoring_standard")
        self.assertEqual(kwargs["payload"], {"question": "设向量组线性无关，说明其性质。"})

        self.assertTrue(validate_direct_scoring_standard_result(generated))
        self.assertFalse(validate_direct_scoring_standard_result({**generated, "focus": ""}))
        self.assertFalse(validate_direct_scoring_standard_result({**generated, "expectedPoints": []}))

        student_response = self.client.post(
            "/api/v2/edu/direct-questions/generate-standard",
            json={"question": "学生不应调用教师生成接口。"},
            headers=self._headers(self.student),
        )
        self.assertEqual(student_response.status_code, 403, student_response.get_json())

        invalid_response = self.client.post(
            "/api/v2/edu/direct-questions/generate-standard",
            json={"question": "   "},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(invalid_response.status_code, 400, invalid_response.get_json())


    def test_saved_teacher_config_generates_assessments_without_system_environment(self):
        self._save_llm_config(self.teacher)
        class_data = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Configured course"},
            headers=self._headers(self.teacher),
        ).get_json()["class"]
        snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "configured.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        ).get_json()["snapshot"]

        def fake_runner(**kwargs):
            if kwargs["task_kind"] == "path":
                return {key: {} for key in kwargs["tasks"]}
            return {
                key: _assessment_result(prefix=f"configured-{key}")
                for key in kwargs["tasks"]
            }

        with patch.object(api_v2, "create_education_context", return_value=object()) as create_context:
            with patch.object(api_v2, "run_structured_education_tasks", side_effect=fake_runner) as runner:
                response = self.client.post(
                    f"/api/v2/edu/classes/{class_data['id']}/assignments",
                    json={"snapshotId": snapshot["id"], "targetNodeId": 3, "title": "Configured task"},
                    headers=self._headers(self.teacher),
                )
        self.assertEqual(response.status_code, 201, response.get_json())
        assessments = response.get_json()["assignment"]["assessments"]
        self.assertTrue(assessments)
        self.assertTrue(all(item["status"] == "ready" for item in assessments))
        self.assertTrue(all(item["questionCount"] == 4 for item in assessments))
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(all(call.args[1]["api_key"] == "user-key" for call in create_context.call_args_list))
        self.assertNotIn("user-key", json.dumps(response.get_json()))

    def test_unconfigured_assessment_failure_has_stable_code_without_consuming_quota(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Unconfigured course"},
            headers=self._headers(self.teacher),
        )
        class_data = created.get_json()["class"]
        snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "unconfigured.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        ).get_json()["snapshot"]
        assignment_response = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": snapshot["id"], "targetNodeId": 3, "title": "Unconfigured task"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(assignment_response.status_code, 201, assignment_response.get_json())
        assignment = assignment_response.get_json()["assignment"]
        self.assertTrue(assignment["assessments"])
        self.assertTrue(all(item["status"] == "failed" for item in assignment["assessments"]))
        self.assertTrue(all(item["generationErrorCode"] == "education_ai_unconfigured" for item in assignment["assessments"]))

        regenerated = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/regenerate",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(regenerated.status_code, 503, regenerated.get_json())
        self.assertEqual(regenerated.get_json()["code"], "education_ai_unconfigured")
        with api_v2.connect_database() as connection:
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)
            ).fetchone()[0]
            usage_count = connection.execute(
                "SELECT COUNT(*) FROM education_ai_usage WHERE user_id = ?", (teacher_id,)
            ).fetchone()[0]
        self.assertEqual(usage_count, 0)

    def _create_draft_assignment_with_assessments(self, *, assessment_results=None):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Assessment course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "Assessment student", "studentNumber": "A001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())
        snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "assessment.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(snapshot.status_code, 201, snapshot.get_json())
        snapshot_data = snapshot.get_json()["snapshot"]
        results = assessment_results
        if results is None:
            results = {str(node_id): _assessment_result(prefix=f"node-{node_id}") for node_id in (1, 2, 3)}
        with patch.object(api_v2, "_education_ai_task", side_effect=RuntimeError("use deterministic path")):
            with patch.object(api_v2, "_education_ai_tasks", return_value=results):
                created_assignment = self.client.post(
                    f"/api/v2/edu/classes/{class_data['id']}/assignments",
                    json={"snapshotId": snapshot_data["id"], "targetNodeId": 3, "title": "Assessment task"},
                    headers=self._headers(self.teacher),
                )
        self.assertEqual(created_assignment.status_code, 201, created_assignment.get_json())
        return class_data, snapshot_data, created_assignment.get_json()["assignment"]

    def _complete_all_assignment_assessments(self, assignment, *, answer=None):
        attempts = []
        for assessment in assignment["assessments"]:
            if assessment["status"] != "ready":
                continue
            started = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/{assessment['nodeId']}/attempts",
                headers=self._headers(self.student),
            )
            self.assertEqual(started.status_code, 201, started.get_json())
            attempt = started.get_json()["attempt"]
            answers = {
                question["id"]: answer or f"answer for {question['id']}"
                for question in attempt["questions"]
            }
            completed = self.client.post(
                f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
                json={"answers": answers},
                headers=self._headers(self.student),
            )
            self.assertEqual(completed.status_code, 200, completed.get_json())
            attempts.append(completed.get_json()["attempt"])
        return attempts

    def test_assessment_partial_generation_blocks_publish_and_hides_teacher_fields_from_students(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments(
            assessment_results={
                "1": _assessment_result(prefix="node-1"),
                "3": _assessment_result(prefix="node-3"),
            },
        )
        assessments = {item["nodeId"]: item for item in assignment["assessments"]}
        self.assertEqual(assessments[1]["status"], "ready")
        self.assertEqual(assessments[1]["questionCount"], 4)
        self.assertIn("expectedPoints", assessments[1]["questions"][0])
        self.assertEqual(assessments[2]["status"], "failed")
        self.assertTrue(assessments[2]["generationError"])

        blocked = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertEqual(blocked.get_json()["code"], "assessment_review_required")
        self.assertEqual(blocked.get_json()["nodeIds"], [2])

        exempted = self.client.delete(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/2",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(exempted.status_code, 200, exempted.get_json())
        self.assertEqual(exempted.get_json()["assessment"]["status"], "exempt")
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        student_view = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_view.status_code, 200, student_view.get_json())
        student_assessments = student_view.get_json()["assignment"]["assessments"]
        self.assertTrue(all("questions" not in item for item in student_assessments))
        self.assertNotIn("expectedPoints", json.dumps(student_assessments))
        self.assertEqual({item["attemptStatus"] for item in student_assessments}, {"not_started"})

        exempt_mastery = self.client.put(
            f"/api/v2/edu/assignments/{assignment['id']}/progress/2",
            json={"state": "mastered"},
            headers=self._headers(self.student),
        )
        self.assertEqual(exempt_mastery.status_code, 200, exempt_mastery.get_json())
        with api_v2.connect_database() as connection:
            source = connection.execute(
                "SELECT mastery_source FROM education_node_progress WHERE assignment_id = ? AND node_id = 2",
                (assignment["id"],),
            ).fetchone()[0]
        self.assertEqual(source, "self")
        self.assertEqual(class_data["id"], assignment["classId"])

    def test_batch_regeneration_retries_only_unresolved_nodes_and_reports_partial_failure(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments(
            assessment_results={
                "1": _assessment_result(prefix="node-1"),
                "3": _assessment_result(prefix="node-3"),
            },
        )
        calls = []

        def fake_tasks(**kwargs):
            calls.append(kwargs)
            return {"2": _assessment_result(prefix="retry-2")}

        with patch.object(api_v2, "_education_ai_tasks", side_effect=fake_tasks):
            retried = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/regenerate-unresolved",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(retried.status_code, 200, retried.get_json())
        body = retried.get_json()
        self.assertEqual(body["retriedNodeIds"], [2])
        self.assertEqual(body["readyNodeIds"], [2])
        self.assertEqual(body["failedNodeIds"], [])
        self.assertEqual(list(calls[0]["tasks"]), ["2"])
        self.assertTrue(calls[0]["scope"].startswith(f"assignments/{assignment['id']}/assessment_regenerations/"))

        with patch.object(api_v2, "_education_ai_tasks") as no_retry:
            clean = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/regenerate-unresolved",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(clean.status_code, 200, clean.get_json())
        no_retry.assert_not_called()

    def test_batch_regeneration_is_teacher_draft_only(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments(
            assessment_results={"1": _assessment_result(prefix="node-1"), "3": _assessment_result(prefix="node-3")},
        )
        student_response = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/regenerate-unresolved",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_response.status_code, 404, student_response.get_json())

        exempted = self.client.delete(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/2",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(exempted.status_code, 200, exempted.get_json())
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        after_publish = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/regenerate-unresolved",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(after_publish.status_code, 403, after_publish.get_json())

    def test_batch_regeneration_keeps_partial_success_and_marks_invalid_nodes(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments(
            assessment_results={"3": _assessment_result(prefix="node-3")},
        )
        with patch.object(
            api_v2,
            "_education_ai_tasks",
            return_value={"1": _assessment_result(prefix="retry-1"), "2": {"category": "general", "questions": []}},
        ) as runner:
            response = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/regenerate-unresolved",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body["retriedNodeIds"], [1, 2])
        self.assertEqual(body["readyNodeIds"], [1])
        self.assertEqual(body["failedNodeIds"], [2])
        self.assertEqual(runner.call_count, 1)

    def test_reference_matrix_error_blocks_assignment_publish(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        assessment = next(item for item in assignment["assessments"] if item["status"] == "ready")
        question = assessment["questions"][0]
        wrong_reference = (
            r"\begin{vmatrix}1&2\\3&4\end{vmatrix}"
            r"\xrightarrow{R_1\leftrightarrow R_2}"
            r"\begin{vmatrix}3&4\\1&2\end{vmatrix}"
        )
        updated = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/{assessment['nodeId']}/questions/{question['id']}",
            json={
                "referenceAnswer": wrong_reference,
                "expectedPoints": question["expectedPoints"],
                "maxScore": question["maxScore"],
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        updated_question = next(item for item in updated.get_json()["assessment"]["questions"] if item["id"] == question["id"])
        self.assertEqual(updated_question["referenceMatrixReport"]["status"], "contradicted")

        blocked = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertEqual(blocked.get_json()["code"], "assessment_scoring_required")
        invalid = next(item for item in blocked.get_json()["invalidQuestions"] if item["questionId"] == question["id"])
        self.assertEqual(invalid["reason"], "reference_matrix_invalid")

        fixed = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/{assessment['nodeId']}/questions/{question['id']}",
            json={
                "referenceAnswer": "修正后的文字参考答案",
                "expectedPoints": question["expectedPoints"],
                "maxScore": question["maxScore"],
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(fixed.status_code, 200, fixed.get_json())
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

    def test_student_assessment_saves_resumes_completes_and_cannot_be_bypassed(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        bypass = self.client.put(
            f"/api/v2/edu/assignments/{assignment['id']}/progress/1",
            json={"state": "mastered"},
            headers=self._headers(self.student),
        )
        self.assertEqual(bypass.status_code, 409, bypass.get_json())
        self.assertEqual(bypass.get_json()["code"], "assignment_review_required")

        started = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/attempts",
            headers=self._headers(self.student),
        )
        self.assertEqual(started.status_code, 201, started.get_json())
        attempt = started.get_json()["attempt"]
        self.assertEqual(len(attempt["questions"]), 4)
        self.assertNotIn("expectedPoints", json.dumps(attempt))
        first_question = attempt["questions"][0]["id"]
        saved = self.client.patch(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}",
            json={"answers": {first_question: "OCR transcribed draft"}},
            headers=self._headers(self.student),
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())

        resumed = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/attempts",
            headers=self._headers(self.student),
        )
        self.assertEqual(resumed.status_code, 201, resumed.get_json())
        self.assertEqual(resumed.get_json()["attempt"]["id"], attempt["id"])
        self.assertEqual(resumed.get_json()["attempt"]["answers"][first_question], "OCR transcribed draft")

        incomplete = self.client.post(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
            json={"answers": {}},
            headers=self._headers(self.student),
        )
        self.assertEqual(incomplete.status_code, 400, incomplete.get_json())
        self.assertEqual(incomplete.get_json()["code"], "assessment_incomplete")
        outsider_save = self.client.patch(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}",
            json={"answers": {first_question: "not mine"}},
            headers=self._headers(self.outsider),
        )
        self.assertEqual(outsider_save.status_code, 404, outsider_save.get_json())

        answers = {question["id"]: f"answer {index}" for index, question in enumerate(attempt["questions"], start=1)}
        completed = self.client.post(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
            json={"answers": answers},
            headers=self._headers(self.student),
        )
        self.assertEqual(completed.status_code, 200, completed.get_json())
        self.assertEqual(completed.get_json()["attempt"]["status"], "completed")
        self.assertEqual(completed.get_json()["path"]["steps"][0]["state"], "in_progress")
        repeated = self.client.post(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
            json={"answers": {}},
            headers=self._headers(self.student),
        )
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertEqual(repeated.get_json()["attempt"]["completedAt"], completed.get_json()["attempt"]["completedAt"])
        with api_v2.connect_database() as connection:
            progress = connection.execute(
                "SELECT state, mastery_source FROM education_node_progress WHERE assignment_id = ? AND node_id = 1",
                (assignment["id"],),
            ).fetchone()
        self.assertEqual(tuple(progress.values()), ("in_progress", "self"))

    def test_teacher_regeneration_is_copy_on_success_and_published_questions_are_frozen(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        node = next(item for item in assignment["assessments"] if item["nodeId"] == 1)
        question = node["questions"][0]
        replacement = {
            "category": "general",
            "requiredKind": question["kind"],
            "question": {
                "kind": question["kind"],
                "question": "single regenerated question",
                "focus": "new focus",
                "expectedPoints": ["new point"],
                "referenceAnswer": "reference answer",
            },
        }
        with patch.object(api_v2, "_education_ai_tasks", return_value={question["id"]: replacement}):
            regenerated = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/questions/{question['id']}/regenerate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(regenerated.status_code, 200, regenerated.get_json())
        regenerated_question = regenerated.get_json()["assessment"]["questions"][0]
        self.assertEqual(regenerated_question["id"], question["id"])
        self.assertEqual(regenerated_question["question"], "single regenerated question")

        with patch.object(api_v2, "_education_ai_tasks", side_effect=RuntimeError("generation failed")):
            failed = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/regenerate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(failed.status_code, 503, failed.get_json())
        self.assertEqual(failed.get_json()["code"], "assessment_regeneration_failed")
        with patch.object(
            api_v2,
            "_education_ai_tasks",
            side_effect=api_v2.EducationAIError(
                "education_ai_limit_reached",
                "education AI daily limit reached",
                429,
            ),
        ):
            limited = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/regenerate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(limited.status_code, 429, limited.get_json())
        self.assertEqual(limited.get_json()["code"], "education_ai_limit_reached")
        unchanged = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["assignment"]
        unchanged_node = next(item for item in unchanged["assessments"] if item["nodeId"] == 1)
        self.assertEqual(unchanged_node["questions"][0]["question"], "single regenerated question")

        unlocked_steps = [
            {**step, "required": False} if step["nodeId"] == 2 else step
            for step in assignment["path"]["steps"]
        ]
        unlocked = self.client.put(
            f"/api/v2/edu/assignments/{assignment['id']}",
            json={"steps": unlocked_steps},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(unlocked.status_code, 200, unlocked.get_json())
        saved = self.client.put(
            f"/api/v2/edu/assignments/{assignment['id']}",
            json={"steps": [step for step in unlocked_steps if step["nodeId"] != 2]},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertNotIn(2, [item["nodeId"] for item in saved.get_json()["assignment"]["assessments"]])

        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        frozen = self.client.delete(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/questions/{question['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(frozen.status_code, 403, frozen.get_json())

    def test_teacher_regeneration_runs_different_nodes_in_parallel_and_counts_each_quota(self):
        self._save_llm_config(self.teacher)
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        barrier = threading.Barrier(2)
        responses = {}
        errors = []

        def fake_runner(**kwargs):
            barrier.wait(timeout=5)
            return {
                key: _assessment_result(prefix=f"parallel-{key}")
                for key in kwargs["tasks"]
            }

        def regenerate(node_id):
            try:
                with api_v2.app.test_client() as client:
                    response = client.post(
                        f"/api/v2/edu/assignments/{assignment['id']}/assessments/{node_id}/regenerate",
                        headers=self._headers(self.teacher),
                    )
                    responses[node_id] = (response.status_code, response.get_json())
            except Exception as exc:  # pragma: no cover - makes thread failures visible to unittest
                errors.append(exc)

        with patch.object(api_v2, "create_education_context", return_value=object()):
            with patch.object(api_v2, "run_structured_education_tasks", side_effect=fake_runner):
                threads = [threading.Thread(target=regenerate, args=(node_id,)) for node_id in (1, 2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)

        self.assertFalse(errors, errors)
        self.assertEqual(set(responses), {1, 2})
        self.assertEqual({status for status, _payload in responses.values()}, {200})
        with api_v2.connect_database() as connection:
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)
            ).fetchone()[0]
            usage_count = connection.execute(
                "SELECT request_count FROM education_ai_usage WHERE user_id = ?",
                (teacher_id,),
            ).fetchone()[0]
        self.assertEqual(usage_count, 2)
        refreshed = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["assignment"]
        for node_id in (1, 2):
            node = next(item for item in refreshed["assessments"] if item["nodeId"] == node_id)
            self.assertTrue(node["questions"][0]["question"].startswith(f"parallel-{node_id}"))

    def test_late_regeneration_is_discarded_when_assignment_is_published(self):
        self._save_llm_config(self.teacher)
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        original_question = next(item for item in assignment["assessments"] if item["nodeId"] == 1)["questions"][0]["question"]
        started = threading.Event()
        release = threading.Event()
        response_holder = {}

        def fake_runner(**kwargs):
            started.set()
            release.wait(timeout=5)
            return {key: _assessment_result(prefix="late") for key in kwargs["tasks"]}

        def regenerate():
            with api_v2.app.test_client() as client:
                response = client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/regenerate",
                    headers=self._headers(self.teacher),
                )
                response_holder["value"] = (response.status_code, response.get_json())

        with patch.object(api_v2, "create_education_context", return_value=object()):
            with patch.object(api_v2, "run_structured_education_tasks", side_effect=fake_runner):
                thread = threading.Thread(target=regenerate)
                thread.start()
                try:
                    self.assertTrue(started.wait(timeout=5))
                    published = self.client.post(
                        f"/api/v2/edu/assignments/{assignment['id']}/publish",
                        headers=self._headers(self.teacher),
                    )
                    self.assertEqual(published.status_code, 200, published.get_json())
                finally:
                    release.set()
                    thread.join(timeout=10)

        self.assertEqual(response_holder["value"][0], 409)
        self.assertEqual(response_holder["value"][1]["code"], "assessment_draft_changed")
        refreshed = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["assignment"]
        self.assertEqual(refreshed["status"], "published")
        self.assertEqual(
            next(item for item in refreshed["assessments"] if item["nodeId"] == 1)["questions"][0]["question"],
            original_question,
        )

    def test_late_regeneration_is_discarded_when_node_is_removed_from_draft(self):
        self._save_llm_config(self.teacher)
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        started = threading.Event()
        release = threading.Event()
        response_holder = {}

        def fake_runner(**kwargs):
            started.set()
            release.wait(timeout=5)
            return {key: _assessment_result(prefix="removed-late") for key in kwargs["tasks"]}

        def regenerate():
            with api_v2.app.test_client() as client:
                response = client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/assessments/2/regenerate",
                    headers=self._headers(self.teacher),
                )
                response_holder["value"] = (response.status_code, response.get_json())

        with patch.object(api_v2, "create_education_context", return_value=object()):
            with patch.object(api_v2, "run_structured_education_tasks", side_effect=fake_runner):
                thread = threading.Thread(target=regenerate)
                thread.start()
                try:
                    self.assertTrue(started.wait(timeout=5))
                    unlocked_steps = [
                        {**step, "required": False} if step["nodeId"] == 2 else step
                        for step in assignment["path"]["steps"]
                    ]
                    unlocked = self.client.put(
                        f"/api/v2/edu/assignments/{assignment['id']}",
                        json={"steps": unlocked_steps},
                        headers=self._headers(self.teacher),
                    )
                    self.assertEqual(unlocked.status_code, 200, unlocked.get_json())
                    trimmed_steps = [step for step in unlocked_steps if step["nodeId"] != 2]
                    saved = self.client.put(
                        f"/api/v2/edu/assignments/{assignment['id']}",
                        json={"steps": trimmed_steps},
                        headers=self._headers(self.teacher),
                    )
                    self.assertEqual(saved.status_code, 200, saved.get_json())
                finally:
                    release.set()
                    thread.join(timeout=10)

        self.assertEqual(response_holder["value"][0], 409)
        self.assertEqual(response_holder["value"][1]["code"], "assessment_draft_changed")
        refreshed = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["assignment"]
        self.assertNotIn(2, [item["nodeId"] for item in refreshed["assessments"]])

    def test_late_single_question_regeneration_is_discarded_after_question_delete(self):
        self._save_llm_config(self.teacher)
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        question = next(item for item in assignment["assessments"] if item["nodeId"] == 1)["questions"][0]
        started = threading.Event()
        release = threading.Event()
        response_holder = {}

        def fake_runner(**kwargs):
            started.set()
            release.wait(timeout=5)
            return {
                key: {
                    "category": "general",
                    "requiredKind": question["kind"],
                    "question": {
                        "kind": question["kind"],
                        "question": "late question",
                        "focus": "late focus",
                        "expectedPoints": ["late point"],
                "referenceAnswer": "reference answer",
                    },
                }
                for key in kwargs["tasks"]
            }

        def regenerate():
            with api_v2.app.test_client() as client:
                response = client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/questions/{question['id']}/regenerate",
                    headers=self._headers(self.teacher),
                )
                response_holder["value"] = (response.status_code, response.get_json())

        with patch.object(api_v2, "create_education_context", return_value=object()):
            with patch.object(api_v2, "run_structured_education_tasks", side_effect=fake_runner):
                thread = threading.Thread(target=regenerate)
                thread.start()
                try:
                    self.assertTrue(started.wait(timeout=5))
                    deleted = self.client.delete(
                        f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/questions/{question['id']}",
                        headers=self._headers(self.teacher),
                    )
                    self.assertEqual(deleted.status_code, 200, deleted.get_json())
                finally:
                    release.set()
                    thread.join(timeout=10)

        self.assertEqual(response_holder["value"][0], 409)
        self.assertEqual(response_holder["value"][1]["code"], "assessment_draft_changed")
        refreshed = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["assignment"]
        node = next(item for item in refreshed["assessments"] if item["nodeId"] == 1)
        self.assertNotIn(question["id"], [item["id"] for item in node["questions"]])

    def test_progress_schema_is_alembic_managed_and_preserves_records(self):
        _class_data, _snapshot, assignment = self._create_published_assignment()
        with api_v2.connect_database() as connection:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = 'student@example.com'"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO education_node_progress
                     (assignment_id, user_id, node_id, state, mastery_source, diagnostic_summary, updated_at)
                   VALUES (?, ?, 1, 'mastered', 'diagnostic', 'persisted progress', ?)""",
                (assignment["id"], user_id, datetime.utcnow().isoformat()),
            )
        with api_v2.connect_database() as connection:
            columns = {
                row["column_name"]
                for row in connection.execute(
                    """SELECT column_name FROM information_schema.columns
                         WHERE table_schema = DATABASE() AND table_name = 'education_node_progress'"""
                ).fetchall()
            }
            progress = connection.execute(
                """SELECT state, mastery_source, diagnostic_summary
                     FROM education_node_progress
                    WHERE assignment_id = ? AND user_id = ? AND node_id = 1""",
                (assignment["id"], user_id),
            ).fetchone()
        self.assertTrue({"state", "mastery_source", "diagnostic_summary"}.issubset(columns))
        self.assertEqual(tuple(progress.values()), ("mastered", "diagnostic", "persisted progress"))

    def test_path_payload_sends_only_structural_base_path(self):
        deterministic = {
            "targetNodeId": 3,
            "candidateNodeIds": [1, 2, 3],
            "steps": [
                {
                    "nodeId": 1,
                    "order": 1,
                    "stage": 1,
                    "role": "prerequisite",
                    "required": False,
                    "cycle": False,
                    "rationale": "这段确定性文字不应发送给路径模型。",
                    "state": "not_started",
                },
                {
                    "nodeId": 3,
                    "order": 2,
                    "stage": 2,
                    "role": "target",
                    "required": True,
                    "cycle": False,
                    "rationale": "目标说明也不应进入 basePath。",
                },
            ],
            "edges": [],
        }

        with patch.object(
            api_v2,
            "_education_snapshot_graph",
            return_value={"nodes": NODES, "edges": []},
        ):
            payload = api_v2._education_path_payload(
                {"id": "path-payload-test"},
                deterministic,
            )

        self.assertEqual(
            payload["basePath"],
            [
                {"nodeId": 1, "order": 1, "stage": 1, "role": "prerequisite", "required": False, "cycle": False},
                {"nodeId": 3, "order": 2, "stage": 2, "role": "target", "required": True, "cycle": False},
            ],
        )
        self.assertNotIn("rationale", json.dumps(payload["basePath"], ensure_ascii=False))

    def _create_published_assignment(self, *, nodes=NODES, edges=EDGES, target_node_id=3):
        response = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "线性代数 2026"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        class_data = response.get_json()["class"]
        join = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "测试学生", "studentNumber": "S001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(join.status_code, 200, join.get_json())
        snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={
                "filename": "linear-algebra.tex",
                "nodes": nodes,
                "edges": edges,
                "sourceMarkdown": "source version one",
                "latexMacros": {"RR": "\\mathbb{R}"},
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(snapshot.status_code, 201, snapshot.get_json())
        snapshot_data = snapshot.get_json()["snapshot"]
        assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={
                "snapshotId": snapshot_data["id"],
                "targetNodeId": target_node_id,
                "title": "完成基扩张定理",
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(assignment.status_code, 201, assignment.get_json())
        assignment_data = assignment.get_json()["assignment"]
        # Most API tests exercise education workflows unrelated to the separate
        # teacher review gate for generated assessment questions.
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assessment_nodes SET status = 'exempt' WHERE assignment_id = ?",
                (assignment_data["id"],),
            )
        publish = self.client.post(
            f"/api/v2/edu/assignments/{assignment_data['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(publish.status_code, 200, publish.get_json())
        return class_data, snapshot_data, assignment_data

    def test_teacher_allowlist_roles_and_registration_boundary(self):
        static_login = self.client.post(
            "/api/v2/auth/login",
            json={"email": self.TEACHER_EMAIL, "password": self.TEACHER_PASSWORD, "educationRole": "teacher"},
        )
        self.assertEqual(static_login.status_code, 200, static_login.get_json())

        db = api_v2.connect_database()
        teacher_row = db.execute(
            "SELECT password_hash, can_teach FROM users WHERE email = ?",
            (self.TEACHER_EMAIL,),
        ).fetchone()
        self.assertEqual(teacher_row[1], 1)
        self.assertNotIn("secret12", teacher_row[0])
        db.close()

        teacher_me = self.client.get("/api/v2/auth/me", headers=self._headers(self.teacher))
        self.assertEqual(teacher_me.status_code, 200)
        self.assertEqual(teacher_me.get_json()["educationRole"], "teacher")
        self.assertTrue(teacher_me.get_json()["canTeach"])

        teacher_as_student = self._login(self.TEACHER_EMAIL, self.TEACHER_PASSWORD, "student")
        student_me = self.client.get("/api/v2/auth/me", headers=self._headers(teacher_as_student))
        self.assertEqual(student_me.get_json()["educationRole"], "student")

        ordinary_teacher_login = self.client.post(
            "/api/v2/auth/login",
            json={"email": "student@example.com", "password": "secret12", "educationRole": "teacher"},
        )
        self.assertEqual(ordinary_teacher_login.status_code, 401)
        self.assertEqual(ordinary_teacher_login.get_json()["code"], "teacher_login_failed")

        teacher_register = self.client.post(
            "/api/v2/auth/register",
            json={"email": "new-teacher@example.com", "password": "secret12", "educationRole": "teacher"},
        )
        self.assertEqual(teacher_register.status_code, 403)
        self.assertEqual(teacher_register.get_json()["code"], "teacher_registration_disabled")

        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE users SET can_teach = 0 WHERE email = ?",
                (self.TEACHER_EMAIL,),
            )
        revoked_existing = self.client.get(
            "/api/v2/edu/classes",
            headers=self._headers(self.teacher),
        )
        revoked = self.client.post(
            "/api/v2/auth/login",
            json={"email": self.TEACHER_EMAIL, "password": self.TEACHER_PASSWORD, "educationRole": "teacher"},
        )
        self.assertEqual(revoked_existing.status_code, 403)
        self.assertEqual(revoked.status_code, 401)

    def test_role_scopes_reject_wrong_education_actions(self):
        teacher_join = self.client.post(
            "/api/v2/edu/classes/ABC12345/join",
            json={"inviteCode": "ABC12345"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_join.status_code, 403)
        student_create = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "学生不应创建"},
            headers=self._headers(self.student),
        )
        self.assertEqual(student_create.status_code, 403)

    def test_auth_defaults_and_stores_only_session_hashes(self):
        default_login = self.client.post(
            "/api/v2/auth/login",
            json={"email": "student@example.com", "password": "secret12"},
        )
        self.assertEqual(default_login.status_code, 200, default_login.get_json())
        self.assertEqual(default_login.get_json()["educationRole"], "student")
        plaintext = default_login.get_json()["token"]

        default_registration = self.client.post(
            "/api/v2/auth/register",
            json={"email": "default-student@example.com", "password": "secret12"},
        )
        self.assertEqual(default_registration.status_code, 201, default_registration.get_json())
        self.assertEqual(default_registration.get_json()["educationRole"], "student")

        with api_v2.connect_database() as db:
            columns = {
                row["column_name"]
                for row in db.execute(
                    """SELECT column_name FROM information_schema.columns
                         WHERE table_schema = DATABASE() AND table_name = 'sessions'"""
                ).fetchall()
            }
            hashes = [row["token_hash"] for row in db.execute("SELECT token_hash FROM sessions").fetchall()]
        self.assertIn("token_hash", columns)
        self.assertNotIn("token", columns)
        self.assertTrue(all(isinstance(value, bytes) and len(value) == 32 for value in hashes))
        self.assertTrue(all(plaintext.encode("utf-8") not in value for value in hashes))

    def test_student_profile_is_required_unique_and_teacher_visible_without_email(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "身份资料测试"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        join_url = f"/api/v2/edu/classes/{class_data['inviteCode']}/join"

        missing_name = self.client.post(
            join_url,
            json={"inviteCode": class_data["inviteCode"], "studentNumber": "S001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(missing_name.status_code, 400)
        self.assertEqual(missing_name.get_json()["code"], "student_name_required")

        joined = self.client.post(
            join_url,
            json={"inviteCode": class_data["inviteCode"], "studentName": "张三", "studentNumber": "s001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())
        student_class = self.client.get("/api/v2/edu/classes", headers=self._headers(self.student)).get_json()["classes"][0]
        self.assertEqual(student_class["studentName"], "张三")
        self.assertEqual(student_class["studentNumber"], "S001")
        self.assertTrue(student_class["profileComplete"])

        duplicate_account = self._register("duplicate@example.com")
        duplicate = self.client.post(
            join_url,
            json={"inviteCode": class_data["inviteCode"], "studentName": "李四", "studentNumber": "S001"},
            headers=self._headers(duplicate_account),
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["code"], "student_number_conflict")

        updated = self.client.put(
            f"/api/v2/edu/classes/{class_data['id']}/membership",
            json={"studentName": "张三丰", "studentNumber": "S002"},
            headers=self._headers(self.student),
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        teacher_members = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/members",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_members.status_code, 200, teacher_members.get_json())
        member = teacher_members.get_json()["members"][0]
        self.assertEqual(member["studentName"], "张三丰")
        self.assertEqual(member["studentNumber"], "S002")
        self.assertNotIn("email", member)

        teacher_update = self.client.put(
            f"/api/v2/edu/classes/{class_data['id']}/membership",
            json={"studentName": "教师", "studentNumber": "T001"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_update.status_code, 403)

        second_class = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "另一门课程"},
            headers=self._headers(self.teacher),
        ).get_json()["class"]
        same_number_other_class = self.client.post(
            f"/api/v2/edu/classes/{second_class['inviteCode']}/join",
            json={"inviteCode": second_class["inviteCode"], "studentName": "张三丰", "studentNumber": "S002"},
            headers=self._headers(self.student),
        )
        self.assertEqual(same_number_other_class.status_code, 200, same_number_other_class.get_json())

    def test_legacy_student_profile_blocks_learning_until_completed(self):
        class_data, snapshot_data, assignment_data = self._create_published_assignment()
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_memberships SET student_name = NULL, student_number = NULL WHERE class_id = ? AND user_id = (SELECT id FROM users WHERE email = 'student@example.com')",
                (class_data["id"],),
            )
            connection.commit()

        classes = self.client.get("/api/v2/edu/classes", headers=self._headers(self.student))
        self.assertEqual(classes.status_code, 200)
        self.assertFalse(classes.get_json()["classes"][0]["profileComplete"])
        assignments = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            headers=self._headers(self.student),
        )
        self.assertEqual(assignments.status_code, 409)
        self.assertEqual(assignments.get_json()["code"], "student_profile_required")
        snapshots = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.student),
        )
        self.assertEqual(snapshots.status_code, 409)
        self.assertEqual(snapshots.get_json()["code"], "student_profile_required")
        direct_assignment = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(direct_assignment.status_code, 409)
        self.assertEqual(direct_assignment.get_json()["classId"], class_data["id"])
        direct_snapshot = self.client.get(
            f"/api/v2/edu/snapshots/{snapshot_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(direct_snapshot.status_code, 409)
        self.assertEqual(direct_snapshot.get_json()["classId"], class_data["id"])

        profile = self.client.put(
            f"/api/v2/edu/classes/{class_data['id']}/membership",
            json={"studentName": "补录学生", "studentNumber": "S009"},
            headers=self._headers(self.student),
        )
        self.assertEqual(profile.status_code, 200, profile.get_json())
        assignment = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(assignment.status_code, 200, assignment.get_json())

    def test_class_snapshot_assignment_and_student_progress_flow(self):
        class_data, snapshot_data, assignment_data = self._create_published_assignment()

        student_view = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_view.status_code, 200, student_view.get_json())
        payload = student_view.get_json()["assignment"]
        self.assertEqual([step["nodeId"] for step in payload["path"]["steps"]], [1, 2, 3])
        self.assertEqual(payload["snapshot"]["sourceMarkdown"], "source version one")

        progress = self.client.put(
            f"/api/v2/edu/assignments/{assignment_data['id']}/progress/1",
            json={"state": "mastered"},
            headers=self._headers(self.student),
        )
        self.assertEqual(progress.status_code, 200, progress.get_json())
        self.assertEqual(progress.get_json()["path"]["steps"][0]["state"], "mastered")

        overview = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}/overview",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(overview.status_code, 200, overview.get_json())
        self.assertEqual(overview.get_json()["students"][0]["masteredCount"], 1)
        self.assertEqual(overview.get_json()["students"][0]["studentName"], "测试学生")
        self.assertEqual(overview.get_json()["students"][0]["studentNumber"], "S001")
        self.assertNotIn("email", overview.get_json()["students"][0])
        self.assertNotIn("answer", json.dumps(overview.get_json()))
        self.assertEqual(snapshot_data["classId"], class_data["id"])

    def test_course_graph_is_listed_before_tasks_and_reuses_snapshot(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "课程图谱测试"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "课程学生", "studentNumber": "C001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())

        first = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={
                "sourceGraphId": "chapter-1",
                "filename": "第一章-线性空间.tex",
                "nodes": NODES,
                "edges": EDGES,
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(first.status_code, 201, first.get_json())
        snapshot = first.get_json()["snapshot"]
        self.assertEqual(snapshot["nodeCount"], len(NODES))
        self.assertEqual(snapshot["edgeCount"], len(EDGES))

        repeat = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"sourceGraphId": "chapter-1", "filename": "重复.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(repeat.status_code, 200, repeat.get_json())
        self.assertFalse(repeat.get_json()["created"])
        self.assertEqual(repeat.get_json()["snapshot"]["id"], snapshot["id"])

        teacher_list = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        student_list = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.student),
        )
        self.assertEqual(teacher_list.status_code, 200, teacher_list.get_json())
        self.assertEqual(student_list.status_code, 200, student_list.get_json())
        self.assertEqual(len(student_list.get_json()["snapshots"]), 1)
        self.assertEqual(student_list.get_json()["snapshots"][0]["filename"], "第一章-线性空间.tex")

        detail = self.client.get(
            f"/api/v2/edu/snapshots/{snapshot['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(detail.status_code, 200, detail.get_json())
        self.assertEqual(len(detail.get_json()["snapshot"]["nodes"]), len(NODES))

        invalid_target = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": snapshot["id"], "targetNodeId": 999, "title": "非法目标"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(invalid_target.status_code, 400, invalid_target.get_json())

        db = api_v2.connect_database()
        snapshots_before_drafts = db.execute(
            "SELECT COUNT(*) FROM education_snapshots WHERE class_id = ?",
            (class_data["id"],),
        ).fetchone()[0]
        db.close()

        first_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": snapshot["id"], "targetNodeId": 3, "title": "第一章目标"},
            headers=self._headers(self.teacher),
        )
        second_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": snapshot["id"], "targetNodeId": 2, "title": "第一章基础"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(first_assignment.status_code, 201, first_assignment.get_json())
        self.assertEqual(second_assignment.status_code, 201, second_assignment.get_json())
        self.assertEqual(first_assignment.get_json()["assignment"]["snapshotId"], snapshot["id"])
        self.assertEqual(second_assignment.get_json()["assignment"]["snapshotId"], snapshot["id"])
        db = api_v2.connect_database()
        snapshots_after_drafts = db.execute(
            "SELECT COUNT(*) FROM education_snapshots WHERE class_id = ?",
            (class_data["id"],),
        ).fetchone()[0]
        db.close()
        self.assertEqual(snapshots_after_drafts, snapshots_before_drafts)

    def test_teacher_deletes_same_source_snapshot_group_with_all_learning_records(self):
        class_data, snapshot_data, published_assignment = self._create_published_assignment()
        duplicate_snapshot_id = "duplicate-snapshot"
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_snapshots SET source_graph_id = ? WHERE id = ?",
                ("shared-course-graph", snapshot_data["id"]),
            )
            connection.execute(
                """INSERT INTO education_snapshots
                     (id, class_id, source_graph_id, filename, node_count, edge_count,
                      source_markdown, latex_macros_json, source_pdf_json, created_by, created_at, snapshot_type)
                   SELECT ?, class_id, source_graph_id, filename, node_count, edge_count,
                          source_markdown, latex_macros_json, source_pdf_json, created_by, ?, snapshot_type
                     FROM education_snapshots WHERE id = ?""",
                (duplicate_snapshot_id, "2026-08-12T00:00:00", snapshot_data["id"]),
            )
        source_graph = api_v2.load_stored_graph(snapshot_data["id"])
        api_v2.persist_graph(
            duplicate_snapshot_id,
            "education_snapshot",
            source_graph["nodes"],
            source_graph["edges"],
        )

        draft = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": duplicate_snapshot_id, "targetNodeId": 3, "title": "同源草稿"},
            headers=self._headers(self.teacher),
        )
        archived = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={"snapshotId": duplicate_snapshot_id, "targetNodeId": 2, "title": "同源归档"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(draft.status_code, 201, draft.get_json())
        self.assertEqual(archived.status_code, 201, archived.get_json())
        draft_id = draft.get_json()["assignment"]["id"]
        archived_id = archived.get_json()["assignment"]["id"]

        other_snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"sourceGraphId": "other-course-graph", "filename": "other.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(other_snapshot.status_code, 201, other_snapshot.get_json())
        other_snapshot_id = other_snapshot.get_json()["snapshot"]["id"]

        progress = self.client.put(
            f"/api/v2/edu/assignments/{published_assignment['id']}/progress/1",
            json={"state": "mastered"},
            headers=self._headers(self.student),
        )
        self.assertEqual(progress.status_code, 200, progress.get_json())

        with api_v2.connect_database() as connection:
            teacher_id = connection.execute("SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)).fetchone()[0]
            student_id = connection.execute("SELECT id FROM users WHERE email = ?", ("student@example.com",)).fetchone()[0]
            connection.execute("UPDATE education_assignments SET status = 'archived' WHERE id = ?", (archived_id,))
            connection.execute(
                "INSERT INTO education_student_paths (assignment_id, user_id, path_json, updated_at) VALUES (?, ?, '{}', ?)",
                (published_assignment["id"], student_id, "2026-08-12T00:00:00"),
            )
            diagnostic_id = "diagnostic-for-deleted-graph"
            connection.execute(
                """INSERT INTO education_diagnostics
                     (id, assignment_id, user_id, node_id, question_json, answer, result, summary, created_at, updated_at)
                   VALUES (?, ?, ?, 1, '{}', 'answer', 'mastered', 'summary', ?, ?)""",
                (diagnostic_id, published_assignment["id"], student_id, "2026-08-12T00:00:00", "2026-08-12T00:00:00"),
            )
            for task_key, task_kind, user_id in (
                (published_assignment["id"], "path", teacher_id),
                (diagnostic_id, "evaluate", student_id),
            ):
                connection.execute(
                    """INSERT INTO education_ai_tasks
                         (id, task_key, user_id, task_kind, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'test', 'done', ?, ?)""",
                    (f"ai-{task_kind}", task_key, user_id, task_kind, "2026-08-12T00:00:00", "2026-08-12T00:00:00"),
                )
            connection.execute(
                "INSERT INTO education_ai_usage (user_id, usage_day, request_count, updated_at) VALUES (?, '2026-08-12', 2, ?)",
                (teacher_id, "2026-08-12T00:00:00"),
            )

        for resource in (
            api_v2._EDUCATION_SNAPSHOT_ROOT / snapshot_data["id"],
            api_v2._EDUCATION_SNAPSHOT_ROOT / duplicate_snapshot_id,
            api_v2._EDUCATION_ROOT / "assignments" / published_assignment["id"],
            api_v2._EDUCATION_ROOT / "diagnostics" / "diagnostic-for-deleted-graph",
        ):
            resource.mkdir(parents=True, exist_ok=True)
            (resource / "checkpoint.json").write_text("{}", encoding="utf-8")

        snapshot_list = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(snapshot_list.status_code, 200, snapshot_list.get_json())
        counts = {
            item["id"]: item["boundAssignmentCount"]
            for item in snapshot_list.get_json()["snapshots"]
        }
        self.assertEqual(counts[snapshot_data["id"]], 1)
        self.assertEqual(counts[duplicate_snapshot_id], 2)

        forbidden = self.client.delete(
            f"/api/v2/edu/snapshots/{snapshot_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.get_json())

        deleted = self.client.delete(
            f"/api/v2/edu/snapshots/{snapshot_data['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertCountEqual(
            deleted.get_json()["deletedSnapshotIds"],
            [snapshot_data["id"], duplicate_snapshot_id],
        )
        self.assertEqual(deleted.get_json()["deletedAssignmentCount"], 3)
        self.assertEqual(deleted.get_json()["cleanupWarnings"], [])

        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_snapshots WHERE source_graph_id = 'shared-course-graph'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_snapshots WHERE id = ?", (other_snapshot_id,)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_assignments WHERE id IN (?, ?, ?)", (published_assignment["id"], draft_id, archived_id)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_node_progress WHERE assignment_id = ?", (published_assignment["id"],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_student_paths WHERE assignment_id = ?", (published_assignment["id"],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_diagnostics WHERE assignment_id = ?", (published_assignment["id"],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_ai_tasks").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT request_count FROM education_ai_usage WHERE user_id = ?", (teacher_id,)).fetchone()[0], 2)

        self.assertFalse((api_v2._EDUCATION_SNAPSHOT_ROOT / snapshot_data["id"]).exists())
        self.assertFalse((api_v2._EDUCATION_SNAPSHOT_ROOT / duplicate_snapshot_id).exists())
        self.assertFalse((api_v2._EDUCATION_ROOT / "assignments" / published_assignment["id"]).exists())
        self.assertFalse((api_v2._EDUCATION_ROOT / "diagnostics" / "diagnostic-for-deleted-graph").exists())

    def test_agent_import_returns_stable_content_key(self):
        def import_graph(nodes):
            return self.client.post(
                "/api/v2/agent-import",
                data={
                    "nodes_file": (io.BytesIO(json.dumps({"nodes": nodes}).encode("utf-8")), "nodes.json"),
                    "edges_file": (io.BytesIO(json.dumps({"edges": EDGES}).encode("utf-8")), "edges.json"),
                },
                content_type="multipart/form-data",
                headers=self._headers(self.teacher),
            )

        first = import_graph(NODES)
        repeat = import_graph(NODES)
        changed_nodes = [{**node, "content": f"{node['content']} changed"} if node["id"] == 1 else node for node in NODES]
        changed = import_graph(changed_nodes)

        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(repeat.status_code, 201, repeat.get_json())
        self.assertEqual(changed.status_code, 201, changed.get_json())
        self.assertTrue(first.get_json()["courseGraphKey"].startswith("import:"))
        self.assertEqual(first.get_json()["courseGraphKey"], repeat.get_json()["courseGraphKey"])
        self.assertNotEqual(first.get_json()["courseGraphKey"], changed.get_json()["courseGraphKey"])

    def test_snapshot_uses_import_job_for_pdf_without_using_it_for_deduplication(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "导入图谱测试"},
            headers=self._headers(self.teacher),
        )
        class_data = created.get_json()["class"]
        job_id = "import-job-with-pdf"
        source_dir = api_v2._source_pdf_dir(job_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (source_dir / "source.tex").write_text("\\documentclass{article}", encoding="utf-8")
        (source_dir / "compile.log").write_text("ok", encoding="utf-8")

        db = api_v2.connect_database()
        teacher_id = db.execute("SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)).fetchone()[0]
        now = "2026-08-11T00:00:00"
        db.execute(
            """INSERT INTO history
                 (id, user_id, filename, node_count, edge_count, source_pdf_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'done', ?)""",
            (
                job_id,
                teacher_id,
                "chapter.tex",
                len(NODES),
                len(EDGES),
                json.dumps({
                    "status": "ready",
                    "available": True,
                    "pdf_name": "source.pdf",
                    "source_name": "source.tex",
                    "log_name": "compile.log",
                }),
                now,
            ),
        )
        db.commit()
        db.close()
        api_v2.persist_graph(job_id, "history", NODES, EDGES)

        snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={
                "sourceGraphId": "import:stable-content-key",
                "sourceJobId": job_id,
                "filename": "chapter.tex",
                "nodes": NODES,
                "edges": EDGES,
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(snapshot.status_code, 201, snapshot.get_json())
        snapshot_data = snapshot.get_json()["snapshot"]
        self.assertEqual(snapshot_data["sourceGraphId"], "import:stable-content-key")
        self.assertTrue(snapshot_data["sourcePdf"]["available"])
        pdf = self.client.get(snapshot_data["sourcePdf"]["pdf_url"], headers=self._headers(self.teacher))
        self.assertEqual(pdf.status_code, 200, pdf.get_json(silent=True))
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        pdf.close()

    def test_progress_preserves_node_zero_and_does_not_wait_for_ai(self):
        class_data, _snapshot_data, assignment_data = self._create_published_assignment(
            nodes=ZERO_NODES,
            edges=ZERO_EDGES,
            target_node_id=1,
        )
        student_view = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_view.status_code, 200, student_view.get_json())
        self.assertEqual([step["nodeId"] for step in student_view.get_json()["assignment"]["path"]["steps"]], [0, 1])

        with patch.object(api_v2, "_education_ai_task", side_effect=AssertionError("progress must not call AI")):
            progress = self.client.put(
                f"/api/v2/edu/assignments/{assignment_data['id']}/progress/0",
                json={"state": "mastered"},
                headers=self._headers(self.student),
            )
        self.assertEqual(progress.status_code, 200, progress.get_json())
        self.assertEqual(progress.get_json()["path"]["steps"][0]["state"], "mastered")

        reloaded = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(reloaded.status_code, 200, reloaded.get_json())
        self.assertEqual(reloaded.get_json()["assignment"]["path"]["steps"][0]["state"], "mastered")

    def test_student_path_ignores_legacy_personal_path_and_compat_endpoint_does_not_call_ai(self):
        class_data, _snapshot_data, assignment_data = self._create_published_assignment()
        with api_v2.app.app_context():
            db = api_v2._get_db()
            db.execute(
                """INSERT INTO education_student_paths (assignment_id, user_id, path_json, updated_at)
                   VALUES (?, (SELECT id FROM users WHERE email = ?), ?, ?)""",
                (
                    assignment_data["id"],
                    "student@example.com",
                    json.dumps({
                        "targetNodeId": 3,
                        "candidateNodeIds": [1, 2, 3],
                        "steps": [
                            {"nodeId": 3, "order": 1, "role": "target", "required": True, "rationale": "旧目标"},
                            {"nodeId": 1, "order": 2, "role": "prerequisite", "required": False, "rationale": "旧基础"},
                            {"nodeId": 2, "order": 3, "role": "prerequisite", "required": True, "rationale": "旧中间"},
                        ],
                    }, ensure_ascii=False),
                    "2026-08-10T00:00:00",
                ),
            )
            db.commit()

        with patch.object(api_v2, "_education_ai_task", side_effect=AssertionError("student path must not call AI")) as ai_task:
            loaded = self.client.get(
                f"/api/v2/edu/assignments/{assignment_data['id']}",
                headers=self._headers(self.student),
            )
            self.assertEqual(loaded.status_code, 200, loaded.get_json())
            self.assertEqual(
                [step["nodeId"] for step in loaded.get_json()["assignment"]["path"]["steps"]],
                [1, 2, 3],
            )

            compatibility = self.client.post(
                f"/api/v2/edu/assignments/{assignment_data['id']}/personalize",
                headers=self._headers(self.student),
            )
            self.assertEqual(compatibility.status_code, 200, compatibility.get_json())
            self.assertEqual(
                [step["nodeId"] for step in compatibility.get_json()["path"]["steps"]],
                [1, 2, 3],
            )
            ai_task.assert_not_called()

    def test_apply_progress_treats_zero_as_a_valid_node_id(self):
        path = {"steps": [
            {"nodeId": 0, "state": "not_started"},
            {"nodeId": 1, "state": "not_started"},
        ]}
        updated = api_v2.apply_progress_to_path(path, {
            0: {"state": "mastered"},
            1: {"state": "needs_review"},
        })
        self.assertEqual([step["state"] for step in updated["steps"]], ["mastered", "needs_review"])

    def test_legacy_diagnostic_endpoint_is_rejected_without_changing_teacher_path(self):
        _class_data, _snapshot_data, assignment_data = self._create_published_assignment()
        with patch.object(api_v2, "_education_ai_task") as ai_task:
            created = self.client.post(
                f"/api/v2/edu/assignments/{assignment_data['id']}/diagnostics",
                json={"nodeId": 1},
                headers=self._headers(self.student),
            )
            self.assertEqual(created.status_code, 410, created.get_json())
            self.assertEqual(created.get_json()["code"], "diagnostics_replaced")
            ai_task.assert_not_called()

        loaded = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(loaded.status_code, 200, loaded.get_json())
        path = loaded.get_json()["assignment"]["path"]
        self.assertEqual([step["nodeId"] for step in path["steps"]], [1, 2, 3])
        self.assertEqual(path["steps"][0]["state"], "not_started")

    def test_student_class_assignment_count_excludes_teacher_drafts(self):
        class_data, snapshot_data, _published_assignment = self._create_published_assignment()
        draft = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={
                "snapshotId": snapshot_data["id"],
                "targetNodeId": 3,
                "title": "draft assignment",
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(draft.status_code, 201, draft.get_json())

        student_classes = self.client.get(
            "/api/v2/edu/classes",
            headers=self._headers(self.student),
        )
        teacher_classes = self.client.get(
            "/api/v2/edu/classes",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(student_classes.status_code, 200, student_classes.get_json())
        self.assertEqual(teacher_classes.status_code, 200, teacher_classes.get_json())
        self.assertEqual(student_classes.get_json()["classes"][0]["assignmentCount"], 1)
        self.assertEqual(teacher_classes.get_json()["classes"][0]["assignmentCount"], 2)

    def test_published_assignment_metadata_edit_and_archive_preserves_progress(self):
        class_data, _snapshot_data, assignment_data = self._create_published_assignment()
        progress = self.client.put(
            f"/api/v2/edu/assignments/{assignment_data['id']}/progress/1",
            json={"state": "mastered"},
            headers=self._headers(self.student),
        )
        self.assertEqual(progress.status_code, 200, progress.get_json())

        updated = self.client.patch(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            json={"title": "  更新后的任务  ", "dueAt": "2026-09-25T09:30:00Z"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()["assignment"]["title"], "更新后的任务")
        self.assertEqual(updated.get_json()["assignment"]["dueAt"], "2026-09-25T09:30:00Z")

        student_view = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_view.status_code, 200, student_view.get_json())
        self.assertEqual(student_view.get_json()["assignment"]["title"], "更新后的任务")

        cleared = self.client.patch(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            json={"dueAt": None},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertIsNone(cleared.get_json()["assignment"]["dueAt"])

        archived = self.client.delete(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(archived.status_code, 200, archived.get_json())
        for token in (self.teacher, self.student):
            assignment_view = self.client.get(
                f"/api/v2/edu/assignments/{assignment_data['id']}",
                headers=self._headers(token),
            )
            self.assertEqual(assignment_view.status_code, 404, assignment_view.get_json())

        teacher_assignments = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            headers=self._headers(self.teacher),
        )
        student_assignments = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            headers=self._headers(self.student),
        )
        self.assertEqual(teacher_assignments.status_code, 200)
        self.assertEqual(student_assignments.status_code, 200)
        self.assertEqual(teacher_assignments.get_json()["assignments"], [])
        self.assertEqual(student_assignments.get_json()["assignments"], [])
        teacher_classes = self.client.get("/api/v2/edu/classes", headers=self._headers(self.teacher))
        self.assertEqual(teacher_classes.status_code, 200)
        self.assertEqual(teacher_classes.get_json()["classes"][0]["assignmentCount"], 0)
        with api_v2.connect_database() as connection:
            status = connection.execute(
                "SELECT status FROM education_assignments WHERE id = ?",
                (assignment_data["id"],),
            ).fetchone()[0]
            progress_count = connection.execute(
                "SELECT COUNT(*) FROM education_node_progress WHERE assignment_id = ?",
                (assignment_data["id"],),
            ).fetchone()[0]
        self.assertEqual(status, "archived")
        self.assertEqual(progress_count, 1)

    def test_published_assignment_edit_permissions_and_validation(self):
        _class_data, _snapshot_data, assignment_data = self._create_published_assignment()
        blank_title = self.client.patch(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            json={"title": "  "},
            headers=self._headers(self.teacher),
        )
        invalid_due = self.client.patch(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            json={"dueAt": "not-a-date"},
            headers=self._headers(self.teacher),
        )
        student_edit = self.client.patch(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            json={"title": "学生不应修改"},
            headers=self._headers(self.student),
        )
        outsider_delete = self.client.delete(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.outsider),
        )
        self.assertEqual(blank_title.status_code, 400)
        self.assertEqual(invalid_due.status_code, 400)
        self.assertEqual(student_edit.status_code, 403)
        self.assertEqual(outsider_delete.status_code, 404)

    def test_non_members_and_student_cannot_access_teacher_operations(self):
        class_data, snapshot_data, assignment_data = self._create_published_assignment()

        outsider_assignment = self.client.get(
            f"/api/v2/edu/assignments/{assignment_data['id']}",
            headers=self._headers(self.outsider),
        )
        outsider_pdf = self.client.get(
            f"/api/v2/edu/snapshots/{snapshot_data['id']}/source-pdf",
            headers=self._headers(self.outsider),
        )
        student_publish = self.client.post(
            f"/api/v2/edu/assignments/{assignment_data['id']}/publish",
            headers=self._headers(self.student),
        )
        student_snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"nodes": NODES, "edges": EDGES},
            headers=self._headers(self.student),
        )

        self.assertEqual(outsider_assignment.status_code, 404)
        self.assertEqual(outsider_pdf.status_code, 404)
        self.assertEqual(student_publish.status_code, 403)
        self.assertEqual(student_snapshot.status_code, 403)

    def test_teacher_class_management_and_removed_member_lifecycle(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "待管理班级"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "测试学生", "studentNumber": "S001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())

        roster = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/members",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(roster.status_code, 200, roster.get_json())
        self.assertEqual(roster.get_json()["members"][0]["status"], "active")
        student_id = roster.get_json()["members"][0]["userId"]
        student_roster = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/members",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_roster.status_code, 403)

        renamed = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}",
            json={"title": "已重命名班级"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(renamed.status_code, 200, renamed.get_json())
        self.assertEqual(renamed.get_json()["class"]["title"], "已重命名班级")

        removed = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}/members/{student_id}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        student_classes = self.client.get(
            "/api/v2/edu/classes",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_classes.status_code, 200)
        self.assertEqual(student_classes.get_json()["classes"], [])
        blocked_rejoin = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "测试学生", "studentNumber": "S001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(blocked_rejoin.status_code, 403)
        self.assertEqual(blocked_rejoin.get_json()["code"], "class_membership_removed")

        restored = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/members/{student_id}/restore",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertEqual(
            len(self.client.get("/api/v2/edu/classes", headers=self._headers(self.student)).get_json()["classes"]),
            1,
        )

        dissolved = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(dissolved.status_code, 200, dissolved.get_json())
        self.assertEqual(
            self.client.get("/api/v2/edu/classes", headers=self._headers(self.teacher)).get_json()["classes"],
            [],
        )
        with api_v2.connect_database() as connection:
            archived_at = connection.execute(
                "SELECT archived_at FROM education_classes WHERE id = ?",
                (class_data["id"],),
            ).fetchone()[0]
        self.assertTrue(archived_at)

    def test_snapshot_is_immutable_and_database_pragmas_are_enabled(self):
        _class_data, snapshot_data, assignment_data = self._create_published_assignment()
        NODES[0]["title_zh"] = "原图已被修改"
        try:
            response = self.client.get(
                f"/api/v2/edu/assignments/{assignment_data['id']}",
                headers=self._headers(self.teacher),
            )
            frozen_nodes = response.get_json()["assignment"]["snapshot"]["nodes"]
            self.assertEqual(frozen_nodes[0]["title_zh"], "线性无关")
        finally:
            NODES[0]["title_zh"] = "线性无关"

        health = api_v2.database_health()
        self.assertTrue(health["ok"], health)
        with api_v2.connect_database() as connection:
            engine = connection.execute(
                """SELECT engine FROM information_schema.tables
                     WHERE table_schema = DATABASE() AND table_name = 'education_snapshots'"""
            ).fetchone()[0]
            forbidden = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                     WHERE table_schema = DATABASE()
                       AND table_name = 'education_snapshots'
                       AND column_name IN ('nodes_json', 'edges_json')"""
            ).fetchone()[0]
        self.assertEqual(engine, "InnoDB")
        self.assertEqual(forbidden, 0)

    def test_student_proof_context_propagates_is_idempotent_and_can_be_corrected(self):
        _class_data, _snapshot_data, assignment = self._create_published_assignment()
        self._save_llm_config(
            self.student,
            api_url="https://student.example.test/v1",
            model_name="student-model",
            api_key="student-key",
        )
        assist_result = {
            "response": "先检查你对线性无关定义的使用。",
            "learningDelta": [
                {
                    "kind": "misconception",
                    "claim": "把生成集误当成线性无关组",
                    "confidence": 0.9,
                    "severity": "high",
                    "relatedNodeIds": [1],
                },
                {
                    "kind": "understanding",
                    "claim": "知道应当检查线性组合系数是否全为零",
                    "confidence": 0.85,
                    "severity": "low",
                    "relatedNodeIds": [1],
                }
            ],
            "classificationStatus": "classified",
        }
        payload = {
            "nodeId": 2,
            "action": "check",
            "userProof": "这个集合生成整个空间，所以它线性无关。",
            "clientInteractionId": "proof-context-1",
            "contextVersion": 0,
        }
        with patch.object(api_v2, "create_education_context", return_value=object()) as create_context:
            with patch.object(api_v2, "run_structured_proof_assist", return_value=assist_result) as runner:
                response = self.client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/proof-assist",
                    json=payload,
                    headers=self._headers(self.student),
                )
                repeated = self.client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/proof-assist",
                    json=payload,
                    headers=self._headers(self.student),
                )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertEqual(response.get_json()["interactionId"], repeated.get_json()["interactionId"])
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(create_context.call_args.args[1]["api_key"], "student-key")
        evidence_id = response.get_json()["stateChanges"][0]["evidenceId"]

        direct = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context?nodeId=2",
            headers=self._headers(self.student),
        )
        prerequisite = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context?nodeId=1",
            headers=self._headers(self.student),
        )
        self.assertEqual(direct.status_code, 200, direct.get_json())
        self.assertEqual(direct.get_json()["contextPreview"]["masteryState"], "needs_review")
        risks = prerequisite.get_json()["contextPreview"]["relatedRisks"]
        self.assertEqual(len(risks), 1)
        self.assertEqual(risks[0]["relationRole"], "prerequisite_risk")
        related_context = prerequisite.get_json()["contextPreview"]["relatedContext"]
        self.assertEqual(len(related_context), 1)
        self.assertEqual(related_context[0]["kind"], "understanding")
        self.assertEqual(prerequisite.get_json()["contextPreview"]["masteryState"], "unknown")

        with api_v2.connect_database() as connection:
            student_user_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", ("student@example.com",)
            ).fetchone()[0]
        teacher_summary = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/students/{student_user_id}/context-summary",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_summary.status_code, 200, teacher_summary.get_json())
        self.assertNotIn("assistant_response", json.dumps(teacher_summary.get_json()))
        self.assertNotIn("user_proof", json.dumps(teacher_summary.get_json()))
        self.assertEqual(teacher_summary.get_json()["summary"]["evidence"][0]["id"], evidence_id)

        corrected = self.client.patch(
            f"/api/v2/edu/context/evidence/{evidence_id}",
            json={"status": "retracted", "note": "这不是我的意思"},
            headers=self._headers(self.student),
        )
        self.assertEqual(corrected.status_code, 200, corrected.get_json())
        after = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context?nodeId=1",
            headers=self._headers(self.student),
        )
        self.assertEqual(after.get_json()["contextPreview"]["relatedRisks"], [])
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_interactions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_evidence_feedback").fetchone()[0], 1)

    def test_unclassified_proof_reply_is_returned_and_queued_as_rebuildable_raw_event(self):
        _class_data, _snapshot_data, assignment = self._create_published_assignment()
        configured = {
            "MATHWEAVER_EDU_LLM_API_URL": "https://llm.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "test-model",
            "MATHWEAVER_EDU_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, configured, clear=False):
            with patch.object(api_v2, "create_education_context", return_value=object()):
                with patch.object(api_v2, "run_structured_proof_assist", return_value={
                    "response": "先检查这一步是否使用了定理条件。",
                    "learningDelta": [],
                    "classificationStatus": "pending",
                }):
                    response = self.client.post(
                        f"/api/v2/edu/assignments/{assignment['id']}/proof-assist",
                        json={
                            "nodeId": 2,
                            "action": "hint",
                            "userProof": "暂时无法结构化分类的学生草稿",
                            "clientInteractionId": "proof-pending-classification",
                        },
                        headers=self._headers(self.student),
                    )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["response"], "先检查这一步是否使用了定理条件。")
        self.assertEqual(response.get_json()["classificationStatus"], "pending")
        self.assertEqual(response.get_json()["stateChanges"], [])
        with api_v2.connect_database() as connection:
            event = connection.execute(
                "SELECT id, user_proof, assistant_response, classification_status FROM learning_interactions"
            ).fetchone()
            evidence_count = connection.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0]
        self.assertEqual(event[1], "暂时无法结构化分类的学生草稿")
        self.assertEqual(event[2], "先检查这一步是否使用了定理条件。")
        self.assertEqual(event[3], "pending")
        self.assertEqual(evidence_count, 0)

        rebuilt_delta = [{
            "kind": "gap",
            "claim": "尚未检查所用定理的条件",
            "confidence": 0.82,
            "severity": "medium",
            "relatedNodeIds": [1],
        }]
        connection = api_v2.connect_database()
        with patch.object(
            student_context,
            "run_structured_education_tasks",
            return_value={event[0]: {"learningDelta": rebuilt_delta}},
        ) as batch_runner:
            rebuilt = student_context.rebuild_pending_student_context(
                connection,
                context=object(),
                checkpoint_dir=Path(self.temp_dir.name) / "context-rebuild-checkpoints",
            )
        connection.commit()
        classified = connection.execute(
            "SELECT classification_status, result_json FROM learning_interactions WHERE id = ?",
            (event[0],),
        ).fetchone()
        rebuilt_evidence = connection.execute(
            "SELECT claim FROM learning_evidence WHERE interaction_id = ?", (event[0],)
        ).fetchone()
        connection.close()
        self.assertEqual(rebuilt, {"requested": 1, "rebuilt": 1, "unresolvedInteractionIds": []})
        self.assertEqual(batch_runner.call_args.kwargs["task_kind"], "proof_context_rebuild")
        self.assertEqual(classified["classification_status"], "classified")
        self.assertEqual(json.loads(classified["result_json"])["classificationStatus"], "classified")
        self.assertEqual(rebuilt_evidence["claim"], "尚未检查所用定理的条件")

    def test_student_context_budget_preserves_pinned_evidence_and_raw_events(self):
        _class_data, _snapshot_data, assignment = self._create_published_assignment()
        configured = {
            "MATHWEAVER_EDU_LLM_API_URL": "https://llm.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "test-model",
            "MATHWEAVER_EDU_LLM_API_KEY": "test-key",
        }
        results = []
        for index in range(4):
            results.append({
                "response": f"第 {index + 1} 次形成性反馈" + "反馈" * 1200,
                "learningDelta": [
                    {
                        "kind": "goal",
                        "claim": f"目标 {index + 1}：" + "保持当前证明目标" * 180,
                        "confidence": 0.9,
                        "severity": "medium",
                        "relatedNodeIds": [],
                    },
                    {
                        "kind": "misconception",
                        "claim": f"误解 {index + 1}：" + "尚未说明线性无关" * 180,
                        "confidence": 0.85,
                        "severity": "high",
                        "relatedNodeIds": [1],
                    },
                ],
                "classificationStatus": "classified",
            })
        responses = []
        with patch.dict(os.environ, configured, clear=False):
            with patch.object(api_v2, "create_education_context", return_value=object()):
                with patch.object(api_v2, "run_structured_proof_assist", side_effect=results):
                    for index in range(4):
                        response = self.client.post(
                            f"/api/v2/edu/assignments/{assignment['id']}/proof-assist",
                            json={
                                "nodeId": 2,
                                "action": "check",
                                "userProof": f"原始草稿 {index + 1}：" + "这是不可覆盖的原始交互" * 1600,
                                "clientInteractionId": f"proof-budget-{index}",
                            },
                            headers=self._headers(self.student),
                        )
                        self.assertEqual(response.status_code, 200, response.get_json())
                        responses.append(response.get_json())

        corrected_evidence_id = responses[0]["stateChanges"][1]["evidenceId"]
        corrected = self.client.patch(
            f"/api/v2/edu/context/evidence/{corrected_evidence_id}",
            json={"status": "retracted", "note": "学生纠正旧判断"},
            headers=self._headers(self.student),
        )
        self.assertEqual(corrected.status_code, 200, corrected.get_json())
        current = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context?nodeId=2",
            headers=self._headers(self.student),
        )
        self.assertEqual(current.status_code, 200, current.get_json())
        body = current.get_json()
        self.assertLessEqual(body["historyTokenEstimate"], 6000)
        self.assertEqual(body["contextPreview"]["goal"]["id"], responses[-1]["stateChanges"][0]["evidenceId"])
        self.assertIn(
            corrected_evidence_id,
            [item["id"] for item in body["contextPreview"]["resolvedItems"]],
        )
        self.assertIn(
            responses[-1]["stateChanges"][1]["evidenceId"],
            [item["id"] for item in body["contextPreview"]["openGaps"]],
        )
        with api_v2.connect_database() as connection:
            interaction_rows = connection.execute(
                "SELECT user_proof, context_snapshot_json FROM learning_interactions ORDER BY created_at"
            ).fetchall()
            summary = connection.execute(
                """SELECT source_watermark, schema_version, prompt_version, token_count
                     FROM learning_context_summaries WHERE scope_type = 'node' LIMIT 1"""
            ).fetchone()
        self.assertEqual(len(interaction_rows), 4)
        self.assertTrue(all(len(row[0]) > 10000 for row in interaction_rows))
        self.assertTrue(all(len(json.loads(row[1]).get("recentInteractions", [])) <= 4 for row in interaction_rows))
        self.assertTrue(summary[0])
        self.assertEqual(summary[1], 1)
        self.assertEqual(summary[2], "student-context-v1")
        self.assertGreater(summary[3], 0)

    def test_student_can_explicitly_export_and_delete_only_their_course_context(self):
        class_data, _snapshot_data, assignment = self._create_published_assignment()
        configured = {
            "MATHWEAVER_EDU_LLM_API_URL": "https://llm.example.test/v1",
            "MATHWEAVER_EDU_LLM_MODEL": "test-model",
            "MATHWEAVER_EDU_LLM_API_KEY": "test-key",
        }
        assist_result = {
            "response": "检查定义的适用条件。",
            "learningDelta": [{
                "kind": "gap",
                "claim": "还没有说明线性无关",
                "confidence": 0.8,
                "severity": "medium",
                "relatedNodeIds": [1],
            }],
            "classificationStatus": "classified",
        }
        with patch.dict(os.environ, configured, clear=False):
            with patch.object(api_v2, "create_education_context", return_value=object()):
                with patch.object(api_v2, "run_structured_proof_assist", return_value=assist_result):
                    created = self.client.post(
                        f"/api/v2/edu/assignments/{assignment['id']}/proof-assist",
                        json={
                            "nodeId": 2,
                            "action": "check",
                            "userProof": "需要导出的原始证明草稿",
                            "clientInteractionId": "proof-export-delete",
                        },
                        headers=self._headers(self.student),
                    )
        self.assertEqual(created.status_code, 200, created.get_json())

        archived = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(archived.status_code, 200, archived.get_json())

        exported = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/student-context/export",
            headers=self._headers(self.student),
        )
        self.assertEqual(exported.status_code, 200, exported.get_json())
        self.assertIn("attachment", exported.headers.get("Content-Disposition", ""))
        self.assertEqual(exported.get_json()["interactions"][0]["user_proof"], "需要导出的原始证明草稿")
        self.assertEqual(exported.get_json()["evidence"][0]["claim"], "还没有说明线性无关")

        wrong_confirmation = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}/student-context",
            json={"confirmClassId": "wrong-class"},
            headers=self._headers(self.student),
        )
        outsider_delete = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}/student-context",
            json={"confirmClassId": class_data["id"]},
            headers=self._headers(self.outsider),
        )
        teacher_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/student-context/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(wrong_confirmation.status_code, 400, wrong_confirmation.get_json())
        self.assertEqual(outsider_delete.status_code, 404, outsider_delete.get_json())
        self.assertEqual(teacher_export.status_code, 403, teacher_export.get_json())

        deleted = self.client.delete(
            f"/api/v2/edu/classes/{class_data['id']}/student-context",
            json={"confirmClassId": class_data["id"]},
            headers=self._headers(self.student),
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(deleted.get_json()["deletedInteractions"], 1)
        self.assertEqual(deleted.get_json()["deletedEvidence"], 1)
        empty_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/student-context/export",
            headers=self._headers(self.student),
        )
        self.assertEqual(empty_export.status_code, 200, empty_export.get_json())
        self.assertEqual(empty_export.get_json()["interactions"], [])
        self.assertEqual(empty_export.get_json()["nodeModels"], [])
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_interactions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM student_node_models").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_node_identities").fetchone()[0], len(NODES))

    def test_student_context_identity_is_course_scoped_and_stable_across_snapshots(self):
        class_data, _snapshot_data, assignment = self._create_published_assignment()
        first = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context",
            headers=self._headers(self.student),
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        second_snapshot = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "linear-algebra-v2.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(second_snapshot.status_code, 201, second_snapshot.get_json())
        second_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            json={
                "snapshotId": second_snapshot.get_json()["snapshot"]["id"],
                "targetNodeId": 3,
                "title": "同课程第二次练习",
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(second_assignment.status_code, 201, second_assignment.get_json())
        second_id = second_assignment.get_json()["assignment"]["id"]
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assessment_nodes SET status = 'exempt' WHERE assignment_id = ?",
                (second_id,),
            )
        published = self.client.post(
            f"/api/v2/edu/assignments/{second_id}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        second = self.client.get(
            f"/api/v2/edu/assignments/{second_id}/student-context",
            headers=self._headers(self.student),
        )
        self.assertEqual(second.status_code, 200, second.get_json())
        with api_v2.connect_database() as connection:
            identity_count = connection.execute(
                "SELECT COUNT(*) FROM education_node_identities WHERE class_id = ?",
                (class_data["id"],),
            ).fetchone()[0]
            occurrence_count = connection.execute(
                """SELECT COUNT(*) FROM education_node_occurrences o
                   JOIN education_snapshots s ON s.id = o.snapshot_id
                  WHERE s.class_id = ?""",
                (class_data["id"],),
            ).fetchone()[0]
        self.assertEqual(identity_count, len(NODES))
        self.assertEqual(occurrence_count, len(NODES) * 2)

        other_class, _other_snapshot, other_assignment = self._create_published_assignment()
        other = self.client.get(
            f"/api/v2/edu/assignments/{other_assignment['id']}/student-context",
            headers=self._headers(self.student),
        )
        self.assertEqual(other.status_code, 200, other.get_json())
        with api_v2.connect_database() as connection:
            first_ids = {
                row[0] for row in connection.execute(
                    "SELECT id FROM education_node_identities WHERE class_id = ?", (class_data["id"],)
                )
            }
            other_ids = {
                row[0] for row in connection.execute(
                    "SELECT id FROM education_node_identities WHERE class_id = ?", (other_class["id"],)
                )
            }
        self.assertTrue(first_ids)
        self.assertTrue(other_ids)
        self.assertTrue(first_ids.isdisjoint(other_ids))

    def test_student_context_isolated_from_outsiders_and_legacy_proof_assist_remains_available(self):
        _class_data, _snapshot_data, assignment = self._create_published_assignment()
        outsider_context = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/student-context?nodeId=2",
            headers=self._headers(self.outsider),
        )
        self.assertEqual(outsider_context.status_code, 404, outsider_context.get_json())

        legacy_payload = {
            "action": "hint",
            "node": {"id": 99, "title_zh": "匿名节点", "content": "待证明命题"},
            "userProof": "匿名单节点草稿",
            "llm_config": {
                "api_url": "https://llm.example.test/v1",
                "model_name": "test-model",
                "api_key": "test-key",
            },
        }
        with patch.object(api_v2, "SimpleLLM") as llm:
            llm.return_value.ask.return_value = "只给出单节点提示"
            legacy = self.client.post("/api/v2/proof-assist", json=legacy_payload)
        self.assertEqual(legacy.status_code, 200, legacy.get_json())
        self.assertEqual(legacy.get_json()["response"], "只给出单节点提示")
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_interactions").fetchone()[0], 0)

    def test_published_historical_reference_can_be_supplemented_before_grading(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self._complete_all_assignment_assessments(assignment)
        submission = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        ).get_json()["submission"]
        question = assignment["assessments"][0]["questions"][0]
        with api_v2.connect_database() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM education_assignment_submissions WHERE id = ?",
                (submission["id"],),
            ).fetchone()
            snapshot_payload = json.loads(row[0])
            next(item for item in snapshot_payload["questions"] if item["questionId"] == question["id"])["referenceAnswer"] = ""
            connection.execute(
                "UPDATE education_assessment_questions SET reference_answer = '' WHERE id = ?",
                (question["id"],),
            )
            connection.execute(
                "UPDATE education_submission_question_grades SET reference_answer = '' WHERE submission_id = ? AND question_id = ?",
                (submission["id"], question["id"]),
            )
            connection.execute(
                "UPDATE education_assignment_submissions SET snapshot_json = ? WHERE id = ?",
                (json.dumps(snapshot_payload, ensure_ascii=False), submission["id"]),
            )

        blocked = self.client.post(
            f"/api/v2/edu/submissions/{submission['id']}/evaluate",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertEqual(blocked.get_json()["code"], "assessment_scoring_required")

        supplemented = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/{question['nodeId']}/questions/{question['id']}",
            json={
                "referenceAnswer": "补录的历史参考答案",
                "expectedPoints": question["expectedPoints"],
                "maxScore": question["maxScore"],
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(supplemented.status_code, 200, supplemented.get_json())
        detail = self.client.get(
            f"/api/v2/edu/submissions/{submission['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["submission"]
        updated_grade = next(grade for grade in detail["grades"] if grade["questionId"] == question["id"])
        self.assertEqual(updated_grade["referenceAnswer"], "补录的历史参考答案")

        frozen = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/{question['nodeId']}/questions/{question['id']}",
            json={
                "referenceAnswer": "再次改写",
                "expectedPoints": question["expectedPoints"],
                "maxScore": question["maxScore"],
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(frozen.status_code, 409, frozen.get_json())
        self.assertEqual(frozen.get_json()["code"], "assessment_scoring_frozen")

    def test_whole_assignment_grading_release_flow(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        attempts = self._complete_all_assignment_assessments(
            assignment,
            answer=(
                r"\begin{pmatrix}1&2\\3&4\end{pmatrix}"
                r"\xrightarrow{R_2\to R_2-R_1}"
                r"\begin{pmatrix}1&2\\2&2\end{pmatrix}"
            ),
        )

        edited_answer = "edited before submit"
        editable = self.client.patch(
            f"/api/v2/edu/assessment-attempts/{attempts[0]['id']}",
            json={"answers": {attempts[0]["questions"][0]["id"]: edited_answer}},
            headers=self._headers(self.student),
        )
        self.assertEqual(editable.status_code, 200, editable.get_json())

        with patch.object(api_v2, "_education_ai_tasks", side_effect=AssertionError("student submission must not call AI")) as ai_tasks:
            submitted = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/submissions",
                headers=self._headers(self.student),
            )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        ai_tasks.assert_not_called()
        submission = submitted.get_json()["submission"]
        repeated = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertEqual(repeated.get_json()["submission"]["id"], submission["id"] )
        locked = self.client.patch(
            f"/api/v2/edu/assessment-attempts/{attempts[0]['id']}",
            json={"answers": {attempts[0]["questions"][0]["id"]: "changed after submit"}},
            headers=self._headers(self.student),
        )
        self.assertEqual(locked.status_code, 409, locked.get_json())
        self.assertEqual(locked.get_json()["code"], "assignment_already_submitted")

        hidden = self.client.get(
            f"/api/v2/edu/submissions/{submission['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_json())
        self.assertNotIn("grades", hidden.get_json()["submission"])
        outsider = self.client.get(
            f"/api/v2/edu/submissions/{submission['id']}",
            headers=self._headers(self.outsider),
        )
        self.assertEqual(outsider.status_code, 404, outsider.get_json())

        captured_tasks = {}
        def fake_grading_tasks(**kwargs):
            captured_tasks.update(kwargs["tasks"])
            return {
                question_id: {
                    "suggestedScore": round(task["maxScore"] * 0.7, 1),
                    "maxScore": task["maxScore"],
                    "rationale": "结构化评分依据",
                    "correctPoints": ["步骤清楚"],
                    "issues": [],
                    "studentFeedback": "继续保持",
                    "confidence": 0.9,
                    "needsTeacherReview": False,
                }
                for question_id, task in kwargs["tasks"].items()
            }
        with patch.object(api_v2, "_education_ai_tasks", side_effect=fake_grading_tasks) as grading_tasks:
            evaluated = self.client.post(
                f"/api/v2/edu/submissions/{submission['id']}/evaluate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(evaluated.status_code, 200, evaluated.get_json())
        grading_tasks.assert_called_once()
        evaluated_submission = evaluated.get_json()["submission"]
        self.assertEqual(evaluated_submission["aiStatus"], "ready")
        self.assertTrue(captured_tasks)
        self.assertTrue(all("matrixCheck" in task for task in captured_tasks.values()))
        self.assertIn(edited_answer, {task["studentAnswer"] for task in captured_tasks.values()})
        self.assertTrue(any(task["matrixCheck"]["status"] == "verified" for task in captured_tasks.values()))
        edited_task = next(task for task in captured_tasks.values() if task["studentAnswer"] == edited_answer)
        self.assertEqual(edited_task["matrixCheck"]["status"], "not_applicable")

        grade_payload = []
        for grade in evaluated_submission["grades"]:
            grade_payload.append({
                "questionId": grade["questionId"],
                "teacherScore": grade["maxScore"] if grade["nodeId"] == 1 else 0,
                "teacherFeedback": "教师最终评语",
            })
        saved = self.client.patch(
            f"/api/v2/edu/submissions/{submission['id']}/grade",
            json={"grades": grade_payload, "teacherSummary": "教师整体结论"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        finalized = self.client.post(
            f"/api/v2/edu/submissions/{submission['id']}/finalize",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(finalized.status_code, 200, finalized.get_json())
        self.assertEqual(finalized.get_json()["submission"]["status"], "finalized")

        still_hidden = self.client.get(
            f"/api/v2/edu/submissions/{submission['id']}",
            headers=self._headers(self.student),
        )
        self.assertNotIn("grades", still_hidden.get_json()["submission"])
        released = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/grades/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(released.status_code, 200, released.get_json())

        report = self.client.get(
            f"/api/v2/edu/submissions/{submission['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(report.status_code, 200, report.get_json())
        report_submission = report.get_json()["submission"]
        self.assertEqual(report_submission["status"], "released")
        self.assertEqual(len(report_submission["grades"]), len(grade_payload))
        self.assertTrue(all(grade["referenceAnswer"] for grade in report_submission["grades"]))
        self.assertTrue(all("matrixReport" in grade and "aiResult" in grade for grade in report_submission["grades"]))
        assignment_view = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.student),
        ).get_json()["assignment"]
        states = {step["nodeId"]: step["state"] for step in assignment_view["path"]["steps"]}
        self.assertEqual(states[1], "mastered")
        self.assertEqual(states[2], "needs_review")
        self.assertEqual(states[3], "needs_review")

    def test_ai_failure_keeps_matrix_reports_and_allows_manual_release(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self._complete_all_assignment_assessments(assignment)
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        ).get_json()["submission"]

        with patch.object(api_v2, "_education_ai_tasks", side_effect=RuntimeError("provider unavailable")):
            failed = self.client.post(
                f"/api/v2/edu/submissions/{submitted['id']}/evaluate",
                headers=self._headers(self.teacher),
            )
        self.assertGreaterEqual(failed.status_code, 400)
        detail = self.client.get(
            f"/api/v2/edu/submissions/{submitted['id']}",
            headers=self._headers(self.teacher),
        ).get_json()["submission"]
        self.assertEqual(detail["aiStatus"], "failed")
        self.assertTrue(all(grade["matrixReport"] for grade in detail["grades"]))

        manual = [{
            "questionId": grade["questionId"],
            "teacherScore": grade["maxScore"],
            "teacherFeedback": "人工评分",
        } for grade in detail["grades"]]
        saved = self.client.patch(
            f"/api/v2/edu/submissions/{submitted['id']}/grade",
            json={"grades": manual, "teacherSummary": "AI 不可用，教师人工定稿"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        finalized = self.client.post(
            f"/api/v2/edu/submissions/{submitted['id']}/finalize",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(finalized.status_code, 200, finalized.get_json())
        released = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/grades/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(released.status_code, 200, released.get_json())

    def test_graph_assignment_still_requires_scoring_points(self):
        _class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        question = assignment["assessments"][0]["questions"][0]
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assessment_questions SET expected_points_json = ? WHERE id = ?",
                (json.dumps([], ensure_ascii=False), question["id"]),
            )
            connection.commit()

        blocked = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertEqual(blocked.get_json()["code"], "assessment_scoring_required")


    def test_direct_assignment_empty_draft_and_multipart_import(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Empty direct assignment course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "Empty draft student", "studentNumber": "E001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())

        empty = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments/direct",
            data={"title": "待导入作业", "questions": "[]"},
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(empty.status_code, 201, empty.get_json())
        assignment = empty.get_json()["assignment"]
        self.assertEqual(assignment["directStructureVersion"], 1)
        self.assertEqual(assignment["targetNodeId"], 0)
        self.assertEqual(assignment["path"]["steps"], [])
        self.assertEqual(assignment["path"]["edges"], [])
        self.assertEqual(assignment["assessments"], [])
        self.assertEqual(assignment["snapshot"]["nodes"], [])
        self.assertEqual(assignment["snapshot"]["edges"], [])
        self.assertNotIn("source", assignment)

        publish = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(publish.status_code, 409, publish.get_json())
        self.assertEqual(publish.get_json().get("code"), "assessment_scoring_required")

        imported = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/direct-questions",
            data={
                "questions": json.dumps([{"order": 1, "question": "导入的第一题"}], ensure_ascii=False),
                "sourceOrigin": "ocr",
                "sourceText": "第 1 题\n导入的第一题",
                "source_file": (io.BytesIO(b"%PDF-empty-import"), "imported.pdf"),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(imported.status_code, 200, imported.get_json())
        updated = imported.get_json()["assignment"]
        self.assertEqual(len(updated["snapshot"]["nodes"]), 1)
        self.assertEqual(len(updated["snapshot"]["edges"]), 0)
        self.assertEqual([step["nodeId"] for step in updated["path"]["steps"]], [1])
        self.assertEqual(updated["snapshot"]["sourceMarkdown"], "第 1 题\n导入的第一题")
        self.assertEqual(updated["source"]["origin"], "ocr")
        self.assertTrue(updated["source"]["hasOriginalFile"])
        source = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/source",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.data, b"%PDF-empty-import")

    def test_direct_assignment_draft_delete_removes_assignment_snapshot_and_source(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Disposable direct assignment course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "Draft student", "studentNumber": "DRAFT-1"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())

        created_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments/direct",
            data={
                "title": "待删除草稿",
                "questions": json.dumps([{"order": 1, "question": "待删除题目", "referenceAnswer": "答案", "maxScore": 100}], ensure_ascii=False),
                "sourceOrigin": "ocr",
                "sourceText": "待删除题目",
                "source_file": (io.BytesIO(b"%PDF-delete-draft"), "draft.pdf"),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created_assignment.status_code, 201, created_assignment.get_json())
        assignment = created_assignment.get_json()["assignment"]
        assignment_id = assignment["id"]
        snapshot_id = assignment["snapshotId"]
        source_dir = api_v2._EDUCATION_ASSIGNMENT_SOURCE_ROOT / assignment_id
        work_dir = api_v2._EDUCATION_ROOT / "assignments" / assignment_id
        snapshot_dir = api_v2._EDUCATION_SNAPSHOT_ROOT / snapshot_id
        work_dir.mkdir(parents=True, exist_ok=True)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "checkpoint.json").write_text("{}", encoding="utf-8")
        (snapshot_dir / "artifact.json").write_text("{}", encoding="utf-8")
        self.assertTrue(source_dir.is_dir())

        student_delete = self.client.delete(
            f"/api/v2/edu/assignments/{assignment_id}",
            headers=self._headers(self.student),
        )
        self.assertIn(student_delete.status_code, (403, 404), student_delete.get_json())

        deleted = self.client.delete(
            f"/api/v2/edu/assignments/{assignment_id}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(deleted.get_json(), {"ok": True})

        missing = self.client.get(
            f"/api/v2/edu/assignments/{assignment_id}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(missing.status_code, 404, missing.get_json())
        assignments = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/assignments",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(assignments.status_code, 200, assignments.get_json())
        self.assertNotIn(assignment_id, [item["id"] for item in assignments.get_json()["assignments"]])

        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_assignments WHERE id = ?", (assignment_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_snapshots WHERE id = ?", (snapshot_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_assignment_sources WHERE assignment_id = ?", (assignment_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_assessment_nodes WHERE assignment_id = ?", (assignment_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_assessment_questions WHERE assignment_id = ?", (assignment_id,)).fetchone()[0], 0)
        self.assertFalse(source_dir.exists())
        self.assertFalse(work_dir.exists())
        self.assertFalse(snapshot_dir.exists())

        _graph_class, _graph_snapshot, graph_assignment = self._create_draft_assignment_with_assessments()
        graph_delete = self.client.delete(
            f"/api/v2/edu/assignments/{graph_assignment['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(graph_delete.status_code, 403, graph_delete.get_json())
        graph_still_exists = self.client.get(
            f"/api/v2/edu/assignments/{graph_assignment['id']}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(graph_still_exists.status_code, 200, graph_still_exists.get_json())

    def test_direct_assignment_input_edit_publish_student_flow_and_source_permissions(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Direct assignment course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "Direct student", "studentNumber": "D001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())

        questions = [
            {"order": 2, "question": "第二题", "kind": "proof", "focus": "边界", "maxScore": 60},
            {"order": 1, "question": "第一题", "kind": "calculation", "focus": "定义", "maxScore": 40},
        ]
        created_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments/direct",
            data={
                "title": "直接题目作业",
                "sourceOrigin": "ocr",
                "sourceText": "第 1 题\n第一题\n第 2 题\n第二题",
                "questions": json.dumps(questions, ensure_ascii=False),
                "source_file": (io.BytesIO(b"%PDF-direct-source"), "questions.pdf"),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created_assignment.status_code, 201, created_assignment.get_json())
        assignment = created_assignment.get_json()["assignment"]
        self.assertEqual(assignment["assignmentType"], "direct")
        self.assertEqual(assignment["directStructureVersion"], 1)
        self.assertEqual(assignment["snapshot"]["snapshotType"], "direct")
        self.assertEqual(assignment["source"]["origin"], "ocr")
        self.assertTrue(assignment["source"]["hasOriginalFile"])
        self.assertEqual(len(assignment["snapshot"]["nodes"]), 2)
        self.assertEqual(len(assignment["snapshot"]["edges"]), 1)
        self.assertEqual(len(assignment["path"]["steps"]), 2)
        self.assertEqual([item["questionCount"] for item in assignment["assessments"]], [1, 1])
        stable_node_ids = [step["nodeId"] for step in assignment["path"]["steps"]]
        graph_snapshots = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(graph_snapshots.status_code, 200, graph_snapshots.get_json())
        self.assertNotIn(assignment["snapshotId"], [item["id"] for item in graph_snapshots.get_json()["snapshots"]])

        source = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/source",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.data, b"%PDF-direct-source")
        self.assertEqual(
            self.client.get(
                f"/api/v2/edu/assignments/{assignment['id']}/source",
                headers=self._headers(self.student),
            ).status_code,
            403,
        )

        missing_reference = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(missing_reference.status_code, 409, missing_reference.get_json())
        self.assertEqual(missing_reference.get_json()["code"], "assessment_scoring_required")

        duplicate_order = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/direct-questions",
            json={"questions": [{"order": 1, "question": "甲"}, {"order": 1, "question": "乙"}]},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(duplicate_order.status_code, 400)

        updated = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/direct-questions",
            json={"questions": [
                {"nodeId": stable_node_ids[1], "order": 2, "question": "更新后的第二题", "kind": "proof", "focus": "证明", "referenceAnswer": "答案 2", "expectedPoints": ["点 2"], "maxScore": 60},
                {"nodeId": stable_node_ids[0], "order": 1, "question": "更新后的第一题", "kind": "calculation", "focus": "计算", "referenceAnswer": "答案 1", "expectedPoints": ["点 1"], "maxScore": 40},
            ]},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        updated_assignment = updated.get_json()["assignment"]
        updated_questions = [assessment["questions"][0] for assessment in updated_assignment["assessments"]]
        self.assertEqual([item["question"] for item in updated_questions], ["更新后的第一题", "更新后的第二题"])
        self.assertEqual([step["nodeId"] for step in updated_assignment["path"]["steps"]], [stable_node_ids[0], stable_node_ids[1]])
        self.assertEqual(sum(item["maxScore"] for item in updated_questions), 100)
        self.assertEqual(updated_questions[0]["focus"], "计算")

        inserted = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}/direct-questions",
            json={"questions": [
                {"nodeId": stable_node_ids[1], "order": 1, "question": "顺序后的第二题", "referenceAnswer": "答案 A", "expectedPoints": [], "maxScore": 1},
                {"nodeId": stable_node_ids[0], "order": 2, "question": "顺序后的第一题", "referenceAnswer": "答案 B", "expectedPoints": [], "maxScore": 1},
                {"order": 3, "question": "新增第三题", "referenceAnswer": "答案 C", "expectedPoints": [], "maxScore": 1},
            ]},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(inserted.status_code, 200, inserted.get_json())
        inserted_assignment = inserted.get_json()["assignment"]
        inserted_ids = [step["nodeId"] for step in inserted_assignment["path"]["steps"]]
        self.assertEqual(inserted_ids[:2], [stable_node_ids[1], stable_node_ids[0]])
        self.assertNotIn(inserted_ids[2], stable_node_ids)
        self.assertEqual(len(inserted_assignment["snapshot"]["nodes"]), 3)
        self.assertEqual(len(inserted_assignment["snapshot"]["edges"]), 2)
        self.assertEqual(sum(question["questions"][0]["maxScore"] for question in inserted_assignment["assessments"]), 100)
        self.assertTrue(all(question["questions"][0]["expectedPoints"] == [] for question in inserted_assignment["assessments"]))

        first_question = inserted_assignment["assessments"][0]["questions"][0]
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assessment_questions SET max_score = 10 WHERE id = ?",
                (first_question["id"],),
            )
            connection.commit()
        wrong_total = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(wrong_total.status_code, 409, wrong_total.get_json())
        self.assertEqual(wrong_total.get_json()["code"], "assessment_scoring_required")
        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assessment_questions SET max_score = ? WHERE id = ?",
                (first_question["maxScore"], first_question["id"]),
            )
            connection.commit()

        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        student_assignment = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_assignment.status_code, 200, student_assignment.get_json())
        student_payload = student_assignment.get_json()["assignment"]
        self.assertEqual(student_payload["assignmentType"], "direct")
        self.assertNotIn("source", student_payload)
        self.assertEqual(student_payload["snapshot"].get("sourceMarkdown"), "")
        self.assertNotIn("%PDF-direct-source", json.dumps(student_payload, ensure_ascii=False))

        for node_id in inserted_ids:
            started = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/assessments/{node_id}/attempts",
                headers=self._headers(self.student),
            )
            self.assertEqual(started.status_code, 201, started.get_json())
            attempt = started.get_json()["attempt"]
            self.assertEqual(len(attempt["questions"]), 1)
            completed = self.client.post(
                f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
                json={"answers": {question["id"]: "学生答案" for question in attempt["questions"]}},
                headers=self._headers(self.student),
            )
            self.assertEqual(completed.status_code, 200, completed.get_json())
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        submission = submitted.get_json()["submission"]

        captured_tasks = {}
        def fake_grading_tasks(**kwargs):
            captured_tasks.update(kwargs["tasks"])
            return {
                question_id: {
                    "suggestedScore": round(task["maxScore"] * 0.8, 1),
                    "maxScore": task["maxScore"],
                    "rationale": "未配置评分点时仍依据参考答案评分",
                    "correctPoints": [],
                    "issues": [],
                    "studentFeedback": "请继续完善解题过程。",
                    "confidence": 0.8,
                    "needsTeacherReview": False,
                }
                for question_id, task in kwargs["tasks"].items()
            }

        with patch.object(api_v2, "_education_ai_tasks", side_effect=fake_grading_tasks) as grading_tasks:
            evaluated = self.client.post(
                f"/api/v2/edu/submissions/{submission['id']}/evaluate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(evaluated.status_code, 200, evaluated.get_json())
        grading_tasks.assert_called_once()
        self.assertTrue(captured_tasks)
        self.assertTrue(all(task["expectedPoints"] == [] for task in captured_tasks.values()))

        evaluated_submission = evaluated.get_json()["submission"]
        publish_before_save = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/grades/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(publish_before_save.status_code, 409, publish_before_save.get_json())
        self.assertEqual(publish_before_save.get_json()["code"], "grading_incomplete")

        saved = self.client.patch(
            f"/api/v2/edu/submissions/{submission['id']}/grade",
            json={
                "grades": [{"questionId": grade["questionId"], "teacherScore": grade["maxScore"], "teacherFeedback": "已保存草稿"} for grade in evaluated_submission["grades"]],
                "teacherSummary": "草稿评语",
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        saved_submission = saved.get_json()["submission"]
        self.assertEqual(saved_submission["status"], "review_draft")
        self.assertEqual(saved_submission["teacherTotal"], 100)

        revised = self.client.patch(
            f"/api/v2/edu/submissions/{submission['id']}/grade",
            json={
                "grades": [{"questionId": grade["questionId"], "teacherScore": 0 if index == 0 else grade["maxScore"], "teacherFeedback": "已修改草稿"} for index, grade in enumerate(saved_submission["grades"])],
                "teacherSummary": "修改后的草稿评语",
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(revised.status_code, 200, revised.get_json())
        self.assertEqual(revised.get_json()["submission"]["status"], "review_draft")

        grading_overview = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/grading-overview",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(grading_overview.status_code, 200, grading_overview.get_json())
        self.assertTrue(grading_overview.get_json()["canPublish"])
        released = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/grades/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(released.status_code, 200, released.get_json())

    def test_bulk_ai_grading_uses_one_identity_bound_task_per_student(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Bulk grading course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        second_student = self._register("bulk-second@example.com")
        for token, name, number in (
            (self.student, "Bulk first", "B001"),
            (second_student, "Bulk second", "B002"),
        ):
            joined = self.client.post(
                f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
                json={"inviteCode": class_data["inviteCode"], "studentName": name, "studentNumber": number},
                headers=self._headers(token),
            )
            self.assertEqual(joined.status_code, 200, joined.get_json())

        created_assignment = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments/direct",
            data={
                "title": "Bulk direct grading",
                "questions": json.dumps([
                    {"order": 1, "question": "第一题", "referenceAnswer": "答案一", "maxScore": 50},
                    {"order": 2, "question": "第二题", "referenceAnswer": "答案二", "maxScore": 50},
                ], ensure_ascii=False),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created_assignment.status_code, 201, created_assignment.get_json())
        assignment = created_assignment.get_json()["assignment"]
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        submissions = []
        for token, answer_prefix in ((self.student, "first-student"), (second_student, "second-student")):
            for assessment in assignment["assessments"]:
                started = self.client.post(
                    f"/api/v2/edu/assignments/{assignment['id']}/assessments/{assessment['nodeId']}/attempts",
                    headers=self._headers(token),
                )
                self.assertEqual(started.status_code, 201, started.get_json())
                attempt = started.get_json()["attempt"]
                completed = self.client.post(
                    f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
                    json={"answers": {question["id"]: f"{answer_prefix}:{question['id']}" for question in attempt["questions"]}},
                    headers=self._headers(token),
                )
                self.assertEqual(completed.status_code, 200, completed.get_json())
            submitted = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/submissions",
                headers=self._headers(token),
            )
            self.assertEqual(submitted.status_code, 201, submitted.get_json())
            submissions.append(submitted.get_json()["submission"])

        expected_identity_by_submission = {
            submissions[0]["id"]: (submissions[0]["userId"], "first-student"),
            submissions[1]["id"]: (submissions[1]["userId"], "second-student"),
        }

        forbidden = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions/evaluate",
            headers=self._headers(self.student),
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.get_json())

        captured = {}
        def fake_bulk_grading(**kwargs):
            captured.update(kwargs)
            output = {}
            for task_key, task in reversed(list(kwargs["tasks"].items())):
                answers = {question["studentAnswer"].split(":", 1)[0] for question in task["questions"]}
                expected_user_id, expected_prefix = expected_identity_by_submission[task["submissionId"]]
                self.assertEqual(task["studentUserId"], expected_user_id)
                self.assertEqual(answers, {expected_prefix})
                score = 11.0 if expected_prefix == "first-student" else 22.0
                grades = [{
                    "questionId": question["questionId"],
                    "suggestedScore": score,
                    "maxScore": question["maxScore"],
                    "rationale": f"{answers.pop()} grading",
                    "correctPoints": [],
                    "issues": [],
                    "studentFeedback": "feedback",
                    "confidence": 0.8,
                    "needsTeacherReview": False,
                } for question in reversed(task["questions"])]
                output[task_key] = {
                    "submissionId": task["submissionId"],
                    "studentUserId": task["studentUserId"],
                    "grades": grades,
                }
            return output

        with patch.object(api_v2, "_education_ai_tasks", side_effect=fake_bulk_grading) as runner:
            evaluated = self.client.post(
                f"/api/v2/edu/assignments/{assignment['id']}/submissions/evaluate",
                headers=self._headers(self.teacher),
            )
        self.assertEqual(evaluated.status_code, 200, evaluated.get_json())
        self.assertEqual(evaluated.get_json()["evaluatedCount"], 2)
        self.assertEqual(evaluated.get_json()["failedCount"], 0)
        runner.assert_called_once()
        self.assertEqual(captured["task_kind"], "grade_submission")
        self.assertEqual(len(captured["tasks"]), 2)
        for task_key, task in captured["tasks"].items():
            self.assertIn(task["submissionId"], task_key)
            self.assertIn(str(task["studentUserId"]), task_key)

        expected_totals = {
            submissions[0]["id"]: 22.0,
            submissions[1]["id"]: 44.0,
        }
        for submission_id, expected_total in expected_totals.items():
            detail = self.client.get(
                f"/api/v2/edu/submissions/{submission_id}",
                headers=self._headers(self.teacher),
            )
            self.assertEqual(detail.status_code, 200, detail.get_json())
            payload = detail.get_json()["submission"]
            self.assertEqual(payload["aiStatus"], "ready")
            self.assertEqual(payload["aiSuggestedTotal"], expected_total)
            self.assertTrue(all(grade["aiSuggestedScore"] == expected_total / 2 for grade in payload["grades"]))

    def test_class_statistics_matrix_and_xlsx_export(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        with api_v2.connect_database() as connection:
            connection.execute("UPDATE education_assignments SET assignment_type = 'direct' WHERE id = ?", (assignment["id"],))
            connection.commit()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self._complete_all_assignment_assessments(assignment)
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        submission_id = submitted.get_json()["submission"]["id"]

        single = self.client.get(
            f"/api/v2/edu/assignments/{assignment['id']}/statistics",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(single.status_code, 200, single.get_json())
        self.assertEqual(single.get_json()["assignmentId"], assignment["id"])
        self.assertNotIn("classTitle", single.get_json())

        student_forbidden = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/statistics",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_forbidden.status_code, 403, student_forbidden.get_json())

        pending = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/statistics",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(pending.status_code, 200, pending.get_json())
        pending_payload = pending.get_json()
        self.assertEqual(len(pending_payload["assignments"]), 1)
        self.assertEqual(pending_payload["assignments"][0]["questionCount"], 12)
        self.assertEqual(pending_payload["students"][0]["assignments"][assignment["id"]]["status"], "submitted")
        self.assertIsNone(pending_payload["students"][0]["averageScore"])
        student_id = pending_payload["students"][0]["userId"]

        pending_student_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/students/{student_id}/statistics/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(pending_student_export.status_code, 200, pending_student_export.get_json())
        with zipfile.ZipFile(io.BytesIO(pending_student_export.data)) as workbook:
            student_sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("Assessment student", student_sheet)
            self.assertIn("A001", student_sheet)
            self.assertIn("待批改", student_sheet)

        with api_v2.connect_database() as connection:
            connection.execute("UPDATE education_assignment_submissions SET status = 'review_draft' WHERE id = ?", (submission_id,))
            connection.commit()
        review_draft = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/statistics",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(review_draft.status_code, 200, review_draft.get_json())
        self.assertEqual(review_draft.get_json()["students"][0]["assignments"][assignment["id"]]["status"], "review_draft")

        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_assignment_submissions SET status = 'finalized', teacher_total = 85 WHERE id = ?",
                (submission_id,),
            )
            connection.commit()
        finalized = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/statistics",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(finalized.status_code, 200, finalized.get_json())
        payload = finalized.get_json()
        self.assertEqual(payload["overview"]["finalizedStudents"], 1)
        self.assertEqual(payload["students"][0]["averageScore"], 85.0)
        self.assertEqual(payload["students"][0]["rank"], 1)
        self.assertEqual(payload["students"][0]["assignments"][assignment["id"]]["score"], 85.0)

        student_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/students/{student_id}/statistics/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(student_export.status_code, 200, student_export.get_json())
        self.assertIn("A001", student_export.headers.get("Content-Disposition", ""))
        with zipfile.ZipFile(io.BytesIO(student_export.data)) as workbook:
            self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("学生成绩", workbook_xml)
            student_sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("Assessment task", student_sheet)
            self.assertIn("85.0", student_sheet)

        student_forbidden_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/students/{student_id}/statistics/export",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_forbidden_export.status_code, 403, student_forbidden_export.get_json())

        missing_student_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/students/999999/statistics/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(missing_student_export.status_code, 404, missing_student_export.get_json())

        exported = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/statistics/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(exported.status_code, 200, exported.get_json())
        content_disposition = exported.headers.get("Content-Disposition", "")
        self.assertIn(".xlsx", content_disposition)
        self.assertRegex(content_disposition, r"\d{4}-\d{2}-\d{2}")
        with zipfile.ZipFile(io.BytesIO(exported.data)) as workbook:
            self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
            self.assertIn("xl/worksheets/sheet2.xml", workbook.namelist())
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("班级成绩总表", workbook_xml)
            self.assertIn("作业汇总", workbook_xml)
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("Assessment task", sheet)
            self.assertIn("85.0", sheet)
            assignment_sheet = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")
            self.assertIn("Assessment task", assignment_sheet)

        with api_v2.connect_database() as connection:
            connection.execute(
                "UPDATE education_memberships SET removed_at = CURRENT_TIMESTAMP WHERE class_id = ? AND user_id = ?",
                (class_data["id"], student_id),
            )
            connection.commit()
        removed_student_export = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/students/{student_id}/statistics/export",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(removed_student_export.status_code, 404, removed_student_export.get_json())


    def test_game_settings_summary_and_permissions(self):
        class_data, _snapshot, _assignment = self._create_published_assignment()

        classic_student = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/game-summary",
            headers=self._headers(self.student),
        )
        self.assertEqual(classic_student.status_code, 200, classic_student.get_json())
        classic_payload = classic_student.get_json()
        self.assertFalse(classic_payload["enabled"])
        self.assertIsNone(classic_payload["profile"])
        self.assertEqual(classic_payload["achievements"], [])
        self.assertEqual(classic_payload["settings"]["studentExperience"], "classic")
        self.assertEqual(classic_payload["settings"]["weeklyXpGoal"], 60)
        self.assertEqual(classic_payload["settings"]["timezone"], "Asia/Shanghai")

        teacher_view = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/game-summary",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_view.status_code, 200, teacher_view.get_json())
        self.assertIsNone(teacher_view.get_json()["profile"])
        self.assertEqual(teacher_view.get_json()["achievements"], [])

        student_update = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.student),
        )
        self.assertEqual(student_update.status_code, 403, student_update.get_json())

        invalid_goal = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"weeklyXpGoal": True},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(invalid_goal.status_code, 400, invalid_goal.get_json())
        invalid_timezone = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"timezone": "UTC"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(invalid_timezone.status_code, 400, invalid_timezone.get_json())

        teacher_update = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map", "weeklyXpGoal": 75},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(teacher_update.status_code, 200, teacher_update.get_json())
        self.assertEqual(teacher_update.get_json()["settings"]["weeklyXpGoal"], 75)

        mapped_student = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/game-summary",
            headers=self._headers(self.student),
        )
        self.assertEqual(mapped_student.status_code, 200, mapped_student.get_json())
        mapped_payload = mapped_student.get_json()
        self.assertTrue(mapped_payload["enabled"])
        self.assertEqual(mapped_payload["profile"]["level"], 1)
        self.assertEqual(mapped_payload["profile"]["totalXp"], 0)
        self.assertEqual(len(mapped_payload["achievements"]), 6)
        self.assertTrue(all(not item["unlocked"] for item in mapped_payload["achievements"]))

    def test_game_settings_fall_back_when_iana_timezone_data_is_missing(self):
        class_data, _snapshot, _assignment = self._create_published_assignment()

        with patch.object(
            education_gamification,
            "ZoneInfo",
            side_effect=education_gamification.ZoneInfoNotFoundError("tzdata unavailable"),
        ):
            enabled = self.client.patch(
                f"/api/v2/edu/classes/{class_data['id']}/game-settings",
                json={"studentExperience": "map"},
                headers=self._headers(self.teacher),
            )
            self.assertEqual(enabled.status_code, 200, enabled.get_json())

            summary = self.client.get(
                f"/api/v2/edu/classes/{class_data['id']}/game-summary",
                headers=self._headers(self.student),
            )
            self.assertEqual(summary.status_code, 200, summary.get_json())
            self.assertTrue(summary.get_json()["enabled"])
            self.assertEqual(summary.get_json()["settings"]["timezone"], "Asia/Shanghai")

    def test_map_assessment_reward_is_idempotent_and_classic_is_silent(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        enabled = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(enabled.status_code, 200, enabled.get_json())
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        started = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/assessments/1/attempts",
            headers=self._headers(self.student),
        )
        self.assertEqual(started.status_code, 201, started.get_json())
        attempt = started.get_json()["attempt"]
        answers = {question["id"]: "学生答案" for question in attempt["questions"]}
        completed = self.client.post(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
            json={"answers": answers},
            headers=self._headers(self.student),
        )
        self.assertEqual(completed.status_code, 200, completed.get_json())
        self.assertEqual(completed.get_json()["reward"]["xpDelta"], 10)
        self.assertEqual(completed.get_json()["reward"]["totalXp"], 10)
        self.assertIn("first_step", [item["key"] for item in completed.get_json()["reward"]["unlockedAchievements"]])

        repeated = self.client.post(
            f"/api/v2/edu/assessment-attempts/{attempt['id']}/complete",
            json={"answers": answers},
            headers=self._headers(self.student),
        )
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertEqual(repeated.get_json()["reward"]["xpDelta"], 0)
        self.assertEqual(repeated.get_json()["reward"]["totalXp"], 10)

        with api_v2.connect_database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM education_game_events WHERE class_id = ?",
                    (class_data["id"],),
                ).fetchone()[0],
                1,
            )

    def test_map_graph_submission_rewards_and_unlocks_route_achievements(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        enabled = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(enabled.status_code, 200, enabled.get_json())
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        due = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}",
            json={"dueAt": "2099-01-01T00:00:00Z"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(due.status_code, 200, due.get_json())

        attempts = self._complete_all_assignment_assessments(assignment)
        self.assertEqual(len(attempts), 3)
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        reward = submitted.get_json()["reward"]
        self.assertEqual(reward["xpDelta"], 30)
        self.assertEqual(reward["totalXp"], 60)
        self.assertEqual(
            {item["key"] for item in reward["unlockedAchievements"]},
            {"pathfinder", "on_time", "full_route"},
        )

        repeated = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        self.assertEqual(repeated.get_json()["reward"]["xpDelta"], 0)
        self.assertEqual(repeated.get_json()["reward"]["totalXp"], 60)

        with api_v2.connect_database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM education_game_events WHERE class_id = ?",
                    (class_data["id"],),
                ).fetchone()[0],
                5,
            )

    def test_direct_submission_unlocks_challenge_achievement(self):
        created = self.client.post(
            "/api/v2/edu/classes",
            json={"title": "Direct game course"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        class_data = created.get_json()["class"]
        joined = self.client.post(
            f"/api/v2/edu/classes/{class_data['inviteCode']}/join",
            json={"inviteCode": class_data["inviteCode"], "studentName": "Game student", "studentNumber": "G001"},
            headers=self._headers(self.student),
        )
        self.assertEqual(joined.status_code, 200, joined.get_json())
        direct = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/assignments/direct",
            data={
                "title": "Direct game challenge",
                "questions": json.dumps([
                    {"order": 1, "question": "第一题", "referenceAnswer": "答案1", "maxScore": 50},
                    {"order": 2, "question": "第二题", "referenceAnswer": "答案2", "maxScore": 50},
                ], ensure_ascii=False),
            },
            content_type="multipart/form-data",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(direct.status_code, 201, direct.get_json())
        assignment = direct.get_json()["assignment"]
        enabled = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(enabled.status_code, 200, enabled.get_json())
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())

        self._complete_all_assignment_assessments(assignment)
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        reward = submitted.get_json()["reward"]
        self.assertEqual(reward["xpDelta"], 20)
        self.assertEqual(reward["totalXp"], 40)
        keys = {item["key"] for item in reward["unlockedAchievements"]}
        self.assertIn("challenge_clear", keys)
        self.assertNotIn("full_route", keys)

    def test_classic_history_backfills_once_when_map_is_enabled(self):
        class_data, _snapshot, assignment = self._create_draft_assignment_with_assessments()
        published = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/publish",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        due = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}",
            json={"dueAt": "2099-01-01T00:00:00Z"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(due.status_code, 200, due.get_json())
        self._complete_all_assignment_assessments(assignment)
        submitted = self.client.post(
            f"/api/v2/edu/assignments/{assignment['id']}/submissions",
            headers=self._headers(self.student),
        )
        self.assertEqual(submitted.status_code, 201, submitted.get_json())
        self.assertIsNone(submitted.get_json()["reward"])
        cleared_due = self.client.patch(
            f"/api/v2/edu/assignments/{assignment['id']}",
            json={"dueAt": None},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(cleared_due.status_code, 200, cleared_due.get_json())

        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_game_events").fetchone()[0], 0)

        enabled = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(enabled.status_code, 200, enabled.get_json())
        summary = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/game-summary",
            headers=self._headers(self.student),
        )
        self.assertEqual(summary.status_code, 200, summary.get_json())
        self.assertEqual(summary.get_json()["profile"]["totalXp"], 60)
        self.assertIn("pathfinder", [item["key"] for item in summary.get_json()["achievements"] if item["unlocked"]])

        repeated = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/game-settings",
            json={"studentExperience": "map"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(repeated.status_code, 200, repeated.get_json())
        with api_v2.connect_database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM education_game_events").fetchone()[0], 5)

    def test_game_profile_uses_class_timezone_and_three_week_streak(self):
        class_data, _snapshot, assignment = self._create_published_assignment()
        with api_v2.connect_database() as connection:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", ("student@example.com",)
            ).fetchone()[0]
            events = []
            event_index = 0
            for week_start in ("2026-08-17T16:00:00Z", "2026-08-24T16:00:00Z", "2026-08-31T16:00:00Z"):
                base = datetime.fromisoformat(week_start.replace("Z", "+00:00"))
                for offset in range(6):
                    occurred = (base.replace(minute=offset)).isoformat().replace("+00:00", "Z")
                    events.append((
                        f"profile-event-{event_index}",
                        class_data["id"],
                        user_id,
                        assignment["id"],
                        "assessment_complete",
                        f"profile-event-key-{event_index}",
                        10,
                        occurred,
                        "{}",
                        occurred,
                    ))
                    event_index += 1
            connection.executemany(
                """INSERT INTO education_game_events
                     (id, class_id, user_id, assignment_id, event_type, event_key,
                      xp_delta, occurred_at, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                events,
            )
            connection.commit()

        profile_db = api_v2.connect_database()
        profile = education_gamification.build_game_profile(
            profile_db,
            class_id=class_data["id"],
            user_id=user_id,
            weekly_goal=60,
            timezone_name="Asia/Shanghai",
            now_utc=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(profile["totalXp"], 180)
        self.assertEqual(profile["level"], 2)
        self.assertEqual(profile["levelXp"], 80)
        self.assertEqual(profile["weeklyXp"], 60)
        self.assertEqual(profile["activeDaysThisWeek"], 1)
        self.assertEqual(profile["consecutiveGoalWeeks"], 3)
        profile_db.close()

    def test_course_graph_order_is_class_scoped_and_new_graphs_append(self):
        class_data, first_snapshot, _assignment = self._create_published_assignment()
        second = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "chapter-two.tex", "nodes": NODES, "edges": EDGES, "sourceGraphId": "chapter-two"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(second.status_code, 201, second.get_json())
        second_id = second.get_json()["snapshot"]["id"]

        initial = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(initial.status_code, 200, initial.get_json())
        initial_snapshots = initial.get_json()["snapshots"]
        initial_ids = [item["id"] for item in initial_snapshots]
        self.assertEqual(initial_ids, [first_snapshot["id"], second_id])
        self.assertEqual([item["courseOrder"] for item in initial_snapshots], [0, 1])

        reordered = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/course-graph-order",
            json={"graphIds": [second_id, first_snapshot["id"]], "knownGraphIds": initial_ids},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(reordered.status_code, 200, reordered.get_json())
        self.assertEqual([item["id"] for item in reordered.get_json()["snapshots"]], [second_id, first_snapshot["id"]])
        self.assertEqual([item["courseOrder"] for item in reordered.get_json()["snapshots"]], [0, 1])

        student_view = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.student),
        )
        self.assertEqual(student_view.status_code, 200, student_view.get_json())
        self.assertEqual([item["id"] for item in student_view.get_json()["snapshots"]], [second_id, first_snapshot["id"]])

        third = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "chapter-three.tex", "nodes": NODES, "edges": EDGES, "sourceGraphId": "chapter-three"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(third.status_code, 201, third.get_json())
        third_id = third.get_json()["snapshot"]["id"]
        appended = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        self.assertEqual([item["id"] for item in appended.get_json()["snapshots"]], [second_id, first_snapshot["id"], third_id])
        self.assertEqual([item["courseOrder"] for item in appended.get_json()["snapshots"]], [0, 1, 2])

    def test_course_graph_order_rejects_invalid_access_and_detects_a_stale_collection(self):
        class_data, first_snapshot, _assignment = self._create_published_assignment()
        second = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "chapter-two.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(second.status_code, 201, second.get_json())
        second_id = second.get_json()["snapshot"]["id"]
        ids = [first_snapshot["id"], second_id]
        endpoint = f"/api/v2/edu/classes/{class_data['id']}/course-graph-order"

        student = self.client.patch(endpoint, json={"graphIds": ids}, headers=self._headers(self.student))
        self.assertEqual(student.status_code, 403, student.get_json())
        outsider = self.client.patch(endpoint, json={"graphIds": ids}, headers=self._headers(self.outsider))
        self.assertEqual(outsider.status_code, 404, outsider.get_json())
        missing = self.client.patch(endpoint, json={"graphIds": [first_snapshot["id"]]}, headers=self._headers(self.teacher))
        self.assertEqual(missing.status_code, 400, missing.get_json())

        with api_v2.connect_database() as connection:
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO education_snapshots
                     (id, class_id, snapshot_type, source_graph_id, filename, node_count, edge_count,
                      source_markdown, latex_macros_json, source_pdf_json, created_by, created_at)
                   VALUES (?, ?, 'direct', NULL, ?, 1, 0, '', '{}', NULL, ?, ?)""",
                ("direct-order-test", class_data["id"], "direct-order-test", teacher_id, datetime.utcnow().isoformat()),
            )
        invalid = self.client.patch(
            endpoint,
            json={"graphIds": [first_snapshot["id"], "direct-order-test"]},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(invalid.status_code, 400, invalid.get_json())

        third = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "chapter-three.tex", "nodes": NODES, "edges": EDGES},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(third.status_code, 201, third.get_json())
        current_ids = [first_snapshot["id"], second_id, third.get_json()["snapshot"]["id"]]
        stale = self.client.patch(
            endpoint,
            json={"graphIds": current_ids, "knownGraphIds": ids},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(stale.status_code, 409, stale.get_json())
        self.assertEqual(stale.get_json()["code"], "course_graph_order_conflict")

    def test_course_graph_order_groups_source_graph_revisions_and_cleans_up_after_delete(self):
        class_data, first_snapshot, _assignment = self._create_published_assignment()
        second = self.client.post(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            json={"filename": "same-source.tex", "nodes": NODES, "edges": EDGES, "sourceGraphId": "chapter-source"},
            headers=self._headers(self.teacher),
        )
        self.assertEqual(second.status_code, 201, second.get_json())
        source_snapshot_id = second.get_json()["snapshot"]["id"]
        with api_v2.connect_database() as connection:
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (self.TEACHER_EMAIL,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO education_snapshots
                     (id, class_id, snapshot_type, source_graph_id, filename, node_count, edge_count,
                      source_markdown, latex_macros_json, source_pdf_json, created_by, created_at)
                   VALUES (?, ?, 'graph', ?, ?, 3, 2, '', '{}', NULL, ?, ?)""",
                ("older-source-revision", class_data["id"], "chapter-source", "same-source-old.tex", teacher_id, "2026-01-01T00:00:00"),
            )
        api_v2.persist_graph("older-source-revision", "education_snapshot", NODES, EDGES)
        listed = self.client.get(
            f"/api/v2/edu/classes/{class_data['id']}/snapshots",
            headers=self._headers(self.teacher),
        )
        listed_snapshots = listed.get_json()["snapshots"]
        source_rows = [item for item in listed_snapshots if item.get("sourceGraphId") == "chapter-source"]
        self.assertEqual(len(source_rows), 2)
        self.assertEqual({item["courseOrder"] for item in source_rows}, {1})

        reordered = self.client.patch(
            f"/api/v2/edu/classes/{class_data['id']}/course-graph-order",
            json={
                "graphIds": ["older-source-revision", first_snapshot["id"]],
                "knownGraphIds": [first_snapshot["id"], "older-source-revision"],
            },
            headers=self._headers(self.teacher),
        )
        self.assertEqual(reordered.status_code, 200, reordered.get_json())
        refreshed_source_rows = [item for item in reordered.get_json()["snapshots"] if item.get("sourceGraphId") == "chapter-source"]
        self.assertEqual({item["courseOrder"] for item in refreshed_source_rows}, {0})

        deleted = self.client.delete(
            f"/api/v2/edu/snapshots/{source_snapshot_id}",
            headers=self._headers(self.teacher),
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        with api_v2.connect_database() as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM education_course_graph_order WHERE class_id = ? AND graph_key = ?",
                (class_data["id"], "source:chapter-source"),
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_alembic_class_schema_has_game_defaults(self):
        with api_v2.connect_database() as connection:
            columns = {
                row["column_name"]: row["column_default"]
                for row in connection.execute(
                    """SELECT column_name, column_default
                         FROM information_schema.columns
                        WHERE table_schema = DATABASE() AND table_name = 'education_classes'"""
                ).fetchall()
            }
            tables = {
                row["table_name"]
                for row in connection.execute(
                    """SELECT table_name FROM information_schema.tables
                         WHERE table_schema = DATABASE()
                           AND table_name IN ('education_game_events', 'education_student_achievements', 'education_course_graph_order')"""
                ).fetchall()
            }
        self.assertEqual(columns["student_experience"], "classic")
        self.assertEqual(int(columns["weekly_xp_goal"]), 60)
        self.assertEqual(columns["timezone"], "Asia/Shanghai")
        self.assertEqual(tables, {"education_game_events", "education_student_achievements", "education_course_graph_order"})


if __name__ == "__main__":
    unittest.main()
