"""Literal SQLite oracle for the frozen nine email tables and four children.

The account, message, and classification tables below are deliberately narrow
stubs. Only the thirteen captured tables and thirteen captured external triggers
are asserted; this is not a full EmailStore constructor or production replay.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


SPECS = Path(__file__).parents[1] / "docs/superpowers/specs"
PARENTS = json.loads((SPECS / "2026-10-13-database-redesign-email-actions-source.json").read_text())
CHILDREN = json.loads((SPECS / "2026-10-13-database-redesign-email-action-children-source.json").read_text())
CHILD_NAMES = tuple(CHILDREN["actual"]["tables"])
PARENT_NAMES = tuple(PARENTS["actual"]["tables"])


def insert(db, table, **values):
    return db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def plan(db, name="plan", version=1):
    return insert(db, "email_action_plans", action_plan_id=name,
                  action_plan_version=version, classification_id=11,
                  account_id="account", category="category", classification_source="model",
                  confidence=0.5, model_id="model", config_version="config",
                  actions_json="[]", action_parameters_json="{}", created_at="time")


def action(db, name="action", plan_id="plan", kind="archive"):
    return insert(db, "email_actions", action_id=name, action_plan_id=plan_id,
                  classification_id=11, account_id="account", action_type=kind,
                  parameters_json="{}", config_version="config", status="pending",
                  created_at="time", updated_at="time")


def claim_values(identity="claim"):
    return dict(action_identity=identity, effect_digest="digest", action_plan_id="plan",
                action_plan_version=1, classification_id=11, account_id="account",
                stable_message_identity="message", thread_identity="thread",
                entry_reference="entry", operations_json="[]", owner_id="owner",
                owner_generation=1, lease_token="lease", account_updated_at="time",
                status="dispatching", claimed_at="time", updated_at="time")


def reply_values(identity="reply", outgoing="outgoing"):
    return dict(action_identity=identity, effect_digest="digest", action_plan_id="plan",
                classification_id=11, account_id="account",
                stable_message_identity="message", outgoing_message_id=outgoing)


@pytest.fixture(params=("actual", "fresh_candidate"))
def source(request):
    return request.param


@pytest.fixture
def db(source):
    parents = PARENTS[source]["tables"]
    children = CHILDREN["actual"]["tables"]
    # The frozen fresh_candidate related objects equal these exact child objects.
    for name in CHILD_NAMES:
        related = PARENTS[source]["related_table_objects"][name]
        assert related["foreign_keys"] == children[name]["foreign_keys"]
        assert related["objects"] == sorted(
            [{"type": "table", "name": name, "tbl_name": name, "sql": children[name]["sql"]}]
            + [{"type": "index", "name": item["name"], "tbl_name": name, "sql": item["sql"]}
               for item in children[name]["indexes"]]
            + children[name]["triggers"], key=lambda item: (item["type"], item["name"]),
        )
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        connection.execute("create table email_classifications(id integer primary key, status text, current_action_plan_id text)")
        connection.execute("create table email_accounts(account_id text primary key)")
        connection.execute("create table email_messages(account_id text, stable_message_identity text, thread_identity text)")
        connection.execute("insert into email_classifications(id) values(11)")
        connection.execute("insert into email_accounts(account_id) values('account')")
        connection.execute("insert into email_messages values('account','message','thread')")
        definitions = parents | children
        for name in (*PARENT_NAMES, *CHILD_NAMES):
            connection.execute(definitions[name]["sql"])
        for name in (*PARENT_NAMES, *CHILD_NAMES):
            definition = definitions[name]
            for index in reversed(definition["indexes"]):
                if index["sql"] is not None:
                    connection.execute(index["sql"])
            for trigger in definition["triggers"]:
                connection.execute(trigger["sql"])
        external = [item for item in PARENTS[source]["external_schema_references"] if item["type"] == "trigger"]
        external += CHILDREN["actual"]["external_schema_references"]
        assert len(external) == len({item["name"] for item in external}) == 13
        for trigger in external:
            connection.execute(trigger["sql"])
        yield connection


def test_exact_child_declarations_and_external_trigger_sql(db, source):
    assert len(CHILD_NAMES) == 4
    assert sum(len(CHILDREN["actual"]["tables"][n]["columns"]) for n in CHILD_NAMES) == 41
    for name in CHILD_NAMES:
        definition = CHILDREN["actual"]["tables"][name]
        assert db.execute("select sql from sqlite_schema where type='table' and name=?", (name,)).fetchone()[0] == definition["sql"]
        assert [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"), row, strict=True))
                for row in db.execute(f"pragma table_xinfo({name})")] == definition["columns"]
        assert [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"), row, strict=True))
                for row in db.execute(f"pragma foreign_key_list({name})")] == definition["foreign_keys"]
        assert [dict(zip(("seq", "name", "unique", "origin", "partial"), row, strict=True))
                for row in db.execute(f"pragma index_list({name})")] == [
                    {key: item[key] for key in ("seq", "name", "unique", "origin", "partial")}
                    for item in definition["indexes"]]
        for index in definition["indexes"]:
            assert db.execute("select sql from sqlite_schema where type='index' and name=?", (index["name"],)).fetchone()[0] == index["sql"]
            assert [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), row, strict=True))
                    for row in db.execute(f"pragma index_xinfo({index['name']})")] == index["columns"]
        assert [dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True))
                for row in db.execute("select type,name,tbl_name,sql from sqlite_schema where type='trigger' and tbl_name=? order by name", (name,))] == sorted(definition["triggers"], key=lambda item: item["name"])
    external = [item for item in PARENTS[source]["external_schema_references"] if item["type"] == "trigger"]
    external += CHILDREN["actual"]["external_schema_references"]
    for trigger in external:
        assert db.execute("select sql from sqlite_schema where type='trigger' and name=?", (trigger["name"],)).fetchone()[0] == trigger["sql"]
    definitions = PARENTS[source]["tables"] | CHILDREN["actual"]["tables"]
    incoming = [dict(child_table=name, **fk) for name, definition in definitions.items()
                for fk in definition["foreign_keys"] if fk["table"] in definitions]
    assert sorted(incoming, key=lambda fk: (fk["child_table"], fk["id"])) == PARENTS[source]["incoming_foreign_keys"]
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_nine_nullable_text_public_primary_keys_allow_multiple_nulls(db):
    plan(db)
    plan(db, "other", 2)
    action(db)
    insert(db, "email_unsubscribe_claims", **claim_values())
    rows = [
        ("email_action_plans", "action_plan_id", dict(action_plan_version=10, classification_id=11, account_id="account", category="category", classification_source="model", confidence=0.5, model_id="model", config_version="config", actions_json="[]", action_parameters_json="{}", created_at="time")),
        ("email_actions", "action_id", dict(action_plan_id="plan", classification_id=11, account_id="account", action_type="label", parameters_json="{}", config_version="config", status="pending", created_at="time", updated_at="time")),
        ("email_unsubscribe_cancellations", "action_identity", dict(decision_json="{}")),
        ("email_unsubscribe_claims", "action_identity", claim_values()),
        ("email_unsubscribe_continuations", "action_identity", dict(effect_digest="digest", observation_reference="observation", controls_json="[]", created_at="time", updated_at="time")),
        ("email_unsubscribe_receipts", "action_identity", dict(effect_digest="digest", action_plan_id="plan", action_plan_version=1, classification_id=11, account_id="account", stable_message_identity="message", thread_identity="thread", entry_reference="entry", outcome="done", receipt_id="receipt", evidence="evidence", created_at="time")),
        ("email_feedback_requests", "feedback_request_id", dict(classification_id=11, category="category", resulting_action_plan_id="plan", applied_at="time")),
        ("email_reply_dispatch_claims", "action_identity", reply_values() | dict(owner_id="owner", owner_generation=1, lease_token="lease", sender="sender", thread_identity="thread", account_updated_at="time", account_snapshot_json="{}", status="retryable", claimed_at="time", updated_at="time")),
        ("email_reply_receipts", "action_identity", reply_values() | dict(provider_operation="sent_readback", provider_target="", provider_result_id="receipt", provider_receipt_json="{}", display_excerpt="", created_at="time")),
    ]
    assert len(rows) == 9
    definitions = PARENTS["actual"]["tables"] | CHILDREN["actual"]["tables"]
    assert {(table, key) for table, key, _ in rows} == {
        (name, column["name"]) for name, definition in definitions.items()
        for column in definition["columns"]
        if column["type"] == "TEXT" and column["pk"] == 1 and not column["notnull"]
    }
    for table, key, values in rows:
        for n in range(2):
            candidate = values | {key: None}
            if table == "email_action_plans":
                candidate["action_plan_version"] += n
            if table == "email_actions":
                candidate["action_type"] = ("label", "mark_read")[n]
            if table == "email_feedback_requests":
                candidate["resulting_action_plan_id"] = ("plan", "other")[n]
                candidate["expected_current_action_plan_id"] = None
            if table in ("email_reply_dispatch_claims", "email_reply_receipts"):
                candidate["outgoing_message_id"] = f"outgoing-{n}"
            insert(db, table, **candidate)
        assert db.execute(f"select count(*) from {table} where {key} is null").fetchone()[0] == 2


def test_email_actions_rowid_is_independent_of_public_text_id(db):
    plan(db)
    action(db)
    rowid = db.execute("select rowid from email_actions where action_id='action'").fetchone()[0]
    db.execute("update email_actions set rowid=? where action_id='action'", (rowid + 100,))
    assert db.execute("select rowid,action_id from email_actions").fetchone() == (rowid + 100, "action")
    assert db.execute("select * from email_actions where rowid=?", (rowid,)).fetchone() is None
    db.execute("update email_actions set action_id='renamed' where action_id='action'")
    assert db.execute("select rowid,action_id from email_actions").fetchone() == (rowid + 100, "renamed")


def test_email_actions_ordinary_rowid_zero_negative_reuse_max_and_blob_id(db):
    plan(db)
    action(db, "first")
    action(db, "highest", kind="label")
    assert db.execute("select action_id,rowid from email_actions order by rowid").fetchall() == [
        ("first", 1), ("highest", 2)]
    db.execute("delete from email_actions where action_id='highest'")
    action(db, "reused", kind="label")
    assert db.execute("select rowid from email_actions where action_id='reused'").fetchone() == (2,)
    db.execute("update email_actions set rowid=0 where action_id='first'")
    db.execute("update email_actions set rowid=-7 where action_id='reused'")
    assert db.execute("select action_id,rowid from email_actions order by rowid").fetchall() == [
        ("reused", -7), ("first", 0)]
    db.execute("update email_actions set rowid=? where action_id='first'", (2**63 - 1,))
    # The ordinary rowid allocator chooses a free positive random ID at MAX.
    action(db, b"\xff", kind="trash")
    new_rowid, storage_type = db.execute(
        "select rowid,typeof(action_id) from email_actions where action_id=?", (b"\xff",)
    ).fetchone()
    assert 0 < new_rowid < 2**63 - 1
    assert storage_type == "blob"


def test_feedback_null_expected_plan_and_parent_fk_actions(db):
    plan(db)
    plan(db, "other", 2)
    insert(db, "email_feedback_requests", feedback_request_id="feedback", classification_id=11,
           category="category", expected_current_action_plan_id=None,
           resulting_action_plan_id="plan", applied_at="time")
    assert db.execute("select expected_current_action_plan_id from email_feedback_requests").fetchone() == (None,)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update email_action_plans set action_plan_id='renamed' where action_plan_id='plan'")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from email_action_plans where action_plan_id='plan'")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert(db, "email_feedback_requests", feedback_request_id="missing", classification_id=11,
               category="category", resulting_action_plan_id="missing", applied_at="time")


def test_retry_composite_identity_is_immutable(db):
    plan(db)
    action(db)
    values = dict(action_id="action", attempt_number=1, source_readback_digest="a" * 64,
                  account_configuration_snapshot=json.dumps([None] * 9), requested_at="time")
    insert(db, "email_direct_retry_requests", **values)
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_retry_request_immutable"):
        insert(db, "email_direct_retry_requests", **values)
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_retry_request_immutable"):
        db.execute("update email_direct_retry_requests set requested_at='later'")
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_retry_request_immutable"):
        db.execute("delete from email_direct_retry_requests")
    insert(db, "email_direct_retry_requests", **(values | {"attempt_number": 2}))
    assert db.execute("select attempt_number from email_direct_retry_requests order by attempt_number").fetchall() == [(1,), (2,)]
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update email_actions set action_id='renamed' where action_id='action'")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from email_actions where action_id='action'")


@pytest.mark.parametrize("table", ("email_reply_dispatch_claims", "email_reply_receipts"))
def test_reply_child_parent_restrict_and_receipt_mutability(db, table):
    plan(db)
    base = reply_values()
    if table == "email_reply_dispatch_claims":
        values = base | dict(owner_id="owner", owner_generation=1, lease_token="lease",
                             sender="sender", thread_identity="thread", account_updated_at="time",
                             account_snapshot_json="{}", status="dispatching",
                             claimed_at="time", updated_at="time")
    else:
        values = base | dict(provider_operation="sent_readback", provider_target="",
                             provider_result_id="receipt", provider_receipt_json="{}",
                             display_excerpt="", created_at="time")
    insert(db, table, **values)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update email_action_plans set action_plan_id='renamed' where action_plan_id='plan'")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from email_action_plans where action_plan_id='plan'")
    if table == "email_reply_receipts":
        db.execute("update email_reply_receipts set provider_receipt_json=? where action_identity='reply'", ('{"smtp_acceptance_id":"later"}',))
        assert db.execute("select provider_receipt_json from email_reply_receipts").fetchone()[0] == '{"smtp_acceptance_id":"later"}'
    else:
        with pytest.raises(sqlite3.IntegrityError, match="email_reply_dispatch_in_flight"):
            db.execute("update email_accounts set account_id='renamed' where account_id='account'")
        with pytest.raises(sqlite3.IntegrityError, match="email_reply_dispatch_in_flight"):
            db.execute("update email_messages set thread_identity='renamed' where account_id='account'")
