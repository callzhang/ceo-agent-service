from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_contracts import EmailCategory
from app.email_classifier_scan import EmailScanConfig
from app.email_classifier_training import CategoryEligibility
from app.email_store import EmailStore
from app.email_classifier_runtime import (
    EmailClassifierRuntime,
    EmailClassifierUnavailable,
    load_active_classifier,
    scan_with_active_model,
    RegistryPredictionClassifier,
)
from app.email_classifier_retrain import TrainingSubprocessController
from app.email_model_registry import EmailModelRegistry


def _model() -> CpuTfidfLogisticClassifier:
    return CpuTfidfLogisticClassifier(model_version="runtime-test").fit(
        ["work project", "work meeting", "junk promotion", "junk offer"],
        ["work", "work", "junk", "junk"],
    )


def test_runtime_prefers_active_model(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    _model().save(active)
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == active
    assert loaded.used_previous is False
    assert loaded.classifier.model_version == "runtime-test"


def test_runtime_falls_back_to_previous_when_active_is_invalid(tmp_path: Path):
    active = tmp_path / "model.active.pkl"
    previous = tmp_path / "model.previous.pkl"
    active.write_bytes(b"not a model")
    _model().save(previous)

    loaded = load_active_classifier(active, previous)

    assert loaded.path == previous
    assert loaded.used_previous is True


def test_runtime_fails_explicitly_when_no_model_is_valid(tmp_path: Path):
    with pytest.raises(EmailClassifierUnavailable, match="no valid"):
        load_active_classifier(
            tmp_path / "model.active.pkl", tmp_path / "model.previous.pkl"
        )


class FakeReadonlySource:
    def fetch_recent(self, mailbox: str = "INBOX", *, limit: int = 50):
        return [
            {
                "messageId": "message-1",
                "accountId": "test-account",
                "folder": mailbox,
                "uidValidity": 1,
                "uid": 1,
                "from": {"email": "team@example.com"},
                "subject": "work project",
                "textBody": "work meeting",
            }
        ]


def test_runtime_loads_model_and_runs_only_readonly_scan(tmp_path: Path):
    active = tmp_path / "registry-model.pkl"
    model = _model()
    model.save(active)

    class FakeRegistry(EmailModelRegistry):
        def __init__(self):
            pass

        def active_model_id_unverified(self):
            return "runtime-test"

        def active_manifest(self):
            return SimpleNamespace(model_id="runtime-test")

        def load_classifier(self, model_id):
            return model

        def get_model(self, model_id):
            return SimpleNamespace(artifact_path=active)

    registry = FakeRegistry()
    config = EmailScanConfig(
        config_version="runtime-scan-v1",
        thresholds={category: 0.0 for category in EmailCategory},
        actions={},
        category_eligibility={
            category: CategoryEligibility(
                category=category,
                configured_threshold=0.0,
                validated_precision=1.0,
                validation_sample_count=30,
                auto_action_eligible=True,
                reason="precision_and_sample_gate_met",
            )
            for category in EmailCategory
        },
    )

    runtime = EmailClassifierRuntime(registry)
    result = scan_with_active_model(
        FakeReadonlySource(),
        EmailStore(tmp_path / "email.sqlite3"),
        config,
        runtime=runtime,
        limit=1,
    )

    assert result.loaded.path == active
    assert result.scan.persisted_count == 1


def test_runtime_zero_mail_scan_still_polls_learning_with_cycle_clock(tmp_path: Path):
    model = _model()
    now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)

    class Registry:
        def active_model_id_unverified(self):
            return "active"

        def active_manifest(self):
            return SimpleNamespace(model_id="active")

        def load_classifier(self, _model_id):
            return model

        def get_model(self, _model_id):
            return SimpleNamespace(artifact_path=tmp_path / "active.pkl")

    class Learning:
        def __init__(self):
            self.polls = []

        def poll_retrain(self, *, now=None):
            self.polls.append(now)

    class EmptySource:
        def fetch_recent(self, mailbox="INBOX", *, limit=50):
            return []

    learning = Learning()
    runtime = EmailClassifierRuntime(Registry(), learning_service=learning)

    result = scan_with_active_model(
        EmptySource(),
        EmailStore(tmp_path / "empty.sqlite3"),
        EmailScanConfig.cold_start(),
        runtime=runtime,
        now=now,
    )

    assert result.scan.persisted_count == 0
    assert learning.polls == [now]


def test_runtime_reuses_failure_counter_across_three_scan_calls(tmp_path: Path):
    model = _model()

    class Broken:
        def predict_message(self, message):
            raise RuntimeError("broken")

    class Registry:
        def __init__(self):
            self.fallbacks = 0

        def active_model_id_unverified(self):
            return "active"

        def active_manifest(self):
            return SimpleNamespace(model_id="active")

        def load_classifier(self, model_id):
            return Broken() if model_id == "active" else model

        def get_model(self, model_id):
            return SimpleNamespace(artifact_path=tmp_path / f"{model_id}.pkl")

        def fallback_to_previous(self, **_values):
            self.fallbacks += 1
            return SimpleNamespace(model_id="previous")

    registry = Registry()
    runtime = EmailClassifierRuntime(registry)
    store = EmailStore(tmp_path / "persistent.sqlite3")
    config = EmailScanConfig.cold_start()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="broken"):
            scan_with_active_model(
                FakeReadonlySource(), store, config, runtime=runtime, limit=1
            )
    result = scan_with_active_model(
        FakeReadonlySource(), store, config, runtime=runtime, limit=1
    )

    assert registry.fallbacks == 1
    assert result.scan.persisted_count == 1


def test_consecutive_prediction_failures_fallback_only_at_threshold():
    class Broken:
        def predict(self, text):
            raise RuntimeError("broken")

    class Previous:
        def predict(self, text):
            return "previous:" + text

    class Registry:
        def __init__(self):
            self.fallbacks = []

        def fallback_to_previous(self, **values):
            self.fallbacks.append(values)
            return SimpleNamespace(model_id="previous-model")

        def load_classifier(self, model_id):
            return Previous()

    registry = Registry()
    classifier = RegistryPredictionClassifier(
        registry, Broken(), "active-model", failure_threshold=3
    )

    with pytest.raises(RuntimeError):
        classifier.predict("one")
    with pytest.raises(RuntimeError):
        classifier.predict("two")
    assert registry.fallbacks == []
    assert classifier.predict("three") == "previous:three"
    assert registry.fallbacks == [
        {
            "reason": "active_prediction_failed_repeatedly",
            "failed_model_id": "active-model",
        }
    ]


def test_training_subprocess_is_nonblocking_and_durably_polled(tmp_path: Path):
    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.exit_code = None

        def poll(self):
            return self.exit_code

    process = FakeProcess()
    controller = TrainingSubprocessController(
        EmailModelRegistry(tmp_path / "registry"),
        launcher=lambda command: process,
    )
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)

    run = controller.start(["python", "-m", "trainer"], now=now)
    assert run.status == "running"
    assert controller.poll(run.run_id, now=now).status == "running"
    process.exit_code = 0
    terminal = controller.poll(run.run_id, now=now)
    assert terminal.status == "failed"
    assert terminal.reason == "training_subprocess_exited_without_result:0"


def test_controller_restart_fails_orphaned_running_record_and_allows_learning_retry(
    tmp_path: Path,
):
    now = datetime(2026, 8, 29, 21, 0, tzinfo=timezone.utc)
    registry = EmailModelRegistry(tmp_path / "registry")
    first = TrainingSubprocessController(
        registry,
        launcher=lambda _command: SimpleNamespace(pid=4321, poll=lambda: None),
    )
    run = first.start(["python", "-m", "trainer"], now=now)
    restarted = TrainingSubprocessController(
        registry,
        pid_is_alive=lambda pid: False,
    )

    failed = restarted.poll(run.run_id, now=now)

    assert failed.status == "failed"
    assert failed.reason == "training_subprocess_orphaned"
