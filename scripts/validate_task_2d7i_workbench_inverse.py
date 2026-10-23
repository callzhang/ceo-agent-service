#!/usr/bin/env python3
"""Run the explicit Workbench attachment inverse against a pinned predecessor."""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def _predecessor(root: Path, code: str, *, payload: dict | None = None) -> dict:
    env = dict(os.environ)
    env["TASK_2D7I_PAYLOAD"] = json.dumps(payload or {})
    run = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); " + code, str(root)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
    )
    if run.returncode:
        raise RuntimeError(f"predecessor cold start failed: {run.stderr}")
    return json.loads(run.stdout)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(db, source_kind: str, public_id: str) -> int:
    row = db.execute(
        "select target_id from task_run_identity_map where source_kind=? and public_key=?",
        (source_kind, public_id),
    ).fetchone()
    if row is None:
        raise AssertionError(f"missing mapped {source_kind} identity: {public_id}")
    return int(row[0])


def _native_store(path: Path):
    from app.workbench.store import WorkbenchStore

    store = object.__new__(WorkbenchStore)
    store.path = path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30_000
    store._read_snapshot_connection = ContextVar("task2d7i_snapshot", default=None)
    store._reused_connection = ContextVar("task2d7i_reused", default=None)
    return store


def _cold_start(predecessor: Path, root: Path, payload: dict) -> dict:
    code = r'''
import hashlib,json,os,sqlite3,sys
from pathlib import Path
from app.workbench.store import WorkbenchStore,WORKBENCH_ATTACHMENT_SQL
def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
root=Path(os.environ["TASK_2D7I_ROOT"]); db=root/"store.sqlite3"
payload=json.loads(os.environ["TASK_2D7I_PAYLOAD"])
before=sqlite3.connect(db).execute("select count(*) from ("+WORKBENCH_ATTACHMENT_SQL+")").fetchone()[0]
store=WorkbenchStore(db)
tasks={task.title:task for task in store.list_tasks(include_archived=True)}
observed={}
for request_id,expected in payload["attachments"].items():
    parent=tasks[expected["parent_title"]]
    listed={a.id for a in store.list_attachments(parent.id)}
    with sqlite3.connect(db) as check: matches=[row for row in check.execute("select * from ("+WORKBENCH_ATTACHMENT_SQL+") where task_id=?",(str(parent.id),)).fetchall() if row[2]==request_id and row[0] in listed]
    if len(matches)!=1: raise AssertionError(f"predecessor lost attachment {request_id}")
    row=matches[0]; path=Path(row[7])
    if not path.is_file() or _sha(path)!=expected["sha256"]: raise AssertionError("predecessor attachment bytes/hash mismatch")
    observed[request_id]={"id":row[0],"parent_id":row[1],"path":str(path),"sha256":_sha(path),"size_bytes":row[5],"filename":row[3],"media_type":row[4]}
control=tasks["Control parent"]
control_rows=store.list_attachments(control.id)
controls=[a for a in control_rows if a.id==payload["control_attachment_id"]]
if len(controls)!=1: raise AssertionError("predecessor changed the captured control attachment")
c=controls[0]
with sqlite3.connect(db) as check: crow=check.execute("select * from ("+WORKBENCH_ATTACHMENT_SQL+") where id=?",(c.id,)).fetchone(); cp=Path(crow[7])
if _sha(cp)!=payload["control_sha256"]: raise AssertionError("predecessor changed control attachment bytes")
written=None
if payload.get("write_old_attachment"):
    a=store.save_attachment(control.id,client_request_id=payload["old_write_request"],filename="old-write.bin",media_type="application/octet-stream",content=b"old-code harmless write")
    with sqlite3.connect(db) as check: oldrow=check.execute("select * from ("+WORKBENCH_ATTACHMENT_SQL+") where id=?",(a.id,)).fetchone(); old_path=Path(oldrow[7])
    written={"id":a.id,"client_request_id":payload["old_write_request"],"path":str(old_path),"sha256":_sha(old_path)}
with sqlite3.connect(db) as check: after=check.execute("select count(*) from ("+WORKBENCH_ATTACHMENT_SQL+")").fetchone()[0]
print(json.dumps({"constructor_file":WorkbenchStore.__init__.__code__.co_filename,"reconcile_file":WorkbenchStore._reconcile_attachments.__code__.co_filename,"rows_before":before,"rows_after":after,"attachments":observed,"control":{"id":c.id,"path":str(cp),"sha256":_sha(cp),"size_bytes":c.size_bytes},"old_write":written}))
'''
    env = dict(os.environ)
    env["TASK_2D7I_ROOT"] = str(root)
    env["TASK_2D7I_PAYLOAD"] = json.dumps(payload)
    run = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); " + code, str(predecessor)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
    )
    if run.returncode:
        raise RuntimeError(f"predecessor cold start failed: {run.stderr}")
    return json.loads(run.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-root", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    predecessor = args.predecessor_root.resolve()
    work_dir = args.work_dir.resolve()
    if not predecessor.is_dir() or not work_dir.is_dir() or any(work_dir.iterdir()):
        raise SystemExit("predecessor root must exist and work-dir must be an existing empty directory")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    module_sha = hashlib.sha256((predecessor / "app/workbench/store.py").read_bytes()).hexdigest()
    source_setup = r'''
import hashlib,json,os
from pathlib import Path
from uuid import uuid4
from app.workbench.store import WorkbenchStore
root=Path(os.environ["TASK_2D7I_ROOT"]); root.mkdir(parents=True,exist_ok=True)
store=WorkbenchStore(root/"store.sqlite3")
empty=store.create_task(title="Mapped empty parent",runtime_kind="codex")
control=store.create_task(title="Control parent",runtime_kind="codex")
content=b"captured control bytes"
a=store.save_attachment(control.id,client_request_id=str(uuid4()),filename="control.bin",media_type="application/octet-stream",content=content)
print(json.dumps({"empty_parent_id":empty.id,"control_parent_id":control.id,"control_attachment_id":a.id,"control_sha256":hashlib.sha256(content).hexdigest(),"control_path":str(root/"workbench"/"attachments"/control.id/a.id),"empty_parent_title":empty.title}))
'''
    env = dict(os.environ)
    env["TASK_2D7I_ROOT"] = str(work_dir)
    setup = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); " + source_setup, str(predecessor)],
        cwd=work_dir, env=env, text=True, capture_output=True,
    )
    if setup.returncode:
        raise RuntimeError(f"predecessor source setup failed: {setup.stderr}")
    source = json.loads(setup.stdout)

    from app.task_entity_inverse_files import (
        capture_workbench_attachment_files, finalize_workbench_attachment_forward_files,
        finalize_workbench_attachment_inverse_files, stage_workbench_attachment_forward_files,
        stage_workbench_attachment_inverse_files,
    )
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    import sqlite3

    db_path = work_dir / "store.sqlite3"
    managed_root = work_dir / "workbench" / "attachments"
    reports = []
    next_old_write = str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        db.commit()
    with sqlite3.connect(db_path) as db:
        empty_id = _identity(db, "workbench_tasks", source["empty_parent_id"])
    store = _native_store(db_path)
    empty_parent = store.get_task(str(empty_id))
    if empty_parent is None:
        raise AssertionError("current Workbench Store cannot read the mapped empty parent")
    new_parent = store.create_task(title="New native parent", runtime_kind="codex")
    new_parent_id = int(new_parent.id)
    for cycle in (1, 2):
        store = _native_store(db_path)
        empty_parent = store.get_task(str(empty_id))
        new_parent = store.get_task(str(new_parent_id))
        if empty_parent is None or new_parent is None:
            raise AssertionError("current Workbench Store cannot read an inverse parent")
        additions = []
        for parent, parent_title in ((empty_parent, "Mapped empty parent"), (new_parent, "New native parent")):
            content = f"cycle-{cycle}:{parent_title}".encode()
            request = str(uuid.uuid4())
            attachment = store.save_attachment(
                parent.id, client_request_id=request, filename=f"cycle-{cycle}.bin",
                media_type="application/octet-stream", content=content,
            )
            additions.append((request, parent_title, content, attachment))
        with store._attachment_lock(create_workbench=False):
            with store._connect() as db:
                snapshot = capture_workbench_attachment_files(db, managed_root)
                db.execute("pragma foreign_keys=off")
                db.execute("begin immediate")
                inverse = restore_task_entities(db, metadata, forward)
                plan = stage_workbench_attachment_inverse_files(db, managed_root, snapshot, inverse)
                db.commit()
                finalize_workbench_attachment_inverse_files(
                    db, plan, inverse["inverse_identity"], managed_root,
                )
        handoff = json.loads(json.dumps(inverse["inverse_identity"]))
        expected = {
            request: {"parent_title": title, "sha256": hashlib.sha256(content).hexdigest()}
            for request, title, content, _ in additions
        }
        previous = _cold_start(predecessor, work_dir, {
            "attachments": expected,
            "control_sha256": source["control_sha256"],
            "control_attachment_id": source["control_attachment_id"],
            "write_old_attachment": cycle == 1,
            "old_write_request": next_old_write,
        })
        pinned_source = str(predecessor / "app/workbench/store.py")
        if previous["constructor_file"] != pinned_source or previous["reconcile_file"] != pinned_source:
            raise AssertionError("predecessor modules did not resolve from selected root")
        if len(previous["attachments"]) != 2 or previous["rows_after"] < previous["rows_before"]:
            raise AssertionError("predecessor attachment readback was incomplete")
        if cycle == 1 and previous["old_write"] is None:
            raise AssertionError("old Workbench API harmless write did not complete")
        store = _native_store(db_path)
        with store._attachment_lock(create_workbench=False):
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                db.execute("pragma foreign_keys=off")
                db.execute("begin immediate")
                source_metadata = capture_task_entities(db)
                forward_plan = stage_workbench_attachment_forward_files(db, managed_root, handoff)
                forward = migrate_task_entities(
                    db, source_metadata, source_context=inverse["source_context"],
                    inverse_identity=handoff,
                )
                db.commit()
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                finalize_workbench_attachment_forward_files(db, forward_plan, handoff, managed_root)
        reports.append({"cycle": cycle, "inverse_file_count": len(plan["attachments"]),
                        "predecessor": previous,
                        "old_write_request": next_old_write if cycle == 1 else None})

    print(json.dumps({"predecessor_sha256": module_sha, "source": source, "cycles": reports}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
