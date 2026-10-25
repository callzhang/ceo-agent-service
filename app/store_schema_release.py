"""Explicit supported Store publications with paired schema inverses."""

from __future__ import annotations

import ast
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

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








def prepare_store_schema_release(root: Path, path: Path) -> None:
    root, path = root.resolve(), path.resolve()
    target = _target_store_version(root)
    if target is None or not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute(
            "select name from main.sqlite_schema where type='table'")}
        if "state" not in tables:
            return None
        from app.state_definitions import state_projection
        version = db.execute(
            f"select value,updated_at from ({state_projection('service_state')}) where key=?",
            (VERSION_KEY,),
        ).fetchone()
        if version is None or version[0] == target:
            return None
        raise ValueError(
            "Store schema release needs a verified inverse for this transition"
        )


def prepare_database_schema_release(root: Path, path: Path, *, recovery_source_path: Path | None = None):
    from app import email_schema_release

    target = (_target_store_version(root), email_schema_release._target_schema_version(root) if (root / "app" / "email_store.py").exists() else None)
    from app.current_schema_manifest import STORE_VERSION as CURRENT_STORE_VERSION, EMAIL_VERSION as CURRENT_EMAIL_VERSION, require_current_pair
    if target == (CURRENT_STORE_VERSION, CURRENT_EMAIL_VERSION) and path.exists():
        from app.action_entity_schema_release import (
            SOURCE_STORE_VERSION as ACTION_ENTITY_SOURCE_VERSION,
            ActionEntitySchemaRelease,
            prepare_action_entity_release,
        )
        from app.state_definitions import state_projection
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            tables = {row[0] for row in db.execute("select name from main.sqlite_schema where type='table'")}
            marker = db.execute(f"select value from ({state_projection('service_state')}) where key=?",
                                (VERSION_KEY,)).fetchone() if "state" in tables else None
            email_version = (db.execute(
                "select max(version) from main.email_schema_migrations"
            ).fetchone()[0] if "email_schema_migrations" in tables else None)
        if not tables:
            require_current_pair(path)
            return None
        if marker == (CURRENT_STORE_VERSION,):
            from app.email_schema_release import (
                email_historical_candidate_release_selected,
                prepare_email_schema_release,
            )
            if email_historical_candidate_release_selected(
                store_marker=marker[0], current_store_marker=CURRENT_STORE_VERSION,
                email_version=email_version, target_email_version=CURRENT_EMAIL_VERSION,
            ):
                return prepare_email_schema_release(root, path)
            require_current_pair(path)
            return None
        if marker == (ACTION_ENTITY_SOURCE_VERSION,):
            return ActionEntitySchemaRelease(root, path, prepare_action_entity_release(path))
        raise ValueError("unsupported current Action source schema; explicit paired release required")
    prepare_store_schema_release(root, path)
    return email_schema_release.prepare_email_schema_release(root, path)
