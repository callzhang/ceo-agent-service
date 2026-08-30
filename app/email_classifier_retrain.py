"""Scheduler-neutral policy for coalescing confirmed email feedback."""

from __future__ import annotations

import json
import os
import subprocess
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
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "last_trained_feedback_count": self.last_trained_feedback_count,
            "last_trained_at": self.last_trained_at,
            "last_feedback_at": self.last_feedback_at,
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
        return cls(last_trained_feedback_count=count, **timestamps)


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


@dataclass(frozen=True)
class TrainingSubprocessRun:
    run_id: str
    status: str
    pid: int
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None


class TrainingSubprocessController:
    """Launch one short-lived trainer without blocking the email scan loop."""

    def __init__(self, registry: EmailModelRegistry, *, launcher=subprocess.Popen):
        self.registry = registry
        self.launcher = launcher
        self._processes: dict[str, object] = {}

    def start(self, command: list[str], *, now: datetime) -> TrainingSubprocessRun:
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("training command must contain non-empty strings")
        process = self.launcher(command)
        run = TrainingSubprocessRun(
            run_id=uuid.uuid4().hex,
            status="running",
            pid=int(process.pid),
            started_at=_format_timestamp(now),
        )
        self._processes[run.run_id] = process
        self._save_run(run)
        return run

    def poll(self, run_id: str, *, now: datetime) -> TrainingSubprocessRun:
        process = self._processes.get(run_id)
        if process is None:
            return self._load_run(run_id)
        exit_code = process.poll()
        if exit_code is None:
            return self._load_run(run_id)
        prior = self._load_run(run_id)
        completed = TrainingSubprocessRun(
            run_id=prior.run_id,
            status="succeeded" if exit_code == 0 else "failed",
            pid=prior.pid,
            started_at=prior.started_at,
            finished_at=_format_timestamp(now),
            exit_code=int(exit_code),
        )
        self._save_run(completed)
        self._processes.pop(run_id, None)
        return completed

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
