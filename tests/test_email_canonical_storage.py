"""Opt-in canonical Email conversion against explicit historical current13 SQL."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import action_storage as actions
from app.store import AutoReplyStore
from app.email_canonical_storage import (
    capture_email_canonical_source,
    migrate_email_canonical_schema,
    restore_email_canonical_schema,
    insert_email_message,
    insert_email_classification,
    upsert_email_message,
    ignore_email_message,
    update_email_message,
    update_email_classification,
    update_email_paired_locator,
    delete_email_message,
    delete_email_classification,
    email_message_projection,
    email_classification_projection,
)
from app.email_canonical_storage import _action_objects, _create_actions, _create_source_table


@pytest.fixture(scope="module")
def current13(tmp_path_factory):
    path = tmp_path_factory.mktemp("email-canonical-source") / "base.sqlite3"
    AutoReplyStore(path)
    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                          "2026-10-17-email-canonical-current13-source.json").read_text())
    db = sqlite3.connect(path)
    try:
        db.execute("pragma foreign_keys=off")
        # The current constructor selects the inline owner. Rebuild the
        # independent historical source declarations before its old Action
        # guard is installed; do not use the conversion under test here.
        db.execute("alter table business drop column import_task_id")
        legacy_capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                                     "2026-10-17-email-canonical-current-copy.json").read_text())
        for table in ("business_source_documents", "business_legacy_links"):
            legacy_objects = legacy_capsule[
                "post_target_remaining_business_declarations"
            ][table]["objects"]
            for kind in ("table", "index", "trigger"):
                for obj_kind, _name, _owner, sql in legacy_objects:
                    if obj_kind == kind and sql:
                        db.execute(sql)
        db.execute("drop table actions")
        for kind in ("emails", "email_classifications", "email_messages"):
            db.execute(f"drop table if exists {kind}")
        for kind in ("email_messages", "email_classifications"):
            _create_source_table(db, capsule["fresh_native_declarations"][kind])
        _create_actions(db, _action_objects("fresh_memory_fk", target=False))
        db.commit()
    finally:
        db.close()
    return path


def _db(tmp_path, current13):
    path = tmp_path / "email.sqlite3"
    shutil.copyfile(current13, path)
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=off")
    return db


def _message(stable="stable-1", **overrides):
    return {
        "account_id": "account", "stable_message_identity": stable,
        "folder": " INBOX ", "uidvalidity": 10, "uid": 20,
        "rfc_message_id": "<message>", "in_reply_to": "", "references_json": "[]",
        "thread_identity": "thread", "sender": "sender", "recipients_json": "[]",
        "subject": "subject", "normalized_text": "body", "preview": "preview",
        "attachment_metadata_json": "[]", "received_at": "received",
        "created_at": "message-created", "updated_at": "message-updated",
    } | overrides


def _classification(stable="stable-1", **overrides):
    return {
        "id": 101, "account_id": "account", "folder": "INBOX",
        "uidvalidity": 10, "uid": 20, "stable_message_identity": stable,
        "rfc_message_id": None, "thread_id": "class-thread", "sender": "class-sender",
        "subject": "class-subject", "preview": "class-preview", "model_text": "model-body",
        "received_at": "class-received", "category": None, "predicted_category": None,
        "confirmed_category": None, "confidence": 0.5, "margin": 0.1,
        "probabilities_json": "{}", "model_id": "model", "config_version": "v1",
        "status": "pending_feedback", "classification_source": "model",
        "agent_result_json": "null", "current_action_plan_id": None,
        "included_in_model_id": None, "legacy_processed_without_plan": 0,
        "confirmed_at": "", "created_at": "class-created", "updated_at": "class-updated",
    } | overrides


def _insert(db, table, values):
    db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _insert_source_action(db, kind, public_key, values):
    """Historical object-payload fixture, independent of the final vector DAO."""
    from app.action_email_definitions import EMAIL_PUBLIC_FIELDS

    assert values[EMAIL_PUBLIC_FIELDS[kind]] == public_key
    rowid = db.execute("select coalesce(max(cast(legacy_key as integer)),0)+1 from actions "
                       "where record_type=?", (kind,)).fetchone()[0]
    db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
               (kind, str(rowid), actions._native_payload(values)))


def test_paired_source_becomes_one_row_and_inverse_uses_current_facts(tmp_path, current13):
    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message())
    _insert(db, "email_classifications", _classification())
    db.commit()
    source = capture_email_canonical_source(db)
    assert json.loads(json.dumps(source)) == source
    migrate_email_canonical_schema(db, source)
    assert db.execute("select count(*) from emails").fetchone() == (1,)
    target_columns = [row[1] for row in db.execute("pragma table_xinfo(emails)")]
    assert len(target_columns) == 47
    assert target_columns.count("message_source_reference_json") == 1
    assert target_columns.count("message_normalized_text") == 1
    assert target_columns.count("class_model_text") == 1
    assert db.execute("select id,classification_id,message_id from emails").fetchone() == (101, 101, 1)
    assert db.execute("select folder from (" + email_message_projection() + ")").fetchone() == (" INBOX ",)
    assert db.execute("select folder from (" + email_classification_projection() + ")").fetchone() == ("INBOX",)
    db.execute("update emails set message_normalized_text='new body',class_model_text='new model'")
    restore_email_canonical_schema(db, source)
    assert db.execute("select normalized_text from email_messages").fetchone() == ("new body",)
    assert db.execute("select model_text from email_classifications").fetchone() == ("new model",)
    db.execute("insert into email_messages(" + ",".join(_message("stable-2")) + ") values(" + ",".join("?" for _ in _message("stable-2")) + ")", tuple(_message("stable-2").values()))
    assert db.execute("select id from email_messages where stable_message_identity='stable-2'").fetchone() == (2,)
    second_source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, second_source)
    assert db.execute("select count(*) from emails").fetchone() == (2,)
    db.close()


def test_populated_forward_inverse_snapshots_with_sqlite_row_factory(tmp_path, current13):
    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message())
    _insert(db, "email_classifications", _classification())
    db.commit()
    db.row_factory = sqlite3.Row
    source = capture_email_canonical_source(db)
    assert source["source_counts"]["email_messages"] == 1
    assert source["source_counts"]["email_classifications"] == 1
    migrate_email_canonical_schema(db, source)
    assert tuple(db.execute("select id,classification_id,message_id from emails").fetchone()) == (101, 101, 1)
    db.execute("update emails set message_normalized_text='current body',class_model_text='current model'")
    restore_email_canonical_schema(db, json.loads(json.dumps(source)))
    assert tuple(db.execute("select id,normalized_text,typeof(normalized_text) from email_messages").fetchone()) == (
        1, "current body", "text")
    assert tuple(db.execute("select id,model_text,typeof(model_text) from email_classifications").fetchone()) == (
        101, "current model", "text")
    assert capture_email_canonical_source(db)["source_tables"] == source["source_tables"]
    db.close()


def test_message_only_then_classification_keeps_both_aliases(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    message_id = insert_email_message(db, _message())
    assert message_id == 1
    classification_id = insert_email_classification(db, _classification())
    assert classification_id == 101
    assert db.execute("select count(*),classification_id,message_id from emails").fetchone() == (1, 101, 1)
    restore_email_canonical_schema(db, source)
    assert db.execute("select id from email_classifications").fetchone() == (101,)
    db.close()


def test_paired_locator_update_matches_native_class_then_message_and_rolls_back(tmp_path, current13):
    native_path = tmp_path / "native-paired-locator.sqlite3"
    shutil.copyfile(current13, native_path)
    native = sqlite3.connect(native_path)
    _insert(native, "email_messages", _message())
    _insert(native, "email_classifications", _classification())
    native.commit()
    native.execute("update email_classifications set folder=?,uidvalidity=?,uid=?,thread_id=?,updated_at=? "
                   "where id=? and account_id=? and stable_message_identity=? and folder=? and uidvalidity=? and uid=?",
                   ("Archive", 11, 21, "new-class-thread", "new-class-time", 101,
                    "account", "stable-1", "INBOX", 10, 20))
    native.execute("update email_messages set folder=?,uidvalidity=?,uid=?,updated_at=? "
                   "where account_id=? and stable_message_identity=? and folder=? and uidvalidity=? and uid=?",
                   (" Archive ", 11, 21, "new-message-time", "account", "stable-1", " INBOX ", 10, 20))
    native_facts = (native.execute("select folder,uidvalidity,uid,thread_id,updated_at from email_classifications").fetchone(),
                    native.execute("select folder,uidvalidity,uid,thread_identity,updated_at from email_messages").fetchone())
    native.rollback()
    assert native.execute("select uid from email_classifications").fetchone() == (20,)
    native.execute("update email_classifications set folder=?,uidvalidity=?,uid=?,thread_id=?,updated_at=? where id=?",
                   ("Other", 12, 22, "other-thread", "other-time", 101))
    native.execute("update email_messages set folder=?,uidvalidity=?,uid=? where stable_message_identity=?",
                   (" Other ", 12, 22, "stable-1"))
    native_unconditional = (native.execute("select folder,uidvalidity,uid,thread_id,updated_at from email_classifications").fetchone(),
                            native.execute("select folder,uidvalidity,uid,thread_identity,updated_at from email_messages").fetchone())
    native.rollback()
    native.close()

    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message())
    _insert(db, "email_classifications", _classification())
    db.commit()
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    db.commit()
    assert update_email_paired_locator(
        db, classification_id=101, account_id="account", stable_message_identity="stable-1",
        class_folder="Archive", message_folder=" Archive ", uidvalidity=11, uid=21,
        class_thread_id="new-class-thread", class_updated_at="new-class-time",
        message_updated_at="new-message-time",
        expected_class_folder="INBOX", expected_message_folder=" INBOX ",
        expected_uidvalidity=10, expected_uid=20,
    ) == 1
    assert (db.execute("select folder,uidvalidity,uid,thread_id,updated_at from (" + email_classification_projection() + ")").fetchone(),
            db.execute("select folder,uidvalidity,uid,thread_identity,updated_at from (" + email_message_projection() + ")").fetchone()) == native_facts
    assert update_email_paired_locator(
        db, classification_id=101, account_id="account", stable_message_identity="stable-1",
        class_folder="ignored", message_folder="ignored", uidvalidity=12, uid=22,
        expected_class_folder="INBOX", expected_message_folder=" INBOX ",
        expected_uidvalidity=10, expected_uid=20,
    ) == 0
    db.rollback()
    assert db.execute("select uid from emails").fetchone() == (20,)
    assert update_email_paired_locator(
        db, classification_id=101, account_id="account", stable_message_identity="stable-1",
        class_folder="Other", message_folder=" Other ", uidvalidity=12, uid=22,
        class_thread_id="other-thread", class_updated_at="other-time",
    ) == 1
    assert (db.execute("select folder,uidvalidity,uid,thread_id,updated_at from (" + email_classification_projection() + ")").fetchone(),
            db.execute("select folder,uidvalidity,uid,thread_identity,updated_at from (" + email_message_projection() + ")").fetchone()) == native_unconditional
    db.close()


@pytest.mark.parametrize("equivalent", ("20", 20.0, True))
def test_shared_locator_affinity_matches_native_and_rejects_real_divergence(tmp_path, current13, equivalent):
    native_path = tmp_path / "native-affinity.sqlite3"
    shutil.copyfile(current13, native_path)
    native = sqlite3.connect(native_path)
    _insert(native, "email_messages", _message(uid=1 if equivalent is True else 20))
    _insert(native, "email_classifications", _classification(uid=equivalent))
    expected = native.execute("select uid,typeof(uid) from email_classifications").fetchone()
    native.close()

    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message(uid=1 if equivalent is True else 20))
    metadata = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, metadata)
    insert_email_classification(db, _classification(uid=equivalent))
    assert db.execute("select uid,typeof(uid) from emails").fetchone() == expected
    update_email_classification(db, 101, {"uid": equivalent})
    with pytest.raises(ValueError, match="shared"):
        update_email_classification(db, 101, {"uid": 22})
    assert db.execute("select uid from emails").fetchone() == expected[:1]
    db.close()


@pytest.mark.parametrize("different", ({"uid": 21}, {"account_id": "another"},
                                            {"stable_message_identity": "another"}))
def test_classification_rejects_truly_different_shared_fact_before_mutation(tmp_path, current13, different):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    with pytest.raises(ValueError):
        insert_email_classification(db, _classification(**different))
    assert db.execute("select count(*),classification_id,uid from emails").fetchone() == (1, None, 20)
    db.close()


def test_classification_shared_update_uses_values_with_sqlite_row_factory(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    insert_email_classification(db, _classification())
    db.row_factory = sqlite3.Row
    assert update_email_classification(db, 101, {"uid": "20", "uidvalidity": 10.0}) == 1
    assert tuple(db.execute("select uidvalidity,uid,typeof(uidvalidity),typeof(uid) from emails").fetchone()) == (
        10, 20, "integer", "integer")
    with pytest.raises(ValueError, match="shared"):
        update_email_classification(db, 101, {"uid": 21})
    assert tuple(db.execute("select uidvalidity,uid from emails").fetchone()) == (10, 20)
    db.close()


def test_alias_collision_and_action_fk_resolve_classification_identity(tmp_path, current13):
    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message())
    _insert(db, "email_classifications", _classification())
    _insert(db, "email_messages", {"id": 101, **_message("stable-2")})
    db.commit()
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    assert db.execute("select id,classification_id,message_id from emails where stable_message_identity='stable-1'").fetchone() == (101, 101, 1)
    assert db.execute("select id,classification_id,message_id from emails where stable_message_identity='stable-2'").fetchone() == (102, None, 101)
    db.commit()
    db.execute("pragma foreign_keys=on")
    plan = {
        "action_plan_id": "plan-1", "action_plan_version": 1,
        "classification_id": 101, "account_id": "account", "category": "work",
        "classification_source": "model", "confidence": 0.5,
        "model_id": "model", "config_version": "v1", "actions_json": "[]",
        "action_parameters_json": "{}", "authorization_snapshot_json": None,
        "legacy_serialization_pre_v16": 0, "created_at": "time",
    }
    _insert_source_action(db, "email_action_plans", plan["action_plan_id"], plan)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insert_source_action(db, "email_action_plans", "invalid-message-only", plan | {
            "action_plan_id": "invalid-message-only", "classification_id": 102,
        })
    _insert_source_action(db, "email_feedback_requests", "feedback-1", {
        "feedback_request_id": "feedback-1", "classification_id": 101,
        "expected_current_action_plan_id": None,
        "category": "work", "resulting_action_plan_id": "plan-1",
        "applied_at": "time",
    })
    assert db.execute("select email_classification_id from actions where record_type='email_feedback_requests'").fetchone() == (101,)
    assert db.execute("pragma foreign_key_check").fetchall() == []
    db.commit()
    db.execute("pragma foreign_keys=off")
    restore_email_canonical_schema(db, source)
    db.commit()
    db.execute("pragma foreign_keys=on")
    assert db.execute("pragma foreign_key_check").fetchall() == []
    assert db.execute("select id from email_classifications").fetchone() == (101,)
    assert db.execute("select id from email_messages order by id").fetchall() == [(1,), (101,)]
    assert db.execute("select email_classification_id from actions where record_type='email_feedback_requests'").fetchone() == (101,)
    db.close()


def test_omitted_classification_defaults_apply_at_classification_time(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    values = _classification()
    for field in ("sender", "subject", "preview", "model_text", "received_at",
                  "agent_result_json", "legacy_processed_without_plan", "confirmed_at",
                  "created_at", "updated_at"):
        del values[field]
    insert_email_classification(db, values)
    row = db.execute("select sender,model_text,agent_result_json,"
                     "legacy_processed_without_plan,created_at,updated_at from ("
                     + email_classification_projection() + ")").fetchone()
    assert row[:4] == ("", "", "null", 0)
    assert row[4] and row[5]
    db.close()


@pytest.mark.parametrize("schema", ("main", "temp"))
def test_unknown_dependency_is_refused_before_conversion(tmp_path, current13, schema):
    db = _db(tmp_path, current13)
    db.execute(f"create view {schema}.unexpected_email_reader as select id from email_messages")
    with pytest.raises(ValueError, match="dependency"):
        capture_email_canonical_source(db)
    assert db.execute("select count(*) from email_messages").fetchone() == (0,)
    db.close()


@pytest.mark.parametrize("profile,variant", (
    ("actual", "actual_no_memory_fk"), ("fresh", "fresh_memory_fk"),
    ("actual", "fresh_memory_fk"), ("fresh", "actual_no_memory_fk"),
))
def test_exact_dual_source_profiles_roundtrip(tmp_path, current13, profile, variant):
    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                          "2026-10-17-email-canonical-current13-source.json").read_text())
    declarations = capsule[("actual" if profile == "actual" else "fresh")
                           + "_native_declarations"]
    with _db(tmp_path, current13) as db:
        if (profile, variant) != ("fresh", "fresh_memory_fk"):
            db.execute("drop table actions")
            db.execute("drop table email_classifications")
            db.execute("drop table email_messages")
            for kind in ("email_messages", "email_classifications"):
                _create_source_table(db, declarations[kind])
            _create_actions(db, _action_objects(variant, target=False))
        source = capture_email_canonical_source(db)
        assert source["profile"] == profile
        assert source["variant"] == variant
        _insert(db, "email_messages", _message())
        _insert(db, "email_classifications", _classification())
        _insert_source_action(db, "email_action_plans", "plan-cross", {
            "action_plan_id": "plan-cross", "action_plan_version": 1,
            "classification_id": 101, "account_id": "account", "category": "work",
            "classification_source": "model", "confidence": 0.5,
            "model_id": "model", "config_version": "v1", "actions_json": "[]",
            "action_parameters_json": "{}", "authorization_snapshot_json": None,
            "legacy_serialization_pre_v16": 0, "created_at": "time",
        })
        action_before = db.execute("select * from actions").fetchall()
        source = capture_email_canonical_source(db)
        assert json.loads(json.dumps(source)) == source
        migrate_email_canonical_schema(db, source)
        assert db.execute("select count(*) from emails").fetchone() == (1,)
        db.execute("update emails set message_normalized_text='current target body'")
        restore_email_canonical_schema(db, json.loads(json.dumps(source)))
        assert db.execute("select id from email_classifications").fetchone() == (101,)
        assert db.execute("select normalized_text from email_messages").fetchone() == ("current target body",)
        assert db.execute("select * from actions").fetchall() == action_before
        assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (1,)
        assert capture_email_canonical_source(db)["source_tables"] == source["source_tables"]


def test_inverse_rejects_changed_source_metadata_without_schema_mutation(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    damaged = json.loads(json.dumps(source))
    table = next(item for item in damaged["source_tables"]["email_messages"]["objects"]
                 if item[0] == "table")
    table[3] = table[3].replace("normalized_text text not null", "normalized_text text")
    with pytest.raises(ValueError, match="metadata"):
        restore_email_canonical_schema(db, damaged)
    assert db.execute("select count(*) from emails").fetchone() == (0,)
    db.close()


def test_inverse_refuses_unknown_target_dependency(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    db.execute("create temp view unexpected_canonical_reader as select id from emails")
    with pytest.raises(ValueError, match="dependency"):
        restore_email_canonical_schema(db, source)
    assert db.execute("select count(*) from emails").fetchone() == (0,)
    db.close()


def test_independent_message_auto_water_and_native_alias_coercion(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    assert insert_email_message(db, _message("stable-0") | {"id": 0}) == 0
    assert insert_email_message(db, _message("stable-50") | {"id": 50.0}) == 50
    db.execute("delete from emails where message_id=50")
    assert insert_email_message(db, _message("stable-next")) == 51
    assert insert_email_classification(db, _classification("stable-next", id=2.0)) == 2
    assert db.execute("select classification_id,message_id from emails where stable_message_identity='stable-next'").fetchone() == (2, 51)
    with pytest.raises(sqlite3.IntegrityError, match="datatype mismatch"):
        insert_email_message(db, _message("bad-blob") | {"id": b"1"})
    db.close()


def test_message_auto_max_fails_with_native_statement_rollback(tmp_path, current13):
    native_path = tmp_path / "native-max.sqlite3"
    shutil.copyfile(current13, native_path)
    native = sqlite3.connect(native_path)
    _insert(native, "email_messages", _message("max") | {"id": 2**63 - 1})
    native.execute("delete from email_messages where stable_message_identity='max'")
    native.commit()
    with pytest.raises(sqlite3.OperationalError) as native_error:
        _insert(native, "email_messages", _message("overflow"))
    expected_error = (native_error.value.sqlite_errorcode, native_error.value.sqlite_errorname)
    assert expected_error == (sqlite3.SQLITE_FULL, "SQLITE_FULL")
    assert not native.in_transaction
    native.close()

    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    db.commit()
    insert_email_message(db, _message("max") | {"id": 2**63 - 1})
    db.execute("delete from emails where stable_message_identity='max'")
    with pytest.raises(sqlite3.OperationalError, match="database or disk is full") as canonical_error:
        insert_email_message(db, _message("overflow"))
    assert (canonical_error.value.sqlite_errorcode, canonical_error.value.sqlite_errorname) == expected_error
    assert not db.in_transaction
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() is None
    db.close()


def test_physical_collision_at_max_has_deterministic_migration_mapping(tmp_path, current13):
    source_path = tmp_path / "source-collision.sqlite3"
    shutil.copyfile(current13, source_path)
    source_db = sqlite3.connect(source_path)
    try:
        _insert(source_db, "email_messages", _message("paired-2") | {"id": 1})
        _insert(source_db, "email_classifications", _classification("paired-2") | {"id": 2})
        _insert(source_db, "email_messages", _message("paired-max") | {"id": 3})
        _insert(source_db, "email_classifications", _classification("paired-max") | {"id": 2**63 - 1})
        _insert(source_db, "email_messages", _message("message-only") | {"id": 2})
        source_db.commit()
    finally:
        source_db.close()

    mappings = []
    for number in range(4):
        clone_path = tmp_path / f"collision-clone-{number}.sqlite3"
        shutil.copyfile(source_path, clone_path)
        with sqlite3.connect(clone_path) as db:
            metadata = capture_email_canonical_source(db)
            migrate_email_canonical_schema(db, metadata)
            mappings.append(db.execute(
                "select stable_message_identity,id,classification_id,message_id "
                "from emails order by stable_message_identity"
            ).fetchall())
    assert len({tuple(mapping) for mapping in mappings}) == 1
    assert mappings[0][0] == ("message-only", 1, None, 2)


def test_native_message_upsert_and_ignore_burn_independent_auto_water(tmp_path, current13):
    native_path = tmp_path / "native.sqlite3"
    shutil.copyfile(current13, native_path)
    with sqlite3.connect(native_path) as native:
        values = _message()
        _insert(native, "email_messages", values)
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        sql = ("insert into email_messages(" + ",".join(columns) + ") values(" + placeholders
               + ") on conflict(stable_message_identity) do update set "
               "folder=excluded.folder,uidvalidity=excluded.uidvalidity,uid=excluded.uid,"
               "in_reply_to=excluded.in_reply_to,references_json=excluded.references_json,"
               "thread_identity=excluded.thread_identity,updated_at=excluded.updated_at")
        changed = _message(folder="Archive", thread_identity="new-thread", normalized_text="ignored-body")
        native.execute(sql, tuple(changed.values()))
        native.execute("insert or ignore into email_messages(" + ",".join(columns) + ") values("
                       + placeholders + ")", tuple(values.values()))
        expected = native.execute("select id,folder,thread_identity,normalized_text from email_messages").fetchone()
        native_water = native.execute("select seq from sqlite_sequence where name='email_messages'").fetchone()[0]
        assert native_water == 3
        native.commit()
        with pytest.raises(sqlite3.IntegrityError):
            native.execute(sql, tuple(_message(references_json="broken").values()))
        assert native.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (3,)
        native.execute(sql, tuple(_message(folder="Rollback").values()))
        assert native.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (4,)
        native.rollback()
        assert native.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (3,)
        native.execute("delete from email_messages")
        _insert(native, "email_messages", _message("next"))
        next_native = native.execute("select id from email_messages").fetchone()[0]

    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    assert insert_email_message(db, values) == 1
    assert upsert_email_message(db, changed) == 1
    assert ignore_email_message(db, values) == 1
    assert db.execute("select message_id,message_folder,message_thread_identity,message_normalized_text from emails").fetchone() == expected
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (native_water,)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        upsert_email_message(db, _message(references_json="broken"))
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (3,)
    upsert_email_message(db, _message(folder="Rollback"))
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (4,)
    db.rollback()
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (3,)
    db.execute("delete from emails")
    assert insert_email_message(db, _message("next")) == next_native
    db.close()


def test_nonrepresentable_shared_source_value_is_rejected_before_ddl(tmp_path, current13):
    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message())
    _insert(db, "email_classifications", _classification(uid=21))
    source = capture_email_canonical_source(db)
    with pytest.raises(ValueError, match="shared"):
        migrate_email_canonical_schema(db, source)
    assert db.execute("select count(*) from email_messages").fetchone() == (1,)
    assert db.execute("select count(*) from email_classifications").fetchone() == (1,)
    db.close()


def test_current_updates_and_deletes_project_into_inverse(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    insert_email_classification(db, _classification(included_in_model_id="frozen-model"))
    db.commit()
    assert update_email_classification(db, 101, {"model_text": "revised"}) == 1
    assert db.execute("select model_text,included_in_model_id from ("
                      + email_classification_projection() + ")").fetchone() == ("revised", None)
    assert update_email_message(db, 1, {"normalized_text": "revised body"}) == 1
    db.rollback()
    assert db.execute("select model_text from (" + email_classification_projection() + ")").fetchone() == ("model-body",)
    assert update_email_message(db, 1, {"normalized_text": "current body"}) == 1
    restore_email_canonical_schema(db, source)
    assert db.execute("select normalized_text from email_messages").fetchone() == ("current body",)
    assert db.execute("select model_text,included_in_model_id from email_classifications").fetchone() == ("model-body", "frozen-model")
    db.close()


def test_classification_delete_keeps_message_and_message_delete_clears_row(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    insert_email_classification(db, _classification())
    assert delete_email_classification(db, 101) == 1
    assert db.execute("select classification_id,message_id from emails").fetchone() == (None, 1)
    restore_email_canonical_schema(db, source)
    assert db.execute("select count(*) from email_classifications").fetchone() == (0,)
    assert db.execute("select id from email_messages").fetchone() == (1,)
    db.close()

    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    insert_email_message(db, _message())
    assert delete_email_message(db, 1) == 1
    assert db.execute("select count(*) from emails").fetchone() == (0,)
    assert db.execute("select seq from sqlite_sequence where name='email_messages'").fetchone() == (1,)
    db.close()


def test_native_blob_and_real_storage_classes_survive_roundtrip(tmp_path, current13):
    db = _db(tmp_path, current13)
    _insert(db, "email_messages", _message(normalized_text=b"\x00\xff", received_at=b"raw-time"))
    _insert(db, "email_classifications", _classification(model_text=b"\xff\x00", confidence=float("inf")))
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    assert db.execute("select typeof(message_normalized_text),typeof(message_received_at),"
                      "typeof(class_model_text),typeof(class_confidence) from emails").fetchone() == (
                          "blob", "blob", "blob", "real")
    restore_email_canonical_schema(db, source)
    assert db.execute("select normalized_text,received_at from email_messages").fetchone() == (b"\x00\xff", b"raw-time")
    assert db.execute("select model_text,confidence from email_classifications").fetchone() == (b"\xff\x00", float("inf"))
    db.close()


def test_ordinary_classification_id_space_handles_zero_negative_null_and_max(tmp_path, current13):
    db = _db(tmp_path, current13)
    source = capture_email_canonical_source(db)
    migrate_email_canonical_schema(db, source)
    for name in ("zero", "negative", "maximum", "allocated"):
        insert_email_message(db, _message(name))
    assert insert_email_classification(db, _classification("zero", id=0)) == 0
    assert insert_email_classification(db, _classification("negative", id=-7)) == -7
    assert insert_email_classification(db, _classification("maximum", id=2**63-1)) == 2**63-1
    generated = insert_email_classification(db, _classification("allocated", id=None))
    assert isinstance(generated, int) and 0 < generated < 2**63-1
    assert len({row[0] for row in db.execute("select classification_id from emails")}) == 4
    db.close()
