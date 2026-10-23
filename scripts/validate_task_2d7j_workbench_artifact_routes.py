#!/usr/bin/env python3
"""Prove Workbench artifact UUID routes across native inverse/reforward cycles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def _run(root: Path, script: str, env: dict[str, str]) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); " + script,
            str(root),
        ],
        cwd=root,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"isolated route proof failed: {result.stderr}")
    return json.loads(result.stdout)


def _route_script() -> str:
    return r"""
import json,os
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.workbench.api import register_workbench_routes
from app.workbench.executor import WorkbenchExecutor
from app.workbench.runtime import RuntimeRegistry
from app.workbench.store import WorkbenchStore
root=Path(os.environ["TASK_2D7J_ROOT"]); db=root/"store.sqlite3"
store=WorkbenchStore(db)
task_id=os.environ["TASK_2D7J_TASK"]; turn_id=os.environ["TASK_2D7J_TURN"]
artifact_id=os.environ["TASK_2D7J_ARTIFACT"]
executor=object.__new__(WorkbenchExecutor); executor.workspace=root
app=FastAPI()
register_workbench_routes(app,store,executor,RuntimeRegistry(),root,
    mutation_guard=lambda request: None,artifact_roots=(root,))
with TestClient(app) as client:
    response=client.get(f"/api/workbench/tasks/{task_id}/turns/{turn_id}/artifacts/{artifact_id}/download")
if response.status_code != 200: raise AssertionError(f"source download route {response.status_code}: {response.text}")
print(json.dumps({"status":response.status_code,"bytes_hex":response.content.hex(),
    "sha256":__import__("hashlib").sha256(response.content).hexdigest(),
    "store_file":WorkbenchStore.__init__.__code__.co_filename,
    "route_file":register_workbench_routes.__code__.co_filename}))
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    predecessor, root = args.predecessor_root.resolve(), args.output_root.resolve()
    if not predecessor.is_dir() or not root.is_dir() or any(root.iterdir()):
        raise SystemExit(
            "predecessor must exist and output-root must be an existing empty directory"
        )
    current = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(current))
    old_store_file = predecessor / "app/workbench/store.py"
    old_api_file = predecessor / "app/workbench/api.py"
    if not old_store_file.is_file() or not old_api_file.is_file():
        raise SystemExit("selected predecessor is missing Workbench source files")
    hashes = {
        "store.py": hashlib.sha256(old_store_file.read_bytes()).hexdigest(),
        "api.py": hashlib.sha256(old_api_file.read_bytes()).hexdigest(),
    }

    # Genuine predecessor constructor, task/turn/artifact writers, and HTTP download route.
    setup = r"""
import hashlib,json,os
from pathlib import Path
from uuid import uuid4
from app.workbench.store import WorkbenchStore
root=Path(os.environ["TASK_2D7J_ROOT"]); root.mkdir(parents=True,exist_ok=True)
content=b"genuine workbench artifact route proof\x00bytes"
store=WorkbenchStore(root/"store.sqlite3")
task=store.create_task(title="Artifact route proof",runtime_kind="codex")
turn=store.create_turn(task.id,user_text="create an artifact",client_request_id=str(uuid4()))
running=store.claim_next_turn(owner="route-proof",now="2026-10-23T12:00:00+00:00")
if running is None or running.id != turn.id: raise AssertionError("predecessor could not claim source Turn")
path=root/"workbench"/"outputs"/"proof.bin"; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
artifact,event=store.append_artifact_event(turn.id,sequence=2,label="proof.bin",path=str(path),media_type="application/octet-stream",owner="route-proof",now="2026-10-23T12:00:01+00:00")
print(json.dumps({"task":task.id,"turn":turn.id,"artifact":artifact.id,"path":str(path),"bytes_hex":content.hex(),"sha256":hashlib.sha256(content).hexdigest(),"event_payload":event.payload}))
"""
    source = _run(predecessor, setup, {"TASK_2D7J_ROOT": str(root)})
    env = {
        "TASK_2D7J_ROOT": str(root),
        "TASK_2D7J_TASK": source["task"],
        "TASK_2D7J_TURN": source["turn"],
        "TASK_2D7J_ARTIFACT": source["artifact"],
    }
    before = _run(predecessor, _route_script(), env)
    if (
        before["bytes_hex"] != source["bytes_hex"]
        or before["store_file"] != str(old_store_file)
        or before["route_file"] != str(old_api_file)
    ):
        raise AssertionError(
            "pre-migration request did not use pinned UUID source route and exact bytes"
        )

    import sqlite3
    from app.task_entity_schema import (
        capture_task_entities,
        migrate_task_entities,
        restore_task_entities,
    )
    from app.run_entity_schema import (
        capture_run_entities,
        migrate_run_entities,
        restore_run_entities,
        run_handoff_context,
        run_handoff_digest,
    )
    from app.workbench.store import WorkbenchStore
    from contextvars import ContextVar

    db_path = root / "store.sqlite3"

    def run_handoff(metadata: dict) -> dict:
        owners = (
            "runs",
            "runtime_attempts",
            "run_failures",
            "schedule_occurrences",
            "workbench_events",
            "codex_session_search_index",
            "email_classifier_runtime_samples",
            "setup_wizard_events",
        )
        context = run_handoff_context(metadata)
        edges = []
        parents = []
        orphans = []
        highwaters = {owner: None for owner in owners}
        return {
            "version": 3,
            "source_context": context,
            "identity_edges": edges,
            "runtime_attempt_parent_edges": parents,
            "runtime_attempt_orphan_workload_edges": orphans,
            "owner_highwaters": highwaters,
            "digest": run_handoff_digest(context, edges, parents, orphans, highwaters),
        }

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        task_source = capture_task_entities(db)
        run_source = capture_run_entities(db)
        source_sequences = [
            tuple(row)
            for row in db.execute(
                "select rowid,name,seq from sqlite_sequence order by rowid"
            )
        ]
        task_forward = migrate_task_entities(db, task_source)
        after_task_sequences = [
            tuple(row)
            for row in db.execute(
                "select rowid,name,seq from sqlite_sequence order by rowid"
            )
        ]
        try:
            run_forward = migrate_run_entities(
                db,
                run_source,
                task_metadata=task_forward,
                run_handoff=run_handoff(run_source),
            )
        except sqlite3.IntegrityError as exc:
            collision = {
                "before_task": source_sequences,
                "after_task_before_run": after_task_sequences,
                "during_failed_run": [
                    tuple(row)
                    for row in db.execute(
                        "select rowid,name,seq from sqlite_sequence order by rowid"
                    )
                ],
                "run_source_sequences": run_source["sequence_rows"],
                "error": str(exc),
            }
            raise RuntimeError(
                "Task/Run allocator composition failed: "
                + json.dumps({"source_artifact": source, "source_route": before,
                              "allocator": collision})
            ) from exc
        db.commit()
    with sqlite3.connect(db_path) as db:
        native_turn = db.execute(
            "select target_id from task_run_identity_map where source_kind='workbench_turns' and public_key=?",
            (source["turn"],),
        ).fetchone()
        native_parent = db.execute(
            "select target_id from task_run_identity_map where source_kind='workbench_tasks' and public_key=?",
            (source["task"],),
        ).fetchone()
    if native_turn is None or native_parent is None:
        raise AssertionError("source Turn/parent identity missing")
    store = object.__new__(WorkbenchStore)
    store.path = db_path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30000
    store._read_snapshot_connection = ContextVar("task2d7j_snapshot", default=None)
    store._reused_connection = ContextVar("task2d7j_reused", default=None)
    # New artifact must come from the real native append writer and its event payload.
    new_path = root / "workbench" / "outputs" / "native-proof.bin"
    new_path.write_bytes(b"new native artifact bytes")
    native_artifact, native_event = store.append_artifact_event(
        str(native_turn[0]),
        sequence=3,
        label="native-proof.bin",
        path=str(new_path),
        media_type="application/octet-stream",
        owner="route-proof",
        now="2026-10-23T12:01:00+00:00",
    )
    source_new_bytes = new_path.read_bytes()
    cycles = []
    for cycle in range(1, 3):
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            next_task_source = capture_task_entities(db)
            next_run_source = capture_run_entities(db)
            task_forward = migrate_task_entities(
                db,
                next_task_source,
                source_context=task_forward["source_context"],
                inverse_identity=task_forward["inverse_identity"],
            )
            run_forward = migrate_run_entities(
                db,
                next_run_source,
                task_metadata=task_forward,
                run_handoff=run_handoff(next_run_source),
            )
            task_inverse = restore_task_entities(db, next_task_source, task_forward)
            run_inverse = restore_run_entities(
                db,
                next_run_source,
                run_forward,
                task_metadata=task_forward,
                task_inverse_metadata=task_inverse,
            )
            db.commit()
        with sqlite3.connect(db_path) as db:
            row = db.execute(
                "select payload_json from runs where record_type='workbench_events' and event_type='artifact_created' and json_extract(payload_json,'$.label')='native-proof.bin'"
            ).fetchone()
            artifact = db.execute(
                "select id,turn_id,path from (select id,turn_id,path from workbench_artifacts)"
            ).fetchall()
            matching = [r for r in artifact if r[2] == str(new_path)]
        if not row or len(matching) != 1:
            raise AssertionError("inverse lost new artifact or event")
        restored_payload = json.loads(row[0])
        source_artifact_id = restored_payload["artifact_id"]
        if restored_payload != {
            "artifact_id": source_artifact_id,
            "label": "native-proof.bin",
            "filename": "native-proof.bin",
            "media_type": "application/octet-stream",
        }:
            raise AssertionError("inverse changed native event payload facts")
        restored_source = _run(
            predecessor,
            _route_script(),
            {**env, "TASK_2D7J_ARTIFACT": source_artifact_id},
        )
        if (
            restored_source["bytes_hex"] != source_new_bytes.hex()
            or restored_source["sha256"] != hashlib.sha256(source_new_bytes).hexdigest()
        ):
            raise AssertionError(
                "source UUID route did not return exact new native artifact bytes"
            )
        # Reforward the exact portable inverse identity captured by inverse; then independently inverse again.
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            handoff = task_inverse["inverse_identity"]
            task_source = capture_task_entities(db)
            run_source = capture_run_entities(db)
            task_forward = migrate_task_entities(
                db,
                task_source,
                source_context=task_inverse["source_context"],
                inverse_identity=handoff,
            )
            run_forward = migrate_run_entities(
                db,
                run_source,
                task_metadata=task_forward,
                run_handoff=run_inverse["run_handoff"],
            )
            db.commit()
        native_payload = None
        with sqlite3.connect(db_path) as db:
            native_payload = db.execute(
                "select payload_json from workbench_events where event_type='artifact_created' and json_extract(payload_json,'$.label')='native-proof.bin'"
            ).fetchone()[0]
            native_row = db.execute(
                "select id,workbench_turn_id,path from workbench_artifacts where path=?",
                (str(new_path),),
            ).fetchone()
        if (
            json.loads(native_payload)
            != {
                "artifact_id": str(native_artifact.id),
                "label": "native-proof.bin",
                "filename": "native-proof.bin",
                "media_type": "application/octet-stream",
            }
            or native_row is None
            or str(native_row[0]) != str(native_artifact.id)
            or int(native_row[1]) != int(native_turn[0])
        ):
            raise AssertionError(
                "reforward changed native artifact/event identity or owner"
            )
        if new_path.read_bytes() != source_new_bytes:
            raise AssertionError("reforward changed artifact bytes")
        cycles.append(
            {
                "cycle": cycle,
                "source_artifact_id": source_artifact_id,
                "source_route_sha256": restored_source["sha256"],
                "native_artifact_id": str(native_artifact.id),
                "payload": restored_payload,
            }
        )
    print(
        json.dumps(
            {
                "predecessor_sha256": hashes,
                "source": source,
                "before_route": before,
                "native_artifact_id": str(native_artifact.id),
                "native_event_id": native_event.id,
                "cycles": cycles,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
