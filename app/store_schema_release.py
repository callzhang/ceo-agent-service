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
        from app.action_postfix_schema_release import (
            SOURCE_ACTION_POSTFIX_VERSION, ACTION_POSTFIX_VERSION,
            prepare_action_postfix_schema_release,
        )
        if (version[0], target) == (SOURCE_ACTION_POSTFIX_VERSION, ACTION_POSTFIX_VERSION):
            return prepare_action_postfix_schema_release(root, path, version)
        from app.action_delivery_schema_release import (
            SOURCE_ACTION_DELIVERY_VERSION, ACTION_DELIVERY_VERSION,
            prepare_action_delivery_schema_release,
        )
        if (version[0], target) == (SOURCE_ACTION_DELIVERY_VERSION, ACTION_DELIVERY_VERSION):
            return prepare_action_delivery_schema_release(root, path, version)
        from app.action_reply_schema_release import (
            SOURCE_ACTION_REPLY_VERSION, ACTION_REPLY_VERSION,
            prepare_action_reply_schema_release,
        )
        if (version[0], target) == (SOURCE_ACTION_REPLY_VERSION, ACTION_REPLY_VERSION):
            return prepare_action_reply_schema_release(root, path, version)
        raise ValueError(
            "Store schema release needs a verified inverse for this transition"
        )


def prepare_database_schema_release(root: Path, path: Path, *, recovery_source_path: Path | None = None):
    from app import email_schema_release

    from app.action_email_schema_release import (
        TARGET_STORE_VERSION, TARGET_EMAIL_VERSION, require_current_email_pair,
        prepare_action_email_schema_release, _migrate_pair,
    )
    target = (_target_store_version(root), email_schema_release._target_schema_version(root) if (root / "app" / "email_store.py").exists() else None)
    from app.specialized_action_schema_release import (
        TARGET_STORE_VERSION as SPECIALIZED_STORE_VERSION, EMAIL_VERSION as SPECIALIZED_EMAIL_VERSION,
        prepare_specialized_action_schema_release,
    )
    if target == (SPECIALIZED_STORE_VERSION, SPECIALIZED_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (SPECIALIZED_STORE_VERSION,):
            require_current_email_pair(path, store_version=SPECIALIZED_STORE_VERSION,
                email_version=SPECIALIZED_EMAIL_VERSION, selection_references=True, result_references=True,
                metadata_owner=True, execution_owner=True, sent_result_references=True, shared_lifecycle=True, specialized_claims=True)
            return None
        return prepare_specialized_action_schema_release(root, path)
    from app.shared_action_schema_release import (
        TARGET_STORE_VERSION as SHARED_STORE_VERSION, EMAIL_VERSION as SHARED_EMAIL_VERSION,
        prepare_shared_action_schema_release,
    )
    if target == (SHARED_STORE_VERSION, SHARED_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (SHARED_STORE_VERSION,):
            require_current_email_pair(path, store_version=SHARED_STORE_VERSION,
                email_version=SHARED_EMAIL_VERSION, selection_references=True, result_references=True,
                metadata_owner=True, execution_owner=True, sent_result_references=True, shared_lifecycle=True)
            return None
        return prepare_shared_action_schema_release(root, path)
    from app.sent_result_schema_release import (
        TARGET_STORE_VERSION as SENT_STORE_VERSION, EMAIL_VERSION as SENT_EMAIL_VERSION,
        prepare_sent_result_schema_release,
    )
    if target == (SENT_STORE_VERSION, SENT_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (SENT_STORE_VERSION,):
            require_current_email_pair(path, store_version=SENT_STORE_VERSION,
                email_version=SENT_EMAIL_VERSION, selection_references=True, result_references=True,
                metadata_owner=True, execution_owner=True, sent_result_references=True)
            return None
        return prepare_sent_result_schema_release(root, path)
    from app.execution_owner_schema_release import (
        TARGET_STORE_VERSION as EXECUTION_STORE_VERSION, EMAIL_VERSION as EXECUTION_EMAIL_VERSION,
        prepare_execution_owner_schema_release,
    )
    if target == (EXECUTION_STORE_VERSION, EXECUTION_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (EXECUTION_STORE_VERSION,):
            require_current_email_pair(path, store_version=EXECUTION_STORE_VERSION,
                email_version=EXECUTION_EMAIL_VERSION, selection_references=True, result_references=True,
                metadata_owner=True, execution_owner=True)
            return None
        return prepare_execution_owner_schema_release(root, path)
    from app.email_metadata_schema_release import (
        TARGET_STORE_VERSION as METADATA_STORE_VERSION, TARGET_EMAIL_VERSION as METADATA_EMAIL_VERSION,
        prepare_email_metadata_schema_release,
    )
    if target == (METADATA_STORE_VERSION, METADATA_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (METADATA_STORE_VERSION,):
            require_current_email_pair(path, store_version=METADATA_STORE_VERSION,
                email_version=METADATA_EMAIL_VERSION, selection_references=True, result_references=True, metadata_owner=True)
            return None
        return prepare_email_metadata_schema_release(root, path)
    from app.result_schema_release import (
        TARGET_STORE_VERSION as RESULT_STORE_VERSION, EMAIL_VERSION as RESULT_EMAIL_VERSION,
        prepare_result_schema_release,
    )
    if target == (RESULT_STORE_VERSION, RESULT_EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (RESULT_STORE_VERSION,):
            require_current_email_pair(path, store_version=RESULT_STORE_VERSION,
                email_version=RESULT_EMAIL_VERSION, selection_references=True, result_references=True)
            return None
        return prepare_result_schema_release(root, path)
    from app.selection_schema_release import (
        TARGET_STORE_VERSION as SELECTION_STORE_VERSION, EMAIL_VERSION,
        prepare_selection_schema_release,
    )
    if target == (SELECTION_STORE_VERSION, EMAIL_VERSION) and path.exists():
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) "
                                "where key=?", (VERSION_KEY,)).fetchone() if "state" in tables else None
        if "state" not in tables or marker == (SELECTION_STORE_VERSION,):
            require_current_email_pair(path, store_version=SELECTION_STORE_VERSION,
                                       email_version=EMAIL_VERSION, selection_references=True)
            return None
        return prepare_selection_schema_release(root, path)
    if target == (TARGET_STORE_VERSION, TARGET_EMAIL_VERSION) and path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute("select value from main.service_state where key=?", (VERSION_KEY,)).fetchone() if "service_state" in tables else None
            email = db.execute("select coalesce(max(version),0) from main.email_schema_migrations").fetchone()[0] if "email_schema_migrations" in tables else None
        if marker == ("2026-10-13.5",) and email == 51:
            paired = prepare_action_email_schema_release(root, path, target_store_version=target[0], target_email_version=target[1], recovery_source_path=recovery_source_path)
            paired.migrate = _migrate_pair
            return paired
        require_current_email_pair(path, store_version=target[0], email_version=target[1])
        return None

    store = prepare_store_schema_release(root, path)
    email = email_schema_release.prepare_email_schema_release(root, path)
    if store is not None and email is not None:
        raise ValueError(
            "simultaneous Store/Email changes need a verified paired inverse"
        )
    return store if store is not None else email
