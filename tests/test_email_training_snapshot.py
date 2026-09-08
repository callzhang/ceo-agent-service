from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

import app.email_training_snapshot as snapshot_module
from app.email_important import ImportantSignals
from app.email_training_snapshot import (
    EmailTrainingSnapshotPublicationJob,
    MODEL_INPUT_SCHEMA_VERSION,
    FolderTrainingSnapshotError,
    build_folder_training_snapshot,
)
from app.email_classifier_learning import EmailClassifierLearningService
from app.email_classifier_retrain import TrainingSubprocessRun
from app.email_model_registry import EmailModelRegistry
from app.email_store import EmailStore


OBSERVED_AT = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)


def _message(identity: str, **overrides):
    value = {
        "account_id": "account-a",
        "stable_message_identity": identity,
        "provider_folder_id": "folder-work",
        "provider_folder_name": "Work",
        "folder_role": "category",
        "bound_category_key": "work",
        "folder_binding_status": "active",
        "processed_by_email_service": True,
        "important_signals": ImportantSignals((), False),
        "sender": {"name": "Sender", "email": "sender@example.test"},
        "to_recipients": [{"name": "Derek", "email": "derek@example.test"}],
        "cc_recipients": [],
        "subject": f"Subject {identity}",
        "body": f"Body {identity}",
        "headers": {"message-id": f"<{identity}@example.test>"},
        "attachments": [],
        "provider_thread_id": f"thread-{identity}",
        "explicit_matter_group": None,
        "source": "natural",
        "received_at": "2026-09-07T12:00:00+00:00",
        "historical_predicted_category": "legal",
        "historical_confirmed_category": "financing",
    }
    value.update(overrides)
    return value


def _snapshot(
    messages,
    *,
    proposed_splits=None,
    snapshot_id="snapshot-1",
    seed=17,
    observed_at=OBSERVED_AT,
):
    return build_folder_training_snapshot(
        messages,
        snapshot_id=snapshot_id,
        description_version="description-v3",
        observed_at=observed_at,
        seed=seed,
        proposed_splits=proposed_splits,
    )


def _by_id(snapshot):
    return {row.stable_message_identity: row for row in snapshot.observations}


def test_production_snapshot_job_builds_persists_and_triggers_with_real_store(
    tmp_path,
) -> None:
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(tmp_path / "email.sqlite3") as db:
        db.execute("update email_category_configs set enabled=0")
        db.execute(
            "update email_category_configs set enabled=1 where category_key='work'"
        )
    registry = EmailModelRegistry(tmp_path / "email-models")

    class Controller:
        def __init__(self):
            self.starts = []

        def start(self, *, now, signal, snapshot_id):
            self.starts.append((signal, snapshot_id))
            return TrainingSubprocessRun(
                run_id="snapshot-job-run",
                status="running",
                pid=7,
                started_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                folder_label_watermark=signal.folder_label_watermark,
                important_label_watermark=signal.important_label_watermark,
            )

    controller = Controller()
    learning = EmailClassifierLearningService(
        store,
        registry=registry,
        retrain_state_path=registry.root / "retrain-state.json",
        controller=controller,
    )
    observations = tuple(
        _message(
            f"work-{index}",
            folder_role="category",
            provider_folder_id="folder-work",
            provider_folder_name="Work",
            bound_category_key="work",
            processed_by_email_service=True,
            important_signals=ImportantSignals(
                ("STARRED",) if index % 2 else (), bool(index % 2)
            ),
            sender={"name": f"Sender {index}", "email": f"sender-{index}@example.test"},
            subject=f"Unique junk token {chr(65 + index % 26)}-{index}",
        )
        for index in range(100)
    )
    job = EmailTrainingSnapshotPublicationJob(
        store=store,
        learning_service=learning,
        observation_loader=lambda: observations,
        description_version_loader=lambda: "description-set-v1",
        seed=20260905,
    )

    result = job.run_once(now=OBSERVED_AT)

    stored = store.get_training_snapshot(result.snapshot["snapshot_id"])
    assert stored is not None
    assert stored["snapshot_digest"] == result.snapshot["snapshot_digest"]
    assert result.retrain.decision.reason == "first_minimum_ready_snapshot"
    assert controller.starts[0][1] == stored["snapshot_id"]


def test_snapshot_observation_event_deduplicates_identical_provider_state(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    published = []

    class Learning:
        registry = SimpleNamespace(root=tmp_path / "registry")

        def publish_training_snapshot(self, snapshot, *, now):
            published.append(snapshot)
            return SimpleNamespace(snapshot=snapshot.to_dict(), retrain="checked")

    observations = (_message("message-1"),)
    job = EmailTrainingSnapshotPublicationJob(
        store=store,
        learning_service=Learning(),
        observation_loader=lambda: pytest.fail("event path must not scan provider"),
        description_version_loader=lambda: "descriptions-v1",
    )

    first = job.publish_observations(observations, now=OBSERVED_AT)
    second = job.publish_observations(
        observations, now=OBSERVED_AT + timedelta(minutes=5)
    )

    assert first.deduplicated is False
    assert first.publication is not None
    assert second.deduplicated is True
    assert second.publication is None
    assert len(published) == 1


def test_snapshot_event_recovers_claimed_crash_without_duplicate_publication(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    starts = []

    class Learning:
        registry = SimpleNamespace(root=tmp_path / "registry")

        def __init__(self):
            self.crash = True

        def publish_training_snapshot(self, snapshot, *, now):
            store.persist_training_snapshot(snapshot)
            if self.crash:
                self.crash = False
                raise RuntimeError("crash after durable snapshot")
            pytest.fail("snapshot publication must not repeat")

        def observe_snapshot_and_maybe_retrain(self, *, now):
            starts.append(now)
            return "triggered-once"

    learning = Learning()
    job = EmailTrainingSnapshotPublicationJob(
        store=store,
        learning_service=learning,
        observation_loader=lambda: (),
        description_version_loader=lambda: "descriptions-v1",
    )
    observations = (_message("message-crash"),)

    with pytest.raises(RuntimeError, match="crash after durable snapshot"):
        job.publish_observations(observations, now=OBSERVED_AT)
    recovered = job.publish_observations(
        observations, now=OBSERVED_AT + timedelta(minutes=1)
    )
    replay = job.publish_observations(
        observations, now=OBSERVED_AT + timedelta(minutes=2)
    )

    assert recovered.publication == "triggered-once"
    assert recovered.deduplicated is False
    assert replay.deduplicated is True
    assert len(starts) == 1


def test_pending_trigger_crash_replay_keeps_one_snapshot_and_one_durable_trigger(
    tmp_path, monkeypatch
):
    store = EmailStore(tmp_path / "email.sqlite3")
    calls = []

    class Learning:
        registry = SimpleNamespace(root=tmp_path / "registry")

        def publish_training_snapshot(self, snapshot, *, now):
            calls.append("publish")
            stored = store.persist_training_snapshot(snapshot)
            return SimpleNamespace(
                snapshot=stored,
                retrain=SimpleNamespace(),
                pending_trigger=True,
            )

        def observe_snapshot_and_maybe_retrain(self, *, now):
            calls.append("recover-pending")
            return SimpleNamespace(pending_trigger=True)

    original_save = snapshot_module._save_observation_event
    crash = {"armed": True}

    def crash_before_pending_status(path, value):
        if value.get("status") == "pending-trigger" and crash["armed"]:
            crash["armed"] = False
            raise RuntimeError("crash before pending event status")
        original_save(path, value)

    monkeypatch.setattr(
        snapshot_module, "_save_observation_event", crash_before_pending_status
    )
    job = EmailTrainingSnapshotPublicationJob(
        store=store,
        learning_service=Learning(),
        observation_loader=lambda: (),
        description_version_loader=lambda: "descriptions-v1",
    )
    observations = (_message("message-pending-crash"),)

    with pytest.raises(RuntimeError, match="crash before pending event status"):
        job.publish_observations(observations, now=OBSERVED_AT)
    recovered = job.publish_observations(
        observations, now=OBSERVED_AT + timedelta(minutes=1)
    )
    replay = job.publish_observations(
        observations, now=OBSERVED_AT + timedelta(minutes=2)
    )

    assert recovered.deduplicated is False
    assert replay.deduplicated is True
    assert calls == ["publish", "recover-pending"]
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select count(*) from email_training_snapshots"
        ).fetchone() == (1,)


def test_observation_event_publishes_folder_important_and_description_changes(
    tmp_path,
):
    published = []
    description = ["descriptions-v1"]

    class Learning:
        registry = SimpleNamespace(root=tmp_path / "registry")

        def publish_training_snapshot(self, snapshot, *, now):
            published.append(snapshot)
            return snapshot.snapshot_id

    job = EmailTrainingSnapshotPublicationJob(
        store=EmailStore(tmp_path / "email.sqlite3"),
        learning_service=Learning(),
        observation_loader=lambda: (),
        description_version_loader=lambda: description[0],
    )
    baseline = _message("message-change")
    job.publish_observations((baseline,), now=OBSERVED_AT)
    job.publish_observations(
        (
            _message(
                "message-change",
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
            ),
        ),
        now=OBSERVED_AT + timedelta(minutes=1),
    )
    job.publish_observations(
        (
            _message(
                "message-change",
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
                important_signals=ImportantSignals(("STARRED",), True),
            ),
        ),
        now=OBSERVED_AT + timedelta(minutes=2),
    )
    description[0] = "descriptions-v2"
    job.publish_observations(
        (
            _message(
                "message-change",
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
                important_signals=ImportantSignals(("STARRED",), True),
            ),
        ),
        now=OBSERVED_AT + timedelta(minutes=3),
    )

    assert [item.description_version for item in published] == [
        "descriptions-v1",
        "descriptions-v1",
        "descriptions-v1",
        "descriptions-v2",
    ]


def test_current_bound_folder_wins_over_historical_prediction_and_confirmation():
    snapshot = _snapshot([_message("message-1")])

    assert snapshot.observations[0].category_key == "work"


def test_folder_move_changes_only_the_next_snapshot_label():
    first = _snapshot([_message("message-1")], snapshot_id="snapshot-1")
    second = _snapshot(
        [
            _message(
                "message-1",
                provider_folder_id="folder-legal",
                provider_folder_name="Legal",
                bound_category_key="legal",
            )
        ],
        snapshot_id="snapshot-2",
    )

    assert first.observations[0].category_key == "work"
    assert second.observations[0].category_key == "legal"
    assert first.snapshot_digest != second.snapshot_digest
    with pytest.raises(FrozenInstanceError):
        first.observations[0].category_key = "legal"  # type: ignore[misc]


@pytest.mark.parametrize("folder_role", ("junk", "trash"))
def test_all_date_junk_and_trash_are_eligible(folder_role):
    snapshot = _snapshot(
        [
            _message(
                f"old-{folder_role}",
                folder_role=folder_role,
                provider_folder_id=f"system-{folder_role}",
                provider_folder_name=folder_role.title(),
                bound_category_key=None,
                processed_by_email_service=False,
                received_at="1999-01-01T00:00:00+00:00",
            )
        ]
    )

    assert snapshot.observations[0].category_key == "junk"


@pytest.mark.parametrize("folder_role", ("inbox", "unbound"))
def test_inbox_and_unbound_have_no_category(folder_role):
    snapshot = _snapshot(
        [
            _message(
                f"message-{folder_role}",
                folder_role=folder_role,
                bound_category_key=None,
                processed_by_email_service=False,
            )
        ]
    )

    assert snapshot.observations[0].category_key is None
    assert snapshot.observations[0].selected_for_training is False


@pytest.mark.parametrize("folder_role", ("sent", "draft"))
def test_sent_and_draft_are_absent(folder_role):
    snapshot = _snapshot([_message(f"message-{folder_role}", folder_role=folder_role)])

    assert snapshot.observations == ()


def test_ordinary_category_requires_service_processing_even_when_folder_is_truth():
    snapshot = _snapshot(
        [_message("unprocessed-work", processed_by_email_service=False)]
    )

    assert snapshot.observations == ()


def test_inactive_folder_binding_has_no_category_label():
    snapshot = _snapshot(
        [
            _message(
                "inactive-binding",
                folder_binding_status="missing",
                processed_by_email_service=False,
            )
        ]
    )

    assert snapshot.observations[0].category_key is None
    assert snapshot.observations[0].selected_for_training is False


def test_important_uses_current_trusted_provider_union_and_junk_forces_false():
    snapshot = _snapshot(
        [
            _message(
                "important-work",
                important_signals=ImportantSignals(("STARRED",), True),
            ),
            _message(
                "important-junk",
                folder_role="trash",
                bound_category_key=None,
                processed_by_email_service=False,
                important_signals=ImportantSignals(("STARRED",), True),
            ),
        ]
    )

    rows = _by_id(snapshot)
    assert rows["important-work"].important is True
    assert rows["important-junk"].important is False


def test_model_input_has_attachment_metadata_but_never_bytes_or_content():
    secret = "ATTACHMENT-BYTES-MUST-NOT-APPEAR"
    snapshot = _snapshot(
        [
            _message(
                "attachment-message",
                attachments=[
                    {
                        "filename": "contract.pdf",
                        "mime_type": "application/pdf",
                        "size_bytes": 31415,
                        "inline": True,
                        "content_id": "cid-7",
                        "disposition": "inline",
                        "content": secret,
                        "bytes": secret.encode(),
                    }
                ],
            )
        ]
    )

    model_input = snapshot.observations[0].normalized_model_input
    payload = json.loads(model_input)
    assert payload["input_schema_version"] == MODEL_INPUT_SCHEMA_VERSION
    assert payload["attachment_count"] == 1
    assert payload["attachments"] == [
        {
            "content_id": "cid-7",
            "disposition": "inline",
            "filename": "contract.pdf",
            "inline": True,
            "mime_type": "application/pdf",
            "size_bytes": 31415,
        }
    ]
    assert secret not in model_input


def test_attachment_tie_order_does_not_change_input_or_snapshot_digest():
    first_attachment = {
        "filename": "image.png",
        "mime_type": "image/png",
        "size_bytes": 42,
        "inline": False,
        "content_id": "same-content-id",
        "disposition": "attachment",
    }
    second_attachment = {
        "filename": "image.png",
        "mime_type": "image/png",
        "size_bytes": 42,
        "inline": True,
        "content_id": "same-content-id",
        "disposition": "inline",
    }

    first = _snapshot(
        [
            _message(
                "attachment-order", attachments=[first_attachment, second_attachment]
            )
        ],
        snapshot_id="attachment-order-a",
    )
    second = _snapshot(
        [
            _message(
                "attachment-order", attachments=[second_attachment, first_attachment]
            )
        ],
        snapshot_id="attachment-order-b",
    )

    assert (
        first.observations[0].normalized_model_input_hash
        == second.observations[0].normalized_model_input_hash
    )
    assert first.snapshot_digest == second.snapshot_digest


def test_model_input_preserves_quoted_reply_body_and_approved_headers():
    quoted = "On Monday Alice wrote:\n> the signed amount is 42"
    snapshot = _snapshot(
        [
            _message(
                "quoted-message",
                body=f"My reply\n\n{quoted}",
                headers={
                    "Message-ID": "<quoted@example.test>",
                    "In-Reply-To": "<parent@example.test>",
                    "References": "<root@example.test> <parent@example.test>",
                    "X-Unapproved-Secret": "do-not-copy",
                },
            )
        ]
    )

    payload = json.loads(snapshot.observations[0].normalized_model_input)
    assert quoted in payload["body"]
    assert payload["headers"] == {
        "in-reply-to": "<parent@example.test>",
        "message-id": "<quoted@example.test>",
        "references": "<root@example.test> <parent@example.test>",
    }


def test_published_snapshot_never_persists_private_unsubscribe_value(tmp_path):
    private_token = "private-token-A-1234"
    private_url = f"https://unsubscribe.example.test/remove?token={private_token}"
    snapshot = _snapshot(
        [
            _message(
                "private-unsubscribe",
                headers={
                    "Message-ID": "<private-unsubscribe@example.test>",
                    "List-Unsubscribe": f"<{private_url}>, <mailto:leave@example.test>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            )
        ]
    )
    payload = json.loads(snapshot.observations[0].normalized_model_input)

    assert payload["headers"] == {"message-id": "<private-unsubscribe@example.test>"}
    assert payload["unsubscribe"] == {
        "has_unsubscribe": True,
        "hosts": ["example.test", "unsubscribe.example.test"],
        "one_click": True,
        "schemes": ["https", "mailto"],
        "value_sha256": snapshot_module.sha256(
            (
                f"<{private_url}>, <mailto:leave@example.test>\n"
                "List-Unsubscribe=One-Click"
            ).encode("utf-8")
        ).hexdigest(),
    }

    database_path = tmp_path / "email.sqlite3"
    EmailStore(database_path).persist_training_snapshot(snapshot)
    persisted_bytes = database_path.read_bytes()
    assert private_url.encode() not in persisted_bytes
    assert private_token.encode() not in persisted_bytes


def test_empty_systematically_invalid_model_input_is_rejected():
    with pytest.raises(FolderTrainingSnapshotError, match="model input is empty"):
        _snapshot(
            [
                _message(
                    "empty",
                    sender={},
                    to_recipients=[],
                    cc_recipients=[],
                    subject="",
                    body="",
                    headers={},
                    attachments=[],
                )
            ]
        )


def test_provider_duplicate_identity_uses_authoritative_bound_folder():
    snapshot = _snapshot(
        [
            _message(
                "duplicate",
                provider_folder_id="all-mail",
                provider_folder_name="All Mail",
                folder_role="unbound",
                bound_category_key=None,
                folder_binding_status=None,
                processed_by_email_service=False,
            ),
            _message(
                "duplicate",
                provider_folder_id="inbox",
                provider_folder_name="Inbox",
                folder_role="inbox",
                bound_category_key=None,
                folder_binding_status=None,
                processed_by_email_service=False,
            ),
            _message("duplicate"),
        ],
        proposed_splits={"duplicate": "train"},
    )

    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].provider_folder_id == "folder-work"
    assert snapshot.observations[0].category_key == "work"


def test_duplicate_identity_in_two_authoritative_category_folders_is_rejected():
    with pytest.raises(FolderTrainingSnapshotError, match="conflicting folder truth"):
        _snapshot(
            [
                _message("duplicate"),
                _message(
                    "duplicate",
                    provider_folder_id="folder-legal",
                    provider_folder_name="Legal",
                    bound_category_key="legal",
                ),
            ]
        )


@pytest.mark.parametrize(
    ("relationship", "overrides"),
    (
        ("thread", {"provider_thread_id": "shared-thread"}),
        ("body", {"body": "same normalized body"}),
        (
            "sender template",
            {
                "sender": {"email": "robot@example.test", "name": "Robot"},
                "subject": "Invoice 2026-0001",
            },
        ),
        ("matter", {"explicit_matter_group": "matter-7"}),
    ),
)
def test_related_group_cannot_leak_across_proposed_splits(relationship, overrides):
    first = _message("first", **overrides)
    second_overrides = dict(overrides)
    if relationship == "sender template":
        second_overrides["subject"] = "Invoice 2026-9999"
    second = _message("second", **second_overrides)

    with pytest.raises(FolderTrainingSnapshotError, match="group leakage"):
        _snapshot(
            [first, second],
            proposed_splits={"first": "train", "second": "test"},
        )


def test_explicit_group_unions_instead_of_overwriting_thread_relationships():
    messages = [
        _message(
            "a",
            provider_thread_id="thread-1",
            explicit_matter_group="matter-a",
        ),
        _message(
            "b",
            provider_thread_id="thread-1",
            explicit_matter_group="matter-b",
        ),
        _message(
            "c",
            provider_thread_id="thread-2",
            explicit_matter_group="matter-b",
        ),
    ]

    snapshot = _snapshot(
        messages,
        proposed_splits={
            message["stable_message_identity"]: "train" for message in messages
        },
    )

    assert len({row.group_key for row in snapshot.observations}) == 1
    assert len({row.split for row in snapshot.observations}) == 1


def test_same_inputs_and_config_have_same_digest_regardless_of_order_or_id():
    messages = [_message("b"), _message("a", source="targeted")]

    first = _snapshot(messages, snapshot_id="snapshot-a")
    second = _snapshot(list(reversed(messages)), snapshot_id="snapshot-b")

    assert first.snapshot_digest == second.snapshot_digest
    assert first.manifest["ordered_stable_ids"] == ["a", "b"]
    assert first.manifest["source_distribution"] == {
        "natural": 1,
        "targeted": 1,
    }


def test_observed_at_is_canonical_utc_and_changes_signed_snapshot_digest():
    same_instant_offset = OBSERVED_AT.astimezone(timezone(timedelta(hours=-7)))

    first = _snapshot([_message("message-1")], snapshot_id="observed-a")
    equivalent = _snapshot(
        [_message("message-1")],
        snapshot_id="observed-b",
        observed_at=same_instant_offset,
    )
    later = _snapshot(
        [_message("message-1")],
        snapshot_id="observed-c",
        observed_at=OBSERVED_AT + timedelta(seconds=1),
    )

    assert first.observed_at == "2026-09-07T18:00:00+00:00"
    assert first.snapshot_digest == equivalent.snapshot_digest
    assert first.snapshot_digest != later.snapshot_digest
    assert first.manifest["observed_at"] == first.observed_at


@pytest.mark.parametrize(
    "change",
    (
        {"provider_folder_name": "Renamed Work"},
        {"important_signals": ImportantSignals(("STARRED",), True)},
        {"body": "changed input"},
    ),
)
def test_folder_star_or_input_change_produces_new_digest(change):
    first = _snapshot([_message("message-1")], snapshot_id="snapshot-a")
    second = _snapshot([_message("message-1", **change)], snapshot_id="snapshot-b")

    assert first.snapshot_digest != second.snapshot_digest


def test_junk_training_is_downsampled_without_duplicate_bodies():
    messages = [
        _message("work-1"),
        _message("work-2"),
        *[
            _message(
                f"junk-{index}",
                folder_role="junk",
                bound_category_key=None,
                processed_by_email_service=False,
                body=f"junk body {index}",
                source="natural" if index % 2 else "targeted",
            )
            for index in range(8)
        ],
    ]

    snapshot = _snapshot(messages)
    selected_junk = [
        row
        for row in snapshot.observations
        if row.category_key == "junk" and row.selected_for_training
    ]

    assert len(selected_junk) <= 2
    assert len({row.normalized_body_digest for row in selected_junk}) == len(
        selected_junk
    )
    assert snapshot.manifest["training_source_distribution"] == {
        source: sum(
            row.selected_for_training and row.source == source
            for row in snapshot.observations
        )
        for source in ("natural", "targeted")
    }


def test_same_body_with_conflicting_categories_is_excluded_globally():
    messages = [
        _message(
            "conflict-work",
            body="identical evidence body",
            bound_category_key="work",
            sender={"name": "Work", "email": "work@example.test"},
            provider_thread_id="work-thread",
        ),
        _message(
            "conflict-legal",
            body="identical evidence body",
            bound_category_key="legal",
            sender={"name": "Legal", "email": "legal@example.test"},
            provider_thread_id="legal-thread",
        ),
    ]

    snapshot = _snapshot(
        messages,
        proposed_splits={
            message["stable_message_identity"]: "train" for message in messages
        },
    )

    assert snapshot.manifest["training_selection"] == []
    assert snapshot.manifest["observation_count"] == 2
    assert snapshot.manifest["selected_group_count"] == 0
    assert snapshot.manifest["selected_category_counts"] == {
        "legal": 0,
        "work": 0,
    }
    assert snapshot.manifest["conflicted_group_count"] == 1
    assert snapshot.manifest["conflicted_groups"] == [
        {
            "categories": ["legal", "work"],
            "group_key": snapshot.observations[0].group_key,
            "reason": "multiple_category_labels",
        }
    ]


def _independent_labeled_message(identity, category, *, source="natural"):
    if category == "junk":
        return _message(
            identity,
            folder_role="junk",
            bound_category_key=None,
            processed_by_email_service=False,
            sender={"name": identity, "email": f"{identity}@example.test"},
            subject=f"Unique {identity}",
            body=f"Unique body {identity}",
            provider_thread_id=f"thread-{identity}",
            source=source,
        )
    return _message(
        identity,
        bound_category_key=category,
        sender={"name": identity, "email": f"{identity}@example.test"},
        subject=f"Unique {identity}",
        body=f"Unique body {identity}",
        provider_thread_id=f"thread-{identity}",
        source=source,
    )


def test_training_selection_balances_every_category_to_minimum_group_count():
    messages = [
        *[_independent_labeled_message(f"work-{index}", "work") for index in range(5)],
        *[
            _independent_labeled_message(
                f"legal-{index}",
                "legal",
                source="targeted" if index == 0 else "natural",
            )
            for index in range(2)
        ],
        *[
            _independent_labeled_message(
                f"junk-{index}",
                "junk",
                source="targeted" if index % 2 == 0 else "natural",
            )
            for index in range(7)
        ],
    ]
    proposed = {message["stable_message_identity"]: "train" for message in messages}

    first = _snapshot(messages, proposed_splits=proposed, snapshot_id="balanced-a")
    second = _snapshot(
        list(reversed(messages)),
        proposed_splits=proposed,
        snapshot_id="balanced-b",
    )

    assert first.manifest["selected_category_counts"] == {
        "junk": 2,
        "legal": 2,
        "work": 2,
    }
    assert len(first.manifest["training_selection"]) == 6
    assert first.manifest["observation_count"] == 14
    assert first.manifest["selected_group_count"] == 6
    assert first.manifest["training_selection"] == second.manifest["training_selection"]
    assert first.snapshot_digest == second.snapshot_digest
    selected = [row for row in first.observations if row.selected_for_training]
    assert len({row.group_key for row in selected}) == len(selected)
    assert first.manifest["training_source_distribution"] == {
        source: sum(row.source == source for row in selected)
        for source in ("natural", "targeted")
    }


def test_hash_split_is_stable_when_a_later_snapshot_adds_a_train_group():
    existing = _independent_labeled_message("stable-4", "work")
    naturally_train = _independent_labeled_message("stable-0", "work")

    with pytest.raises(FolderTrainingSnapshotError, match="untrainable.*work"):
        _snapshot([existing], snapshot_id="before-train-peer", seed=17)

    later = _snapshot(
        [existing, naturally_train],
        snapshot_id="after-train-peer",
        seed=17,
    )

    assert _by_id(later)["stable-4"].split == "test"
    assert _by_id(later)["stable-0"].split == "train"


def test_fixed_hash_splits_produce_stable_equal_category_balance():
    messages = [
        _independent_labeled_message("stable-4", "work"),
        _independent_labeled_message("stable-0", "work"),
        _independent_labeled_message("stable-16", "legal"),
        _independent_labeled_message("stable-1", "legal"),
    ]

    first = _snapshot(messages, snapshot_id="stable-balance-a", seed=17)
    second = _snapshot(
        list(reversed(messages)),
        snapshot_id="stable-balance-b",
        seed=17,
    )

    assert {identity: row.split for identity, row in _by_id(first).items()} == {
        "stable-0": "train",
        "stable-1": "train",
        "stable-16": "validation",
        "stable-4": "test",
    }
    assert first.manifest["selected_category_counts"] == {
        "legal": 1,
        "work": 1,
    }
    assert first.manifest["training_selection"] == ["stable-0", "stable-1"]
    assert first.snapshot_digest == second.snapshot_digest


def test_explicit_split_with_zero_train_category_is_rejected_as_untrainable():
    with pytest.raises(FolderTrainingSnapshotError, match="untrainable.*legal"):
        _snapshot(
            [
                _message("work"),
                _message("legal", bound_category_key="legal"),
            ],
            proposed_splits={"work": "train", "legal": "test"},
        )
