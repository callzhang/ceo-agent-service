"""Explicit supported Store publications with paired schema inverses."""

from __future__ import annotations

import ast
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.run_schema_release import RunSchemaRelease
    from app.task_schema_release import TaskSchemaRelease
    from app.workbench_schema_release import WorkbenchSchemaRelease
    from app.action_schema_release import ActionSchemaRelease

VERSION_KEY = "store_schema_version"

def _target_store_version(root: Path) -> str | None:
    path = root / "app" / "store.py"
    if not path.exists():
        return None
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "STORE_SCHEMA_VERSION"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if type(version) is str:
                return version
    raise ValueError("target Store schema version is not a string constant")


def _migrate_store(root: Path, path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; "
            "from app.store import AutoReplyStore; AutoReplyStore(Path(sys.argv[1]))",
            str(path),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=True,
        capture_output=True,
        timeout=300,
    )








def prepare_store_schema_release(
    root: Path, path: Path
) -> RunSchemaRelease | TaskSchemaRelease | WorkbenchSchemaRelease | ActionSchemaRelease | None:
    root, path = root.resolve(), path.resolve()
    target = _target_store_version(root)
    if target is None or not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute(
            "select 1 from sqlite_master where type='table' and name='service_state'"
        ).fetchone():
            return None
        version = db.execute(
            "select value,updated_at from service_state where key=?", (VERSION_KEY,)
        ).fetchone()
        if version is None or version[0] == target:
            return None
        from app.run_schema_release import OLD_RUN_VERSION, RUN_VERSION, prepare_run_schema_release

        if (version[0], target) == (OLD_RUN_VERSION, RUN_VERSION):
            return prepare_run_schema_release(root, path, version)
        from app.task_schema_release import OLD_TASK_VERSION, TASK_VERSION, prepare_task_schema_release
        if (version[0], target) == (OLD_TASK_VERSION, TASK_VERSION):
            return prepare_task_schema_release(root, path, version)
        from app.workbench_storage import SOURCE_VERSION, WORKBENCH_VERSION
        if (version[0], target) == (SOURCE_VERSION, WORKBENCH_VERSION):
            from app.workbench_schema_release import prepare_workbench_schema_release
            return prepare_workbench_schema_release(root, path, version)
        from app.action_schema_release import SOURCE_ACTION_VERSION, ACTION_VERSION, prepare_action_schema_release
        if (version[0], target) == (SOURCE_ACTION_VERSION, ACTION_VERSION):
            return prepare_action_schema_release(root, path, version)
        from app.action_receipt_schema_release import (
            SOURCE_ACTION_RECEIPT_VERSION, ACTION_RECEIPT_VERSION,
            prepare_action_receipt_schema_release,
        )
        if (version[0], target) == (SOURCE_ACTION_RECEIPT_VERSION, ACTION_RECEIPT_VERSION):
            return prepare_action_receipt_schema_release(root, path, version)
        raise ValueError(
            "Store schema release needs a verified inverse for this transition"
        )


def prepare_database_schema_release(root: Path, path: Path):
    from app import email_schema_release

    store = prepare_store_schema_release(root, path)
    email = email_schema_release.prepare_email_schema_release(root, path)
    if store is not None and email is not None:
        raise ValueError(
            "simultaneous Store/Email changes need a verified paired inverse"
        )
    return store if store is not None else email
