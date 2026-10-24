import hashlib
import sqlite3

import pytest


def _source_database(path):
    from app.current_schema_manifest import create_current_email_migration_ledger
    from app.predecessor_database_schema import create_store_2026_10_21_1_schema
    from app.state_storage import upsert_state

    db = sqlite3.connect(path)
    create_store_2026_10_21_1_schema(db)
    create_current_email_migration_ledger(db)
    db.executemany(
        "insert into email_schema_migrations(version,applied_at) values(?,?)",
        ((version, f"email-{version}") for version in range(1, 54)),
    )
    db.commit()
    db.execute("begin")
    upsert_state(db, "service_state",
                 {"key": "store_schema_version", "value": "2026-10-21.1"},
                 {"value": "json_quote(excluded.value)"})
    db.commit()
    db.close()


def test_paired_task_run_release_publishes_inverses_and_reforwards(tmp_path):
    from app.current_schema_manifest import require_current_pair
    from app.predecessor_database_schema import PREDECESSOR_STORE_VERSION
    from app.store_schema_release import prepare_database_schema_release
    from app.task_run_entity_schema_release import TARGET_STORE_VERSION

    path = tmp_path / "production-shaped-source.sqlite3"
    _source_database(path)
    with sqlite3.connect(path) as db:
        source_catalog = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from main.sqlite_schema "
            "where name not glob 'sqlite_*' order by type,name"))
        source_digest = hashlib.sha256(repr(source_catalog).encode()).hexdigest()
        assert source_digest == "920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290"
        from app.state_definitions import state_projection
        marker = db.execute(f"select updated_at from ({state_projection('service_state')}) "
                            "where key='store_schema_version'").fetchone()[0]
        original_fk = tuple(tuple(row) for row in db.execute("pragma foreign_key_check"))

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    first = prepare_database_schema_release(root, path)
    assert first is not None
    first.publish()
    require_current_pair(path)
    from app import task_entity_storage, run_entity_storage
    from app.action_storage import insert_action
    from app.store import AutoReplyStore
    store = AutoReplyStore.__new__(AutoReplyStore)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin")
        now = db.execute("select current_timestamp").fetchone()[0]
        task = task_entity_storage.create_task(db, values={
            "kind": "reply", "status": "done", "available_at": "", "attempts": 0,
            "error": "", "execution_generation": "gen-1",
            "claimed_input_version": 0,
            "created_at": now, "updated_at": now,
        }, parameters={"force_new_decision": 0, "manual_rerun_attempt_id": 0,
                       "manual_rerun_revision_key": "", "recovery_code": ""})
        task_input, _ = task_entity_storage.append_task_input(db, task_id=task["id"], values={
            "channel": "dingtalk", "conversation_id": "roundtrip", "conversation_title": "RT",
            "single_chat": 0, "trigger_message_id": "roundtrip-message",
            "trigger_create_time": now, "trigger_sender": "user", "trigger_text": "body",
            "business_object_key": "message:dingtalk:roundtrip:roundtrip-message",
            "input_revision_key": "rt-1",
        })
        assert task_entity_storage.bind_current_input(
            db, task_id=task["id"], input_id=task_input["id"],
            expected_input_version=None, input_version=1,
        )
        task_entity_storage.associate_business_key(
            db, task_id=task["id"], business_key=task_input["business_object_key"],
            created_at=now, updated_at=now,
        )
        consumer = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "execution_generation": "gen-1", "role": "consumer",
            "status": "completed", "proposal_revision": 0, "turn_attempt": 0,
            "operation_id": "roundtrip-consumer", "session_id": "",
            "transcript_start": 0, "transcript_end": 0, "structured_error_json": "{}",
            "error": None, "lease_owner": "", "lease_expires_at": "",
            "started_at": now, "completed_at": now, "created_at": now, "updated_at": now,
        })
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"],
            "execution_generation": "gen-1", "role": "audit", "status": "completed",
            "proposal_revision": 0, "turn_attempt": 0,
            "operation_id": "roundtrip-audit", "session_id": "",
            "transcript_start": 0, "transcript_end": 0, "structured_error_json": "{}",
            "error": None, "lease_owner": "", "lease_expires_at": "",
            "started_at": now, "completed_at": now, "created_at": now, "updated_at": now,
        })
        store._adopt_review_candidate_in_connection(db, consumer, {
            "outcome": "proposal", "proposal": {"actions": [{"id": "effect-1"}]},
        })
        candidate = store._candidate_context(db, consumer["id"])
        store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], {
            "outcome": "approve", "candidate_digest": candidate["candidate_digest"],
        })
        attempt = insert_action(db, "reply_attempts", {
            "conversation_id": "roundtrip", "conversation_title": "RT",
            "trigger_message_id": "roundtrip-message", "trigger_sender": "user",
            "trigger_text": "body", "action": "agent_run", "sensitivity_kind": "general",
            "send_status": "skipped", "agent_run_id": audit["id"],
        })
        db.commit()
    assert attempt.lastrowid is not None
    with sqlite3.connect(path) as db:
        from app.state_definitions import state_projection
        assert db.execute(f"select value from ({state_projection('service_state')}) "
                          "where key='store_schema_version'").fetchone()[0] == TARGET_STORE_VERSION
        assert not tuple(db.execute("pragma foreign_key_check"))
    first.rollback()
    with sqlite3.connect(path) as db:
        restored_catalog = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from main.sqlite_schema "
            "where name not glob 'sqlite_*' order by type,name"))
        assert restored_catalog == source_catalog
        assert hashlib.sha256(repr(restored_catalog).encode()).hexdigest() == source_digest
        assert db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                          "where key='store_schema_version'").fetchone() == (
                              PREDECESSOR_STORE_VERSION, marker)
        assert tuple(tuple(row) for row in db.execute("pragma foreign_key_check")) == original_fk
        assert db.execute("select count(*) from tasks where record_type='reply_tasks'").fetchone()[0] == 1
        assert db.execute("select count(*) from runs where record_type='agent_runs'").fetchone()[0] == 2
        assert db.execute("select count(*) from actions where record_type='reply_attempts'").fetchone()[0] == 1
        assert db.execute("select count(*) from actions where record_type='candidate_reviews'").fetchone()[0] == 1

    second = prepare_database_schema_release(root, path)
    assert second is not None
    second.publish()
    require_current_pair(path)
    second.finalize()
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from tasks where kind='reply'").fetchone()[0] == 1
        assert db.execute("select count(*) from runs where role in ('consumer','audit')").fetchone()[0] == 2
        assert db.execute("select count(*) from run_adopted_reviews where decision='approve'").fetchone()[0] == 1


def test_work_summary_error_message_reference_follows_typed_task_inverse(tmp_path):
    from app.current_action_schema import current_action_payloads
    from app.action_storage import insert_error_action
    from app.task8_storage import register_task8_functions, task8_insert_parts
    from app.task_run_entity_schema_release import prepare_task_run_entity_release

    path = tmp_path / "work-summary-error.sqlite3"
    _source_database(path)
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        parts = task8_insert_parts("work_summary_inputs", {
            "legacy_key": "2", "source_type": "local_file", "source_ref": "input-2",
            "payload_json": "{}", "status": "done", "attempts": 0,
            "error": "", "created_at": "created", "updated_at": "updated",
            "available_at": "", "source_created_at": "", "body_sha256": "",
            "body_bytes": 0, "body_compacted": 0,
        })
        db.execute(f"insert into tasks ({parts.columns}) values ({parts.values})", parts.parameters)
        insert_error_action(db, {
            "conversation_id": "work_summary_input", "message_id": "2",
            "kind": "task_agent", "detail": "retry", "created_at": "error-time",
            "resolved_at": "", "resolution": "",
        })
        db.commit()
    release = prepare_task_run_entity_release(path)
    assert release is not None
    release.publish()
    path_to_message = current_action_payloads().path("errors", "message_id")
    with sqlite3.connect(path) as db:
        task_id = db.execute(
            "select id from tasks where kind='work_summary'"
        ).fetchone()[0]
        assert task_id != 2, "fixture must exercise a changed canonical Task identity"
        encoded = db.execute(
            "select payload_json from actions where record_type='errors'"
        ).fetchone()[0]
        assert db.execute("select json_extract(?,?)", (encoded, path_to_message)).fetchone()[0] == str(task_id)
    release.rollback()
    with sqlite3.connect(path) as db:
        encoded = db.execute(
            "select payload_json from actions where record_type='errors'"
        ).fetchone()[0]
        assert db.execute("select json_extract(?,?)", (encoded, path_to_message)).fetchone()[0] == "2"
    reforward = prepare_task_run_entity_release(path)
    assert reforward is not None
    reforward.publish()
    with sqlite3.connect(path) as db:
        native_id = db.execute("select id from tasks where kind='work_summary'").fetchone()[0]
        encoded = db.execute(
            "select payload_json from actions where record_type='errors'"
        ).fetchone()[0]
        assert db.execute("select json_extract(?,?)", (encoded, path_to_message)).fetchone()[0] == str(native_id)
    reforward.rollback()




def test_public_reply_enqueue_and_run_claim_roundtrip_to_source_and_reforward(tmp_path):
    import sqlite3
    from pathlib import Path

    from app.current_schema_manifest import require_current_pair
    from app.store import AgentRole, AutoReplyStore
    from app.store_schema_release import prepare_database_schema_release

    path = tmp_path / "public-api-source.sqlite3"
    _source_database(path)
    root = Path(__file__).resolve().parents[1]
    release = prepare_database_schema_release(root, path)
    assert release is not None
    release.publish()

    store = AutoReplyStore(path)
    assert store.enqueue_reply_task(
        conversation_id="public-roundtrip", conversation_title="Public API",
        single_chat=False, trigger_message_id="public-message",
        trigger_create_time="2026-10-23 12:00:00", trigger_sender="user",
        trigger_text="round trip", execution_generation="public-generation",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    consumer_claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="public-api-test",
    )
    assert consumer_claim.run.status == "running"

    release.rollback()
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        from app.task20_definitions import task20_projection
        business_key = db.execute(
            f"select * from ({task20_projection('business_object_tasks')}) where reply_task_id=?",
            (task.id,),
        ).fetchone()
        assert business_key is not None
        assert business_key["created_at"] is not None
        assert business_key["updated_at"] is not None
        source_run = db.execute(
            "select * from runs where task_id=? and role='consumer'", (task.id,)
        ).fetchone()
        assert source_run is not None
        assert source_run["error"] is None
        assert source_run["status"] == "running"

    reforward = prepare_database_schema_release(root, path)
    assert reforward is not None
    reforward.publish()
    require_current_pair(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select count(*) from tasks where id=? and kind='reply'", (task.id,)
        ).fetchone()[0] == 1
        native_run = db.execute(
            "select error,status from runs where task_id=? and role='consumer'", (task.id,)
        ).fetchone()
        assert native_run == (None, "running")


def test_paired_release_rejects_mixed_source_and_rechecks_before_writing(tmp_path):
    from pathlib import Path
    from app.store_schema_release import prepare_database_schema_release

    root = Path(__file__).resolve().parents[1]
    mixed = tmp_path / "mixed.sqlite3"
    _source_database(mixed)
    with sqlite3.connect(mixed) as db:
        db.execute("create table mixed_schema_object(id integer)")
        db.commit()
        before = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not glob 'sqlite_*' order by type,name"))
    with pytest.raises(ValueError, match="unsupported Store 2026-10-21.1 / Email53 source schema"):
        prepare_database_schema_release(root, mixed)
    with sqlite3.connect(mixed) as db:
        after = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not glob 'sqlite_*' order by type,name"))
        assert after == before

    changed = tmp_path / "changed-after-prepare.sqlite3"
    _source_database(changed)
    release = prepare_database_schema_release(root, changed)
    assert release is not None
    with sqlite3.connect(changed) as db:
        db.execute("create table preparation_drift(id integer)")
        db.commit()
        before = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not glob 'sqlite_*' order by type,name"))
    with pytest.raises(ValueError, match="unsupported Store 2026-10-21.1 / Email53 source schema"):
        release.publish()
    with sqlite3.connect(changed) as db:
        after = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where name not glob 'sqlite_*' order by type,name"))
        assert after == before


def test_paired_release_rollback_is_idempotent_before_and_after_failed_publish(tmp_path, monkeypatch):
    from pathlib import Path
    from app.current_schema_manifest import schema_objects
    from app.store_schema_release import prepare_database_schema_release
    import app.run_entity_schema as run_schema

    path = tmp_path / "failed-publish.sqlite3"
    _source_database(path)
    root = Path(__file__).resolve().parents[1]
    release = prepare_database_schema_release(root, path)
    assert release is not None
    with sqlite3.connect(path) as db:
        source_catalog = schema_objects(db)
    release.rollback()  # prepare-then-rollback is a verified no-op
    assert schema_objects(sqlite3.connect(path)) == source_catalog

    def fail_after_task_conversion(*args, **kwargs):
        raise RuntimeError("injected Run conversion failure")

    monkeypatch.setattr(run_schema, "migrate_run_entities", fail_after_task_conversion)
    with pytest.raises(RuntimeError, match="injected Run conversion failure"):
        release.publish()
    release.rollback()  # publish rolls back its caller-owned transaction first
    with sqlite3.connect(path) as db:
        assert schema_objects(db) == source_catalog
        from app.state_definitions import state_projection
        assert db.execute(
            f"select value,updated_at from ({state_projection('service_state')}) where key='store_schema_version'"
        ).fetchone() == release.original_version
        assert not tuple(db.execute("pragma foreign_key_check"))
