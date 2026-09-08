from datetime import datetime, timezone
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

import app.email_classifier_retrain as retrain_module
from app.email_description_optimizer import (
    DescriptionProposal,
    DescriptionProposalRepository,
    build_description_set_overlay,
    category_description_digest,
)
from app.email_embedding_classifier import CategoryDescription
from app.email_classifier_retrain import (
    RetrainPolicy,
    RetrainState,
    SnapshotTrainingSignal,
    TrainingSubprocessController,
    TrainingSubprocessRun,
    evaluate_snapshot_retrain,
    load_retrain_state,
    read_frozen_historical_error_state,
    save_retrain_state,
)
from app.email_model_registry import HistoricalSystematicErrorState
from app.email_classifier_learning import EmailClassifierLearningService
from app.email_model_registry import EmailModelRegistry
from app.email_store import EmailStore


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def _signal(**overrides):
    values = {
        "snapshot_sha": "a" * 64,
        "description_version": "descriptions-v1",
        "folder_label_watermark": 0,
        "important_label_watermark": 0,
        "minimum_ready": True,
        "manual": False,
    }
    values.update(overrides)
    return SnapshotTrainingSignal(**values)


def test_snapshot_change_threshold_is_50_and_49_never_trains() -> None:
    state = RetrainState(
        last_trained_snapshot_sha="0" * 64,
        last_trained_description_version="descriptions-v1",
        last_trained_folder_label_watermark=100,
        last_trained_important_label_watermark=40,
        minimum_ready_snapshot_trained=True,
    )

    not_due = evaluate_snapshot_retrain(
        state,
        signal=_signal(folder_label_watermark=149, important_label_watermark=40),
    )
    due = evaluate_snapshot_retrain(
        state,
        signal=_signal(folder_label_watermark=149, important_label_watermark=41),
    )

    assert RetrainPolicy().minimum_new_examples == 50
    assert not_due.due is False
    assert not_due.pending_examples == 49
    assert due.due is True
    assert due.reason == "label_change_threshold"
    assert due.pending_examples == 50


def test_manual_description_change_and_first_ready_snapshot_are_triggers() -> None:
    empty = RetrainState()
    first = evaluate_snapshot_retrain(empty, signal=_signal())
    assert first.due is True
    assert first.reason == "first_minimum_ready_snapshot"

    trained = empty.mark_snapshot_trained(_signal(), run_id="run-1", now=NOW)
    changed = evaluate_snapshot_retrain(
        trained,
        signal=_signal(snapshot_sha="b" * 64, description_version="descriptions-v2"),
    )
    manual = evaluate_snapshot_retrain(
        trained,
        signal=_signal(snapshot_sha="c" * 64, manual=True),
    )
    assert changed.due is True and changed.reason == "description_version_changed"
    assert manual.due is True and manual.reason == "manual"


def test_polling_and_elapsed_time_never_start_training() -> None:
    state = RetrainState(
        last_trained_snapshot_sha="a" * 64,
        last_trained_description_version="descriptions-v1",
        last_trained_folder_label_watermark=10,
        last_trained_important_label_watermark=5,
        minimum_ready_snapshot_trained=True,
    )
    unchanged = evaluate_snapshot_retrain(
        state,
        signal=_signal(
            snapshot_sha="a" * 64,
            folder_label_watermark=10,
            important_label_watermark=5,
        ),
    )
    assert unchanged.due is False
    assert unchanged.reason is None


def test_snapshot_observer_starts_once_while_poll_only_observes(
    tmp_path, monkeypatch
) -> None:
    store = EmailStore(tmp_path / "email.sqlite3")
    monkeypatch.setattr(
        store,
        "latest_training_snapshot_state",
        lambda: {
            "snapshot_id": "snapshot-1",
            "snapshot_sha": "a" * 64,
            "description_version": "descriptions-v1",
            "folder_label_watermark": 50,
            "important_label_watermark": 0,
            "minimum_ready": True,
        },
    )

    class Controller:
        def __init__(self):
            self.starts = 0

        def start(self, *, now, signal, snapshot_id):
            from app.email_classifier_retrain import TrainingSubprocessRun

            self.starts += 1
            return TrainingSubprocessRun(
                run_id="run-1",
                status="running",
                pid=7,
                started_at=now.isoformat(),
                updated_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                folder_label_watermark=signal.folder_label_watermark,
                important_label_watermark=signal.important_label_watermark,
            )

        def poll(self, run_id, *, now):
            raise AssertionError("fresh run should not be polled in this test")

    controller = Controller()
    service = EmailClassifierLearningService(
        store,
        registry=EmailModelRegistry(tmp_path / "registry"),
        retrain_state_path=tmp_path / "registry" / "retrain-state.json",
        controller=controller,
    )

    before = service.poll_retrain(now=NOW)
    started = service.observe_snapshot_and_maybe_retrain(now=NOW)

    assert before.training_run is None
    assert controller.starts == 1
    assert started.training_run is not None
    assert started.training_run.run_id == "run-1"


@pytest.mark.parametrize("path", ("normal", "pending", "proposal"))
def test_poll_recovers_durably_reserved_run_without_duplicate_launch(
    tmp_path, path
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    launches = []

    def launcher(command):
        launches.append(command)
        return SimpleNamespace(pid=4321, poll=lambda: None)

    controller = TrainingSubprocessController(
        registry,
        launcher=launcher,
        pid_is_alive=lambda pid: pid == 4321,
    )
    signal = _signal(
        snapshot_sha={"normal": "a", "pending": "b", "proposal": "c"}[path] * 64
    )
    overlay = None
    if path == "proposal":
        overlay = SimpleNamespace(
            proposal_id="proposal-reserved",
            source_description_version="descriptions-v1",
            description_set_digest="d" * 64,
        )
    reserved = controller.start(
        ["trainer", path],
        now=NOW,
        signal=signal,
        snapshot_id=f"snapshot-{path}",
        description_overlay=overlay,
    )
    state = RetrainState()
    if path == "pending":
        state = state.with_pending_snapshot(signal, snapshot_id="snapshot-pending")
    state_path = registry.root / "retrain-state.json"
    save_retrain_state(state_path, state)
    service = EmailClassifierLearningService(
        EmailStore(tmp_path / "email.sqlite3"),
        registry=registry,
        retrain_state_path=state_path,
        controller=controller,
    )

    recovered = service.poll_retrain(now=NOW)
    replay = service.poll_retrain(now=NOW)

    assert recovered.training_run is not None
    assert recovered.training_run.run_id == reserved.run_id
    assert load_retrain_state(state_path).active_run_id == reserved.run_id
    assert replay.training_run is not None
    assert len(launches) == 1


@pytest.mark.parametrize("path", ("normal", "pending", "proposal"))
def test_reserved_launch_intent_replays_once_after_crash(tmp_path, path) -> None:
    from datetime import timedelta

    registry = EmailModelRegistry(tmp_path / "registry")
    store_path = tmp_path / "email.sqlite3"
    signal = _signal(
        snapshot_sha={"normal": "d", "pending": "e", "proposal": "f"}[path] * 64
    )
    overlay = None
    if path == "proposal":
        overlay = SimpleNamespace(
            proposal_id="proposal-crash",
            source_description_version="descriptions-v1",
            description_set_digest="9" * 64,
        )

    crashing = TrainingSubprocessController(
        registry,
        store_path=store_path,
        launcher=lambda _command: (_ for _ in ()).throw(SystemExit("crash")),
    )
    with pytest.raises(SystemExit, match="crash"):
        crashing.start(
            now=NOW,
            signal=signal,
            snapshot_id=f"snapshot-{path}",
            description_overlay=overlay,
        )

    launches = []

    def launcher(command):
        launches.append(tuple(command))
        return SimpleNamespace(pid=4321, poll=lambda: None)

    restarted = TrainingSubprocessController(
        registry,
        store_path=store_path,
        launcher=launcher,
        pid_is_alive=lambda pid: pid == 4321,
    )
    state = RetrainState()
    if path == "pending":
        state = state.with_pending_snapshot(signal, snapshot_id="snapshot-pending")
    state_path = registry.root / "retrain-state.json"
    save_retrain_state(state_path, state)
    service = EmailClassifierLearningService(
        EmailStore(store_path),
        registry=registry,
        retrain_state_path=state_path,
        controller=restarted,
    )

    recovered = service.poll_retrain(now=NOW)
    replay = service.poll_retrain(now=NOW + timedelta(seconds=61))

    assert recovered.training_run is not None
    assert recovered.training_run.status == "launching"
    assert replay.training_run is not None
    assert replay.training_run.status == "running"
    assert len(launches) == 1
    assert launches[0][0:3] == (
        __import__("sys").executable,
        "-m",
        "app.email_classifier_retrain",
    )


@pytest.mark.parametrize("path", ("normal", "pending", "proposal"))
def test_spawned_launch_claim_is_not_spawned_again_after_parent_crash(
    tmp_path, path
) -> None:
    from datetime import timedelta

    registry = EmailModelRegistry(tmp_path / "registry")
    store_path = tmp_path / "email.sqlite3"
    launches = []
    signal = _signal(
        snapshot_sha={"normal": "1", "pending": "2", "proposal": "3"}[path] * 64
    )
    overlay = None
    if path == "proposal":
        overlay = SimpleNamespace(
            proposal_id="proposal-parent-crash",
            source_description_version="descriptions-v1",
            description_set_digest="8" * 64,
        )

    def spawned_then_parent_crashed(command):
        launches.append(tuple(command))
        raise SystemExit("parent-crashed-after-spawn")

    crashing = TrainingSubprocessController(
        registry,
        store_path=store_path,
        launcher=spawned_then_parent_crashed,
        launch_lease_seconds=60,
    )
    with pytest.raises(SystemExit, match="parent-crashed-after-spawn"):
        crashing.start(
            now=NOW,
            signal=signal,
            snapshot_id=f"snapshot-{path}",
            description_overlay=overlay,
        )

    run_path = next(registry.runs.glob("*.json"))
    claimed = crashing._load_run(run_path.stem)
    assert claimed.status == "launching"
    assert claimed.launch_attempt == 1
    assert claimed.launch_lease_expires_at is not None

    restarted = TrainingSubprocessController(
        registry,
        store_path=store_path,
        launcher=lambda command: launches.append(tuple(command)),
        launch_lease_seconds=60,
    )
    observed = restarted.poll(claimed.run_id, now=NOW + timedelta(seconds=10))
    replay = restarted.poll(claimed.run_id, now=NOW + timedelta(seconds=20))

    assert observed.status == "launching"
    assert replay.status == "launching"
    assert len(launches) == 1
    assert claimed.snapshot_id == f"snapshot-{path}"
    assert claimed.description_proposal_id == (
        "proposal-parent-crash" if path == "proposal" else None
    )


def test_expired_launch_claim_retries_same_typed_intent_once(tmp_path) -> None:
    from datetime import timedelta

    registry = EmailModelRegistry(tmp_path / "registry")
    launches = []
    crashing = TrainingSubprocessController(
        registry,
        store_path=tmp_path / "email.sqlite3",
        launcher=lambda command: (
            launches.append(tuple(command)),
            (_ for _ in ()).throw(SystemExit("crash")),
        )[1],
        launch_lease_seconds=30,
    )
    with pytest.raises(SystemExit, match="crash"):
        crashing.start(now=NOW, signal=_signal(), snapshot_id="snapshot-expired")
    claimed = crashing._load_run(next(registry.runs.glob("*.json")).stem)

    restarted = TrainingSubprocessController(
        registry,
        store_path=tmp_path / "email.sqlite3",
        launcher=lambda command: (
            launches.append(tuple(command))
            or SimpleNamespace(pid=4321, poll=lambda: None)
        ),
        pid_is_alive=lambda pid: pid == 4321,
        launch_lease_seconds=30,
    )
    running = restarted.poll(claimed.run_id, now=NOW + timedelta(seconds=31))
    replay = restarted.poll(claimed.run_id, now=NOW + timedelta(seconds=32))

    assert running.status == "running"
    assert replay.status == "running"
    assert running.launch_attempt == 2
    assert launches[0] == launches[1]
    assert len(launches) == 2


@pytest.mark.parametrize("terminal_status", ("succeeded", "failed"))
def test_fast_training_terminal_state_cannot_be_downgraded_to_running(
    tmp_path, terminal_status
) -> None:
    registry = EmailModelRegistry(tmp_path / "registry")
    controller = None

    def launcher(_command):
        queued_path = next(registry.runs.glob("*.json"))
        queued = controller._load_run(queued_path.stem)
        controller._save_run(
            replace(
                queued,
                status=terminal_status,
                updated_at=NOW.isoformat(),
                finished_at=NOW.isoformat(),
                exit_code=0 if terminal_status == "succeeded" else 1,
                model_id="candidate-fast" if terminal_status == "succeeded" else None,
                reason=None if terminal_status == "succeeded" else "trainer_failed",
            )
        )
        return SimpleNamespace(pid=4321, poll=lambda: 0)

    controller = TrainingSubprocessController(registry, launcher=launcher)
    started = controller.start(
        ["trainer", "fast"],
        now=NOW,
        signal=_signal(),
        snapshot_id="snapshot-fast",
    )
    polled = controller.poll(started.run_id, now=NOW)

    assert started.status == terminal_status
    assert polled.status == terminal_status


def test_snapshot_watermarks_round_trip_in_retrain_state(tmp_path) -> None:
    from app.email_classifier_retrain import load_retrain_state

    signal = _signal(folder_label_watermark=61, important_label_watermark=12)
    expected = RetrainState().mark_snapshot_trained(signal, run_id="run-9", now=NOW)
    path = tmp_path / "retrain-state.json"
    save_retrain_state(path, expected)

    assert load_retrain_state(path) == expected


class _PublishingStore:
    def __init__(self, state, *, fail=False):
        self.state = state
        self.fail = fail
        self.events = []

    def persist_training_snapshot(self, snapshot):
        self.events.append(("published", snapshot))
        if self.fail:
            raise RuntimeError("publication failed")
        return {"snapshot_id": self.state["snapshot_id"], "frozen": True}

    def latest_training_snapshot_state(self):
        self.events.append(("observed", self.state["snapshot_id"]))
        return self.state


class _PublishingController:
    def __init__(self):
        self.starts = []

    def start(self, *, now, signal, snapshot_id):
        self.starts.append((signal, snapshot_id))
        return TrainingSubprocessRun(
            run_id=f"run-{len(self.starts)}",
            status="running",
            pid=7,
            started_at=now.isoformat(),
            updated_at=now.isoformat(),
            snapshot_id=snapshot_id,
            snapshot_sha=signal.snapshot_sha,
            description_version=signal.description_version,
            folder_label_watermark=signal.folder_label_watermark,
            important_label_watermark=signal.important_label_watermark,
        )


def _publication_service(tmp_path, state, *, retrain_state=None, fail=False):
    store = _PublishingStore(state, fail=fail)
    controller = _PublishingController()
    state_path = tmp_path / "registry" / "retrain-state.json"
    if retrain_state is not None:
        save_retrain_state(state_path, retrain_state)
    service = EmailClassifierLearningService(
        store,
        registry=EmailModelRegistry(tmp_path / "registry"),
        retrain_state_path=state_path,
        controller=controller,
    )
    return service, store, controller


def test_production_snapshot_publication_immediately_triggers_first_ready(tmp_path):
    state = {
        "snapshot_id": "snapshot-first",
        "snapshot_sha": "1" * 64,
        "description_version": "description-set-v1",
        "folder_label_watermark": 10,
        "important_label_watermark": 10,
        "minimum_ready": True,
    }
    service, store, controller = _publication_service(tmp_path, state)

    result = service.publish_training_snapshot("frozen-snapshot", now=NOW)

    assert store.events == [
        ("published", "frozen-snapshot"),
        ("observed", "snapshot-first"),
    ]
    assert result.retrain.decision.reason == "first_minimum_ready_snapshot"
    assert controller.starts[0][1] == "snapshot-first"


def test_production_snapshot_publication_triggers_at_50_label_changes(tmp_path):
    trained_signal = _signal(
        snapshot_sha="1" * 64,
        description_version="description-set-v1",
        folder_label_watermark=100,
        important_label_watermark=25,
    )
    prior = RetrainState().mark_snapshot_trained(
        trained_signal, run_id="prior", now=NOW
    )
    state = {
        "snapshot_id": "snapshot-50",
        "snapshot_sha": "2" * 64,
        "description_version": "description-set-v1",
        "folder_label_watermark": 149,
        "important_label_watermark": 26,
        "minimum_ready": True,
    }
    service, _store, controller = _publication_service(
        tmp_path, state, retrain_state=prior
    )

    result = service.publish_training_snapshot("frozen-snapshot", now=NOW)

    assert result.retrain.decision.reason == "label_change_threshold"
    assert result.retrain.decision.pending_examples == 50
    assert len(controller.starts) == 1


def test_production_snapshot_publication_triggers_description_change(tmp_path):
    prior = RetrainState().mark_snapshot_trained(
        _signal(
            snapshot_sha="1" * 64,
            description_version="description-set-v1",
        ),
        run_id="prior",
        now=NOW,
    )
    state = {
        "snapshot_id": "snapshot-description",
        "snapshot_sha": "2" * 64,
        "description_version": "description-set-v2",
        "folder_label_watermark": 0,
        "important_label_watermark": 0,
        "minimum_ready": True,
    }
    service, _store, controller = _publication_service(
        tmp_path, state, retrain_state=prior
    )

    result = service.publish_training_snapshot("frozen-snapshot", now=NOW)

    assert result.retrain.decision.reason == "description_version_changed"
    assert len(controller.starts) == 1


def test_failed_snapshot_publication_never_observes_or_trains(tmp_path):
    state = {
        "snapshot_id": "snapshot-failed",
        "snapshot_sha": "3" * 64,
        "description_version": "description-set-v1",
        "folder_label_watermark": 50,
        "important_label_watermark": 0,
        "minimum_ready": True,
    }
    service, store, controller = _publication_service(tmp_path, state, fail=True)

    with pytest.raises(RuntimeError, match="publication failed"):
        service.publish_training_snapshot("frozen-snapshot", now=NOW)

    assert store.events == [("published", "frozen-snapshot")]
    assert controller.starts == []


def test_snapshot_arriving_during_training_is_persisted_and_triggered_after_terminal(
    tmp_path,
):
    old_signal = _signal(
        snapshot_sha="1" * 64,
        description_version="description-set-v1",
        folder_label_watermark=100,
    )
    prior = RetrainState(active_run_id="run-active")
    state = {
        "snapshot_id": "snapshot-pending",
        "snapshot_sha": "2" * 64,
        "description_version": "description-set-v1",
        "folder_label_watermark": 150,
        "important_label_watermark": 0,
        "minimum_ready": True,
    }
    store = _PublishingStore(state)

    class Controller:
        def __init__(self):
            self.starts = []

        def poll(self, run_id, *, now):
            assert run_id == "run-active"
            return TrainingSubprocessRun(
                run_id=run_id,
                status="succeeded",
                pid=7,
                started_at=NOW.isoformat(),
                updated_at=now.isoformat(),
                finished_at=now.isoformat(),
                model_id="candidate-old",
                snapshot_id="snapshot-old",
                snapshot_sha=old_signal.snapshot_sha,
                description_version=old_signal.description_version,
                folder_label_watermark=old_signal.folder_label_watermark,
                important_label_watermark=old_signal.important_label_watermark,
            )

        def start(self, *, now, signal, snapshot_id):
            self.starts.append((signal, snapshot_id))
            return TrainingSubprocessRun(
                run_id="run-pending",
                status="running",
                pid=8,
                started_at=now.isoformat(),
                updated_at=now.isoformat(),
                snapshot_id=snapshot_id,
                snapshot_sha=signal.snapshot_sha,
                description_version=signal.description_version,
                folder_label_watermark=signal.folder_label_watermark,
                important_label_watermark=signal.important_label_watermark,
            )

    controller = Controller()
    state_path = tmp_path / "registry" / "retrain-state.json"
    save_retrain_state(state_path, prior)
    service = EmailClassifierLearningService(
        store,
        registry=EmailModelRegistry(tmp_path / "registry"),
        retrain_state_path=state_path,
        controller=controller,
    )

    published = service.publish_training_snapshot("frozen-pending", now=NOW)

    durable = load_retrain_state(state_path)
    assert published.retrain.decision.reason == "pending_active_training"
    assert durable.pending_snapshot_id == "snapshot-pending"
    assert durable.pending_snapshot_sha == "2" * 64
    assert durable.active_run_id == "run-active"
    assert controller.starts == []
    event_path = tmp_path / "registry" / "training-observation-event.json"
    event_path.write_text(
        json.dumps(
            {
                "observation_digest": "3" * 64,
                "status": "pending-trigger",
                "snapshot_id": "snapshot-pending",
                "snapshot_sha": "2" * 64,
                "observed_at": NOW.isoformat(),
                "description_version": "description-set-v1",
            }
        ),
        encoding="utf-8",
    )

    completed = service.poll_retrain(now=NOW)

    assert completed.decision.reason == "label_change_threshold"
    assert completed.training_run.run_id == "run-pending"
    assert controller.starts[0][1] == "snapshot-pending"
    durable = load_retrain_state(state_path)
    assert durable.pending_snapshot_id is None
    assert durable.active_run_id == "run-pending"
    assert json.loads(event_path.read_text(encoding="utf-8"))["status"] == "published"


def test_training_run_freezes_durable_systematic_error_state(tmp_path):
    registry = EmailModelRegistry(tmp_path / "registry")
    cleared = registry.record_historical_systematic_error_state(
        HistoricalSystematicErrorState(
            unresolved=False,
            source="operator-review",
            reason="no unresolved historical error",
            updated_at=NOW.isoformat(),
        )
    )
    controller = TrainingSubprocessController(
        registry,
        launcher=lambda _command: type(
            "Process", (), {"pid": 4321, "poll": lambda self: None}
        )(),
    )

    run = controller.start(command=["trainer"], now=NOW, signal=_signal())

    assert run.unresolved_historical_systematic_error is False
    assert run.historical_systematic_error_state_sha256 == cleared.state_sha256
    assert run.historical_systematic_error_source == cleared.source
    assert run.historical_systematic_error_reason == cleared.reason
    assert run.historical_systematic_error_updated_at == cleared.updated_at
    assert read_frozen_historical_error_state(registry, run) == cleared

    registry.record_historical_systematic_error_state(
        HistoricalSystematicErrorState(
            unresolved=True,
            source="error-review",
            reason="new systematic error found",
            updated_at="2026-09-07T20:01:00+00:00",
        )
    )
    with pytest.raises(RuntimeError, match="changed after training was queued"):
        read_frozen_historical_error_state(registry, run)


def test_training_subprocess_reads_frozen_error_state_and_description_overlay(
    tmp_path, monkeypatch
):
    registry = EmailModelRegistry(tmp_path / "registry")
    error_state = registry.record_historical_systematic_error_state(
        HistoricalSystematicErrorState(
            unresolved=False,
            source="operator-review",
            reason="historical review complete",
            updated_at=NOW.isoformat(),
        )
    )
    active = {
        "work": CategoryDescription(
            core="Routine work.",
            include=("Projects.",),
            exclude=("Contracts.",),
            version="work-v1",
        ),
        "legal": CategoryDescription(
            core="External legal matters.",
            include=("Contracts.",),
            exclude=("Routine projects.",),
            version="legal-v1",
        ),
    }
    proposal = DescriptionProposal(
        proposal_id="proposal-subprocess",
        category="legal",
        source_description_version="legal-v1",
        source_description_digest=category_description_digest(active["legal"]),
        source_snapshot_id="snapshot-1",
        source_snapshot_sha="a" * 64,
        proposed=CategoryDescription(
            core="External contracts and disputes.",
            include=("Regulatory notices.",),
            exclude=("Routine projects.",),
            version="legal-v2",
        ),
        cited_sample_ids=("s1", "s2", "s3", "s4", "s5"),
        reason="Five independent conflicts.",
        conflict_category_pair=("work", "legal"),
        conflict_description_digests=(
            category_description_digest(active["work"]),
            category_description_digest(active["legal"]),
        ),
    )
    overlay = build_description_set_overlay(proposal, active)
    repository = DescriptionProposalRepository(registry.root)
    repository.persist(proposal)
    repository.persist_overlay(overlay)
    controller = TrainingSubprocessController(registry, store_path=tmp_path / "db")
    queued = TrainingSubprocessRun(
        run_id="run-overlay",
        status="queued",
        pid=0,
        started_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        snapshot_id="snapshot-1",
        snapshot_sha="a" * 64,
        description_version=overlay.description_set_version,
        unresolved_historical_systematic_error=False,
        historical_systematic_error_state_sha256=error_state.state_sha256,
        historical_systematic_error_source=error_state.source,
        historical_systematic_error_reason=error_state.reason,
        historical_systematic_error_updated_at=error_state.updated_at,
        description_proposal_id=proposal.proposal_id,
        source_description_version=proposal.source_description_version,
        description_set_digest=overlay.description_set_digest,
    )
    controller._save_run(queued)
    observed = {}

    class Store:
        def __init__(self, _path):
            pass

        def list_category_configs(self):
            raise AssertionError("proposal subprocess must use immutable overlay")

    monkeypatch.setattr(retrain_module, "EmailStore", Store)
    monkeypatch.setattr(retrain_module, "EmbeddingCache", lambda *_a, **_k: object())
    monkeypatch.setattr(
        retrain_module,
        "train_frozen_embedding_candidate",
        lambda **kwargs: (
            observed.update(kwargs)
            or SimpleNamespace(model_id="email-embedding-mlp-overlay")
        ),
    )
    evaluations = []
    monkeypatch.setattr(
        DescriptionProposalRepository,
        "record_evaluation",
        lambda self, proposal_id, *, model_id: evaluations.append(
            (proposal_id, model_id)
        ),
    )
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_DIMENSION", "2")
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_REVISION", "gpu4-r1")

    exit_code = retrain_module._run_training_job(
        db_path=tmp_path / "db",
        registry_path=registry.root,
        run_id=queued.run_id,
        snapshot_id=queued.snapshot_id,
        trained_at=NOW,
    )

    assert exit_code == 0
    assert observed["descriptions"] == overlay.descriptions
    assert observed["description_overlay"] == overlay
    assert observed["historical_systematic_error_state"] == error_state
    assert evaluations == [(proposal.proposal_id, "email-embedding-mlp-overlay")]


def test_proposal_evaluation_does_not_advance_active_description_watermark(
    tmp_path, monkeypatch
):
    registry = EmailModelRegistry(tmp_path / "registry")
    state_path = registry.root / "retrain-state.json"
    save_retrain_state(state_path, RetrainState(active_run_id="proposal-run"))
    run = TrainingSubprocessRun(
        run_id="proposal-run",
        status="succeeded",
        pid=7,
        started_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        finished_at=NOW.isoformat(),
        model_id="email-embedding-mlp-overlay",
        snapshot_id="snapshot-1",
        snapshot_sha="a" * 64,
        description_version="description-set-sha256:" + "b" * 64,
        folder_label_watermark=50,
        important_label_watermark=10,
        description_proposal_id="proposal-1",
        source_description_version="legal-v1",
        description_set_digest="b" * 64,
    )

    class Controller:
        def poll(self, run_id, *, now):
            assert run_id == "proposal-run"
            return run

    monkeypatch.setattr(
        DescriptionProposalRepository,
        "record_evaluation",
        lambda self, proposal_id, *, model_id: SimpleNamespace(
            evaluation_passed=True,
            evaluated_description_set_digest="b" * 64,
        ),
    )
    monkeypatch.setattr(
        DescriptionProposalRepository,
        "decide",
        lambda self, proposal_id, *, accept: None,
    )
    monkeypatch.setattr(
        DescriptionProposalRepository,
        "list_by_status",
        lambda self, status: (),
    )

    service = EmailClassifierLearningService(
        object(),
        registry=registry,
        retrain_state_path=state_path,
        controller=Controller(),
    )

    result = service.poll_retrain(now=NOW)

    assert result.state.active_run_id is None
    assert result.state.last_trained_snapshot_sha is None
    assert result.state.last_trained_description_version is None
    assert result.state.minimum_ready_snapshot_trained is False
