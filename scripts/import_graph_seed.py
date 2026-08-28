"""将通过预检的正式图谱数据包幂等导入主数据库。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.graph_seed_service import (  # noqa: E402
    GraphSeedValidationError,
    import_graph_dataset,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a validated MathWeaver graph dataset."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--teacher-email", required=True)
    parser.add_argument("--class-title", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = import_graph_dataset(
            args.dataset, args.teacher_email, args.class_title
        )
    except GraphSeedValidationError:
        print(
            json.dumps({"ok": False, "code": "dataset_validation_failed"}),
            file=sys.stderr,
        )
        return 2
    except LookupError:
        print(
            json.dumps({"ok": False, "code": "teacher_not_found"}),
            file=sys.stderr,
        )
        return 3
    except Exception:
        # CLI 不回显驱动异常或连接串，详细诊断只进入受控服务日志。
        print(json.dumps({"ok": False, "code": "graph_import_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
