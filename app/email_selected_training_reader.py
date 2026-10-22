"""Current training inputs from validated external baselines or exact sources."""

from datetime import datetime, timezone
import imaplib

from app.email_training_data import TrainingSnapshotUnavailable
from app.email_training_snapshot import (
    MODEL_INPUT_SCHEMA_VERSION,
    SELECTED_TRAINING_SNAPSHOT_VERSION,
    canonical_model_input,
    provider_model_input_fields,
)

_VERSION_FIELDS = {"classification_updated_at", "message_updated_at"}


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    # Existing SQLite CURRENT_TIMESTAMP values are UTC without an offset.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def build_selected_training_reader(store, *, source_reader=None):
    if source_reader is None:
        from app.config import effective_env_values
        from app.email_source_reader import build_email_source_reader

        source_reader = build_email_source_reader(
            store, environment_factory=effective_env_values
        )
    return EmailSelectedTrainingReader(store, source_reader=source_reader)


class EmailSelectedTrainingReader:
    def __init__(self, store, *, source_reader, batch_size: int = 50):
        if batch_size <= 0:
            raise ValueError("training source batch size must be positive")
        self.store = store
        self.source_reader = source_reader
        self.batch_size = batch_size

    def _metadata(self, sources):
        return [
            row
            for row in self.store.list_selected_training_source_metadata()
            if row["source"] in sources
        ]

    def _reference(self):
        return self.store.latest_training_snapshot_reference(
            SELECTED_TRAINING_SNAPSHOT_VERSION
        )

    @staticmethod
    def _frozen_input(row, observation):
        if observation is None or any(
            row[name] != observation[name]
            for name in ("category_key", "important", "provider_thread_id")
        ):
            return None
        if observation["input_schema_version"] != MODEL_INPUT_SCHEMA_VERSION:
            return None
        observed = _timestamp(observation["observed_at"])
        if any(_timestamp(row[name]) > observed for name in _VERSION_FIELDS):
            return None
        return observation["normalized_model_input"]

    def list_selected_training_records(self, *, sources):
        selected_sources = set(sources) & {"agent_auto_label", "user_feedback"}
        if not selected_sources:
            return []
        metadata = self._metadata(selected_sources)
        if not metadata:
            return []
        reference = self._reference()
        observations = {}
        if reference is not None:
            # This getter verifies the complete file digest/schema/identity
            # against its exact current persisted reference. Never hide failure
            # by selecting another historical file or reading a DB body.
            snapshot = self.store.get_training_snapshot(reference["snapshot_id"])
            if snapshot is None:
                raise TrainingSnapshotUnavailable(
                    "selected training baseline is unavailable"
                )
            observations = {
                (row["account_id"], row["stable_message_identity"], row["source"]): row
                for row in snapshot["observations"]
            }
        inputs = {}
        accounts = {}
        try:
            for row in metadata:
                key = (row["account_id"], row["stable_message_identity"])
                text = self._frozen_input(row, observations.get((*key, row["source"])))
                if text is None:
                    accounts.setdefault(key[0], []).append(key[1])
                else:
                    inputs[key] = text
            for account, identities in accounts.items():
                for start in range(0, len(identities), self.batch_size):
                    messages = self.source_reader.read_many(
                        account, identities[start : start + self.batch_size]
                    )
                    for identity, message in messages.items():
                        inputs[(account, identity)] = canonical_model_input(
                            provider_model_input_fields(message)
                        )
        except (LookupError, OSError, ValueError, imaplib.IMAP4.error) as exc:
            raise TrainingSnapshotUnavailable(
                "selected training input source is unavailable"
            ) from exc
        if (
            self._metadata(selected_sources) != metadata
            or self._reference() != reference
        ):
            raise TrainingSnapshotUnavailable(
                "selected training sources changed during read"
            )
        return [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in _VERSION_FIELDS
                },
                "normalized_model_input": inputs[
                    (row["account_id"], row["stable_message_identity"])
                ],
            }
            for row in metadata
        ]
