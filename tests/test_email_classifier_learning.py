from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock, Thread
import time

from app.email_classifier_contracts import (
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
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


def _classification(message_id: str, category: EmailCategory) -> EmailClassification:
    classification_id = int.from_bytes(
        sha256(message_id.encode("utf-8")).digest()[:8], "big"
    ) & ((1 << 63) - 1) or 1
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
    tmp_path: Path, count: int = 1, *, minimum_new_examples: int = 5
):
    store = EmailStore(tmp_path / "email.sqlite3")
    categories = [EmailCategory.WORK, EmailCategory.JUNK]
    rows = []
    for index in range(count):
        category = categories[index % 2]
        row = store.upsert_classification(
            _classification(f"message-{index}", category),
            model_text=f"__subject__token-{index} {category.value}",
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
    for index, row in enumerate(rows):
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
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


def test_feedback_api_service_confirms_first_and_records_state_without_retraining(tmp_path: Path):
    service, store, rows, _ = _service_with_pending(tmp_path)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"], EmailCategory.IMPORTANT, now=now
    )

    assert result is not None
    assert result.confirmed["classification_source"] == "user"
    assert result.retrain is not None
    assert result.retrain.decision.due is False
    assert result.error is None
    assert load_retrain_state(tmp_path / "models" / "retrain-state.json").last_feedback_at
    assert store.list_training_examples()[0]["label"] == "important"


def test_feedback_service_retrains_after_batch_threshold(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(tmp_path, count=6)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    promoted_result = None
    for index, row in enumerate(rows):
        result = service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
            now=now,
        )
        if result is not None and result.retrain is not None and result.retrain.training_result is not None:
            promoted_result = result

    polled = service.poll_retrain(now=now + timedelta(seconds=31))
    assert polled.training_run is not None
    assert polled.training_run.status in {"queued", "running"}
    assert len(polled.training_run.sample_snapshots) == 6
    assert all(
        len(sample["sample_digest"]) == 64
        for sample in polled.training_run.sample_snapshots
    )
    for _ in range(100):
        polled = service.poll_retrain(now=now + timedelta(seconds=32))
        if polled.training_run is not None and polled.training_run.status not in {
            "queued",
            "running",
        }:
            break
        time.sleep(0.01)
    promoted_result = polled

    assert promoted_result is not None
    assert promoted_result.training_run is not None
    assert promoted_result.training_run.status == "succeeded"
    assert registry.active_manifest() is not None
    assert store.list_unincluded_training_examples() == []
    assert load_retrain_state(tmp_path / "models" / "retrain-state.json").last_trained_feedback_count == 6


def test_feedback_service_keeps_confirmation_when_training_is_not_ready(tmp_path: Path):
    service, store, rows, _ = _service_with_pending(
        tmp_path, minimum_new_examples=1
    )
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)

    result = service.confirm_and_maybe_retrain(
        rows[0]["id"], EmailCategory.WORK, now=now
    )

    assert result is not None
    assert result.confirmed["status"] == "processed"
    assert result.error is None
    assert store.list_training_examples()[0]["label"] == "work"


def test_five_feedback_start_on_later_runtime_tick_without_sixth_feedback(tmp_path: Path):
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
            now=now,
        )
        assert result is not None
        assert result.retrain is not None
        assert result.retrain.decision.due is False

    assert controller.starts == []
    tick = service.poll_retrain(now=now + timedelta(seconds=31))

    assert tick.training_run is not None
    assert tick.training_run.run_id == "run-1"
    assert controller.starts == [now + timedelta(seconds=31)]


def test_manual_training_uses_same_readiness_path_with_only_trigger_override(tmp_path: Path):
    service, _store, rows, _registry = _service_with_pending(tmp_path, count=5)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    for index, row in enumerate(rows):
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
            now=now,
        )

    result = service.request_manual_training(now=now)

    assert result.decision.due is True
    assert result.decision.reason == "manual"
    assert result.training_run is not None


def test_concurrent_manual_requests_launch_only_one_training_child(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(tmp_path, count=5)
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

    assert controller.starts == ["run-1"]
    assert {result.training_run.run_id for result in results} == {"run-1"}
    assert load_retrain_state(service.retrain_state_path).active_run_id == "run-1"


def test_concurrent_manual_and_poll_launch_only_one_training_child(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(tmp_path, count=5)
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

    assert controller.starts == ["run-1"]
    assert {result.training_run.run_id for result in results} == {"run-1"}
    assert load_retrain_state(service.retrain_state_path).active_run_id == "run-1"


def test_learning_poll_clears_orphan_and_next_tick_can_retry(tmp_path: Path):
    service, store, rows, registry = _service_with_pending(tmp_path, count=5)
    now = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    for index, row in enumerate(rows):
        service.confirm_and_maybe_retrain(
            row["id"],
            EmailCategory.WORK if index % 2 == 0 else EmailCategory.JUNK,
            now=now,
        )
    first = TrainingSubprocessController(
        registry,
        store_path=store.path,
        launcher=lambda _command: type(
            "Process", (), {"pid": 4321, "poll": lambda self: None}
        )(),
    )
    run = first.start(now=now + timedelta(seconds=31))
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
    assert retried.training_run is not None
    assert retried.training_run.status == "running"
    assert retried.training_run.run_id != run.run_id
