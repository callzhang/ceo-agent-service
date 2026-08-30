"""Scheduler-neutral policy for coalescing confirmed email feedback."""

from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.email_classifier_training import TrainingResult, train_and_promote
from app.email_model_registry import EmailModelRegistry
from app.email_store import EmailStore


@dataclass(frozen=True)
class RetrainPolicy:
    minimum_new_examples: int = 5
    idle_seconds: float = 30.0
    max_interval_seconds: float = 600.0

    def __post_init__(self) -> None:
        if self.minimum_new_examples <= 0:
            raise ValueError("minimum_new_examples must be positive")
        if self.idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        if self.max_interval_seconds <= 0:
            raise ValueError("max_interval_seconds must be positive")


@dataclass(frozen=True)
class RetrainState:
    last_trained_feedback_count: int = 0
    last_trained_at: str | None = None
    last_feedback_at: str | None = None
    active_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.last_trained_feedback_count < 0:
            raise ValueError("last_trained_feedback_count must be non-negative")
        for name, value in (
            ("last_trained_at", self.last_trained_at),
            ("last_feedback_at", self.last_feedback_at),
        ):
            if value is not None:
                _parse_timestamp(value)

    def record_feedback(self, now: datetime) -> "RetrainState":
        return replace(self, last_feedback_at=_format_timestamp(now))

    def mark_trained(self, feedback_count: int, now: datetime) -> "RetrainState":
        if feedback_count < self.last_trained_feedback_count:
            raise ValueError("trained feedback count cannot decrease")
        return replace(
            self,
            last_trained_feedback_count=feedback_count,
            last_trained_at=_format_timestamp(now),
            active_run_id=None,
        )

    def with_active_run(self, run_id: str | None) -> "RetrainState":
        return replace(self, active_run_id=run_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "last_trained_feedback_count": self.last_trained_feedback_count,
            "last_trained_at": self.last_trained_at,
            "last_feedback_at": self.last_feedback_at,
            "active_run_id": self.active_run_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RetrainState":
        count = value.get("last_trained_feedback_count", 0)
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("last_trained_feedback_count must be an integer")
        timestamps: dict[str, str | None] = {}
        for name in ("last_trained_at", "last_feedback_at"):
            timestamp = value.get(name)
            if timestamp is not None and not isinstance(timestamp, str):
                raise ValueError(f"{name} must be a timestamp string or null")
            timestamps[name] = timestamp
        active_run_id = value.get("active_run_id")
        if active_run_id is not None and (
            not isinstance(active_run_id, str) or not active_run_id
        ):
            raise ValueError("active_run_id must be a non-empty string or null")
        return cls(
            last_trained_feedback_count=count,
            active_run_id=active_run_id,
            **timestamps,
        )


@dataclass(frozen=True)
class RetrainDecision:
    due: bool
    reason: str | None
    pending_examples: int


@dataclass(frozen=True)
class AutoRetrainResult:
    decision: RetrainDecision
    state: RetrainState
    training_result: TrainingResult | None
    training_run: "TrainingSubprocessRun | None" = None


@dataclass(frozen=True)
class TrainingSubprocessRun:
    run_id: str
    status: str
    pid: int
    started_at: str
    updated_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    model_id: str | None = None
    reason: str | None = None
    sample_snapshots: tuple[dict[str, object], ...] = ()


class TrainingSubprocessController:
    """Launch one short-lived trainer without blocking the email scan loop."""

    def __init__(
        self,
        registry: EmailModelRegistry,
        *,
        store_path: str | Path | None = None,
        launcher=subprocess.Popen,
        pid_is_alive=None,
        stale_after_seconds: float = 300.0,
    ):
        self.registry = registry
        self.store_path = Path(store_path) if store_path is not None else None
        self.launcher = launcher
        self.pid_is_alive = pid_is_alive or _pid_is_alive
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.stale_after_seconds = stale_after_seconds
        self._processes: dict[str, object] = {}

    def start(
        self,
        command: list[str] | None = None,
        *,
        now: datetime,
    ) -> TrainingSubprocessRun:
        run_id = uuid.uuid4().hex
        if command is None:
            if self.store_path is None:
                raise ValueError("store_path is required for registry training")
            command = [
                sys.executable,
                "-m",
                "app.email_classifier_retrain",
                "--run-training",
                "--db",
                str(self.store_path),
                "--registry",
                str(self.registry.root),
                "--run-id",
                run_id,
                "--trained-at",
                _format_timestamp(now),
            ]
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("training command must contain non-empty strings")
        snapshots = tuple(
            EmailStore(self.store_path).list_training_examples(include_inclusion=True)
            if self.store_path is not None
            else ()
        )
        timestamp = _format_timestamp(now)
        queued = TrainingSubprocessRun(
            run_id=run_id,
            status="queued",
            pid=0,
            started_at=timestamp,
            updated_at=timestamp,
            sample_snapshots=snapshots,
        )
        self._save_run(queued)
        process = self.launcher(command)
        run = TrainingSubprocessRun(
            run_id=run_id,
            status="running",
            pid=int(process.pid),
            started_at=timestamp,
            updated_at=timestamp,
            sample_snapshots=snapshots,
        )
        self._processes[run.run_id] = process
        self._save_run(run)
        return run

    def poll(self, run_id: str, *, now: datetime) -> TrainingSubprocessRun:
        durable = self._load_run(run_id)
        if durable.status in {"succeeded", "rejected", "failed"}:
            self._processes.pop(run_id, None)
            return durable
        process = self._processes.get(run_id)
        if process is None:
            if durable.pid > 0 and self.pid_is_alive(durable.pid):
                return durable
            if durable.pid > 0:
                return self._fail_orphan(
                    durable, now=now, reason="training_subprocess_orphaned"
                )
            updated_at = _parse_timestamp(durable.updated_at or durable.started_at)
            stale = (_as_utc(now) - updated_at).total_seconds() >= self.stale_after_seconds
            if stale:
                return self._fail_orphan(
                    durable, now=now, reason="training_subprocess_stale"
                )
            return durable
        exit_code = process.poll()
        if exit_code is None:
            return self._load_run(run_id)
        prior = self._load_run(run_id)
        if prior.status in {"succeeded", "rejected", "failed"}:
            self._processes.pop(run_id, None)
            return prior
        completed = TrainingSubprocessRun(
            run_id=prior.run_id,
            status="failed",
            pid=prior.pid,
            started_at=prior.started_at,
            updated_at=_format_timestamp(now),
            finished_at=_format_timestamp(now),
            exit_code=int(exit_code),
            reason=f"training_subprocess_exited_without_result:{exit_code}",
            sample_snapshots=prior.sample_snapshots,
        )
        self._save_run(completed)
        self._processes.pop(run_id, None)
        return completed

    def _fail_orphan(
        self, run: TrainingSubprocessRun, *, now: datetime, reason: str
    ) -> TrainingSubprocessRun:
        failed = replace(
            run,
            status="failed",
            updated_at=_format_timestamp(now),
            finished_at=_format_timestamp(now),
            exit_code=None,
            reason=reason,
        )
        self._save_run(failed)
        return failed

    def _run_path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("invalid training run_id")
        return self.registry.runs / f"{run_id}.json"

    def _save_run(self, run: TrainingSubprocessRun) -> None:
        destination = self._run_path(run.run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(run.__dict__, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _load_run(self, run_id: str) -> TrainingSubprocessRun:
        try:
            payload = json.loads(self._run_path(run_id).read_text(encoding="utf-8"))
            return TrainingSubprocessRun(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid durable training run") from exc


def load_retrain_state(path: str | Path) -> RetrainState:
    source = Path(path)
    if not source.exists():
        return RetrainState()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid retrain state: {source}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"invalid retrain state: {source}")
    try:
        return RetrainState.from_mapping(payload)
    except ValueError as exc:
        raise ValueError(f"invalid retrain state: {source}") from exc


def save_retrain_state(path: str | Path, state: RetrainState) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(state.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def evaluate_retrain(
    state: RetrainState,
    *,
    feedback_count: int,
    now: datetime,
    policy: RetrainPolicy = RetrainPolicy(),
) -> RetrainDecision:
    if feedback_count < state.last_trained_feedback_count:
        raise ValueError("feedback count cannot be below trained count")
    current = _as_utc(now)
    pending = feedback_count - state.last_trained_feedback_count
    if pending < policy.minimum_new_examples:
        return RetrainDecision(False, None, pending)
    if state.last_trained_at is not None and (
        current - _parse_timestamp(state.last_trained_at)
    ).total_seconds() >= policy.max_interval_seconds:
        return RetrainDecision(True, "max_interval", pending)
    if state.last_feedback_at is not None and (
        current - _parse_timestamp(state.last_feedback_at)
    ).total_seconds() >= policy.idle_seconds:
        return RetrainDecision(True, "idle_debounce", pending)
    return RetrainDecision(False, None, pending)


def retrain_if_due(
    store: EmailStore,
    state: RetrainState,
    active_path: str | Path,
    previous_path: str | Path,
    *,
    now: datetime,
    model_version: str,
    policy: RetrainPolicy = RetrainPolicy(),
    c: float = 0.25,
    minimum_examples: int = 5,
    minimum_per_category: int = 2,
) -> AutoRetrainResult:
    """Promote only after a due decision and validated training succeed."""
    feedback_count = len(store.list_training_examples())
    decision = evaluate_retrain(
        state,
        feedback_count=feedback_count,
        now=now,
        policy=policy,
    )
    if not decision.due:
        return AutoRetrainResult(decision, state, None)
    result = train_and_promote(
        store,
        active_path,
        previous_path,
        model_version=model_version,
        c=c,
        minimum_examples=minimum_examples,
        minimum_per_category=minimum_per_category,
    )
    return AutoRetrainResult(
        decision,
        state.mark_trained(feedback_count, now),
        result,
    )


def _format_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _run_training_job(
    *, db_path: Path, registry_path: Path, run_id: str, trained_at: datetime
) -> int:
    registry = EmailModelRegistry(registry_path)
    controller = TrainingSubprocessController(registry, store_path=db_path)
    started = TrainingSubprocessRun(
        run_id=run_id,
        status="running",
        pid=os.getpid(),
        started_at=_format_timestamp(trained_at),
        updated_at=_format_timestamp(datetime.now(timezone.utc)),
        sample_snapshots=controller._load_run(run_id).sample_snapshots,
    )
    controller._save_run(started)
    try:
        result = train_and_promote(
            EmailStore(db_path),
            registry,
            trained_at=trained_at,
            training_examples=started.sample_snapshots,
        )
        terminal = TrainingSubprocessRun(
            run_id=run_id,
            status="succeeded" if result.promoted else "rejected",
            pid=os.getpid(),
            started_at=started.started_at,
            updated_at=_format_timestamp(datetime.now(timezone.utc)),
            finished_at=_format_timestamp(datetime.now(timezone.utc)),
            exit_code=0,
            model_id=result.model_id,
            reason=result.promotion_reason,
            sample_snapshots=started.sample_snapshots,
        )
        controller._save_run(terminal)
        return 0
    except Exception as exc:
        controller._save_run(
            TrainingSubprocessRun(
                run_id=run_id,
                status="failed",
                pid=os.getpid(),
                started_at=started.started_at,
                updated_at=_format_timestamp(datetime.now(timezone.utc)),
                finished_at=_format_timestamp(datetime.now(timezone.utc)),
                exit_code=1,
                reason=f"{type(exc).__name__}:{exc}",
                sample_snapshots=started.sample_snapshots,
            )
        )
        return 1


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-training", action="store_true")
    parser.add_argument("--db")
    parser.add_argument("--registry")
    parser.add_argument("--run-id")
    parser.add_argument("--trained-at")
    args = parser.parse_args()
    if not args.run_training:
        parser.error("--run-training is required")
    return _run_training_job(
        db_path=Path(args.db),
        registry_path=Path(args.registry),
        run_id=str(args.run_id),
        trained_at=_parse_timestamp(str(args.trained_at)),
    )


if __name__ == "__main__":
    raise SystemExit(_main())
