"""One-time preparation of existing selected inputs after the paired migration.

This is not a runtime input reader. The old input serializer is used only to
publish an incomplete current dataset once, before DB input retirement. Normal
training reads the verified external baseline or an exact original source.
"""

from datetime import datetime, timezone
import uuid

from app.email_selected_training_reader import EmailSelectedTrainingReader
from app.email_training_data import TrainingSnapshotUnavailable
from app.email_training_snapshot import (
    SELECTED_TRAINING_SNAPSHOT_VERSION,
    SELECTED_TRAINING_SNAPSHOT_ID_PREFIX,
    build_selected_training_snapshot,
    training_snapshot_id,
)


def _index(observations):
    return {
        (row["account_id"], row["stable_message_identity"], row["source"]): row
        for row in observations
    }


def _covers(metadata, observations):
    return all(
        EmailSelectedTrainingReader._frozen_input(
            row,
            observations.get(
                (row["account_id"], row["stable_message_identity"], row["source"])
            ),
        )
        is not None
        for row in metadata
    )


def refresh_current_selected_baseline(store, *, now=None):
    metadata = store.list_selected_training_source_metadata()
    reference = store.latest_training_snapshot_reference(
        SELECTED_TRAINING_SNAPSHOT_VERSION
    )
    if not metadata:
        return reference
    if reference is not None:
        snapshot = store.get_training_snapshot(reference["snapshot_id"])
        if snapshot is None:
            raise TrainingSnapshotUnavailable(
                "current selected baseline is unavailable"
            )
        if _covers(metadata, _index(snapshot["observations"])):
            return reference
    records = store.list_selected_training_records()
    labels = [
        {
            key: value
            for key, value in row.items()
            if key not in {"classification_updated_at", "message_updated_at"}
        }
        for row in metadata
    ]
    if [
        {key: value for key, value in row.items() if key != "normalized_model_input"}
        for row in records
    ] != labels or store.list_selected_training_source_metadata() != metadata:
        raise TrainingSnapshotUnavailable(
            "selected training inputs changed during migration preparation"
        )
    current = datetime.now(timezone.utc) if now is None else now
    prepared = build_selected_training_snapshot(
        records,
        snapshot_id=training_snapshot_id(
            SELECTED_TRAINING_SNAPSHOT_ID_PREFIX, current, uuid.uuid4().hex
        ),
        description_version="selected-training-input-v1",
        observed_at=current,
        seed=20260905,
    )
    if not _covers(metadata, _index([row.to_dict() for row in prepared.observations])):
        raise TrainingSnapshotUnavailable(
            "current selected input clocks exceed publication time"
        )
    store.persist_training_snapshot(prepared)
    # Do not prune here: the existing training lifecycle retains active pins
    # and removes older data once training is idle.
    return store.latest_training_snapshot_reference(SELECTED_TRAINING_SNAPSHOT_VERSION)
