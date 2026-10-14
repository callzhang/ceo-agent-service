"""Read-only preflight for the unselected Email action migration candidate."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app import action_storage
from app.action_email_storage import capture_email_action_source


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


def _schema(db):
    return list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by type,name"))


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
