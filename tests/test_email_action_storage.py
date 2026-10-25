"""Opt-in Email action schema against the frozen native source declarations."""

import json
from pathlib import Path
import sqlite3
from contextlib import closing

import pytest

from app.action_storage import (
    ACTION_PROJECTIONS, EMAIL_ACTION_PROJECTIONS, create_action_schema,
    insert_action, insert_email_action, ALL_ACTION_TYPES_WITH_REPLY, _action_definitions,
    _references_for,
    _encoded_sqlite_value,
)
from app.action_email_definitions import (
    EMAIL_COMPOSITE_KEYS, EMAIL_ROWID_TYPES,
    EMAIL_FIELDS, EMAIL_TYPES, EMAIL_NOT_NULL, EMAIL_CHECKS,
    EMAIL_SOURCE_FKS, EMAIL_SOURCE_INDEXES,
    email_action_projection,
)
from app import action_email_definitions


SPEC = Path(__file__).parents[1] / "docs/superpowers/specs"
PARENTS = json.loads((SPEC / "2026-10-13-database-redesign-email-actions-source.json").read_text())
CHILDREN = json.loads((SPEC / "2026-10-13-database-redesign-email-action-children-source.json").read_text())


def _source_tables(variant):
    return PARENTS[variant]["tables"] | CHILDREN["actual"]["tables"]


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate"))
def test_email_opt_in_has_all_thirteen_native_fields_and_default_is_unchanged(variant):
    original = _action_definitions(include_reply=True, reply_variant="fresh_memory_fk")
    with sqlite3.connect(":memory:") as db:
        db.execute("create table email_classifications(id integer primary key)")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True)
        columns = {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert {"email_" + c["name"] for table in _source_tables(variant).values()
                for c in table["columns"] if c["name"] != "id"} <= columns
        assert "email_public_key" in columns
        assert "email_operation_parent_type" in columns
        assert any(row[3] == "email_operation_parent_type" for row in db.execute(
            "pragma foreign_key_list(actions)"))
        for kind, indexes in EMAIL_SOURCE_INDEXES.items():
            for number, (unique, fields) in enumerate(indexes):
                name = f"idx_actions_email_{kind}_{number}"
                key_columns = tuple(row[2] for row in db.execute(f"pragma index_xinfo({name})") if row[5])
                assert key_columns == tuple("email_" + field for field in fields)
                row = db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()
                assert row and ("create unique index" in row[0].lower()) == unique
    assert _action_definitions(include_reply=True, reply_variant="fresh_memory_fk") == original
    assert set(EMAIL_ACTION_PROJECTIONS) == set(_source_tables(variant))


def test_finite_metadata_matches_frozen_source_objects():
    tables = _source_tables("actual")
    assert set(EMAIL_FIELDS) == set(tables)
    assert hasattr(action_email_definitions, "EMAIL_DEFAULTS")
    for kind, table in tables.items():
        names = tuple(column["name"] for column in table["columns"])
        assert EMAIL_FIELDS[kind] == names
        assert EMAIL_NOT_NULL[kind] == tuple(column["name"] for column in table["columns"]
                                             if column["notnull"])
        assert all(EMAIL_TYPES[column["name"]] == column["type"].lower()
                   for column in table["columns"])
        assert EMAIL_CHECKS[kind] == tuple(
            " ".join("email_" + token if token in names else token for token in expression)
            for expression in table["check_expression_tokens"]
        )
        assert EMAIL_SOURCE_FKS[kind] == tuple(
            (fk["from"], fk["table"], fk["to"], fk["on_update"], fk["on_delete"])
            for fk in table["foreign_keys"]
        )
        assert EMAIL_SOURCE_INDEXES[kind] == tuple(
            (bool(index["unique"]), tuple(column["name"] for column in index["columns"]
                                          if column["key"]))
            for index in table["indexes"]
        )
        assert action_email_definitions.EMAIL_DEFAULTS[kind] == {
            column["name"]: column["dflt_value"] for column in table["columns"]
            if column["dflt_value"] is not None
        }


def test_email_generated_fields_are_null_for_older_actions_and_absent_email_fields():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text, legacy_key text)")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("insert into runs(id,record_type,legacy_key) values(1,'agent_runs','1')")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True,
                             compact_payload=True)
        insert_action(db, "external_action_results", dict(
            external_action_key="older-key", business_object_key="business",
            action_identity="older-identity", operation="send",
            target_identifiers_json="raw targets", provider_result_json="raw result",
            result_digest="digest", first_agent_run_id=1,
        ))
        old = db.execute(ACTION_PROJECTIONS["external_action_results"]).fetchone()
        assert old[0] == "older-key"
        assert "older-identity" in old
        assert db.execute("select email_action_identity,email_created_at,email_operation "
                          "from actions where record_type='external_action_results'").fetchone() == (
                              None, None, None,
                          )
        cancellation = dict(action_identity="cancel", decision_json="{}")
        insert_email_action(db, "email_unsubscribe_cancellations", cancellation)
        assert db.execute("select email_action_identity,email_decision_json,email_status,email_created_at "
                          "from actions where record_type='email_unsubscribe_cancellations'").fetchone() == (
                              "cancel", "{}", None, None,
                          )


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_email_rows_reject_all_unrelated_physical_parent_references(variant):
    references = tuple(_references_for(ALL_ACTION_TYPES_WITH_REPLY))
    assert set(references) == {
        "task_id", "consumer_run_id", "audit_run_id", "predecessor_review_id",
        "candidate_id", "review_id", "selection_id", "execution_id",
        "first_agent_run_id", "agent_run_id", "sent_reply_id", "reply_task_id",
    }
    if variant == "fresh_memory_fk":
        references += ("memory_attempt_ref",)
    with closing(sqlite3.connect(":memory:")) as db:
        # Checks must reject these fields even when FK enforcement is disabled.
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        db.execute("create table email_classifications(id integer primary key)")
        create_action_schema(db, include_reply=True, reply_variant=variant, include_email=True)
        payload = json.dumps({"action_identity": "cancel", "decision_json": "{}"})
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   ("email_unsubscribe_cancellations", "1", payload))
        assert db.execute(EMAIL_ACTION_PROJECTIONS["email_unsubscribe_cancellations"]).fetchone() == (
            "cancel", "{}",
        )
        for field in references:
            candidate = json.dumps({"action_identity": f"cancel-{field}", "decision_json": "{}"})
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                db.execute(
                    f"insert into actions(record_type,legacy_key,payload_json,{field}) values(?,?,?,?)",
                    ("email_unsubscribe_cancellations", "2", candidate, 123),
                )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            db.execute("update actions set task_id=123 where record_type='email_unsubscribe_cancellations'")
        assert db.execute("select count(*) from actions where record_type='email_unsubscribe_cancellations'").fetchone() == (1,)
        db.execute("insert into tasks(id,record_type) values(123,'reply_tasks')")
        db.commit()
        db.execute("pragma foreign_keys=on")
        assert db.execute("pragma foreign_keys").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            db.execute("insert into actions(record_type,legacy_key,payload_json,task_id) values(?,?,?,123)",
                       ("email_unsubscribe_cancellations", "3",
                        json.dumps({"action_identity": "another", "decision_json": "{}"})))
        db.execute("delete from tasks where id=123")


def _insert(db, kind, values):
    return db.execute(
        f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _copy_source_row(source, target, kind, source_rowid, source_tables):
    fields = tuple(c["name"] for c in source_tables[kind]["columns"])
    original = source.execute(
        f"select {','.join(fields)} from {kind} where rowid=?", (source_rowid,)
    ).fetchone()
    row = dict(zip(fields, original, strict=True))
    if kind in EMAIL_COMPOSITE_KEYS:
        pair = EMAIL_COMPOSITE_KEYS[kind]
        key = json.dumps([_encoded_sqlite_value(row[x]) for x in pair], separators=(",", ":"))
        payload = {field: _encoded_sqlite_value(value) for field, value in row.items()
                   if field not in pair}
    elif kind in EMAIL_ROWID_TYPES:
        key = str(source_rowid)
        payload = {field: _encoded_sqlite_value(value) for field, value in row.items()}
    else:
        key = str(row["id"])
        payload = {field: _encoded_sqlite_value(value) for field, value in row.items()
                   if field != "id"}
    target.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (kind, key, json.dumps(payload, separators=(",", ":"), allow_nan=False)))
    return fields, original


def _cases():
    claim = dict(action_identity="claim", effect_digest="digest", action_plan_id="plan",
                 action_plan_version=1, classification_id=11, account_id="account",
                 stable_message_identity="message", thread_identity="thread",
                 entry_reference="entry", operations_json="[]", owner_id="owner",
                 owner_generation=1, lease_token="lease", account_updated_at="time",
                 status="dispatching", claimed_at="time", updated_at="time")
    reply = dict(action_identity="reply", effect_digest="digest", action_plan_id="plan",
                 classification_id=11, account_id="account",
                 stable_message_identity="message", outgoing_message_id="outgoing")
    return {
        "email_action_plans": dict(action_plan_id="plan", action_plan_version=1,
            classification_id=11, account_id="account", category="category",
            classification_source="model", confidence=0.5, model_id="model",
            config_version="config", actions_json="[]", action_parameters_json="{}",
            created_at="time"),
        "email_actions": dict(action_id="action", action_plan_id="plan",
            classification_id=11, account_id="account", action_type="archive",
            parameters_json="{}", config_version="config", status="pending",
            created_at="time", updated_at="time"),
        "email_action_attempts": dict(action_id="action", attempt_number=1,
            status="done", provider_operation="operation", provider_target="target",
            provider_result_id="receipt", error="", started_at="start", finished_at="finish"),
        "email_unsubscribe_cancellations": dict(action_identity="cancel", decision_json="{}"),
        "email_unsubscribe_claims": claim,
        "email_unsubscribe_continuations": dict(action_identity="claim", effect_digest="digest",
            observation_reference="observation", controls_json="[]", created_at="time", updated_at="time"),
        "email_unsubscribe_effects": dict(action_identity="claim", effect_digest="effect",
            operations_json="[]", created_at="time"),
        "email_unsubscribe_receipts": dict(action_identity="receipt", effect_digest="digest",
            action_plan_id="plan", action_plan_version=1, classification_id=11,
            account_id="account", stable_message_identity="message", thread_identity="thread",
            entry_reference="entry", outcome="done", receipt_id="receipt", evidence="evidence",
            created_at="time"),
        "email_unsubscribe_steps": dict(action_identity="claim", effect_digest="digest",
            sequence=1, operation="operation", state="state", reference="reference", created_at="time"),
        "email_direct_retry_requests": dict(action_id="action", attempt_number=1,
            source_readback_digest="a" * 64, account_configuration_snapshot=json.dumps([None] * 9),
            requested_at="time"),
        "email_feedback_requests": dict(feedback_request_id="feedback", classification_id=11,
            category="category", expected_current_action_plan_id=None,
            resulting_action_plan_id="plan", applied_at="time"),
        "email_reply_dispatch_claims": reply | dict(owner_id="owner", owner_generation=1,
            lease_token="lease", sender="sender", thread_identity="thread", account_updated_at="time",
            account_snapshot_json="{}", status="dispatching", claimed_at="time", updated_at="time"),
        "email_reply_receipts": reply | dict(provider_operation="sent_readback", provider_target="",
            provider_result_id="receipt", provider_receipt_json="{}", display_excerpt="", created_at="time"),
    }


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate"))
def test_every_frozen_field_projects_with_native_value_and_storage_class(variant):
    source_tables = _source_tables(variant)
    assert sum(len(table["columns"]) for table in source_tables.values()) == 144
    with sqlite3.connect(":memory:") as source, sqlite3.connect(":memory:") as target:
        for db in (source, target):
            db.execute("pragma foreign_keys=on")
            db.execute("create table email_classifications(id integer primary key)")
            db.execute("insert into email_classifications(id) values(11)")
        target.execute("create table tasks(id integer primary key, record_type text)")
        target.execute("create table runs(id integer primary key, record_type text)")
        for kind, table in source_tables.items():
            source.execute(table["sql"])
        for table in source_tables.values():
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    source.execute(index["sql"])
            for trigger in table["triggers"]:
                source.execute(trigger["sql"])
        create_action_schema(target, include_reply=True, reply_variant="fresh_memory_fk", include_email=True)
        for kind, values in _cases().items():
            source_cursor = _insert(source, kind, values)
            fields, original = _copy_source_row(source, target, kind, source_cursor.lastrowid, source_tables)
            projection = email_action_projection(kind, source_variant=variant)
            projected = target.execute(projection).fetchone()
            assert projected == original, kind
            source_types = source.execute(f"select {','.join('typeof(' + x + ')' for x in fields)} "
                                          f"from {kind} where rowid=?", (source_cursor.lastrowid,)).fetchone()
            target_types = target.execute("select " + ",".join("typeof(" + x + ")" for x in fields) +
                                          " from (" + projection + ")").fetchone()
            assert target_types == source_types, kind
        for source_sql, target_sql in (
            ("update email_actions set attempt_count=2 where action_id='action'",
             "update actions set payload_json=json_set(payload_json,'$.attempt_count',2) "
             "where record_type='email_actions'"),
            ("update email_direct_retry_requests set requested_at='later'",
             "update actions set payload_json=json_set(payload_json,'$.requested_at','later') "
             "where record_type='email_direct_retry_requests'"),
            ("delete from email_direct_retry_requests",
             "delete from actions where record_type='email_direct_retry_requests'"),
        ):
            with pytest.raises(sqlite3.IntegrityError) as source_error:
                source.execute(source_sql)
            with pytest.raises(sqlite3.IntegrityError) as target_error:
                target.execute(target_sql)
            assert target_error.value.sqlite_errorcode == source_error.value.sqlite_errorcode


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate"))
def test_nullable_blob_public_keys_and_native_parent_constraints(variant):
    tables = _source_tables(variant)
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        for db in (source, target):
            db.execute("pragma foreign_keys=on")
            db.execute("create table email_classifications(id integer primary key)")
            db.execute("insert into email_classifications values(11)")
        target.execute("create table tasks(id integer primary key, record_type text)")
        target.execute("create table runs(id integer primary key, record_type text)")
        for kind, table in tables.items():
            source.execute(table["sql"])
        create_action_schema(target, include_reply=True, reply_variant="fresh_memory_fk", include_email=True)
        plan = _cases()["email_action_plans"]
        for version, key in ((10, None), (11, None), (12, b"\x00plan"), (13, "parent")):
            rowid = _insert(source, "email_action_plans", plan | {
                "action_plan_version": version, "action_plan_id": key,
            }).lastrowid
            _copy_source_row(source, target, "email_action_plans", rowid, tables)
        assert source.execute("select count(*) from email_action_plans where action_plan_id is null").fetchone() == (2,)
        assert target.execute("select count(*) from actions where record_type='email_action_plans' "
                              "and email_public_key is null").fetchone() == (2,)
        assert source.execute("select typeof(action_plan_id) from email_action_plans "
                              "where action_plan_version=12").fetchone() == ("blob",)
        assert target.execute("select typeof(email_public_key) from actions "
                              "where record_type='email_action_plans' and email_action_plan_version=12").fetchone() == ("blob",)
        source.execute("update email_action_plans set rowid=-4 where action_plan_id='parent'")
        target.execute("update actions set legacy_key='-4' where record_type='email_action_plans' "
                       "and email_public_key='parent'")
        assert source.execute("select rowid from email_action_plans where action_plan_id='parent'").fetchone() == (-4,)
        assert target.execute(email_action_projection("email_action_plans", include_rowid=True) +
                              " and action_plan_id='parent'").fetchone()[0] == -4
        source.execute("update email_action_plans set rowid=0 where action_plan_version=10")
        target.execute("update actions set legacy_key='0' where record_type='email_action_plans' "
                       "and email_action_plan_version=10")
        assert source.execute("select rowid from email_action_plans where action_plan_version=10").fetchone() == (0,)
        assert target.execute("select cast(legacy_key as integer) from actions where "
                              "record_type='email_action_plans' and email_action_plan_version=10").fetchone() == (0,)

        child = _cases()["email_actions"] | {"action_id": "child", "action_plan_id": "parent"}
        rowid = _insert(source, "email_actions", child).lastrowid
        _copy_source_row(source, target, "email_actions", rowid, tables)
        blob_child = child | {"action_id": b"\x00action", "action_plan_id": b"\x00plan"}
        rowid = _insert(source, "email_actions", blob_child).lastrowid
        _copy_source_row(source, target, "email_actions", rowid, tables)
        assert target.execute("select typeof(action_id),typeof(action_plan_id) from (" +
                              EMAIL_ACTION_PROJECTIONS["email_actions"] + ") where "
                              "action_plan_id=x'00706c616e'").fetchone() == ("blob", "blob")
        for source_sql, target_sql in (
            ("update email_action_plans set action_plan_id='renamed' where action_plan_id='parent'",
             "update actions set payload_json=json_set(payload_json,'$.action_plan_id','renamed') "
             "where record_type='email_action_plans' and email_public_key='parent'"),
            ("delete from email_action_plans where action_plan_id='parent'",
             "delete from actions where record_type='email_action_plans' and email_public_key='parent'"),
        ):
            with pytest.raises(sqlite3.IntegrityError) as source_error:
                source.execute(source_sql)
            with pytest.raises(sqlite3.IntegrityError) as target_error:
                target.execute(target_sql)
            assert target_error.value.sqlite_errorcode == source_error.value.sqlite_errorcode

        missing = child | {"action_id": "missing", "action_plan_id": "absent", "action_type": "mark_read"}
        with pytest.raises(sqlite3.IntegrityError) as source_error:
            _insert(source, "email_actions", missing)
        target_payload = target.execute("select payload_json from actions where record_type='email_actions'").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError) as target_error:
            target.execute("insert into actions(record_type,legacy_key,payload_json) "
                           "values('email_actions','200',json_set(?,'$.action_id','missing',"
                           "'$.action_plan_id','absent','$.action_type','mark_read'))", (target_payload,))
        assert target_error.value.sqlite_errorcode == source_error.value.sqlite_errorcode

        with pytest.raises(sqlite3.IntegrityError) as source_error:
            _insert(source, "email_actions", child | {"action_id": "duplicate"})
        with pytest.raises(sqlite3.IntegrityError) as target_error:
            target.execute("insert into actions(record_type,legacy_key,payload_json) "
                           "values('email_actions','201',json_set(?,'$.action_id','duplicate'))",
                           (target_payload,))
        assert target_error.value.sqlite_errorcode == source_error.value.sqlite_errorcode

        invalid = child | {"action_id": "invalid", "action_type": "mark_read", "status": "bogus"}
        with pytest.raises(sqlite3.IntegrityError) as source_error:
            _insert(source, "email_actions", invalid)
        with pytest.raises(sqlite3.IntegrityError) as target_error:
            target.execute("insert into actions(record_type,legacy_key,payload_json) "
                           "values('email_actions','202',json_set(?,'$.action_id','invalid',"
                           "'$.action_type','mark_read','$.status','bogus'))", (target_payload,))
        assert target_error.value.sqlite_errorcode == source_error.value.sqlite_errorcode
