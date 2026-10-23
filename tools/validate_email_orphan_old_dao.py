#!/usr/bin/env python3
"""Run the offline Email orphan fixture through an explicitly selected old DAO."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def _old_dao_reader(predecessor: Path):
    program = r'''import json, sys
from contextvars import ContextVar
from pathlib import Path
from app.store import AutoReplyStore
store = AutoReplyStore.__new__(AutoReplyStore)
store.path = Path(sys.argv[1])
store.busy_timeout_seconds = 10
store.busy_timeout_milliseconds = 10000
store._read_snapshot_connection = ContextVar("read_snapshot", default=None)
store._reused_connection = ContextVar("reused_connection", default=None)
rows = store.list_runtime_operation_attempts("email_classification", sys.argv[2])
parent_runnable = store.runtime_operation_parent_is_runnable(
    "email_classification", sys.argv[2])
print(json.dumps({"attempts": [row.model_dump(mode="json") for row in rows],
                  "parent_runnable": parent_runnable}, sort_keys=True))
'''

    def read(path: Path, operation_key: str) -> dict:
        env = {**os.environ, "PYTHONPATH": str(predecessor)}
        result = subprocess.run(
            [sys.executable, "-c", program, str(path), operation_key],
            cwd=predecessor, env=env, check=True, text=True, capture_output=True,
        )
        return json.loads(result.stdout)

    return read


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-root", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    predecessor = args.predecessor_root.resolve(strict=True)
    if not (predecessor / "app/store.py").is_file():
        parser.error("--predecessor-root must contain app/store.py")
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=False)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.test_run_entity_schema import _closed_offline_email_task_attempt_roundtrip

    _closed_offline_email_task_attempt_roundtrip(
        work_dir, predecessor_reader=_old_dao_reader(predecessor), preserve_database=True,
    )
    print(json.dumps({
        "result": "passed",
        "predecessor_root": str(predecessor),
        "database": str(work_dir / "task2d6-offline-inverse.sqlite3"),
        "old_dao_reads": 2,
        "attempts_per_read": 2,
        "parent_runnable": False,
        "database_preserved": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
