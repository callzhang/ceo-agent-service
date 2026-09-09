"""Scheduler-neutral policy for staged frozen-snapshot training."""

from __future__ import annotations

import json
import argparse
from contextlib import contextmanager
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from app.email_classifier_training import (
    TrainingResult,
    train_frozen_embedding_candidate,
)
from app.email_embedding_cache import EmbeddingCache
from app.email_embedding_classifier import CategoryDescription
from app.email_model_registry import (
    EmailModelRegistry,
    HistoricalSystematicErrorState,
)
from app.email_store import EmailStore


_RETRAIN_STATE_THREAD_LOCK = threading.RLock()


@dataclass(frozen=True)
class RetrainPolicy:
    minimum_new_examples: int = 50
    # Accepted only for loading old settings. Elapsed time is intentionally not
    # consulted by the Task 9 trigger policy.
    idle_seconds: float | None = None
    max_interval_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.minimum_new_examples <= 0:
            raise ValueError("minimum_new_examples must be positive")
        for name in ("idle_seconds", "max_interval_seconds"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")


@dataclass(frozen=True)
class SnapshotTrainingSignal:
    snapshot_sha: str
    description_version: str
    folder_label_watermark: int
    important_label_watermark: int
    minimum_ready: bool
    manual: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.snapshot_sha) is not str
            or len(self.snapshot_sha) != 64
            or any(
                character not in "0123456789abcdef" for character in self.snapshot_sha
            )
        ):
            raise ValueError("snapshot_sha must be lowercase SHA-256")
        if (
            type(self.description_version) is not str
            or not self.description_version.strip()
        ):
            raise ValueError("description_version must be non-empty")
        for name in ("folder_label_watermark", "important_label_watermark"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.minimum_ready) is not bool or type(self.manual) is not bool:
            raise TypeError("snapshot signal flags must be booleans")


@dataclass(frozen=True)
class RetrainState:
    last_trained_feedback_count: int = 0
    last_trained_at: str | None = None
    last_feedback_at: str | None = None
    active_run_id: str | None = None
    last_trained_snapshot_sha: str | None = None
    last_trained_description_version: str | None = None
    last_trained_folder_label_watermark: int = 0
    last_trained_important_label_watermark: int = 0
    minimum_ready_snapshot_trained: bool = False
    pending_snapshot_id: str | None = None
    pending_snapshot_sha: str | None = None
    pending_description_version: str | None = None
    pending_folder_label_watermark: int | None = None
    pending_important_label_watermark: int | None = None
    pending_minimum_ready: bool | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.last_trained_feedback_count, bool)
            or not isinstance(self.last_trained_feedback_count, int)
            or self.last_trained_feedback_count < 0
        ):
            raise ValueError("last_trained_feedback_count must be non-negative")
        if self.last_trained_snapshot_sha is not None and (
            len(self.last_trained_snapshot_sha) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.last_trained_snapshot_sha
            )
        ):
            raise ValueError("last_trained_snapshot_sha must be lowercase SHA-256")
        if self.last_trained_description_version is not None and not (
            isinstance(self.last_trained_description_version, str)
            and self.last_trained_description_version.strip()
        ):
            raise ValueError("last_trained_description_version must be non-empty")
        for name in (
            "last_trained_folder_label_watermark",
            "last_trained_important_label_watermark",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.minimum_ready_snapshot_trained) is not bool:
            raise TypeError("minimum_ready_snapshot_trained must be boolean")
        pending = (
            self.pending_snapshot_id,
            self.pending_snapshot_sha,
            self.pending_description_version,
            self.pending_folder_label_watermark,
            self.pending_important_label_watermark,
            self.pending_minimum_ready,
        )
        if any(value is not None for value in pending):
            if any(value is None for value in pending):
                raise ValueError("pending snapshot binding must be complete")
            SnapshotTrainingSignal(
                snapshot_sha=self.pending_snapshot_sha,
                description_version=self.pending_description_version,
                folder_label_watermark=self.pending_folder_label_watermark,
                important_label_watermark=self.pending_important_label_watermark,
                minimum_ready=self.pending_minimum_ready,
            )
            if not self.pending_snapshot_id.strip():
                raise ValueError("pending_snapshot_id must be non-empty")
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

    def with_pending_snapshot(
        self, signal: SnapshotTrainingSignal, *, snapshot_id: str
    ) -> "RetrainState":
        if not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty")
        return replace(
            self,
            pending_snapshot_id=snapshot_id,
            pending_snapshot_sha=signal.snapshot_sha,
            pending_description_version=signal.description_version,
            pending_folder_label_watermark=signal.folder_label_watermark,
            pending_important_label_watermark=signal.important_label_watermark,
            pending_minimum_ready=signal.minimum_ready,
        )

    def without_pending_snapshot(self) -> "RetrainState":
        return replace(
            self,
            pending_snapshot_id=None,
            pending_snapshot_sha=None,
            pending_description_version=None,
            pending_folder_label_watermark=None,
            pending_important_label_watermark=None,
            pending_minimum_ready=None,
        )

    def pending_signal(self) -> SnapshotTrainingSignal | None:
        if self.pending_snapshot_id is None:
            return None
        return SnapshotTrainingSignal(
            snapshot_sha=self.pending_snapshot_sha,
            description_version=self.pending_description_version,
            folder_label_watermark=self.pending_folder_label_watermark,
            important_label_watermark=self.pending_important_label_watermark,
            minimum_ready=self.pending_minimum_ready,
        )

    def mark_snapshot_trained(
        self,
        signal: SnapshotTrainingSignal,
        *,
        run_id: str,
        now: datetime,
    ) -> "RetrainState":
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        return replace(
            self,
            last_trained_snapshot_sha=signal.snapshot_sha,
            last_trained_description_version=signal.description_version,
            last_trained_folder_label_watermark=signal.folder_label_watermark,
            last_trained_important_label_watermark=signal.important_label_watermark,
            minimum_ready_snapshot_trained=True,
            last_trained_at=_format_timestamp(now),
            active_run_id=None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "last_trained_feedback_count": self.last_trained_feedback_count,
            "last_trained_at": self.last_trained_at,
            "last_feedback_at": self.last_feedback_at,
            "active_run_id": self.active_run_id,
            "last_trained_snapshot_sha": self.last_trained_snapshot_sha,
            "last_trained_description_version": self.last_trained_description_version,
            "last_trained_folder_label_watermark": self.last_trained_folder_label_watermark,
            "last_trained_important_label_watermark": self.last_trained_important_label_watermark,
            "minimum_ready_snapshot_trained": self.minimum_ready_snapshot_trained,
            "pending_snapshot_id": self.pending_snapshot_id,
            "pending_snapshot_sha": self.pending_snapshot_sha,
            "pending_description_version": self.pending_description_version,
            "pending_folder_label_watermark": self.pending_folder_label_watermark,
            "pending_important_label_watermark": (
                self.pending_important_label_watermark
            ),
            "pending_minimum_ready": self.pending_minimum_ready,
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
            last_trained_snapshot_sha=value.get("last_trained_snapshot_sha"),
            last_trained_description_version=value.get(
                "last_trained_description_version"
            ),
            last_trained_folder_label_watermark=value.get(
                "last_trained_folder_label_watermark", 0
            ),
            last_trained_important_label_watermark=value.get(
                "last_trained_important_label_watermark", 0
            ),
            minimum_ready_snapshot_trained=value.get(
                "minimum_ready_snapshot_trained", False
            ),
            pending_snapshot_id=value.get("pending_snapshot_id"),
            pending_snapshot_sha=value.get("pending_snapshot_sha"),
            pending_description_version=value.get("pending_description_version"),
            pending_folder_label_watermark=value.get("pending_folder_label_watermark"),
            pending_important_label_watermark=value.get(
                "pending_important_label_watermark"
            ),
            pending_minimum_ready=value.get("pending_minimum_ready"),
            **timestamps,
        )


@dataclass(frozen=True)
class RetrainDecision:
    due: bool
    reason: str | None
    pending_examples: int


def evaluate_snapshot_retrain(
    state: RetrainState,
    *,
    signal: SnapshotTrainingSignal,
    policy: RetrainPolicy = RetrainPolicy(),
) -> RetrainDecision:
    """Decide from durable snapshot changes; elapsed time is never a trigger."""

    folder_delta = signal.folder_label_watermark - (
        state.last_trained_folder_label_watermark
    )
    important_delta = signal.important_label_watermark - (
        state.last_trained_important_label_watermark
    )
    if folder_delta < 0 or important_delta < 0:
        raise ValueError("label watermarks cannot move backwards")
    changed = folder_delta + important_delta
    if state.active_run_id is not None:
        return RetrainDecision(False, None, changed)
    if not signal.minimum_ready:
        return RetrainDecision(False, "training_not_ready", changed)
    if signal.manual:
        return RetrainDecision(True, "manual", changed)
    if not state.minimum_ready_snapshot_trained:
        return RetrainDecision(True, "first_minimum_ready_snapshot", changed)
    if signal.description_version != state.last_trained_description_version:
        return RetrainDecision(True, "description_version_changed", changed)
    if changed >= policy.minimum_new_examples:
        return RetrainDecision(True, "label_change_threshold", changed)
    return RetrainDecision(False, None, changed)


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
    snapshot_id: str = ""
    snapshot_sha: str = ""
    description_version: str = ""
    folder_label_watermark: int = 0
    important_label_watermark: int = 0
    unresolved_historical_systematic_error: bool = True
    historical_systematic_error_state_sha256: str = ""
    historical_systematic_error_source: str = ""
    historical_systematic_error_reason: str = ""
    historical_systematic_error_updated_at: str = ""
    description_proposal_id: str | None = None
    source_description_version: str | None = None
    description_set_digest: str = ""
    launch_attempt: int = 0
    launch_lease_expires_at: str | None = None


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
        launch_lease_seconds: float = 60.0,
    ):
        self.registry = registry
        self.store_path = Path(store_path) if store_path is not None else None
        self.launcher = launcher
        self.pid_is_alive = pid_is_alive or _pid_is_alive
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if launch_lease_seconds <= 0:
            raise ValueError("launch_lease_seconds must be positive")
        self.stale_after_seconds = stale_after_seconds
        self.launch_lease_seconds = launch_lease_seconds
        self._processes: dict[str, object] = {}

    def start(
        self,
        command: list[str] | None = None,
        *,
        now: datetime,
        signal: SnapshotTrainingSignal | None = None,
        snapshot_id: str = "",
        description_overlay: object | None = None,
    ) -> TrainingSubprocessRun:
        run_id = uuid.uuid4().hex
        historical_error_state = self.registry.historical_systematic_error_state()
        proposal_id = getattr(description_overlay, "proposal_id", None)
        source_description_version = getattr(
            description_overlay, "source_description_version", None
        )
        description_set_digest = getattr(
            description_overlay, "description_set_digest", ""
        )
        if signal is None and self.store_path is not None:
            latest = EmailStore(self.store_path).latest_training_snapshot_state()
            if latest is not None:
                snapshot_id = str(latest["snapshot_id"])
                signal = SnapshotTrainingSignal(
                    snapshot_sha=str(latest["snapshot_sha"]),
                    description_version=str(latest["description_version"]),
                    folder_label_watermark=int(latest["folder_label_watermark"]),
                    important_label_watermark=int(latest["important_label_watermark"]),
                    minimum_ready=bool(latest["minimum_ready"]),
                )
        if command is None:
            if self.store_path is None:
                raise ValueError("store_path is required for registry training")
            if not snapshot_id:
                raise ValueError("snapshot_id is required for registry training")
            command = self._training_command(
                run_id=run_id,
                snapshot_id=snapshot_id,
                trained_at=_format_timestamp(now),
            )
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("training command must contain non-empty strings")
        if signal is None:
            signal = SnapshotTrainingSignal(
                snapshot_sha="0" * 64,
                description_version="legacy-unbound",
                folder_label_watermark=0,
                important_label_watermark=0,
                minimum_ready=False,
            )
        timestamp = _format_timestamp(now)
        queued = TrainingSubprocessRun(
            run_id=run_id,
            status="queued",
            pid=0,
            started_at=timestamp,
            updated_at=timestamp,
            snapshot_id=snapshot_id,
            snapshot_sha=signal.snapshot_sha,
            description_version=signal.description_version,
            folder_label_watermark=signal.folder_label_watermark,
            important_label_watermark=signal.important_label_watermark,
            unresolved_historical_systematic_error=(historical_error_state.unresolved),
            historical_systematic_error_state_sha256=(
                historical_error_state.state_sha256
            ),
            historical_systematic_error_source=historical_error_state.source,
            historical_systematic_error_reason=historical_error_state.reason,
            historical_systematic_error_updated_at=historical_error_state.updated_at,
            description_proposal_id=proposal_id,
            source_description_version=source_description_version,
            description_set_digest=description_set_digest,
        )
        self._save_run(queued)
        return self._launch_reserved(queued, now=now, command=command)

    def poll(self, run_id: str, *, now: datetime) -> TrainingSubprocessRun:
        durable = self._load_run(run_id)
        if durable.status in {"succeeded", "rejected", "failed"}:
            self._processes.pop(run_id, None)
            return durable
        process = self._processes.get(run_id)
        if process is None:
            if durable.status == "queued" and durable.pid == 0:
                return self._launch_reserved(durable, now=now)
            if durable.status == "launching" and durable.pid == 0:
                lease_expires = _parse_timestamp(
                    durable.launch_lease_expires_at or durable.updated_at
                )
                if _as_utc(now) < lease_expires:
                    return durable
                return self._launch_reserved(durable, now=now)
            if durable.pid > 0 and self.pid_is_alive(durable.pid):
                return durable
            if durable.pid > 0:
                return self._fail_orphan(
                    durable, now=now, reason="training_subprocess_orphaned"
                )
            updated_at = _parse_timestamp(durable.updated_at or durable.started_at)
            stale = (
                _as_utc(now) - updated_at
            ).total_seconds() >= self.stale_after_seconds
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
        completed = replace(
            prior,
            status="failed",
            updated_at=_format_timestamp(now),
            finished_at=_format_timestamp(now),
            exit_code=int(exit_code),
            reason=f"training_subprocess_exited_without_result:{exit_code}",
            launch_lease_expires_at=None,
        )
        self._save_run(completed)
        self._processes.pop(run_id, None)
        return completed

    def recover_reserved_or_started_run(
        self, *, now: datetime
    ) -> TrainingSubprocessRun | None:
        """Return the sole durable in-flight run after caller-state loss."""

        recoverable: list[TrainingSubprocessRun] = []
        for path in sorted(self.registry.runs.glob("*.json")):
            run = self._load_run(path.stem)
            if run.status in {"queued", "launching", "running"}:
                recoverable.append(run)
        if len(recoverable) > 1:
            raise ValueError("multiple durable training runs are in flight")
        if not recoverable:
            return None
        run = recoverable[0]
        if run.status in {"queued", "launching"} and run.pid == 0:
            return self._launch_reserved(run, now=now)
        return run

    def _launch_reserved(
        self,
        run: TrainingSubprocessRun,
        *,
        now: datetime,
        command: list[str] | None = None,
    ) -> TrainingSubprocessRun:
        claimed, should_spawn = self._claim_launch(run.run_id, now=now)
        if not should_spawn:
            return claimed
        if command is None:
            if self.store_path is None:
                return claimed
            command = self._training_command(
                run_id=run.run_id,
                snapshot_id=run.snapshot_id,
                trained_at=run.started_at,
            )
        try:
            process = self.launcher(command)
        except Exception as exc:
            self._save_run(
                replace(
                    claimed,
                    status="failed",
                    updated_at=_format_timestamp(now),
                    finished_at=_format_timestamp(now),
                    reason=f"training_subprocess_launch_failed:{type(exc).__name__}",
                    launch_lease_expires_at=None,
                )
            )
            raise
        self._processes[run.run_id] = process
        stored = self._save_run(
            replace(
                claimed,
                status="running",
                pid=int(process.pid),
                updated_at=_format_timestamp(now),
                launch_lease_expires_at=None,
            )
        )
        if stored.status in {"succeeded", "rejected", "failed"}:
            self._processes.pop(run.run_id, None)
        return stored

    def _claim_launch(
        self, run_id: str, *, now: datetime
    ) -> tuple[TrainingSubprocessRun, bool]:
        destination = self._run_path(run_id)
        with self.registry._locked():
            current = self._load_run(run_id)
            if current.status in {"running", "succeeded", "rejected", "failed"}:
                return current, False
            if current.status == "launching":
                expires = _parse_timestamp(
                    current.launch_lease_expires_at or current.updated_at
                )
                if _as_utc(now) < expires:
                    return current, False
            elif current.status != "queued":
                raise ValueError("durable training launch status is invalid")
            claimed = replace(
                current,
                status="launching",
                pid=0,
                updated_at=_format_timestamp(now),
                launch_attempt=current.launch_attempt + 1,
                launch_lease_expires_at=_format_timestamp(
                    _as_utc(now) + timedelta(seconds=self.launch_lease_seconds)
                ),
            )
            self._write_run_unlocked(destination, claimed)
            return claimed, True

    def _training_command(
        self, *, run_id: str, snapshot_id: str, trained_at: str
    ) -> list[str]:
        if self.store_path is None:
            raise ValueError("store_path is required for registry training")
        return [
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
            trained_at,
            "--snapshot-id",
            snapshot_id,
        ]

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

    def _save_run(self, run: TrainingSubprocessRun) -> TrainingSubprocessRun:
        destination = self._run_path(run.run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.registry._locked():
            if destination.exists():
                current = self._load_run(run.run_id)
                if current.status in {"succeeded", "rejected", "failed"}:
                    return current
                if current.status == "running" and run.status in {
                    "queued",
                    "launching",
                }:
                    return current
                if current.status == "launching" and run.status == "queued":
                    return current
            self._write_run_unlocked(destination, run)
        return run

    def _write_run_unlocked(
        self, destination: Path, run: TrainingSubprocessRun
    ) -> None:
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


def read_frozen_historical_error_state(
    registry: EmailModelRegistry, run: TrainingSubprocessRun
) -> HistoricalSystematicErrorState:
    """Read the durable source and require it to match the queued run snapshot."""

    current = registry.historical_systematic_error_state()
    if (
        current.state_sha256 != run.historical_systematic_error_state_sha256
        or current.unresolved != run.unresolved_historical_systematic_error
        or current.source != run.historical_systematic_error_source
        or current.reason != run.historical_systematic_error_reason
        or current.updated_at != run.historical_systematic_error_updated_at
    ):
        raise RuntimeError(
            "historical systematic-error state changed after training was queued"
        )
    return current


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
            json.dump(
                state.to_dict(), handle, ensure_ascii=False, separators=(",", ":")
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def retrain_state_reservation(path: str | Path):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    with _RETRAIN_STATE_THREAD_LOCK:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def evaluate_retrain(
    state: RetrainState,
    *,
    feedback_count: int,
    now: datetime,
    policy: RetrainPolicy = RetrainPolicy(),
) -> RetrainDecision:
    if feedback_count < state.last_trained_feedback_count:
        raise ValueError("feedback count cannot be below trained count")
    _as_utc(now)
    pending = feedback_count - state.last_trained_feedback_count
    if pending < policy.minimum_new_examples:
        return RetrainDecision(False, None, pending)
    return RetrainDecision(True, "label_change_threshold", pending)


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
    """Deprecated path-based promotion entry point; Task 9 never activates models."""
    feedback_count = len(store.list_training_examples())
    decision = evaluate_retrain(
        state,
        feedback_count=feedback_count,
        now=now,
        policy=policy,
    )
    if decision.due:
        decision = RetrainDecision(
            False, "legacy_promotion_disabled", decision.pending_examples
        )
    return AutoRetrainResult(decision, state, None)


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
    *,
    db_path: Path,
    registry_path: Path,
    run_id: str,
    snapshot_id: str,
    trained_at: datetime,
) -> int:
    registry = EmailModelRegistry(registry_path)
    controller = TrainingSubprocessController(registry, store_path=db_path)
    queued = controller._load_run(run_id)
    started = replace(
        queued,
        status="running",
        pid=os.getpid(),
        updated_at=_format_timestamp(datetime.now(timezone.utc)),
        launch_lease_expires_at=None,
    )
    controller._save_run(started)
    try:
        if snapshot_id != started.snapshot_id:
            raise RuntimeError("training command snapshot does not match queued run")
        store = EmailStore(db_path)
        historical_error_state = read_frozen_historical_error_state(registry, started)
        dimension = int(os.environ["CEO_EMAIL_EMBEDDING_DIMENSION"])
        description_overlay = None
        if started.description_proposal_id is not None:
            from app.email_description_optimizer import DescriptionProposalRepository

            proposal_repository = DescriptionProposalRepository(registry.root)
            description_overlay = proposal_repository.get_overlay(
                started.description_proposal_id, started.description_set_digest
            )
            if (
                description_overlay.source_description_version
                != started.source_description_version
                or description_overlay.source_snapshot_id != started.snapshot_id
                or description_overlay.source_snapshot_sha != started.snapshot_sha
                or description_overlay.description_set_version
                != started.description_version
            ):
                raise RuntimeError("queued description proposal binding is invalid")
            descriptions = description_overlay.descriptions
        else:
            descriptions = {
                row["category_key"]: CategoryDescription(
                    core=row["core_description"],
                    include=tuple(row["include"]),
                    exclude=tuple(row["exclude"]),
                    version=row["description_version"],
                )
                for row in store.list_category_configs()
                if row["enabled"]
            }
        from app.email_candidate_benchmark import benchmark_candidate

        result = train_frozen_embedding_candidate(
            store=store,
            snapshot_id=snapshot_id,
            registry=registry,
            cache=EmbeddingCache(registry.root, dimension=dimension),
            descriptions=descriptions,
            embedding_model_id=os.environ.get(
                "CEO_EMAIL_EMBEDDING_MODEL",
                "jinaai/jina-embeddings-v5-text-small",
            ),
            embedding_revision=os.environ["CEO_EMAIL_EMBEDDING_REVISION"],
            parent_model_id=registry.active_model_id_unverified(),
            trained_at=trained_at,
            expected_snapshot_sha=started.snapshot_sha,
            expected_description_version=started.description_version,
            historical_systematic_error_state=historical_error_state,
            description_overlay=description_overlay,
            benchmark_candidate=benchmark_candidate,
        )
        if description_overlay is not None:
            proposal_repository.record_evaluation(
                description_overlay.proposal_id, model_id=result.model_id
            )
        terminal = replace(
            started,
            status="succeeded",
            updated_at=_format_timestamp(datetime.now(timezone.utc)),
            finished_at=_format_timestamp(datetime.now(timezone.utc)),
            exit_code=0,
            model_id=result.model_id,
            reason="candidate_staged_not_activated",
        )
        controller._save_run(terminal)
        return 0
    except Exception as exc:
        controller._save_run(
            replace(
                started,
                status="failed",
                updated_at=_format_timestamp(datetime.now(timezone.utc)),
                finished_at=_format_timestamp(datetime.now(timezone.utc)),
                exit_code=1,
                reason=f"{type(exc).__name__}:{exc}",
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
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    if not args.run_training:
        parser.error("--run-training is required")
    return _run_training_job(
        db_path=Path(args.db),
        registry_path=Path(args.registry),
        run_id=str(args.run_id),
        snapshot_id=str(args.snapshot_id),
        trained_at=_parse_timestamp(str(args.trained_at)),
    )


if __name__ == "__main__":
    raise SystemExit(_main())
