"""Read-only preflight for the unselected Email action migration candidate."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import ModuleType
from tempfile import TemporaryDirectory

import pytest
from test_email_action_storage import _cases

from app import action_storage
from app.action_email_storage import (
    capture_email_action_source, migrate_email_action_schema,
    restore_email_action_schema, _projection,
)


SPECS = Path(__file__).parents[1] / "docs/superpowers/specs"
PARENTS = json.loads((SPECS / "2026-10-13-database-redesign-email-actions-source.json").read_text())
CHILDREN = json.loads((SPECS / "2026-10-13-database-redesign-email-action-children-source.json").read_text())
PARENT_NAMES = tuple(PARENTS["actual"]["tables"])
CHILD_NAMES = tuple(CHILDREN["actual"]["tables"])
SOURCE_NAMES = (*PARENT_NAMES, *CHILD_NAMES)


@pytest.fixture(params=("actual", "fresh_candidate"))
def source(request):
    return request.param


@pytest.fixture
def db(source):
    parents = PARENTS[source]["tables"]
    children = CHILDREN["actual"]["tables"]
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("create table tasks(id integer primary key, record_type text)")
        connection.execute("create table runs(id integer primary key, record_type text)")
        connection.execute("create table email_classifications(id integer primary key, status text, current_action_plan_id text)")
        connection.execute("create table email_accounts(account_id text primary key)")
        connection.execute("create table email_messages(account_id text, stable_message_identity text, thread_identity text)")
        action_storage.create_action_schema(
            connection, include_reply=True,
            reply_variant="actual_no_memory_fk" if source == "actual" else "fresh_memory_fk",
        )
        definitions = parents | children
        for name in SOURCE_NAMES:
            connection.execute(definitions[name]["sql"])
        for name in SOURCE_NAMES:
            for index in reversed(definitions[name]["indexes"]):
                if index["sql"] is not None:
                    connection.execute(index["sql"])
            for trigger in definitions[name]["triggers"]:
                connection.execute(trigger["sql"])
        external = [item for item in PARENTS[source]["external_schema_references"]
                    if item["type"] == "trigger"]
        external += CHILDREN["actual"]["external_schema_references"]
        for trigger in external:
            connection.execute(trigger["sql"])
        connection.execute("begin") if not connection.in_transaction else None
        yield connection


@pytest.fixture
def historical_text_action_writers():
    """Load the frozen Actions TEXT writer without changing current writers."""
    module_name = "_test_action_storage_68a137490"
    source = subprocess.check_output(
        ["git", "show", "68a137490:app/action_storage.py"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    )
    module = ModuleType(module_name)
    module.__file__ = "68a137490:app/action_storage.py"
    sys.modules[module_name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _patch_historical_text_action_writers(monkeypatch, module):
    for name in (
        "insert_action",
        "insert_email_action",
        "update_email_action",
        "delete_email_action",
    ):
        monkeypatch.setattr(action_storage, name, getattr(module, name))


def _schema(db):
    return list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by type,name"))


def test_forward_inverse_empty_current_facts(db):
    original = _schema(db)
    state = migrate_email_action_schema(db)
    assert action_storage.action_schema_is_current(
        db, include_email=True, include_reply=True,
        reply_variant=state["action_variant"],
    )
    restore_email_action_schema(db, state)
    assert _schema(db) == original


def _insert_case(db, kind, values, *, rowid=None):
    columns = (("rowid",) if rowid is not None else ()) + tuple(values)
    bound = ((rowid,) if rowid is not None else ()) + tuple(values.values())
    return db.execute(
        f"insert into main.{kind}({','.join(columns)}) values({','.join('?' for _ in columns)})",
        bound,
    )


def _source_facts(db):
    facts = {}
    for kind in SOURCE_NAMES:
        columns = tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({kind})") if row[6] == 0)
        names = (("rowid",) if kind in action_storage.EMAIL_ROWID_TYPES else ()) + columns
        order = "rowid" if kind in action_storage.EMAIL_ROWID_TYPES else "id" if "id" in columns else ",".join(action_storage.EMAIL_COMPOSITE_KEYS[kind])
        facts[kind] = (
            columns,
            list(db.execute(f"select {','.join(names)} from main.{kind} order by {order}")),
            list(db.execute(f"select {','.join('typeof(' + name + ')' for name in names)} "
                            f"from main.{kind} order by {order}")),
        )
    return facts


def test_all_thirteen_families_preserve_current_facts(
    db, source, historical_text_action_writers, monkeypatch
):
    db.execute("insert into email_classifications(id,status,current_action_plan_id) values(11,'done','plan')")
    db.execute("insert into email_accounts(account_id) values('account')")
    db.execute("insert into email_messages(account_id,stable_message_identity,thread_identity) "
               "values('account','message','thread')")
    db.execute("insert into main.actions(id,record_type,legacy_key,candidate_id,payload_json) "
               "values(1,'candidate_supplements','1',1,'{\"instruction\":\"old\",\"created_at\":\"now\"}')")
    cases = _cases()
    for kind, values in cases.items():
        _insert_case(db, kind, values)
    db.execute("update main.email_actions set attempt_count=1 where action_id='action'")
    for locator, version in ((-2, 2), (0, 3)):
        _insert_case(db, "email_action_plans", cases["email_action_plans"] |
                     {"action_plan_id": None, "action_plan_version": version}, rowid=locator)
    db.execute("update main.email_reply_receipts set display_excerpt=?",
               (b'\x00raw\xff',))
    db.execute("update main.email_actions set provider_result_id=? where action_id='action'",
               ("invalid JSON\x00raw",))
    db.execute("update main.sqlite_sequence set seq=21 where name='email_action_attempts'")
    db.execute("update main.sqlite_sequence set seq=22 where name='email_unsubscribe_steps'")
    db.execute("insert into main.sqlite_sequence(name,seq) values('actions:reply_attempts',19)")
    original = _source_facts(db)
    state = migrate_email_action_schema(db)
    assert db.execute("select count(*) from main.actions where record_type in (" +
                      ",".join("?" for _ in SOURCE_NAMES) + ")", SOURCE_NAMES).fetchone()[0] == 15
    assert db.execute("select id,payload_json from main.actions where id=1").fetchone()[0] == 1
    _patch_historical_text_action_writers(monkeypatch, historical_text_action_writers)
    action_storage.insert_email_action(
        db, "email_action_plans", cases["email_action_plans"] |
        {"action_plan_id": None, "action_plan_version": 4})
    action_storage.insert_action(db, "email_action_attempts", cases["email_action_attempts"] |
                                 {"attempt_number": 2, "provider_result_id": "second"})
    action_storage.update_email_action(db, "email_actions", 1, {"attempt_count": 2})
    action_storage.insert_action(db, "email_unsubscribe_steps", cases["email_unsubscribe_steps"] |
                                 {"sequence": 2, "reference": "later"})
    action_storage.update_email_action(db, "email_reply_receipts", 1,
                                       {"display_excerpt": "changed\x00text"})
    action_storage.update_email_action(db, "email_unsubscribe_claims", 1,
                                       {"status": "done"})
    action_storage.insert_action(db, "email_direct_retry_requests",
                                 cases["email_direct_retry_requests"] |
                                 {"attempt_number": 2, "requested_at": "later"})
    action_storage.delete_email_action(db, "email_unsubscribe_cancellations", 1)
    db.execute("update main.actions set payload_json=? where id=1",
               ('{"instruction":"current","created_at":"now"}',))
    target_facts = {kind: _projection(db, kind, source) for kind in SOURCE_NAMES}
    old_action = list(db.execute("select id,record_type,legacy_key,payload_json from main.actions "
                                  "where record_type='candidate_supplements'"))
    target_waters = list(db.execute("select name,seq from main.sqlite_sequence "
                                    "where name like 'actions:%' order by name"))
    restore_email_action_schema(db, state)
    current = _source_facts(db)
    assert current != original
    assert len(current["email_action_attempts"][1]) == 2
    assert len(current["email_unsubscribe_steps"][1]) == 2
    assert current["email_unsubscribe_cancellations"][1] == []
    assert db.execute("select seq from main.sqlite_sequence where name='email_action_attempts'").fetchone() == (22,)
    assert db.execute("select seq from main.sqlite_sequence where name='email_unsubscribe_steps'").fetchone() == (23,)
    assert db.execute("select seq from main.sqlite_sequence where name='actions:reply_attempts'").fetchone() == (19,)
    migrate_email_action_schema(db)
    assert {kind: _projection(db, kind, source) for kind in SOURCE_NAMES} == target_facts
    assert list(db.execute("select id,record_type,legacy_key,payload_json from main.actions "
                           "where record_type='candidate_supplements'")) == old_action
    assert list(db.execute("select name,seq from main.sqlite_sequence "
                           "where name like 'actions:%' order by name")) == target_waters


def test_zero_and_absent_source_auto_waters_round_trip(db):
    db.execute("insert into main.sqlite_sequence(name,seq) values('email_action_attempts',0)")
    assert db.execute("select 1 from main.sqlite_sequence where name='email_unsubscribe_steps'").fetchone() is None
    state = migrate_email_action_schema(db)
    assert db.execute("select seq from main.sqlite_sequence where name='actions:email_action_attempts'").fetchone() == (0,)
    assert db.execute("select 1 from main.sqlite_sequence where name='actions:email_unsubscribe_steps'").fetchone() is None
    restore_email_action_schema(db, state)
    assert db.execute("select seq from main.sqlite_sequence where name='email_action_attempts'").fetchone() == (0,)
    assert db.execute("select 1 from main.sqlite_sequence where name='email_unsubscribe_steps'").fetchone() is None


def test_existing_source_foreign_key_finding_is_not_treated_as_new_orphan(db):
    _insert_case(db, "email_action_plans", _cases()["email_action_plans"])
    before = list(db.execute("pragma main.foreign_key_check(email_action_plans)"))
    assert before
    state = migrate_email_action_schema(db)
    assert list(db.execute("pragma main.foreign_key_check(actions)"))
    restore_email_action_schema(db, state)
    assert list(db.execute("pragma main.foreign_key_check(email_action_plans)")) == before


def test_deleted_highest_ordinary_rowid_is_reused_after_forward(
    db, historical_text_action_writers, monkeypatch
):
    case = _cases()["email_action_plans"]
    _insert_case(db, "email_action_plans", case, rowid=1)
    _insert_case(db, "email_action_plans", case | {"action_plan_id": "discarded",
                                                 "action_plan_version": 2}, rowid=9)
    db.execute("delete from main.email_action_plans where rowid=9")
    state = migrate_email_action_schema(db)
    _patch_historical_text_action_writers(monkeypatch, historical_text_action_writers)
    result = action_storage.insert_email_action(
        db, "email_action_plans", case | {"action_plan_id": "new", "action_plan_version": 3})
    assert result.lastrowid == 2
    restore_email_action_schema(db, state)
    assert list(db.execute("select rowid from main.email_action_plans order by rowid")) == [(1,), (2,)]


_BLOCKED_EXTERNAL = {
    "direct": (
        ("delete from main.email_accounts where account_id='account'", "email_direct_action_in_flight"),
        ("update main.email_accounts set account_id=account_id where account_id='account'", "email_direct_action_in_flight"),
        ("update main.email_classifications set status='changed' where id=11", "email_direct_action_in_flight"),
    ),
    "unsubscribe": (
        ("delete from main.email_accounts where account_id='account'", "email_unsubscribe_write_in_flight"),
        ("update main.email_accounts set account_id=account_id where account_id='account'", "email_unsubscribe_write_in_flight"),
        ("delete from main.email_messages where stable_message_identity='message'", "email_unsubscribe_write_in_flight"),
        ("update main.email_messages set thread_identity='changed' where stable_message_identity='message'", "email_unsubscribe_write_in_flight"),
        ("update main.email_classifications set status='changed' where id=11", "email_unsubscribe_write_in_flight"),
    ),
    "reply": (
        ("delete from main.email_accounts where account_id='account'", "email_reply_dispatch_in_flight"),
        ("update main.email_accounts set account_id=account_id where account_id='account'", "email_reply_dispatch_in_flight"),
        ("delete from main.email_messages where stable_message_identity='message'", "email_reply_dispatch_in_flight"),
        ("update main.email_classifications set current_action_plan_id='other' where id=11", "email_reply_dispatch_in_flight"),
        ("update main.email_messages set thread_identity='changed' where stable_message_identity='message'", "email_reply_dispatch_in_flight"),
    ),
}


def _set_external_status(db, kind, status):
    table = {"direct": "email_actions", "unsubscribe": "email_unsubscribe_claims",
             "reply": "email_reply_dispatch_claims"}[kind]
    if db.execute("select 1 from main.sqlite_schema where type='table' and name=?", (table,)).fetchone():
        db.execute(f"update main.{table} set status=?", (status,))
    else:
        db.execute("update main.actions set payload_json=json_set(payload_json,'$.status',?) "
                   "where record_type=?", (status, table))


def _assert_external_blockers(db):
    for active in _BLOCKED_EXTERNAL:
        for kind in _BLOCKED_EXTERNAL:
            _set_external_status(db, kind, "processing" if kind == active == "direct" else
                                 "dispatching" if kind == active else
                                 "pending" if kind == "direct" else "done")
        for number, (sql, error) in enumerate(_BLOCKED_EXTERNAL[active]):
            db.execute(f"savepoint blocker_{number}")
            try:
                with pytest.raises(sqlite3.IntegrityError, match=error):
                    db.execute(sql)
            finally:
                db.execute(f"rollback to blocker_{number}")
                db.execute(f"release blocker_{number}")
        # Changed-value predicates have no effect when the named values stay equal.
        db.execute("update main.email_classifications set status=status,current_action_plan_id=current_action_plan_id where id=11")
        db.execute("update main.email_messages set account_id=account_id,stable_message_identity=stable_message_identity,thread_identity=thread_identity where stable_message_identity='message'")


def test_thirteen_external_blockers_survive_both_transitions(db):
    db.execute("insert into email_classifications(id,status,current_action_plan_id) values(11,'done','plan')")
    db.execute("insert into email_accounts(account_id) values('account')")
    db.execute("insert into email_messages(account_id,stable_message_identity,thread_identity) "
               "values('account','message','thread')")
    cases = _cases()
    for kind in ("email_action_plans", "email_actions", "email_unsubscribe_claims",
                 "email_reply_dispatch_claims"):
        _insert_case(db, kind, cases[kind])
    _assert_external_blockers(db)
    state = migrate_email_action_schema(db)
    _assert_external_blockers(db)
    restore_email_action_schema(db, state)
    _assert_external_blockers(db)


def test_main_rows_and_temp_shadows_stay_separate_through_inverse(db, source):
    db.execute("create temp table actions(id integer, record_type text)")
    db.execute("create temp table email_action_plans(note blob)")
    db.execute("insert into temp.actions values(77,'shadow')")
    db.execute("insert into temp.email_action_plans values(?)", (b'\x00temp',))
    db.execute("create index temp.idx_email_actions_status on actions(record_type)")
    db.execute("create index temp.idx_actions_email_public_key on actions(record_type)")
    db.execute("create temp trigger trg_email_direct_action_blocks_account_delete "
               "before delete on actions begin select 1; end")
    _insert_case(db, "email_unsubscribe_cancellations",
                 {"action_identity": "main", "decision_json": "{}"}, rowid=-4)
    main_before = _schema(db)
    temp_before = list(db.execute("select type,name,sql from temp.sqlite_schema order by type,name"))
    state = migrate_email_action_schema(db)
    assert _projection(db, "email_unsubscribe_cancellations", source)[1][0][0] == -4
    restore_email_action_schema(db, state)
    assert db.execute("select rowid,action_identity from main.email_unsubscribe_cancellations").fetchone() == (-4, "main")
    assert db.execute("select * from temp.actions").fetchone() == (77, "shadow")
    assert db.execute("select * from temp.email_action_plans").fetchone() == (b'\x00temp',)
    assert _schema(db) == main_before
    assert list(db.execute("select type,name,sql from temp.sqlite_schema order by type,name")) == temp_before


@pytest.mark.parametrize("attachment", ("unqualified_owner", "explicit_owner", "view_body", "trigger_body"))
def test_forward_refuses_temp_dependency_before_drop(db, attachment):
    if attachment == "unqualified_owner":
        db.execute("create temp trigger temp_source_guard before update on email_unsubscribe_cancellations "
                   "begin select 1; end")
    elif attachment == "explicit_owner":
        db.execute("create temp trigger temp_source_guard before update on main.email_unsubscribe_cancellations "
                   "begin select 1; end")
    elif attachment == "view_body":
        db.execute("create temp view temp_source_observer as select action_identity "
                   "from main.email_unsubscribe_cancellations")
    else:
        db.execute("create temp table unrelated_owner(id integer)")
        db.execute("create temp trigger temp_source_guard before update on unrelated_owner "
                   "begin select count(*) from main.email_unsubscribe_cancellations; end")
    main_before = _schema(db)
    temp_before = list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid"))
    with pytest.raises(ValueError, match="dependency"):
        migrate_email_action_schema(db)
    assert _schema(db) == main_before
    assert list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid")) == temp_before


@pytest.mark.parametrize("attachment", ("unqualified_owner", "explicit_owner", "view_body", "trigger_body"))
def test_inverse_refuses_temp_dependency_before_drop(db, attachment):
    state = migrate_email_action_schema(db)
    if attachment == "unqualified_owner":
        db.execute("create temp trigger temp_target_guard before update on actions "
                   "begin select 1; end")
    elif attachment == "explicit_owner":
        db.execute("create temp trigger temp_target_guard before update on main.actions "
                   "begin select 1; end")
    elif attachment == "view_body":
        db.execute("create temp view temp_target_observer as select id from main.actions")
    else:
        db.execute("create temp table unrelated_owner(id integer)")
        db.execute("create temp trigger temp_target_guard before update on unrelated_owner "
                   "begin select count(*) from main.actions; end")
    main_before = _schema(db)
    temp_before = list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid"))
    with pytest.raises(ValueError, match="dependency"):
        restore_email_action_schema(db, state)
    assert _schema(db) == main_before
    assert list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid")) == temp_before


def test_temp_rename_chronology_rebinds_unqualified_owner_without_refusal(db):
    db.execute("create temp table earlier_name(id integer)")
    db.execute("create temp trigger temp_observer before update on actions begin select 1; end")
    db.execute("alter table temp.earlier_name rename to actions")
    before = list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid"))
    state = migrate_email_action_schema(db)
    assert list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid")) == before
    restore_email_action_schema(db, state)
    assert list(db.execute("select type,name,tbl_name,sql from temp.sqlite_schema order by rowid")) == before


def test_source_capture_with_temp_is_readonly_on_file_backed_main(db, source):
    with TemporaryDirectory(prefix="email-action-3a2-readonly-", dir="/tmp") as directory:
        path = Path(directory) / "source.sqlite3"
        contents = db.serialize(name="main")
        path.write_bytes(contents)
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as readonly:
            readonly.execute("create temp table actions(record_type text)")
            readonly.execute("create temp table unrelated_owner(id integer)")
            readonly.execute("create temp trigger unrelated_guard before update on unrelated_owner "
                             "begin select 1; end")
            temp_before = list(readonly.execute(
                "select type,name,tbl_name,sql from temp.sqlite_schema order by rowid"))
            readonly.execute("pragma query_only=on")
            captured = capture_email_action_source(readonly)
            assert captured["email_variant"] == source
            assert readonly.execute("pragma query_only").fetchone() == (1,)
            assert list(readonly.execute(
                "select type,name,tbl_name,sql from temp.sqlite_schema order by rowid")) == temp_before
        assert path.read_bytes() == contents


def test_inverse_rejects_external_dependency_before_mutation(db):
    state = migrate_email_action_schema(db)
    db.execute("create view outside_email as select id from main.actions")
    before = _schema(db)
    with pytest.raises(ValueError, match="dependency"):
        restore_email_action_schema(db, state)
    assert _schema(db) == before
    assert db.in_transaction


@pytest.mark.parametrize("damage", ("unknown_alias", "changed_blocker", "source_name"))
def test_inverse_rejects_unknown_target_state_before_mutation(db, damage):
    state = migrate_email_action_schema(db)
    if damage == "unknown_alias":
        db.execute("insert into main.sqlite_sequence(name,seq) values('actions:email_unknown',7)")
    elif damage == "changed_blocker":
        db.execute("drop trigger main.trg_email_direct_action_blocks_account_delete")
        db.execute("create trigger trg_email_direct_action_blocks_account_delete "
                   "before delete on email_accounts begin select 1; end")
    else:
        db.execute("create table email_action_plans(note text)")
    before = _schema(db)
    before_seq = list(db.execute("select name,seq from main.sqlite_sequence order by name"))
    with pytest.raises(ValueError):
        restore_email_action_schema(db, state)
    assert _schema(db) == before
    assert list(db.execute("select name,seq from main.sqlite_sequence order by name")) == before_seq
    assert db.in_transaction


def test_caller_can_rollback_forward_after_late_sqlite_failure(db):
    _insert_case(db, "email_unsubscribe_cancellations",
                 {"action_identity": "main", "decision_json": "{}"})
    before_schema = _schema(db)
    before_facts = _source_facts(db)
    db.execute("savepoint attempt")
    def deny_copy(action, name, _second, _database, _source):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and name == "actions" else sqlite3.SQLITE_OK
    db.set_authorizer(deny_copy)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            migrate_email_action_schema(db)
    finally:
        db.set_authorizer(None)
        db.execute("rollback to attempt")
        db.execute("release attempt")
    assert _schema(db) == before_schema
    assert _source_facts(db) == before_facts
    assert db.in_transaction


def test_caller_can_rollback_inverse_after_late_sqlite_failure(db, source):
    _insert_case(db, "email_unsubscribe_cancellations",
                 {"action_identity": "main", "decision_json": "{}"})
    state = migrate_email_action_schema(db)
    before_schema = _schema(db)
    before_fact = _projection(db, "email_unsubscribe_cancellations", source)
    db.execute("savepoint inverse_attempt")
    def deny_copy(action, name, _second, _database, _source):
        return (sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and
                name == "email_unsubscribe_cancellations" else sqlite3.SQLITE_OK)
    db.set_authorizer(deny_copy)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            restore_email_action_schema(db, state)
    finally:
        db.set_authorizer(None)
        db.execute("rollback to inverse_attempt")
        db.execute("release inverse_attempt")
    assert _schema(db) == before_schema
    assert _projection(db, "email_unsubscribe_cancellations", source) == before_fact
    assert db.in_transaction


def test_capture_exact_source_and_absent_sequence(db, source):
    before = _schema(db)
    captured = capture_email_action_source(db)
    assert captured["email_variant"] == source
    assert captured["action_variant"] == (
        "actual_no_memory_fk" if source == "actual" else "fresh_memory_fk")
    assert set(captured["source_tables"]) == set(SOURCE_NAMES)
    assert sum(captured["counts"].values()) == 0
    assert captured["sequences"]["email_action_attempts"] is None
    assert captured["sequences"]["email_unsubscribe_steps"] is None
    assert len(captured["external_triggers"]) == 13
    assert len(captured["source_actions"]) > 1
    assert _schema(db) == before


def test_capture_preserves_zero_and_highwater_metadata(db):
    db.execute("insert into sqlite_sequence(name,seq) values('email_action_attempts',0)")
    db.execute("insert into sqlite_sequence(name,seq) values('email_unsubscribe_steps',57)")
    before = _schema(db)
    captured = capture_email_action_source(db)
    assert captured["sequences"]["email_action_attempts"] == 0
    assert captured["sequences"]["email_unsubscribe_steps"] == 57
    assert _schema(db) == before


def test_capture_keeps_source_rowid_separate_from_existing_action_id(db):
    db.execute("insert into actions(id,record_type,legacy_key,candidate_id,payload_json) "
               "values(1,'candidate_supplements','1',1,'{\"instruction\":\"x\",\"created_at\":\"now\"}')")
    db.execute("insert into email_action_plans(rowid,action_plan_id,action_plan_version,"
               "classification_id,account_id,category,classification_source,confidence,"
               "model_id,config_version,actions_json,action_parameters_json,created_at) "
               "values(1,'plan',1,11,'account','category','model',0.5,'model','config','[]','{}','now')")
    db.execute("insert into email_action_plans(rowid,action_plan_id,action_plan_version,"
               "classification_id,account_id,category,classification_source,confidence,"
               "model_id,config_version,actions_json,action_parameters_json,created_at) "
               "values(17,'gone',2,11,'account','category','model',0.5,'model','config','[]','{}','now')")
    db.execute("delete from email_action_plans where rowid=17")
    captured = capture_email_action_source(db)
    assert captured["counts"]["email_action_plans"] == 1
    assert captured["locator_modes"]["email_action_plans"] == "rowid"
    assert db.execute("select rowid from email_action_plans").fetchone()[0] == 1
    assert db.execute("select id from actions").fetchone()[0] == 1


def test_rejects_cross_pair_variant(db, source):
    db.execute("drop table actions")
    action_storage.create_action_schema(
        db, include_reply=True,
        reply_variant="fresh_memory_fk" if source == "actual" else "actual_no_memory_fk",
    )
    before = _schema(db)
    with pytest.raises(ValueError, match="source declaration pair"):
        capture_email_action_source(db)
    assert _schema(db) == before


def test_main_inbound_reference_cannot_be_hidden_by_temp_table(db):
    db.execute("create table external_child(id integer references email_action_attempts(id))")
    db.execute("create temp table external_child(id integer)")
    before = _schema(db)
    with pytest.raises(ValueError, match="inbound"):
        capture_email_action_source(db)
    assert _schema(db) == before


def test_temp_source_row_does_not_change_main_count(db, source):
    sql = PARENTS[source]["tables"]["email_unsubscribe_cancellations"]["sql"]
    db.execute(sql.replace("CREATE TABLE", "CREATE TEMP TABLE", 1))
    db.execute("insert into temp.email_unsubscribe_cancellations values('shadow','{}')")
    before = _schema(db)
    captured = capture_email_action_source(db)
    assert captured["counts"]["email_unsubscribe_cancellations"] == 0
    assert db.execute("select count(*) from temp.email_unsubscribe_cancellations").fetchone()[0] == 1
    assert _schema(db) == before


def test_temp_sequence_cannot_hide_main_highwater(db):
    db.execute("insert into main.sqlite_sequence(name,seq) values('email_action_attempts',57)")
    db.execute("create temp table staging(id integer primary key autoincrement)")
    db.execute("insert into temp.staging default values")
    before = _schema(db)
    captured = capture_email_action_source(db)
    assert captured["sequences"]["email_action_attempts"] == 57
    assert _schema(db) == before


def test_temp_declaration_and_actions_rows_cannot_replace_main_capture(db):
    db.execute("create temp table email_action_plans(other text)")
    db.execute("create temp table actions(record_type text)")
    db.execute("insert into temp.actions values('outside')")
    main_before = _schema(db)
    temp_before = list(db.execute("select type,name,sql from temp.sqlite_schema order by type,name"))
    transaction_before = db.in_transaction
    captured = capture_email_action_source(db)
    assert len(captured["source_tables"]["email_action_plans"]["columns"]) == len(
        PARENTS[captured["email_variant"]]["tables"]["email_action_plans"]["columns"])
    assert db.execute("select record_type from temp.actions").fetchone()[0] == "outside"
    assert _schema(db) == main_before
    assert list(db.execute("select type,name,sql from temp.sqlite_schema order by type,name")) == temp_before
    assert db.in_transaction == transaction_before


@pytest.mark.parametrize("mutation", (
    "changed_table", "inbound_table", "outside_view", "outside_trigger",
    "inbound_action_table", "outside_action_view", "outside_action_trigger",
    "target_name_collision", "changed_actions", "changed_external_trigger",
    "email_allocator_alias", "unexpected_sequence",
))
def test_refuses_unsupported_source_without_mutation(db, mutation):
    if mutation == "changed_table":
        db.execute("create index unexpected_email_plan on email_action_plans(category)")
    elif mutation == "inbound_table":
        db.execute("create table external_child(id integer references email_action_attempts(id))")
    elif mutation == "inbound_action_table":
        db.execute("create table external_action_child(id integer references actions(id))")
    elif mutation == "outside_view":
        db.execute("create view unexpected_email_view as select * from email_actions")
    elif mutation == "outside_action_view":
        db.execute("create view unexpected_action_view as select id from actions")
    elif mutation == "outside_trigger":
        db.execute("create trigger unexpected_email_trigger after insert on email_accounts begin select count(*) from email_actions; end")
    elif mutation == "outside_action_trigger":
        db.execute("create trigger unexpected_action_trigger after insert on email_accounts "
                   "begin select count(*) from actions; end")
    elif mutation == "target_name_collision":
        db.execute("create table idx_actions_email_public_key(id integer)")
    elif mutation == "changed_actions":
        db.execute("create index unexpected_actions_idx on actions(id)")
    elif mutation == "changed_external_trigger":
        db.execute("drop trigger trg_email_direct_action_blocks_account_delete")
        db.execute("create trigger trg_email_direct_action_blocks_account_delete "
                   "before delete on email_accounts begin select 1; end")
    elif mutation == "unexpected_sequence":
        db.execute("insert into sqlite_sequence(name,seq) values('email_action_plans',9)")
    else:
        db.execute("insert into sqlite_sequence(name,seq) values('actions:email_actions',3)")
    before_schema = _schema(db)
    before_rows = list(db.execute("select id,record_type,legacy_key from actions"))
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        capture_email_action_source(db)
    assert _schema(db) == before_schema
    assert list(db.execute("select id,record_type,legacy_key from actions")) == before_rows
