from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock, Thread
import time

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    INITIAL_EMAIL_CATEGORY_KEYS,
)
from app.email_classifier_learning import EmailClassifierLearningService
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
