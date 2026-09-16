from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from threading import Barrier, Lock, Thread
import time

import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    INITIAL_EMAIL_CATEGORY_KEYS,
)
from app.email_classifier_learning import (
    EmailClassifierLearningService,
    UnsupportedTrainingSelection,
    _selection_provenance,
)
from app.email_classifier_retrain import (
    RetrainPolicy,
    TrainingSubprocessController,
    TrainingSubprocessRun,
    load_retrain_state,
    save_retrain_state,
)
from app.email_model_registry import EmailModelRegistry
from app.email_store import EmailStore


CURRENT_EMAIL_CATEGORIES = tuple(
    EmailCategory(category) for category in INITIAL_EMAIL_CATEGORY_KEYS
)


def test_selection_provenance_rejects_empty_selection_by_default() -> None:
    class EmptySelectedSourceStore:
        def latest_training_snapshot_state(self):
            return {
                "snapshot_id": "snapshot-empty",
                "snapshot_sha": "a" * 64,
                "snapshot_version": "email-folder-snapshot.v1",
                "description_version": "description-v1",
            }

        def get_training_snapshot(self, snapshot_id: str):
            assert snapshot_id == "snapshot-empty"
            return {
                "observations": [
                    {"stable_message_identity": "message-1", "category_key": "legal"}
                ]
            }

        def list_training_examples(self, *, include_inclusion: bool):
            assert include_inclusion is True
            return []

    with pytest.raises(ValueError, match="category is unavailable"):
        _selection_provenance(
            EmptySelectedSourceStore(),
            sources=["user_feedback"],
            categories=["legal"],
        )

    preview = _selection_provenance(
        EmptySelectedSourceStore(),
        sources=["user_feedback"],
        categories=["legal"],
        allow_empty=True,
    )
    assert preview["selected_training_records"] == []
    assert preview["provenance"][0]["sample_count"] == 0


def _classification(message_id: str, category: EmailCategory) -> EmailClassification:
    classification_id = (
        int.from_bytes(sha256(message_id.encode("utf-8")).digest()[:8], "big")
        & ((1 << 63) - 1)
        or 1
    )
    return EmailClassification(
        classification_id=classification_id,
        stable_message_identity=f"test-account:imap:INBOX:1:{classification_id}",
        provider_locator=EmailProviderLocator(
            account_id="test-account",
            folder="INBOX",
            uidvalidity=1,
            uid=classification_id,
        ),
        category=category,
        confidence=0.61,
        margin=0.11,
        probabilities={category.value: 0.61},
        model_id="email/logistic/model-before-feedback",
        config_version="email-v1",
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        classification_source="model",
        action_plan=None,
    )


def _service_with_pending(
    tmp_path: Path,
    count: int = 1,
    *,
    minimum_new_examples: int = 5,
    categories: tuple[EmailCategory, ...] = (
        EmailCategory.WORK,
        EmailCategory.JUNK,
    ),
):
    store = EmailStore(tmp_path / "email.sqlite3")
    rows = []
    for index in range(count):
        category = categories[index % len(categories)]
        row = store.upsert_classification(
            _classification(f"message-{index}", category),
            model_text=f"__subject__sample-{index} __category__{category.value}",
        )
        rows.append(row)
    registry = EmailModelRegistry(tmp_path / "models")
    service = EmailClassifierLearningService(
        store,
        registry=registry,
        retrain_state_path=tmp_path / "models" / "retrain-state.json",
        policy=RetrainPolicy(minimum_new_examples=minimum_new_examples),
    )
    return service, store, rows, registry


class _ConcurrentController:
    def __init__(self):
        self._lock = Lock()
        self.starts = []

    def start(self, *, now):
        with self._lock:
            run_id = f"run-{len(self.starts) + 1}"
            self.starts.append(run_id)
        time.sleep(0.05)
        return TrainingSubprocessRun(
            run_id=run_id,
            status="running",
            pid=123,
            started_at=now.isoformat(),
            updated_at=now.isoformat(),
        )

    def poll(self, run_id, *, now):
        return TrainingSubprocessRun(
            run_id=run_id,
            status="running",
            pid=123,
            started_at=now.isoformat(),
            updated_at=now.isoformat(),
        )


def _confirm_all(service, rows, *, now):
    for row in rows:
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory(str(row["predicted_category"])),
            feedback_request_id=f"confirm-all-{row['id']}",
            expected_current_action_plan_id=None,
            now=now,
        )


def _parallel_calls(*calls):
    barrier = Barrier(len(calls) + 1)
    results = []
    failures = []

    def invoke(call):
        barrier.wait()
        try:
            results.append(call())
        except BaseException as exc:
            failures.append(exc)

    threads = [Thread(target=invoke, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    return results


def test_feedback_api_service_confirms_first_and_records_state_without_retraining(
    tmp_path: Path,
):
    service, store, rows, _ = _service_with_pending(tmp_path)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.LEGAL,
        feedback_request_id="learning-first-confirmation",
        expected_current_action_plan_id=None,
        now=now,
    )

    assert result is not None
    assert result.confirmed["classification_source"] == "user"
    assert result.retrain is not None
    assert result.retrain.decision.due is False
    assert result.error is None
    assert load_retrain_state(
        tmp_path / "models" / "retrain-state.json"
    ).last_feedback_at
    assert store.list_training_examples()[0]["label"] == "legal"


def test_manual_training_selection_rejects_unknown_sources(tmp_path: Path):
    service, _store, _rows, _ = _service_with_pending(tmp_path)

    with pytest.raises(UnsupportedTrainingSelection):
        service.request_manual_training(
            selection={
                "sources": ["not-a-training-source"],
                "categories": ["work", "legal"],
            }
        )


def test_manual_folder_selection_is_bound_to_the_training_run(tmp_path: Path):
    service, store, _rows, _ = _service_with_pending(tmp_path)
    snapshot = {
        "snapshot_id": "email-folder-snapshot-20260912T000000.000000Z-aaaaaaaaaaaa",
        "snapshot_sha": "a" * 64,
        "snapshot_version": "email-folder-training-snapshot-v1",
        "description_version": "description-set-sha256:" + "b" * 64,
        "input_schema_version": "email-folder-model-input-v2",
        "folder_label_watermark": 2,
        "important_label_watermark": 2,
        "minimum_ready": True,
        "observations": [
            {
                "stable_message_identity": "mail-1",
                "category_key": "work",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"mail one"}',
            },
            {
                "stable_message_identity": "mail-2",
                "category_key": "legal",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"mail two"}',
            },
        ],
    }
    store.latest_training_snapshot_state = lambda: snapshot
    store.get_training_snapshot = lambda _snapshot_id: snapshot

    class Controller:
        def __init__(self):
            self.selection = None

        def start(self, *, now, signal, snapshot_id, training_selection):
            self.selection = training_selection
            return TrainingSubprocessRun(
                run_id="selected-run",
                status="running",
                pid=123,
                started_at=now.isoformat(),
                updated_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                training_selection=training_selection,
            )

    controller = Controller()
    service.controller = controller
    result = service.request_manual_training(
        selection={
            "sources": ["folder_snapshot"],
            "categories": ["work"],
            "model_families": ["embedding-mlp"],
        }
    )

    assert result.training_run is not None
    assert result.decision.reason == "manual_selected"
    assert result.training_run.training_selection["categories"] == ["work"]
    assert result.training_run.training_selection["model_families"] == ["embedding-mlp"]
    assert controller.selection["provenance"][0]["sample_count"] == 1
    assert controller.selection["provenance"][0]["dataset_digest"]
    payload = json.loads(
        next((tmp_path / "models").glob("training-request-*.json")).read_text()
    )
    assert payload["run_id"] == "selected-run"
    assert payload["execution"] == "staged_candidate_training"


def test_manual_agent_and_user_selection_uses_selected_labels(
    tmp_path: Path, monkeypatch
):
    service, store, _rows, _ = _service_with_pending(tmp_path)
    snapshot = {
        "snapshot_id": "email-folder-snapshot-20260912T000000.000000Z-bbbbbbbbbbbb",
        "snapshot_sha": "c" * 64,
        "snapshot_version": "email-folder-training-snapshot-v1",
        "description_version": "description-set-sha256:" + "d" * 64,
        "input_schema_version": "email-folder-model-input-v3",
        "folder_label_watermark": 2,
        "important_label_watermark": 0,
        "minimum_ready": True,
        "observations": [
            {
                "stable_message_identity": "mail-user",
                "category_key": "work",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"folder user"}',
            },
            {
                "stable_message_identity": "mail-agent",
                "category_key": "legal",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"folder agent"}',
            },
        ],
    }
    store.latest_training_snapshot_state = lambda: snapshot
    store.get_training_snapshot = lambda _snapshot_id: snapshot
    store.list_selected_training_records = lambda: [
        {
            "source": "user_feedback",
            "stable_message_identity": "mail-user",
            "category_key": "legal",
            "account_id": "account-a",
            "normalized_model_input": '{"body":"user confirmation"}',
        },
        {
            "source": "agent_auto_label",
            "stable_message_identity": "mail-agent",
            "category_key": "work",
            "account_id": "account-a",
            "normalized_model_input": '{"body":"agent label"}',
        },
    ]
    monkeypatch.setattr(
        "app.email_classifier_learning.build_selected_training_snapshot",
        lambda *_, **__: object(),
    )
    store.persist_training_snapshot = lambda _: {
        "snapshot_id": "email-selected-training-test",
        "snapshot_digest": "e" * 64,
        "snapshot_version": "email-selected-training-snapshot-v1",
        "description_version": "selected-training-input-v1",
    }
    service.controller = type(
        "Controller",
        (),
        {
            "start": lambda self, **kwargs: TrainingSubprocessRun(
                run_id="source-selected-run",
                status="running",
                pid=1,
                started_at=kwargs["now"].isoformat(),
                updated_at=kwargs["now"].isoformat(),
                snapshot_id=kwargs["snapshot_id"],
                snapshot_sha=kwargs["signal"].snapshot_sha,
                description_version=kwargs["signal"].description_version,
                training_selection=kwargs["training_selection"],
            ),
        },
    )()

    result = service.request_manual_training(
        selection={
            "sources": ["agent_auto_label", "user_feedback"],
            "categories": ["work", "legal"],
            "model_families": ["embedding-mlp"],
        }
    )

    selection = result.training_run.training_selection
    assert selection["selected_message_identities"] == ["mail-agent", "mail-user"]
    by_source_category = {
        (row["source"], row["category"]): row["sample_count"]
        for row in selection["provenance"]
    }
    assert by_source_category[("user_feedback", "legal")] == 1
    assert by_source_category[("agent_auto_label", "work")] == 1


def test_manual_training_selection_is_idempotent_for_same_scope(tmp_path: Path):
    service, store, _rows, _ = _service_with_pending(tmp_path)
    snapshot = {
        "snapshot_id": "email-folder-snapshot-20260912T000000.000000Z-aaaaaaaaaaaa",
        "snapshot_sha": "a" * 64,
        "snapshot_version": "email-folder-training-snapshot-v1",
        "description_version": "description-set-sha256:" + "b" * 64,
        "input_schema_version": "email-folder-model-input-v2",
        "folder_label_watermark": 2,
        "important_label_watermark": 2,
        "minimum_ready": True,
        "observations": [
            {
                "stable_message_identity": "mail-1",
                "category_key": "work",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"mail one"}',
            }
        ],
    }
    store.latest_training_snapshot_state = lambda: snapshot
    store.get_training_snapshot = lambda _snapshot_id: snapshot
    service.controller = type(
        "Controller",
        (),
        {
            "start": lambda self, **kwargs: TrainingSubprocessRun(
                run_id="selected-run",
                status="running",
                pid=123,
                started_at=kwargs["now"].isoformat(),
                updated_at=kwargs["now"].isoformat(),
                snapshot_id=kwargs["snapshot_id"],
                snapshot_sha=kwargs["signal"].snapshot_sha,
                description_version=kwargs["signal"].description_version,
                training_selection=kwargs["training_selection"],
            ),
            "_load_run": lambda self, _run_id: TrainingSubprocessRun(
                run_id="selected-run",
                status="running",
                pid=123,
                started_at="2026-09-12T00:00:00+00:00",
                updated_at="2026-09-12T00:00:00+00:00",
            ),
        },
    )()
    selection = {
        "sources": ["folder_snapshot"],
        "categories": ["work"],
    }
    service.request_manual_training(selection=selection)
    service.request_manual_training(selection=selection)
    assert len(list((tmp_path / "models").glob("training-request-*.json"))) == 1


def test_manual_training_selection_retries_after_a_failed_run(tmp_path: Path):
    service, store, _rows, _ = _service_with_pending(tmp_path)
    snapshot = {
        "snapshot_id": "email-folder-snapshot-20260912T000000.000000Z-aaaaaaaaaaaa",
        "snapshot_sha": "a" * 64,
        "snapshot_version": "email-folder-training-snapshot-v1",
        "description_version": "description-set-sha256:" + "b" * 64,
        "input_schema_version": "email-folder-model-input-v2",
        "folder_label_watermark": 2,
        "important_label_watermark": 2,
        "minimum_ready": True,
        "observations": [
            {
                "stable_message_identity": "mail-1",
                "category_key": "work",
                "account_id": "account-a",
                "provider_thread_id": None,
                "normalized_model_input": '{"body":"mail one"}',
            }
        ],
    }
    store.latest_training_snapshot_state = lambda: snapshot
    store.get_training_snapshot = lambda _snapshot_id: snapshot
    starts = []

    class Controller:
        def start(self, **kwargs):
            starts.append(kwargs)
            return TrainingSubprocessRun(
                run_id=f"retry-{len(starts)}",
                status="running",
                pid=123,
                started_at=kwargs["now"].isoformat(),
                updated_at=kwargs["now"].isoformat(),
                snapshot_id=kwargs["snapshot_id"],
                snapshot_sha=kwargs["signal"].snapshot_sha,
                description_version=kwargs["signal"].description_version,
                training_selection=kwargs["training_selection"],
            )

        def _load_run(self, _run_id):
            return TrainingSubprocessRun(
                run_id="retry-1",
                status="failed",
                pid=123,
                started_at="2026-09-12T00:00:00+00:00",
                updated_at="2026-09-12T00:00:00+00:00",
            )

    service.controller = Controller()
    selection = {"sources": ["folder_snapshot"], "categories": ["work"]}

    service.request_manual_training(selection=selection)
    retry = service.request_manual_training(selection=selection)

    assert retry.decision.reason == "manual_selected"
    assert len(starts) == 2
    assert len(list((tmp_path / "models").glob("training-request-*.json"))) == 2


def test_learning_service_corrects_processed_classification_through_pipeline(
    tmp_path: Path,
):
    service, store, rows, _ = _service_with_pending(tmp_path)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    first = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.WORK,
        feedback_request_id="learning-correction-first",
        expected_current_action_plan_id=None,
        now=now,
    )
    assert first is not None
    first_plan_id = first.confirmed["current_action_plan_id"]
    store.upsert_config(
        category=EmailCategory.LEGAL,
        description="legal",
        threshold=0.97,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="important-learning-v2",
    )

    corrected = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.LEGAL,
        feedback_request_id="learning-correction-second",
        expected_current_action_plan_id=first_plan_id,
        now=now + timedelta(seconds=1),
    )

    assert corrected is not None
    assert corrected.error is None
    assert corrected.confirmed["confirmed_category"] == "legal"
    assert corrected.confirmed["current_action_plan_id"] != first_plan_id
    assert corrected.confirmed["action_plan"]["action_plan_version"] == 2
    assert store.list_training_examples()[0]["label"] == "legal"


def test_learning_service_exact_replay_does_not_record_or_request_retraining(
    tmp_path: Path,
):
    service, _store, rows, _ = _service_with_pending(tmp_path)
    first_at = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    first = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.LEGAL,
        feedback_request_id="learning-feedback-1",
        expected_current_action_plan_id=None,
        now=first_at,
    )
    state_path = tmp_path / "models" / "retrain-state.json"
    state_after_first = state_path.read_text(encoding="utf-8")

    replay = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.LEGAL,
        feedback_request_id="learning-feedback-1",
        expected_current_action_plan_id=None,
        now=first_at + timedelta(hours=1),
    )

    assert first is not None and first.feedback_applied is True
    assert replay is not None
    assert replay.feedback_applied is False
    assert replay.feedback_replayed is True
    assert replay.retrain is None
    assert state_path.read_text(encoding="utf-8") == state_after_first


def test_feedback_only_never_starts_snapshot_training(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(
        tmp_path,
        count=18,
        categories=CURRENT_EMAIL_CATEGORIES,
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    for row in rows:
        result = service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory(str(row["predicted_category"])),
            feedback_request_id=f"learning-batch-{row['id']}",
            expected_current_action_plan_id=None,
            now=now,
        )
        assert result is not None
        assert result.retrain is not None
        assert result.retrain.decision.due is False

    polled = service.poll_retrain(now=now + timedelta(seconds=31))
    assert polled.training_run is None
    assert registry.active_manifest() is None
    assert len(store.list_unincluded_training_examples()) == 18


def test_feedback_service_keeps_confirmation_when_training_is_not_ready(tmp_path: Path):
    service, store, rows, _ = _service_with_pending(tmp_path, minimum_new_examples=1)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"],
        EmailCategory.WORK,
        feedback_request_id="learning-not-ready",
        expected_current_action_plan_id=None,
        now=now,
    )

    assert result is not None
    assert result.confirmed["status"] == "processed"
    assert result.error is None
    assert store.list_training_examples()[0]["label"] == "work"


def test_partial_taxonomy_feedback_waits_without_repeated_training_launch(
    tmp_path: Path,
):
    class Controller:
        def __init__(self):
            self.starts = []

        def start(self, *, now):
            self.starts.append(now)
            return TrainingSubprocessRun(
                run_id="run-1",
                status="running",
                pid=123,
                started_at=now.isoformat(),
                updated_at=now.isoformat(),
            )

        def poll(self, run_id, *, now):
            raise AssertionError("new run should not be polled immediately")

    service, _store, rows, _registry = _service_with_pending(tmp_path, count=5)
    controller = Controller()
    service.controller = controller
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    for index, row in enumerate(rows):
        result = service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
            feedback_request_id=f"learning-runtime-tick-{row['id']}",
            expected_current_action_plan_id=None,
            now=now,
        )
        assert result is not None
        assert result.retrain is not None
        assert result.retrain.decision.due is False

    assert controller.starts == []
    tick = service.poll_retrain(now=now + timedelta(seconds=31))

    assert tick.training_run is None
    assert tick.decision.due is False
    assert tick.decision.reason is None
    assert controller.starts == []

    second_tick = service.poll_retrain(now=now + timedelta(seconds=62))
    assert second_tick.training_run is None
    assert second_tick.decision.reason is None
    assert controller.starts == []


def test_manual_training_uses_same_readiness_path_with_only_trigger_override(
    tmp_path: Path,
):
    service, _store, rows, _registry = _service_with_pending(
        tmp_path,
        count=18,
        categories=CURRENT_EMAIL_CATEGORIES,
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    for row in rows:
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory(str(row["predicted_category"])),
            feedback_request_id=f"learning-manual-{row['id']}",
            expected_current_action_plan_id=None,
            now=now,
        )

    result = service.request_manual_training(now=now)

    assert result.decision.due is False
    assert result.decision.reason == "training_not_ready"
    assert result.training_run is None


def test_concurrent_manual_requests_launch_only_one_training_child(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(
        tmp_path,
        count=18,
        categories=CURRENT_EMAIL_CATEGORIES,
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    _confirm_all(service, rows, now=now)
    controller = _ConcurrentController()
    first = EmailClassifierLearningService(
        store,
        registry=registry,
        retrain_state_path=service.retrain_state_path,
        controller=controller,
    )
    second = EmailClassifierLearningService(
        EmailStore(store.path),
        registry=registry,
        retrain_state_path=service.retrain_state_path,
        controller=controller,
    )

    results = _parallel_calls(
        lambda: first.request_manual_training(now=now),
        lambda: second.request_manual_training(now=now),
    )

    assert controller.starts == []
    assert all(result.training_run is None for result in results)
    assert load_retrain_state(service.retrain_state_path).active_run_id is None


def test_concurrent_manual_and_poll_launch_only_one_training_child(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(
        tmp_path,
        count=18,
        categories=CURRENT_EMAIL_CATEGORIES,
    )
    feedback_at = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    _confirm_all(service, rows, now=feedback_at)
    controller = _ConcurrentController()
    manual = EmailClassifierLearningService(
        store,
        registry=registry,
        retrain_state_path=service.retrain_state_path,
        controller=controller,
    )
    polling = EmailClassifierLearningService(
        EmailStore(store.path),
        registry=registry,
        retrain_state_path=service.retrain_state_path,
        controller=controller,
    )
    due_at = feedback_at + timedelta(seconds=31)

    results = _parallel_calls(
        lambda: manual.request_manual_training(now=due_at),
        lambda: polling.poll_retrain(now=due_at),
    )

    assert controller.starts == []
    assert all(result.training_run is None for result in results)
    assert load_retrain_state(service.retrain_state_path).active_run_id is None


def test_learning_poll_clears_orphan_and_next_tick_can_retry(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(
        tmp_path,
        count=18,
        categories=CURRENT_EMAIL_CATEGORIES,
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    for row in rows:
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory(str(row["predicted_category"])),
            feedback_request_id=f"learning-orphan-{row['id']}",
            expected_current_action_plan_id=None,
            now=now,
        )
    first = TrainingSubprocessController(
        registry,
        store_path=store.path,
        launcher=lambda _command: type(
            "Process", (), {"pid": 4321, "poll": lambda self: None}
        )(),
    )
    from app.email_classifier_retrain import SnapshotTrainingSignal

    signal = SnapshotTrainingSignal(
        snapshot_sha="a" * 64,
        description_version="v1",
        folder_label_watermark=50,
        important_label_watermark=0,
        minimum_ready=True,
    )
    run = first.start(
        command=["trainer"],
        now=now + timedelta(seconds=31),
        signal=signal,
        snapshot_id="snapshot-1",
    )
    state = load_retrain_state(tmp_path / "models" / "retrain-state.json")
    save_retrain_state(
        tmp_path / "models" / "retrain-state.json", state.with_active_run(run.run_id)
    )
    restarted = TrainingSubprocessController(
        registry,
        store_path=store.path,
        launcher=lambda _command: type(
            "Process", (), {"pid": 4322, "poll": lambda self: None}
        )(),
        pid_is_alive=lambda _pid: False,
    )
    service.controller = restarted

    failed = service.poll_retrain(now=now + timedelta(seconds=32))
    retried = service.poll_retrain(now=now + timedelta(seconds=33))

    assert failed.training_run is not None
    assert failed.training_run.status == "failed"
    assert failed.state.active_run_id is None
    assert retried.training_run is None
    assert retried.decision.due is False


def test_unselected_categories_train_as_others_when_others_is_selected(tmp_path: Path):
    """Mail from a category nobody picked teaches the model that the message is
    outside its scope, instead of being forced into the nearest picked one."""

    from app.email_classifier_learning import _selection_provenance

    class _Store:
        def latest_training_snapshot_state(self):
            return None

        def list_selected_training_records(self):
            return [
                {
                    "source": "user_feedback",
                    "account_id": "account-a",
                    "stable_message_identity": f"mail-{index}",
                    "provider_thread_id": None,
                    "normalized_model_input": '{"body":"m"}',
                    "category_key": category,
                }
                for index, category in enumerate(("work", "work", "personal", "finance"))
            ]

    store = _Store()
    with_others = _selection_provenance(
        store, sources=["user_feedback"], categories=["work", "others"]
    )
    labels = sorted(
        str(row["category_key"]) for row in with_others["selected_training_records"]
    )
    assert labels == ["others", "others", "work", "work"]

    without_others = _selection_provenance(
        store, sources=["user_feedback"], categories=["work"]
    )
    assert sorted(
        str(row["category_key"])
        for row in without_others["selected_training_records"]
    ) == ["work", "work"]
