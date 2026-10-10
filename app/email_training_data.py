"""One external complete email training snapshot; SQLite keeps only summaries."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.email_training_snapshot import (
    FolderTrainingSnapshot,
    TrainingSnapshotObservation,
    validate_folder_training_snapshot,
)


class TrainingSnapshotUnavailable(RuntimeError):
    """The requested historical training payload is no longer retained."""


def data_directory(database: Path) -> Path:
    return database.with_name(database.stem + "-training-data")


def data_path(database: Path, digest: str) -> Path:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("training snapshot digest is invalid")
    return data_directory(database) / (digest + ".json")


def pin_path(database: Path, run_id: str) -> Path:
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for character in run_id):
        raise ValueError("training run id is invalid")
    return data_directory(database) / "pins" / (run_id + ".json")


def write_pin(database: Path, run_id: str, snapshot_id: str, digest: str) -> None:
    path = pin_path(database, run_id)
    read_snapshot(database, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pin-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            json.dump({"snapshot_id": snapshot_id, "digest": digest}, writer, sort_keys=True)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pinned_digest(database: Path, run_id: str, snapshot_id: str) -> str | None:
    path = pin_path(database, run_id)
    if not path.is_file():
        return None
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrainingSnapshotUnavailable("training snapshot pin is unavailable") from exc
    if pin.get("snapshot_id") != snapshot_id:
        return None
    return str(pin["digest"])


def write_snapshot(database: Path, snapshot: FolderTrainingSnapshot) -> Path:
    validate_folder_training_snapshot(snapshot, restored=True, allow_legacy_manifest=True)
    destination = data_path(database, snapshot.snapshot_digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if read_snapshot(database, snapshot.snapshot_digest).to_dict() != snapshot.to_dict():
            raise ValueError("external training snapshot digest conflict")
        return destination
    descriptor, temporary_name = tempfile.mkstemp(prefix=".training-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            json.dump(snapshot.to_dict(), writer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
        read_snapshot(database, snapshot.snapshot_digest)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_snapshot(database: Path, digest: str) -> FolderTrainingSnapshot:
    path = data_path(database, digest)
    if not path.is_file():
        raise TrainingSnapshotUnavailable("training snapshot data is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        restored = FolderTrainingSnapshot(
            snapshot_id=payload["snapshot_id"],
            snapshot_version=payload["snapshot_version"],
            description_version=payload["description_version"],
            input_schema_version=payload["input_schema_version"],
            seed=payload["seed"],
            observed_at=payload["observed_at"],
            observations=tuple(TrainingSnapshotObservation(**row) for row in payload["observations"]),
            snapshot_digest=payload["snapshot_digest"],
            _manifest_json=json.dumps(payload["manifest"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        validate_folder_training_snapshot(restored, restored=True, allow_legacy_manifest=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TrainingSnapshotUnavailable("training snapshot data is unavailable or corrupt") from exc
    if restored.snapshot_digest != digest:
        raise TrainingSnapshotUnavailable("training snapshot data digest mismatch")
    return restored


def summarize(snapshot: FolderTrainingSnapshot) -> dict[str, object]:
    rows = snapshot.observations
    categories: dict[str, int] = {}
    groups: dict[str, set[str]] = {}
    splits: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.category_key is not None:
            categories[row.category_key] = categories.get(row.category_key, 0) + 1
            groups.setdefault(row.category_key, set()).add(row.group_key)
        split = splits.setdefault(row.split, {"count": 0, "categories": set(), "important": set()})
        split["count"] += 1
        split["important"].add(row.important)
        if row.category_key and (row.split != "train" or row.selected_for_training):
            split["categories"].add(row.category_key)
    return {
        "sample_count": len(rows),
        "group_count": len({row.group_key for row in rows}),
        "category_sample_counts": categories,
        "category_group_counts": {key: len(value) for key, value in groups.items()},
        "splits": {key: {
            "count": value["count"],
            "categories": sorted(value["categories"]),
            "important": sorted(value["important"]),
        } for key, value in splits.items()},
    }


def prune_snapshots(database: Path, keep_digests: set[str]) -> list[Path]:
    """Remove obsolete external payloads after the caller establishes idle training."""
    removed: list[Path] = []
    for path in (data_directory(database) / "pins").glob("*.json"):
        path.unlink()
        removed.append(path)
    for path in data_directory(database).glob("*.json"):
        if path.stem not in keep_digests:
            path.unlink()
            removed.append(path)
    return removed
