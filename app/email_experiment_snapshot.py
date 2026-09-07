"""Privacy-bounded, reloadable snapshots for offline email experiments."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from app.email_classifier_contracts import EmailCategory
from app.email_store import _validate_model_text


SNAPSHOT_VERSION = "email-experiment-snapshot-v1"
_EXAMPLE_FIELDS = frozenset(
    {"message_id", "model_text", "label", "received_at", "source_group"}
)
_SERIALIZED_EXAMPLE_FIELDS = frozenset(
    {"label", "model_text", "received_date", "sample_id_digest", "source_group_digest"}
)


class EmailExperimentSnapshotError(ValueError):
    """The snapshot is malformed or contains data outside the experiment contract."""


@dataclass(frozen=True)
class EmailExperimentSnapshot:
    snapshot_version: str
    captured_at: str
    label_source: str
    examples: tuple[dict[str, str], ...]
    snapshot_digest: str

    @property
    def sample_count(self) -> int:
        return len(self.examples)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_version": self.snapshot_version,
            "captured_at": self.captured_at,
            "label_source": self.label_source,
            "sample_count": self.sample_count,
            "examples": [dict(example) for example in self.examples],
            "snapshot_digest": self.snapshot_digest,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "EmailExperimentSnapshot":
        if not isinstance(value, Mapping):
            raise EmailExperimentSnapshotError("snapshot must be an object")
        expected_fields = {
            "snapshot_version",
            "captured_at",
            "label_source",
            "sample_count",
            "examples",
            "snapshot_digest",
        }
        unsupported = set(value) - expected_fields
        if unsupported:
            raise EmailExperimentSnapshotError(
                f"unsupported field: {sorted(unsupported)[0]}"
            )
        if value.get("snapshot_version") != SNAPSHOT_VERSION:
            raise EmailExperimentSnapshotError("unsupported snapshot version")
        captured_at = _timestamp(value.get("captured_at"), "captured_at")
        label_source = _text(value.get("label_source"), "label_source")
        examples_value = value.get("examples")
        if not isinstance(examples_value, Sequence) or isinstance(
            examples_value, (str, bytes)
        ):
            raise EmailExperimentSnapshotError("examples must be a list")
        examples = tuple(_validate_serialized_example(item) for item in examples_value)
        sample_count = value.get("sample_count")
        if isinstance(sample_count, bool) or sample_count != len(examples):
            raise EmailExperimentSnapshotError("sample_count does not match examples")
        digest = _text(value.get("snapshot_digest"), "snapshot_digest")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise EmailExperimentSnapshotError("snapshot_digest must be sha256 hex")
        snapshot = cls(
            snapshot_version=SNAPSHOT_VERSION,
            captured_at=captured_at,
            label_source=label_source,
            examples=examples,
            snapshot_digest=digest,
        )
        if digest != _snapshot_digest(snapshot):
            raise EmailExperimentSnapshotError("snapshot digest mismatch")
        return snapshot


def build_snapshot(
    examples: Sequence[Mapping[str, object]],
    *,
    captured_at: datetime,
    label_source: str,
) -> EmailExperimentSnapshot:
    """Normalize live experiment rows into a safe, reloadable snapshot."""

    captured = _datetime(captured_at, "captured_at")
    source = _text(label_source, "label_source")
    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for example in examples:
        if not isinstance(example, Mapping):
            raise EmailExperimentSnapshotError("example must be an object")
        unsupported = set(example) - _EXAMPLE_FIELDS
        if unsupported:
            raise EmailExperimentSnapshotError(
                f"unsupported field: {sorted(unsupported)[0]}"
            )
        message_id = _text(example.get("message_id"), "message_id")
        if message_id in seen_ids:
            raise EmailExperimentSnapshotError("duplicate message_id")
        seen_ids.add(message_id)
        model_text = _text(example.get("model_text"), "model_text")
        try:
            _validate_model_text(model_text)
        except ValueError as exc:
            raise EmailExperimentSnapshotError(str(exc)) from exc
        label = _text(example.get("label"), "label")
        if label not in {category.value for category in EmailCategory}:
            raise EmailExperimentSnapshotError(f"unsupported label: {label}")
        received_at = _datetime(example.get("received_at"), "received_at")
        source_group = _text(example.get("source_group"), "source_group")
        normalized.append(
            {
                "label": label,
                "model_text": model_text,
                "received_date": received_at.date().isoformat(),
                "sample_id_digest": _hex_digest(message_id),
                "source_group_digest": _hex_digest(source_group.lower()),
            }
        )
    snapshot = EmailExperimentSnapshot(
        snapshot_version=SNAPSHOT_VERSION,
        captured_at=captured.isoformat(),
        label_source=source,
        examples=tuple(normalized),
        snapshot_digest="",
    )
    return EmailExperimentSnapshot(
        snapshot_version=snapshot.snapshot_version,
        captured_at=snapshot.captured_at,
        label_source=snapshot.label_source,
        examples=snapshot.examples,
        snapshot_digest=_snapshot_digest(snapshot),
    )


def save_snapshot(path: str | Path, snapshot: EmailExperimentSnapshot) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validated = EmailExperimentSnapshot.from_mapping(snapshot.to_dict())
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
                validated.to_dict(),
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_snapshot(path: str | Path) -> EmailExperimentSnapshot:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise EmailExperimentSnapshotError("could not read snapshot") from exc
    return EmailExperimentSnapshot.from_mapping(value)


def _validate_serialized_example(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise EmailExperimentSnapshotError("example must be an object")
    unsupported = set(value) - _SERIALIZED_EXAMPLE_FIELDS
    if unsupported:
        raise EmailExperimentSnapshotError(
            f"unsupported field: {sorted(unsupported)[0]}"
        )
    result = {
        key: _text(value.get(key), key) for key in _SERIALIZED_EXAMPLE_FIELDS
    }
    try:
        _validate_model_text(result["model_text"])
    except ValueError as exc:
        raise EmailExperimentSnapshotError(str(exc)) from exc
    if result["label"] not in {category.value for category in EmailCategory}:
        raise EmailExperimentSnapshotError(f"unsupported label: {result['label']}")
    try:
        datetime.fromisoformat(result["received_date"])
    except ValueError as exc:
        raise EmailExperimentSnapshotError("received_date must be ISO date") from exc
    for field in ("sample_id_digest", "source_group_digest"):
        digest = result[field]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise EmailExperimentSnapshotError(f"{field} must be sha256 hex")
    return {key: result[key] for key in sorted(result)}


def _snapshot_digest(snapshot: EmailExperimentSnapshot) -> str:
    return deterministic_payload_digest(
        {
            "snapshot_version": snapshot.snapshot_version,
            "captured_at": snapshot.captured_at,
            "label_source": snapshot.label_source,
            "examples": [dict(example) for example in snapshot.examples],
        }
    )


def deterministic_payload_digest(value: object) -> str:
    """Hash one JSON-domain value using its canonical UTF-8 representation."""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EmailExperimentSnapshotError(
            "digest payload must contain only finite JSON values"
        ) from exc
    return sha256(payload).hexdigest()


def _hex_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmailExperimentSnapshotError(f"{name} must be a non-empty string")
    return value


def _datetime(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise EmailExperimentSnapshotError(
                f"{name} must be an ISO timestamp"
            ) from exc
    else:
        raise EmailExperimentSnapshotError(f"{name} must be timezone-aware")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmailExperimentSnapshotError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: object, name: str) -> str:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EmailExperimentSnapshotError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmailExperimentSnapshotError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()
