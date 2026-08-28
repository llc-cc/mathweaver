from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from matrix_flow.parser import parse_matrix_owner


def _compact_cell(value: Any) -> str:
    return "".join(str(value or "").split()).replace("−", "-").replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")


def audit(root: Path) -> dict[str, Any]:
    cases = {item["id"]: item for item in json.loads((root / "cases.json").read_text(encoding="utf-8"))}
    job_paths = sorted((root / "ocr_results").glob("*.job.json"))
    summary: dict[str, Any] = {
        "case_count": len(job_paths),
        "parsed_case_count": 0,
        "unavailable_case_count": 0,
        "mounted_case_count": 0,
        "flow_count": 0,
        "state_count": 0,
        "strict_candidate_count": 0,
        "tolerant_candidate_count": 0,
        "rejected_candidate_count": 0,
        "dimension_match_count": 0,
        "exact_cell_match_count": 0,
        "forbidden_mount_count": 0,
        "failures": [],
    }
    for job_path in job_paths:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        case_id = str(job.get("id") or job_path.name.removesuffix(".job.json"))
        result_path = Path((job.get("job") or {}).get("result_path") or "")
        if not result_path.is_file():
            summary["unavailable_case_count"] += 1
            continue
        source = result_path.read_text(encoding="utf-8")
        try:
            parsed = parse_matrix_owner(
                {"statement": source, "proof": ""},
                owner={"global_id": case_id, "source_block_key": case_id},
                source_origin="ocr",
            )
        except Exception as exc:
            summary["failures"].append({"id": case_id, "reason": "parser_crash", "error": str(exc)})
            continue
        summary["parsed_case_count"] += 1
        summary["strict_candidate_count"] += parsed["counts"]["strict"]
        summary["tolerant_candidate_count"] += parsed["counts"]["tolerant"]
        summary["rejected_candidate_count"] += parsed["counts"]["rejected"]
        flows = parsed["flows"]
        summary["flow_count"] += len(flows)
        states = [node for flow in flows for node in flow.get("nodes") or []]
        summary["state_count"] += len(states)
        if flows:
            summary["mounted_case_count"] += 1
        invalid = False
        for node in states:
            span = node.get("source_span") or {}
            start = span.get("start")
            end = span.get("end")
            cells = node.get("cells") or []
            if not isinstance(start, int) or not isinstance(end, int) or source[start:end] != node.get("latex"):
                summary["failures"].append({"id": case_id, "reason": "inexact_state_span", "state_id": node.get("id")})
                invalid = True
            width = len(cells[0]) if cells else 0
            if not cells or not width or any(len(row) != width or any(not str(cell).strip() for cell in row) for row in cells):
                summary["failures"].append({"id": case_id, "reason": "non_rectangular_state", "state_id": node.get("id")})
                invalid = True
        lowered = source.lower()
        forbidden = "\\binom" in source or "<table" in lowered or "![" in source
        if forbidden and flows:
            summary["forbidden_mount_count"] += 1
            summary["failures"].append({"id": case_id, "reason": "forbidden_source_mounted"})
            invalid = True
        case = cases.get(case_id) or {}
        if states and not invalid:
            node = states[0]
            if node.get("rows") == case.get("rows") and node.get("columns") == case.get("cols"):
                summary["dimension_match_count"] += 1
            expected_cells = [[_compact_cell(cell) for cell in row] for row in case.get("cells") or []]
            actual_cells = [[_compact_cell(cell) for cell in row] for row in node.get("cells") or []]
            if expected_cells and actual_cells == expected_cells:
                summary["exact_cell_match_count"] += 1
    summary["ok"] = (
        not summary["failures"]
        and summary["parsed_case_count"] + summary["unavailable_case_count"] == summary["case_count"]
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit MatrixFlow production parsing over the OCR matrix benchmark.")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=BACKEND_ROOT / "test_output" / "ocr_matrix_benchmark" / "2026-08-12-dev-3.4.4",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = audit(args.root.resolve())
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
