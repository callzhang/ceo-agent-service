#!/usr/bin/env python3
"""Apply the explicit Task/Run inverse from its paired release handoff."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    args = parser.parse_args()
    from app.task_run_entity_schema_release import TaskRunEntitySchemaRelease

    release = TaskRunEntitySchemaRelease.from_inverse_handoff(args.database, args.handoff)
    release.rollback()
    print(f"Task/Run inverse restored predecessor schema in {args.database.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
