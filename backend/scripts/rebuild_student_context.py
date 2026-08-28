"""Reclassify pending course proof interactions with the canonical batch runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import api_v2
from education_service import create_education_context
from student_context import rebuild_pending_student_context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    api_v2._init_db()
    config = api_v2._education_llm_config()
    if not config:
        print(json.dumps({"error": "education AI is not configured"}, ensure_ascii=False))
        return 2
    checkpoint_dir = api_v2._DATA_ROOT / "education" / "context_rebuild" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    with api_v2.app.app_context():
        db = api_v2._get_db()
        context = create_education_context(api_v2._DATA_ROOT, config)
        result = rebuild_pending_student_context(
            db,
            context=context,
            checkpoint_dir=checkpoint_dir,
            limit=args.limit,
        )
        db.commit()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["unresolvedInteractionIds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
