from tests.support.email_listing_oracle import StoredEmailListingOracle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gc
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import sqlite3
import sys
from threading import Barrier

import pytest

import app.email_store as email_store_module
from tests.email_schema_fixtures import (
    restore_action_plan_snapshot_column,
    restore_schema48_action_plan_snapshots,  # noqa: F401 - historical fixture import retained
)
from app.email_training_snapshot import (
    MODEL_INPUT_SCHEMA_VERSION,
    build_folder_training_snapshot,
    build_selected_training_snapshot,
)
from app.email_experiment_snapshot import deterministic_payload_digest
from app.email_classifier_contracts import (
    EmailAction,
    EmailActionAuthorization,  # noqa: F401 - historical fixture import retained
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    _action_plan_identity,
    build_email_action_plan,
)
from app.email_store import (
    DIRECT_EMAIL_ACTION_SQL,
    EMAIL_DIRECT_ACTION_SQL,
    EMAIL_PLAN_SQL,
    EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL,
    EMAIL_UNSUBSCRIBE_RECEIPT_SQL,
    EMAIL_UNSUBSCRIBE_STEP_SQL,
    StoredEmailLocator,
    EmailActionAttemptConflict,
    EmailActionPlanConflict,
    EmailClassificationConflict,
    EmailClassificationIdentityCollision,
    EmailPersistenceCorruption,
    EmailTrainingSnapshotConflict,
    EmailUnsubscribeClaimConflict,
    EmailUnsubscribeReceiptConflict,
    EmailStore,
    email_unsubscribe_effect_digest,
)
from app.action_storage import _encoded_native_sqlite_value
from app.email_important import ImportantSignals
from app.email_provider_folders import FolderRole, ProviderFolder
from app.email_task_adapter import email_action_identity
from app.email_pipeline import apply_human_confirmation
from app.email_unsubscribe import normalize_unsubscribe_result_text
from app.store import AgentRole, AutoReplyStore, _close_idle_connections
from app.action_storage import (
    email_action_projection,
    insert_action,
    update_action,
    update_email_action,
)
from app.email_canonical_storage import (
    email_classification_projection, email_message_projection,
)
from app.email_canonical_storage import (
    delete_email_classification,
)
from app.configs_definitions import config_projection
from app.configs_storage import delete_config, insert_config, update_config


def _set_training_snapshot_raw(db, snapshot_id: str, field: str, value) -> None:
    if field in {"snapshot_digest", "frozen"}:
        column = "snapshot_snapshot_digest" if field == "snapshot_digest" else "snapshot_frozen"
        db.execute(
            f"update configs set {column}=? where record_type='email_training_snapshots' and public_key=?",
            (value, snapshot_id),
        )
    else:
        db.execute(
            "update configs set payload_json=json_set(payload_json, ?, json(?)) "
            "where record_type='email_training_snapshots' and public_key=?",
            (f"$.{field}", json.dumps(
                _encoded_native_sqlite_value(value), ensure_ascii=False,
                allow_nan=False, separators=(",", ":"),
            ), snapshot_id),
        )


def _frozen_training_observation(**overrides):
    value = {
        "account_id": "account-a",
        "stable_message_identity": "account-a:message-id:<one@example.test>",
        "provider_folder_id": "folder-work",
        "provider_folder_name": "Work",
        "folder_role": "category",
        "bound_category_key": "work",
        "folder_binding_status": "active",
        "processed_by_email_service": True,
        "important_signals": ImportantSignals(("STARRED",), True),
        "sender": {"name": "Sender", "email": "sender@example.test"},
        "to_recipients": [{"name": "Derek", "email": "derek@example.test"}],
        "cc_recipients": [],
        "subject": "One",
        "body": "Body including quoted reply\n> prior body",
        "headers": {"message-id": "<one@example.test>"},
        "attachments": [],
        "provider_thread_id": "thread-one",
        "explicit_matter_group": None,
        "source": "natural",
        "received_at": "2026-09-07T12:00:00+00:00",
    }
    value.update(overrides)
    return value


def _frozen_training_snapshot(snapshot_id: str = "snapshot-store-1"):
    return build_folder_training_snapshot(
        [_frozen_training_observation()],
        snapshot_id=snapshot_id,
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc),
        seed=17,
    )


def _legacy_unsigned_record_digest(row) -> str:
    return deterministic_payload_digest(
        {
            "account_id": row.account_id,
            "stable_message_identity": row.stable_message_identity,
            "provider_folder_id": row.provider_folder_id,
            "provider_folder_name": row.provider_folder_name,
            "category_key": row.category_key,
            "important": row.important,
            "normalized_model_input_hash": row.normalized_model_input_hash,
            "input_schema_version": row.input_schema_version,
            "provider_thread_id": row.provider_thread_id,
            "normalized_body_digest": row.normalized_body_digest,
            "sender_template_signature": row.sender_template_signature,
            "explicit_matter_group": row.explicit_matter_group,
            "group_key": row.group_key,
            "source": row.source,
            "split": row.split,
            "selected_for_training": row.selected_for_training,
        }
    )


def _unsigned_time_legacy_manifest(snapshot, *, include_counts: bool):
    legacy_manifest = snapshot.manifest
    legacy_manifest.pop("observed_at")
    legacy_manifest.pop("overall_sha256")
    legacy_record_digests = [
        _legacy_unsigned_record_digest(row) for row in snapshot.observations
    ]
    legacy_manifest["ordered_record_digests"] = legacy_record_digests
    if not include_counts:
        for field in (
            "observation_count",
            "selected_group_count",
            "selected_category_counts",
            "conflicted_group_count",
            "conflicted_groups",
        ):
            legacy_manifest.pop(field)
    legacy_digest = deterministic_payload_digest(legacy_manifest)
    legacy_manifest["overall_sha256"] = legacy_digest
    return legacy_manifest, legacy_digest, legacy_record_digests


def _rewind_email_schema(db: sqlite3.Connection, *, version: int) -> None:
    """Remove migration records and objects introduced after a fixture version."""

    restore_action_plan_snapshot_column(db)
    db.execute("drop index if exists idx_email_training_observations_provider_truth")
    db.execute("drop table if exists email_classifier_runtime_samples")
    db.execute("delete from email_schema_migrations where version > ?", (version,))
    db.execute(
        "insert or ignore into email_schema_migrations(version, applied_at) "
        "values (?, '2026-09-08T00:00:00+00:00')",
        (version,),
    )


def test_snapshot_readback_requires_external_data_for_db_digest(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "training-snapshot-v24-legacy-manifest.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)
    legacy_manifest, legacy_digest, _ = _unsigned_time_legacy_manifest(
        snapshot, include_counts=True
    )
    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger trg_configs_snapshot_update")
        _set_training_snapshot_raw(
            db, snapshot.snapshot_id, "manifest_json",
            json.dumps(legacy_manifest, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")),
        )
        _set_training_snapshot_raw(db, snapshot.snapshot_id, "snapshot_digest", legacy_digest)

    with pytest.raises(RuntimeError, match="unavailable"):
        store.get_training_snapshot(snapshot.snapshot_id)


def test_store_round_trips_frozen_training_snapshot(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot.sqlite3")
    snapshot = _frozen_training_snapshot()

    stored = store.persist_training_snapshot(snapshot)
    loaded = store.get_training_snapshot(snapshot.snapshot_id)

    assert stored["snapshot_digest"] == snapshot.snapshot_digest
    assert loaded is not None
    assert loaded["manifest"] == snapshot.manifest
    assert loaded["observations"] == [row.to_dict() for row in snapshot.observations]


def test_store_persists_agent_label_selected_training_snapshot(tmp_path: Path):
    store = EmailStore(tmp_path / "agent-selected-training-snapshot.sqlite3")
    snapshot = build_selected_training_snapshot(
        [
            {
                "source": "agent_auto_label",
                "account_id": "account-a",
                "stable_message_identity": f"account-a:agent-label:{index}",
                "provider_thread_id": f"thread-agent-{index}",
                "category_key": "work",
                "normalized_model_input": json.dumps({"body": f"Agent labelled work mail {index}"}),
            }
            for index in range(8)
        ],
        snapshot_id="agent-selected-snapshot",
        description_version="selected-training-input-v1",
        observed_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        seed=20260905,
    )

    stored = store.persist_training_snapshot(snapshot)

    assert stored["observations"][0]["source"] == "agent_auto_label"


def test_alternating_snapshot_types_retain_each_publication_baseline(tmp_path):
    from app.email_training_data import TrainingSnapshotUnavailable, data_path

    store = EmailStore(tmp_path / "alternating-snapshots.sqlite3")
    first = _frozen_training_snapshot("folder-first")
    store.persist_training_snapshot(first)
    selected = build_selected_training_snapshot(
        [
            {
                "source": "agent_auto_label",
                "account_id": "account-a",
                "stable_message_identity": f"account-a:agent-label:{index}",
                "provider_thread_id": f"thread-agent-{index}",
                "category_key": "work",
                "normalized_model_input": json.dumps({"body": f"Mail {index}"}),
            }
            for index in range(8)
        ],
        snapshot_id="selected",
        description_version="selected-training-input-v1",
        observed_at=datetime(2026, 9, 7, 19, tzinfo=timezone.utc),
        seed=17,
    )
    store.persist_training_snapshot(selected)
    store.pin_training_snapshot("folder-run", first.snapshot_id)
    store.prune_training_snapshot_data(training_active=False)
    assert data_path(store.path, first.snapshot_digest).is_file()
    second = build_folder_training_snapshot(
        [_frozen_training_observation(body="Changed body, unchanged labels")],
        snapshot_id="folder-second",
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 20, tzinfo=timezone.utc),
        seed=17,
    )
    stored = store.persist_training_snapshot(second)
    assert stored["folder_label_watermark"] == 1
    assert stored["important_label_watermark"] == 1
    store.prune_training_snapshot_data(training_active=False)
    assert store.get_training_snapshot(selected.snapshot_id) is not None
    assert not data_path(store.path, first.snapshot_digest).exists()
    with pytest.raises(TrainingSnapshotUnavailable, match="historical"):
        store.get_training_snapshot(first.snapshot_id)


def test_identical_training_snapshot_persistence_is_idempotent(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot-idempotent.sqlite3")
    snapshot = _frozen_training_snapshot()

    first = store.persist_training_snapshot(snapshot)
    second = store.persist_training_snapshot(snapshot)

    assert second == first
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("select count(*) from configs where record_type='email_training_snapshots'").fetchone()[0]
            == 1
        )
        assert db.execute("select 1 from sqlite_schema where name='email_training_snapshot_observations'").fetchone() is None


def test_same_snapshot_id_rejects_changed_frozen_content(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot-conflict.sqlite3")
    first = _frozen_training_snapshot()
    changed = build_folder_training_snapshot(
        [
            _frozen_training_observation(
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
                important_signals=ImportantSignals((), False),
            )
        ],
        snapshot_id=first.snapshot_id,
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc),
        seed=17,
    )
    store.persist_training_snapshot(first)

    with pytest.raises(EmailTrainingSnapshotConflict, match="already exists"):
        store.persist_training_snapshot(changed)


def test_store_rejects_observation_metadata_tampering(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot-tamper.sqlite3")
    snapshot = _frozen_training_snapshot()
    forged_row = replace(snapshot.observations[0], category_key="legal")
    forged = replace(snapshot, observations=(forged_row,))

    with pytest.raises(ValueError, match="observation digest"):
        store.persist_training_snapshot(forged)


def test_correcting_a_label_back_does_not_lower_the_watermark(tmp_path: Path):
    """A watermark counts changes, and undoing a change is another change."""

    store = EmailStore(tmp_path / "watermark-history.sqlite3")
    store.persist_training_snapshot(_frozen_training_snapshot("snapshot-one"))
    moved = build_folder_training_snapshot(
        [
            _frozen_training_observation(
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
            )
        ],
        snapshot_id="snapshot-two",
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc),
        seed=17,
    )
    store.persist_training_snapshot(moved)
    after_move = store.latest_training_snapshot_state()["folder_label_watermark"]

    store.persist_training_snapshot(
        build_folder_training_snapshot(
            [_frozen_training_observation()],
            snapshot_id="snapshot-three",
            description_version="description-v3",
            observed_at=datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc),
            seed=17,
        )
    )
    after_undo = store.latest_training_snapshot_state()["folder_label_watermark"]

    assert after_undo > after_move


def test_later_snapshot_does_not_mutate_earlier_observation(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot-history.sqlite3")
    first = _frozen_training_snapshot("snapshot-before-move")
    moved = build_folder_training_snapshot(
        [
            _frozen_training_observation(
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
                important_signals=ImportantSignals((), False),
            )
        ],
        snapshot_id="snapshot-after-move",
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc),
        seed=17,
    )

    store.persist_training_snapshot(first)
    store.persist_training_snapshot(moved)

    with pytest.raises(RuntimeError, match="unavailable"):
        store.get_training_snapshot("snapshot-before-move")
    assert (
        store.get_training_snapshot("snapshot-after-move")["observations"][0][
            "category_key"
        ]
        == "legal"
    )
    state = store.latest_training_snapshot_state()
    assert state is not None
    assert state["snapshot_id"] == "snapshot-after-move"
    assert state["folder_label_watermark"] == 2
    # Moving mail between two categories the owner has to act on does not
    # change whether it asks anything of him.
    assert state["important_label_watermark"] == 1
    assert state["minimum_ready"] is False


@pytest.mark.parametrize(
    "field,value",
    (
        ("seed", 99),
    ),
)
def test_database_rejects_frozen_snapshot_mutation(tmp_path: Path, field: str, value):
    store = EmailStore(tmp_path / "training-snapshot-immutable.sqlite3")
    store.persist_training_snapshot(_frozen_training_snapshot())

    with (
        sqlite3.connect(store.path) as db,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        _set_training_snapshot_raw(db, "snapshot-store-1", field, value)


def test_retired_observations_and_frozen_snapshot_immutability(tmp_path: Path):
    store = EmailStore(tmp_path / "training-snapshot-append.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)

    with sqlite3.connect(store.path) as db:
        assert db.execute("select 1 from sqlite_schema where name='email_training_snapshot_observations'").fetchone() is None
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            _set_training_snapshot_raw(db, snapshot.snapshot_id, "manifest_json", "{}")


def test_readback_rejects_persisted_observation_not_covered_by_manifest(
    tmp_path: Path,
):
    from app.email_training_data import TrainingSnapshotUnavailable, data_path

    store = EmailStore(tmp_path / "training-snapshot-corrupt.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)
    path = data_path(store.path, snapshot.snapshot_digest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    copied = dict(payload["observations"][0])
    copied["stable_message_identity"] = "account-a:message-id:<corrupt@example.test>"
    payload["observations"].append(copied)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TrainingSnapshotUnavailable, match="training snapshot.*corrupt"):
        store.get_training_snapshot(snapshot.snapshot_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_version", "tampered-version"),
        ("description_version", "tampered-description"),
        ("input_schema_version", "tampered-schema"),
        ("seed", 99),
        ("frozen", 0),
    ],
)
def test_external_snapshot_readback_rejects_parent_identity_tampering(tmp_path, field, value):
    store = EmailStore(tmp_path / "snapshot-identity.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)
    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger trg_configs_snapshot_update")
        _set_training_snapshot_raw(db, snapshot.snapshot_id, field, value)
    with pytest.raises(EmailPersistenceCorruption, match="training snapshot"):
        store.get_training_snapshot(snapshot.snapshot_id)


@pytest.mark.parametrize("folders", [[], [None], [" "], [7]])
def test_lightweight_initialization_rejects_invalid_scan_folder_names(tmp_path, folders):
    path = tmp_path / "invalid-account-folders.sqlite3"
    EmailStore(path)
    _insert_account_with_scan_folders_json(path, json.dumps(folders))
    with pytest.raises(EmailPersistenceCorruption, match="scan_folders_json"):
        EmailStore(path, validate_rows=False)


def test_snapshot_publication_rejects_corrupt_previous_identity(tmp_path):
    store = EmailStore(tmp_path / "snapshot-publication-identity.sqlite3")
    first = _frozen_training_snapshot("first")
    store.persist_training_snapshot(first)
    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger trg_configs_snapshot_update")
        _set_training_snapshot_raw(db, first.snapshot_id, "seed", 99)
    second = build_folder_training_snapshot(
        [_frozen_training_observation(body="Changed body, unchanged labels")],
        snapshot_id="second",
        description_version="description-v3",
        observed_at=datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc),
        seed=17,
    )
    with pytest.raises(EmailPersistenceCorruption, match="training snapshot"):
        store.persist_training_snapshot(second)
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from configs where record_type='email_training_snapshots'").fetchone()[0] == 1


def test_readback_rejects_coherent_timestamp_tampering_with_stale_digests(
    tmp_path: Path,
):
    from app.email_training_data import TrainingSnapshotUnavailable, data_path

    store = EmailStore(tmp_path / "training-snapshot-time-corrupt.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)
    tampered_time = "2026-09-07T19:00:00+00:00"
    path = data_path(store.path, snapshot.snapshot_digest)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observed_at"] = tampered_time
    for observation in payload["observations"]:
        observation["observed_at"] = tampered_time
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger trg_configs_snapshot_update")
        _set_training_snapshot_raw(db, snapshot.snapshot_id, "observed_at", tampered_time)

    with pytest.raises(
        TrainingSnapshotUnavailable,
        match="training snapshot data is unavailable or corrupt",
    ):
        store.get_training_snapshot(snapshot.snapshot_id)


def _pending_category_classification(
    category: str,
    *,
    classification_id: int,
) -> EmailClassification:
    message_id = f"<{category}-{classification_id}@example.com>"
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "stable_message_identity": (f"dingtalk-account:message-id:{message_id}"),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": "INBOX",
                "uidvalidity": 42,
                "uid": classification_id,
                "rfc_message_id": message_id,
            },
            "category": category,
            "confidence": 0.79,
            "margin": 0.31,
            "probabilities": {category: 0.79, "legal": 0.21},
            "model_id": "email/logistic/model-1",
            "config_version": "email-v1",
            "status": EmailClassificationStatus.PENDING_FEEDBACK,
            "classification_source": "model",
            "action_plan": None,
        }
    )


def test_real_store_human_confirmation_persists_plain_string_category(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "plain-category.sqlite3")
    classification = _pending_category_classification(
        "work",
        classification_id=101,
    )
    persisted = store.persist_scan_result(
        classification,
        model_text="__subject__plain category integration",
    )

    application = apply_human_confirmation(
        store,
        persisted["id"],
        "work",
        feedback_request_id="feedback-plain-category",
        expected_current_action_plan_id=None,
        now=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert application is not None
    assert type(application.confirmed["category"]) is str
    assert type(application.confirmed["action_plan"]["category"]) is str
    with sqlite3.connect(store.path) as db:
        stored_category = db.execute(
            f"select category from ({EMAIL_PLAN_SQL}) where action_plan_id=?",
            (application.resulting_action_plan_id,),
        ).fetchone()[0]
    assert type(stored_category) is str
    assert stored_category == "work"


def test_real_store_persists_and_reopens_custom_category(tmp_path: Path):
    database = tmp_path / "custom-category.sqlite3"
    store = EmailStore(database)
    classification = _pending_category_classification(
        "board_governance",
        classification_id=102,
    )

    persisted = store.persist_scan_result(
        classification,
        model_text="__subject__board governance",
    )
    reopened = EmailStore(database)
    restored = reopened.get_classification(persisted["id"])

    assert restored is not None
    assert type(restored["category"]) is str
    assert restored["category"] == "board_governance"
    assert set(restored["probabilities"]) == {"board_governance", "legal"}


def _classification(
    *,
    status: EmailClassificationStatus,
    message_id: str = "msg-1",
    confidence: float = 0.93,
    model_id: str = "email/logistic/model-1",
    config_version: str = "email-v1",
    category: EmailCategory = EmailCategory.WORK,
    actions: tuple[EmailAction, ...] = (EmailAction.LABEL,),
    action_parameters: dict[EmailAction, dict[str, object]] | None = None,
    action_authorizations: tuple[dict[str, object], ...] | None = None,
    classification_id: int | None = None,
    stable_message_identity: str | None = None,
    folder: str = "INBOX",
    uidvalidity: int = 42,
    uid: int | None = None,
    rfc_message_id: str | None = None,
    thread_id: str | None = None,
) -> EmailClassification:
    generated_id = (
        int.from_bytes(sha256(message_id.encode("utf-8")).digest()[:8], "big")
        & ((1 << 63) - 1)
        or 1
    )
    classification_id = classification_id or generated_id
    uid = uid or classification_id
    if rfc_message_id is None and stable_message_identity is None:
        rfc_message_id = f"<{message_id}@example.com>"
    stable_message_identity = stable_message_identity or (
        f"dingtalk-account:message-id:{rfc_message_id}"
        if rfc_message_id is not None
        else f"dingtalk-account:imap:{folder}:{uidvalidity}:{uid}"
    )
    if action_parameters is None:
        action_parameters = {
            EmailAction.LABEL: {"labels": [category.value]},
        }
    action_plan = None
    if status is EmailClassificationStatus.PROCESSED:
        created_at = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
        action_plan = build_email_action_plan(
            classification_id=classification_id,
            account_id="dingtalk-account",
            category=category,
            classification_source="model",
            confidence=confidence,
            model_id=model_id,
            config_version=config_version,
            actions=actions,
            action_parameters=action_parameters,
            created_at=created_at,
            action_authorizations=action_authorizations,
        )
    return EmailClassification.model_validate(
        {
            "classification_id": classification_id,
            "stable_message_identity": (stable_message_identity),
            "provider_locator": {
                "account_id": "dingtalk-account",
                "folder": folder,
                "uidvalidity": uidvalidity,
                "uid": uid,
                "rfc_message_id": rfc_message_id,
                "thread_id": thread_id,
            },
            "category": category,
            "confidence": confidence,
            "margin": 0.41,
            "probabilities": {"work": 0.93, "legal": 0.52},
            "model_id": model_id,
            "config_version": config_version,
            "status": status,
            "classification_source": "model",
            "action_plan": action_plan,
        }
    )


def _versioned_plan(
    base: EmailActionPlan,
    *,
    version: int,
    category: EmailCategory,
    actions: tuple[EmailAction, ...],
    action_parameters: dict[EmailAction, dict[str, object]],
) -> EmailActionPlan:
    created_at = base.created_at + timedelta(minutes=version)
    identity = _action_plan_identity(
        action_plan_version=version,
        classification_id=base.classification_id,
        account_id=base.account_id,
        category=category,
        classification_source="user",
        confidence=base.confidence,
        model_id=base.model_id,
        config_version=f"email-v{version}",
        actions=actions,
        action_parameters=action_parameters,
        created_at=created_at,
    )
    return EmailActionPlan.model_validate(
        {
            "action_plan_id": identity,
            "action_plan_version": version,
            "classification_id": base.classification_id,
            "account_id": base.account_id,
            "category": category,
            "classification_source": "user",
            "confidence": base.confidence,
            "model_id": base.model_id,
            "config_version": f"email-v{version}",
            "actions": actions,
            "action_parameters": action_parameters,
            "created_at": created_at,
        }
    )


def _persist_scan(
    store: EmailStore,
    classification: EmailClassification,
    *,
    normalized_text: str = "__subject__need a decision",
    cursor_uidvalidity: int | None = None,
    cursor_last_seen_uid: int | None = None,
    expected_cursor_uidvalidity: int | None = None,
) -> dict[str, object]:
    locator = classification.provider_locator
    cursor_expectation = (
        {"expected_cursor_uidvalidity": expected_cursor_uidvalidity}
        if expected_cursor_uidvalidity is not None
        else {}
    )
    return store.persist_scan_result(
        classification,
        sender="sender@example.com",
        recipients=("recipient@example.com",),
        subject="Need a decision",
        normalized_text=normalized_text,
        preview="Please review",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="brief.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                inline=False,
            ),
        ),
        received_at="2026-08-29T15:59:00+00:00",
        model_text="__subject__need a decision",
        cursor_uidvalidity=cursor_uidvalidity or locator.uidvalidity,
        cursor_last_seen_uid=cursor_last_seen_uid or locator.uid,
        cursor_last_success_at="2026-08-29T16:00:00+00:00",
        **cursor_expectation,
    )


def test_provider_observation_preserves_star_and_flag_signals(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "provider-signals.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    persisted = _persist_scan(store, classification)

    store.record_current_provider_observations(
        [
            {
                "account_id": "dingtalk-account",
                "stable_message_identity": persisted["stable_message_identity"],
                "provider_folder_id": "folder-work",
                "provider_folder_name": "Work",
                "folder_role": FolderRole.CATEGORY,
                "bound_category_key": "work",
                "folder_binding_status": "active",
                "important_signals": ImportantSignals((r"\Flagged", "$Important"), True),
            }
        ],
        unavailable_folders=(),
        observed_at="2026-09-15T12:00:00+00:00",
    )

    state = store.get_provider_classification_state(int(persisted["id"]))

    assert state["important_signals"] == [r"\Flagged", "$Important"]
    assert state["starred"] is True
    assert state["important_flag"] is True


def test_classification_body_display_removes_html_style_payload(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "html-display.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    _persist_scan(
        store,
        classification,
        normalized_text=(
            "From: sender@example.com\n"
            "Subject: HTML newsletter\n"
            "Content-Type: text/html; charset=utf-8\n\n"
            "<html><head><style>@font-face { src: url(font.woff2); }</style>"
            "</head><body><p>Hello <strong>world</strong>.</p>"
            "<p>Keep this original message.</p></body></html>"
        ),
    )

    rows, total = StoredEmailListingOracle(store).list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=20,
        offset=0,
    )
    detail = store.get_classification(classification.classification_id)

    assert total == 1
    assert rows[0]["message_text"] == "Hello world. Keep this original message."
    assert detail is not None
    assert "message_text" not in detail  # Originals are read by the Console source reader.


def test_classification_body_display_removes_standalone_css_prefix(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "css-prefix-display.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    _persist_scan(
        store,
        classification,
        normalized_text=(
            "/* stylesheet emitted by an earlier HTML text extraction */\n"
            "@media screen { @font-face { src: url(font.woff2); } }\n"
            "Visible original email text.\n\nSecond visible paragraph."
        ),
    )

    rows, total = StoredEmailListingOracle(store).list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        limit=20,
        offset=0,
    )

    assert total == 1
    assert rows[0]["message_text"] == "Visible original email text.\n\nSecond visible paragraph."


def test_persist_scan_result_stores_thread_reference_metadata(tmp_path: Path):
    database = tmp_path / "thread-metadata.sqlite3"
    store = EmailStore(database)
    classification = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="thread-metadata",
        thread_id="thread-1",
    )

    store.persist_scan_result(
        classification,
        sender="sender@example.com",
        subject="Re: project",
        normalized_text="__subject__re project",
        preview="Project update",
        model_text="__subject__re project",
        in_reply_to="<parent@example.com>",
        references=("<thread@example.com>", "<parent@example.com>"),
        cursor_uidvalidity=42,
        cursor_last_seen_uid=classification.provider_locator.uid,
        cursor_last_success_at="2026-08-31T20:00:00+00:00",
    )

    row = _fetchall(
        database, f"select in_reply_to, references_json from ({email_message_projection()})"
    )[0]
    assert row["in_reply_to"] == "<parent@example.com>"
    assert json.loads(row["references_json"]) == [
        "<thread@example.com>",
        "<parent@example.com>",
    ]


def _confirm(
    store: EmailStore,
    row_id: int,
    category: EmailCategory,
    *,
    request_id: str | None = None,
) -> dict[str, object] | None:
    return store.confirm_classification(
        row_id,
        category,
        feedback_request_id=(request_id or f"test-feedback-{row_id}-{category.value}"),
        expected_current_action_plan_id=None,
    )


def _fetchall(path: Path, sql: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(sql, parameters).fetchall()


_UNSUBSCRIBE_OWNER_A = {
    "owner_id": "email-worker",
    "generation": 21,
    "lease_token": "unsubscribe-lease-a",
}
_UNSUBSCRIBE_OWNER_B = {
    "owner_id": "email-worker",
    "generation": 22,
    "lease_token": "unsubscribe-lease-b",
}


def _unsubscribe_authorization(
    store: EmailStore, *, entry_reference: str = "unsubscribe-entry:" + "b" * 64
) -> dict[str, object]:
    store.create_account(
        {
            "account_id": "dingtalk-account",
            "display_name": "DingTalk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://smtp-test",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(EmailAction.UNSUBSCRIBE,),
        action_parameters={},
        thread_id="thread-unsubscribe-41",
    )
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    plan = classification.action_plan
    action_identity = email_action_identity(
        account_id=classification.provider_locator.account_id,
        stable_message_identity=classification.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    authorization = {
        "action_identity": action_identity,
        "action_plan_id": plan.action_plan_id,
        "action_plan_version": plan.action_plan_version,
        "classification_id": classification.classification_id,
        "account_id": classification.provider_locator.account_id,
        "stable_message_identity": classification.stable_message_identity,
        "thread_identity": "thread-unsubscribe-41",
        "entry_reference": entry_reference,
        "operations": [
            {
                "operation_reference": "step-1",
                "kind": "open_entry",
                "target_reference": "entry",
            },
        ],
    }
    authorization["effect_digest"] = email_unsubscribe_effect_digest(**authorization)
    return authorization


def _persist_unsubscribe_result_fixture(
    database: Path,
    *,
    result_text: str,
    observation_digest: str | None = None,
    result_text_digest: str | None = None,
    result_text_truncated: bool | None = None,
) -> tuple[EmailStore, dict[str, object], dict[str, object]]:
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    result_metadata: dict[str, object] = {}
    if observation_digest is not None:
        result_metadata["observation_digest"] = observation_digest
    if result_text_digest is not None:
        result_metadata["result_text_digest"] = result_text_digest
    if result_text_truncated is not None:
        result_metadata["result_text_truncated"] = result_text_truncated
    receipt = store.persist_email_unsubscribe_terminal(
        **authorization,
        outcome="done",
        receipt_id="unsubscribe-receipt:result-text",
        evidence="terminal-page",
        result_text=result_text,
        **result_metadata,
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:result-text",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )
    return store, authorization, receipt


def test_backfill_fills_only_an_empty_recipient_list(tmp_path: Path):
    store = EmailStore(tmp_path / "recipients-backfill.sqlite3")
    persisted = _persist_scan(store, _classification(status=EmailClassificationStatus.PENDING_FEEDBACK))
    account, identity = "dingtalk-account", persisted["stable_message_identity"]
    with store._connect() as db:
        db.execute("update emails set message_recipients_json='[]'")

    assert store.backfill_email_message_recipients(account, identity, ["a@example.com", " b@example.com "])
    assert store.get_classification(int(persisted["id"]))["recipients"] == ["a@example.com", "b@example.com"]
    # Saved recipients are never replaced, and an empty answer writes nothing.
    assert not store.backfill_email_message_recipients(account, identity, ["other@example.com"])
    assert not store.backfill_email_message_recipients(account, identity, [])
    assert store.get_classification(int(persisted["id"]))["recipients"] == ["a@example.com", "b@example.com"]


def test_backfill_fills_a_missing_entry_url_only_when_it_hashes_to_the_receipt(
    tmp_path: Path,
):
    store, authorization, _receipt = _persist_unsubscribe_result_fixture(
        tmp_path / "backfill.sqlite3", result_text="退订成功"
    )
    action_identity = str(authorization["action_identity"])
    with store._connect() as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (action_identity,),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row["rowid"], {"entry_url": ""})
    wrong = "https://elsewhere.example.test/unsubscribe?token=other"

    with pytest.raises(ValueError, match="entry reference"):
        store.backfill_email_unsubscribe_entry_url(action_identity, wrong)
    assert store.backfill_email_unsubscribe_entry_url("no-such-action", wrong) is False
    with store._connect() as db:
        assert db.execute(
            f"select entry_url from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
            (action_identity,),
        ).fetchone()[0] == ""


def test_list_unsubscribe_classifications_projects_durable_claims(tmp_path: Path):
    store = EmailStore(tmp_path / "unsubscribe-list.sqlite3")
    authorization = _unsubscribe_authorization(store)

    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True

    rows, total = StoredEmailListingOracle(store).list_unsubscribe_classifications(limit=20, offset=0)

    assert total == 1
    assert [row["id"] for row in rows] == [authorization["classification_id"]]
    assert rows[0]["message_text"]
    with pytest.raises(ValueError, match="pagination"):
        StoredEmailListingOracle(store).list_unsubscribe_classifications(limit=0, offset=0)


def _count_connections(store: EmailStore, monkeypatch) -> list[int]:
    opened = [0]
    original = store._open_connection

    def counted(*args, **kwargs) -> sqlite3.Connection:
        opened[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_open_connection", counted)
    return opened


def test_page_lookups_open_one_connection_and_match_the_per_row_answers(
    tmp_path: Path, monkeypatch
):
    """The list once opened two connections per row, and a 50-row page spent
    its time connecting to a multi-GB database rather than reading it."""

    store = EmailStore(tmp_path / "page-lookups.sqlite3")
    first = _persist_scan(store, _classification(status=EmailClassificationStatus.PENDING_FEEDBACK))
    ids = [int(first["id"]), 999999]
    opened = _count_connections(store, monkeypatch)

    provider_states = store.get_provider_classification_states(ids)
    assert opened[0] == 1
    unsubscribe_states = store.list_unsubscribe_states(ids)
    assert opened[0] == 2

    assert provider_states == {
        classification_id: store.get_provider_classification_state(classification_id)
        for classification_id in ids
    }
    assert provider_states[999999]["reason"] == "classification_missing"
    assert unsubscribe_states == {ids[0]: None, 999999: None}


def test_unsubscribe_state_is_the_newest_unsubscribe_event_of_the_detail(tmp_path: Path):
    store, authorization, receipt = _persist_unsubscribe_result_fixture(
        tmp_path / "state.sqlite3", result_text="退订成功"
    )
    classification_id = authorization["classification_id"]

    events = store.list_email_classification_observability(classification_id)
    newest = [event for event in events if event["kind"] == "unsubscribe"][-1]

    assert store.list_unsubscribe_states([classification_id]) == {
        classification_id: {
            "status": newest["status"],
            "outcome": newest["outcome"],
            "error": newest.get("error", ""),
        }
    }


def _weaken_scan_cursor_state_check(database: Path) -> None:
    with sqlite3.connect(database) as db:
        original = db.execute(
            "select sql from sqlite_schema where type='table' and name='state'"
        ).fetchone()[0]
        assert original.count("uidvalidity>0") == 1
        damaged = original.replace("uidvalidity>0", "uidvalidity>=0", 1)
        version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema=on")
        db.execute(
            "update sqlite_schema set sql=? where type='table' and name='state'",
            (damaged,),
        )
        db.execute(f"pragma schema_version={version + 1}")
        db.commit()
        db.execute("pragma writable_schema=off")


def _damage_current_email_actions_metadata(
    database: Path,
    *,
    missing_column: str | None = None,
    changed_check: str | None = None,
    missing_attempt_fk: bool = False,
    nocase_column: str | None = None,
) -> None:
    """Change one known Email29 action-table contract in a current test DB."""
    missing_dependents = {
        "email_result_text_truncated": (
            " and (email_result_text_truncated is not null)",
            " and ((email_result_text_truncated in ( 0 , 1 )))",
        ),
        "email_result_text_digest": (
            " and (email_result_text_digest is not null)",
            " and ((email_result_text_digest = '' or length ( email_result_text_digest ) = 64))",
        ),
        "email_authorization_snapshot_json": (
            " and ((email_authorization_snapshot_json is null or json_valid ( email_authorization_snapshot_json )))",
        ),
    }
    checks = {
        "action_uppercase_status": (
            "email_status in ( 'pending' , 'processing' , 'done' , 'skipped' , 'failed' )",
            "email_status in ( 'PENDING' , 'PROCESSING' , 'DONE' , 'FAILED' )",
        ),
        "action_auto_reply": (
            "email_action_type in ( 'label' , 'mark_read' , 'archive' , 'move' , 'trash' , 'flag_important' )",
            "email_action_type in ( 'label' , 'mark_read' , 'archive' , 'move' , 'trash' , 'flag_important' , 'auto_reply' )",
        ),
        "action_reordered_status": (
            "email_status in ( 'pending' , 'processing' , 'done' , 'skipped' , 'failed' )",
            "email_status in ( 'pending' , 'processing' , 'done' , 'failed' , 'skipped' )",
        ),
        "attempt_nonpositive_number": (
            "email_attempt_number > 0", "email_attempt_number >= 0",
        ),
        "attempt_processing_status": (
            "email_status in ( 'done' , 'skipped' , 'failed' )",
            "email_status in ( 'processing' , 'done' , 'skipped' , 'failed' )",
        ),
    }
    assert sum((missing_column is not None, changed_check is not None,
                missing_attempt_fk, nocase_column is not None)) == 1
    with sqlite3.connect(database) as db:
        original = db.execute(
            "select sql from sqlite_schema where type='table' and name='actions'"
        ).fetchone()[0]
        damaged = original
        if missing_column is not None:
            assert missing_column in missing_dependents
            start = damaged.index("," + missing_column + " ")
            end = damaged.index(" virtual", start) + len(" virtual")
            damaged = damaged[:start] + damaged[end:]
            for clause in missing_dependents[missing_column]:
                assert damaged.count(clause) == 1
                damaged = damaged.replace(clause, "")
        elif changed_check is not None:
            assert changed_check in checks
            old, new = checks[changed_check]
            assert damaged.count(old) == (2 if changed_check == "attempt_nonpositive_number" else 1)
            if changed_check == "attempt_nonpositive_number":
                assert damaged.index("check(record_type<>'email_action_attempts'") < damaged.index(old)
                assert damaged.index(old) < damaged.index("check(record_type<>'email_action_plans'")
            damaged = damaged.replace(old, new, 1)
        elif missing_attempt_fk:
            clause = ("foreign key(email_operation_parent_type,email_action_id) "
                      "references actions(record_type,email_public_key) "
                      "on update NO ACTION on delete RESTRICT")
            assert damaged.count("," + clause) == 1
            damaged = damaged.replace("," + clause, "")
        else:
            assert nocase_column in {"email_action_type", "email_status"}
            old = f"{nocase_column} text generated always"
            assert damaged.count(old) == 1
            damaged = damaged.replace(old, f"{nocase_column} text collate nocase generated always")
        assert damaged != original
        version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema=on")
        try:
            db.execute(
                "update sqlite_schema set sql=? where type='table' and name='actions'",
                (damaged,),
            )
            db.execute(f"pragma schema_version={version + 1}")
            db.commit()
        finally:
            db.execute("pragma writable_schema=off")


def _assert_current_email_metadata_rejected_without_repair(database: Path) -> None:
    with sqlite3.connect(database) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from actions").fetchone()[0] >= 0
        schema_before = db.execute(
            "select type,name,sql from sqlite_schema order by type,name"
        ).fetchall()
        facts_before = db.execute(
            "select record_type,legacy_key,payload_json from actions order by id"
        ).fetchall()
    bytes_before = sha256(database.read_bytes()).hexdigest()
    for validate_rows in (True, False):
        with pytest.raises(
            sqlite3.IntegrityError, match="unsupported or malformed Store source schema"
        ):
            EmailStore(database, validate_rows=validate_rows)
    with sqlite3.connect(database) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from actions").fetchone()[0] >= 0
        assert db.execute(
            "select type,name,sql from sqlite_schema order by type,name"
        ).fetchall() == schema_before
        assert db.execute(
            "select record_type,legacy_key,payload_json from actions order by id"
        ).fetchall() == facts_before
    assert sha256(database.read_bytes()).hexdigest() == bytes_before


def test_terminal_unsubscribe_probe_does_not_hydrate_classification_payloads(tmp_path):
    from contextlib import contextmanager

    store = EmailStore(tmp_path / "unsubscribe-terminal-probe.sqlite3")
    original_connect = store._connect

    @contextmanager
    def guarded_connect():
        with original_connect() as db:
            def authorize(action, table, column, database, source):
                if action == sqlite3.SQLITE_READ and table == "emails":
                    if column in {"message_subject", "message_preview", "class_model_text", "class_agent_result_json"}:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db

    store._connect = guarded_connect
    assert store.list_terminal_unsubscribe_tasks_missing_receipts() == []


def _insert_account_with_scan_folders_json(database: Path, value: object) -> None:
    with sqlite3.connect(database) as db:
        if isinstance(value, (bytes, bytearray, memoryview)):
            # Preserve the malformed-UTF8 corruption case by bypassing only
            # the payload-shape CHECK, as the historical fixture did.
            db.execute("pragma ignore_check_constraints = on")
        db.execute("begin immediate")
        insert_config(db, "email_accounts", {
            "account_id": "shape-test-account",
            "display_name": "Shape test",
            "email_address": "redacted@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": 1,
            "imap_username": "redacted@example.com",
            "imap_secret_reference": "IMAP_SECRET_REFERENCE",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": 1,
            "smtp_username": "redacted@example.com",
            "smtp_secret_reference": "SMTP_SECRET_REFERENCE",
            "enabled": 1,
            "scan_folders_json": value,
            "scan_interval_seconds": 60,
            "created_at": "2026-08-29T16:00:00+00:00",
            "updated_at": "2026-08-29T16:00:00+00:00",
        })


def _create_action_with_attempts(
    database: Path,
    statuses: tuple[str, ...],
) -> tuple[EmailStore, str]:
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    action_id = _fetchall(database, f"select action_id from ({EMAIL_DIRECT_ACTION_SQL})")[0][
        "action_id"
    ]
    for attempt_number, status in enumerate(statuses, start=1):
        store.append_action_attempt(
            action_id=action_id,
            attempt_number=attempt_number,
            status=status,
            provider_operation=f"operation-{attempt_number}",
            provider_target=f"target-{attempt_number}",
            provider_result_id=(
                f"receipt-{attempt_number}" if status == "done" else ""
            ),
            error=(f"error-{attempt_number}" if status == "failed" else ""),
            started_at=f"2026-08-29T16:0{attempt_number}:00+00:00",
            finished_at=f"2026-08-29T16:0{attempt_number}:01+00:00",
        )
    return store, action_id


def test_email_cursor_conflict_is_a_clear_domain_error():
    conflict_type = getattr(email_store_module, "EmailCursorConflict", None)

    assert conflict_type is not None
    assert issubclass(conflict_type, RuntimeError)


def test_email_store_lists_pending_and_processed_separately(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        sender="sender@example.com",
        subject="Need a decision",
        preview="Please review",
    )
    second = _classification(
        status=EmailClassificationStatus.PROCESSED, message_id="msg-2"
    )
    store.upsert_classification(second)

    pending, pending_total = StoredEmailListingOracle(store).list_classifications(
        status=EmailClassificationStatus.PENDING_FEEDBACK, limit=20, offset=0
    )
    processed, processed_total = StoredEmailListingOracle(store).list_classifications(
        status=EmailClassificationStatus.PROCESSED, limit=20, offset=0
    )

    assert pending_total == 1
    assert pending[0]["subject"] == "Need a decision"
    assert processed_total == 1
    assert processed[0]["status"] == "processed"
    assert processed[0]["model_id"] == "email/logistic/model-1"
    assert "model_version" not in processed[0]


def test_email_store_gets_one_classification_directly_by_primary_key(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    pending = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="primary-key-pending",
        )
    )
    processed = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            message_id="primary-key-processed",
        )
    )

    assert store.get_classification(pending["id"])["status"] == "pending_feedback"
    assert store.get_classification(processed["id"])["status"] == "processed"
    assert store.get_classification(999) is None


def test_email_store_rejects_unredacted_model_text(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="model_text must be redacted"):
        store.upsert_classification(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
            model_text="sender@example.com https://private.example/message",
        )


def test_feedback_moves_a_message_to_processed_and_records_user_source(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    confirmed = _confirm(store, row["id"], EmailCategory.NOTIFICATION)

    assert confirmed is not None
    assert confirmed["category"] == "notification"
    assert confirmed["status"] == "processed"
    assert confirmed["classification_source"] == "user"
    assert confirmed["action_plan"]["category"] == "notification"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert _confirm(store, 999, EmailCategory.WORK) is None


def test_feedback_rebuilds_action_plan_for_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="需要尽快处理",
        threshold=0.97,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["important"]}},
        enabled=True,
        config_version="important-v2",
    )
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        model_text="__subject__合同确认",
    )

    confirmed = _confirm(store, row["id"], EmailCategory.NOTIFICATION)

    assert confirmed is not None
    assert confirmed["category"] == "notification"
    assert confirmed["config_version"] == "important-v2"
    assert confirmed["action_plan"]["action_plan_version"] == 1
    assert confirmed["action_plan"]["classification_id"] == confirmed["id"]
    assert confirmed["action_plan"]["account_id"] == "dingtalk-account"
    assert confirmed["action_plan"]["category"] == "notification"
    assert confirmed["action_plan"]["classification_source"] == "user"
    assert confirmed["action_plan"]["model_id"] == "email/logistic/model-1"
    assert confirmed["action_plan"]["config_version"] == "important-v2"
    assert confirmed["action_plan"]["actions"] == ["label"]
    assert confirmed["action_plan"]["action_parameters"] == {
        "label": {"labels": ["important"]}
    }
    assert "is_execution_authorization" not in confirmed["action_plan"]


def test_processed_email_cannot_be_confirmed_as_new_feedback(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PROCESSED)
    )

    with pytest.raises(EmailClassificationConflict):
        _confirm(store, row["id"], EmailCategory.NOTIFICATION)


def test_concurrent_feedback_allows_one_confirmation_and_one_conflict(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "worker.sqlite3")
    row = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
        model_text="__subject__concurrent-confirmation",
    )
    ready = Barrier(2)

    def confirm(category: EmailCategory):
        ready.wait()
        try:
            return _confirm(store, row["id"], category)
        except EmailClassificationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                confirm,
                (EmailCategory.NOTIFICATION, EmailCategory.PERSONAL),
            )
        )

    confirmed = [result for result in results if isinstance(result, dict)]
    conflicts = [
        result for result in results if isinstance(result, EmailClassificationConflict)
    ]
    assert len(confirmed) == 1
    assert len(conflicts) == 1
    persisted, total = StoredEmailListingOracle(store).list_classifications(
        status=EmailClassificationStatus.PROCESSED,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert persisted[0]["category"] == confirmed[0]["category"]
    assert store.count_unincluded_training_examples() == 1


def test_training_examples_exclude_pending_or_unconfirmed_user_rows_without_reopen(
    tmp_path: Path,
):
    database = tmp_path / "training-boundary.sqlite3"
    store = EmailStore(database)
    contaminated = _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="pending-contamination",
        ),
    )
    confirmed = _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="confirmed-training-example",
        ),
    )
    confirmed_row = _confirm(
        store,
        confirmed["id"],
        EmailCategory.NOTIFICATION,
    )
    assert confirmed_row is not None
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update emails
            set class_classification_source='user'
            where classification_id=?
            """,
            (contaminated["id"],),
        )

    assert store.count_unincluded_training_examples() == 1
    assert store.get_classification(confirmed_row["id"])["confirmed_category"] == "notification"


def test_rescan_preserves_a_user_confirmed_category(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )
    _confirm(store, original["id"], EmailCategory.NOTIFICATION)

    rescanned = store.upsert_classification(
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    )

    assert rescanned["id"] == original["id"]
    assert rescanned["category"] == "notification"
    assert rescanned["status"] == "processed"
    assert rescanned["classification_source"] == "user"


def test_rescan_preserves_all_user_confirmed_action_plan_fields(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")
    original = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.61,
            model_id="email/logistic/model-v1",
        )
    )
    confirmed = _confirm(store, original["id"], EmailCategory.NOTIFICATION)
    assert confirmed is not None

    rescanned = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            confidence=0.88,
            model_id="email/logistic/model-v2",
        )
    )

    plan = rescanned["action_plan"]
    assert plan is not None
    assert rescanned["id"] == plan["classification_id"]
    assert rescanned["account_id"] == plan["account_id"]
    assert rescanned["category"] == plan["category"]
    assert rescanned["classification_source"] == plan["classification_source"]
    assert rescanned["confidence"] == plan["confidence"] == 0.61
    assert rescanned["model_id"] == plan["model_id"] == "email/logistic/model-v1"
    assert rescanned["config_version"] == plan["config_version"]
    assert rescanned["action_plan"] == confirmed["action_plan"]


def test_email_store_persists_category_configuration(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")

    config = store.upsert_config(
        category=EmailCategory.EXTERNAL_BILLING,
        description="营销订阅和定期通讯",
        threshold=0.98,
        actions=(EmailAction.LABEL, EmailAction.UNSUBSCRIBE),
        action_parameters={EmailAction.LABEL: {"labels": ["subscription"]}},
        enabled=True,
        config_version="email-v2",
    )

    assert config["actions"] == ["label", "unsubscribe"]
    assert config["action_parameters"] == {"label": {"labels": ["subscription"]}}
    assert (
        next(
            row for row in store.list_configs() if row["category"] == "external_billing"
        )
        == config
    )


def test_email_store_rejects_auto_reply_category_configuration(tmp_path: Path):
    store = EmailStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="auto_reply is disabled"):
        store.upsert_config(
            category=EmailCategory.WORK,
            description="Work",
            threshold=0.95,
            actions=(EmailAction.AUTO_REPLY,),
            action_parameters={
                EmailAction.AUTO_REPLY: {"instruction": "Reply automatically"}
            },
            enabled=True,
            config_version="email-config:auto-reply-rejected",
        )


def test_reopen_rejects_feedback_request_linked_to_another_classification(
    tmp_path: Path,
):
    database = tmp_path / "feedback-corruption.sqlite3"
    store = EmailStore(database)
    first = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="feedback-first",
        )
    )
    second = store.upsert_classification(
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            message_id="feedback-second",
        )
    )
    applied = store.apply_human_classification(
        first["id"],
        EmailCategory.NOTIFICATION,
        feedback_request_id="feedback-request-1",
        expected_current_action_plan_id=None,
    )
    assert applied is not None
    with sqlite3.connect(database) as db:
        feedback_rows = db.execute(
            f"select rowid from ({email_action_projection('email_feedback_requests', include_rowid=True)})"
        ).fetchall()
        for feedback_row in feedback_rows:
            update_email_action(db, "email_feedback_requests", feedback_row[0], {
                "classification_id": second["id"],
            })

    with pytest.raises(EmailPersistenceCorruption, match="feedback request"):
        EmailStore(database)


def test_runtime_initialization_validates_accounts_without_historical_scan(tmp_path, monkeypatch):
    path = tmp_path / "runtime-init.sqlite3"
    EmailStore(path)

    def unexpected_historical_scan(*_args):
        raise AssertionError("runtime initialization scanned historical rows")

    monkeypatch.setattr(EmailStore, "_validate_durable_state", unexpected_historical_scan)
    calls = []
    original = EmailStore._validate_account_configuration

    def validate_accounts(self, db):
        calls.append(self.path)
        original(self, db)

    monkeypatch.setattr(EmailStore, "_validate_account_configuration", validate_accounts)
    EmailStore(path, validate_rows=False)
    assert calls == [path]


def test_email_store_migration_is_idempotent(tmp_path: Path):
    database = tmp_path / "idempotent.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )

    EmailStore(database)
    EmailStore(database)

    assert len(_fetchall(database, "select * from email_schema_migrations")) == 1
    assert len(_fetchall(database, f"select * from ({email_message_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({email_classification_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_PLAN_SQL})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL})")) == 1


def test_current_email_schema_persists_external_training_payload(tmp_path: Path) -> None:
    from app.email_training_data import read_snapshot

    assert email_store_module.EMAIL_SCHEMA_VERSION >= 46
    store = EmailStore(tmp_path / "schema-46.sqlite3")
    snapshot = _frozen_training_snapshot()
    store.persist_training_snapshot(snapshot)
    assert read_snapshot(store.path, snapshot.snapshot_digest).to_dict() == snapshot.to_dict()
    with sqlite3.connect(store.path) as db:
        assert db.execute("select max(version) from email_schema_migrations").fetchone()[0] == email_store_module.EMAIL_SCHEMA_VERSION
        assert db.execute("select 1 from sqlite_schema where name='email_training_snapshot_observations'").fetchone() is None
        summary = json.loads(db.execute(
            f"select manifest_json from ({config_projection('email_training_snapshots')}) where snapshot_id=?",
            (snapshot.snapshot_id,),
        ).fetchone()[0])
        assert summary["sample_count"] == len(snapshot.observations)


@pytest.mark.parametrize(
    "read_data_version", (False, True), ids=("native-trace", "quoted-main-data-version")
)
def test_current_schema_initialization_preserves_delete_journal_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_data_version: bool,
):
    database = tmp_path / "current-schema-read-snapshot.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        journal_mode = db.execute("pragma journal_mode = delete").fetchone()[0]
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    assert journal_mode == "delete"

    statements: list[str] = []
    original_open_connection = EmailStore._open_connection

    def traced_open_connection(
        self: EmailStore, *, foreign_keys: bool = True
    ) -> sqlite3.Connection:
        db = original_open_connection(self, foreign_keys=foreign_keys)
        db.set_trace_callback(statements.append)
        if read_data_version:
            # SQLite 3.45.1 emits this read during schema introspection on Linux.
            assert isinstance(db.execute("PRAGMA 'main'.data_version").fetchone()[0], int)
        return db

    monkeypatch.setattr(EmailStore, "_open_connection", traced_open_connection)

    EmailStore(database)

    with sqlite3.connect(database) as db:
        journal_mode = db.execute("pragma journal_mode").fetchone()[0]
        schema_version_after = db.execute("pragma schema_version").fetchone()[0]
    normalized = [" ".join(statement.lower().split()) for statement in statements]
    assert journal_mode == "delete"
    assert schema_version_after == schema_version_before
    assert "begin" in normalized
    assert "begin immediate" not in normalized
    assert not any(
        statement.startswith("pragma journal_mode") for statement in normalized
    )
    read_pragma_prefixes = (
        "pragma table_list",
        "pragma table_info",
        "pragma index_list",
        "pragma index_info",
        "pragma index_xinfo",
        "pragma foreign_key_list",
        "pragma main.table_xinfo",
        "pragma main.foreign_key_list",
        "pragma main.index_list",
        "pragma main.index_xinfo",
        # The read-schema cache compares the file's schema version.
        "pragma database_list",
        "pragma main.schema_version",
    )
    rejected = [
        statement
        for statement in normalized
        if not (
            statement in {"begin", "commit", "pragma 'main'.data_version"}
            or statement.startswith("select ")
            or statement.startswith(read_pragma_prefixes)
        )
    ]
    assert not rejected, (
        f"rejected normalized SQLite traces={rejected}; "
        f"Python={sys.version}; SQLite={sqlite3.sqlite_version}"
    )
    assert not any(
        statement.startswith(("create ", "alter ", "insert ", "update ", "delete "))
        for statement in normalized
    )


def test_current_schema_rejects_extra_canonical_email_column(tmp_path: Path):
    database = tmp_path / "extra-canonical-column.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        db.execute("alter table emails add column unrelated_extension text")
    with pytest.raises(
        sqlite3.IntegrityError,
        match="unsupported or malformed Store source schema",
    ):
        EmailStore(database)
    assert "unrelated_extension" in {
        row["name"] for row in _fetchall(database, "pragma table_info(emails)")
    }


def test_current_schema_rejects_weakened_cursor_state_check(
    tmp_path: Path,
):
    database = tmp_path / "weakened-cursor-schema.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    _weaken_scan_cursor_state_check(database)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="unsupported or malformed Store source schema",
    ):
        EmailStore(database)


def test_current_schema_rejects_wrong_account_column_nullability(tmp_path: Path):
    database = tmp_path / "wrong-account-nullability.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        original = db.execute("select sql from sqlite_schema where name='configs'").fetchone()[0]
        assert original.count("(account_display_name is not null)") == 1
        damaged = original.replace("(account_display_name is not null)", "(1)")
        version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema=on")
        db.execute("update sqlite_schema set sql=? where name='configs'", (damaged,))
        db.execute(f"pragma schema_version={version + 1}")
        db.commit()
        db.execute("pragma writable_schema=off")

    before = sha256(database.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source schema"):
        EmailStore(database)
    assert sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    ("original_clause", "damaged_clause"),
    (
        ("category_threshold real generated always", "category_threshold text generated always"),
        ("trim(category_core_description)!=''", "trim(category_core_description)=''"),
    ),
)
def test_current_schema_rejects_wrong_config_type_or_check(
    tmp_path: Path,
    original_clause: str,
    damaged_clause: str,
):
    database = tmp_path / "wrong-config-declaration.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        original = db.execute(
            "select sql from sqlite_schema where type='table' and name='configs'"
        ).fetchone()[0]
        assert original.count(original_clause) == 1
        damaged = original.replace(original_clause, damaged_clause, 1)
        version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema=on")
        db.execute(
            "update sqlite_schema set sql=? where type='table' and name='configs'",
            (damaged,),
        )
        db.execute(f"pragma schema_version={version + 1}")
        db.commit()
        db.execute("pragma writable_schema=off")
    with pytest.raises(
        sqlite3.IntegrityError, match="unsupported or malformed Store source schema"
    ):
        EmailStore(database)


def test_durable_validation_boundary_normalizes_missing_row_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "missing-row-field.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    def read_missing_field(_self, db):
        row = db.execute(f"select stable_message_identity from ({email_message_projection()})").fetchone()
        row["thread_identity"]

    monkeypatch.setattr(EmailStore, "_validate_durable_rows", read_missing_field)

    with pytest.raises(EmailPersistenceCorruption, match="missing a required field"):
        EmailStore(database)


@pytest.mark.parametrize(
    ("damage_sql", "match"),
    (
        ("drop table state", "unsupported or malformed Store source schema"),
        (
            "drop index idx_state_email_scan_cursors_pk",
            "unsupported or malformed Store source schema",
        ),
        (
            "drop trigger trg_configs_snapshot_update",
            "unsupported or malformed Store source schema",
        ),
    ),
)
def test_current_schema_missing_required_structure_is_domain_corruption(
    tmp_path: Path,
    damage_sql: str,
    match: str,
):
    database = tmp_path / f"missing-structure-{match}.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        db.execute(damage_sql)

    with pytest.raises(sqlite3.IntegrityError, match=match):
        EmailStore(database)


def test_current_schema_requires_unsubscribe_receipt_classification_index(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-unsubscribe-receipt-index.sqlite3"
    EmailStore(database)
    index_name = "idx_actions_email_email_unsubscribe_receipts_0"
    with sqlite3.connect(database) as db:
        indexes = {
            row[1]
            for row in db.execute("pragma index_list(actions)")
        }
        assert index_name in indexes
        assert db.execute(
            "select sql from sqlite_schema where type='index' and name=?",
            (index_name,),
        ).fetchone()[0] == (
            "CREATE INDEX idx_actions_email_email_unsubscribe_receipts_0 "
            "on actions(email_classification_id,email_action_identity) "
            "where record_type='email_unsubscribe_receipts'"
        )
        db.execute(f"drop index {index_name}")

    with pytest.raises(
        sqlite3.IntegrityError, match="unsupported or malformed Store source schema"
    ):
        EmailStore(database)
    with sqlite3.connect(database) as db:
        assert index_name not in {
            row[1]
            for row in db.execute("pragma index_list(actions)")
        }


@pytest.mark.parametrize(
    "missing_column",
    ("result_text_truncated", "result_text_digest"),
)
def test_current_v17_schema_missing_result_integrity_column_fails_without_repair(
    tmp_path: Path,
    missing_column: str,
) -> None:
    database = tmp_path / f"missing-{missing_column}.sqlite3"
    EmailStore(database)
    target_column = "email_" + missing_column
    _damage_current_email_actions_metadata(database, missing_column=target_column)
    with sqlite3.connect(database) as db:
        assert target_column not in {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert target_column not in db.execute("select sql from sqlite_schema where name='actions'").fetchone()[0]
    _assert_current_email_metadata_rejected_without_repair(database)


def test_current_schema_missing_required_state_primary_key_is_rejected(
    tmp_path: Path,
):
    database = tmp_path / "missing-cursor-primary-key.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    with sqlite3.connect(database) as db:
        original = db.execute(
            "select sql from sqlite_schema where type='table' and name='state'"
        ).fetchone()[0]
        assert original.count("id integer primary key") == 1
        damaged = original.replace("id integer primary key", "id integer", 1)
        version = db.execute("pragma schema_version").fetchone()[0]
        db.execute("pragma writable_schema=on")
        db.execute(
            "update sqlite_schema set sql=? where type='table' and name='state'",
            (damaged,),
        )
        db.execute(f"pragma schema_version={version + 1}")
        db.commit()
        db.execute("pragma writable_schema=off")

    with pytest.raises(
        sqlite3.IntegrityError,
        match="unsupported or malformed Store source schema",
    ):
        EmailStore(database)


def test_current_schema_missing_required_unique_key_is_rejected_without_repair(
    tmp_path: Path,
):
    database = tmp_path / "missing-message-unique-key.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()

    def unique_index_columns(db):
        return {
            tuple(column[2] for column in db.execute(
                f"pragma index_info({json.dumps(index[1])})"
            ))
            for index in db.execute("pragma index_list('emails')")
            if index[2]
        }

    with sqlite3.connect(database) as db:
        assert not db.in_transaction
        assert db.execute("pragma foreign_keys").fetchone()[0] == 0
        assert db.execute("select count(*) from emails").fetchone()[0] == 0
        original_sql = db.execute(
            "select sql from sqlite_schema where type='table' and name='emails'"
        ).fetchone()[0]
        required_unique = "stable_message_identity text not null unique,"
        assert original_sql.count(required_unique) == 1
        owned_objects = db.execute(
            "select type,name,sql from sqlite_schema "
            "where tbl_name='emails' and type in ('index','trigger') "
            "and sql is not null order by type,name"
        ).fetchall()
        protected_schema = db.execute(
            "select type,name,tbl_name,sql from sqlite_schema "
            "where tbl_name!='emails' order by type,name,tbl_name"
        ).fetchall()
        sequence_exists = db.execute(
            "select 1 from sqlite_schema where name='sqlite_sequence'"
        ).fetchone() is not None
        sequence_before = (
            db.execute("select name,seq from sqlite_sequence order by name").fetchall()
            if sequence_exists else None
        )
        columns_before = db.execute("pragma table_xinfo('emails')").fetchall()
        foreign_keys_before = db.execute("pragma foreign_key_list('emails')").fetchall()
        unique_before = unique_index_columns(db)
        assert {
            ("classification_id",), ("message_id",),
            ("stable_message_identity",),
        } <= unique_before

        # Empty parent: DROP does not rename dependent references.
        db.execute("drop table emails")
        db.execute(original_sql.replace(
            required_unique, "stable_message_identity text not null,"
        ))
        for _kind, _name, sql in owned_objects:
            db.execute(sql)

        assert db.execute("select count(*) from emails").fetchone()[0] == 0
        assert db.execute("pragma table_xinfo('emails')").fetchall() == columns_before
        assert db.execute("pragma foreign_key_list('emails')").fetchall() == foreign_keys_before
        assert unique_index_columns(db) == unique_before - {("stable_message_identity",)}
        assert db.execute(
            "select type,name,tbl_name,sql from sqlite_schema "
            "where tbl_name!='emails' order by type,name,tbl_name"
        ).fetchall() == protected_schema
        if sequence_exists:
            assert db.execute(
                "select name,seq from sqlite_sequence order by name"
            ).fetchall() == sequence_before

    with sqlite3.connect(database) as db:
        damaged_schema = db.execute(
            "select type,name,tbl_name,sql from sqlite_schema order by type,name,tbl_name"
        ).fetchall()
        damaged_actions = db.execute(
            "select record_type,legacy_key,payload_json from actions order by id"
        ).fetchall()
        damaged_ledger = db.execute(
            "select version,applied_at from email_schema_migrations order by version"
        ).fetchall()
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    damaged_bytes = sha256(database.read_bytes()).hexdigest()

    # Paired preflight rejects the malformed current declaration before row validation.
    with pytest.raises(
        sqlite3.IntegrityError, match="unsupported or malformed Store source schema"
    ):
        EmailStore(database)

    with sqlite3.connect(database) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute(
            "select type,name,tbl_name,sql from sqlite_schema order by type,name,tbl_name"
        ).fetchall() == damaged_schema
        assert db.execute(
            "select record_type,legacy_key,payload_json from actions order by id"
        ).fetchall() == damaged_actions
        assert db.execute(
            "select version,applied_at from email_schema_migrations order by version"
        ).fetchall() == damaged_ledger
        assert db.execute("select count(*) from emails").fetchone()[0] == 0
    assert sha256(database.read_bytes()).hexdigest() == damaged_bytes


def test_invalid_utf8_json_blob_is_domain_corruption(tmp_path: Path):
    database = tmp_path / "invalid-utf8-json.sqlite3"
    EmailStore(database)
    gc.collect()
    _close_idle_connections()
    _insert_account_with_scan_folders_json(database, sqlite3.Binary(b"\xff"))

    with pytest.raises(EmailPersistenceCorruption, match="scan_folders_json"):
        EmailStore(database)


def test_malformed_schema_version_is_domain_corruption(tmp_path: Path):
    database = tmp_path / "malformed-schema-version.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            create table email_schema_migrations (
                version text primary key,
                applied_at text not null
            );
            insert into email_schema_migrations values (
                'not-an-integer',
                '2026-08-29T16:00:00+00:00'
            );
            """
        )

    with sqlite3.connect(database) as db:
        schema_before = db.execute(
            "select type, name, tbl_name, sql from sqlite_schema order by type, name"
        ).fetchall()
        facts_before = db.execute("select * from email_schema_migrations").fetchall()
        cookie_before = db.execute("pragma schema_version").fetchone()[0]
    bytes_before = sha256(database.read_bytes()).hexdigest()

    with pytest.raises(
        sqlite3.IntegrityError,
        match="unsupported or malformed Store source schema",
    ):
        EmailStore(database)
    with sqlite3.connect(database) as db:
        assert db.execute(
            "select type, name, tbl_name, sql from sqlite_schema order by type, name"
        ).fetchall() == schema_before
        assert db.execute(
            "select * from email_schema_migrations"
        ).fetchall() == facts_before
        assert db.execute("pragma schema_version").fetchone()[0] == cookie_before
    assert sha256(database.read_bytes()).hexdigest() == bytes_before
    assert not Path(f"{database}-wal").exists()


def test_current_v16_missing_authorization_column_fails_without_schema_repair(
    tmp_path: Path,
):
    database = tmp_path / "v16-missing-authorization-column.sqlite3"
    EmailStore(database)
    target_column = "email_authorization_snapshot_json"
    _damage_current_email_actions_metadata(database, missing_column=target_column)
    with sqlite3.connect(database) as db:
        assert target_column not in {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert target_column not in db.execute("select sql from sqlite_schema where name='actions'").fetchone()[0]
    _assert_current_email_metadata_rejected_without_repair(database)


def test_concurrent_first_initialization_is_transactionally_idempotent(
    tmp_path: Path,
):
    database = tmp_path / "concurrent-init.sqlite3"
    ready = Barrier(2)

    def initialize(_: int) -> EmailStore:
        ready.wait()
        return EmailStore(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        stores = list(executor.map(initialize, range(2)))

    assert len(stores) == 2
    assert len(_fetchall(database, "select * from email_schema_migrations")) == 1
    assert EmailStore(database).count_unincluded_training_examples() == 0


def test_startup_rejects_orphan_message_without_classification(tmp_path: Path):
    database = tmp_path / "orphan-message.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        assert delete_email_classification(db, int(db.execute("select classification_id from emails").fetchone()[0])) == 1

    EmailStore(database)


def test_startup_accepts_canonical_and_empty_locator_metadata_equivalence(
    tmp_path: Path,
):
    database = tmp_path / "canonical-locator.sqlite3"
    store = EmailStore(database)
    canonical = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="canonical-rfc",
        stable_message_identity=("dingtalk-account:message-id:<Canonical@example.com>"),
        rfc_message_id="<Canonical@EXAMPLE.COM>",
        thread_id="  thread-canonical  ",
    )
    fallback = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="canonical-empty",
        stable_message_identity="dingtalk-account:imap:Archive:42:9",
        folder="Archive",
        uid=9,
        rfc_message_id=None,
        thread_id="   ",
    )
    store.upsert_classification(canonical)
    store.upsert_classification(fallback)

    rows = _fetchall(
        database,
        """
        select stable_message_identity,
               class_rfc_message_id as classification_rfc,
               class_thread_id as classification_thread,
               message_rfc_message_id as message_rfc,
               message_thread_identity as message_thread
        from emails order by stable_message_identity
        """,
    )
    by_identity = {row["stable_message_identity"]: row for row in rows}
    canonical_row = by_identity[canonical.stable_message_identity]
    assert canonical_row["classification_rfc"] == "<Canonical@example.com>"
    assert canonical_row["message_rfc"] == "<Canonical@example.com>"
    assert canonical_row["classification_thread"] == "thread-canonical"
    assert canonical_row["message_thread"] == "thread-canonical"
    fallback_row = by_identity[fallback.stable_message_identity]
    assert fallback_row["classification_rfc"] is None
    assert fallback_row["message_rfc"] == ""
    assert fallback_row["classification_thread"] is None
    assert fallback_row["message_thread"] == ""

    EmailStore(database)


def test_startup_normalizes_both_persisted_locator_metadata_sides(tmp_path: Path):
    database = tmp_path / "semantic-locator-equivalence.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            thread_id="thread-semantic",
        ),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update emails
            set message_rfc_message_id='<msg-1@EXAMPLE.COM>',
                message_thread_identity='  thread-semantic  '
            """
        )
        db.execute(
            """
            update emails
            set class_rfc_message_id=' msg-1@example.com ',
                class_thread_id='thread-semantic'
            """
        )

    message = _fetchall(database, f"select * from ({email_message_projection()})")[0]
    classification = _fetchall(database, f"select * from ({email_classification_projection()})")[0]
    assert message["rfc_message_id"] != classification["rfc_message_id"]
    assert message["thread_identity"] != classification["thread_id"]
    message_canonical = EmailProviderLocator.model_validate(
        {
            "account_id": message["account_id"],
            "folder": message["folder"],
            "uidvalidity": message["uidvalidity"],
            "uid": message["uid"],
            "rfc_message_id": message["rfc_message_id"],
            "thread_id": message["thread_identity"],
        }
    )
    classification_canonical = EmailProviderLocator.model_validate(
        {
            "account_id": classification["account_id"],
            "folder": classification["folder"],
            "uidvalidity": classification["uidvalidity"],
            "uid": classification["uid"],
            "rfc_message_id": classification["rfc_message_id"],
            "thread_id": classification["thread_id"],
        }
    )
    assert message_canonical.rfc_message_id == "<msg-1@example.com>"
    assert message_canonical.rfc_message_id == classification_canonical.rfc_message_id
    assert message_canonical.thread_id == "thread-semantic"
    assert message_canonical.thread_id == classification_canonical.thread_id

    EmailStore(database)


def test_startup_rejects_current_action_plan_pointer_rollback(tmp_path: Path):
    database = tmp_path / "pointer-rollback.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update emails
            set class_current_action_plan_id=?
            """,
            (
                classification.action_plan.action_plan_id,
            ),
        )

    with pytest.raises(EmailPersistenceCorruption, match="highest ActionPlan"):
        EmailStore(database)


@pytest.mark.parametrize(
    "classification_update",
    [
        "account_id='other-account'",
        "class_category='personal', class_confirmed_category='personal'",
        "class_classification_source='user'",
        "class_confidence=0.22",
        "class_model_id='email/logistic/other-model'",
        "class_config_version='other-config'",
    ],
)
def test_startup_rejects_current_plan_classification_field_mismatch(
    tmp_path: Path,
    classification_update: str,
):
    database = tmp_path / "plan-classification-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(f"update emails set {classification_update}")

    with pytest.raises(EmailPersistenceCorruption, match="classification fields"):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_action_plan(tmp_path: Path):
    database = tmp_path / "pending-with-plan.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "update emails set class_status='pending_feedback', class_confirmed_category=null"
        )

    with pytest.raises(
        EmailPersistenceCorruption, match="pending feedback.*ActionPlan"
    ):
        EmailStore(database)


def test_startup_rejects_pending_feedback_with_user_source(tmp_path: Path):
    database = tmp_path / "pending-user-source.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute("update emails set class_classification_source='user'")

    with pytest.raises(EmailPersistenceCorruption, match="pending feedback.*model"):
        EmailStore(database)


def test_startup_rejects_legacy_user_processed_with_mismatched_confirmation(
    tmp_path: Path,
):
    database = tmp_path / "legacy-user-confirmation-mismatch.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            """
            update emails
            set class_status='processed', class_classification_source='user',
                class_category='important', class_confirmed_category='work'
            """
        )

    with pytest.raises(EmailPersistenceCorruption, match="user-confirmed.*category"):
        EmailStore(database)


def test_startup_accepts_model_processed_without_user_confirmation(tmp_path: Path):
    database = tmp_path / "model-processed-unconfirmed.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    with sqlite3.connect(database) as db:
        db.execute("update emails set class_confirmed_category=null")

    EmailStore(database)


def test_pending_feedback_persists_message_and_cursor_without_plan_or_actions(
    tmp_path: Path,
):
    database = tmp_path / "pending.sqlite3"
    store = EmailStore(database)

    persisted = _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )

    assert persisted["status"] == "pending_feedback"
    assert persisted["current_action_plan_id"] is None
    assert len(_fetchall(database, f"select * from ({email_message_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_PLAN_SQL})")) == 0
    assert len(_fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL})")) == 0
    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 42
    assert cursor["last_seen_uid"] == persisted["uid"]


def test_processed_scan_persists_plan_and_only_direct_action_rows(tmp_path: Path):
    database = tmp_path / "processed.sqlite3"
    store = EmailStore(database)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(
            EmailAction.LABEL,
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.UNSUBSCRIBE,
        ),
        action_parameters={
            EmailAction.LABEL: {"labels": ["work"]},
            EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt"},
        },
    )

    persisted = _persist_scan(store, classification)

    assert (
        persisted["current_action_plan_id"] == classification.action_plan.action_plan_id
    )
    plans = _fetchall(database, f"select * from ({EMAIL_PLAN_SQL})")
    assert len(plans) == 1
    assert json.loads(plans[0]["actions_json"]) == [
        "label",
        "mark_read",
        "auto_reply",
        "unsubscribe",
    ]
    actions = _fetchall(
        database,
        f"select action_type, status from ({EMAIL_DIRECT_ACTION_SQL}) order by action_type",
    )
    assert [(row["action_type"], row["status"]) for row in actions] == [
        ("label", "pending"),
        ("mark_read", "pending"),
    ]


def test_exact_scan_replay_is_idempotent(tmp_path: Path):
    database = tmp_path / "replay.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)

    first = _persist_scan(store, classification)
    second = _persist_scan(store, classification)

    assert second["id"] == first["id"]
    assert len(_fetchall(database, f"select * from ({email_message_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({email_classification_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_PLAN_SQL})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL})")) == 1
    assert store.count_unincluded_training_examples() == 0


def test_processed_model_rescan_preserves_business_snapshot_plan_and_actions(
    tmp_path: Path,
):
    database = tmp_path / "processed-model-rescan.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="processed-model-rescan",
        confidence=0.93,
        model_id="email/logistic/model-original",
        config_version="email-config-original",
        category=EmailCategory.WORK,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["work"]}},
        thread_id="thread-original",
    )
    _persist_scan(store, original)
    classification_before = dict(
        _fetchall(database, f"select * from ({email_classification_projection()})")[0]
    )
    message_before = dict(_fetchall(database, f"select * from ({email_message_projection()})")[0])
    plans_before = [
        dict(row)
        for row in _fetchall(
            database,
            f"select * from ({EMAIL_PLAN_SQL}) order by action_plan_version",
        )
    ]
    actions_before = [
        dict(row)
        for row in _fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL}) order by action_id")
    ]
    training_before = store.owner_labelled_examples()[0]

    changed = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="processed-model-rescan-changed",
        confidence=0.51,
        model_id="email/logistic/model-changed",
        config_version="email-config-changed",
        category=EmailCategory.PERSONAL,
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Archive/Personal"}},
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=original.provider_locator.rfc_message_id,
        thread_id="thread-current",
    )
    rescanned = _persist_scan(
        store,
        changed,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
    )

    classification_after = dict(
        _fetchall(database, f"select * from ({email_classification_projection()})")[0]
    )
    message_after = dict(_fetchall(database, f"select * from ({email_message_projection()})")[0])
    assert {
        field: classification_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_id")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_id": "thread-current",
    }
    for field, value in classification_before.items():
        if field not in {"folder", "uidvalidity", "uid", "thread_id", "updated_at"}:
            assert classification_after[field] == value
    assert {
        field: message_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_identity")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_identity": "thread-current",
    }
    for field, value in message_before.items():
        if field not in {
            "folder",
            "uidvalidity",
            "uid",
            "thread_identity",
            "updated_at",
        }:
            assert message_after[field] == value
    assert rescanned["category"] == classification_before["category"]
    assert rescanned["model_id"] == classification_before["model_id"]
    assert rescanned["confidence"] == classification_before["confidence"]
    assert rescanned["config_version"] == classification_before["config_version"]
    assert (
        rescanned["current_action_plan_id"]
        == classification_before["current_action_plan_id"]
    )
    assert [
        dict(row)
        for row in _fetchall(
            database,
            f"select * from ({EMAIL_PLAN_SQL}) order by action_plan_version",
        )
    ] == plans_before
    assert [
        dict(row)
        for row in _fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL}) order by action_id")
    ] == actions_before
    assert store.owner_labelled_examples()[0] == training_before
    assert len(_fetchall(database, f"select * from ({email_message_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({email_classification_projection()})")) == 1


def test_changed_plan_appends_next_version_and_preserves_history(tmp_path: Path):
    database = tmp_path / "history.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    first = _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE, EmailAction.UNSUBSCRIBE),
        action_parameters={},
    )

    corrected = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )
    replayed = store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )

    assert first["current_action_plan_id"] != corrected["current_action_plan_id"]
    assert replayed["current_action_plan_id"] == second_plan.action_plan_id
    assert corrected["confirmed_category"] == "notification"
    plans = _fetchall(
        database,
        f"select action_plan_id, action_plan_version from ({EMAIL_PLAN_SQL}) order by action_plan_version",
    )
    assert [(row["action_plan_id"], row["action_plan_version"]) for row in plans] == [
        (classification.action_plan.action_plan_id, 1),
        (second_plan.action_plan_id, 2),
    ]
    assert len(_fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL})")) == 2


def test_agent_correction_persists_its_durable_agent_result(tmp_path: Path):
    from app.email_classifier_agent import DurableAgentClassificationResult

    database = tmp_path / "agent-correction.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    created_at = classification.action_plan.created_at + timedelta(minutes=2)
    agent_result = DurableAgentClassificationResult(
        category="notification",
        important=True,
        certainty="certain",
        confidence=classification.action_plan.confidence,
        reason="A service notification requiring attention.",
        unsubscribe_candidate_index=None,
        unsubscribe_candidate_source=None,
        unsubscribe_candidate_digest=None,
        unsubscribe_candidate_reference=None,
    )
    plan = EmailActionPlan.model_validate(
        {
            "action_plan_id": _action_plan_identity(
                action_plan_version=2,
                classification_id=classification.classification_id,
                account_id=classification.action_plan.account_id,
                category=EmailCategory.NOTIFICATION,
                classification_source="agent",
                confidence=classification.action_plan.confidence,
                model_id="email-classifier-agent:v1",
                config_version="email-agent-correction-v1",
                actions=(EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
                action_parameters={
                    EmailAction.MOVE: {"target_folder": "通知"},
                },
                created_at=created_at,
            ),
            "action_plan_version": 2,
            "classification_id": classification.classification_id,
            "account_id": classification.action_plan.account_id,
            "category": EmailCategory.NOTIFICATION,
            "classification_source": "agent",
            "confidence": classification.action_plan.confidence,
            "model_id": "email-classifier-agent:v1",
            "config_version": "email-agent-correction-v1",
            "actions": (EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
            "action_parameters": {
                EmailAction.MOVE: {"target_folder": "通知"},
            },
            "created_at": created_at,
        }
    )

    corrected = store.append_action_plan_version(
        classification.classification_id,
        plan,
        confirmed_category=EmailCategory.NOTIFICATION,
        agent_result=agent_result,
    )

    assert corrected["classification_source"] == "agent"
    assert corrected["agent_result"] == agent_result.model_dump(mode="json")
    reopened = EmailStore(database)
    restored = reopened.get_classification(classification.classification_id)
    assert restored is not None
    assert restored["classification_source"] == "agent"
    assert restored["agent_result"] == agent_result.model_dump(mode="json")
    assert restored["action_plan"] == corrected["action_plan"]
    training_records = store.list_selected_training_records()
    assert len(training_records) == 1
    training_input = json.loads(training_records[0]["normalized_model_input"])
    assert training_records[0]["source"] == "agent_auto_label"
    # A notification is filed and skimmed, so it is never important evidence
    # even when the Agent flagged it.
    assert training_records[0]["important"] is False
    assert training_input["input_schema_version"] == MODEL_INPUT_SCHEMA_VERSION
    assert training_input["subject"] == "Need a decision"
    assert training_input["body"] == "__subject__need a decision"
    assert training_input["attachments"] == [
        {
            "content_id": "",
            "disposition": "",
            "filename": "brief.pdf",
            "inline": False,
            "mime_type": "application/pdf",
            "size_bytes": 1024,
        }
    ]


def test_human_correction_clears_superseded_agent_result(tmp_path: Path):
    """A later manual decision must not be presented as an Agent decision."""

    from app.email_classifier_agent import DurableAgentClassificationResult

    database = tmp_path / "human-correction-clears-agent-result.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    agent_result = DurableAgentClassificationResult(
        category="notification",
        important=False,
        certainty="certain",
        confidence=classification.action_plan.confidence,
        reason="This is a service notification.",
        unsubscribe_candidate_index=None,
        unsubscribe_candidate_source=None,
        unsubscribe_candidate_digest=None,
        unsubscribe_candidate_reference=None,
    )
    agent_plan = EmailActionPlan.model_validate(
        {
            "action_plan_id": _action_plan_identity(
                action_plan_version=2,
                classification_id=classification.classification_id,
                account_id=classification.action_plan.account_id,
                category=EmailCategory.NOTIFICATION,
                classification_source="agent",
                confidence=classification.action_plan.confidence,
                model_id="email-classifier-agent:v1",
                config_version="email-agent-correction-v1",
                actions=(EmailAction.MOVE,),
                action_parameters={EmailAction.MOVE: {"target_folder": "通知"}},
                created_at=classification.action_plan.created_at + timedelta(minutes=2),
            ),
            "action_plan_version": 2,
            "classification_id": classification.classification_id,
            "account_id": classification.action_plan.account_id,
            "category": EmailCategory.NOTIFICATION,
            "classification_source": "agent",
            "confidence": classification.action_plan.confidence,
            "model_id": "email-classifier-agent:v1",
            "config_version": "email-agent-correction-v1",
            "actions": (EmailAction.MOVE,),
            "action_parameters": {EmailAction.MOVE: {"target_folder": "通知"}},
            "created_at": classification.action_plan.created_at + timedelta(minutes=2),
        }
    )
    agent_current = store.append_action_plan_version(
        classification.classification_id,
        agent_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
        agent_result=agent_result,
    )

    application = store.apply_human_classification(
        classification.classification_id,
        EmailCategory.WORK,
        feedback_request_id="manual-supersedes-agent",
        expected_current_action_plan_id=agent_current["current_action_plan_id"],
        created_at=classification.action_plan.created_at + timedelta(minutes=3),
    )

    assert application is not None
    updated = store.get_classification(classification.classification_id)
    assert updated is not None
    assert updated["classification_source"] == "user"
    assert updated["agent_result"] is None
    reopened = EmailStore(database)
    restored = reopened.get_classification(classification.classification_id)
    assert restored is not None
    assert restored["agent_result"] is None


def test_agent_correction_rejects_missing_durable_agent_result(tmp_path: Path):
    store = EmailStore(tmp_path / "agent-correction-missing-result.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    plan = EmailActionPlan.model_validate(
        {
            "action_plan_id": _action_plan_identity(
                action_plan_version=2,
                classification_id=classification.classification_id,
                account_id=classification.action_plan.account_id,
                category=EmailCategory.NOTIFICATION,
                classification_source="agent",
                confidence=classification.action_plan.confidence,
                model_id="email-classifier-agent:v1",
                config_version="email-agent-correction-v1",
                actions=(EmailAction.MOVE,),
                action_parameters={EmailAction.MOVE: {"target_folder": "通知"}},
                created_at=classification.action_plan.created_at + timedelta(minutes=2),
            ),
            "action_plan_version": 2,
            "classification_id": classification.classification_id,
            "account_id": classification.action_plan.account_id,
            "category": EmailCategory.NOTIFICATION,
            "classification_source": "agent",
            "confidence": classification.action_plan.confidence,
            "model_id": "email-classifier-agent:v1",
            "config_version": "email-agent-correction-v1",
            "actions": (EmailAction.MOVE,),
            "action_parameters": {EmailAction.MOVE: {"target_folder": "通知"}},
            "created_at": classification.action_plan.created_at + timedelta(minutes=2),
        }
    )

    with pytest.raises(ValueError, match="Agent correction requires"):
        store.append_action_plan_version(
            classification.classification_id,
            plan,
            confirmed_category=EmailCategory.NOTIFICATION,
        )


@pytest.mark.parametrize("invalid_category", ("subscription", " Work", 123))
def test_append_action_plan_version_rejects_invalid_correction_category(
    tmp_path: Path,
    invalid_category: object,
):
    store = EmailStore(tmp_path / "invalid-correction-category.sqlite3")
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    corrected = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )

    with pytest.raises(ValueError):
        store.append_action_plan_version(
            classification.classification_id,
            corrected,
            confirmed_category=invalid_category,  # type: ignore[arg-type]
        )


def test_action_plan_same_id_with_changed_fields_conflicts(tmp_path: Path):
    database = tmp_path / "plan-immutable-id.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    conflicting = classification.action_plan.model_copy(update={"config_version": "changed"})

    with pytest.raises(EmailActionPlanConflict, match="different immutable fields"):
        store.append_action_plan_version(
            classification.classification_id, conflicting,
            confirmed_category=conflicting.category,
        )
    rows = _fetchall(database, f"select action_plan_id, action_plan_version from ({EMAIL_PLAN_SQL})")
    assert [(row["action_plan_id"], row["action_plan_version"]) for row in rows] == [
        (classification.action_plan.action_plan_id, 1),
    ]


def test_action_plan_version_gap_conflicts(tmp_path: Path):
    database = tmp_path / "plan-version-gap.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    gap = _versioned_plan(
        classification.action_plan, version=3, category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,), action_parameters={},
    )

    with pytest.raises(EmailActionPlanConflict, match="version must be 2"):
        store.append_action_plan_version(
            classification.classification_id, gap, confirmed_category=EmailCategory.NOTIFICATION,
        )
    rows = _fetchall(database, f"select action_plan_version from ({EMAIL_PLAN_SQL})")
    assert [row["action_plan_version"] for row in rows] == [1]


def test_changed_snapshot_cannot_reuse_an_existing_plan_version(tmp_path: Path):
    database = tmp_path / "plan-conflict.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    conflicting = _versioned_plan(
        classification.action_plan,
        version=1,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )

    with pytest.raises(EmailActionPlanConflict, match="version"):
        store.append_action_plan_version(
            classification.classification_id,
            conflicting,
            confirmed_category=EmailCategory.NOTIFICATION,
        )


def test_rescan_updates_locator_without_replacing_final_decision_or_plan(
    tmp_path: Path,
):
    database = tmp_path / "move.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="fallback",
        stable_message_identity="dingtalk-account:imap:INBOX:42:7",
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id=None,
    )
    first = _persist_scan(store, original, cursor_last_seen_uid=7)
    moved = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="fallback-moved",
        confidence=0.51,
        model_id="email/logistic/model-2",
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=None,
    )

    rescanned = _persist_scan(
        store,
        moved,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
    )

    assert rescanned["folder"] == "Archive"
    assert rescanned["uidvalidity"] == 84
    assert rescanned["uid"] == 9
    assert rescanned["status"] == "processed"
    assert rescanned["model_id"] == first["model_id"]
    assert rescanned["current_action_plan_id"] == first["current_action_plan_id"]
    assert len(_fetchall(database, f"select * from ({email_message_projection()})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_PLAN_SQL})")) == 1
    assert len(_fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL})")) == 1


def test_rescan_only_updates_mutable_locator_and_preserves_business_snapshot(
    tmp_path: Path,
):
    database = tmp_path / "immutable-message-snapshot.sqlite3"
    store = EmailStore(database)
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="Requires attention",
        threshold=0.97,
        actions=(EmailAction.LABEL,),
        action_parameters={EmailAction.LABEL: {"labels": ["important"]}},
        enabled=True,
        config_version="important-v1",
    )
    original = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="immutable-snapshot",
        thread_id="thread-original",
    )
    store.persist_scan_result(
        original,
        sender="original-sender@example.com",
        recipients=("original-to@example.com", "original-cc@example.com"),
        subject="Original subject",
        normalized_text="__subject__original normalized text",
        preview="Original preview",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="original.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                inline=False,
            ),
        ),
        received_at="2026-08-29T15:59:00+00:00",
        model_text="__subject__original normalized text",
        cursor_uidvalidity=42,
        cursor_last_seen_uid=original.provider_locator.uid,
        cursor_last_success_at="2026-08-29T16:00:00+00:00",
    )
    confirmed = _confirm(
        store,
        original.classification_id,
        EmailCategory.NOTIFICATION,
    )
    assert confirmed is not None

    message_before = dict(_fetchall(database, f"select * from ({email_message_projection()})")[0])
    classification_before = dict(
        _fetchall(database, f"select * from ({email_classification_projection()})")[0]
    )
    plans_before = [
        dict(row)
        for row in _fetchall(
            database,
            f"select * from ({EMAIL_PLAN_SQL}) order by action_plan_version",
        )
    ]
    actions_before = [
        dict(row)
        for row in _fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL}) order by action_id")
    ]
    training_before = store.owner_labelled_examples()[0]
    count_sources = {
        "email_messages": f"({email_message_projection()})",
        "email_classifications": f"({email_classification_projection()})",
        "email_action_plans": f"({EMAIL_PLAN_SQL})",
        "email_actions": f"({EMAIL_DIRECT_ACTION_SQL})",
    }
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {source}")[0]["count"]
        for table, source in count_sources.items()
    }

    moved = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="immutable-snapshot-rescan",
        confidence=0.21,
        model_id="email/logistic/model-rescan",
        category=EmailCategory.PERSONAL,
        classification_id=original.classification_id,
        stable_message_identity=original.stable_message_identity,
        folder="Archive",
        uidvalidity=84,
        uid=9,
        rfc_message_id=original.provider_locator.rfc_message_id,
        thread_id="thread-current",
    )
    rescanned = store.persist_scan_result(
        moved,
        sender="different-sender@example.net",
        recipients=("different-to@example.net",),
        subject="Different subject",
        normalized_text="__subject__different normalized text",
        preview="Different preview",
        attachment_metadata=(
            EmailAttachmentMetadata(
                filename="different.png",
                mime_type="image/png",
                size_bytes=9999,
                inline=True,
            ),
        ),
        received_at="2026-08-30T12:00:00+00:00",
        model_text="__subject__different normalized text",
        cursor_uidvalidity=84,
        cursor_last_seen_uid=9,
        cursor_last_success_at="2026-08-30T12:01:00+00:00",
    )

    message_after = dict(_fetchall(database, f"select * from ({email_message_projection()})")[0])
    classification_after = dict(
        _fetchall(database, f"select * from ({email_classification_projection()})")[0]
    )
    assert {
        field: message_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_identity")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_identity": "thread-current",
    }
    for field in (
        "id",
        "account_id",
        "stable_message_identity",
        "rfc_message_id",
        "sender",
        "recipients_json",
        "subject",
        "normalized_text",
        "preview",
        "attachment_metadata_json",
        "received_at",
        "created_at",
    ):
        assert message_after[field] == message_before[field]

    assert {
        field: classification_after[field]
        for field in ("folder", "uidvalidity", "uid", "thread_id")
    } == {
        "folder": "Archive",
        "uidvalidity": 84,
        "uid": 9,
        "thread_id": "thread-current",
    }
    for field, value in classification_before.items():
        if field not in {"folder", "uidvalidity", "uid", "thread_id", "updated_at"}:
            assert classification_after[field] == value
    assert rescanned["current_action_plan_id"] == confirmed["current_action_plan_id"]
    assert [
        dict(row)
        for row in _fetchall(
            database,
            f"select * from ({EMAIL_PLAN_SQL}) order by action_plan_version",
        )
    ] == plans_before
    assert [
        dict(row)
        for row in _fetchall(database, f"select * from ({EMAIL_DIRECT_ACTION_SQL}) order by action_id")
    ] == actions_before
    assert store.owner_labelled_examples()[0] == training_before
    assert {
        table: _fetchall(database, f"select count(*) as count from {source}")[0]["count"]
        for table, source in count_sources.items()
    } == counts_before


def test_classification_id_collision_fails_closed_with_domain_error(tmp_path: Path):
    database = tmp_path / "collision.sqlite3"
    store = EmailStore(database)
    first = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="first",
        classification_id=12345,
    )
    second = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="second",
        classification_id=12345,
    )
    _persist_scan(store, first)

    with pytest.raises(EmailClassificationIdentityCollision, match="12345"):
        _persist_scan(store, second)

    rows = _fetchall(
        database, f"select stable_message_identity from ({email_classification_projection()})"
    )
    assert [row["stable_message_identity"] for row in rows] == [
        first.stable_message_identity
    ]


def test_cursor_creation_and_same_generation_advancement_are_monotonic(
    tmp_path: Path,
):
    database = tmp_path / "cursor.sqlite3"
    store = EmailStore(database)
    first = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, uid=9)
    _persist_scan(store, first, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    earlier = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="earlier-same-generation",
        uid=5,
    )
    _persist_scan(store, earlier, cursor_uidvalidity=42, cursor_last_seen_uid=5)

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 42
    assert cursor["last_seen_uid"] == 9


def test_empty_scan_cursor_initialization_and_reset_use_compare_and_set(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "empty-cursor.sqlite3")

    store.persist_empty_scan_cursor(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=42,
        last_success_at="2026-08-30T00:00:00+00:00",
    )
    assert store.get_scan_cursor("dingtalk-account", "INBOX")["last_seen_uid"] == 0

    store.persist_empty_scan_cursor(
        account_id="dingtalk-account",
        folder="INBOX",
        uidvalidity=84,
        last_success_at="2026-08-30T00:01:00+00:00",
        expected_cursor_uidvalidity=42,
    )
    reset = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert reset["uidvalidity"] == 84
    assert reset["last_seen_uid"] == 0

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        store.persist_empty_scan_cursor(
            account_id="dingtalk-account",
            folder="INBOX",
            uidvalidity=126,
            last_success_at="2026-08-30T00:02:00+00:00",
            expected_cursor_uidvalidity=42,
        )
    unchanged = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert unchanged["uidvalidity"] == 84
    assert unchanged["last_seen_uid"] == 0


def test_cursor_generation_reset_requires_compare_and_set(tmp_path: Path):
    database = tmp_path / "cursor-reset.sqlite3"
    store = EmailStore(database)
    first = _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, uid=9)
    _persist_scan(store, first, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="after-reset",
        uidvalidity=84,
        uid=2,
    )
    with pytest.raises(email_store_module.EmailCursorConflict, match="expected"):
        _persist_scan(store, reset, cursor_uidvalidity=84, cursor_last_seen_uid=2)

    _persist_scan(
        store,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2


def test_same_generation_update_rejects_stale_uidvalidity_expectation_atomically(
    tmp_path: Path,
):
    database = tmp_path / "same-generation-stale-expectation.sqlite3"
    store = EmailStore(database)
    initial = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="same-generation-initial",
        uid=9,
    )
    _persist_scan(store, initial, cursor_uidvalidity=42, cursor_last_seen_uid=9)
    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="same-generation-reset",
        uidvalidity=84,
        uid=2,
    )
    _persist_scan(
        store,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )
    source_tables = {
        "email_messages": f"({email_message_projection()})",
        "email_classifications": f"({email_classification_projection()})",
        "email_action_plans": f"({EMAIL_PLAN_SQL})",
        "email_actions": f"({EMAIL_DIRECT_ACTION_SQL})",
    }
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {source_tables[table]}")[0]["count"]
        for table in (
            "email_messages",
            "email_classifications",
            "email_action_plans",
            "email_actions",
        )
    }
    stale = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="same-generation-stale",
        uidvalidity=84,
        uid=10,
    )

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        _persist_scan(
            store,
            stale,
            cursor_uidvalidity=84,
            cursor_last_seen_uid=10,
            expected_cursor_uidvalidity=42,
        )

    cursor = store.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2
    assert {
        table: _fetchall(database, f"select count(*) as count from {source_tables[table]}")[0]["count"]
        for table in counts_before
    } == counts_before
    assert not _fetchall(
        database,
        f"select id from ({email_classification_projection()}) where id=?",
        (stale.classification_id,),
    )


def test_cursor_expectation_requires_cursor_progress(tmp_path: Path):
    store = EmailStore(tmp_path / "cursor-expectation.sqlite3")

    with pytest.raises(ValueError, match="expected cursor generation requires"):
        store.persist_scan_result(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
            expected_cursor_uidvalidity=42,
        )


def test_stale_cursor_generation_commit_rolls_back_all_scan_state(tmp_path: Path):
    database = tmp_path / "stale-cursor.sqlite3"
    store_a = EmailStore(database)
    store_b = EmailStore(database)
    initial = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="cursor-initial",
        uid=9,
    )
    _persist_scan(store_a, initial, cursor_uidvalidity=42, cursor_last_seen_uid=9)

    reset = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        message_id="cursor-reset",
        uidvalidity=84,
        uid=2,
    )
    _persist_scan(
        store_a,
        reset,
        cursor_uidvalidity=84,
        cursor_last_seen_uid=2,
        expected_cursor_uidvalidity=42,
    )
    source_tables = {
        "email_messages": f"({email_message_projection()})",
        "email_classifications": f"({email_classification_projection()})",
        "email_action_plans": f"({EMAIL_PLAN_SQL})",
        "email_actions": f"({EMAIL_DIRECT_ACTION_SQL})",
    }
    counts_before = {
        table: _fetchall(database, f"select count(*) as count from {source_tables[table]}")[0]["count"]
        for table in (
            "email_messages",
            "email_classifications",
            "email_action_plans",
            "email_actions",
        )
    }
    stale = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="stale-generation",
        uidvalidity=42,
        uid=10,
    )

    with pytest.raises(email_store_module.EmailCursorConflict, match="expected 42"):
        _persist_scan(
            store_b,
            stale,
            cursor_uidvalidity=42,
            cursor_last_seen_uid=10,
            expected_cursor_uidvalidity=42,
        )

    cursor = store_b.get_scan_cursor("dingtalk-account", "INBOX")
    assert cursor is not None
    assert cursor["uidvalidity"] == 84
    assert cursor["last_seen_uid"] == 2
    assert {
        table: _fetchall(database, f"select count(*) as count from {source_tables[table]}")[0]["count"]
        for table in counts_before
    } == counts_before
    assert not _fetchall(
        database,
        f"select id from ({email_classification_projection()}) where id=?",
        (stale.classification_id,),
    )


def test_direct_action_probe_is_not_a_reservation(tmp_path, monkeypatch):
    database = tmp_path / "probe-is-not-reservation.sqlite3"
    store = EmailStore(database)
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    other = EmailStore(database)
    select = store._next_direct_action_row
    probes = []

    def claim_between_probe_and_transaction(db, claimed_at, account_ids):
        row = select(db, claimed_at, account_ids)
        probes.append(db.in_transaction)
        if len(probes) == 1:
            assert other.claim_next_direct_action(claimed_at=claimed_at) is not None
        return row

    monkeypatch.setattr(store, "_next_direct_action_row", claim_between_probe_and_transaction)
    assert store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00") is None
    assert probes == [False, True]
    rows = _fetchall(database, f"select status from ({EMAIL_DIRECT_ACTION_SQL})")
    assert [row["status"] for row in rows] == ["processing"]


def test_claim_direct_action_uses_current_immutable_plan_and_stable_locator(
    tmp_path: Path,
):
    database = tmp_path / "claim-current-plan.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        stable_message_identity="dingtalk-account:imap:INBOX:42:7",
        uid=7,
    )
    _persist_scan(store, original)
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="Move important mail",
        threshold=0.9,
        actions=(EmailAction.MOVE,),
        action_parameters={
            EmailAction.MOVE: {"target_folder": "Important"},
        },
        enabled=True,
        config_version="important-v2",
    )
    assert original.action_plan is not None
    application = store.apply_human_classification(
        original.classification_id,
        EmailCategory.NOTIFICATION,
        feedback_request_id="feedback-current-plan",
        expected_current_action_plan_id=original.action_plan.action_plan_id,
        created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
    )
    assert application is not None

    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")

    assert claimed is not None
    assert claimed.action_plan_id == application.resulting_action_plan_id
    assert claimed.action_type is EmailAction.MOVE
    assert dict(claimed.parameters) == {"target_folder": "Important"}
    assert claimed.config_version == "important-v2"
    assert claimed.locator.stable_message_identity == (
        "dingtalk-account:imap:INBOX:42:7"
    )
    assert claimed.locator.folder == "INBOX"
    assert claimed.attempt_number == 1
    with pytest.raises(TypeError):
        claimed.parameters["target_folder"] = "Mutated"

    old_action = _fetchall(
        database,
        f"select * from ({DIRECT_EMAIL_ACTION_SQL}) where action_plan_id=?",
        (original.action_plan.action_plan_id,),
    )[0]
    assert old_action["status"] == "pending"


def _correct_to_move_plan(
    store: EmailStore,
    original: EmailClassification,
    *,
    request_id: str,
):
    assert original.action_plan is not None
    store.upsert_config(
        category=EmailCategory.NOTIFICATION,
        description="Move important mail",
        threshold=0.9,
        actions=(EmailAction.MOVE,),
        action_parameters={
            EmailAction.MOVE: {"target_folder": "Important"},
        },
        enabled=True,
        config_version="important-v2",
    )
    application = store.apply_human_classification(
        original.classification_id,
        EmailCategory.NOTIFICATION,
        feedback_request_id=request_id,
        expected_current_action_plan_id=original.action_plan.action_plan_id,
        created_at=datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc),
    )
    assert application is not None
    return application


@pytest.mark.parametrize("resolution", ("complete", "recover"))
def test_processing_direct_action_blocks_plan_switch_until_closed(
    tmp_path: Path,
    resolution: str,
):
    database = tmp_path / f"historical-processing-{resolution}.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id=f"historical-processing-{resolution}",
    )
    _persist_scan(store, original)
    historical = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert historical is not None
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        _correct_to_move_plan(
            store,
            original,
            request_id=f"feedback-historical-processing-{resolution}",
        )

    if resolution == "complete":
        store.complete_direct_action_attempt(
            historical,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=historical.locator.stable_message_identity,
            provider_result_id="revision-v1",
            error="",
            finished_at="2026-08-30T12:03:00+00:00",
        )
    else:
        assert (
            store.recover_stale_processing_actions(
                stale_before="2026-08-30T12:00:01+00:00",
                recovered_at="2026-08-30T12:03:00+00:00",
            )
            == 1
        )

    application = _correct_to_move_plan(
        store,
        original,
        request_id=f"feedback-historical-processing-{resolution}",
    )
    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:04:00+00:00")
    assert current is not None
    assert current.action_plan_id == application.resulting_action_plan_id
    assert current.action_type is EmailAction.MOVE


@pytest.mark.parametrize("historical_status", ("pending", "failed"))
def test_historical_non_processing_action_does_not_block_current_plan(
    tmp_path: Path,
    historical_status: str,
):
    database = tmp_path / f"historical-{historical_status}.sqlite3"
    store = EmailStore(database)
    original = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id=f"historical-{historical_status}",
    )
    _persist_scan(store, original)
    if historical_status == "failed":
        historical = store.claim_next_direct_action(
            claimed_at="2026-08-30T12:00:00+00:00"
        )
        assert historical is not None
        store.complete_direct_action_attempt(
            historical,
            status="failed",
            provider_operation="STORE LABELS",
            provider_target=historical.locator.stable_message_identity,
            provider_result_id="",
            error="provider_readback_mismatch",
            finished_at="2026-08-30T12:00:01+00:00",
        )
    application = _correct_to_move_plan(
        store,
        original,
        request_id=f"feedback-historical-{historical_status}",
    )

    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")

    assert current is not None
    assert current.action_plan_id == application.resulting_action_plan_id
    assert current.action_type is EmailAction.MOVE


def test_processing_direct_action_plan_fence_is_scoped_to_its_classification(
    tmp_path: Path,
):
    database = tmp_path / "processing-plan-fence-scope.sqlite3"
    store = EmailStore(database)
    blocked = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="blocked-historical-processing",
    )
    available = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="available-current-plan",
    )
    _persist_scan(store, blocked)
    _persist_scan(store, available)
    processing = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert processing is not None
    correction_target = (
        available
        if processing.classification_id == blocked.classification_id
        else blocked
    )
    application = _correct_to_move_plan(
        store,
        correction_target,
        request_id="feedback-available-while-other-processing",
    )

    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")

    assert claimed is not None
    assert claimed.classification_id == correction_target.classification_id
    assert claimed.action_plan_id == application.resulting_action_plan_id
    assert claimed.action_type is EmailAction.MOVE


def test_concurrent_direct_action_claim_has_one_winner(tmp_path: Path):
    database = tmp_path / "concurrent-action-claim.sqlite3"
    seed = EmailStore(database)
    _persist_scan(
        seed,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    stores = (EmailStore(database), EmailStore(database))
    barrier = Barrier(2)

    def claim(store: EmailStore):
        barrier.wait()
        return store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, stores))

    assert sum(result is not None for result in results) == 1
    current = _fetchall(database, f"select * from ({DIRECT_EMAIL_ACTION_SQL})")[0]
    assert current["status"] == "processing"
    assert current["attempt_count"] == 0


def test_processing_action_blocks_concurrent_sibling_claims(tmp_path: Path):
    database = tmp_path / "processing-blocks-siblings.sqlite3"
    seed = EmailStore(database)
    _persist_scan(
        seed,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.LABEL,
                EmailAction.MARK_READ,
                EmailAction.ARCHIVE,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
            },
        ),
    )
    first = seed.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert first is not None
    assert first.action_type is EmailAction.LABEL
    stores = (EmailStore(database), EmailStore(database))
    barrier = Barrier(2)

    def claim(store: EmailStore):
        barrier.wait()
        return store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, stores))

    assert results == [None, None]
    statuses = _fetchall(
        database,
        f"select action_type, status from ({EMAIL_DIRECT_ACTION_SQL}) order by action_type",
    )
    assert {row["action_type"]: row["status"] for row in statuses} == {
        "archive": "pending",
        "label": "processing",
        "mark_read": "pending",
    }


@pytest.mark.parametrize(
    ("destination", "destination_parameters"),
    (
        (EmailAction.ARCHIVE, {}),
        (EmailAction.MOVE, {"target_folder": "Archive/Work"}),
        (EmailAction.TRASH, {}),
    ),
)
def test_failed_higher_priority_actions_retry_before_destination(
    tmp_path: Path,
    destination: EmailAction,
    destination_parameters: dict[str, object],
):
    database = tmp_path / "failed-action-dependency.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.LABEL,
                EmailAction.MARK_READ,
                destination,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
                destination: destination_parameters,
            },
        ),
    )

    claimed_types: list[EmailAction] = []

    label_one = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert label_one is not None
    claimed_types.append(label_one.action_type)
    store.complete_direct_action_attempt(
        label_one,
        status="failed",
        provider_operation="STORE LABELS",
        provider_target=label_one.locator.stable_message_identity,
        provider_result_id="",
        error="provider_readback_mismatch",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    label_two = store.claim_next_direct_action(claimed_at="2026-08-30T12:01:00+00:00")
    assert label_two is not None
    claimed_types.append(label_two.action_type)
    store.complete_direct_action_attempt(
        label_two,
        status="done",
        provider_operation="readback_noop",
        provider_target=label_two.locator.stable_message_identity,
        provider_result_id="revision-label",
        error="",
        finished_at="2026-08-30T12:01:01+00:00",
    )

    read_one = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")
    assert read_one is not None
    claimed_types.append(read_one.action_type)
    store.complete_direct_action_attempt(
        read_one,
        status="failed",
        provider_operation="STORE \\Seen",
        provider_target=read_one.locator.stable_message_identity,
        provider_result_id="",
        error="provider_readback_mismatch",
        finished_at="2026-08-30T12:02:01+00:00",
    )

    read_two = store.claim_next_direct_action(claimed_at="2026-08-30T12:03:00+00:00")
    assert read_two is not None
    claimed_types.append(read_two.action_type)
    store.complete_direct_action_attempt(
        read_two,
        status="done",
        provider_operation="readback_noop",
        provider_target=read_two.locator.stable_message_identity,
        provider_result_id="revision-read",
        error="",
        finished_at="2026-08-30T12:03:01+00:00",
    )

    destination_claim = store.claim_next_direct_action(
        claimed_at="2026-08-30T12:04:00+00:00"
    )
    assert destination_claim is not None
    claimed_types.append(destination_claim.action_type)

    assert claimed_types == [
        EmailAction.LABEL,
        EmailAction.LABEL,
        EmailAction.MARK_READ,
        EmailAction.MARK_READ,
        destination,
    ]


def test_destination_action_is_claimed_after_locator_preserving_actions(
    tmp_path: Path,
):
    database = tmp_path / "destination-action-last.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.ARCHIVE,
                EmailAction.MARK_READ,
                EmailAction.LABEL,
            ),
            action_parameters={
                EmailAction.LABEL: {"labels": ["work"]},
            },
            stable_message_identity="dingtalk-account:imap:INBOX:42:7",
            uid=7,
        ),
    )

    claimed_types: list[EmailAction] = []
    for attempt_index in range(3):
        claimed = store.claim_next_direct_action(
            claimed_at=f"2026-08-30T12:0{attempt_index}:00+00:00"
        )
        assert claimed is not None
        claimed_types.append(claimed.action_type)
        store.complete_direct_action_attempt(
            claimed,
            status="done",
            provider_operation="readback_noop",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id=f"revision-{attempt_index}",
            error="",
            finished_at=f"2026-08-30T12:0{attempt_index}:01+00:00",
        )

    assert claimed_types == [
        EmailAction.LABEL,
        EmailAction.MARK_READ,
        EmailAction.ARCHIVE,
    ]


def test_move_then_important_persists_locator_and_retries_only_flag(tmp_path: Path):
    database = tmp_path / "move-then-important.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.FLAG_IMPORTANT, EmailAction.MOVE),
            action_parameters={
                EmailAction.MOVE: {"target_folder": "Legal"},
            },
            stable_message_identity="dingtalk-account:imap:INBOX:42:7",
            uid=7,
        ),
    )

    move = store.claim_next_direct_action(claimed_at="2026-09-07T12:00:00+00:00")
    assert move is not None
    assert move.action_type is EmailAction.MOVE
    moved_locator = type(move.locator)(
        account_id=move.account_id,
        folder="Legal",
        uidvalidity=84,
        uid=19,
        rfc_message_id=move.locator.rfc_message_id,
        thread_id=move.locator.thread_id,
        stable_message_identity=move.locator.stable_message_identity,
    )
    store.complete_direct_action_attempt(
        move,
        status="done",
        provider_operation="MOVE",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="move-revision",
        error="",
        finished_at="2026-09-07T12:00:01+00:00",
        updated_locator=moved_locator,
    )

    flag = store.claim_next_direct_action(claimed_at="2026-09-07T12:01:00+00:00")
    assert flag is not None
    assert flag.action_type is EmailAction.FLAG_IMPORTANT
    assert flag.locator == moved_locator
    store.complete_direct_action_attempt(
        flag,
        status="failed",
        provider_operation="STORE IMPORTANT",
        provider_target=flag.locator.stable_message_identity,
        provider_result_id="",
        error="provider_apply_failed:TimeoutError",
        finished_at="2026-09-07T12:01:01+00:00",
    )

    retry = store.claim_next_direct_action(claimed_at="2026-09-07T12:02:00+00:00")
    assert retry is not None
    assert retry.action_id == flag.action_id
    assert retry.action_type is EmailAction.FLAG_IMPORTANT
    assert retry.locator == moved_locator
    assert retry.attempt_number == 2
    assert len(store.list_action_attempts(move.action_id)) == 1


def test_explicit_historical_move_dependencies_gate_exact_and_global_claims(
    tmp_path: Path,
):
    database = tmp_path / "historical-move-dependencies.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(
                EmailAction.MOVE,
                EmailAction.FLAG_IMPORTANT,
                EmailAction.MARK_READ,
            ),
            action_parameters={
                EmailAction.MOVE: {"target_folder": "Legal"},
                EmailAction.FLAG_IMPORTANT: {"depends_on": ["move"]},
                EmailAction.MARK_READ: {"depends_on": ["flag_important"]},
            },
        ),
    )
    classification = store.get_classification_by_stable_identity(
        "dingtalk-account:message-id:<msg-1@example.com>"
    )
    assert classification is not None
    action_ids = {
        action["action_type"]: action["action_id"]
        for action in _fetchall(
            database,
            f"select action_type, action_id from ({EMAIL_DIRECT_ACTION_SQL}) "
            "where classification_id=?",
            (classification["id"],),
        )
    }

    assert (
        store.claim_direct_action(
            action_id=action_ids["mark_read"],
            claimed_at="2026-09-07T12:00:00+00:00",
        )
        is None
    )
    assert (
        store.claim_direct_action(
            action_id=action_ids["flag_important"],
            claimed_at="2026-09-07T12:00:00+00:00",
        )
        is None
    )
    move = store.claim_next_direct_action(claimed_at="2026-09-07T12:00:00+00:00")
    assert move is not None
    assert move.action_type is EmailAction.MOVE
    moved_locator = type(move.locator)(
        account_id=move.account_id,
        folder="Legal",
        uidvalidity=84,
        uid=119,
        rfc_message_id=move.locator.rfc_message_id,
        thread_id=move.locator.thread_id,
        stable_message_identity=move.locator.stable_message_identity,
    )
    store.complete_direct_action_attempt(
        move,
        status="done",
        provider_operation="MOVE",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="move-receipt",
        error="",
        finished_at="2026-09-07T12:00:01+00:00",
        updated_locator=moved_locator,
    )

    flag = store.claim_next_direct_action(claimed_at="2026-09-07T12:01:00+00:00")
    assert flag is not None
    assert flag.action_type is EmailAction.FLAG_IMPORTANT
    assert flag.locator == moved_locator
    assert flag.parameters == {}
    store.complete_direct_action_attempt(
        flag,
        status="done",
        provider_operation="STORE IMPORTANT",
        provider_target=flag.locator.stable_message_identity,
        provider_result_id="flag-receipt",
        error="",
        finished_at="2026-09-07T12:01:01+00:00",
    )

    mark_read = store.claim_direct_action(
        action_id=action_ids["mark_read"],
        claimed_at="2026-09-07T12:02:00+00:00",
    )
    assert mark_read is not None
    assert mark_read.action_type is EmailAction.MARK_READ
    assert mark_read.locator == moved_locator
    assert mark_read.parameters == {}


def test_explicit_historical_trash_dependency_gates_exact_and_global_claims(
    tmp_path: Path,
):
    database = tmp_path / "historical-trash-dependencies.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.TRASH, EmailAction.MARK_READ),
            action_parameters={
                EmailAction.MARK_READ: {"depends_on": ["trash"]},
            },
        ),
    )
    classification = store.get_classification_by_stable_identity(
        "dingtalk-account:message-id:<msg-1@example.com>"
    )
    assert classification is not None
    action_ids = {
        action["action_type"]: action["action_id"]
        for action in _fetchall(
            database,
            f"select action_type, action_id from ({EMAIL_DIRECT_ACTION_SQL}) "
            "where classification_id=?",
            (classification["id"],),
        )
    }

    assert (
        store.claim_direct_action(
            action_id=action_ids["mark_read"],
            claimed_at="2026-09-07T12:00:00+00:00",
        )
        is None
    )
    trash = store.claim_next_direct_action(
        claimed_at="2026-09-07T12:00:00+00:00"
    )
    assert trash is not None
    assert trash.action_type is EmailAction.TRASH
    trashed_locator = type(trash.locator)(
        account_id=trash.account_id,
        folder="Trash",
        uidvalidity=84,
        uid=120,
        rfc_message_id=trash.locator.rfc_message_id,
        thread_id=trash.locator.thread_id,
        stable_message_identity=trash.locator.stable_message_identity,
    )
    store.complete_direct_action_attempt(
        trash,
        status="done",
        provider_operation="MOVE Trash",
        provider_target=trash.locator.stable_message_identity,
        provider_result_id="trash-receipt",
        error="",
        finished_at="2026-09-07T12:00:01+00:00",
        updated_locator=trashed_locator,
    )

    mark_read = store.claim_next_direct_action(
        claimed_at="2026-09-07T12:01:00+00:00"
    )
    assert mark_read is not None
    assert mark_read.action_type is EmailAction.MARK_READ
    assert mark_read.locator == trashed_locator
    assert mark_read.parameters == {}


def test_login_required_unsubscribe_receipt_releases_dependent_trash(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "login-required-unsubscribe-trash.sqlite3")
    store.create_account(
        {
            "account_id": "dingtalk-account",
            "display_name": "DingTalk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "derek@example.com",
            "smtp_secret_reference": "keychain://smtp-test",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        actions=(EmailAction.UNSUBSCRIBE, EmailAction.TRASH),
        action_parameters={},
        thread_id="thread-unsubscribe-trash",
    )
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    action_plan = classification.action_plan
    action_identity = email_action_identity(
        account_id=classification.provider_locator.account_id,
        stable_message_identity=classification.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=action_plan.action_plan_version,
    )
    authorization = {
        "action_identity": action_identity,
        "action_plan_id": action_plan.action_plan_id,
        "action_plan_version": action_plan.action_plan_version,
        "classification_id": classification.classification_id,
        "account_id": classification.provider_locator.account_id,
        "stable_message_identity": classification.stable_message_identity,
        "thread_identity": "thread-unsubscribe-trash",
        "entry_reference": "unsubscribe-entry:" + "c" * 64,
        "operations": [
            {
                "operation_reference": "step-1",
                "kind": "open_entry",
                "target_reference": "entry",
            },
        ],
    }
    authorization["effect_digest"] = email_unsubscribe_effect_digest(**authorization)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    store.persist_email_unsubscribe_terminal(
        **authorization,
        outcome="skipped_login_required",
        receipt_id="unsubscribe-receipt:login-required",
        evidence="login-required-page",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "skipped_login_required",
            "reference": "unsubscribe-receipt:login-required",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )

    trash = store.claim_next_direct_action(claimed_at="2026-09-07T12:00:00+00:00")

    assert trash is not None
    assert trash.action_type is EmailAction.TRASH


def test_retry_delayed_move_blocks_important_flag_claim(tmp_path: Path):
    store = EmailStore(tmp_path / "delayed-move-blocks-important.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
            action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
        ),
    )
    move = store.claim_next_direct_action(claimed_at="2026-09-07T12:00:00+00:00")
    assert move is not None
    assert move.action_type is EmailAction.MOVE
    store.complete_direct_action_attempt(
        move,
        status="failed",
        provider_operation="MOVE",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="",
        error="provider_apply_failed:TimeoutError",
        finished_at="2026-09-07T12:00:01+00:00",
    )

    assert (
        store.claim_next_direct_action(claimed_at="2026-09-07T12:00:02+00:00") is None
    )


def test_non_retryable_failed_move_blocks_important_flag_claim(tmp_path: Path):
    store = EmailStore(tmp_path / "permanent-move-blocks-important.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
            action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
        ),
    )
    move = store.claim_next_direct_action(claimed_at="2026-09-07T12:00:00+00:00")
    assert move is not None
    store.complete_direct_action_attempt(
        move,
        status="failed",
        provider_operation="MOVE",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="",
        error="provider_apply_failed:ImapDestinationUnavailable",
        finished_at="2026-09-07T12:00:01+00:00",
        retryable=False,
    )

    assert (
        store.claim_next_direct_action(claimed_at="2026-09-08T12:00:00+00:00") is None
    )


def test_exhausted_provider_factory_move_recovers_after_retry_ceiling(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "exhausted-provider-factory-move.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
            action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
        ),
    )
    claim_times = (
        "2026-09-07T12:00:00+00:00",
        "2026-09-07T12:01:00+00:00",
        "2026-09-07T12:03:00+00:00",
    )
    for attempt_number, claimed_at in enumerate(claim_times, start=1):
        move = store.claim_next_direct_action(claimed_at=claimed_at)
        assert move is not None
        assert move.action_type is EmailAction.MOVE
        assert move.attempt_number == attempt_number
        store.complete_direct_action_attempt(
            move,
            status="failed",
            provider_operation="provider_factory",
            provider_target=move.locator.stable_message_identity,
            provider_result_id="",
            error="provider_factory_failed:gaierror",
            finished_at=claimed_at,
        )

    retry = store.claim_next_direct_action(claimed_at="2026-09-08T12:00:00+00:00")

    assert retry is not None
    assert retry.action_type is EmailAction.MOVE
    assert retry.attempt_number == 4


def test_exhausted_missing_message_trash_recovers_after_retry_ceiling(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "exhausted-missing-message-trash.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.TRASH,),
            action_parameters={EmailAction.TRASH: {}},
        ),
    )
    claim_times = (
        "2026-09-07T12:00:00+00:00",
        "2026-09-07T12:01:00+00:00",
        "2026-09-07T12:03:00+00:00",
    )
    for attempt_number, claimed_at in enumerate(claim_times, start=1):
        trash = store.claim_next_direct_action(claimed_at=claimed_at)
        assert trash is not None
        assert trash.action_type is EmailAction.TRASH
        assert trash.attempt_number == attempt_number
        store.complete_direct_action_attempt(
            trash,
            status="failed",
            provider_operation="READ",
            provider_target=trash.locator.stable_message_identity,
            provider_result_id="",
            error="provider_read_failed:ImapMessageUnavailable",
            finished_at=claimed_at,
        )

    retry = store.claim_next_direct_action(claimed_at="2026-09-08T12:00:00+00:00")

    assert retry is not None
    assert retry.action_type is EmailAction.TRASH
    assert retry.attempt_number == 4


def test_exhausted_ambiguous_message_move_recovers_after_retry_ceiling(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "exhausted-ambiguous-message-move.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.MOVE,),
            action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
        ),
    )
    claim_times = (
        "2026-09-07T12:00:00+00:00",
        "2026-09-07T12:01:00+00:00",
        "2026-09-07T12:03:00+00:00",
    )
    for attempt_number, claimed_at in enumerate(claim_times, start=1):
        move = store.claim_next_direct_action(claimed_at=claimed_at)
        assert move is not None
        assert move.action_type is EmailAction.MOVE
        assert move.attempt_number == attempt_number
        store.complete_direct_action_attempt(
            move,
            status="failed",
            provider_operation="READ",
            provider_target=move.locator.stable_message_identity,
            provider_result_id="",
            error=(
                "provider_read_failed:imap.uid_lookup_ambiguous:"
                "message lookup was ambiguous"
            ),
            finished_at=claimed_at,
        )

    retry = store.claim_next_direct_action(claimed_at="2026-09-08T12:00:00+00:00")

    assert retry is not None
    assert retry.action_type is EmailAction.MOVE
    assert retry.attempt_number == 4


def test_complete_direct_action_appends_attempt_and_updates_current_atomically(
    tmp_path: Path,
):
    database = tmp_path / "complete-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None

    attempt = store.complete_direct_action_attempt(
        claimed,
        status="done",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="revision-1",
        error="",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    assert attempt["attempt_number"] == 1
    assert attempt["status"] == "done"
    current = _fetchall(
        database,
        f"select * from ({DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
        (claimed.action_id,),
    )[0]
    assert current["status"] == "done"
    assert current["attempt_count"] == 1
    assert current["provider_result_id"] == "revision-1"
    EmailStore(database)
    assert (
        store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00") is None
    )


def test_direct_email_worker_calls_provider_substitute_once_through_action_owner(
    tmp_path: Path,
):
    from app.email_provider_actions import (
        DeterministicEmailActionExecutor,
        ProviderMessageState,
    )
    from app.email_worker import _run_next_direct_action

    class ProviderSubstitute:
        def __init__(self):
            self.apply_calls = 0

        def read_state(self, locator, *, action_type):
            return ProviderMessageState(
                revision="before", labels=frozenset(), is_read=False,
                archived=False, folder=locator.folder, trashed=False,
            )

        def apply(self, locator, action_type, parameters, *, observed_state):
            self.apply_calls += 1
            return None

    database = tmp_path / "direct-provider-substitute.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(
        store,
        classification,
    )
    provider = ProviderSubstitute()
    result = _run_next_direct_action(
        store, lambda _account_id: DeterministicEmailActionExecutor(provider),
    )

    assert result is not None and result.status == "done"
    assert provider.apply_calls == 1
    assert classification.action_plan is not None
    action_id = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id
    )[0]
    current = _fetchall(
        database,
        f"select * from ({DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
        (action_id,),
    )[0]
    assert current["status"] == "done"
    assert current["attempt_count"] == 1
    assert len(store.list_action_attempts(action_id)) == 1


def test_direct_action_completion_rejects_superseded_plan(tmp_path: Path):
    database = tmp_path / "complete-superseded-plan.sqlite3"
    store = EmailStore(database)
    original = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, original)
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        db.execute("drop trigger trg_emails_direct_action_blocks_plan_switch")
        db.execute(
            "update emails set class_current_action_plan_id=null where classification_id=?",
            (claimed.classification_id,),
        )

    with pytest.raises(EmailActionAttemptConflict, match="no longer current"):
        store.complete_direct_action_attempt(
            claimed,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="revision-1",
            error="",
            finished_at="2026-08-30T12:00:01+00:00",
        )

    assert store.list_action_attempts(claimed.action_id) == []


def test_non_retryable_direct_action_is_not_reclaimed(tmp_path: Path):
    database = tmp_path / "non-retryable-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    store.complete_direct_action_attempt(
        claimed,
        status="failed",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="",
        error="provider_apply_failed:ImapPermanentFlagsUnsupported",
        finished_at="2026-08-30T12:00:01+00:00",
        retryable=False,
    )

    assert (
        store.claim_next_direct_action(claimed_at="2026-08-30T12:10:00+00:00") is None
    )


def test_processing_direct_action_blocks_account_rebinding_and_delete(tmp_path: Path):
    database = tmp_path / "processing-account-fence.sqlite3"
    store = EmailStore(database)
    store.create_account(
        {
            "account_id": "dingtalk-account",
            "display_name": "DingTalk",
            "email_address": "derek@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "derek@example.com",
            "imap_secret_reference": "keychain://imap-test",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    original = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, original)
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    current = store.get_account(claimed.account_id)
    assert current is not None

    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        store.update_account(
            claimed.account_id,
            {
                **current,
                "imap_host": "different.example.com",
                "imap_username": "different@example.com",
            },
        )
    with pytest.raises(sqlite3.IntegrityError, match="email_direct_action_in_flight"):
        store.delete_account_if_unchanged(
            claimed.account_id,
            expected_updated_at=str(current["updated_at"]),
        )


def test_locator_refresh_does_not_invalidate_stable_claim_identity(tmp_path: Path):
    database = tmp_path / "locator-refresh-during-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        # The locator UID is shared by the two selected Email aliases.
        db.execute(
            """
            update emails
            set message_folder='Archive', uidvalidity=43, uid=8
            where classification_id=?
            """,
            (claimed.classification_id,),
        )

    attempt = store.complete_direct_action_attempt(
        claimed,
        status="done",
        provider_operation="STORE LABELS",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="revision-1",
        error="",
        finished_at="2026-08-30T12:00:01+00:00",
    )

    assert attempt["status"] == "done"
    assert attempt["provider_target"] == claimed.locator.stable_message_identity


def test_direct_action_completion_rolls_back_attempt_and_current_state_together(
    tmp_path: Path,
):
    database = tmp_path / "complete-action-rollback.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create trigger reject_email_action_attempt
            before insert on action_receipts
            when new.attempt_number is not null
            begin
                select raise(abort, 'injected attempt failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected attempt failure"):
        store.complete_direct_action_attempt(
            claimed,
            status="failed",
            provider_operation="STORE LABELS",
            provider_target=claimed.locator.stable_message_identity,
            provider_result_id="",
            error="provider_apply_failed:TimeoutError",
            finished_at="2026-08-30T12:00:01+00:00",
        )
    current = _fetchall(
        database,
        f"select * from ({DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
        (claimed.action_id,),
    )[0]
    assert current["status"] == "processing"
    assert current["attempt_count"] == 0

    assert store.list_action_attempts(claimed.action_id) == []
    current = _fetchall(
        database,
        f"select * from ({DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
        (claimed.action_id,),
    )[0]
    assert current["status"] == "processing"
    assert current["attempt_count"] == 0
    assert current["started_at"] == claimed.claim_started_at


def test_stale_processing_recovery_records_failure_and_makes_action_claimable(
    tmp_path: Path,
):
    database = tmp_path / "recover-stale-action.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    claimed = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert claimed is not None

    assert (
        store.recover_stale_processing_actions(
            stale_before="2026-08-30T11:59:59+00:00",
            recovered_at="2026-08-30T12:01:00+00:00",
        )
        == 0
    )
    assert (
        store.recover_stale_processing_actions(
            stale_before="2026-08-30T12:00:01+00:00",
            recovered_at="2026-08-30T12:02:00+00:00",
        )
        == 1
    )

    attempts = store.list_action_attempts(claimed.action_id)
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [
        (1, "failed")
    ]
    assert attempts[0]["provider_operation"] == "startup_recovery"
    assert attempts[0]["provider_target"] == (claimed.locator.stable_message_identity)
    assert attempts[0]["error"] == "stale_processing_recovered"
    EmailStore(database)
    retried = store.claim_next_direct_action(claimed_at="2026-08-30T12:03:00+00:00")
    assert retried is not None
    assert retried.action_id == claimed.action_id
    assert retried.attempt_number == 2


def test_stale_claim_cannot_complete_after_recovery_and_retry(tmp_path: Path):
    database = tmp_path / "stale-claim-cas.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    stale = store.claim_next_direct_action(claimed_at="2026-08-30T12:00:00+00:00")
    assert stale is not None
    store.recover_stale_processing_actions(
        stale_before="2026-08-30T12:00:01+00:00",
        recovered_at="2026-08-30T12:01:00+00:00",
    )
    current = store.claim_next_direct_action(claimed_at="2026-08-30T12:02:00+00:00")
    assert current is not None

    with pytest.raises(EmailActionAttemptConflict, match="claim changed"):
        store.complete_direct_action_attempt(
            stale,
            status="done",
            provider_operation="STORE LABELS",
            provider_target=stale.locator.stable_message_identity,
            provider_result_id="revision-stale",
            error="",
            finished_at="2026-08-30T12:03:00+00:00",
        )

    assert len(store.list_action_attempts(stale.action_id)) == 1


def test_corrupt_stored_json_is_reported_as_domain_corruption(tmp_path: Path):
    database = tmp_path / "corrupt.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PENDING_FEEDBACK),
    )
    with sqlite3.connect(database) as db:
        db.execute("update emails set class_probabilities_json='not-json'")

    with pytest.raises(EmailPersistenceCorruption, match="probabilities_json"):
        EmailStore(database)


def test_corrupt_historical_action_plan_is_rejected_on_open(tmp_path: Path):
    database = tmp_path / "corrupt-plan.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    second_plan = _versioned_plan(
        classification.action_plan,
        version=2,
        category=EmailCategory.NOTIFICATION,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
    )
    store.append_action_plan_version(
        classification.classification_id,
        second_plan,
        confirmed_category=EmailCategory.NOTIFICATION,
    )
    with sqlite3.connect(database) as db:
        plan_rows = db.execute(
            f"select rowid from ({email_action_projection('email_action_plans', include_rowid=True)}) "
            "where action_plan_version=1"
        ).fetchall()
        for plan_row in plan_rows:
            update_email_action(db, "email_action_plans", plan_row[0], {
                "actions_json": '["not-an-email-action"]',
            })

    with pytest.raises(EmailPersistenceCorruption, match="ActionPlan"):
        EmailStore(database)


@pytest.mark.parametrize(
    "unsafe_result_text",
    (
        "Open https://news.example.com/unsubscribe?token=private-query",
        "Credential password=private-password-value",
        "Browser profile /Users/derek/private-email-profile",
    ),
)
def test_startup_rejects_noncanonical_or_unsafe_unsubscribe_result_text(
    tmp_path: Path,
    unsafe_result_text: str,
) -> None:
    database = tmp_path / "unsafe-unsubscribe-result.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "result_text": unsafe_result_text,
                "observation_digest": sha256(unsafe_result_text.encode("utf-8")).hexdigest(),
            })

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


def test_startup_rejects_wrong_untruncated_unsubscribe_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wrong-unsubscribe-result-digest.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "observation_digest": "f" * 64,
            })

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


@pytest.mark.parametrize(
    ("result_text", "observation_digest"),
    (
        ("", "a" * 64),
        ("Unsubscribed", ""),
    ),
)
def test_startup_rejects_empty_nonempty_unsubscribe_digest_mismatch(
    tmp_path: Path,
    result_text: str,
    observation_digest: str,
) -> None:
    database = tmp_path / "unsubscribe-empty-digest-mismatch.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "result_text": result_text,
                "observation_digest": observation_digest,
            })

    with pytest.raises(
        EmailPersistenceCorruption,
        match="unsubscribe receipt",
    ):
        EmailStore(database)


def test_unsubscribe_result_read_fails_closed_after_durable_row_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-result-read-corruption.sqlite3"
    store, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    unsafe_result_text = "https://news.example.com/unsubscribe?token=private-query"
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "result_text": unsafe_result_text,
                "observation_digest": sha256(unsafe_result_text.encode("utf-8")).hexdigest(),
            })

    with pytest.raises(EmailPersistenceCorruption):
        store.get_email_unsubscribe_receipt(str(authorization["action_identity"]))
    with pytest.raises(EmailPersistenceCorruption):
        store.list_email_classification_observability(
            int(authorization["classification_id"])
        )


def test_valid_16kib_truncated_unsubscribe_result_preserves_full_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-result-truncated.sqlite3"
    full_observation = "A" * (16 * 1024 + 777)
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text=full_observation,
    )
    expected_text, expected_digest = normalize_unsubscribe_result_text(full_observation)

    assert len(expected_text.encode("utf-8")) == 16 * 1024
    assert receipt["result_text"] == expected_text
    assert receipt["observation_digest"] == expected_digest
    assert receipt["result_text_truncated"] is True
    assert (
        receipt["result_text_digest"]
        == sha256(expected_text.encode("utf-8")).hexdigest()
    )
    assert receipt["result_text_digest"] != expected_digest
    assert expected_digest == sha256(full_observation.encode("utf-8")).hexdigest()
    reopened = EmailStore(database)
    assert (
        reopened.get_email_unsubscribe_receipt(str(authorization["action_identity"]))[
            "result_text_digest"
        ]
        == receipt["result_text_digest"]
    )


def test_exact_16kib_untruncated_result_requires_matching_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-exact-16kib-untruncated.sqlite3"
    _, authorization, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text="A" * (16 * 1024),
    )
    assert receipt["result_text_truncated"] is False
    assert receipt["observation_digest"] == receipt["result_text_digest"]
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "observation_digest": "f" * 64,
            })

    with pytest.raises(EmailPersistenceCorruption, match="observation digest"):
        EmailStore(database)


def test_stored_bounded_result_digest_is_always_verified(tmp_path: Path) -> None:
    database = tmp_path / "unsubscribe-bounded-digest.sqlite3"
    _, authorization, _ = _persist_unsubscribe_result_fixture(
        database,
        result_text="Unsubscribed",
    )
    with sqlite3.connect(database) as db:
        receipt_rows = db.execute(
            f"select rowid from ({EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for receipt_row in receipt_rows:
            update_email_action(db, "email_unsubscribe_receipts", receipt_row[0], {
                "result_text_digest": "f" * 64,
            })

    with pytest.raises(EmailPersistenceCorruption, match="bounded result digest"):
        EmailStore(database)


def test_empty_result_persists_empty_integrity_metadata(tmp_path: Path) -> None:
    database = tmp_path / "unsubscribe-empty-integrity.sqlite3"
    _, _, receipt = _persist_unsubscribe_result_fixture(database, result_text="")

    assert receipt["result_text"] == ""
    assert receipt["observation_digest"] == ""
    assert receipt["result_text_digest"] == ""
    assert receipt["result_text_truncated"] is False


def test_explicit_bounded_unsubscribe_result_preserves_full_observation_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-explicit-bounded-result.sqlite3"
    full_observation = "A" * (16 * 1024 + 777)
    bounded_text, full_digest = normalize_unsubscribe_result_text(full_observation)
    bounded_digest = sha256(bounded_text.encode("utf-8")).hexdigest()

    _, _, receipt = _persist_unsubscribe_result_fixture(
        database,
        result_text=bounded_text,
        observation_digest=full_digest,
        result_text_digest=bounded_digest,
        result_text_truncated=True,
    )

    assert receipt["result_text"] == bounded_text
    assert receipt["result_text_digest"] == bounded_digest
    assert receipt["observation_digest"] == full_digest
    assert receipt["result_text_truncated"] is True


def test_explicit_exact_16kib_result_rejects_mismatched_observation_digest(
    tmp_path: Path,
) -> None:
    result_text = "A" * (16 * 1024)
    bounded_digest = sha256(result_text.encode("utf-8")).hexdigest()

    with pytest.raises(EmailUnsubscribeReceiptConflict, match="integrity"):
        _persist_unsubscribe_result_fixture(
            tmp_path / "unsubscribe-explicit-exact-bound.sqlite3",
            result_text=result_text,
            observation_digest="f" * 64,
            result_text_digest=bounded_digest,
            result_text_truncated=False,
        )


@pytest.mark.parametrize(
    ("result_text", "observation_digest", "result_text_digest", "truncated"),
    [
        ("A" * (16 * 1024), "e" * 64, "f" * 64, True),
        ("A" * (16 * 1024), "e" * 64, sha256(b"A" * (16 * 1024)).hexdigest(), False),
        (
            "Unsubscribed",
            sha256(b"Unsubscribed").hexdigest(),
            sha256(b"Unsubscribed").hexdigest(),
            True,
        ),
    ],
)
def test_explicit_unsubscribe_result_rejects_inconsistent_caller_metadata(
    tmp_path: Path,
    result_text: str,
    observation_digest: str,
    result_text_digest: str,
    truncated: bool,
) -> None:
    with pytest.raises(EmailUnsubscribeReceiptConflict, match="integrity"):
        _persist_unsubscribe_result_fixture(
            tmp_path / f"unsubscribe-explicit-mismatch-{truncated}.sqlite3",
            result_text=result_text,
            observation_digest=observation_digest,
            result_text_digest=result_text_digest,
            result_text_truncated=truncated,
        )


@pytest.mark.parametrize("result_text", ("", "Unsubscribed"))
def test_explicit_empty_and_short_unsubscribe_results_remain_unchanged(
    tmp_path: Path,
    result_text: str,
) -> None:
    digest = sha256(result_text.encode("utf-8")).hexdigest() if result_text else ""

    _, _, receipt = _persist_unsubscribe_result_fixture(
        tmp_path / f"unsubscribe-explicit-short-{len(result_text)}.sqlite3",
        result_text=result_text,
        observation_digest=digest,
        result_text_digest=digest,
        result_text_truncated=False,
    )

    assert receipt["result_text"] == result_text
    assert receipt["observation_digest"] == digest
    assert receipt["result_text_digest"] == digest
    assert receipt["result_text_truncated"] is False


def _persist_unsubscribe_continuation_fixture(
    store: EmailStore,
) -> dict[str, object]:
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    store.persist_email_unsubscribe_continuation(
        **authorization,
        controls=(
            {
                "reference": "unsubscribe-control:" + "c" * 64,
                "kind": "button",
                "intent": "confirm",
            },
        ),
        observation_reference="unsubscribe-state:" + "d" * 64,
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "action_required",
            "reference": "unsubscribe-state:" + "d" * 64,
        },
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    return authorization


def test_unsubscribe_state_snapshot_decodes_one_read_consistent_view(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-snapshot.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)

    snapshot = store.get_email_unsubscribe_state_snapshot(
        str(authorization["action_identity"])
    )

    assert snapshot["claim"]["effect_digest"] == authorization["effect_digest"]
    assert snapshot["continuation"]["effect_digest"] == authorization["effect_digest"]
    assert [effect["effect_digest"] for effect in snapshot["effects"]] == [
        authorization["effect_digest"]
    ]


def test_unsubscribe_terminal_snapshot_rejects_step_effect_digest_tamper(
    tmp_path: Path,
) -> None:
    store, authorization, _ = _persist_unsubscribe_result_fixture(
        tmp_path / "unsubscribe-terminal-step-effect.sqlite3",
        result_text="Unsubscribed",
    )
    with sqlite3.connect(store.path) as db:
        step_rows = db.execute(
            f"select id from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        for step_row in step_rows:
            update_action(db, "email_unsubscribe_steps", step_row[0], {
                "effect_digest": "f" * 64,
            })

    with pytest.raises(EmailPersistenceCorruption, match="effect-bound"):
        store.get_email_unsubscribe_terminal_snapshot(
            str(authorization["action_identity"]),
            str(authorization["effect_digest"]),
        )


def test_unsubscribe_terminal_snapshot_uses_one_sqlite_read_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, authorization, receipt = _persist_unsubscribe_result_fixture(
        tmp_path / "unsubscribe-terminal-atomic-read.sqlite3",
        result_text="Unsubscribed",
    )
    original_decode = store._email_unsubscribe_claim_row
    raced = False

    def mutate_after_first_read(row):
        nonlocal raced
        if not raced:
            raced = True
            with sqlite3.connect(store.path) as writer:
                step_rows = writer.execute(
                    f"select id from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=?",
                    (authorization["action_identity"],),
                ).fetchall()
                for step_row in step_rows:
                    update_action(writer, "email_unsubscribe_steps", step_row[0], {
                        "effect_digest": "f" * 64,
                    })
        return original_decode(row)

    monkeypatch.setattr(store, "_email_unsubscribe_claim_row", mutate_after_first_read)

    snapshot = store.get_email_unsubscribe_terminal_snapshot(
        str(authorization["action_identity"]),
        str(authorization["effect_digest"]),
    )

    assert snapshot is not None
    assert snapshot["receipt"]["receipt_id"] == receipt["receipt_id"]
    assert "result_text_digest" not in snapshot["receipt"]
    assert "result_text_truncated" not in snapshot["receipt"]
    assert raced is True
    monkeypatch.setattr(store, "_email_unsubscribe_claim_row", original_decode)
    with pytest.raises(EmailPersistenceCorruption, match="effect-bound"):
        store.get_email_unsubscribe_terminal_snapshot(
            str(authorization["action_identity"]),
            str(authorization["effect_digest"]),
        )


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("email_unsubscribe_claims", "operations_json"),
        ("email_unsubscribe_effects", "operations_json"),
        ("email_unsubscribe_continuations", "controls_json"),
    ),
)
def test_unsubscribe_state_snapshot_reports_malformed_durable_json_as_corruption(
    tmp_path: Path,
    table: str,
    column: str,
) -> None:
    store = EmailStore(tmp_path / f"unsubscribe-corrupt-{table}-{column}.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)
    with sqlite3.connect(store.path) as db:
        if table == "email_unsubscribe_effects":
            row = db.execute(
                f"select action_identity,effect_digest from ({email_action_projection(table)}) "
                "where action_identity=?",
                (authorization["action_identity"],),
            ).fetchone()
            assert row is not None
            update_action(db, table, tuple(row), {column: "{}"})
        else:
            row = db.execute(
                f"select rowid from ({email_action_projection(table, include_rowid=True)}) "
                "where action_identity=?",
                (authorization["action_identity"],),
            ).fetchone()
            assert row is not None
            update_email_action(db, table, row[0], {column: "{}"})

    with pytest.raises(EmailPersistenceCorruption):
        store.get_email_unsubscribe_state_snapshot(
            str(authorization["action_identity"])
        )


def test_unsubscribe_state_snapshot_surfaces_real_sqlite_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-busy.sqlite3")
    gc.collect()
    _close_idle_connections()
    setup = sqlite3.connect(store.path)
    try:
        setup.execute("pragma journal_mode=delete")
    finally:
        setup.close()
    authorization = _persist_unsubscribe_continuation_fixture(store)
    gc.collect()
    _close_idle_connections()

    original_open_connection = store._open_connection

    def no_wait_open_connection(*args, **kwargs):
        db = original_open_connection(*args, **kwargs)
        db.execute("pragma busy_timeout=0")
        return db

    monkeypatch.setattr(store, "_open_connection", no_wait_open_connection)
    blocker = sqlite3.connect(store.path, timeout=0)
    try:
        blocker.execute("begin exclusive")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.get_email_unsubscribe_state_snapshot(
                str(authorization["action_identity"])
            )
    finally:
        blocker.rollback()
        blocker.close()


def test_unsubscribe_state_snapshot_rejects_row_33_before_effect_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-state-over-limit.sqlite3")
    authorization = _persist_unsubscribe_continuation_fixture(store)
    with sqlite3.connect(store.path) as db:
        for index in range(32):
            insert_action(db, "email_unsubscribe_effects", {
                "action_identity": authorization["action_identity"],
                "effect_digest": sha256(f"overflow-effect-{index}".encode()).hexdigest(),
                "previous_effect_digest": "",
                "operations_json": "{}",
                "audit_agent_run_id": None,
                "created_at": f"2026-09-03T00:00:{index:02d}+00:00",
            })
        assert db.execute(
            f"select count(*) from ({email_action_projection('email_unsubscribe_effects')}) "
            "where action_identity=?", (authorization["action_identity"],),
        ).fetchone()[0] == 33

    decoded = 0

    def reject_decode(_row):
        nonlocal decoded
        decoded += 1
        raise AssertionError("effect payload decoded before cardinality gate")

    monkeypatch.setattr(store, "_email_unsubscribe_effect_row", reject_decode)
    monkeypatch.setattr(store, "_email_unsubscribe_continuation_row", reject_decode)

    with pytest.raises(
        EmailPersistenceCorruption,
        match="durable continuation operation limit",
    ):
        store.get_email_unsubscribe_state_snapshot(
            str(authorization["action_identity"])
        )
    assert decoded == 0


@pytest.mark.parametrize("mutation", ("plan", "status", "account", "message"))
def test_unsubscribe_claim_fences_current_authorization_during_browser_write(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = EmailStore(tmp_path / f"unsubscribe-fence-{mutation}.sqlite3")
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True

    with pytest.raises(
        sqlite3.IntegrityError, match="email_unsubscribe_write_in_flight"
    ):
        if mutation == "plan":
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update emails set class_current_action_plan_id=null "
                    "where classification_id=?",
                    (authorization["classification_id"],),
                )
        elif mutation == "status":
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update emails set class_status='pending_feedback' "
                    "where classification_id=?",
                    (authorization["classification_id"],),
                )
        elif mutation == "account":
            current = store.get_account(str(authorization["account_id"]))
            assert current is not None
            store.update_account(
                str(authorization["account_id"]),
                {**current, "enabled": False},
            )
        else:
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "update emails set message_thread_identity='changed-thread' "
                    "where stable_message_identity=?",
                    (authorization["stable_message_identity"],),
                )


def test_unsubscribe_claim_recovery_requires_proven_owner_termination(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-owner-recovery.sqlite3")
    authorization = _unsubscribe_authorization(store)
    store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )

    with pytest.raises(EmailUnsubscribeClaimConflict, match="termination"):
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda _owner: False,
            recovered_at="2026-08-30T10:00:00+00:00",
        )
    assert (
        store.get_email_unsubscribe_claim(authorization["action_identity"])["status"]
        == "dispatching"
    )
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda owner: owner == _UNSUBSCRIBE_OWNER_A,
            recovered_at="2026-08-30T10:01:00+00:00",
        )
        == 1
    )
    assert (
        store.get_email_unsubscribe_claim(authorization["action_identity"])["status"]
        == "uncertain"
    )


def test_uncertain_unsubscribe_claim_cannot_be_reacquired_for_blind_write(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-uncertain-replay.sqlite3")
    authorization = _unsubscribe_authorization(store)
    first = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert first is not None and first["acquired"] is True
    assert (
        store.recover_terminated_email_unsubscribe_claims(
            owner=_UNSUBSCRIBE_OWNER_A,
            termination_verifier=lambda owner: owner == _UNSUBSCRIBE_OWNER_A,
            recovered_at="2026-08-30T10:02:00+00:00",
        )
        == 1
    )

    replay = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_B,
    )

    assert replay is not None
    assert replay["acquired"] is False
    assert replay["status"] == "uncertain"
    persisted = store.get_email_unsubscribe_claim(authorization["action_identity"])
    assert persisted is not None
    assert persisted["status"] == "uncertain"
    assert persisted["owner_id"] == _UNSUBSCRIBE_OWNER_A["owner_id"]
    assert persisted["owner_generation"] == _UNSUBSCRIBE_OWNER_A["generation"]
    assert persisted["lease_token"] == _UNSUBSCRIBE_OWNER_A["lease_token"]


def test_explicit_retry_releases_failed_unsubscribe_without_browser_state(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-explicit-retry.sqlite3")
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None
    task_store = AutoReplyStore(store.path)
    task = task_store.ensure_reply_task(
        channel="email",
        conversation_id="email-thread",
        conversation_title="Email",
        single_chat=True,
        trigger_message_id=authorization["action_identity"],
        trigger_create_time="2026-09-09T03:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="unsubscribe",
        execution_generation="generation-explicit-retry",
    )
    consumer = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer-owner",
    ).run
    consumer = task_store.complete_agent_run(
        consumer.id,
        {"outcome": "proposal"},
        owner="consumer-owner",
    )
    audit = task_store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="email-unsubscribe-audit",
        owner="audit-owner",
    ).run
    task_store.fail_agent_run(
        audit.id,
        {
            "code": "email_unsubscribe_browser_failed",
            "retryable": True,
            "authorization_required": False,
            "session_continuable": False,
        },
        owner="audit-owner",
    )
    with sqlite3.connect(store.path) as db:
        claim_row = db.execute(
            f"select rowid from ({email_action_projection('email_unsubscribe_claims', include_rowid=True)}) "
            "where action_identity=?", (authorization["action_identity"],),
        ).fetchone()
        assert claim_row is not None
        update_email_action(db, "email_unsubscribe_claims", claim_row[0], {
            "status": "uncertain", "phase": "effect_uncertain", "audit_agent_run_id": audit.id,
        })
        update_action(db, "email_unsubscribe_effects",
                      (authorization["action_identity"], claim["effect_digest"]),
                      {"audit_agent_run_id": audit.id})

    store.release_failed_email_unsubscribe_for_explicit_retry(
        authorization["action_identity"],
        audit_agent_run_id=audit.id,
    )

    assert store.get_email_unsubscribe_claim(authorization["action_identity"]) is None
    assert store.get_email_unsubscribe_effect(
        authorization["action_identity"], claim["effect_digest"]
    ) is None


def test_dispatching_unsubscribe_claim_is_not_reacquired_by_same_owner(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-same-owner.sqlite3")
    authorization = _unsubscribe_authorization(store)

    first = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    second = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )

    assert first is not None and first["acquired"] is True
    assert second is not None and second["acquired"] is False
    assert second["status"] == "dispatching"


def test_no_claim_terminal_rejects_forged_unsubscribe_action_identity(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-forged-terminal.sqlite3")
    authorization = _unsubscribe_authorization(store)
    forged = {**authorization, "action_identity": "email-action:forged"}
    forged["effect_digest"] = email_unsubscribe_effect_digest(
        **{key: value for key, value in forged.items() if key != "effect_digest"}
    )

    with pytest.raises(EmailUnsubscribeReceiptConflict, match="identity"):
        store.persist_email_unsubscribe_terminal(
            **forged,
            outcome="skipped_no_reliable_entry",
            receipt_id="unsubscribe-receipt:forged",
            evidence="entry-selection",
            final_step=None,
            claim_owner=None,
        )


def test_startup_rejects_forged_durable_unsubscribe_action_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unsubscribe-forged-startup.sqlite3"
    store = EmailStore(database)
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    assert claim is not None and claim["acquired"] is True
    forged_identity = "email-action:forged"
    forged_digest = email_unsubscribe_effect_digest(
        **{
            **{
                key: value
                for key, value in authorization.items()
                if key != "effect_digest"
            },
            "action_identity": forged_identity,
        }
    )
    with sqlite3.connect(database) as db:
        db.execute("pragma foreign_keys=off")
        [claim_row] = db.execute(
            f"select rowid from ({email_action_projection('email_unsubscribe_claims', include_rowid=True)}) "
            "where action_identity=?",
            (authorization["action_identity"],),
        ).fetchall()
        assert update_email_action(
            db,
            "email_unsubscribe_claims",
            claim_row[0],
            {"action_identity": forged_identity, "effect_digest": forged_digest},
        ).rowcount == 1

    with pytest.raises(EmailPersistenceCorruption, match="ActionPlan"):
        EmailStore(database)


def test_unsubscribe_receipt_is_exactly_bound_and_terminal_write_is_atomic(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "unsubscribe-receipt-binding.sqlite3")
    authorization = _unsubscribe_authorization(store)
    store.claim_email_unsubscribe_write(
        **authorization,
        owner=_UNSUBSCRIBE_OWNER_A,
    )
    receipt_arguments = {
        key: authorization[key]
        for key in (
            "action_identity",
            "effect_digest",
            "action_plan_id",
            "action_plan_version",
            "classification_id",
            "account_id",
            "stable_message_identity",
            "thread_identity",
            "entry_reference",
            "operations",
        )
    }
    with sqlite3.connect(store.path) as db:
        quoted_action_identity = db.execute(
            "select quote(?)", (authorization["action_identity"],)
        ).fetchone()[0]
        db.execute(
            f"""
            create trigger test_abort_unsubscribe_claim_done
            before update on actions
            when old.record_type='email_unsubscribe_claims'
              and new.record_type='email_unsubscribe_claims'
              and new.email_status='done'
              and new.email_action_identity={quoted_action_identity}
            begin
                select raise(abort, 'simulated_unsubscribe_terminal_crash');
            end
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="simulated_unsubscribe_terminal_crash",
    ):
        store.persist_email_unsubscribe_terminal(
            **receipt_arguments,
            outcome="done",
            receipt_id="unsubscribe-receipt:done-41",
            evidence="terminal-page",
            final_step={
                "sequence": 1,
                "operation": "open_entry",
                "state": "done",
                "reference": "unsubscribe-receipt:done-41",
            },
            claim_owner=_UNSUBSCRIBE_OWNER_A,
        )
    assert store.get_email_unsubscribe_receipt(authorization["action_identity"]) is None
    assert store.list_email_unsubscribe_steps(authorization["action_identity"]) == []

    with sqlite3.connect(store.path) as db:
        db.execute("drop trigger test_abort_unsubscribe_claim_done")
    store.persist_email_unsubscribe_terminal(
        **receipt_arguments,
        outcome="done",
        receipt_id="unsubscribe-receipt:done-41",
        evidence="terminal-page",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "done",
            "reference": "unsubscribe-receipt:done-41",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )
    with pytest.raises(EmailUnsubscribeReceiptConflict):
        store.persist_email_unsubscribe_terminal(
            **{**receipt_arguments, "effect_digest": "c" * 64},
            outcome="done",
            receipt_id="unsubscribe-receipt:done-41",
            evidence="terminal-page",
            final_step=None,
            claim_owner=None,
        )


def test_account_scan_folders_json_must_be_a_list_of_folder_names(tmp_path: Path):
    database = tmp_path / "corrupt-account.sqlite3"
    EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute("begin immediate")
        insert_config(db, "email_accounts", {
            "account_id": "ding-main", "display_name": "DingTalk",
            "email_address": "redacted@example.com", "imap_host": "imap.example.com",
            "imap_port": 993, "imap_tls": 1, "imap_username": "redacted@example.com",
            "imap_secret_reference": "CEO_EMAIL_DING_IMAP_SECRET",
            "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_tls": 1,
            "smtp_username": "redacted@example.com",
            "smtp_secret_reference": "CEO_EMAIL_DING_SMTP_SECRET", "enabled": 1,
            "scan_folders_json": "{}", "scan_interval_seconds": 60,
            "created_at": "2026-08-29T16:00:00+00:00",
            "updated_at": "2026-08-29T16:00:00+00:00",
        })

    with pytest.raises(EmailPersistenceCorruption, match="scan_folders_json"):
        EmailStore(database)


def _task2_account_values(
    account_id: str,
    *,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "display_name": account_id,
        "email_address": f"{account_id}@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": f"{account_id}@example.com",
        "imap_secret_reference": f"keychain://{account_id}",
        "imap_move_mode": "copy_as_move" if account_id == "primary" else "move",
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_tls": True,
        "smtp_username": "",
        "smtp_secret_reference": "",
        "enabled": enabled,
        "scan_folders": ["INBOX"],
        "scan_interval_seconds": 60,
    }


def _task2_verified_binding(
    *,
    account_id: str = "primary",
    folder_id: str = "folder-partners",
    folder_name: str = "合作伙伴",
    status: str = "active",
    verified_at: str = "2026-09-07T12:10:00+00:00",
    folder_role: FolderRole = FolderRole.UNBOUND,
):
    binding_type = getattr(email_store_module, "VerifiedEmailFolderBinding")
    return binding_type(
        account_id=account_id,
        provider_folder_id=folder_id,
        provider_folder_name=folder_name,
        binding_status=status,
        last_verified_at=verified_at,
        provider_folder_role=folder_role,
    )


def _create_task2_category(
    store: EmailStore,
    *,
    category_key: str = "partner_updates",
    display_name: str = "合作伙伴",
    bindings: tuple[object, ...] | None = None,
) -> dict[str, object]:
    if bindings is None:
        bindings = (_task2_verified_binding(folder_name=display_name),)
    return store.create_category_with_bindings(
        category_key=category_key,
        display_name=display_name,
        core_description=f"Material messages for {category_key}.",
        include=(f"included {category_key} messages",),
        exclude=(f"excluded {category_key} promotion",),
        threshold=0.91,
        actions=(),
        action_parameters={},
        enabled=True,
        description_version=f"{category_key}-description-v1",
        config_version=f"{category_key}-config-v1",
        bindings=bindings,
    )


def test_fresh_category_configs_seed_structured_approved_boundaries(tmp_path: Path):
    store = EmailStore(tmp_path / "seeded-categories.sqlite3")

    configs = {row["category_key"]: row for row in store.list_category_configs()}

    assert configs["work"]["display_name"] == "工作"
    assert "daily operations" in configs["work"]["core_description"]
    assert "customers" in configs["work"]["include"]
    assert "human resources" in configs["work"]["exclude"]
    assert "recruiting" in configs["human_resources"]["include"]
    assert "legal disputes" in configs["human_resources"]["exclude"]
    assert "non-financing contracts" in configs["legal"]["include"]
    assert "financing legal documents" in configs["legal"]["exclude"]
    assert "due diligence" in configs["financing"]["include"]
    assert "unsolicited fundraising promotion" in configs["financing"]["exclude"]
    assert "Derek" in configs["personal"]["include"]
    assert "company operations" in configs["personal"]["exclude"]
    assert "verification codes" in configs["notification"]["include"]
    assert "real business discussion" in configs["notification"]["exclude"]
    assert "requests payment" in configs["external_billing"]["include"]
    assert "our invoices to customers" in configs["external_billing"]["exclude"]
    assert "logistics" in configs["shopping"]["include"]
    assert "external professional-service invoices" in configs["shopping"]["exclude"]
    assert "suspicious promotion" in configs["junk"]["include"]
    assert (
        "Unsubscribe-link presence must not be classification evidence"
        in configs["junk"]["exclude"]
    )
    assert all(config["threshold"] == 0.95 for config in configs.values())
    assert all(config["actions"] == [] for config in configs.values())
    assert all(config["action_parameters"] == {} for config in configs.values())


@pytest.mark.parametrize("field,value", (
    ("include_json", "not-json"), ("core_description", " "),
    ("include_json", "[]"), ("include_json", '[" " ]'),
    ("exclude_json", "[]"), ("exclude_json", '[""]'),
))
def test_category_config_schema_rejects_invalid_structured_descriptions(
    tmp_path: Path,
    field: str, value: str,
):
    database = tmp_path / "invalid-category-description.sqlite3"
    EmailStore(database)

    with sqlite3.connect(database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "update configs set payload_json=json_set(payload_json, ?, ?) "
                "where record_type='email_category_configs' and public_key='work'",
                ("$." + field, value),
            )


def test_create_category_with_verified_bindings_is_atomic_and_account_complete(
    tmp_path: Path,
):
    database = tmp_path / "create-category.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("primary"))
    store.create_account(_task2_account_values("disabled", enabled=False))

    created = _create_task2_category(store)

    assert created["enabled"] is True
    assert store.get_category_config("partner_updates") == created
    assert store.list_account_folder_bindings("partner_updates") == [
        {
            "account_id": "primary",
            "category_key": "partner_updates",
            "provider_folder_id": "folder-partners",
            "provider_folder_name": "合作伙伴",
            "binding_status": "active",
            "last_verified_at": "2026-09-07T12:10:00+00:00",
        }
    ]
    with pytest.raises(TypeError, match="verified folder binding"):
        store.create_category_with_bindings(
            category_key="raw_binding",
            display_name="Raw",
            core_description="Raw client binding claims.",
            include=("raw claim",),
            exclude=("verified claim",),
            threshold=0.9,
            actions=(),
            action_parameters={},
            enabled=True,
            description_version="raw-v1",
            config_version="raw-v1",
            bindings=(
                {
                    "account_id": "primary",
                    "provider_folder_id": "raw-folder",
                    "provider_folder_name": "Raw",
                    "binding_status": "active",
                    "last_verified_at": "2026-09-07T12:11:00+00:00",
                },
            ),
        )
    assert store.get_category_config("raw_binding") is None


def test_unresolved_binding_is_persisted_but_forces_category_disabled(tmp_path: Path):
    store = EmailStore(tmp_path / "ambiguous-category.sqlite3")
    store.create_account(_task2_account_values("primary"))

    created = _create_task2_category(
        store,
        category_key="vendor_updates",
        display_name="供应商",
        bindings=(
            _task2_verified_binding(
                folder_id="",
                folder_name="供应商",
                status="ambiguous",
                verified_at="2026-09-07T12:12:00+00:00",
            ),
        ),
    )

    assert created["enabled"] is False
    assert (
        store.list_account_folder_bindings("vendor_updates")[0]["binding_status"]
        == "ambiguous"
    )


def test_active_provider_folder_conflict_rolls_back_dynamic_category(tmp_path: Path):
    conflict_type = getattr(email_store_module, "EmailFolderBindingConflict")
    store = EmailStore(tmp_path / "binding-conflict.sqlite3")
    store.create_account(_task2_account_values("primary"))

    def create(category_key: str, folder_id: str) -> dict[str, object]:
        return _create_task2_category(
            store,
            category_key=category_key,
            display_name=category_key,
            bindings=(
                _task2_verified_binding(
                    folder_id=folder_id,
                    folder_name=category_key,
                    verified_at="2026-09-07T12:13:00+00:00",
                ),
            ),
        )

    create("partners", "shared-folder-id")
    with pytest.raises(conflict_type):
        create("vendors", "shared-folder-id")

    assert store.get_category_config("vendors") is None
    assert store.list_account_folder_bindings("vendors") == []


def test_update_descriptions_and_binding_status_round_trip(tmp_path: Path):
    store = EmailStore(tmp_path / "update-category.sqlite3")
    store.create_account(_task2_account_values("primary"))
    _create_task2_category(
        store,
        category_key="partners",
        display_name="合作伙伴",
        bindings=(
            _task2_verified_binding(
                folder_id="partners-folder",
                verified_at="2026-09-07T12:14:00+00:00",
            ),
        ),
    )

    updated = store.update_category_descriptions(
        "partners",
        core_description="Material partner discussions.",
        include=("partner contracts",),
        exclude=("generic sales promotion",),
        threshold=0.94,
        enabled=True,
        description_version="partners-description-v2",
        config_version="partners-config-v2",
    )
    binding = store.set_folder_binding_status(
        account_id="primary",
        category_key="partners",
        provider_folder_id="",
        provider_folder_name="合作伙伴",
        binding_status="missing",
        last_verified_at="2026-09-07T12:15:00+00:00",
        provider_folder_role=FolderRole.UNBOUND,
    )

    assert updated is not None
    assert updated["core_description"] == "Material partner discussions."
    assert updated["include"] == ["partner contracts"]
    assert updated["exclude"] == ["generic sales promotion"]
    assert updated["threshold"] == 0.94
    assert updated["description_version"] == "partners-description-v2"
    assert updated["config_version"] == "partners-config-v2"
    assert binding["binding_status"] == "missing"
    assert store.get_category_config("partners")["enabled"] is False


def test_compatibility_upsert_cannot_enable_without_complete_active_bindings(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "compatibility-binding-completeness.sqlite3")
    store.create_account(_task2_account_values("primary"))

    updated = store.upsert_config(
        category=EmailCategory.WORK,
        description="Daily work.",
        threshold=0.9,
        actions=(),
        action_parameters={},
        enabled=True,
        config_version="work-v2",
    )

    assert updated["enabled"] is False
    assert store.get_category_config("work")["enabled"] is False


def test_creating_enabled_account_disables_categories_without_its_active_binding(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "create-enabled-account-bindings.sqlite3")
    assert all(row["enabled"] for row in store.list_category_configs())

    store.create_account(_task2_account_values("new-enabled"))

    assert all(not row["enabled"] for row in store.list_category_configs())


def test_enabling_account_keeps_only_categories_with_complete_active_bindings(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "enable-account-bindings.sqlite3")
    store.create_account(_task2_account_values("secondary", enabled=False))
    _create_task2_category(
        store,
        category_key="covered_category",
        bindings=(
            _task2_verified_binding(
                account_id="secondary",
                folder_id="covered-folder",
            ),
        ),
    )
    _create_task2_category(
        store,
        category_key="uncovered_category",
        bindings=(),
    )

    store.update_account(
        "secondary",
        _task2_account_values("secondary", enabled=True),
    )

    configs = {row["category_key"]: row for row in store.list_category_configs()}
    assert configs["covered_category"]["enabled"] is True
    assert configs["uncovered_category"]["enabled"] is False
    assert configs["work"]["enabled"] is False


def test_account_enable_and_category_disabling_are_one_transaction(tmp_path: Path):
    database = tmp_path / "account-category-atomicity.sqlite3"
    store = EmailStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create trigger abort_category_disabling
            before update on configs
            when old.record_type='email_category_configs'
              and old.category_enabled=1 and new.category_enabled=0
            begin
                select raise(abort, 'forced category disable failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced category disable failure"):
        store.create_account(_task2_account_values("rolled-back-account"))

    assert store.get_account("rolled-back-account") is None
    assert all(row["enabled"] for row in store.list_category_configs())


def test_reopen_rejects_enabled_category_missing_enabled_account_binding(
    tmp_path: Path,
):
    database = tmp_path / "category-binding-corruption.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("primary"))
    with sqlite3.connect(database) as db:
        db.execute("begin immediate")
        rowid = db.execute(
            f"select rowid from ({config_projection('email_category_configs', include_rowid=True)}) "
            "where category_key='work'"
        ).fetchone()[0]
        assert update_config(db, "email_category_configs", rowid, {"enabled": 1}) == 1

    with pytest.raises(
        EmailPersistenceCorruption,
        match="enabled email category is missing an active account folder binding",
    ):
        EmailStore(database)


def test_seeded_category_requires_explicit_enable_after_bindings_complete(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "upsert-first-binding.sqlite3")
    store.create_account(_task2_account_values("primary"))
    store.create_account(_task2_account_values("secondary"))
    assert store.get_category_config("work")["enabled"] is False

    first = store.upsert_verified_folder_binding(
        "work",
        _task2_verified_binding(
            account_id="primary",
            folder_id="work-primary",
            folder_name="工作",
        ),
    )
    updated = store.upsert_verified_folder_binding(
        "work",
        _task2_verified_binding(
            account_id="primary",
            folder_id="work-primary-updated",
            folder_name="工作更新",
            verified_at="2026-09-07T12:30:00+00:00",
        ),
    )

    assert first["provider_folder_id"] == "work-primary"
    assert updated["provider_folder_id"] == "work-primary-updated"
    assert updated["provider_folder_name"] == "工作更新"
    assert store.get_category_config("work")["enabled"] is False

    store.upsert_verified_folder_binding(
        "work",
        _task2_verified_binding(
            account_id="secondary",
            folder_id="work-secondary",
            folder_name="工作",
        ),
    )

    complete = store.get_category_config("work")
    assert complete["enabled"] is False

    enabled = store.update_category_descriptions(
        "work",
        core_description=complete["core_description"],
        include=complete["include"],
        exclude=complete["exclude"],
        threshold=complete["threshold"],
        enabled=True,
        description_version=complete["description_version"],
        config_version="work-explicitly-enabled-v1",
    )

    assert enabled is not None
    assert enabled["enabled"] is True


def test_junk_upsert_accepts_only_verified_system_trash_binding(tmp_path: Path) -> None:
    store = EmailStore(tmp_path / "junk-trash-binding.sqlite3")
    store.create_account(_task2_account_values("primary"))
    trash = _task2_verified_binding(
        account_id="primary",
        folder_id="provider-trash",
        folder_name="Deleted",
        folder_role=FolderRole.TRASH,
    )

    persisted = store.upsert_verified_folder_binding("junk", trash)

    assert persisted["provider_folder_id"] == "provider-trash"
    with pytest.raises(ValueError, match="system Trash"):
        store.upsert_verified_folder_binding(
            "junk",
            _task2_verified_binding(
                account_id="primary",
                folder_id="provider-junk",
                folder_name="垃圾邮件",
            ),
        )


def test_junk_coordinator_persists_whitespace_provider_trash_exactly(
    tmp_path: Path,
) -> None:
    class WhitespaceTrashProvider:
        def list_folders(self):
            return (ProviderFolder(" Deleted ", " Deleted ", FolderRole.TRASH),)

        def create_folder_exact(self, name: str) -> None:
            raise AssertionError(f"junk folder must not be created: {name}")

        def close(self) -> None:
            pass

    coordinator = import_module("app.email_worker").ProviderFolderBindingCoordinator(
        lambda _account: WhitespaceTrashProvider(),
        now=lambda: "2026-09-08T10:00:00+00:00",
    )
    binding = coordinator.create_and_verify_bindings(
        category_key="junk",
        provider_folder_name="垃圾邮件",
        enabled_accounts=({"account_id": "primary"},),
    )[0]
    store = EmailStore(tmp_path / "whitespace-trash-binding.sqlite3")
    store.create_account(_task2_account_values("primary"))

    persisted = store.upsert_verified_folder_binding("junk", binding)

    assert binding.binding_status == "active"
    assert binding.provider_folder_id == " Deleted "
    assert binding.provider_folder_name == " Deleted "
    assert persisted["provider_folder_id"] == " Deleted "
    assert persisted["provider_folder_name"] == " Deleted "
    assert store.list_account_folder_bindings("junk") == [persisted]


@pytest.mark.parametrize("negative_status", ("missing", "ambiguous", "error"))
def test_junk_negative_verification_invalidates_stale_trash_and_disables_writes(
    tmp_path: Path,
    negative_status: str,
) -> None:
    database = tmp_path / f"junk-{negative_status}-invalidation.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("primary"))
    store.upsert_verified_folder_binding(
        "junk",
        _task2_verified_binding(
            account_id="primary",
            folder_id="provider-trash",
            folder_name="Deleted",
            folder_role=FolderRole.TRASH,
        ),
    )
    junk = store.get_category_config("junk")
    enabled = store.update_category_descriptions(
        "junk",
        core_description=junk["core_description"],
        include=junk["include"],
        exclude=junk["exclude"],
        threshold=junk["threshold"],
        enabled=True,
        description_version=junk["description_version"],
        config_version=f"junk-enabled-before-{negative_status}",
    )
    assert enabled is not None and enabled["enabled"] is True

    observed = store.upsert_verified_folder_binding(
        "junk",
        _task2_verified_binding(
            account_id="primary",
            folder_id="",
            folder_name="垃圾邮件",
            status=negative_status,
            verified_at="2026-09-08T11:00:00+00:00",
        ),
    )

    assert observed["provider_folder_id"] == ""
    assert observed["binding_status"] == negative_status
    assert store.get_category_config("junk")["enabled"] is False
    reopened = EmailStore(database)
    assert reopened.list_account_folder_bindings("junk") == [observed]
    assert reopened.get_category_config("junk")["enabled"] is False


@pytest.mark.parametrize(
    "system_role",
    (
        FolderRole.INBOX,
        FolderRole.JUNK,
        FolderRole.TRASH,
        FolderRole.SENT,
        FolderRole.DRAFT,
    ),
)
def test_business_binding_rejects_active_provider_system_folder_role(
    tmp_path: Path,
    system_role: FolderRole,
) -> None:
    store = EmailStore(tmp_path / f"business-system-role-{system_role}.sqlite3")
    store.create_account(_task2_account_values("primary"))

    with pytest.raises(ValueError, match="business category"):
        store.upsert_verified_folder_binding(
            "work",
            _task2_verified_binding(
                account_id="primary",
                folder_id="system-folder",
                folder_name="工作",
                folder_role=system_role,
            ),
        )


def test_scalar_binding_status_update_cannot_bypass_system_folder_role_invariant(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "scalar-system-role.sqlite3")
    store.create_account(_task2_account_values("primary"))
    store.upsert_verified_folder_binding(
        "work", _task2_verified_binding(account_id="primary")
    )

    with pytest.raises(ValueError, match="business category"):
        store.set_folder_binding_status(
            account_id="primary",
            category_key="work",
            provider_folder_id="system-trash",
            provider_folder_name="Trash",
            binding_status="active",
            last_verified_at="2026-09-08T12:30:00+00:00",
            provider_folder_role=FolderRole.TRASH,
        )


def test_category_binding_refresh_rolls_back_all_accounts_and_descriptions_on_conflict(
    tmp_path: Path,
) -> None:
    store = EmailStore(tmp_path / "atomic-category-refresh.sqlite3")
    store.create_account(_task2_account_values("primary"))
    store.create_account(_task2_account_values("secondary"))
    for account_id in ("primary", "secondary"):
        store.upsert_verified_folder_binding(
            "work",
            _task2_verified_binding(
                account_id=account_id,
                folder_id=f"old-work-{account_id}",
                folder_name="工作",
            ),
        )
    work = store.get_category_config("work")
    store.update_category_descriptions(
        "work",
        core_description=work["core_description"],
        include=work["include"],
        exclude=work["exclude"],
        threshold=work["threshold"],
        enabled=True,
        description_version=work["description_version"],
        config_version="work-before-atomic-refresh",
    )
    store.upsert_verified_folder_binding(
        "legal",
        _task2_verified_binding(
            account_id="secondary",
            folder_id="secondary-conflict",
            folder_name="法务",
        ),
    )
    before_config = store.get_category_config("work")
    before_bindings = store.list_account_folder_bindings("work")

    with pytest.raises(email_store_module.EmailFolderBindingConflict):
        store.refresh_category_with_bindings(
            "work",
            core_description="Changed description must roll back.",
            include=("changed include",),
            exclude=("changed exclude",),
            threshold=0.81,
            enabled=False,
            description_version="work-description-conflict",
            config_version="work-config-conflict",
            bindings=(
                _task2_verified_binding(
                    account_id="primary",
                    folder_id="new-work-primary",
                    folder_name="工作",
                ),
                _task2_verified_binding(
                    account_id="secondary",
                    folder_id="secondary-conflict",
                    folder_name="工作",
                ),
            ),
        )

    assert store.get_category_config("work") == before_config
    assert store.list_account_folder_bindings("work") == before_bindings


def test_active_binding_upsert_preserves_explicitly_disabled_category(tmp_path: Path):
    store = EmailStore(tmp_path / "binding-preserves-disabled.sqlite3")
    store.create_account(_task2_account_values("primary"))
    binding = _task2_verified_binding(
        account_id="primary",
        folder_id="work-primary",
        folder_name="工作",
    )
    store.upsert_verified_folder_binding("work", binding)
    work = store.get_category_config("work")
    explicitly_enabled = store.update_category_descriptions(
        "work",
        core_description=work["core_description"],
        include=work["include"],
        exclude=work["exclude"],
        threshold=work["threshold"],
        enabled=True,
        description_version=work["description_version"],
        config_version="work-enabled-v1",
    )
    explicitly_disabled = store.update_category_descriptions(
        "work",
        core_description=work["core_description"],
        include=work["include"],
        exclude=work["exclude"],
        threshold=work["threshold"],
        enabled=False,
        description_version=work["description_version"],
        config_version="work-disabled-v1",
    )

    store.upsert_verified_folder_binding(
        "work",
        _task2_verified_binding(
            account_id="primary",
            folder_id="work-primary-updated",
            folder_name="工作更新",
            verified_at="2026-09-07T12:40:00+00:00",
        ),
    )

    assert explicitly_enabled is not None
    assert explicitly_enabled["enabled"] is True
    assert explicitly_disabled is not None
    assert explicitly_disabled["enabled"] is False
    assert store.get_category_config("work")["enabled"] is False


def test_upsert_verified_folder_binding_is_typed_atomic_and_unique(tmp_path: Path):
    conflict_type = getattr(email_store_module, "EmailFolderBindingConflict")
    store = EmailStore(tmp_path / "upsert-binding-conflict.sqlite3")
    store.create_account(_task2_account_values("primary"))
    work_binding = _task2_verified_binding(
        account_id="primary",
        folder_id="claimed-folder",
        folder_name="工作",
    )
    store.upsert_verified_folder_binding("work", work_binding)

    with pytest.raises(conflict_type):
        store.upsert_verified_folder_binding(
            "legal",
            _task2_verified_binding(
                account_id="primary",
                folder_id="claimed-folder",
                folder_name="法务",
            ),
        )
    with pytest.raises(TypeError, match="VerifiedEmailFolderBinding"):
        store.upsert_verified_folder_binding(
            "legal",
            {
                "account_id": "primary",
                "provider_folder_id": "raw-folder",
                "provider_folder_name": "法务",
                "binding_status": "active",
                "last_verified_at": "2026-09-07T12:31:00+00:00",
            },
        )

    assert store.list_account_folder_bindings("legal") == []
    assert store.list_account_folder_bindings("work") == [
        {
            "account_id": "primary",
            "category_key": "work",
            "provider_folder_id": "claimed-folder",
            "provider_folder_name": "工作",
            "binding_status": "active",
            "last_verified_at": "2026-09-07T12:10:00+00:00",
        }
    ]


def test_category_enablement_snapshot_restores_create_and_update_compensation(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "category-enablement-compensation.sqlite3")
    enabled_before_create = {
        row["category_key"]: row["enabled"] for row in store.list_category_configs()
    }

    created, create_snapshot = store.create_account_with_category_enablement_snapshot(
        _task2_account_values("created")
    )

    assert store.delete_account_if_unchanged(
        "created",
        expected_updated_at=created["updated_at"],
        category_enablement_snapshot=create_snapshot,
    )
    assert store.get_account("created") is None
    assert {
        row["category_key"]: row["enabled"] for row in store.list_category_configs()
    } == enabled_before_create

    store.create_account(_task2_account_values("updated", enabled=False))
    account_before_update = store.get_account("updated")
    enabled_before_update = {
        row["category_key"]: row["enabled"] for row in store.list_category_configs()
    }
    update_result = store.update_account_with_category_enablement_snapshot(
        "updated",
        _task2_account_values("updated", enabled=True),
    )
    assert update_result is not None
    updated, previous, update_snapshot = update_result

    assert previous == account_before_update
    assert store.restore_account_if_unchanged(
        previous,
        expected_updated_at=updated["updated_at"],
        category_enablement_snapshot=update_snapshot,
    )
    assert store.get_account("updated") == account_before_update
    assert {
        row["category_key"]: row["enabled"] for row in store.list_category_configs()
    } == enabled_before_update


def test_category_enablement_snapshot_refuses_concurrent_category_edit(tmp_path: Path):
    store = EmailStore(tmp_path / "category-enablement-conflict.sqlite3")
    created, snapshot = store.create_account_with_category_enablement_snapshot(
        _task2_account_values("primary")
    )
    work = store.get_category_config("work")
    assert work is not None
    store.update_category_descriptions(
        "work",
        core_description=work["core_description"],
        include=work["include"],
        exclude=work["exclude"],
        threshold=work["threshold"],
        enabled=False,
        description_version=work["description_version"],
        config_version="concurrent-work-edit-v1",
    )

    restored = store.delete_account_if_unchanged(
        "primary",
        expected_updated_at=created["updated_at"],
        category_enablement_snapshot=snapshot,
    )

    assert restored is False
    assert store.get_account("primary") == created
    assert store.get_category_config("work")["config_version"] == (
        "concurrent-work-edit-v1"
    )


def _fd_regression_account_values() -> dict[str, object]:
    return {
        "account_id": "fd-regression-account",
        "display_name": "FD Regression",
        "email_address": "fd-regression@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": "fd-regression@example.com",
        "imap_secret_reference": "keychain://imap-test",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_tls": True,
        "smtp_username": "fd-regression@example.com",
        "smtp_secret_reference": "keychain://smtp-test",
        "enabled": True,
        "scan_folders": ["INBOX"],
        "scan_interval_seconds": 60,
    }


def test_connect_closes_connection_after_each_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "connect-closes.sqlite3")
    opened: list[sqlite3.Connection] = []
    original_open_connection = EmailStore._open_connection

    def recording_open_connection(self: EmailStore, *args, **kwargs) -> sqlite3.Connection:
        db = original_open_connection(self, *args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(EmailStore, "_open_connection", recording_open_connection)

    assert store.get_account("missing") is None
    store.create_account(_fd_regression_account_values())
    assert store.get_account("fd-regression-account") is not None
    assert store.get_email_unsubscribe_state_snapshot("missing-identity") == {
        "claim": None,
        "continuation": None,
        "effects": (),
        "receipt": None,
    }
    assert store.get_email_unsubscribe_terminal_snapshot("missing-identity") is None

    assert len(opened) >= 5
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("select 1")


def test_connect_rolls_back_and_closes_when_the_body_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EmailStore(tmp_path / "connect-rollback.sqlite3")
    store.create_account(_fd_regression_account_values())
    opened: list[sqlite3.Connection] = []
    original_open_connection = EmailStore._open_connection

    def recording_open_connection(self: EmailStore, *args, **kwargs) -> sqlite3.Connection:
        db = original_open_connection(self, *args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(EmailStore, "_open_connection", recording_open_connection)

    with pytest.raises(RuntimeError, match="body failed"):
        with store._connect() as db:
            db.execute("begin immediate")
            account = db.execute(
                f"select rowid from ({config_projection('email_accounts', include_rowid=True)}) "
                "where account_id=?",
                ("fd-regression-account",),
            ).fetchone()
            assert account is not None
            delete_config(db, "email_accounts", account[0])
            raise RuntimeError("body failed")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("select 1")
    assert store.get_account("fd-regression-account") is not None


def test_slow_connection_names_caller_without_logging_body_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.email_store as module

    store = EmailStore(tmp_path / "slow-context.sqlite3")
    ticks = iter((0.0, 3.0, 4.0, 5.0))
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks), raising=False)
    private_payload = "private-mail-content"
    with store._connect() as db:
        db.execute("select ?", (private_payload,)).fetchone()
    assert "elapsed_seconds=5.000" in caplog.text
    assert "test_slow_connection_names_caller" in caplog.text
    assert private_payload not in caplog.text
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("select 1")


def test_slow_direct_action_claim_reports_only_stage_timings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import time

    store = EmailStore(tmp_path / "slow-direct-action-claim.sqlite3")

    def slow_empty_probe(self, db, claimed_at, account_ids):
        time.sleep(1.01)
        return None

    monkeypatch.setattr(EmailStore, "_next_direct_action_row", slow_empty_probe)

    assert store.claim_next_direct_action(
        claimed_at="2026-10-11T00:00:00+00:00",
    ) is None
    assert "slow direct email action claim" in caplog.text
    assert "preflight_seconds=" in caplog.text
    assert "writer_lock_wait_seconds=0.000" in caplog.text
    assert "locked_recheck_seconds=0.000" in caplog.text
    assert "outcome=no_action" in caplog.text


@pytest.mark.skipif(
    not os.path.isdir("/dev/fd"), reason="process fd listing requires /dev/fd"
)
def test_read_loop_does_not_accumulate_file_descriptors(tmp_path: Path) -> None:
    store = EmailStore(tmp_path / "fd-growth.sqlite3")
    store.create_account(_fd_regression_account_values())
    gc.collect()
    _close_idle_connections()
    gc.disable()
    try:
        before = len(os.listdir("/dev/fd"))
        for _ in range(50):
            store.get_account("fd-regression-account")
            store.list_accounts()
            store.get_email_unsubscribe_state_snapshot("missing-identity")
        after = len(os.listdir("/dev/fd"))
    finally:
        gc.enable()
    assert after == before


_UNSUBSCRIBE_ENTRY_URL = (
    "https://r.openai.com/asm/unsubscribe"
    "?token=abc123def456ghi789jkl012mno345pqr678stu901"
)


def _entry_reference_for(url: str) -> str:
    return "unsubscribe-entry:" + sha256(url.encode("utf-8")).hexdigest()


def test_unsubscribe_receipt_records_the_entry_url_it_was_earned_against(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    authorization = _unsubscribe_authorization(
        store, entry_reference=_entry_reference_for(_UNSUBSCRIBE_ENTRY_URL)
    )
    claim = store.claim_email_unsubscribe_write(
        **authorization, owner=_UNSUBSCRIBE_OWNER_A
    )
    assert claim is not None and claim["acquired"] is True

    receipt = store.persist_email_unsubscribe_terminal(
        **authorization,
        entry_url=_UNSUBSCRIBE_ENTRY_URL,
        outcome="skipped_no_reliable_entry",
        receipt_id="unsubscribe-receipt:entry-url",
        evidence="page-not-operable",
        final_step={
            "sequence": 1,
            "operation": "open_entry",
            "state": "skipped_no_reliable_entry",
            "reference": "unsubscribe-receipt:entry-url",
        },
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )

    # Without this the outcome named a host and no address, so no reviewer
    # could open what the run opened: the URL is stored nowhere else and the
    # message body the candidate came from is not kept.
    assert receipt["entry_url"] == _UNSUBSCRIBE_ENTRY_URL
    persisted = store.get_email_unsubscribe_receipt(
        str(authorization["action_identity"])
    )
    assert persisted is not None
    assert persisted["entry_url"] == _UNSUBSCRIBE_ENTRY_URL


def test_unsubscribe_receipt_accepts_every_entry_shape_the_reference_allows(
    tmp_path: Path,
):
    # unsubscribe_entry_reference mints digests for private HTTPS, loopback
    # HTTP and mailto entries. A second scheme rule here could only disagree
    # with it, and one did: it rejected loopback entries and turned the whole
    # unsubscribe into unsubscribe_operation_rejected.
    for index, url in enumerate(
        (
            "http://127.0.0.1:8123/unsubscribe?token=loopback",
            "mailto:unsubscribe@news.example.com?subject=stop",
        )
    ):
        store = EmailStore(tmp_path / f"email-{index}.sqlite3")
        authorization = _unsubscribe_authorization(
            store, entry_reference=_entry_reference_for(url)
        )
        claim = store.claim_email_unsubscribe_write(
            **authorization, owner=_UNSUBSCRIBE_OWNER_A
        )
        assert claim is not None and claim["acquired"] is True

        receipt = store.persist_email_unsubscribe_terminal(
            **authorization,
            entry_url=url,
            outcome="done",
            receipt_id=f"unsubscribe-receipt:shape-{index}",
            evidence="terminal-page",
            claim_owner=_UNSUBSCRIBE_OWNER_A,
        )

        assert receipt["entry_url"] == url


def test_unsubscribe_receipt_rejects_an_entry_url_it_is_not_bound_to(tmp_path: Path):
    store = EmailStore(tmp_path / "email.sqlite3")
    authorization = _unsubscribe_authorization(
        store, entry_reference=_entry_reference_for(_UNSUBSCRIBE_ENTRY_URL)
    )
    claim = store.claim_email_unsubscribe_write(
        **authorization, owner=_UNSUBSCRIBE_OWNER_A
    )
    assert claim is not None and claim["acquired"] is True

    with pytest.raises(ValueError, match="entry reference"):
        store.persist_email_unsubscribe_terminal(
            **authorization,
            entry_url="https://r.openai.com/asm/unsubscribe?token=another-address",
            outcome="done",
            receipt_id="unsubscribe-receipt:mismatch",
            evidence="terminal-page",
            claim_owner=_UNSUBSCRIBE_OWNER_A,
        )


def test_unsubscribe_receipt_has_no_entry_url_when_no_entry_was_selected(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    authorization = _unsubscribe_authorization(store)
    claim = store.claim_email_unsubscribe_write(
        **authorization, owner=_UNSUBSCRIBE_OWNER_A
    )
    assert claim is not None and claim["acquired"] is True

    receipt = store.persist_email_unsubscribe_terminal(
        **authorization,
        outcome="skipped_no_reliable_entry",
        receipt_id="unsubscribe-receipt:no-entry",
        evidence="entry-selection",
        claim_owner=_UNSUBSCRIBE_OWNER_A,
    )

    # An entry that was never selected has no URL to record.
    assert receipt["entry_url"] == ""


def test_a_skipped_attempt_is_terminal_and_schedules_no_retry(tmp_path: Path):
    """A gone message ends the action without a retry or an error."""
    database = tmp_path / "skipped-attempt.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    action_id = _fetchall(database, f"select action_id from ({EMAIL_DIRECT_ACTION_SQL})")[0][
        "action_id"
    ]

    attempt = store.append_action_attempt(
        action_id=action_id,
        attempt_number=1,
        status="skipped",
        provider_operation="readback_missing",
        provider_target="dingtalk-account:message-id:<msg-1@example.com>",
        provider_result_id="message-unavailable:abc123",
        error="",
        started_at="2026-09-16T07:27:18+00:00",
        finished_at="2026-09-16T07:27:36+00:00",
    )

    assert attempt["status"] == "skipped"
    assert attempt["provider_result_id"] == "message-unavailable:abc123"
    assert attempt["error"] == ""


def test_a_skipped_attempt_still_requires_a_receipt_and_no_error(tmp_path: Path):
    database = tmp_path / "skipped-receipt.sqlite3"
    store = EmailStore(database)
    _persist_scan(
        store,
        _classification(status=EmailClassificationStatus.PROCESSED),
    )
    action = store.claim_next_direct_action(claimed_at="2026-09-16T07:27:13+00:00")
    assert action is not None

    with pytest.raises(ValueError, match="skipped attempts require"):
        store.complete_direct_action_attempt(
            action,
            status="skipped",
            provider_operation="readback_missing",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error="",
            finished_at="2026-09-16T07:27:36+00:00",
        )


def test_exhausted_interrupted_claim_recovers_after_retry_ceiling(tmp_path: Path):
    """A restart that interrupts the claim must not end the action."""
    store = EmailStore(tmp_path / "exhausted-interrupted-claim.sqlite3")
    _persist_scan(
        store,
        _classification(
            status=EmailClassificationStatus.PROCESSED,
            actions=(EmailAction.MOVE,),
            action_parameters={EmailAction.MOVE: {"target_folder": "Legal"}},
        ),
    )
    claim_times = (
        "2026-09-07T12:00:00+00:00",
        "2026-09-07T12:01:00+00:00",
        "2026-09-07T12:03:00+00:00",
    )
    for attempt_number, claimed_at in enumerate(claim_times, start=1):
        move = store.claim_next_direct_action(claimed_at=claimed_at)
        assert move is not None
        assert move.attempt_number == attempt_number
        store.recover_stale_processing_actions(
            stale_before="2026-09-07T23:00:00+00:00",
            recovered_at=claimed_at,
        )

    exhausted = _fetchall(
        tmp_path / "exhausted-interrupted-claim.sqlite3",
        f"select status, attempt_count, error from ({EMAIL_DIRECT_ACTION_SQL})",
    )[0]
    assert exhausted["status"] == "failed"
    assert exhausted["error"] == "stale_processing_recovered"

    retry = store.claim_next_direct_action(claimed_at="2026-09-08T12:00:00+00:00")

    assert retry is not None
    assert retry.action_type is EmailAction.MOVE
    assert retry.attempt_number == 4


def _enable_category_with_binding(store: EmailStore, category_key: str, account_id: str):
    store.upsert_verified_folder_binding(
        category_key,
        _task2_verified_binding(
            account_id=account_id,
            folder_id=f"{category_key}-folder",
            folder_name=f"{category_key}-folder",
            folder_role=FolderRole.TRASH if category_key == "junk" else FolderRole.UNBOUND,
        ),
    )
    config = store.get_category_config(category_key)
    return store.update_category_descriptions(
        category_key,
        core_description=config["core_description"],
        include=config["include"],
        exclude=config["exclude"],
        threshold=config["threshold"],
        enabled=True,
        description_version=config["description_version"],
        config_version=f"{category_key}-enabled-for-{account_id}",
    )


def test_a_verified_second_mailbox_gets_its_categories_switched_back_on(
    tmp_path: Path,
):
    """Adding a mailbox pauses the categories; verifying its folders resumes them."""

    store = EmailStore(tmp_path / "restore-after-binding.sqlite3")
    store.create_account(_task2_account_values("primary"))
    for category_key in ("work", "junk"):
        _enable_category_with_binding(store, category_key, "primary")

    _created, snapshot = store.create_account_with_category_enablement_snapshot(
        _task2_account_values("second")
    )

    assert [state.category_key for state in snapshot.states] == ["junk", "work"]
    assert store.get_category_config("work")["enabled"] is False
    assert store.categories_missing_active_bindings("second") == ("junk", "work")

    for category_key in ("work", "junk"):
        store.upsert_verified_folder_binding(
            category_key,
            _task2_verified_binding(
                account_id="second",
                folder_id=f"{category_key}-folder",
                folder_name=f"{category_key}-folder",
                folder_role=(
                    FolderRole.TRASH if category_key == "junk" else FolderRole.UNBOUND
                ),
            ),
        )

    assert store.restore_category_enablement_with_complete_bindings(snapshot) == ()
    assert store.get_category_config("work")["enabled"] is True
    assert store.get_category_config("junk")["enabled"] is True
    assert store.categories_missing_active_bindings("second") == ()


def test_a_category_whose_folder_stays_unverified_is_not_switched_back_on(
    tmp_path: Path,
):
    store = EmailStore(tmp_path / "restore-partial-binding.sqlite3")
    store.create_account(_task2_account_values("primary"))
    for category_key in ("work", "junk"):
        _enable_category_with_binding(store, category_key, "primary")

    _created, snapshot = store.create_account_with_category_enablement_snapshot(
        _task2_account_values("second")
    )
    store.upsert_verified_folder_binding(
        "work",
        _task2_verified_binding(
            account_id="second", folder_id="work-folder", folder_name="work-folder"
        ),
    )
    store.upsert_verified_folder_binding(
        "junk",
        _task2_verified_binding(
            account_id="second", folder_id="", folder_name="junk-folder", status="error"
        ),
    )

    assert store.restore_category_enablement_with_complete_bindings(snapshot) == ("junk",)
    assert store.get_category_config("work")["enabled"] is True
    assert store.get_category_config("junk")["enabled"] is False
    assert store.categories_missing_active_bindings("second") == ("junk",)


def test_a_training_label_correction_never_moves_or_trashes_the_message(
    tmp_path: Path,
):
    """Relabelling reviewed training data must not act on mail filed long ago."""

    database = tmp_path / "label-only-correction.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("dingtalk-account"))
    _enable_category_with_binding(store, "junk", "dingtalk-account")
    corrected = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="label-only",
        actions=(),
        action_parameters={},
    )
    reclassified = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="console-reclassified",
        actions=(),
        action_parameters={},
    )
    for classification in (corrected, reclassified):
        _persist_scan(store, classification)

    # A console reclassification to junk derives trash from the binding...
    console = store.apply_human_classification(
        reclassified.classification_id,
        EmailCategory.JUNK,
        feedback_request_id="console-junk",
        expected_current_action_plan_id=store.get_classification(
            reclassified.classification_id
        )["current_action_plan_id"],
    )
    assert console is not None
    # ...a training correction to the same category records the label only.
    application = store.correct_training_label(
        corrected.classification_id,
        EmailCategory.JUNK,
        feedback_request_id="training-review-junk",
        expected_current_action_plan_id=store.get_classification(
            corrected.classification_id
        )["current_action_plan_id"],
    )

    assert application is not None
    with sqlite3.connect(database) as db:
        actions_by_classification = dict(
            db.execute(
                f"select classification_id, count(*) from ({EMAIL_DIRECT_ACTION_SQL}) "
                "group by classification_id"
            ).fetchall()
        )
    assert actions_by_classification.get(reclassified.classification_id, 0) > 0
    assert actions_by_classification.get(corrected.classification_id, 0) == 0
    updated = store.get_classification(corrected.classification_id)
    assert updated["confirmed_category"] == "junk"
    assert updated["classification_source"] == "user"
    assert updated["action_plan"]["actions"] == []


def test_a_second_copy_of_a_filed_message_gets_the_same_decision(tmp_path: Path):
    """A duplicate left in the inbox used to read as handled and sit there."""

    database = tmp_path / "duplicate-copy.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("dingtalk-account"))
    _enable_category_with_binding(store, "work", "dingtalk-account")
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="duplicate",
        category=EmailCategory.WORK,
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "work-folder"}},
    )
    _persist_scan(store, classification)
    filed = store.get_classification(classification.classification_id)
    # The first copy was filed into 工作 and its move finished there.
    first = store.claim_next_direct_action(
        claimed_at="2026-09-18T06:00:00+00:00", account_ids=("dingtalk-account",)
    )
    assert first is not None
    store.complete_direct_action_attempt(
        first,
        status="done",
        provider_operation="UID MOVE",
        provider_target=first.locator.stable_message_identity,
        provider_result_id="moved-to-work",
        error="",
        finished_at="2026-09-18T06:00:05+00:00",
        updated_locator=StoredEmailLocator(
            account_id=first.locator.account_id,
            folder="工作",
            uidvalidity=int(filed["uidvalidity"]),
            uid=173,
            stable_message_identity=first.locator.stable_message_identity,
            rfc_message_id=first.locator.rfc_message_id,
            thread_id=first.locator.thread_id,
        ),
    )
    inbox_copy = EmailProviderLocator(
        account_id=filed["account_id"],
        folder="INBOX",
        uidvalidity=int(filed["uidvalidity"]),
        uid=32777,
        rfc_message_id=filed["rfc_message_id"],
    )

    updated = store.reapply_classification_to_copy(
        filed["stable_message_identity"], inbox_copy
    )

    assert updated is not None
    assert (updated["folder"], updated["uid"]) == ("INBOX", 32777)
    assert updated["category"] == "work"
    # The stored message and the plan must agree with the moved classification,
    # which durable validation checks on every open.
    reopened = EmailStore(database)
    assert reopened.get_classification(classification.classification_id)["uid"] == 32777
    claimed = store.claim_next_direct_action(
        claimed_at="2026-09-18T07:00:00+00:00", account_ids=("dingtalk-account",)
    )
    assert claimed is not None
    assert claimed.action_type is EmailAction.MOVE
    assert claimed.locator.folder == "INBOX" and claimed.locator.uid == 32777
    # The same copy twice is not new work.
    assert store.reapply_classification_to_copy(
        filed["stable_message_identity"],
        EmailProviderLocator(
            account_id=filed["account_id"], folder="INBOX",
            uidvalidity=int(filed["uidvalidity"]), uid=32777,
            rfc_message_id=filed["rfc_message_id"],
        ),
    ) is None


def test_a_copy_is_left_alone_while_the_previous_one_still_has_work(tmp_path: Path):
    database = tmp_path / "duplicate-inflight.sqlite3"
    store = EmailStore(database)
    store.create_account(_task2_account_values("dingtalk-account"))
    _enable_category_with_binding(store, "work", "dingtalk-account")
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="inflight-duplicate",
        category=EmailCategory.WORK,
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "work-folder"}},
    )
    _persist_scan(store, classification)
    filed = store.get_classification(classification.classification_id)

    assert store.reapply_classification_to_copy(
        filed["stable_message_identity"],
        EmailProviderLocator(
            account_id=filed["account_id"], folder="INBOX",
            uidvalidity=int(filed["uidvalidity"]), uid=99,
            rfc_message_id=filed["rfc_message_id"],
        ),
    ) is None


def test_work_mail_keeps_its_important_flag_as_training_evidence(tmp_path: Path):
    """Mail that needs a decision stays important; notifications do not."""

    from app.email_classifier_agent import DurableAgentClassificationResult

    database = tmp_path / "important-work-label.sqlite3"
    store = EmailStore(database)
    classification = _classification(status=EmailClassificationStatus.PROCESSED)
    _persist_scan(store, classification)
    assert classification.action_plan is not None
    created_at = classification.action_plan.created_at + timedelta(minutes=2)
    agent_result = DurableAgentClassificationResult(
        category="work",
        important=True,
        certainty="certain",
        confidence=classification.action_plan.confidence,
        reason="A customer is waiting on a decision.",
        unsubscribe_candidate_index=None,
        unsubscribe_candidate_source=None,
        unsubscribe_candidate_digest=None,
        unsubscribe_candidate_reference=None,
    )
    plan = EmailActionPlan.model_validate(
        {
            "action_plan_id": _action_plan_identity(
                action_plan_version=2,
                classification_id=classification.classification_id,
                account_id=classification.action_plan.account_id,
                category=EmailCategory.WORK,
                classification_source="agent",
                confidence=classification.action_plan.confidence,
                model_id="email-classifier-agent:v1",
                config_version="email-agent-correction-v1",
                actions=(EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
                action_parameters={EmailAction.MOVE: {"target_folder": "工作"}},
                created_at=created_at,
            ),
            "action_plan_version": 2,
            "classification_id": classification.classification_id,
            "account_id": classification.action_plan.account_id,
            "category": EmailCategory.WORK,
            "classification_source": "agent",
            "confidence": classification.action_plan.confidence,
            "model_id": "email-classifier-agent:v1",
            "config_version": "email-agent-correction-v1",
            "actions": (EmailAction.MOVE, EmailAction.FLAG_IMPORTANT),
            "action_parameters": {EmailAction.MOVE: {"target_folder": "工作"}},
            "created_at": created_at,
        }
    )
    store.append_action_plan_version(
        classification.classification_id,
        plan,
        confirmed_category=EmailCategory.WORK,
        agent_result=agent_result,
    )

    records = store.list_selected_training_records()

    assert [row["category_key"] for row in records] == ["work"]
    assert records[0]["important"] is True


def test_pending_training_count_preserves_eligibility_without_loading_texts(tmp_path):
    store = EmailStore(tmp_path / "training-count.sqlite3")
    for name, model_text, confirm in [
        ("eligible", "__subject__eligible", True),
        ("empty", "", True),
        ("included", "__subject__included", True),
        ("pending", "__subject__pending", False),
    ]:
        value = store.upsert_classification(
            _classification(status=EmailClassificationStatus.PENDING_FEEDBACK, message_id=name),
            model_text=model_text,
        )
        if confirm:
            _confirm(store, value["id"], EmailCategory.WORK)
        if name == "included":
            with store._connect() as db:
                db.execute("update emails set class_included_in_model_id='existing-model' where classification_id=?", (value["id"],))
    assert store.count_unincluded_training_examples() == 1
