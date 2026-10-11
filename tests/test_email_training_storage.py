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


def test_training_migration_retains_latest_complete_snapshot_per_type(tmp_path):
    from app.email_training_data import data_path
    from app.email_training_snapshot import build_folder_training_snapshot
    from test_email_store import _frozen_training_observation
    import app.email_store as email_store_module

    path = tmp_path / "legacy-both-types.sqlite3"
    store = EmailStore(path)
    folder_old = build_folder_training_snapshot(
        [_frozen_training_observation(subject="Old folder")],
        snapshot_id="folder-old", description_version="description-v3",
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), seed=17,
    )
    selected_old = snapshot("selected-old", 2)
    folder_current = build_folder_training_snapshot(
        [_frozen_training_observation(subject="Current folder")],
        snapshot_id="folder-current", description_version="description-v3",
        observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc), seed=17,
    )
    selected_current = snapshot("selected-current", 4)
    selected_corrupt = snapshot("selected-corrupt", 5)
    snapshots = (
        folder_old, selected_old, folder_current, selected_current,
        selected_corrupt,
    )
    for frozen in snapshots:
        store.persist_training_snapshot(frozen)

    with sqlite3.connect(path) as db:
        expected_metadata = {
            row[0]: tuple(row[1:])
            for row in db.execute(
                "select snapshot_id, snapshot_version, snapshot_digest, "
                "folder_label_watermark, important_label_watermark "
                "from email_training_snapshots"
            )
        }
        db.execute("drop trigger trg_email_training_snapshots_immutable_update")
        db.execute("drop trigger trg_email_training_observations_require_unfrozen_snapshot")
        for frozen in snapshots:
            db.execute(
                "update email_training_snapshots set manifest_json=? where snapshot_id=?",
                (json.dumps(frozen.manifest), frozen.snapshot_id),
            )
            for observation in frozen.observations:
                fields = observation.to_dict()
                fields["important"] = int(fields["important"])
                fields["selected_for_training"] = int(fields["selected_for_training"])
                columns = ", ".join(fields)
                placeholders = ", ".join("?" for _ in fields)
                db.execute(
                    f"insert into email_training_snapshot_observations ({columns}) "
                    f"values ({placeholders})",
                    tuple(fields.values()),
                )
        db.execute("drop trigger trg_email_training_observations_immutable_update")
        db.execute(
            "update email_training_snapshot_observations "
            "set ordered_record_digest=? where snapshot_id=?",
            ("0" * 64, selected_corrupt.snapshot_id),
        )
        db.execute(email_store_module._REQUIRED_TRIGGER_SQL[
            "trg_email_training_observations_immutable_update"
        ])
        db.execute(email_store_module._REQUIRED_TRIGGER_SQL[
            "trg_email_training_snapshots_immutable_update"
        ])
        db.execute("delete from email_schema_migrations where version=46")
        db.execute(
            "insert into email_schema_migrations(version, applied_at) "
            "values (45, '2026-10-09T00:00:00+00:00')"
        )
    for file in (tmp_path / "legacy-both-types-training-data").glob("*.json"):
        file.unlink()

    migrated = EmailStore(path)
    migrated.prune_training_snapshot_data(training_active=False)
    for frozen in (folder_current, selected_current):
        restored = migrated.get_training_snapshot(frozen.snapshot_id)
        assert restored["snapshot_id"] == frozen.snapshot_id
        assert restored["snapshot_version"] == frozen.snapshot_version
        assert restored["snapshot_digest"] == frozen.snapshot_digest
        assert restored["manifest"] == frozen.manifest
        assert restored["observations"] == [row.to_dict() for row in frozen.observations]
        assert data_path(path, frozen.snapshot_digest).is_file()
    for frozen in (folder_old, selected_old, selected_corrupt):
        with pytest.raises(RuntimeError, match="unavailable"):
            migrated.get_training_snapshot(frozen.snapshot_id)
        assert not data_path(path, frozen.snapshot_digest).exists()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select count(*) from email_training_snapshot_observations"
        ).fetchone()[0] == 0
        metadata = {
            row[0]: tuple(row[1:])
            for row in db.execute(
                "select snapshot_id, snapshot_version, snapshot_digest, "
                "folder_label_watermark, important_label_watermark "
                "from email_training_snapshots"
            )
        }
        assert metadata == expected_metadata
        summaries = {
            row[0]: json.loads(row[1]) for row in db.execute(
                "select snapshot_id, manifest_json from email_training_snapshots"
            )
        }
        assert summaries[selected_corrupt.snapshot_id] == {}
        assert all("sample_count" in summary and "observations" not in summary
                   for snapshot_id, summary in summaries.items()
                   if snapshot_id != selected_corrupt.snapshot_id)
        assert "private training body" not in " ".join(
            row[0] for row in db.execute(
                "select manifest_json from email_training_snapshots"
            )
        )
