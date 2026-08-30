"""Immutable local registry for CPU email classifier artifacts and manifests."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, get_args

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_model import CpuTfidfLogisticClassifier


ModelStatus = Literal["candidate", "active", "previous", "rejected", "failed"]
MODEL_STATUSES = frozenset(get_args(ModelStatus))
MODEL_FAMILY = "tfidf-logistic-regression"
MODEL_ID_PREFIX = "email-tfidf-lr-"
MAX_CPU_P95_MS = 100.0
_PROCESS_LOCK = threading.RLock()


class ModelRegistryError(RuntimeError):
    """The durable model registry is malformed or a transition is unsafe."""


def build_model_id(*, trained_at: datetime, artifact_sha256: str) -> str:
    if trained_at.tzinfo is None or trained_at.utcoffset() is None:
        raise ValueError("trained_at must be timezone-aware")
    digest = _digest(artifact_sha256)
    timestamp = trained_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"email-tfidf-lr-{timestamp}-{digest[:8]}"


@dataclass(frozen=True)
class EmailModelMetadata:
    model_id: str
    parent_model_id: str | None
    model_family: str
    tokenizer_version: str
    feature_version: str
    training_dataset_version: str
    trained_at: str
    training_started_at: str
    training_finished_at: str
    sample_count: int
    new_sample_count: int
    category_counts: Mapping[str, int]
    account_counts: Mapping[str, int]
    validation_method: str
    accuracy: float
    macro_f1: float
    per_category_metrics: Mapping[str, Mapping[str, object]]
    prediction_latency_p50_ms: float
    prediction_latency_p95_ms: float
    artifact_sha256: str
    status: ModelStatus
    promotion_reason: str
    failure_reason: str

    def __post_init__(self) -> None:
        if not self.model_id.startswith(MODEL_ID_PREFIX):
            raise ValueError("invalid email model_id")
        if self.model_family != MODEL_FAMILY:
            raise ValueError("unsupported email model family")
        _digest(self.artifact_sha256)
        for value in (
            self.trained_at,
            self.training_started_at,
            self.training_finished_at,
        ):
            _timestamp(value)
        for name, value in (
            ("sample_count", self.sample_count),
            ("new_sample_count", self.new_sample_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name, value in (("accuracy", self.accuracy), ("macro_f1", self.macro_f1)):
            _unit_float(name, value)
        for name, value in (
            ("prediction_latency_p50_ms", self.prediction_latency_p50_ms),
            ("prediction_latency_p95_ms", self.prediction_latency_p95_ms),
        ):
            if not isinstance(value, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative float")
        if self.status not in MODEL_STATUSES:
            raise ValueError("invalid email model status")
        object.__setattr__(self, "category_counts", dict(self.category_counts))
        object.__setattr__(self, "account_counts", dict(self.account_counts))
        object.__setattr__(
            self,
            "per_category_metrics",
            {key: dict(value) for key, value in self.per_category_metrics.items()},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "parent_model_id": self.parent_model_id,
            "model_family": self.model_family,
            "tokenizer_version": self.tokenizer_version,
            "feature_version": self.feature_version,
            "training_dataset_version": self.training_dataset_version,
            "trained_at": self.trained_at,
            "training_started_at": self.training_started_at,
            "training_finished_at": self.training_finished_at,
            "sample_count": self.sample_count,
            "new_sample_count": self.new_sample_count,
            "category_counts": dict(self.category_counts),
            "account_counts": dict(self.account_counts),
            "validation_method": self.validation_method,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "per_category_metrics": {
                key: dict(value) for key, value in self.per_category_metrics.items()
            },
            "prediction_latency_p50_ms": self.prediction_latency_p50_ms,
            "prediction_latency_p95_ms": self.prediction_latency_p95_ms,
            "artifact_sha256": self.artifact_sha256,
            "status": self.status,
            "promotion_reason": self.promotion_reason,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "EmailModelMetadata":
        try:
            return cls(
                model_id=_text(value["model_id"], "model_id"),
                parent_model_id=_optional_text(value.get("parent_model_id"), "parent_model_id"),
                model_family=_text(value["model_family"], "model_family"),
                tokenizer_version=_text(value["tokenizer_version"], "tokenizer_version"),
                feature_version=_text(value["feature_version"], "feature_version"),
                training_dataset_version=_text(
                    value["training_dataset_version"], "training_dataset_version"
                ),
                trained_at=_text(value["trained_at"], "trained_at"),
                training_started_at=_text(
                    value["training_started_at"], "training_started_at"
                ),
                training_finished_at=_text(
                    value["training_finished_at"], "training_finished_at"
                ),
                sample_count=_integer(value["sample_count"], "sample_count"),
                new_sample_count=_integer(value["new_sample_count"], "new_sample_count"),
                category_counts=_integer_mapping(value["category_counts"], "category_counts"),
                account_counts=_integer_mapping(value["account_counts"], "account_counts"),
                validation_method=_text(value["validation_method"], "validation_method"),
                accuracy=_float(value["accuracy"], "accuracy"),
                macro_f1=_float(value["macro_f1"], "macro_f1"),
                per_category_metrics=_mapping_of_mappings(
                    value["per_category_metrics"], "per_category_metrics"
                ),
                prediction_latency_p50_ms=_float(
                    value["prediction_latency_p50_ms"], "prediction_latency_p50_ms"
                ),
                prediction_latency_p95_ms=_float(
                    value["prediction_latency_p95_ms"], "prediction_latency_p95_ms"
                ),
                artifact_sha256=_text(value["artifact_sha256"], "artifact_sha256"),
                status=_status(value["status"]),
                promotion_reason=_text_allow_empty(
                    value.get("promotion_reason", ""), "promotion_reason"
                ),
                failure_reason=_text_allow_empty(
                    value.get("failure_reason", ""), "failure_reason"
                ),
            )
        except KeyError as exc:
            raise ValueError(f"missing model metadata field: {exc.args[0]}") from exc


@dataclass(frozen=True)
class ModelManifest:
    model_id: str
    artifact_sha256: str
    artifact: str
    metadata: str
    switched_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "artifact_sha256": self.artifact_sha256,
            "artifact": self.artifact,
            "metadata": self.metadata,
            "switched_at": self.switched_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ModelManifest":
        return cls(
            model_id=_text(value.get("model_id"), "model_id"),
            artifact_sha256=_digest(_text(value.get("artifact_sha256"), "artifact_sha256")),
            artifact=_relative_path(value.get("artifact"), "artifact"),
            metadata=_relative_path(value.get("metadata"), "metadata"),
            switched_at=_text(value.get("switched_at"), "switched_at"),
        )


@dataclass(frozen=True)
class ModelManifestSnapshot:
    active: ModelManifest | None
    previous: ModelManifest | None


@dataclass(frozen=True)
class ModelRecord:
    metadata: EmailModelMetadata
    status: ModelStatus
    status_reason: str
    artifact_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class ModelRuntimeFailure:
    event_id: str
    failed_model_id: str
    fallback_model_id: str
    reason: str
    occurred_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ModelRuntimeFailure":
        return cls(
            event_id=_text(value.get("event_id"), "event_id"),
            failed_model_id=_text(value.get("failed_model_id"), "failed_model_id"),
            fallback_model_id=_text(value.get("fallback_model_id"), "fallback_model_id"),
            reason=_text(value.get("reason"), "reason"),
            occurred_at=_text(value.get("occurred_at"), "occurred_at"),
        )


class EmailModelRegistry:
    """Append-only model records with atomically switched active manifests."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.artifacts = self.root / "artifacts"
        self.metadata = self.root / "metadata"
        self.lifecycle = self.root / "lifecycle"
        self.runtime_failures = self.root / "runtime-failures"
        self.runs = self.root / "runs"
        for path in (
            self.artifacts,
            self.metadata,
            self.lifecycle,
            self.runtime_failures,
            self.runs,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".registry.lock"

    def stage_candidate(
        self,
        source_artifact: str | Path,
        metadata: EmailModelMetadata,
        *,
        parity_texts: Sequence[str],
        expected_labels: Sequence[str],
    ) -> ModelRecord:
        if metadata.status != "candidate":
            raise ModelRegistryError("new model metadata must have candidate status")
        if len(parity_texts) != len(expected_labels) or not parity_texts:
            raise ModelRegistryError("reload parity inputs must be non-empty and aligned")
        source = Path(source_artifact)
        digest = _sha256_file(source)
        if digest != metadata.artifact_sha256:
            raise ModelRegistryError("candidate artifact digest does not match metadata")
        trained_at = _timestamp(metadata.trained_at)
        expected_id = build_model_id(trained_at=trained_at, artifact_sha256=digest)
        if metadata.model_id != expected_id:
            raise ModelRegistryError("candidate model_id does not match final artifact digest")
        artifact_path = self._artifact_path(metadata.model_id)
        metadata_path = self._metadata_path(metadata.model_id)
        with self._locked():
            if artifact_path.exists() or metadata_path.exists():
                raise ModelRegistryError(f"model artifact already exists: {metadata.model_id}")
            _copy_immutable(source, artifact_path)
            _write_json_immutable(metadata_path, metadata.to_dict())
            self._append_lifecycle(metadata.model_id, "candidate", metadata.promotion_reason)
        try:
            loaded = self.load_classifier(metadata.model_id)
            _validate_category_protocol(metadata, set(loaded.class_labels()))
            observed = tuple(loaded.predict(text).label for text in parity_texts)
            if observed != tuple(expected_labels):
                raise ModelRegistryError("candidate reload prediction parity failed")
        except Exception as exc:
            self._append_lifecycle(
                metadata.model_id,
                "failed",
                f"reload_verification_failed:{type(exc).__name__}",
            )
            if isinstance(exc, ModelRegistryError):
                raise
            raise ModelRegistryError("candidate reload verification failed") from exc
        return self.get_model(metadata.model_id)

    def promote(self, model_id: str, *, reason: str) -> ModelManifest:
        reason = _text(reason, "reason")
        with self._locked():
            candidate = self.get_model(model_id)
            if candidate.status != "candidate":
                raise ModelRegistryError("only a candidate model can be promoted")
            self.load_classifier(model_id)
            current = self._read_manifest(self.root / "active.json")
            if current is not None:
                self._verify_manifest(current)
                _write_json_atomic(self.root / "previous.json", current.to_dict())
                self._append_lifecycle(current.model_id, "previous", "superseded_by:" + model_id)
            manifest = self._manifest_for(candidate.metadata)
            _write_json_atomic(self.root / "active.json", manifest.to_dict())
            self._append_lifecycle(model_id, "active", reason)
            return manifest

    def snapshot_manifests(self) -> ModelManifestSnapshot:
        with self._locked():
            return ModelManifestSnapshot(
                active=self._read_manifest(self.root / "active.json"),
                previous=self._read_manifest(self.root / "previous.json"),
            )

    def restore_manifest_snapshot(
        self,
        snapshot: ModelManifestSnapshot,
        *,
        failed_model_id: str,
        reason: str,
    ) -> None:
        with self._locked():
            try:
                self._restore_manifest(self.root / "active.json", snapshot.active)
                self._restore_manifest(
                    self.root / "previous.json", snapshot.previous
                )
                if self._read_manifest(self.root / "active.json") != snapshot.active:
                    raise ModelRegistryError("active manifest restore mismatch")
                if self._read_manifest(self.root / "previous.json") != snapshot.previous:
                    raise ModelRegistryError("previous manifest restore mismatch")
                if snapshot.active is not None:
                    self._append_lifecycle(
                        snapshot.active.model_id,
                        "active",
                        "consistency_restore",
                    )
                if snapshot.previous is not None:
                    self._append_lifecycle(
                        snapshot.previous.model_id,
                        "previous",
                        "consistency_restore",
                    )
                self._append_lifecycle(failed_model_id, "failed", reason)
            except Exception as exc:
                try:
                    self._append_lifecycle(
                        failed_model_id,
                        "failed",
                        "promotion_consistency_restore_failed",
                    )
                except Exception:
                    pass
                raise ModelRegistryError(
                    "promotion consistency manifest restore failed"
                ) from exc

    def reject(self, model_id: str, *, reason: str) -> None:
        record = self.get_model(model_id)
        if record.status != "candidate":
            raise ModelRegistryError("only a candidate model can be rejected")
        self._append_lifecycle(model_id, "rejected", _text(reason, "reason"))

    def mark_failed(self, model_id: str, *, reason: str) -> None:
        self.get_model(model_id)
        self._append_lifecycle(model_id, "failed", _text(reason, "reason"))

    def fallback_to_previous(
        self,
        *,
        reason: str,
        failed_model_id: str,
        occurred_at: datetime | None = None,
    ) -> ModelManifest:
        with self._locked():
            active = self._read_manifest_unverified(self.root / "active.json")
            previous = self._read_manifest(self.root / "previous.json")
            if active is None or active.model_id != failed_model_id:
                raise ModelRegistryError("failed model is not the active model")
            if previous is None or previous.model_id == failed_model_id:
                raise ModelRegistryError("no distinct previous model is available")
            self._verify_manifest(previous)
            switched = replace(previous, switched_at=_format_timestamp(occurred_at))
            _write_json_atomic(self.root / "active.json", switched.to_dict())
            self._append_lifecycle(failed_model_id, "failed", reason)
            self._append_lifecycle(previous.model_id, "active", "runtime_fallback")
            event_time = _format_timestamp(occurred_at)
            event = ModelRuntimeFailure(
                event_id=uuid.uuid4().hex,
                failed_model_id=failed_model_id,
                fallback_model_id=previous.model_id,
                reason=_text(reason, "reason"),
                occurred_at=event_time,
            )
            _write_json_immutable(
                self.runtime_failures / f"{event_time.replace(':', '')}-{event.event_id}.json",
                event.__dict__,
            )
            return switched

    def active_manifest(self) -> ModelManifest | None:
        return self._read_manifest(self.root / "active.json")

    def active_model_id_unverified(self) -> str | None:
        manifest = self._read_manifest_unverified(self.root / "active.json")
        return manifest.model_id if manifest is not None else None

    def previous_manifest(self) -> ModelManifest | None:
        return self._read_manifest(self.root / "previous.json")

    def get_model(self, model_id: str) -> ModelRecord:
        metadata_path = self._metadata_path(model_id)
        artifact_path = self._artifact_path(model_id)
        if not metadata_path.exists() or not artifact_path.exists():
            raise ModelRegistryError(f"unknown email model: {model_id}")
        payload = _read_json(metadata_path)
        try:
            metadata = EmailModelMetadata.from_mapping(payload)
        except ValueError as exc:
            raise ModelRegistryError(f"invalid model metadata: {model_id}") from exc
        if metadata.model_id != model_id:
            raise ModelRegistryError("model metadata identity mismatch")
        status, reason = self._latest_lifecycle(model_id, metadata.status, metadata.promotion_reason)
        return ModelRecord(metadata, status, reason, artifact_path, metadata_path)

    def load_classifier(self, model_id: str) -> CpuTfidfLogisticClassifier:
        record = self.get_model(model_id)
        if _sha256_file(record.artifact_path) != record.metadata.artifact_sha256:
            raise ModelRegistryError("model artifact digest verification failed")
        try:
            classifier = CpuTfidfLogisticClassifier.load(record.artifact_path)
        except Exception as exc:
            raise ModelRegistryError("model artifact cannot be loaded") from exc
        classifier.model_version = model_id
        observed = set(classifier.class_labels())
        _validate_category_protocol(record.metadata, observed)
        return classifier

    def list_runtime_failures(self) -> list[ModelRuntimeFailure]:
        result: list[ModelRuntimeFailure] = []
        for path in sorted(self.runtime_failures.glob("*.json")):
            result.append(ModelRuntimeFailure.from_mapping(_read_json(path)))
        return result

    def _manifest_for(self, metadata: EmailModelMetadata) -> ModelManifest:
        return ModelManifest(
            model_id=metadata.model_id,
            artifact_sha256=metadata.artifact_sha256,
            artifact=str(self._artifact_path(metadata.model_id).relative_to(self.root)),
            metadata=str(self._metadata_path(metadata.model_id).relative_to(self.root)),
            switched_at=_format_timestamp(),
        )

    @staticmethod
    def _restore_manifest(path: Path, manifest: ModelManifest | None) -> None:
        if manifest is None:
            path.unlink(missing_ok=True)
        else:
            _write_json_atomic(path, manifest.to_dict())

    def _verify_manifest(self, manifest: ModelManifest) -> None:
        record = self.get_model(manifest.model_id)
        expected_artifact = self.root / manifest.artifact
        expected_metadata = self.root / manifest.metadata
        if expected_artifact != record.artifact_path or expected_metadata != record.metadata_path:
            raise ModelRegistryError("model manifest paths do not match registry")
        if manifest.artifact_sha256 != record.metadata.artifact_sha256:
            raise ModelRegistryError("model manifest digest mismatch")
        self.load_classifier(manifest.model_id)

    def _read_manifest(self, path: Path) -> ModelManifest | None:
        try:
            manifest = self._read_manifest_unverified(path)
            if manifest is None:
                return None
            self._verify_manifest(manifest)
            return manifest
        except (OSError, ValueError, ModelRegistryError) as exc:
            raise ModelRegistryError(f"invalid model manifest: {path.name}") from exc

    def _read_manifest_unverified(self, path: Path) -> ModelManifest | None:
        if not path.exists():
            return None
        try:
            return ModelManifest.from_mapping(_read_json(path))
        except (OSError, ValueError, ModelRegistryError) as exc:
            raise ModelRegistryError(f"invalid model manifest: {path.name}") from exc

    def _artifact_path(self, model_id: str) -> Path:
        return self.artifacts / f"{_model_id(model_id)}.pkl"

    def _metadata_path(self, model_id: str) -> Path:
        return self.metadata / f"{_model_id(model_id)}.json"

    def _append_lifecycle(self, model_id: str, status: ModelStatus, reason: str) -> None:
        event_id = uuid.uuid4().hex
        _write_json_immutable(
            self.lifecycle / f"{model_id}-{event_id}.json",
            {
                "event_id": event_id,
                "model_id": model_id,
                "status": status,
                "reason": reason,
                "occurred_at": _format_timestamp(),
            },
        )

    def _latest_lifecycle(
        self, model_id: str, default_status: ModelStatus, default_reason: str
    ) -> tuple[ModelStatus, str]:
        events: list[tuple[str, str, ModelStatus, str]] = []
        for path in self.lifecycle.glob(f"{model_id}-*.json"):
            payload = _read_json(path)
            events.append(
                (
                    _text(payload.get("occurred_at"), "occurred_at"),
                    _text(payload.get("event_id"), "event_id"),
                    _status(payload.get("status")),
                    _text_allow_empty(payload.get("reason", ""), "reason"),
                )
            )
        if not events:
            return default_status, default_reason
        _, _, status, reason = max(events)
        return status, reason

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with _PROCESS_LOCK:
            self._lock_path.touch(exist_ok=True)
            with self._lock_path.open("r+b") as handle:
                _lock_file(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _copy_immutable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
    except FileExistsError as exc:
        raise ModelRegistryError(f"immutable path already exists: {destination.name}") from exc


def _write_json_immutable(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ModelRegistryError(f"immutable path already exists: {path.name}") from exc


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelRegistryError(f"invalid registry JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ModelRegistryError(f"invalid registry JSON object: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _format_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return current.astimezone(timezone.utc).isoformat()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _digest(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("artifact_sha256 must be 64 lowercase hexadecimal characters")
    return normalized


def _model_id(value: object) -> str:
    result = _text(value, "model_id")
    if not result.startswith(MODEL_ID_PREFIX) or Path(result).name != result:
        raise ModelRegistryError("invalid model_id path component")
    return result


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _text_allow_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{field} must be a string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _relative_path(value: object, field: str) -> str:
    result = _text(value, field)
    path = Path(result)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a safe relative path")
    return result


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _unit_float(name: str, value: float) -> None:
    if not isinstance(value, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a finite float between 0 and 1")


def _integer_mapping(value: object, field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, int] = {}
    for key, item in value.items():
        result[_text(key, f"{field} key")] = _integer(item, f"{field} value")
    return result


def _mapping_of_mappings(value: object, field: str) -> dict[str, dict[str, object]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, dict[str, object]] = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} values must be objects")
        result[_text(key, f"{field} key")] = dict(item)
    return result


def _status(value: object) -> ModelStatus:
    result = _text(value, "status")
    if result not in MODEL_STATUSES:
        raise ValueError("invalid email model status")
    return result  # type: ignore[return-value]


def _validate_category_protocol(
    metadata: EmailModelMetadata, artifact_labels: set[str]
) -> None:
    allowed = {category.value for category in EmailCategory}
    category_labels = set(metadata.category_counts)
    metric_labels = set(metadata.per_category_metrics)
    if (
        not artifact_labels
        or artifact_labels != category_labels
        or artifact_labels != metric_labels
        or not artifact_labels <= allowed
    ):
        raise ModelRegistryError("candidate category protocol mismatch")
    for label, metric in metadata.per_category_metrics.items():
        required = {
            "precision",
            "recall",
            "f1",
            "validation_sample_count",
            "configured_threshold",
            "minimum_validation_samples",
            "auto_action_eligible",
            "eligibility_reason",
        }
        if set(metric) != required:
            raise ModelRegistryError(f"candidate metrics protocol mismatch: {label}")
        for field in ("precision", "recall", "f1", "configured_threshold"):
            value = metric[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
            ):
                raise ModelRegistryError(f"candidate metric {field} is invalid: {label}")
        for field in ("validation_sample_count", "minimum_validation_samples"):
            value = metric[field]
            minimum = 0 if field == "validation_sample_count" else 1
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ModelRegistryError(f"candidate metric {field} is invalid: {label}")
        if not isinstance(metric["auto_action_eligible"], bool):
            raise ModelRegistryError(
                f"candidate metric auto_action_eligible is invalid: {label}"
            )
        if not isinstance(metric["eligibility_reason"], str) or not metric[
            "eligibility_reason"
        ].strip():
            raise ModelRegistryError(
                f"candidate metric eligibility_reason is invalid: {label}"
            )
