"""Regression checks for preserving canonical TeX across the graph API path."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import api_v2
from api_v2 import _legacy_display_nodes, _normalize_nodes, _project_display_result, _tex_integrity_issues
from matrix_flow.parser import parse_matrix_flows
from pipeline.common.node import adjust, normalize_latex_backslashes
from pipeline.stages.extract_statements.stage import extract_nonempty_blocks
from pipeline.stages.generate_titles.stage import finalize_natural_node_list


MATRIX = r"\begin{pmatrix}5&-3\\2&4\end{pmatrix}"


class LatexDataIntegrityTests(unittest.TestCase):
    def test_pipeline_helpers_preserve_matrix_rows_and_line_boundaries(self):
        text = f"\n矩阵：${MATRIX}$\n\n\n证明。\n"
        self.assertEqual(normalize_latex_backslashes(text), text)

        adjusted = adjust({
            "node_type": "定义",
            "title": {"chinese": text, "english": text},
            "content": text,
            "conditions": [{"text": text}],
            "conclusions": [{"text": text}],
        })
        self.assertEqual(adjusted["title"]["chinese"], text)
        self.assertEqual(adjusted["content"], text)
        self.assertEqual(adjusted["conditions"][0]["text"], text)
        self.assertEqual(adjusted["conclusions"][0]["text"], text)

        extracted = extract_nonempty_blocks({"0": {"content": text, "proof": text}})
        self.assertEqual(extracted[0]["pos1"]["content"], text)
        self.assertEqual(extracted[0]["pos1"]["proof"], text)

    def test_title_finalization_preserves_matrix_rows(self):
        node = {
            "node_type": "example",
            "title": {"chinese": MATRIX, "english": MATRIX},
            "content": MATRIX,
            "conditions": [{"text": MATRIX}],
            "conclusions": [{"text": MATRIX}],
        }
        finalized = finalize_natural_node_list({"0": {"pos1": node}})[0]
        self.assertEqual(finalized["title"]["chinese"], MATRIX)
        self.assertEqual(finalized["conditions"][0]["text"], MATRIX)
        self.assertEqual(finalized["conclusions"][0]["text"], MATRIX)

    def test_api_normalization_does_not_apply_display_rewrites(self):
        text = f"矩阵：${MATRIX}$"
        node = {
            "id": 7,
            "global_id": "node-7",
            "node_type": "例子",
            "title_zh": "矩阵乘法",
            "content": text,
            "proof": text,
        }
        normalized = _normalize_nodes([node])[0]
        self.assertEqual(normalized["content"], text)
        self.assertEqual(normalized["proof"], text)

    def test_history_projection_repairs_only_a_uniquely_retained_source(self):
        canonical = f"矩阵：${MATRIX}$"
        damaged = canonical.replace(r"\\2", r"\2")
        self.assertIn("matrix_row_separator", _tex_integrity_issues(damaged))

        stored = [{"content": damaged, "source_statement": canonical}]
        projected = _legacy_display_nodes(stored, "")
        self.assertEqual(projected[0]["content"], canonical)
        self.assertEqual(stored[0]["content"], damaged)

    def test_projection_uses_source_span_and_recovers_nested_display_fields(self):
        source_matrix = r"\begin{smallmatrix}1&2\\3&7\end{smallmatrix}"
        source = f"prefix {source_matrix} suffix"
        damaged = r"\begin{pmatrix}1&2\3&7\end{pmatrix}"
        node = {
            "source_span": {"start": 0, "end": len(source)},
            "source_text": damaged,
            "source_statement": damaged,
            "title_zh": damaged,
            "content": damaged,
            "subject": [damaged],
            "conditions": [{"text": damaged}],
            "conclusions": [{"text": damaged}],
        }
        projected = _project_display_result({"nodes": [node]}, source)["nodes"][0]
        expected = r"\begin{pmatrix}1&2\\3&7\end{pmatrix}"
        self.assertEqual(projected["title_zh"], expected)
        self.assertEqual(projected["content"], expected)
        self.assertEqual(projected["source_text"], expected)
        self.assertEqual(projected["subject"], [expected])
        self.assertEqual(projected["conditions"][0]["text"], expected)
        self.assertEqual(projected["conclusions"][0]["text"], expected)
        self.assertEqual(node["title_zh"], damaged)

    def test_projection_recovers_rows_before_letters_commands_negative_values_and_spacing(self):
        cases = [
            (
                r"\begin{pmatrix}a_{11}&a_{12}&\cdots&a_{1n}\\a_{21}&a_{22}&\cdots&a_{2n}\\\vdots&\vdots&\ddots&\vdots\\a_{m1}&a_{m2}&\cdots&a_{mn}\end{pmatrix}",
                r"\begin{pmatrix}a_{11}&a_{12}&\cdots&a_{1n}\a_{21}&a_{22}&\cdots&a_{2n}\vdots&\vdots&\ddots&\vdots\a_{m1}&a_{m2}&\cdots&a_{mn}\end{pmatrix}",
            ),
            (
                r"\begin{pmatrix}1&2\\-3&4\end{pmatrix}",
                r"\begin{pmatrix}1&2\-3&4\end{pmatrix}",
            ),
            (
                r"\begin{array}{cc}1&2\\[1em]3&4\end{array}",
                r"\begin{array}{cc}1&2\[1em]3&4\end{array}",
            ),
        ]
        for canonical, damaged in cases:
            projected = _legacy_display_nodes(
                [{"content": damaged, "source_statement": canonical}],
                "",
            )[0]
            self.assertEqual(projected["content"], canonical)

    def test_normalize_nodes_source_statement_projection_repairs_remark_original_form(self):
        canonical = (
            r"设 $m,n$ 为正整数。\[\begin{pmatrix}"
            r"a_{11}&a_{12}&\cdots&a_{1n}\\"
            r"a_{21}&a_{22}&\cdots&a_{2n}\\"
            r"\vdots&\vdots&\ddots&\vdots\\"
            r"a_{m1}&a_{m2}&\cdots&a_{mn}"
            r"\end{pmatrix}\]"
        )
        damaged = canonical.replace("\\\\", "\\")
        normalized = _normalize_nodes([{
            "content": canonical,
            "remark": {"original_form": damaged},
            "source_span": {"start": 0, "end": len(canonical)},
        }])[0]
        self.assertEqual(normalized["source_statement"], damaged)
        projected = _legacy_display_nodes([normalized], canonical)[0]
        self.assertEqual(projected["source_statement"], canonical)

    def test_projection_leaves_ambiguous_or_intact_matrices_unchanged(self):
        damaged = r"\begin{pmatrix}1&2\3&7\end{pmatrix}"
        intact = r"\begin{pmatrix}1&2\\3&7\end{pmatrix}"
        ambiguous_source = (
            r"\begin{smallmatrix}1&2\\3&7\end{smallmatrix}"
            r"\begin{smallmatrix}1 & 2\\3 & 7\end{smallmatrix}"
        )
        projected = _legacy_display_nodes(
            [{"content": damaged, "source_statement": ambiguous_source}],
            "",
        )[0]
        self.assertEqual(projected["content"], damaged)
        projected = _legacy_display_nodes(
            [{"content": intact, "source_statement": MATRIX}],
            "",
        )[0]
        self.assertEqual(projected["content"], intact)

    def test_job_result_projects_without_mutating_the_job_store(self):
        source = r"\begin{smallmatrix}1&2\\3&7\end{smallmatrix}"
        damaged = r"\begin{pmatrix}1&2\3&7\end{pmatrix}"
        job_id = "latex-display-projection-test"
        original_jobs = api_v2._jobs
        api_v2._jobs = {
            job_id: {
                "status": "done",
                "source_markdown": source,
                "result": {"nodes": [{"content": damaged, "source_span": {"start": 0, "end": len(source)}}]},
            }
        }
        try:
            with api_v2.app.test_client() as client:
                response = client.get(f"/api/v2/jobs/{job_id}/result")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["nodes"][0]["content"], MATRIX.replace("5&-3", "1&2").replace("2&4", "3&7"))
            self.assertEqual(api_v2._jobs[job_id]["result"]["nodes"][0]["content"], damaged)
        finally:
            api_v2._jobs = original_jobs

    def test_new_matrix_flow_records_exact_owner_excerpt(self):
        source = (
            r"\begin{pmatrix}1&0\\0&1\end{pmatrix}"
            r"\to"
            r"\begin{pmatrix}0&1\\1&0\end{pmatrix}"
        )
        flows = parse_matrix_flows(source, owner={"global_id": "n1"})
        self.assertEqual(len(flows), 1)
        owner = flows[0]["owner"]
        span = owner["source_span"]
        self.assertEqual(owner["source_excerpt"], source[span["start"]:span["end"]])


if __name__ == "__main__":
    unittest.main()
