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
turn=store.get_turn(turn_id)
artifact=store.get_artifact(artifact_id)
events=store.events_after(turn_id)
matches=[event for event in events if event.event_type=="artifact_created" and event.payload.get("artifact_id")==artifact_id]
if turn is None or artifact is None or artifact.turn_id!=turn_id or len(matches)!=1:
    raise AssertionError("source artifact/event projection does not resolve on its source Turn")
executor=object.__new__(WorkbenchExecutor); executor.workspace=root
app=FastAPI()
register_workbench_routes(app,store,executor,RuntimeRegistry(),root,
    mutation_guard=lambda request: None,artifact_roots=(root,))
client=TestClient(app)
response=client.get(f"/api/workbench/tasks/{task_id}/turns/{turn_id}/artifacts/{artifact_id}/download")
client.close()
if response.status_code != 200: raise AssertionError(f"source download route {response.status_code}: {response.text}")
print(json.dumps({"status":response.status_code,"bytes_hex":response.content.hex(),
    "sha256":__import__("hashlib").sha256(response.content).hexdigest(),
    "event_id":matches[0].id,"event_payload":matches[0].payload,
    "artifact_turn_id":artifact.turn_id,"artifact_path":artifact.path,
    "store_file":WorkbenchStore.__init__.__code__.co_filename,
    "route_file":register_workbench_routes.__code__.co_filename}))
"""


def _current_download(
    store, root: Path, parent_id: str, turn_id: str, artifact_id: str
) -> dict:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.workbench.api import register_workbench_routes
    from app.workbench.executor import WorkbenchExecutor
    from app.workbench.runtime import RuntimeRegistry

    executor = object.__new__(WorkbenchExecutor)
    executor.workspace = root
    app = FastAPI()
    register_workbench_routes(
        app,
        store,
        executor,
        RuntimeRegistry(),
        root,
        mutation_guard=lambda request: None,
        artifact_roots=(root,),
    )
    client = TestClient(app)
    response = client.get(
        f"/api/workbench/tasks/{parent_id}/turns/{turn_id}/artifacts/{artifact_id}/download"
    )
    client.close()
    if response.status_code != 200:
        raise AssertionError(
            f"current download route {response.status_code}: {response.text}"
        )
    return {
        "status": response.status_code,
        "bytes_hex": response.content.hex(),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }


def _native_store(db_path: Path):
    from contextvars import ContextVar
    from app.workbench.store import WorkbenchStore

    store = object.__new__(WorkbenchStore)
    store.path = db_path
    store.busy_timeout_seconds = 30
    store.busy_timeout_milliseconds = 30_000
    store._read_snapshot_connection = ContextVar("task2d7j_snapshot", default=None)
    store._reused_connection = ContextVar("task2d7j_reused", default=None)
    return store


def _identity(db, source_kind: str, public_key: str) -> str:
    row = db.execute(
        "select target_id from task_run_identity_map where source_kind=? and public_key=?",
        (source_kind, public_key),
    ).fetchone()
    if row is None:
        raise AssertionError(
            f"missing canonical identity for {source_kind}:{public_key}"
        )
    return str(row[0])


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
    import uuid
    from app.task_entity_schema import (
        capture_task_entities,
        migrate_task_entities,
        restore_task_entities,
    )
    from app.task_entity_inverse_files import (
        capture_workbench_attachment_files,
        finalize_workbench_attachment_forward_files,
        finalize_workbench_attachment_inverse_files,
        stage_workbench_attachment_forward_files,
        stage_workbench_attachment_inverse_files,
    )
    from app.run_entity_schema import (
        capture_run_entities,
        migrate_run_entities,
        restore_run_entities,
        run_handoff_context,
        run_handoff_digest,
    )

    db_path = root / "store.sqlite3"
    attachment_root = root / "workbench" / "attachments"

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
        task_forward = migrate_task_entities(db, task_source)
        run_forward = migrate_run_entities(
            db,
            run_source,
            task_metadata=task_forward,
            run_handoff=run_handoff(run_source),
        )
        db.commit()
    with sqlite3.connect(db_path) as db:
        native_turn = _identity(db, "workbench_turns", source["turn"])
        native_parent = _identity(db, "workbench_tasks", source["task"])
    store = _native_store(db_path)
    source_native_events = store.events_after(native_turn)
    original_event = next(
        event
        for event in source_native_events
        if event.event_type == "artifact_created"
        and event.payload.get("label") == "proof.bin"
    )
    original_native_id = str(original_event.payload["artifact_id"])
    original_native = store.get_artifact(original_native_id)
    if original_native is None or original_native.turn_id != native_turn:
        raise AssertionError(
            "forward source artifact reference lost its native Turn owner"
        )
    if original_native_id != str(original_event.payload["artifact_id"]):
        raise AssertionError(
            "current event does not reference the same source artifact"
        )
    current_before = _current_download(
        store, root, native_parent, native_turn, original_native_id
    )
    if current_before["bytes_hex"] != source["bytes_hex"]:
        raise AssertionError("current route changed source artifact bytes")

    # New artifact must come from the real native append writer and its event payload.
    new_path = root / "workbench" / "outputs" / "native-proof.bin"
    new_path.write_bytes(b"new native artifact bytes")
    native_artifact, native_event = store.append_artifact_event(
        native_turn,
        sequence=3,
        label="native-proof.bin",
        path=str(new_path),
        media_type="application/octet-stream",
        owner="route-proof",
        now="2026-10-23T12:01:00+00:00",
    )
    source_new_bytes = new_path.read_bytes()
    native_created_id = str(native_artifact.id)
    native_events = store.events_after(native_turn)
    appended = next(event for event in native_events if event.id == native_event.id)
    if appended.payload != {
        "artifact_id": native_created_id,
        "label": "native-proof.bin",
        "filename": "native-proof.bin",
        "media_type": "application/octet-stream",
    }:
        raise AssertionError(
            "native writer event payload does not resolve to its artifact"
        )
    if store.get_artifact(native_created_id).turn_id != native_turn:
        raise AssertionError("native writer artifact owner differs from its Turn")
    current_native = _current_download(
        store, root, native_parent, native_turn, native_created_id
    )
    if current_native["bytes_hex"] != source_new_bytes.hex():
        raise AssertionError("current download route changed new native artifact bytes")

    # The first inverse uses the exact source metadata captured before either forward conversion.
    def inverse(
        task_source_metadata,
        run_source_metadata,
        task_forward_metadata,
        run_forward_metadata,
    ):
        with store._attachment_lock(create_workbench=False):
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                attachment_snapshot = capture_workbench_attachment_files(
                    db, attachment_root
                )
                db.execute("pragma foreign_keys=off")
                db.execute("begin immediate")
                task_inverse_metadata = restore_task_entities(
                    db, task_source_metadata, task_forward_metadata
                )
                file_plan = stage_workbench_attachment_inverse_files(
                    db, attachment_root, attachment_snapshot, task_inverse_metadata
                )
                run_inverse_metadata = restore_run_entities(
                    db,
                    run_source_metadata,
                    run_forward_metadata,
                    task_metadata=task_forward_metadata,
                    task_inverse_metadata=task_inverse_metadata,
                )
                db.commit()
            with sqlite3.connect(db_path) as db:
                finalize_workbench_attachment_inverse_files(
                    db,
                    file_plan,
                    task_inverse_metadata["inverse_identity"],
                    attachment_root,
                )
        return task_inverse_metadata, run_inverse_metadata

    task_inverse, run_inverse = inverse(
        task_source, run_source, task_forward, run_forward
    )
    handoff = json.loads(json.dumps(task_inverse["inverse_identity"]))
    run_handoff_portable = json.loads(json.dumps(run_inverse["run_handoff"]))
    new_source_artifact_id = _run(
            predecessor,
            r"""import json,os; from pathlib import Path; from app.workbench.store import WorkbenchStore
s=WorkbenchStore(Path(os.environ["TASK_2D7J_ROOT"])/"store.sqlite3")
e=[e for e in s.events_after(os.environ["TASK_2D7J_TURN"]) if e.event_type=="artifact_created" and e.payload.get("label")=="native-proof.bin"]
if len(e)!=1: raise AssertionError("inverse source artifact event missing")
a=s.get_artifact(e[0].payload["artifact_id"])
if a is None or a.turn_id!=os.environ["TASK_2D7J_TURN"]: raise AssertionError("inverse source artifact does not belong to Turn")
print(json.dumps({"artifact_id":a.id,"event_id":e[0].id,"payload":e[0].payload,"path":a.path,"turn_id":a.turn_id}))""",
            {**env, "TASK_2D7J_ARTIFACT": ""},
        )
    if not uuid.UUID(new_source_artifact_id["artifact_id"]):
        raise AssertionError("inverse did not allocate a source UUID artifact identity")
    if new_source_artifact_id["payload"] != {
        "artifact_id": new_source_artifact_id["artifact_id"],
        "label": "native-proof.bin",
        "filename": "native-proof.bin",
        "media_type": "application/octet-stream",
    }:
        raise AssertionError(
            "inverse changed new artifact event's unrelated payload facts"
        )
    if Path(new_source_artifact_id["path"]).read_bytes() != source_new_bytes:
        raise AssertionError("inverse changed new native artifact bytes")
    old_new_artifact_route = _run(
        predecessor,
        _route_script(),
        {**env, "TASK_2D7J_ARTIFACT": new_source_artifact_id["artifact_id"]},
    )
    if old_new_artifact_route["bytes_hex"] != source_new_bytes.hex():
        raise AssertionError(
            "unchanged predecessor UUID route did not return new artifact bytes"
        )
    source_new_event_id = new_source_artifact_id["event_id"]
    cycles = []
    for cycle in range(1, 3):
        with store._attachment_lock(create_workbench=False):
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                db.execute("pragma foreign_keys=off")
                db.execute("begin immediate")
                cycle_task_source = capture_task_entities(db)
                cycle_run_source = capture_run_entities(db)
                forward_file_plan = stage_workbench_attachment_forward_files(
                    db, attachment_root, handoff
                )
                task_forward = migrate_task_entities(
                    db,
                    cycle_task_source,
                    source_context=task_inverse["source_context"],
                    inverse_identity=handoff,
                )
                run_forward = migrate_run_entities(
                    db,
                    cycle_run_source,
                    task_metadata=task_forward,
                    run_handoff=run_handoff_portable,
                )
                db.commit()
            with sqlite3.connect(db_path) as db:
                finalize_workbench_attachment_forward_files(
                    db, forward_file_plan, handoff, attachment_root
                )
        store = _native_store(db_path)
        with sqlite3.connect(db_path) as db:
            canonical_turn = _identity(db, "workbench_turns", source["turn"])
            canonical_parent = _identity(db, "workbench_tasks", source["task"])
        current_events = store.events_after(canonical_turn)
        current_new_events = [
            event
            for event in current_events
            if event.event_type == "artifact_created"
            and event.payload.get("label") == "native-proof.bin"
        ]
        if len(current_new_events) != 1:
            raise AssertionError("reforward lost the native artifact event")
        current_event = current_new_events[0]
        if (
            current_event.id != native_event.id
            or current_event.payload != appended.payload
        ):
            raise AssertionError(
                "reforward changed native event identity or unrelated payload values"
            )
        current_artifact = store.get_artifact(native_created_id)
        if current_artifact is None or current_artifact.turn_id != canonical_turn:
            raise AssertionError(
                "reforward changed native artifact identity or Turn owner"
            )
        if Path(current_artifact.path).read_bytes() != source_new_bytes:
            raise AssertionError("reforward changed native artifact bytes")
        route_readback = _current_download(
            store, root, canonical_parent, canonical_turn, native_created_id
        )
        if route_readback["bytes_hex"] != source_new_bytes.hex():
            raise AssertionError(
                "reforward current route returned different artifact bytes"
            )

        if cycle == 1:
            task_inverse, run_inverse = inverse(
                cycle_task_source, cycle_run_source, task_forward, run_forward
            )
            handoff = json.loads(json.dumps(task_inverse["inverse_identity"]))
            run_handoff_portable = json.loads(json.dumps(run_inverse["run_handoff"]))
            source_view = _run(
                    predecessor,
                    r"""import json,os; from pathlib import Path; from app.workbench.store import WorkbenchStore
s=WorkbenchStore(Path(os.environ["TASK_2D7J_ROOT"])/"store.sqlite3")
e=[e for e in s.events_after(os.environ["TASK_2D7J_TURN"]) if e.event_type=="artifact_created" and e.payload.get("label")=="native-proof.bin"]
if len(e)!=1: raise AssertionError("second inverse source artifact event missing")
a=s.get_artifact(e[0].payload["artifact_id"])
print(json.dumps({"artifact_id":a.id,"event_id":e[0].id,"payload":e[0].payload,"path":a.path,"turn_id":a.turn_id}))""",
                    env,
                )
            if (
                source_view["artifact_id"] != new_source_artifact_id["artifact_id"]
                or source_view["event_id"] != source_new_event_id
            ):
                raise AssertionError(
                    "portable inverse changed source artifact/event identity"
                )
            if Path(source_view["path"]).read_bytes() != source_new_bytes:
                raise AssertionError("second inverse changed source artifact bytes")
            old_route_again = _run(
                predecessor,
                _route_script(),
                {**env, "TASK_2D7J_ARTIFACT": source_view["artifact_id"]},
            )
            if old_route_again["bytes_hex"] != source_new_bytes.hex():
                raise AssertionError("source UUID route failed after second inverse")
        cycles.append(
            {
                "cycle": cycle,
                "canonical_turn": canonical_turn,
                "canonical_parent": canonical_parent,
                "native_artifact_id": native_created_id,
                "native_event_id": current_event.id,
                "source_artifact_id": new_source_artifact_id["artifact_id"],
                "source_event_id": source_new_event_id,
                "payload": current_event.payload,
                "route_sha256": route_readback["sha256"],
            }
        )
    print(
        json.dumps(
            {
                "predecessor_sha256": hashes,
                "source": source,
                "before_route": before,
                "current_original_route": current_before,
                "current_new_route": current_native,
                "native_artifact_id": native_created_id,
                "native_event_id": native_event.id,
                "inverse_source_artifact": new_source_artifact_id,
                "inverse_old_route": old_new_artifact_route,
                "cycles": cycles,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
