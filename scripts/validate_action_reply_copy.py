"""Offline copy acceptance for the Store18 to Store19 Attempt/Memory release."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

from app import action_reply_schema_release as reply_release
from app.action_reply_storage import PUBLISHED_ACTION_TYPES, capture_action_reply_schema
from app.action_storage import (
    ACTION_SEQUENCE_NAMES, DELIVERY_SEQUENCE_NAME,
    REPLY_SEQUENCE_NAMES, action_schema_is_current, delete_action, insert_action,
    reply_action_projection, update_action,
)
from app.store_schema_release import _migrate_store, prepare_store_schema_release

SOURCE_TABLES = ("reply_attempts", "memory_write_events")
PRODUCTION_ROOT = Path.home() / "Services/ceo-agent-service"
MARKER = "store_schema_version"


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def rowset(db, sql, parameters=()):
    cursor = db.execute(sql, parameters)
    names = tuple(item[0] for item in cursor.description)
    values = tuple(tuple(row) for row in cursor)
    return names, values, tuple(tuple(type(value).__name__ for value in row) for row in values)


def fingerprint(db, sql, parameters=()):
    digest = hashlib.sha256()
    count = 0
    for row in db.execute(sql, parameters):
        digest.update(pickle.dumps(tuple(row), protocol=4))
        count += 1
    return count, digest.digest()


def table_snapshot(db, name):
    columns = [row[1] for row in db.execute(f"pragma table_xinfo({quote(name)})") if row[6] == 0]
    selected = ",".join(quote(column) for column in columns)
    types = ",".join(f"typeof({quote(column)})" for column in columns)
    order = ",".join(str(index) for index in range(1, len(columns) + 1))
    return (fingerprint(db, f"select {selected} from {quote(name)} order by {order}"),
            fingerprint(db, f"select {types} from {quote(name)} order by {order}"))


def unrelated_snapshot(db):
    names = [row[0] for row in db.execute(
        "select name from sqlite_schema where type='table' and name not like 'sqlite_%' order by name")]
    return {name: table_snapshot(db, name) for name in names
            if name not in (*SOURCE_TABLES, "actions", "service_state")}


def service_snapshot(db):
    return fingerprint(
        db, "select key,value,updated_at,typeof(value),typeof(updated_at) "
            "from service_state where key<>? order by key", (MARKER,))


def marker(db):
    return db.execute(
        "select value,updated_at from service_state where key=?", (MARKER,)).fetchone()


def action_snapshot(db, columns):
    selected = ",".join(quote(column) for column in columns)
    types = ",".join(f"typeof({quote(column)})" for column in columns)
    predicate = "record_type in (" + ",".join("?" for _ in PUBLISHED_ACTION_TYPES) + ")"
    return (fingerprint(db, f"select {selected} from actions where {predicate} order by id",
                        PUBLISHED_ACTION_TYPES),
            fingerprint(db, f"select {types} from actions where {predicate} order by id",
                        PUBLISHED_ACTION_TYPES))


def waters(db, names):
    return {name: (row[0] if (row := db.execute(
        "select seq from sqlite_sequence where name=?", (name,)).fetchone()) else None)
            for name in names}


def consistency(db, expected_fk=None):
    assert db.execute("pragma quick_check").fetchone() == ("ok",)
    assert db.execute("pragma integrity_check").fetchone() == ("ok",)
    fk = tuple(tuple(row) for row in db.execute("pragma foreign_key_check"))
    if expected_fk is not None:
        assert fk == expected_fk, "FK findings changed"
    return fk


def normalized_marker(value):
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError, UnicodeError):
        return "[]"
    if not isinstance(parsed, list):
        return "[]"
    expected = {"tool": "audit_review", "result": "queued"}
    return json.dumps([expected for item in parsed if item == expected], separators=(",", ":"))


def source_reply_rows(db):
    result = {}
    for table in SOURCE_TABLES:
        columns = tuple(row[1] for row in db.execute(
            f"pragma table_xinfo({quote(table)})") if row[6] == 0)
        selected = ",".join(map(quote, columns))
        source_rows = tuple(tuple(row) for row in db.execute(
            f"select {selected} from {table} order by id"))
        expected = []
        for row in source_rows:
            values = list(row)
            if table == "reply_attempts":
                position = columns.index("audit_tool_events_json")
                values[position] = normalized_marker(values[position])
            else:
                values[columns.index("payload_json")] = "{}"
            expected.append(tuple(values))
        result[table] = (columns, tuple(expected))
    return result


def queue_snapshot(db, table, projection=None):
    source = f"({projection})" if projection else quote(table)
    status = "send_status" if table == "reply_attempts" else "status"
    return (
        rowset(db, f"select {status},count(*) from {source} group by {status} order by {status}"),
        rowset(db, f"select id,{status} from {source} order by {status},id"),
    )


def projected_rows(db, variant, table):
    projection = reply_action_projection(table, variant)
    return rowset(db, f"select * from ({projection}) order by id")


def assert_expected_reply(db, variant, expected):
    for table, (columns, values) in expected.items():
        actual = projected_rows(db, variant, table)
        assert actual == (
            columns, values,
            tuple(tuple(type(value).__name__ for value in row) for row in values)
        ), f"{table} values/types differ"


def full_snapshot(db, variant, action_columns):
    reply = {table: projected_rows(db, variant, table) for table in SOURCE_TABLES}
    seq_names = (*ACTION_SEQUENCE_NAMES.values(), DELIVERY_SEQUENCE_NAME,
                 *REPLY_SEQUENCE_NAMES.values())
    return (unrelated_snapshot(db), service_snapshot(db), action_snapshot(db, action_columns),
            reply, waters(db, seq_names), marker(db), consistency(db))


OLD_PROGRAM = '''
from pathlib import Path
import json, sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
store = AutoReplyStore(Path(sys.argv[1]))
EmailStore(Path(sys.argv[1]))
if sys.argv[2] == "read":
    first = store.get_reply_attempt(int(sys.argv[3]))
    second = store.get_reply_attempt(int(sys.argv[4]))
    assert first is not None and second is not None
    assert json.loads(first.audit_tool_events_json) == [{"tool":"audit_review","result":"queued"},{"tool":"audit_review","result":"queued"}]
    assert second.audit_tool_events_json == "[]"
    assert first.codex_reason == "adopted" and first.audit_summary == "adopted summary"
    assert first.draft_reply_text == "draft\\x00body" and first.final_reply_text == "updated\\x00final"
    assert first.corrected_reply_text == "corrected\\x00body"
    assert first.oa_action_result_json == "raw\\x00oa"
    assert first.calendar_response_result_json == "broken {"
    assert first.mail_action_result_json == "raw\\x00mail"
    assert first.reaction_action_result_json == "raw\\x00reaction"
    assert first.document_action_result_json == "raw\\x00document"
    assert first.skill_update_receipts_json == "raw\\x00skill"
    receipts = store.list_memory_write_events_for_attempt(second.id)
    assert len(receipts) == 1
    event = receipts[0]
    assert event.payload_json == "{}" and event.status == "written"
    assert event.attempts == 3 and event.memory_episode_id == "fake"
    assert event.last_error == "raw\\x00error"
    assert event.created_at == "2026-01-01 01:02:03" and event.updated_at == "2026-01-02 03:04:05"
else:
    fake = {"ok": True, "episode_uuid": "copy-only-fake-episode", "processing_status": "completed"}
    events = json.dumps([{"tool":"memory_write", "output":json.dumps(fake)}])
    new_id = store.record_reply_attempt(conversation_id="copy-old", conversation_title="copy", trigger_message_id="old-write", trigger_sender="copy", trigger_text="transient old body", action="agent_run", sensitivity_kind="ordinary", draft_reply_text="transient draft", audit_tool_events_json=events, send_status="pending")
    attempt = store.get_reply_attempt(new_id)
    assert attempt is not None and attempt.id == new_id
    receipts = store.list_memory_write_events_for_attempt(new_id)
    assert len(receipts) == 1 and receipts[0].status == "written" and receipts[0].memory_episode_id == fake["episode_uuid"]
    print(json.dumps({"attempt_id":new_id,"memory_id":receipts[0].id}))
'''


def old_process(oldroot, path, mode, attempt_ids=()):
    args = [sys.executable, "-c", OLD_PROGRAM, str(path), mode]
    args.extend(str(attempt_id) for attempt_id in attempt_ids)
    result = subprocess.run(args, cwd=oldroot, env={**os.environ, "PYTHONPATH": str(oldroot)},
                            capture_output=True, text=True, timeout=300, check=True)
    return json.loads(result.stdout) if mode == "write" else None


def case(repo, oldroot, path):
    source_file_bytes = path.stat().st_size
    with closing(sqlite3.connect(path)) as db:
        baseline_fk = consistency(db)
        source_schema = capture_action_reply_schema(db)
        source_schema_sha256 = hashlib.sha256(json.dumps(
            source_schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        source_marker = marker(db)
        unrelated = unrelated_snapshot(db)
        service = service_snapshot(db)
        action_columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
        retained = action_snapshot(db, action_columns)
        base_waters = waters(db, (*ACTION_SEQUENCE_NAMES.values(), DELIVERY_SEQUENCE_NAME))
        reply_waters = waters(db, SOURCE_TABLES)
        expected = source_reply_rows(db)
        source_queues = {table: queue_snapshot(db, table) for table in SOURCE_TABLES}
        source_counts = {name: len(expected[name][1]) for name in SOURCE_TABLES}
        tables_before = db.execute(
            "select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0]
    release = prepare_store_schema_release(repo, path)
    assert isinstance(release, reply_release.ActionReplySchemaRelease)
    assert release.migrate is _migrate_store
    release.publish()
    variant = source_schema["target_variant"]
    with closing(sqlite3.connect(path)) as db:
        assert marker(db)[0] == reply_release.ACTION_REPLY_VERSION
        assert action_schema_is_current(db, include_reply=True, reply_variant=variant)
        assert all(db.execute(
            "select 1 from sqlite_schema where type='table' and name=?", (table,)
        ).fetchone() is None for table in SOURCE_TABLES)
        assert unrelated_snapshot(db) == unrelated and service_snapshot(db) == service
        assert action_snapshot(db, action_columns) == retained
        assert waters(db, base_waters) == base_waters
        assert waters(db, REPLY_SEQUENCE_NAMES.values()) == {
            REPLY_SEQUENCE_NAMES[t]: reply_waters[t] for t in SOURCE_TABLES}
        assert_expected_reply(db, variant, expected)
        assert {table: queue_snapshot(db, table, reply_action_projection(table, variant))
                for table in SOURCE_TABLES} == source_queues
        consistency(db, baseline_fk)
        start = max((row[0] for row in expected["reply_attempts"][1]), default=0)
        values = {
            "conversation_id":"copy", "conversation_title":"copy", "trigger_message_id":"copy-new",
            "trigger_sender":"copy", "trigger_text":"copy source", "action":"agent_run",
            "sensitivity_kind":"ordinary", "send_status":"pending", "codex_reason":"adopted",
            "audit_summary":"adopted summary",
            "draft_reply_text":"draft\x00body", "final_reply_text":"final\x00body",
            "corrected_reply_text":"corrected\x00body", "oa_action_result_json":"raw\x00oa",
            "calendar_response_result_json":"broken {", "mail_action_result_json":"raw\x00mail",
            "reaction_action_result_json":"raw\x00reaction",
            "document_action_result_json":"raw\x00document",
            "skill_update_receipts_json":"raw\x00skill",
            "audit_tool_events_json":json.dumps([
                {"tool":"audit_review","result":"queued"}, {"tool":"detail","result":"private"},
                {"tool":"audit_review","result":"queued"}])
        }
        first = insert_action(db, "reply_attempts", values).lastrowid
        second = insert_action(db, "reply_attempts", {
            **values, "trigger_message_id":"copy-second", "audit_tool_events_json":"not JSON"}).lastrowid
        deleted = insert_action(db, "reply_attempts", {
            **values, "trigger_message_id":"copy-deleted"}).lastrowid
        assert first > start and second > first and deleted > second
        insert_action(db, "memory_write_events", {
            "attempt_id":first, "event_type":"copy", "status":"pending", "attempts":2,
            "last_error":"raw\x00error", "memory_episode_id":"fake",
            "created_at":"2026-01-01 01:02:03", "updated_at":"2026-01-02 03:04:05"})
        memory_id = db.execute(
            f"select max(id) from ({reply_action_projection('memory_write_events', variant)})"
        ).fetchone()[0]
        update_action(db, "reply_attempts", first, {
            "final_reply_text":"updated\x00final", "agent_run_id":None})
        update_action(db, "memory_write_events", memory_id, {
            "attempt_id":second, "status":"written", "attempts":3})
        delete_action(db, "reply_attempts", deleted)
        assert db.execute(
            f"select 1 from ({reply_action_projection('reply_attempts', variant)}) where id=?",
            (deleted,)).fetchone() is None
        db.commit()
        current = full_snapshot(db, variant, action_columns)
    release.rollback()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert capture_action_reply_schema(db) == source_schema
        assert marker(db) == source_marker
        assert unrelated_snapshot(db) == unrelated and service_snapshot(db) == service
        assert action_snapshot(db, action_columns) == retained
        assert waters(db, base_waters) == base_waters
        assert waters(db, SOURCE_TABLES) == {
            table: current[4][REPLY_SEQUENCE_NAMES[table]] for table in SOURCE_TABLES}
        for table in SOURCE_TABLES:
            columns = current[3][table][0]
            assert rowset(
                db, f"select {','.join(map(quote, columns))} from {table} order by id"
            ) == current[3][table]
        assert db.execute(
            "select 1 from reply_attempts where id=?", (deleted,)).fetchone() is None
        assert db.execute(
            "select payload_json from memory_write_events where id=?",
            (memory_id,)).fetchone() == ("{}",)
        assert db.execute(
            "select audit_tool_events_json from reply_attempts where id=?",
            (first,)).fetchone() == (
            '[{"tool":"audit_review","result":"queued"},{"tool":"audit_review","result":"queued"}]',)
        consistency(db, baseline_fk)
        inverse_snapshot = (
            unrelated_snapshot(db), service_snapshot(db), action_snapshot(db, action_columns),
            {table: rowset(db, f"select * from {table} order by id") for table in SOURCE_TABLES},
            waters(db, (*base_waters, *SOURCE_TABLES)), marker(db), consistency(db))
    old_process(oldroot, path, "read", (first, second))
    with closing(sqlite3.connect(path)) as db:
        after_read = (
            unrelated_snapshot(db), service_snapshot(db), action_snapshot(db, action_columns),
            {table: rowset(db, f"select * from {table} order by id") for table in SOURCE_TABLES},
            waters(db, (*base_waters, *SOURCE_TABLES)), marker(db), consistency(db))
        assert after_read == inverse_snapshot, "old constructor changed copy facts"
    written = old_process(oldroot, path, "write")
    assert written["attempt_id"] > deleted
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "select status,memory_episode_id from memory_write_events where id=?",
            (written["memory_id"],)).fetchone() == ("written", "copy-only-fake-episode")
        old_expected = source_reply_rows(db)
        old_waters = waters(db, SOURCE_TABLES)
        assert unrelated_snapshot(db) == unrelated and service_snapshot(db) == service
        consistency(db, baseline_fk)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert marker(db)[0] == reply_release.ACTION_REPLY_VERSION
        assert action_schema_is_current(db, include_reply=True, reply_variant=variant)
        assert unrelated_snapshot(db) == unrelated and service_snapshot(db) == service
        assert action_snapshot(db, action_columns) == retained
        assert waters(db, base_waters) == base_waters
        assert waters(db, REPLY_SEQUENCE_NAMES.values()) == {
            REPLY_SEQUENCE_NAMES[t]: old_waters[t] for t in SOURCE_TABLES}
        assert_expected_reply(db, variant, old_expected)
        consistency(db, baseline_fk)
        tables_after = db.execute(
            "select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'"
        ).fetchone()[0]
        assert tables_after == tables_before - 2
        return {
            "source_variant":source_schema["source_variant"],
            "source_schema_sha256":source_schema_sha256,
            "source_attempts":source_counts["reply_attempts"],
            "source_memory_events":source_counts["memory_write_events"],
            "source_tables":tables_before, "target_tables":tables_after,
            "source_file_bytes":source_file_bytes,
            "target_file_bytes":path.stat().st_size,
            "baseline_fk_findings":len(baseline_fk),
            "synthetic_attempts_after":db.execute(
                f"select count(*) from ({reply_action_projection('reply_attempts', variant)})"
            ).fetchone()[0] - source_counts["reply_attempts"],
            "synthetic_memory_after":db.execute(
                f"select count(*) from ({reply_action_projection('memory_write_events', variant)})"
            ).fetchone()[0] - source_counts["memory_write_events"],
            "old_code_read_unchanged":True, "old_code_public_write_receipt":True,
            "current_fact_inverse":True, "republish_values_types_waters_equal":True
        }


def run(source, source_ref, repo):
    production_start = subprocess.check_output(
        ["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    candidate = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    source_hashes = {
        name:hashlib.sha256((repo / name).read_bytes()).hexdigest()
        for name in ("app/action_reply_storage.py", "app/action_reply_schema_release.py",
                     "app/action_storage.py", "app/store.py", "app/store_schema_release.py")
    }
    with TemporaryDirectory(prefix="ceo-phase5e-copy.", dir="/tmp") as temporary:
        root = Path(temporary)
        oldroot = root / "old-code"
        oldroot.mkdir()
        archive = subprocess.check_output(
            ["git", "-C", str(repo), "archive", source_ref, "app"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(oldroot, filter="data")
        actual = root / "actual.sqlite3"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live, \
                closing(sqlite3.connect(actual)) as copy:
            live.backup(copy)
        actual_result = case(repo, oldroot, actual)
        fresh = root / "old-fresh.sqlite3"
        subprocess.run([
            sys.executable, "-c",
            "from pathlib import Path; import sys; from app.store import AutoReplyStore; "
            "from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
            str(fresh)], cwd=oldroot, env={**os.environ,"PYTHONPATH":str(oldroot)},
            capture_output=True, check=True, timeout=300)
        fresh_result = case(repo, oldroot, fresh)
    production_end = subprocess.check_output(
        ["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    return {
        "schema_version":1, "scope":"OFFLINE Phase5E paired constructor copy acceptance",
        "observed_at":datetime.now(timezone.utc).isoformat(),
        "candidate_commit":candidate, "actual_old_source_ref":source_ref,
        "production_head_start":production_start, "production_head_end":production_end,
        "production_binding":"Non-atomic: online SQLite backup and production checkout HEAD were read separately; exact Store18/Email51 and source schema fingerprints bind the copy.",
        "candidate_source_sha256":source_hashes,
        "cases":{"actual_online_backup":actual_result,"old_fresh_constructor":fresh_result},
        "checks":{"owned_temporary_copies_cleaned":True,"provider_calls":0,
                  "production_mutations":0,"native_cli_calls":0,
                  "all_copy_assertions_passed":True},
        "evidence_limit":"Private rows, row hashes, archived code and copied databases were transient and deleted. Offline storage compatibility only; no native role, provider, CI, deployment or live readback claim."
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    proof = run(args.source.resolve(), args.source_ref, Path(__file__).resolve().parents[1])
    args.evidence.write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"actual":proof["cases"]["actual_online_backup"],
                      "fresh":proof["cases"]["old_fresh_constructor"],"cleaned":True}))
