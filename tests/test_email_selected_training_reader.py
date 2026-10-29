"""Selected training input sources: real final SQLite and frozen external data."""

from tests.support.email_native_fixtures import mark_processed_with_plan

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests import test_email_classification_reader as fixtures
from tests.test_email_training_label_metadata import eligible
from app.email_training_snapshot import (
    build_selected_training_snapshot,
    canonical_model_input,
    provider_model_input_fields,
)
from app.email_training_data import TrainingSnapshotUnavailable, data_path

catalog = fixtures.catalog


@pytest.fixture
def selected(catalog):
    store = eligible(catalog)
    with store._connect() as db:
        db.execute("update emails set message_updated_at='2026-10-18 00:00:00'")
    return store


def freeze(store, *, identity="selected-first", day=19):
    records = store.list_selected_training_records()
    snapshot = build_selected_training_snapshot(
        records,
        snapshot_id=identity,
        description_version="selected-training-input-v1",
        observed_at=datetime(2026, 10, day, tzinfo=timezone.utc),
        seed=17,
    )
    store.persist_training_snapshot(snapshot)
    return records, snapshot


def original(identity):
    return {
        "messageId": identity.split("message-id:", 1)[-1],
        "from": {"email": "sender@example.test", "name": "Sender"},
        "toRecipients": [{"email": "target@example.test", "name": "Target"}],
        "ccRecipients": [{"email": "copy@example.test", "name": "Copy"}],
        "subject": "Actual provider subject",
        "textBody": "actual provider body",
        "listUnsubscribe": "<https://example.test/unsubscribe>",
        "listUnsubscribePost": "List-Unsubscribe=One-Click",
        "attachments": [
            {
                "filename": "note.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 4,
                "inline": False,
            }
        ],
    }


def source_reader(store, *, batch_size=50, read=None):
    from app.email_selected_training_reader import EmailSelectedTrainingReader

    calls = []

    def read_many(account, identities):
        calls.append((account, tuple(identities)))
        return (
            read(account, identities)
            if read is not None
            else {identity: original(identity) for identity in identities}
        )

    return EmailSelectedTrainingReader(
        store,
        source_reader=SimpleNamespace(read_many=read_many),
        batch_size=batch_size,
    ), calls


def forbid_stored_inputs(store, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "selected source must not read database originals or model text"
        )

    for name in (
        "list_selected_training_records",
        "get_classification",
    ):
        monkeypatch.setattr(store, name, forbidden)


def test_selected_source_metadata_has_only_labels_and_existing_versions(selected):
    rows = selected.list_selected_training_source_metadata()
    labels = selected.list_selected_training_labels()
    assert [
        {
            key: value
            for key, value in row.items()
            if key not in {"classification_updated_at", "message_updated_at"}
        }
        for row in rows
    ] == labels
    assert rows[0]["message_updated_at"] == "2026-10-18 00:00:00"
    assert (
        not {"normalized_text", "model_text", "normalized_model_input"} & rows[0].keys()
    )


def test_exact_latest_frozen_input_is_used_without_body_or_provider_read(
    selected, monkeypatch
):
    expected, snapshot = freeze(selected)
    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected)
    assert (
        reader.list_selected_training_records(sources=["agent_auto_label"]) == expected
    )
    assert calls == []
    reference = selected.latest_training_snapshot_reference(snapshot.snapshot_version)
    assert reference["snapshot_id"] == snapshot.snapshot_id
    assert reference["snapshot_digest"] == snapshot.snapshot_digest


def test_new_record_uses_the_same_actual_source_normalizer_as_online_inference(
    selected, monkeypatch
):
    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected)
    records = reader.list_selected_training_records(sources=["agent_auto_label"])
    assert len(records) == 1 and len(calls) == 1
    identity = records[0]["stable_message_identity"]
    assert records[0]["normalized_model_input"] == canonical_model_input(
        provider_model_input_fields(original(identity))
    )
    assert records[0]["category_key"] == "work"
    assert records[0]["source"] == "agent_auto_label"


@pytest.mark.parametrize(
    "changed", ["label", "message_version", "classification_version"]
)
def test_changed_current_fact_is_not_replaced_by_a_stale_frozen_input(
    selected, monkeypatch, changed
):
    _, snapshot = freeze(selected)
    with selected._connect() as db:
        if changed == "label":
            db.execute(
                "update emails set class_category='legal' where classification_id=1"
            )
        elif changed == "message_version":
            db.execute(
                "update emails set message_updated_at='2026-10-20 00:00:00' where classification_id=1"
            )
        else:
            db.execute(
                "update emails set class_updated_at='2026-10-20 00:00:00' where classification_id=1"
            )
    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected)
    records = reader.list_selected_training_records(sources=["agent_auto_label"])
    assert len(calls) == 1
    assert records[0]["normalized_model_input"] == canonical_model_input(
        provider_model_input_fields(original(records[0]["stable_message_identity"]))
    )
    assert records[0]["category_key"] == ("legal" if changed == "label" else "work")


def test_provider_reads_are_account_scoped_and_bounded(selected, monkeypatch):
    with selected._connect() as db:
        mark_processed_with_plan(db, 3)
    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected, batch_size=1)
    records = reader.list_selected_training_records(sources=["agent_auto_label"])
    assert [row["classification_id"] for row in records] == [1, 3]
    assert len(calls) == 2 and all(len(keys) == 1 for _, keys in calls)


def test_corrupt_latest_file_is_explicit_and_does_not_fall_through_to_provider(
    selected, monkeypatch
):
    _, snapshot = freeze(selected)
    data_path(selected.path, snapshot.snapshot_digest).write_text("{}")
    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected)
    with pytest.raises(TrainingSnapshotUnavailable, match="unavailable|corrupt"):
        reader.list_selected_training_records(sources=["agent_auto_label"])
    assert calls == []


def test_source_unavailable_does_not_reuse_a_database_body(selected, monkeypatch):
    forbid_stored_inputs(selected, monkeypatch)

    def missing(*args):
        raise LookupError("original unavailable")

    reader, calls = source_reader(selected, read=missing)
    with pytest.raises(TrainingSnapshotUnavailable, match="unavailable"):
        reader.list_selected_training_records(sources=["agent_auto_label"])
    assert len(calls) == 1


def test_current_label_change_during_provider_io_returns_no_partial_set(
    selected, monkeypatch
):
    forbid_stored_inputs(selected, monkeypatch)

    def changed(account, identities):
        with selected._connect() as db:
            db.execute(
                "update emails set class_category='legal' where classification_id=1"
            )
        return {identity: original(identity) for identity in identities}

    reader, calls = source_reader(selected, read=changed)
    with pytest.raises(TrainingSnapshotUnavailable, match="changed"):
        reader.list_selected_training_records(sources=["agent_auto_label"])
    assert len(calls) == 1


def test_folder_only_selection_does_not_read_selected_label_metadata(
    selected, monkeypatch
):
    def forbidden():
        raise AssertionError(
            "folder-only selection must not read selected service labels"
        )

    monkeypatch.setattr(selected, "list_selected_training_source_metadata", forbidden)
    reader, calls = source_reader(selected)
    assert reader.list_selected_training_records(sources=["folder_snapshot"]) == []
    assert calls == []


def test_manual_training_freezes_source_input_without_reading_db_body(
    selected, monkeypatch, tmp_path
):
    from app.email_classifier_learning import EmailClassifierLearningService
    from app.email_classifier_retrain import TrainingSubprocessRun
    from app.email_model_registry import EmailModelRegistry

    forbid_stored_inputs(selected, monkeypatch)
    reader, calls = source_reader(selected)

    class Controller:
        def start(self, **kwargs):
            return TrainingSubprocessRun(
                run_id="source-selected-run",
                status="running",
                pid=1,
                started_at=kwargs["now"].isoformat(),
                updated_at=kwargs["now"].isoformat(),
                snapshot_id=kwargs["snapshot_id"],
                snapshot_sha=kwargs["signal"].snapshot_sha,
                description_version=kwargs["signal"].description_version,
                training_selection=kwargs["training_selection"],
            )

    service = EmailClassifierLearningService(
        selected,
        registry=EmailModelRegistry(tmp_path / "models"),
        retrain_state_path=tmp_path / "retrain-state.json",
        controller=Controller(),
        selected_training_reader=reader,
    )
    result = service.request_manual_training(
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
        selection={
            "sources": ["agent_auto_label"],
            "categories": ["work"],
            "model_families": ["embedding-mlp"],
        },
    )
    assert result.decision.reason == "manual_selected" and len(calls) == 1
    frozen = selected.get_training_snapshot(result.training_run.snapshot_id)
    assert len(frozen["observations"]) == 1
    observation = frozen["observations"][0]
    assert observation["normalized_model_input"] == canonical_model_input(
        provider_model_input_fields(original(observation["stable_message_identity"]))
    )
    assert (
        observation["source"] == "agent_auto_label"
        and observation["category_key"] == "work"
    )
