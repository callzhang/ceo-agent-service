from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from app import action_entity_schema_release as release


SOURCE_VERSION = "2026-10-19.12"
TARGET_VERSION = "2026-10-21.1"
SOURCE_MARKER_TIME = "  original marker time  "


def _source_database(path):
    """Build a synthetic Store19.12/Email53 with its pre-Action declaration."""
    from app.current_action_schema_release import source_objects
    from app.state_storage import insert_state
    from app.task8_storage import register_task8_functions

    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        for kind, name, table, sql in sorted(
                source_objects("fresh_memory_fk"), key=lambda item: item[0] != "table"):
            db.execute(sql)
        db.execute("begin")
        insert_state(db, "service_state", {"key": "store_schema_version", "value": SOURCE_VERSION,
                                            "updated_at": SOURCE_MARKER_TIME})
        db.execute("insert into email_schema_migrations(version,applied_at) values(53,' email marker ')")
        db.commit()


def _prepare(path):
    contract = release.prepare_action_entity_release(path)
    assert contract["source_version"] == SOURCE_VERSION
    return contract


def _snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute(
            "select name from sqlite_schema where type='table'")}
        def rows(query, table):
            return db.execute(query).fetchall() if table in tables else []
        return {
            "marker": tuple(db.execute("select value,updated_at from (select * from state where record_type='service_state') where key='store_schema_version'").fetchone()),
            "actions": db.execute("select id,_kind,legacy_key,record_type,payload_json from actions order by id").fetchall(),
            "receipts": rows("select * from action_receipts order by id", "action_receipts"),
            "observers": rows("select * from action_observers order by action_id,agent_run_id", "action_observers"),
            "history_refs": db.execute("select legacy_key,payload_json from actions where record_type='email_historical_operations' order by legacy_key").fetchall(),
        }


def test_forward_preserves_business_facts_and_receipts(tmp_path):
    path = tmp_path / "actions.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select value from (select * from state where record_type='service_state') where key='store_schema_version'").fetchone() == (TARGET_VERSION,)
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (53,)


def test_conflicting_identity_aborts_without_mutation(tmp_path):
    path = tmp_path / "conflict.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    before = _snapshot(path)
    with pytest.raises(ValueError, match="conflict|identity"):
        release.publish_action_entity_release(path, contract)
    assert _snapshot(path) == before


def test_new_action_and_recall_survive_inverse(tmp_path):
    path = tmp_path / "current-facts.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        from app.action_entity_storage import create_action, append_action_receipt, add_action_observer

        action = create_action(db, values={"operation": "send_reply", "status": "done",
                                           "prepared_body": "native task output"})
        receipt = append_action_receipt(db, action["id"], values={
            "operation": "send_reply", "status": "done", "provider_result_json": '{"id":"provider-1"}',
            "provider_result_id": "provider-1", "observed_at": "provider receipt clock",
        })
        add_action_observer(db, action["id"], agent_run_id=901, task_id=701, created_at="observer clock")
        db.execute("update actions set action_data=jsonb_set(action_data,'$.recall_status',json('" + '"recalled"' + "')) where id=?",
                   (action["id"],))
        db.commit()
    release.invert_action_entity_release(path, contract)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select action_receipt_id from actions where id=?", (action["id"],)).fetchone() == (receipt,)
        assert db.execute("select recall_status from actions where id=?", (action["id"],)).fetchone() == ("recalled",)
        assert db.execute("select count(*) from action_observers where action_id=?", (action["id"],)).fetchone() == (1,)


def test_inverse_does_not_restore_source_snapshot(tmp_path):
    path = tmp_path / "current-data.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    release.invert_action_entity_release(path, contract)
    assert _snapshot(path)["marker"] == (SOURCE_VERSION, SOURCE_MARKER_TIME)


def test_reforward_has_no_duplicate_effect(tmp_path):
    path = tmp_path / "reforward.sqlite3"
    _source_database(path)
    contract = _prepare(path)
    release.publish_action_entity_release(path, contract)
    release.invert_action_entity_release(path, contract)
    second = _prepare(path)
    release.publish_action_entity_release(path, second)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select count(*) from actions where record_type='action'").fetchone() == (0,)


def test_unsupported_source_rejected_before_write(tmp_path):
    path = tmp_path / "unsupported.sqlite3"
    _source_database(path)
    with closing(sqlite3.connect(path)) as db:
        from app.state_storage import update_state_rows
        db.execute("begin")
        update_state_rows(db, "service_state", {"value": "unknown"}, where_sql="key='store_schema_version'")
        db.commit()
    before = _snapshot(path)
    with pytest.raises(ValueError, match="source|schema|version"):
        release.prepare_action_entity_release(path)
    assert _snapshot(path) == before
