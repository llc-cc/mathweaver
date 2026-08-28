import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.stages.extract_statements.stage import repair_missing_labels_from_problem_dict


DEFAULT_BOOK_DIR = ROOT / "test_output" / "高等代数-丘维声-上册-第三章节选" / "_stage_cache"


def main():
    unsplit = json.loads((DEFAULT_BOOK_DIR / "unsplit_statement_dict.json").read_text())
    problem = json.loads((DEFAULT_BOOK_DIR / "problem_dict.json").read_text())
    repaired, count = repair_missing_labels_from_problem_dict(unsplit, problem)

    print(f"repaired_count={count}")
    for key in ["7", "8", "9", "10", "12", "13"]:
        if key not in repaired:
            continue
        block = repaired[key]["pos1"]
        print(
            f"key={key} "
            f"node_type={block.get('node_type')} "
            f"label={repr(block.get('label'))} "
            f"content_head={repr((block.get('content') or '')[:60])}"
        )


if __name__ == "__main__":
    main()
