"""Declarative feature-to-skill relationships and persisted feature switches."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


_SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_STATE_WRITE_THREAD_LOCK = threading.RLock()


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    name: str
    description: str
    skills: tuple[str, ...]
    default_enabled: bool


@dataclass(frozen=True)
class FeatureState:
    feature_id: str
    enabled: bool


class FeatureRegistry:
    """Load the committed feature catalog and keep switch state separately."""

    def __init__(
        self,
        registry_path: Path | str | None = None,
        state_path: Path | str | None = None,
        project_skills_root: Path | str | None = None,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        self.registry_path = Path(registry_path or repository_root / "data/config/skill-features.json")
        self.state_path = Path(state_path or repository_root / "data/config/skill-state.json")
        self.project_skills_root = Path(project_skills_root or repository_root / "skills")
        self._features = self._load_registry()
        self._by_id = {feature.feature_id: feature for feature in self._features}
        self._states = self._load_state()

    def list_features(self) -> tuple[FeatureDefinition, ...]:
        return self._features

    def list_project_skill_names(self) -> tuple[str, ...]:
        if not self.project_skills_root.is_dir():
            return ()
        return tuple(
            sorted(
                directory.name
                for directory in self.project_skills_root.iterdir()
                if directory.is_dir()
                and not directory.is_symlink()
                and (directory / "SKILL.md").is_file()
            )
        )

    def skills_for(self, feature_id: str) -> tuple[str, ...]:
        return self._definition(feature_id).skills

    def features_for_skill(self, skill_name: str) -> tuple[str, ...]:
        _validate_name(skill_name, "skill name")
        return tuple(sorted(
            feature.feature_id
            for feature in self._features
            if skill_name in feature.skills
        ))

    def is_enabled(self, feature_id: str) -> bool:
        definition = self._definition(feature_id)
        return self._states.get(feature_id, definition.default_enabled)

    def set_enabled(self, feature_id: str, enabled: bool) -> FeatureState:
        self._definition(feature_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        # Lock and reread so separate registry instances cannot overwrite each
        # other's changes based on stale in-memory state.
        with self._state_lock():
            next_states = self._load_state()
            next_states[feature_id] = enabled
            self._write_state(next_states)
            self._states = next_states
        return FeatureState(feature_id=feature_id, enabled=enabled)

    def feature_enabled(self, feature_id: str) -> bool:
        """Single runtime-facing feature switch check."""
        return self.is_enabled(feature_id)

    def feature_status(self, feature_id: str, available_skills: set[str]) -> str:
        definition = self._definition(feature_id)
        return "ready" if set(definition.skills) <= set(available_skills) else "incomplete"

    def _definition(self, feature_id: str) -> FeatureDefinition:
        _validate_name(feature_id, "feature id")
        try:
            return self._by_id[feature_id]
        except KeyError as exc:
            raise KeyError(f"unknown feature id: {feature_id}") from exc

    def _load_registry(self) -> tuple[FeatureDefinition, ...]:
        payload = _read_json(self.registry_path, "feature registry")
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise ValueError("feature registry must contain a features list")
        definitions: list[FeatureDefinition] = []
        seen: set[str] = set()
        for raw in payload["features"]:
            if not isinstance(raw, dict):
                raise ValueError("each feature definition must be an object")
            feature_id = _required_string(raw, "feature_id")
            _validate_name(feature_id, "feature id")
            if feature_id in seen:
                raise ValueError(f"duplicate feature id: {feature_id}")
            seen.add(feature_id)
            name = _required_string(raw, "name")
            description = _required_string(raw, "description")
            skills = raw.get("skills")
            if not isinstance(skills, list) or not skills:
                raise ValueError(f"feature {feature_id!r} must have a non-empty skills list")
            validated_skills = tuple(
                _validated_string(skill, "skill name") for skill in skills
            )
            if len(set(validated_skills)) != len(validated_skills):
                raise ValueError(f"feature {feature_id!r} has duplicate skills")
            default_enabled = raw.get("default_enabled")
            if not isinstance(default_enabled, bool):
                raise ValueError(f"feature {feature_id!r} default_enabled must be boolean")
            definitions.append(
                FeatureDefinition(
                    feature_id=feature_id,
                    name=name,
                    description=description,
                    skills=validated_skills,
                    default_enabled=default_enabled,
                )
            )
        return tuple(definitions)

    def _load_state(self) -> dict[str, bool]:
        if not self.state_path.exists():
            return {}
        payload = _read_json(self.state_path, "feature state")
        if not isinstance(payload, dict):
            raise ValueError("feature state must be an object")
        if "features" in payload:
            if not isinstance(payload["features"], dict):
                raise ValueError("feature state features must be an object")
            values = payload["features"]
        else:
            values = payload
        states: dict[str, bool] = {}
        for feature_id, enabled in values.items():
            self._definition(feature_id)
            if not isinstance(enabled, bool):
                raise ValueError(f"feature state for {feature_id!r} must be boolean")
            states[feature_id] = enabled
        return states

    def _write_state(self, states: dict[str, bool]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
            dir=self.state_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"features": states}, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.state_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @contextmanager
    def _state_lock(self):
        lock_path = self.state_path.with_name(f"{self.state_path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _STATE_WRITE_THREAD_LOCK:
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


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label}: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 {label}: {path}") from exc


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _validated_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    _validate_name(value, label)
    return value


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a non-empty string")
    if not value or value in {".", ".."} or any(char not in _SAFE_NAME_CHARS for char in value):
        raise ValueError(f"invalid {label}: {value!r}")
