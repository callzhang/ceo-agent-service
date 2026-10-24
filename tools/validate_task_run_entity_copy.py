#!/usr/bin/env python3
"""Run current-copy Task/Run inverse, predecessor cold-write and reforward gates.

The exact paired-release forward pass writes its portable inverse handoff beside
the disposable target before commit. The independent inverse process consumes
that actual handoff after native current facts have been added.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_native_copy_lifecycle import (
    _file_sha256, _portable_value, _restore_portable_value,
)


def _forward_frozen_source(source: Path, target: Path, handoff: Path) -> dict:
    """Forward the exact frozen source and persist its actual in-transaction handoff."""
    from app.current_schema_manifest import require_current_pair
    from app.task_run_entity_schema_release import (
        TARGET_STORE_VERSION, prepare_task_run_entity_release,
    )

    if target.exists():
        raise FileExistsError(f"acceptance target already exists: {target}")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as frozen, \
            closing(sqlite3.connect(target)) as candidate:
        frozen.backup(candidate)
    copied_sha256 = _file_sha256(target)
    if _file_sha256(source) != copied_sha256:
        raise AssertionError("fresh target does not match the frozen-source bytes")
    release = prepare_task_run_entity_release(target, recovery_source_path=source)
    if release is None or release.recovery_handoff_path != handoff:
        raise AssertionError("paired release did not bind the handoff to the active target")
    release.publish()
    release.finalize()
    require_current_pair(target)
    if not handoff.is_file():
        raise AssertionError("forward publication did not persist its inverse handoff")
    return {
        "frozen_source_sha256": _file_sha256(source),
        "target_sha256_before_forward": copied_sha256,
        "target_store_version": TARGET_STORE_VERSION,
        "inverse_handoff_path": str(handoff),
        "inverse_handoff_bytes": handoff.stat().st_size,
    }


def _inverse_child(target: Path, handoff_path: Path) -> dict:
    from app.task_run_entity_schema_release import TaskRunEntitySchemaRelease
    release = TaskRunEntitySchemaRelease.from_inverse_handoff(target, handoff_path)
    release.rollback()
    return {"inverse": "passed", "target": str(target)}


def _cold_old_store(predecessor: Path, target: Path, task_id: int, output: Path) -> dict:
    program = r'''import json, sys
from pathlib import Path
root, source_root, db_path, task_id, output = map(str, sys.argv[1:])
sys.path.insert(0, root)
sys.path.append(source_root)
from tests.support.memory_limit import MemoryLimit
MemoryLimit(3 * 1024**3).start()
from app.store import AutoReplyStore
store = AutoReplyStore(Path(db_path))
task = store.get_reply_task(int(task_id))
if task is None or task.id != int(task_id):
    raise AssertionError("pinned predecessor public Store could not cold-read the native Task")
written = store.ensure_reply_task(
    conversation_id="task5-cold-write-" + str(task_id),
    conversation_title="Task 5 harmless predecessor write",
    single_chat=False, trigger_message_id="task5-cold-write-message-" + str(task_id),
    trigger_create_time="2026-10-24T00:00:00Z", trigger_sender="Task5 fixture",
    trigger_text="Synthetic harmless predecessor Store write",
)
readback = store.get_reply_task(written.id)
if readback is None or readback.id != written.id:
    raise AssertionError("pinned predecessor public Store write did not read back")
Path(output).write_text(json.dumps({
    "module": __import__("app.store", fromlist=["AutoReplyStore"]).__file__,
    "read_task_id": task.id, "write_task_id": written.id,
    "write_readback_task_id": readback.id,
}, sort_keys=True))
'''
    env = {**os.environ, "PYTHONPATH": str(predecessor)}
    child = subprocess.run(
        [sys.executable, "-c", program, str(predecessor), str(ROOT), str(target), str(task_id), str(output)],
        cwd=predecessor, env=env, text=True, capture_output=True, timeout=300,
    )
    if child.returncode:
        raise RuntimeError(child.stderr or child.stdout or f"old predecessor exited {child.returncode}")
    result = json.loads(output.read_text())
    if Path(result["module"]).resolve().parent.parent != predecessor.resolve():
        raise AssertionError("cold Store process imported code outside the pinned predecessor")
    return result


def _add_current_input_revisions(target: Path) -> dict:
    from app.store import AutoReplyStore
    from app.task_entity_storage import append_task_input, bind_claimed_input, bind_current_input

    store = AutoReplyStore(target)
    task = store.ensure_reply_task(
        conversation_id="task5-input-versions-current-copy",
        conversation_title="Task 5 TaskInput revision fixture",
        single_chat=False, trigger_message_id="task5-input-versions-trigger",
        trigger_create_time="2026-10-24T00:00:00Z", trigger_sender="Task5 fixture",
        trigger_text="Initial native input",
    )
    with store._connect() as db:
        db.execute("begin immediate")
        initial = db.execute(
            "select * from task_inputs where id=?", (task.current_input_id,)
        ).fetchone()
        initial_values = dict(initial)
        initial_values.pop("id")
        initial_values.pop("task_id")
        replay, replay_inserted = append_task_input(
            db, task_id=task.id, values=initial_values,
        )
        if replay_inserted or replay["id"] != task.current_input_id:
            raise AssertionError("full TaskInput identity replay did not return its existing row")
        revised = dict(initial_values)
        revised.update({
            "trigger_text": "Current native input revision",
            "input_revision_key": "task5-revision-2",
            "created_at": "2026-10-24T00:01:00Z",
        })
        current, inserted = append_task_input(db, task_id=task.id, values=revised)
        if not inserted:
            raise AssertionError("new TaskInput revision key did not insert a new revision")
        bound = bind_current_input(
            db, task_id=task.id, input_id=current["id"],
            expected_input_version=task.input_version,
            input_version=(task.input_version or 0) + 1,
        )
        held = bind_claimed_input(
            db, task_id=task.id, input_id=task.current_input_id,
            claimed_input_version=task.input_version,
        )
        if bound is None or held is None:
            raise AssertionError("TaskInput current/claimed pointer binding missed")
        db.commit()
    readback = store.get_reply_task(task.id)
    if (readback.current_input_id != current["id"]
            or readback.claimed_input_id != task.current_input_id
            or readback.current_input_id == readback.claimed_input_id):
        raise AssertionError("current and claimed TaskInput pointers did not remain distinct")
    return {
        "task_id": task.id, "initial_input_id": task.current_input_id,
        "current_input_id": current["id"], "replay_inserted": replay_inserted,
        "current_version": readback.input_version,
        "claimed_version": readback.claimed_input_version,
    }


def _check_after_reforward(target: Path, native: dict, old_write: dict) -> dict:
    from app.current_schema_manifest import require_current_pair
    from app.store import AutoReplyStore

    require_current_pair(target)
    store = AutoReplyStore(target)
    task = store.get_reply_task(native["task_id"])
    if (task is None or task.current_input_id != native["current_input_id"]
            or task.claimed_input_id != native["initial_input_id"]
            or task.input_version != native["current_version"]
            or task.claimed_input_version != native["claimed_version"]):
        raise AssertionError("current native Task/Input facts changed across inverse/reforward")
    old_task = store.get_reply_task(old_write["write_task_id"])
    if old_task is None:
        raise AssertionError("predecessor public write did not survive reforward")
    with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("pragma quick_check").fetchone()[0]
        fks = tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))
        marker = db.execute(
            "select value from service_state where key='store_schema_version'"
        ).fetchone()
        task_rows = db.execute(
            "select current_input_id,claimed_input_id,input_version,claimed_input_version "
            "from tasks where id=?", (native["task_id"],)
        ).fetchone()
        inputs = db.execute(
            "select id,input_revision_key,typeof(trigger_text),trigger_text from task_inputs "
            "where task_id=? order by id", (native["task_id"],)
        ).fetchall()
    if integrity != "ok" or fks != (("runs", 190042, "tasks", 1),):
        raise AssertionError({"quick_check": integrity, "foreign_key_check": fks})
    return {
        "quick_check": integrity, "foreign_key_check": fks,
        "schema_marker": marker[0] if marker else None,
        "native_input_pointers": tuple(task_rows),
        "native_input_revisions": tuple(tuple(row) for row in inputs),
        "old_write_task_readback": old_task.id,
    }


def _native_facts_snapshot(target: Path, task_ids: list[int]) -> dict:
    """Hash bounded current facts without retaining user text or a DB-sized map."""
    from app.action_storage import SENT_REPLY_SQL, REPLY_ATTEMPT_COMMON_SQL
    from app.store import (CANDIDATE_REVIEW_SQL, CANDIDATE_SELECTION_SQL,
                           CANDIDATE_EXECUTION_SQL, EXTERNAL_ACTION_RESULT_SQL,
                           REVIEW_CANDIDATE_SQL, AutoReplyStore)

    store = AutoReplyStore(target)
    facts = []
    sequences_seen = None
    for task_id in task_ids:
        task = store.get_reply_task(task_id)
        if task is None:
            raise AssertionError(f"native Task {task_id} is missing")
        runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
        run_facts = []
        attempt_facts = []
        for run in runs:
            run_facts.append({"id": run.id, "role": run.role.value, "status": run.status,
                              "revision": run.proposal_revision, "parent_id": run.parent_agent_run_id,
                              "session_id": run.codex_session_id, "start": run.transcript_start_line,
                              "end": run.transcript_end_line})
            for attempt in store.list_agent_runtime_attempts(run.id):
                attempt_facts.append({"id": attempt.id, "run_id": run.id,
                    "session_mode": getattr(attempt.session_mode, "value", attempt.session_mode),
                    "source_session_id": attempt.source_session_id, "session_id": attempt.session_id,
                    "status": getattr(attempt.status, "value", attempt.status),
                    "failure_class": attempt.failure_class, "failure_code": attempt.failure_code,
                    "model": attempt.model})
        with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            task_row = db.execute("select * from tasks where id=?", (task.id,)).fetchone()
            input_rows = db.execute("select * from task_inputs where task_id=? order by id", (task.id,)).fetchall()
            reviews = db.execute(
                f"select r.* from ({CANDIDATE_REVIEW_SQL}) r join ({REVIEW_CANDIDATE_SQL}) c "
                "on c.id=r.candidate_id where c.task_id=? and c.execution_generation=? order by r.id",
                (task.id, task.execution_generation)).fetchall()
            adopted = db.execute(
                f"select c.* from ({REVIEW_CANDIDATE_SQL}) c "
                "where c.task_id=? and c.execution_generation=? order by c.id",
                (task.id, task.execution_generation)).fetchall()
            selections = db.execute(
                f"select s.* from ({CANDIDATE_SELECTION_SQL}) s join "
                f"({REVIEW_CANDIDATE_SQL}) c on c.id=s.candidate_id "
                "where c.task_id=? and c.execution_generation=? order by s.id",
                (task.id, task.execution_generation)).fetchall()
            executions = db.execute(
                f"select e.* from ({CANDIDATE_EXECUTION_SQL}) e join "
                f"({REVIEW_CANDIDATE_SQL}) c on c.id=e.candidate_id "
                "where c.task_id=? and c.execution_generation=? order by e.id",
                (task.id, task.execution_generation)).fetchall()
            effects = db.execute(
                f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=? order by 1",
                (task.business_object_key,)).fetchall()
            sent = db.execute(
                f"select * from ({SENT_REPLY_SQL}) where conversation_id=? order by 1",
                (task.conversation_id,)).fetchall()
            business_attempts = db.execute(
                f"select * from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=? order by 1",
                (task.conversation_id,)).fetchall()
            fks = tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))
            sequences = tuple(sorted(tuple(row) for row in db.execute(
                "select name,seq from sqlite_sequence where name in "
                "('tasks','task_inputs','runs','runtime_attempts')")))
            key_digest = hashlib.sha256()
            business_key_count = 0
            for key_row in db.execute(
                    "select id,task_id,business_key,created_at,updated_at "
                    "from task_business_keys order by business_key"):
                key_digest.update(json.dumps(list(key_row), ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8"))
                key_digest.update(b"\n")
                business_key_count += 1
        if sequences_seen is not None and sequences_seen != sequences:
            raise AssertionError("Task allocator highwaters changed during bounded snapshot")
        sequences_seen = sequences
        raw = {"task": dict(task_row), "inputs": [dict(row) for row in input_rows],
               "runs": run_facts, "runtime_attempts": attempt_facts,
               "adopted_candidates": [dict(row) for row in adopted],
               "reviews": [dict(row) for row in reviews],
               "selections": [dict(row) for row in selections],
               "executions": [dict(row) for row in executions],
               "effects": [dict(row) for row in effects],
               "sent": [dict(row) for row in sent],
               "business_attempts": [dict(row) for row in business_attempts]}
        encoded = json.dumps(_portable_value(raw), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        facts.append({"task_id": task.id, "status": task.status,
            "current_input_id": task.current_input_id, "claimed_input_id": task.claimed_input_id,
            "input_version": task.input_version, "claimed_input_version": task.claimed_input_version,
            "run_ids": [row["id"] for row in run_facts],
            "runtime_attempt_ids": [row["id"] for row in attempt_facts],
            "review_ids": [row["id"] for row in raw["reviews"]],
            "effect_count": len(effects), "sent_count": len(sent),
            "business_attempt_count": len(business_attempts),
            "semantic_sha256": hashlib.sha256(encoded).hexdigest(),
            "foreign_key_findings": fks})
    return {"facts": facts, "sequence_highwaters": sequences_seen or (),
            "task_business_keys_count": business_key_count,
            "task_business_keys_sha256": key_digest.hexdigest()}


def _final_native_roundtrip(target: Path, frozen_source: Path, predecessor: Path,
                            work_dir: Path, task_ids: list[int]) -> dict:
    """Cold-inverse actual CLI receipts, cold read/write with exact predecessor, then reforward."""
    from app.task_run_entity_schema_release import prepare_task_run_entity_release
    from app.current_schema_manifest import require_current_pair

    target, frozen_source, predecessor, work_dir = map(Path.resolve,
        (target, frozen_source, predecessor, work_dir))
    work_dir.mkdir(parents=True, exist_ok=True)
    handoff = target.with_name(target.name + ".task-run-inverse.json")
    if not handoff.is_file():
        raise AssertionError("canonical portable inverse handoff is missing before final current-fact inverse")
    before = _native_facts_snapshot(target, task_ids)
    source_hash = _file_sha256(frozen_source)
    handoff_bytes = handoff.stat().st_size
    inverse = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--inverse-child",
         "--target", str(target), "--handoff", str(handoff)],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=1800,
    )
    if inverse.returncode:
        raise RuntimeError(inverse.stderr or inverse.stdout or "post-native portable inverse failed")
    if not handoff.is_file():
        raise AssertionError("inverse did not retain its source identity continuation")
    from app.task_run_entity_schema_release import _restore_portable
    source_identity = _restore_portable(json.loads(handoff.read_text(encoding="utf-8")))
    if (source_identity.get("format") != "ceo-task-run-source-identity-v1"
            or not source_identity.get("inverse_identity", {}).get("meeting_business_keys")):
        raise AssertionError("inverse did not durably retain Meeting delivery-key identities")
    inverse_identity = source_identity["inverse_identity"]
    identity_receipt = {
        "sha256": hashlib.sha256(json.dumps(
            _portable_value(inverse_identity), sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")).hexdigest(),
        "owner_count": len(inverse_identity.get("owners", ())),
        "meeting_business_key_count": len(inverse_identity.get("meeting_business_keys", ())),
        "workbench_parent_count": len(inverse_identity.get("workbench_parent_identities", ())),
        "workbench_attachment_count": len(inverse_identity.get("workbench_attachment_identities", ())),
        "workbench_artifact_count": len(inverse_identity.get("workbench_artifact_identities", ())),
        "attachment_file_count": len(inverse_identity.get("workbench_attachment_files", ())),
    }
    cold = _cold_old_store(predecessor, target, task_ids[-1],
                           work_dir / "post-native-predecessor-cold.json")
    release = prepare_task_run_entity_release(target, recovery_source_path=frozen_source)
    if release is None:
        raise AssertionError("post-native predecessor target no longer qualifies for reforward")
    release.publish()
    release.finalize()
    require_current_pair(target)
    from scripts.validate_native_copy_lifecycle import original_rows_unchanged
    expected_native = frozen_source.with_name("fresh-source-expected-native.sqlite3")
    if not expected_native.is_file():
        raise AssertionError("independent exact-source native baseline is missing")
    source_tables_compared = original_rows_unchanged(
        expected_native, target,
        natural_key_overrides={"task_business_keys": ["business_key"]},
        ignored_value_columns={"task_business_keys": ["id"]},
        allow_store_schema_marker_timestamp=True,
    )
    after = _native_facts_snapshot(target, task_ids)
    if before["facts"] != after["facts"]:
        raise AssertionError({"before": before, "after": after})
    before_sequences = dict(before["sequence_highwaters"])
    after_sequences = dict(after["sequence_highwaters"])
    if any(after_sequences.get(name, 0) < highwater
           for name, highwater in before_sequences.items()):
        raise AssertionError({"sequence_highwaters_before": before_sequences,
                              "sequence_highwaters_after": after_sequences})
    if (before["task_business_keys_count"] != after["task_business_keys_count"]
            or before["task_business_keys_sha256"] != after["task_business_keys_sha256"]):
        raise AssertionError("Task business-key IDs or values changed across portable inverse/reforward")
    if _file_sha256(frozen_source) != source_hash:
        raise AssertionError("frozen source changed during post-native inverse/reforward")
    if not handoff.is_file():
        raise AssertionError("reforward did not persist its canonical inverse handoff")
    result = {"passed": True, "inverse": "independent JSON handoff subprocess after actual native receipts",
              "cold_predecessor": cold, "reforward": "passed",
              "exact_source_expected_native_rows_unchanged": True,
              "expected_native_user_tables_compared": source_tables_compared,
              "frozen_source_sha256": source_hash, "pre_inverse_handoff_bytes": handoff_bytes,
              "post_reforward_handoff_bytes": handoff.stat().st_size,
              "source_identity_continuation": {
                  "format": source_identity["format"],
                  **identity_receipt,
              },
              "native_facts_unchanged": before["facts"],
              "task_business_keys_unchanged": {
                  "count": before["task_business_keys_count"],
                  "sha256": before["task_business_keys_sha256"],
              },
              "sequence_highwaters_before": before_sequences,
              "sequence_highwaters_after": after_sequences,
              "foreign_key_findings": after["facts"][0]["foreign_key_findings"]}
    (work_dir / "post-native-roundtrip-result.json").write_text(
        json.dumps(_portable_value(result), allow_nan=False, ensure_ascii=False, indent=2))
    return result


def run(target: Path, frozen_source: Path, predecessor: Path, work_dir: Path) -> dict:
    from app.task_run_entity_schema_release import (
        TARGET_STORE_VERSION, prepare_task_run_entity_release,
    )

    target, frozen_source, predecessor, work_dir = map(Path.resolve,
                                                       (target, frozen_source, predecessor, work_dir))
    if target == frozen_source:
        raise ValueError("frozen source must never be the mutable current target")
    work_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = target.with_name(target.name + ".task-run-inverse.json")
    evidence: dict = {
        "target_path": str(target), "frozen_source_path": str(frozen_source),
        "pinned_predecessor": str(predecessor),
        "frozen_source_sha256": _file_sha256(frozen_source),
    }
    evidence["forward"] = _forward_frozen_source(frozen_source, target, handoff_path)
    native = _add_current_input_revisions(target)
    evidence["new_native_input_revision_facts"] = native

    handoff_bytes = handoff_path.stat().st_size
    inverse_output = work_dir / "inverse-child.json"
    inverse = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--inverse-child",
         "--target", str(target), "--handoff", str(handoff_path)],
        cwd=Path(__file__).resolve().parents[1], text=True,
        capture_output=True, timeout=1800,
    )
    if inverse.returncode:
        raise RuntimeError(inverse.stderr or inverse.stdout or "portable inverse child failed")
    evidence["portable_inverse"] = {"exit_code": inverse.returncode,
                                      "handoff_bytes": handoff_bytes}
    cold = _cold_old_store(
        predecessor, target, native["task_id"], work_dir / "predecessor-cold-read-write.json",
    )
    evidence["genuine_predecessor_cold_read_write"] = cold
    release = prepare_task_run_entity_release(target, recovery_source_path=frozen_source)
    if release is None:
        raise AssertionError("current target unexpectedly had no paired release")
    release.publish()
    release.finalize()
    if TARGET_STORE_VERSION != "2026-10-23.1":
        raise AssertionError("unexpected Task/Run target version")
    evidence["reforward"] = _check_after_reforward(target, native, cold)
    evidence["target_sha256_after_reforward"] = _file_sha256(target)
    evidence["passed"] = True
    result = work_dir / "task-run-roundtrip-result.json"
    result.write_text(json.dumps(_portable_value(evidence), allow_nan=False,
                                 ensure_ascii=False, indent=2))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--frozen-source", type=Path)
    parser.add_argument("--predecessor-root", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--inverse-child", action="store_true")
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--native-roundtrip", action="store_true")
    parser.add_argument("--native-task-id", type=int, action="append", default=[])
    args = parser.parse_args()
    from tests.support.memory_limit import MemoryLimit
    memory_limit = MemoryLimit(3 * 1024**3)
    memory_limit.start()
    if args.inverse_child:
        print(json.dumps(_inverse_child(args.target, args.handoff)))
        return 0
    if args.native_roundtrip:
        if not args.frozen_source or not args.predecessor_root or not args.work_dir or not args.native_task_id:
            parser.error("post-native roundtrip requires frozen source, predecessor, work dir, and task ids")
        result = _final_native_roundtrip(args.target, args.frozen_source,
                                         args.predecessor_root, args.work_dir,
                                         args.native_task_id)
        print(json.dumps(_portable_value(result), allow_nan=False, ensure_ascii=False))
        return 0
    if not args.frozen_source or not args.predecessor_root or not args.work_dir:
        parser.error("full validation requires --frozen-source, --predecessor-root and --work-dir")
    result = run(args.target, args.frozen_source, args.predecessor_root, args.work_dir)
    print(json.dumps(_portable_value(result), allow_nan=False, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
