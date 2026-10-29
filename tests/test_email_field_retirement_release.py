from contextlib import closing
import json
import sqlite3

import pytest

from app.current_schema_manifest import schema_objects, schema_is_current
from app.email_field_retirement_release import (
    prepare_email_field_retirement_release, source_objects, _plan_payload,
)
from app.task8_storage import register_task8_functions
from tests.support.store28_5_source import create_store28_5_source


def _connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    register_task8_functions(db)
    return closing(db)


def _source(path):
    with _connect(path) as db:
        db.execute("begin")
        create_store28_5_source(db)
        db.commit()


def _input(db, *, source, identity="synthetic-email", channel="email", compact=True):
    from app.task_entity_storage import create_task
    task = create_task(db, values={"kind": "email_classification", "status": "done",
                                  "created_at": "source-time", "updated_at": "source-time"})
    payload = {"input_compacted": True, "input_sha256": "a" * 64, "input_bytes": 71,
               "identity_sha256": "b" * 64, "provider_locator": {"uid": 17},
               "config_version": "v1", "unsubscribe_candidates": []}
    if source:
        payload["stable_message_identity"] = identity
    if not compact:
        payload = {"stable_message_identity": identity, "active": "original input"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    columns = "task_id,channel,stable_message_identity,input_json" if source else "task_id,stable_message_identity,input_json"
    values = (task["id"], channel, identity, raw) if source else (task["id"], identity, raw)
    db.execute(f"insert into email_classification_inputs({columns}) values({','.join('?' for _ in values)})", values)
    return task["id"], raw


def test_empty_source_forward_inverse_and_exact_current_declaration(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    release = prepare_email_field_retirement_release(path)
    release.publish()
    release.verify_loaded()
    with _connect(path) as db:
        assert schema_is_current(db)
        assert "channel" not in {r[1] for r in db.execute("pragma table_info(email_classification_inputs)")}
        assert "class_legacy_processed_without_plan" not in {r[1] for r in db.execute("pragma table_xinfo(emails)")}
        assert "email_legacy_serialization_pre_v16" not in {r[1] for r in db.execute("pragma table_xinfo(actions)")}
    release.rollback()
    with _connect(path) as db:
        assert schema_objects(db) == source_objects()
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_input_owner_retirement_preserves_hashes_active_input_and_new_writes(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    with _connect(path) as db:
        db.execute("begin immediate")
        old_id, original = _input(db, source=True)
        active_id, active = _input(db, source=True, identity="active-email", compact=False)
        db.commit()
    release = prepare_email_field_retirement_release(path)
    release.publish()
    with _connect(path) as db:
        raw = db.execute("select input_json from email_classification_inputs where task_id=?", (old_id,)).fetchone()[0]
        data = json.loads(raw)
        assert "stable_message_identity" not in data
        assert data["input_sha256"] == "a" * 64 and data["input_bytes"] == 71
        assert data["identity_sha256"] == "b" * 64
        assert db.execute("select input_json from email_classification_inputs where task_id=?", (active_id,)).fetchone()[0] == active
        db.execute("begin immediate")
        new_id, _ = _input(db, source=False, identity="new-email")
        db.commit()
    release.rollback()
    with _connect(path) as db:
        assert db.execute("select input_json from email_classification_inputs where task_id=?", (old_id,)).fetchone()[0] == original
        restored = db.execute("select channel,input_json from email_classification_inputs where task_id=?", (new_id,)).fetchone()
        assert restored[0] == "email" and json.loads(restored[1])["stable_message_identity"] == "new-email"
    again = prepare_email_field_retirement_release(path)
    again.publish()
    again.finalize()


@pytest.mark.parametrize("wrong", ["other-channel", ""])
def test_non_email_channel_is_rejected_before_mutation(tmp_path, wrong):
    path = tmp_path / "source.sqlite3"
    _source(path)
    with _connect(path) as db:
        db.execute("begin immediate")
        _input(db, source=True, channel=wrong)
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="channel"):
        prepare_email_field_retirement_release(path)
    assert path.read_bytes() == before


def test_source_input_identity_conflict_is_rejected_before_mutation(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    with _connect(path) as db:
        db.execute("begin immediate")
        task_id, _ = _input(db, source=True)
        db.execute("update email_classification_inputs set stable_message_identity='different' where task_id=?", (task_id,))
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="identity"):
        prepare_email_field_retirement_release(path)
    assert path.read_bytes() == before


def test_source_change_after_prepare_is_revalidated(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    release = prepare_email_field_retirement_release(path)
    with _connect(path) as db:
        db.execute("begin immediate")
        _input(db, source=True, channel="changed")
        db.commit()
    with pytest.raises(ValueError, match="channel"):
        release.publish()
    with _connect(path) as db:
        assert schema_objects(db) == source_objects()


def test_unpublished_rollback_does_not_claim_a_changed_source_is_restored(tmp_path):
    path = tmp_path / "source.sqlite3"
    _source(path)
    release = prepare_email_field_retirement_release(path)
    release.rollback()
    with _connect(path) as db:
        db.execute("create table unrelated(value)")
        db.commit()
    with pytest.raises(ValueError, match="exact source"):
        release.rollback()


def test_inverse_rejects_a_mixed_email_ledger_before_mutation(tmp_path):
    path = tmp_path / "mixed.sqlite3"
    _source(path)
    release = prepare_email_field_retirement_release(path)
    release.publish()
    with _connect(path) as db:
        db.execute("update email_schema_migrations set version=53 where version=54")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="exact published target"):
        release.rollback()
    assert path.read_bytes() == before


def test_plan_marker_is_a_reconstructible_zero_without_reencoding_other_cells():
    with closing(sqlite3.connect(":memory:")) as db:
        values = ["plan", 1, 17, "account", "important", "model", 0.9, "model", "v1", "[]", "{}", None, 0, "original-time"]
        raw = db.execute("select jsonb(?)", (json.dumps(values),)).fetchone()[0]
        target = _plan_payload(raw, inverse=False)
        assert _plan_payload(target, inverse=True) == raw
        projected = json.loads(db.execute("select json(?)", (target,)).fetchone()[0])
        assert projected == values[:12] + values[13:]
        values[12] = 1
        wrong = db.execute("select jsonb(?)", (json.dumps(values),)).fetchone()[0]
        with pytest.raises(ValueError, match="zero marker"):
            _plan_payload(wrong, inverse=False)
