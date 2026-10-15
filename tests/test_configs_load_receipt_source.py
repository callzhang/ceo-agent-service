"""Published source contract for the two immutable managed Skill receipt families.

Every Store call below runs from the archived published code against an owned
fresh database. Explicit setup SQL is marked separately from public writers.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

pytest_plugins = ("test_native_copy_lifecycle_harness",)

PROBE = Path(__file__).resolve().parents[1] / (
    "docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json"
)


def _archived(old_email_copy, script):
    _, db_path = old_email_copy
    old_root = db_path.parent / "published-old"
    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path)],
        cwd=old_root, env={**os.environ, "PYTHONPATH": str(old_root)},
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout)


def _columns(db, table):
    names = ("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden")
    return [dict(zip(names, row, strict=True)) for row in db.execute(
        f"pragma table_xinfo({table})"
    )]


def test_receipt_declarations_match_frozen_actual_in_published_fresh(old_email_copy):
    """The two source families share literal schema, but allocate separately."""
    frozen = json.loads(PROBE.read_text())["actual_frozen_receipt_metadata"]
    _, path = old_email_copy
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        for table, expected in frozen.items():
            actual_objects = [
                {"type": kind, "name": name, "sql": sql}
                for kind, name, sql in db.execute(
                    "select type,name,sql from sqlite_schema "
                    "where tbl_name=? and type in ('table','index','trigger') "
                    "order by type,name", (table,)
                )
            ]
            assert actual_objects == expected["objects"]
            assert _columns(db, table) == expected["columns"]
            assert [list(row) for row in db.execute(
                f"pragma foreign_key_list({table})"
            )] == expected["foreign_keys"]
            assert db.execute(
                "select seq from sqlite_sequence where name=?", (table,)
            ).fetchone() is None

        # Native SQL setup only; its explicit IDs prove independent receipt
        # AUTOINCREMENT streams without touching the frozen source database.
        db.execute("begin")
        skill = db.execute(
            "insert into managed_skills(name,display_name) "
            "values('receipt-metadata','Receipt Metadata')"
        ).lastrowid
        revision = (db.execute(
            "insert into managed_skill_revisions"
            "(skill_id,revision_number,content,sha256,source) "
            "values(?,1,'body','digest','test')", (skill,)
        ).lastrowid,)
        config = (db.execute(
            "insert into runtime_skill_configs(status) values('pending_restart')"
        ).lastrowid,)
        db.execute(
            "insert into managed_skill_export_receipts(id,revision_id,sha256,path) "
            "values(7000,?,'digest','path')", revision,
        )
        db.execute(
            "insert into runtime_skill_load_receipts(id,config_id,pid) "
            "values(9000,?,7)", config,
        )
        export_id = db.execute(
            "insert into managed_skill_export_receipts(revision_id,sha256,path) "
            "values(?,'digest','path')", revision,
        ).lastrowid
        load_id = db.execute(
            "insert into runtime_skill_load_receipts(config_id,pid) values(?,7)", config,
        ).lastrowid
        assert (export_id, load_id) == (7001, 9001)
        assert dict(db.execute(
            "select name,seq from sqlite_sequence where name in "
            "('managed_skill_export_receipts','runtime_skill_load_receipts')"
        )) == {
            "managed_skill_export_receipts": 7001,
            "runtime_skill_load_receipts": 9001,
        }
        db.rollback()


def test_archived_public_load_writer_preserves_exact_bytes_and_identity(old_email_copy):
    result = _archived(old_email_copy, r'''
import json, sqlite3, sys
from pathlib import Path
from app.store import AutoReplyStore
p = Path(sys.argv[1]); store = AutoReplyStore(p)
with sqlite3.connect(p) as db:
    db.execute("pragma foreign_keys=on")
    parent_row = db.execute("select id from runtime_skill_configs order by id desc limit 1").fetchone()
parent = parent_row[0] if parent_row else None
empty = store.create_runtime_skill_config({}, expected_parent_id=parent)
empty_receipt = store.record_runtime_skill_load(empty.id, pid=8123, loaded={})
# Native SQL setup supplies exact 20/100 IDs and one synthetic Unicode digest.
# The public revision validator is not being tested by these inserted rows.
with sqlite3.connect(p) as db:
    db.execute("pragma foreign_keys=on")
    for skill_id, digest in ((20, "dígést"), (100, "alpha"), (30, "disabled")):
        db.execute("insert into managed_skills(id,name,display_name) values(?,?,?)",
                   (skill_id, f"source-skill-{skill_id}", "Source"))
        db.execute("insert into managed_skill_revisions(skill_id,revision_number,content,sha256,source) "
                   "values(?,1,'source body',?,'test')", (skill_id, digest))
    revisions = dict(db.execute("select skill_id,id from managed_skill_revisions "
                                "where skill_id in (20,100,30)"))
    db.commit()
before = store.get_runtime_skill_config(empty.id)
config = store.create_runtime_skill_config([
    {"skill_id": 100, "revision_id": revisions[100], "enabled": True, "load_order": 2},
    {"skill_id": 30, "revision_id": revisions[30], "enabled": False, "load_order": 1},
    {"skill_id": 20, "revision_id": revisions[20], "enabled": True, "load_order": 0},
], expected_parent_id=empty.id)
loaded = {100: "alpha", 20: "dígést"}
first = store.record_runtime_skill_load(config.id, pid=8123, loaded=loaded)
second = store.record_runtime_skill_load(config.id, pid=8123, loaded=loaded)
listed = store.list_runtime_skill_load_receipts(config.id)
latest = store.latest_runtime_skill_load_receipt_for_pid(8123)
with sqlite3.connect(p) as db:
    rows = db.execute("select id,typeof(loaded_json),loaded_json,error from "
                      "runtime_skill_load_receipts where config_id=? order by id", (config.id,)).fetchall()
    full_rows = db.execute(
        "select id,config_id,pid,loaded_json,error,created_at from "
        "runtime_skill_load_receipts where config_id=? order by id", (config.id,)
    ).fetchall()
listed_payload = [r.__dict__.copy() for r in listed]
latest_payload = latest.__dict__.copy()
print(json.dumps({"empty": [empty_receipt.id,empty_receipt.loaded_json,empty_receipt.error],
                  "before": before.status,
                  "config": [config.id,store.get_runtime_skill_config(config.id).status],
                  "receipts": [first.__dict__,second.__dict__],
                  "bindings": [[b.skill_id,b.enabled,b.load_order] for b in
                               store.list_runtime_skill_bindings(config.id)],
                  "listed_ids": [r.id for r in listed], "latest_id": latest.id,
                  "listed": listed_payload, "latest": latest_payload,
                  "rows": rows, "full_rows": full_rows}))
''')
    assert result["empty"][1:] == ["{}", ""]
    assert result["before"] == "active"
    assert result["config"][1] == "active"
    assert result["bindings"] == [[20, True, 0], [30, False, 1], [100, True, 2]]
    expected = json.dumps({20: "dígést", 100: "alpha"}, sort_keys=True)
    assert expected == '{"20": "d\\u00edg\\u00e9st", "100": "alpha"}'
    assert expected != json.dumps({"20": "dígést", "100": "alpha"}, sort_keys=True)
    ids = [receipt["id"] for receipt in result["receipts"]]
    assert len(ids) == len(set(ids)) == 2
    assert result["listed_ids"] == ids
    assert result["latest_id"] == ids[-1]
    fields = ("id", "config_id", "pid", "loaded_json", "error", "created_at")
    persisted = [dict(zip(fields, row, strict=True)) for row in result["full_rows"]]
    assert result["receipts"] == persisted
    assert result["listed"] == persisted
    assert result["latest"] == persisted[-1]
    assert all(row["config_id"] == result["config"][0] and
               row["pid"] == 8123 and row["created_at"] for row in persisted)
    for receipt, (row_id, value_type, body, error) in zip(
        result["receipts"], result["rows"], strict=True,
    ):
        assert (receipt["id"], receipt["loaded_json"], receipt["error"]) == (
            row_id, expected, "",
        )
        assert (value_type, body, error) == ("text", expected, "")
        assert body.encode("utf-8") == expected.encode("utf-8")


def test_archived_public_rejection_and_failure_receipt_are_atomic(old_email_copy):
    result = _archived(old_email_copy, r'''
import json, sqlite3, sys
from pathlib import Path
from app.store import AutoReplyStore
p = Path(sys.argv[1]); store = AutoReplyStore(p)
skill = store.create_managed_skill("source-reject", "Source Reject")
revision = store.create_managed_skill_revision(
    skill.id, "---\nname: source-reject\ndescription: Test\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# Test\n", source="settings")
with sqlite3.connect(p) as db:
    latest = db.execute("select id from runtime_skill_configs order by id desc limit 1").fetchone()
    parent = latest[0] if latest else None
other = store.create_runtime_skill_config({}, expected_parent_id=parent)
other_receipt = store.record_runtime_skill_load(other.id, pid=8124, loaded={})
config = store.create_runtime_skill_config({skill.id: revision.id}, expected_parent_id=other.id)
rejections = []
for loaded in ({}, {skill.id: revision.sha256, 999: "extra"},
               {skill.id: "wrong"}):
    try:
        store.record_runtime_skill_load(config.id, pid=8124, loaded=loaded)
    except ValueError as exc:
        rejections.append(str(exc))
    else:
        raise AssertionError("invalid public receipt was accepted")
with sqlite3.connect(p) as db:
    after_reject = (db.execute("select status from runtime_skill_configs where id=?",
                               (config.id,)).fetchone()[0],
                    db.execute("select count(*) from runtime_skill_load_receipts "
                               "where config_id=?", (config.id,)).fetchone()[0])
failed = store.record_runtime_skill_load_failure(config.id, pid=8124, error="load failed é")
with sqlite3.connect(p) as db:
    persisted = db.execute("select typeof(loaded_json),loaded_json,error,pid "
                           "from runtime_skill_load_receipts where id=?", (failed.id,)).fetchone()
    full_row = db.execute(
        "select id,config_id,pid,loaded_json,error,created_at from "
        "runtime_skill_load_receipts where id=?", (failed.id,)
    ).fetchone()
before_count = db.execute("select count(*) from runtime_skill_configs").fetchone()[0]
try:
    store.create_runtime_skill_config(
        {skill.id: 999999}, expected_parent_id=config.id)
except ValueError as exc:
    invalid_config = str(exc)
else:
    raise AssertionError("invalid revision binding was accepted")
with sqlite3.connect(p) as db:
    after_count = db.execute("select count(*) from runtime_skill_configs").fetchone()[0]
print(json.dumps({"rejections": rejections, "after_reject": after_reject,
                  "failure": failed.__dict__, "persisted": persisted,
                  "full_row": full_row,
                  "status": store.get_runtime_skill_config(config.id).status,
                  "latest_id": store.latest_runtime_skill_load_receipt_for_pid(8124).id,
                  "latest": store.latest_runtime_skill_load_receipt_for_pid(8124).__dict__,
                  "other_id": other_receipt.id,
                  "listed_ids": [r.id for r in store.list_runtime_skill_load_receipts(config.id)],
                  "listed": [r.__dict__ for r in store.list_runtime_skill_load_receipts(config.id)],
                  "config_id": config.id,
                  "invalid_config": invalid_config,
                  "config_counts": [before_count, after_count]}))
''')
    assert result["rejections"] == [
        "runtime Skill load receipt does not match bindings",
    ] * 3
    assert result["after_reject"] == ["pending_restart", 0]
    assert result["persisted"] == ["text", "{}", "load failed é", 8124]
    assert result["failure"]["loaded_json"] == "{}"
    assert result["failure"]["error"] == "load failed é"
    assert result["status"] == "load_failed"
    assert result["latest_id"] == result["failure"]["id"]
    assert result["other_id"] != result["failure"]["id"]
    assert result["listed_ids"] == [result["failure"]["id"]]
    fields = ("id", "config_id", "pid", "loaded_json", "error", "created_at")
    persisted = dict(zip(fields, result["full_row"], strict=True))
    assert persisted == result["failure"] == result["latest"]
    assert result["listed"] == [persisted]
    assert persisted["config_id"] == result["config_id"]
    assert persisted["pid"] == 8124 and persisted["created_at"]
    assert persisted["loaded_json"].encode("utf-8") == b"{}"
    assert result["invalid_config"] == (
        "runtime Skill binding references invalid managed Skill revision"
    )
    assert result["config_counts"][0] == result["config_counts"][1]


def test_native_sql_body_counterexample_and_late_binding_insert(old_email_copy):
    result = _archived(old_email_copy, r'''
import json, sqlite3, sys
from pathlib import Path
from app.store import AutoReplyStore
p = Path(sys.argv[1]); store = AutoReplyStore(p)
skill = store.create_managed_skill("late-binding", "Late Binding")
revision = store.create_managed_skill_revision(
    skill.id, "---\nname: late-binding\ndescription: Test\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n# Test\n", source="settings")
with sqlite3.connect(p) as db:
    db.execute("pragma foreign_keys=on")
    latest = db.execute("select id from runtime_skill_configs order by id desc limit 1").fetchone()
parent = latest[0] if latest else None
config = store.create_runtime_skill_config({}, expected_parent_id=parent)
receipt = store.record_runtime_skill_load(config.id, pid=8125, loaded={})
with sqlite3.connect(p) as db:
    db.execute("pragma foreign_keys=on")
    skill_id, revision_id, sha = skill.id, revision.id, revision.sha256
    # Source triggers forbid UPDATE/DELETE of bindings, but allow this late INSERT.
    db.execute("insert into runtime_skill_bindings"
               "(config_id,skill_id,revision_id,enabled,load_order) "
               "values(?,?,?,1,0)", (config.id, skill_id, revision_id))
    fixed = db.execute("select loaded_json from runtime_skill_load_receipts where id=?",
                       (receipt.id,)).fetchone()[0]
    derived = json.dumps({skill_id: sha}, sort_keys=True)
    # Native SQL accepts malformed TEXT and BLOB bodies. Neither has passed
    # through the public writer or its exact-map validation.
    noncanonical_config = db.execute(
        "insert into runtime_skill_configs(parent_id,status) "
        "values(?,'pending_restart')", (config.id,)
    ).lastrowid
    db.execute("insert into runtime_skill_load_receipts"
               "(config_id,pid,loaded_json,error) values(?,8126,' { } ','')",
               (noncanonical_config,))
    db.execute("update runtime_skill_configs set status='active' where id=?",
               (noncanonical_config,))
    noncanonical = db.execute(
        "select loaded_json from runtime_skill_load_receipts where config_id=?",
        (noncanonical_config,)
    ).fetchone()[0]
    pending = db.execute("insert into runtime_skill_configs"
                         "(parent_id,status) values(?,'pending_restart')", (config.id,)).lastrowid
    malformed = db.execute("insert into runtime_skill_load_receipts"
                           "(config_id,pid,loaded_json,error) values(?,8126,?,'')",
                           (pending, '{bad-json')).lastrowid
    blob = db.execute("insert into runtime_skill_load_receipts"
                      "(config_id,pid,loaded_json,error) values(?,8126,?,'')",
                      (pending, sqlite3.Binary(b'\xff'))).lastrowid
    bodies = db.execute("select id,typeof(loaded_json) from runtime_skill_load_receipts "
                        "where id in (?,?) order by id", (malformed, blob)).fetchall()
    try:
        db.execute("update runtime_skill_configs set status='active' where id=?", (pending,))
    except sqlite3.DatabaseError as exc:
        activation_error = str(exc)
    else:
        raise AssertionError("malformed native receipt activated config")
    pending_status = db.execute(
        "select status from runtime_skill_configs where id=?", (pending,)
    ).fetchone()[0]
    db.commit()
print(json.dumps({"fixed": fixed, "derived": derived,
                  "noncanonical": noncanonical, "body_types": bodies,
                  "activation_error": activation_error,
                  "pending_status": pending_status,
                  "binding_count": len(store.list_runtime_skill_bindings(config.id)),
                  "receipt_ids": [r.id for r in store.list_runtime_skill_load_receipts(config.id)]}))
''')
    assert result["fixed"] == "{}"
    assert result["derived"] != result["fixed"]
    assert result["noncanonical"] == " { } "
    assert result["binding_count"] == 1
    assert len(result["receipt_ids"]) == 1
    assert result["body_types"][0][1] == "text"
    assert result["body_types"][1][1] == "blob"
    assert result["activation_error"] == "malformed JSON"
    assert result["pending_status"] == "pending_restart"
