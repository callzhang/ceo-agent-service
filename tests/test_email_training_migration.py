"""Migration-only current selected dataset preparation, without model execution."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests import test_email_selected_training_reader as fixtures
from app.email_selected_training_reader import EmailSelectedTrainingReader
from app.email_training_data import TrainingSnapshotUnavailable, data_path

catalog = fixtures.catalog
selected = fixtures.selected


def test_refresh_publishes_exact_current_inputs_and_only_metadata_in_db(selected):
    from app.email_training_migration import refresh_current_selected_baseline

    expected = selected.list_selected_training_records()
    with selected._connect() as db:
        before = [tuple(row) for row in db.execute("select * from emails order by id")]
    reference = refresh_current_selected_baseline(
        selected, now=datetime(2026, 10, 20, tzinfo=timezone.utc)
    )

    def forbidden(*args):
        raise AssertionError("prepared baseline must not need provider input")

    actual = EmailSelectedTrainingReader(
        selected, source_reader=SimpleNamespace(read_many=forbidden)
    ).list_selected_training_records(sources=["agent_auto_label", "user_feedback"])
    assert actual == expected
    assert reference == selected.latest_training_snapshot_reference(
        "email-selected-training-snapshot-v1"
    )
    with selected._connect() as db:
        assert [
            tuple(row) for row in db.execute("select * from emails order by id")
        ] == before
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"


def test_complete_current_baseline_is_reused_without_old_input_read_or_new_file(
    selected, monkeypatch
):
    from app.email_training_migration import refresh_current_selected_baseline

    _, snapshot = fixtures.freeze(selected)
    reference = selected.latest_training_snapshot_reference(snapshot.snapshot_version)

    def forbidden(*args, **kwargs):
        raise AssertionError("complete current baseline must be reused")

    monkeypatch.setattr(selected, "list_selected_training_records", forbidden)
    monkeypatch.setattr(selected, "persist_training_snapshot", forbidden)
    assert refresh_current_selected_baseline(selected) == reference


def test_corrupt_current_baseline_is_not_hidden_by_republishing_from_db(
    selected, monkeypatch
):
    from app.email_training_migration import refresh_current_selected_baseline

    _, snapshot = fixtures.freeze(selected)
    data_path(selected.path, snapshot.snapshot_digest).write_text("{}")

    def forbidden(*args, **kwargs):
        raise AssertionError("corruption must remain explicit")

    monkeypatch.setattr(selected, "list_selected_training_records", forbidden)
    with pytest.raises(TrainingSnapshotUnavailable, match="unavailable|corrupt"):
        refresh_current_selected_baseline(selected)


def test_label_change_while_capturing_input_aborts_without_publishing(
    selected, monkeypatch
):
    from app.email_training_migration import refresh_current_selected_baseline

    original = selected.list_selected_training_records

    def changed():
        rows = original()
        with selected._connect() as db:
            db.execute(
                "update emails set class_category='legal' where classification_id=1"
            )
        return rows

    monkeypatch.setattr(selected, "list_selected_training_records", changed)
    with pytest.raises(TrainingSnapshotUnavailable, match="changed"):
        refresh_current_selected_baseline(selected)
    assert (
        selected.latest_training_snapshot_reference(
            "email-selected-training-snapshot-v1"
        )
        is None
    )


