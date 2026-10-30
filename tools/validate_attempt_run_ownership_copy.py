"""Validate an operator-supplied copy, including real predecessor cold writes.

No workers, models or provider execution. Receipts are explicit test doubles.
The output contains only hashes, counts and validation record identities.
"""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.attempt_run_ownership import materialize_payload  # noqa: E402
from app.attempt_run_ownership_release import prepare_attempt_run_ownership_release, source_objects  # noqa: E402
from app.current_schema_manifest import schema_objects  # noqa: E402
from app.task8_storage import register_task8_functions  # noqa: E402


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def connect(path):
    db = sqlite3.connect(path)
    register_task8_functions(db)
    db.create_function("ceo_validation_materialized_attempt", 1, lambda value: materialize_payload(db, value))
    return closing(db)


def fingerprint(path, bounds=None):
    result = {}
    with connect(path) as db:
        for (table,) in db.execute("select name from sqlite_schema where type='table' order by name"):
            fields = [row[1] for row in db.execute(f"pragma table_xinfo({quote(table)})") if row[6] == 0]
            bound = db.execute(f"select max(rowid) from {quote(table)}").fetchone()[0] if bounds is None else bounds[table]["max_rowid"]
            projections = ["case when _kind=15 then ceo_validation_materialized_attempt(payload_json) else payload_json end"
                           if table == "actions" and field == "payload_json" else quote(field) for field in fields]
            where = "rowid<=?" if bound is not None else "0"
            args = (bound,) if bound is not None else ()
            if table == "state":
                where += " and not(record_type='service_state' and json_extract(payload_json,'$.key')='store_schema_version')"
            digest = hashlib.sha256()
            count = 0
            for row in db.execute(f"select rowid,{','.join(projections)} from {quote(table)} where {where} order by rowid", args):
                for value in row:
                    raw = value if isinstance(value, bytes) else repr(value).encode()
                    digest.update(type(value).__name__.encode() + b":" + str(len(raw)).encode() + b":" + raw)
                count += 1
            result[table] = {"max_rowid": bound, "rows": count, "sha256": digest.hexdigest()}
    return result


def sequences(path):
    with connect(path) as db:
        return list(db.execute("select rowid,name,seq from sqlite_sequence order by rowid"))


def seed_current(path):
    from app.action_entity_storage import append_action_receipt, create_action
    from app.reply_receipt_reference import add_reference
    from app.run_entity_storage import create_run
    from app.store import AutoReplyStore, _close_idle_connections

    tag = "ownership-validation-" + uuid.uuid4().hex
    store = AutoReplyStore(path)
    assert store.enqueue_reply_task(
        conversation_id=tag, conversation_title="Synthetic validation", single_chat=True,
        trigger_message_id=tag, trigger_create_time="2026-10-29T00:00:00+00:00",
        trigger_sender="test_double", trigger_text="Synthetic input; no external delivery",
    )
    with store._connect() as db:
        task_id = db.execute("select task_id from task_inputs where conversation_id=? and trigger_message_id=?", (tag, tag)).fetchone()[0]
        generation = db.execute("select execution_generation from tasks where id=?", (task_id,)).fetchone()[0]
        consumer = create_run(db, values={"task_id": task_id, "role": "consumer", "status": "completed",
            "execution_generation": generation, "proposal_revision": 1, "turn_attempt": 1,
            "session_id": "synthetic-consumer", "transcript_start": 2, "transcript_end": 5})
        audit = create_run(db, values={"task_id": task_id, "parent_id": consumer["id"], "role": "audit", "status": "completed",
            "execution_generation": generation, "proposal_revision": 1, "turn_attempt": 1,
            "session_id": "synthetic-audit", "transcript_start": 6, "transcript_end": 9})
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task_id, expected_execution_generation=generation, run_id=audit["id"],
        task_status="failed", task_error="synthetic_failure", available_at="",
        conversation_id=tag, conversation_title="Synthetic validation", trigger_message_id=tag,
        trigger_sender="test_double", trigger_text="Synthetic input; no external delivery",
        audit_tool_events_json="[]", audit_summary="Synthetic failure detail is preserved",
        send_status="failed", send_error="synthetic_failure", channel="dingtalk",
    )
    with store._connect() as db:
        action = create_action(db, values={"operation": "approve", "status": "done"})
        body = json.dumps({"provider": "test_double", "ok": True}, separators=(",", ":"))
        receipt_id = append_action_receipt(db, action["id"], values={"operation": "approve", "provider_result_json": body})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt_id, expected_body=body)
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt.codex_reason == attempt.audit_summary == "Synthetic failure detail is preserved"
    assert attempt.codex_session_id == "synthetic-audit"
    assert attempt.oa_action_result_json == body
    _close_idle_connections()
    return {"task_id": task_id, "consumer_run_id": consumer["id"], "audit_run_id": audit["id"],
            "attempt_id": attempt_id, "action_id": action["id"], "receipt_id": receipt_id}


OLD_WRITE = r'''
from pathlib import Path
import json,sys
from app.store import AutoReplyStore,_close_idle_connections
path=Path(sys.argv[1]);seed=json.loads(sys.argv[2]);store=AutoReplyStore(path)
a=store.get_reply_attempt(seed['attempt_id'])
assert (a.codex_reason,a.audit_summary,a.codex_session_id,a.codex_transcript_start_line,a.codex_transcript_end_line)==('Synthetic failure detail is preserved','Synthetic failure detail is preserved','synthetic-audit',6,9)
assert json.loads(a.oa_action_result_json)=={'provider':'test_double','ok':True}
id=store.record_reply_attempt(conversation_id='cold-source-validation',conversation_title='Synthetic cold validation',trigger_message_id='cold-source-input',trigger_sender='test_double',trigger_text='Synthetic source input',action='stop_with_error',sensitivity_kind='general',codex_reason='independent cause',audit_summary='different failure detail',codex_session_id='orphan-cold-session',codex_transcript_start_line=11,codex_transcript_end_line=14,send_status='failed')
b=store.get_reply_attempt(id);assert b.codex_reason=='independent cause' and b.audit_summary=='different failure detail'
_close_idle_connections();print(json.dumps({'cold_attempt_id':id,'source_read_write_passed':True}))
'''


def validate(path, old_code):
    def forbid_network(*_args, **_kwargs):
        raise AssertionError("copy validation forbids provider/network execution")

    socket.socket.connect = forbid_network
    before = fingerprint(path)
    water = sequences(path)
    with connect(path) as db:
        initial_fk = list(db.execute("pragma foreign_key_check"))
    release = prepare_attempt_run_ownership_release(path)
    release.publish()
    release.verify_loaded()
    assert fingerprint(path, before) == before
    assert sequences(path) == water
    seed = seed_current(path)
    updated_water = sequences(path)
    release.rollback()
    restored = fingerprint(path, before)
    assert {k: v for k, v in restored.items() if k != "sqlite_sequence"} == {k: v for k, v in before.items() if k != "sqlite_sequence"}
    assert sequences(path) == updated_water
    with connect(path) as db:
        assert schema_objects(db) == source_objects()
        assert list(db.execute("pragma foreign_key_check")) == initial_fk
    env = {**os.environ, "PYTHONPATH": str(old_code), "CEO_ENV_FILE": "/private/tmp/no-ownership-validation.env"}
    old = subprocess.run([sys.executable, "-c", OLD_WRITE, str(path), json.dumps(seed)], cwd=old_code,
                         env=env, capture_output=True, text=True, check=True, timeout=120)
    cold = json.loads(old.stdout.strip().splitlines()[-1])
    after_cold_water = sequences(path)
    again = prepare_attempt_run_ownership_release(path)
    again.publish()
    again.finalize()
    assert sequences(path) == after_cold_water
    current = fingerprint(path, before)
    assert {k: v for k, v in current.items() if k != "sqlite_sequence"} == {k: v for k, v in before.items() if k != "sqlite_sequence"}
    from app.store import AutoReplyStore, _close_idle_connections
    attempt = AutoReplyStore(path).get_reply_attempt(cold["cold_attempt_id"])
    assert (attempt.codex_reason, attempt.audit_summary, attempt.codex_session_id) == ("independent cause", "different failure detail", "orphan-cold-session")
    _close_idle_connections()
    return {"forward_original_fingerprints_equal": True, "inverse_original_fingerprints_equal": True,
            "reforward_original_fingerprints_equal": True, "allocation_watermarks_preserved": True,
            "original_tables": len(before), "original_rows": sum(v["rows"] for v in before.values()),
            "foreign_key_findings": initial_fk, "synthetic": seed, "old_code": cold,
            "real_sends": 0, "workers_started": False, "native_cli_invoked": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--old-code", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.database.resolve(), args.old_code.resolve())
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
