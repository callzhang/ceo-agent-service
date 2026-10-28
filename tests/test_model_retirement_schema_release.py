"""Explicit Store26.1 -> Store28.1 conversion of current facts, never snapshots."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.configs_storage import insert_config
from app.model_retirement_schema_source import store26_objects
from app.model_retirement_schema_release import (
    prepare_model_retirement_release,
    retirement_target_objects,
)
from app.state_definitions import state_projection
from app.state_storage import insert_state
from app.task8_storage import register_task8_functions


PROMOTION = {
    "config_version": "p1", "micro_f1_min": 0.8,
    "category_precision_min": 0.7, "category_validation_samples_min": 5,
    "p95_latency_max_ms": 200.0, "created_at": "fixture-clock",
}


def _digest(objects):
    return hashlib.sha256(json.dumps(objects, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _objects(db):
    return tuple(tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_schema "
        "where name not glob 'sqlite_*' order by type,name"))


def _open(path):
    db = sqlite3.connect(path, isolation_level=None)
    register_task8_functions(db)
    return db


def _source(path):
    with closing(_open(path)) as db:
        db.execute("begin")
        order = {"table": 0, "index": 1, "trigger": 2}
        for _, _, _, sql in sorted(store26_objects(), key=lambda row: (order[row[0]], row[1])):
            db.execute(sql)
        insert_state(db, "service_state", {
            "key": "store_schema_version", "value": "2026-10-26.1",
            "updated_at": "original-marker-clock",
        })
        db.execute("insert into email_schema_migrations values(54,'original-ledger-clock')")
        insert_config(db, "email_model_promotion_configs", PROMOTION)
        db.execute("insert into run_entity_identity_map(rowid,source_kind,physical_id,"
                   "public_key,public_key_type,public_key_order,target_id,allocator_highwater) "
                   "values(987,'agent_runs',7,'public-key','text',3,99,null)")
        db.execute("update sqlite_sequence set seq=9000 where name='configs'")
        db.execute("insert into sqlite_sequence(rowid,name,seq) "
                   "values(2001,'fixture-watermark','9001')")
        db.commit()


def _facts(db):
    facts = {}
    retired = {"transition_promotion_config_version", "transition_promotion_ref", "allocator_highwater"}
    for table in ("configs", "run_entity_identity_map"):
        fields = [row[1] for row in db.execute(f"pragma table_xinfo({table})")
                  if row[6] == 0 and row[1] not in retired]
        names = ",".join('"' + name + '"' for name in fields)
        facts[table] = tuple(db.execute(f"select rowid,{names} from {table} order by rowid"))
    facts["sequences"] = tuple(db.execute("select rowid,name,seq,typeof(seq) from sqlite_sequence order by rowid"))
    return facts


def test_forward_inverse_preserves_current_rows_types_hidden_rowids_and_watermarks(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        original = _facts(db)
    release = prepare_model_retirement_release(path)
    release.publish()
    release.verify_loaded()
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(retirement_target_objects())
        assert len(_objects(db)) == 463
        assert _facts(db) == original
        assert list(db.execute("pragma foreign_key_check")) == []
        db.execute("begin")
        insert_config(db, "email_model_promotion_configs", PROMOTION | {"config_version": "p2"})
        db.execute("insert into run_entity_identity_map(rowid,source_kind,physical_id,"
                   "public_key,public_key_type,public_key_order,target_id) "
                   "values(1500,'agent_runs',8,x'00ff','blob',4,100)")
        db.execute("update sqlite_sequence set seq=12000 where name='configs'")
        db.commit()
        current = _facts(db)
    release.rollback()
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(store26_objects())
        assert _facts(db) == current
        assert db.execute("select count(*) from run_entity_identity_map "
                          "where allocator_highwater is not null").fetchone()[0] == 0
        assert db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                          "where key='store_schema_version'").fetchone() == (
                              "2026-10-26.1", "original-marker-clock")
    again = prepare_model_retirement_release(path)
    again.publish()
    again.finalize()
    with closing(_open(path)) as db:
        assert _facts(db) == current


def test_source_nonnull_highwater_is_not_silently_retired(tmp_path):
    path = tmp_path / "highwater.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        db.execute("update run_entity_identity_map set allocator_highwater=123")
    with pytest.raises(ValueError, match="allocator_highwater"):
        prepare_model_retirement_release(path)
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(store26_objects())
        assert db.execute("select allocator_highwater from run_entity_identity_map").fetchone() == (123,)


def test_nonempty_retired_transition_family_is_rejected_without_deleting_facts(tmp_path):
    path = tmp_path / "transition.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        parent = db.execute("select id from configs where record_type='email_model_promotion_configs'").fetchone()[0]
        payload = json.dumps({"actor": "actor", "from_mode": "agent_primary",
                              "to_mode": "model_primary", "from_model_id": None,
                              "target_model_id": None, "status": "applied",
                              "reason": "", "created_at": "fixture-clock"})
        db.execute("insert into configs(record_type,legacy_key,payload_json,"
                   "transition_promotion_config_version,transition_promotion_ref) "
                   "values('email_model_mode_transitions',?,?,?,?)",
                   (json.dumps(["request"]), payload, "p1", parent))
    with pytest.raises(ValueError, match="email_model_mode_transitions"):
        prepare_model_retirement_release(path)
    with closing(_open(path)) as db:
        assert db.execute("select count(*) from configs "
                          "where record_type='email_model_mode_transitions'").fetchone()[0] == 1
        assert _digest(_objects(db)) == _digest(store26_objects())


def test_failure_after_first_table_rebuild_rolls_back_entire_source(tmp_path, monkeypatch):
    from app import model_retirement_schema_release as module

    path = tmp_path / "atomic.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        before = _facts(db)
    release = prepare_model_retirement_release(path)
    original = module._rebuild_table

    def interrupted(db, table, *, target):
        original(db, table, target=target)
        if table == "configs":
            raise RuntimeError("injected interruption after Configs rebuild")

    monkeypatch.setattr(module, "_rebuild_table", interrupted)
    with pytest.raises(RuntimeError, match="injected interruption"):
        release.publish()
    release.rollback()
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(store26_objects())
        assert _facts(db) == before
        assert list(db.execute("pragma foreign_key_check")) == []


def test_partial_source_is_rejected_before_any_schema_or_fact_change(tmp_path):
    path = tmp_path / "partial.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        db.execute("drop index idx_codex_session_id")
        before, facts = _digest(_objects(db)), _facts(db)
    with pytest.raises(ValueError, match="unsupported Store26.1"):
        prepare_model_retirement_release(path)
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == before
        assert _facts(db) == facts


def test_current_constructor_does_not_convert_old_schema_or_touch_its_facts(tmp_path):
    from app.store import AutoReplyStore

    path = tmp_path / "runtime-source.sqlite3"
    _source(path)
    with closing(_open(path)) as db:
        before = _facts(db)
    with pytest.raises(sqlite3.IntegrityError, match="explicit paired schema release required"):
        AutoReplyStore(path)
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(store26_objects())
        assert _facts(db) == before



def test_copy_fixture_uses_current_cold_store_and_fake_receipt_without_cli(tmp_path):
    from tools.validate_model_retirement_copy import _seed

    path = tmp_path / "fixture.sqlite3"
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    AutoReplyStore(path)
    EmailStore(path)
    first = _seed(Path(__file__).resolve().parents[1], path, [])
    second = _seed(Path(__file__).resolve().parents[1], path, [first])
    assert first["task_id"] != second["task_id"]
    with closing(_open(path)) as db:
        body = db.execute("select provider_result_json from action_receipts where id=?",
                          (second["receipt_id"],)).fetchone()[0]
        assert json.loads(body) == {"provider": "test_double", "ok": True}


def test_deployment_ledger_writes_before_after_and_inverse_use_same_native_state(tmp_path):
    from app.repository_updater import ExistingSchemaUpgradeStateStore

    path = tmp_path / "ledger.sqlite3"
    _source(path)
    state = ExistingSchemaUpgradeStateStore(path)
    state.bind_committed_schema()
    state.set_service_state("retirement-ledger-fixture", "updating")
    release = prepare_model_retirement_release(path)
    release.publish()
    state.bind_committed_schema()
    assert state.get_service_state("retirement-ledger-fixture") == "updating"
    state.set_service_state("retirement-ledger-fixture", "restarting")
    release.rollback()
    state.bind_committed_schema()
    assert state.get_service_state("retirement-ledger-fixture") == "restarting"
    with closing(_open(path)) as db:
        assert _digest(_objects(db)) == _digest(store26_objects())
