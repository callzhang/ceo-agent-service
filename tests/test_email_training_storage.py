from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.email_store import EmailStore
from app.email_model_registry import EmailModelRegistry
from app.email_classifier_retrain import SnapshotTrainingSignal, TrainingSubprocessController
from app.email_training_snapshot import build_selected_training_snapshot


def snapshot(snapshot_id: str, day: int):
    return build_selected_training_snapshot(
        [{
            "source": "agent_auto_label",
            "account_id": "account-a",
            "stable_message_identity": f"account-a:{snapshot_id}",
            "provider_thread_id": f"thread-{snapshot_id}",
            "category_key": "work",
            "normalized_model_input": json.dumps({"body": f"private training body {snapshot_id}"}),
        }],
        snapshot_id=snapshot_id,
        description_version="selected-training-input-v1",
        observed_at=datetime(2026, 10, day, tzinfo=timezone.utc),
        seed=17,
    )


def test_training_payload_lives_outside_sqlite_and_old_data_is_unavailable(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    first = snapshot("first", 1)
    second = snapshot("second", 2)

    store.persist_training_snapshot(first)
    store.persist_training_snapshot(second)

    assert store.get_training_snapshot("second")["observations"] == [
        row.to_dict() for row in second.observations
    ]
    assert store.prune_training_snapshot_data(training_active=True) == []
    assert len(list((tmp_path / "email-training-data").glob("*.json"))) == 2
    store.prune_training_snapshot_data(training_active=False)
    assert len(list((tmp_path / "email-training-data").glob("*.json"))) == 1
    with pytest.raises(RuntimeError, match="unavailable"):
        store.get_training_snapshot("first")
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from email_training_snapshot_observations").fetchone()[0] == 0
        assert "private training body" not in " ".join(
            str(row) for row in db.execute("select manifest_json from email_training_snapshots")
        )


def test_active_training_pin_reads_superseded_snapshot_only_in_its_run(tmp_path):
    path = tmp_path / "email.sqlite3"
    store = EmailStore(path)
    first = snapshot("first", 1)
    store.persist_training_snapshot(first)
    store.pin_training_snapshot("run-one", "first")
    store.persist_training_snapshot(snapshot("second", 2))

    with pytest.raises(RuntimeError, match="unavailable"):
        store.get_training_snapshot("first")
    child = EmailStore(path, training_run_id="run-one")
    assert child.get_training_snapshot("first")["snapshot_digest"] == first.snapshot_digest
    store.prune_training_snapshot_data(training_active=False)
    with pytest.raises(RuntimeError, match="unavailable"):
        child.get_training_snapshot("first")


def test_controller_pins_snapshot_before_launch(tmp_path):
    path = tmp_path / "email.sqlite3"
    store = EmailStore(path)
    first = snapshot("first", 1)
    store.persist_training_snapshot(first)
    controller = TrainingSubprocessController(
        EmailModelRegistry(tmp_path / "models"), store_path=path,
        launcher=lambda command: SimpleNamespace(pid=1234, poll=lambda: None),
    )
    signal = SnapshotTrainingSignal(
        snapshot_sha=first.snapshot_digest, description_version="v1",
        folder_label_watermark=0, important_label_watermark=0,
        minimum_ready=True,
    )
    run = controller.start(now=datetime(2026, 10, 1, tzinfo=timezone.utc),
                           signal=signal, snapshot_id="first")
    store.persist_training_snapshot(snapshot("second", 2))
    assert EmailStore(path, training_run_id=run.run_id).get_training_snapshot("first")


@pytest.mark.parametrize("corrupt_latest", [False, True])
def test_training_storage_migrates_latest_complete_legacy_snapshot(tmp_path, corrupt_latest):
    from test_email_store import _frozen_training_snapshot, _frozen_training_observation
    from app.email_training_snapshot import build_folder_training_snapshot
    import app.email_store as email_store_module

    path = tmp_path / "legacy.sqlite3"
    store = EmailStore(path)
    old = _frozen_training_snapshot("old")
    new = build_folder_training_snapshot(
        [_frozen_training_observation(subject="New observation")],
        snapshot_id="new", description_version="description-v3",
        observed_at=datetime(2026, 9, 8, 18, tzinfo=timezone.utc), seed=17,
    )
    store.persist_training_snapshot(old)
    store.persist_training_snapshot(new)
    with sqlite3.connect(path) as db:
        db.execute("drop trigger trg_email_training_snapshots_immutable_update")
        db.execute("drop trigger trg_email_training_observations_require_unfrozen_snapshot")
        for frozen in (old, new):
            db.execute(
                "update email_training_snapshots set manifest_json=? where snapshot_id=?",
                (json.dumps(frozen.manifest), frozen.snapshot_id),
            )
            for row in frozen.observations:
                fields = row.to_dict()
                fields["important"] = int(fields["important"])
                fields["selected_for_training"] = int(fields["selected_for_training"])
                columns = ", ".join(fields)
                placeholders = ", ".join("?" for _ in fields)
                db.execute(
                    f"insert into email_training_snapshot_observations ({columns}) values ({placeholders})",
                    tuple(fields.values()),
                )
        db.execute(email_store_module._REQUIRED_TRIGGER_SQL["trg_email_training_snapshots_immutable_update"])
        if corrupt_latest:
            db.execute("drop trigger trg_email_training_observations_immutable_update")
            db.execute("update email_training_snapshot_observations "
                       "set ordered_record_digest=? where snapshot_id='new'", ("0" * 64,))
            db.execute(email_store_module._REQUIRED_TRIGGER_SQL["trg_email_training_observations_immutable_update"])
        db.execute("delete from email_schema_migrations where version=46")
        db.execute("insert into email_schema_migrations(version, applied_at) values (45, '2026-10-09T00:00:00+00:00')")
    for file in (tmp_path / "legacy-training-data").glob("*.json"):
        file.unlink()
    migrated = EmailStore(path)

    kept, unavailable = (old, new) if corrupt_latest else (new, old)
    assert migrated.get_training_snapshot(kept.snapshot_id)["snapshot_digest"] == kept.snapshot_digest
    with pytest.raises(RuntimeError, match="unavailable"):
        migrated.get_training_snapshot(unavailable.snapshot_id)
