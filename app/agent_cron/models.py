from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterable
from typing import Any, Mapping


class ScheduledTaskVersionConflictError(ValueError):
    """Raised when an edit was based on an obsolete task version."""


def ensure_utc_datetime(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def parse_utc_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def canonical_json_object(value: Mapping[str, Any], *, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object")
    try:
        serialized = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decoded = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field} must be a JSON object")
    return serialized


def decode_json_object(value: str, *, field: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field} must be a JSON object")
    return decoded


def canonical_capabilities_json(value: Iterable[str], *, field: str) -> str:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a collection of capability names")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError(
            f"{field} must be a collection of capability names"
        ) from exc
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field} contains an invalid capability name")
    normalized = tuple(item.strip() for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} contains duplicate capability names")
    return json.dumps(sorted(normalized), ensure_ascii=False, separators=(",", ":"))


def decode_capabilities_json(value: str, *, field: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be a capability array") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"{field} must be a capability array")
    canonical = canonical_capabilities_json(decoded, field=field)
    return tuple(json.loads(canonical))


@dataclass(frozen=True)
class ScheduledTaskSkillRef:
    skill_source: str
    skill_name: str
    position: int
    scheduled_task_id: int = 0
    managed_skill_id: int | None = None
    managed_revision_id: int | None = None

    def __post_init__(self) -> None:
        if self.skill_source not in {"managed", "operation"}:
            raise ValueError("scheduled task Skill ref source is invalid")
        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("scheduled task Skill ref name must be nonempty")
        if (
            not isinstance(self.position, int)
            or isinstance(self.position, bool)
            or self.position < 0
        ):
            raise ValueError(
                "scheduled task Skill ref position must be a nonnegative integer"
            )
        if (
            not isinstance(self.scheduled_task_id, int)
            or isinstance(self.scheduled_task_id, bool)
            or self.scheduled_task_id < 0
        ):
            raise ValueError(
                "scheduled task Skill ref task id must be a nonnegative integer"
            )
        if self.skill_source == "managed":
            managed_ids = (self.managed_skill_id, self.managed_revision_id)
            if any(value is None for value in managed_ids):
                raise ValueError("managed Skill ref requires an exact revision")
            if any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in managed_ids
            ):
                raise ValueError("managed Skill ref identifiers must be positive")
        elif self.managed_skill_id is not None or self.managed_revision_id is not None:
            raise ValueError(
                "operation Skill ref must not include managed identifiers"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "scheduled_task_id": self.scheduled_task_id,
            "skill_source": self.skill_source,
            "skill_name": self.skill_name,
            "managed_skill_id": self.managed_skill_id,
            "managed_revision_id": self.managed_revision_id,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ScheduledTaskSkillRef":
        if not isinstance(value, dict):
            raise ValueError("scheduled task Skill ref snapshot must be an object")
        expected_keys = {
            "scheduled_task_id",
            "skill_source",
            "skill_name",
            "managed_skill_id",
            "managed_revision_id",
            "position",
        }
        if set(value) != expected_keys:
            raise ValueError("scheduled task Skill ref snapshot has invalid fields")
        if not isinstance(value["scheduled_task_id"], int):
            raise ValueError("scheduled task Skill ref task id must be an integer")
        if not isinstance(value["skill_source"], str):
            raise ValueError("scheduled task Skill ref source must be text")
        if not isinstance(value["skill_name"], str):
            raise ValueError("scheduled task Skill ref name must be text")
        if not isinstance(value["position"], int):
            raise ValueError("scheduled task Skill ref position must be an integer")
        for field in ("managed_skill_id", "managed_revision_id"):
            if value[field] is not None and not isinstance(value[field], int):
                raise ValueError(f"scheduled task Skill ref {field} must be an integer")
        return cls(
            scheduled_task_id=value["scheduled_task_id"],
            skill_source=value["skill_source"],
            skill_name=value["skill_name"],
            managed_skill_id=value["managed_skill_id"],
            managed_revision_id=value["managed_revision_id"],
            position=value["position"],
        )


@dataclass(frozen=True)
class ScheduledTask:
    id: int
    migration_key: str | None
    name: str
    prompt: str
    command: str
    cron_expression: str
    timezone_name: str
    runtime_id: str
    runtime_options_json: str
    required_runtime_capabilities_json: str
    working_directory: str
    enabled: bool
    version: int
    skill_refs: tuple[ScheduledTaskSkillRef, ...]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @property
    def runtime_options(self) -> dict[str, Any]:
        return decode_json_object(
            self.runtime_options_json,
            field="scheduled task runtime options",
        )

    @property
    def required_runtime_capabilities(self) -> tuple[str, ...]:
        return decode_capabilities_json(
            self.required_runtime_capabilities_json,
            field="scheduled task required runtime capabilities",
        )


@dataclass(frozen=True)
class ScheduledTaskSnapshot:
    task_id: int
    task_version: int
    name: str
    prompt: str
    command: str
    cron_expression: str
    timezone_name: str
    runtime_id: str
    runtime_options_json: str
    required_runtime_capabilities_json: str
    working_directory: str
    skill_refs: tuple[ScheduledTaskSkillRef, ...]

    @property
    def runtime_options(self) -> dict[str, Any]:
        return decode_json_object(
            self.runtime_options_json,
            field="scheduled task snapshot runtime options",
        )

    @property
    def required_runtime_capabilities(self) -> tuple[str, ...]:
        return decode_capabilities_json(
            self.required_runtime_capabilities_json,
            field="scheduled task snapshot required runtime capabilities",
        )

    @classmethod
    def from_task(cls, task: ScheduledTask) -> "ScheduledTaskSnapshot":
        return cls(
            task_id=task.id,
            task_version=task.version,
            name=task.name,
            prompt=task.prompt,
            command=task.command,
            cron_expression=task.cron_expression,
            timezone_name=task.timezone_name,
            runtime_id=task.runtime_id,
            runtime_options_json=task.runtime_options_json,
            required_runtime_capabilities_json=(
                task.required_runtime_capabilities_json
            ),
            working_directory=task.working_directory,
            skill_refs=task.skill_refs,
        )

    def to_json(self) -> str:
        payload = {
            "task_id": self.task_id,
            "task_version": self.task_version,
            "name": self.name,
            "prompt": self.prompt,
            "command": self.command,
            "cron_expression": self.cron_expression,
            "timezone_name": self.timezone_name,
            "runtime_id": self.runtime_id,
            "runtime_options": self.runtime_options,
            "required_runtime_capabilities": list(
                self.required_runtime_capabilities
            ),
            "working_directory": self.working_directory,
            "skill_refs": [ref.to_dict() for ref in self.skill_refs],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "ScheduledTaskSnapshot":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("scheduled task snapshot must be valid JSON") from exc
        expected_keys = {
            "task_id",
            "task_version",
            "name",
            "prompt",
            "command",
            "cron_expression",
            "timezone_name",
            "runtime_id",
            "runtime_options",
            "required_runtime_capabilities",
            "working_directory",
            "skill_refs",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("scheduled task snapshot has invalid fields")
        if (
            type(payload["task_id"]) is not int
            or type(payload["task_version"]) is not int
        ):
            raise ValueError("scheduled task snapshot identity is invalid")
        if payload["task_id"] <= 0 or payload["task_version"] <= 0:
            raise ValueError("scheduled task snapshot identity is invalid")
        text_fields = (
            "name",
            "prompt",
            "command",
            "cron_expression",
            "timezone_name",
            "runtime_id",
            "working_directory",
        )
        if any(not isinstance(payload[field], str) for field in text_fields):
            raise ValueError("scheduled task snapshot text field is invalid")
        runtime_options_json = canonical_json_object(
            payload["runtime_options"],
            field="scheduled task snapshot runtime options",
        )
        required_runtime_capabilities_json = canonical_capabilities_json(
            payload["required_runtime_capabilities"],
            field="scheduled task snapshot required runtime capabilities",
        )
        if not isinstance(payload["skill_refs"], list):
            raise ValueError("scheduled task snapshot Skill refs must be a list")
        refs = tuple(
            ScheduledTaskSkillRef.from_dict(item) for item in payload["skill_refs"]
        )
        if any(ref.scheduled_task_id != payload["task_id"] for ref in refs):
            raise ValueError("scheduled task snapshot Skill ref task mismatch")
        if tuple(ref.position for ref in refs) != tuple(range(len(refs))):
            raise ValueError(
                "scheduled task snapshot Skill ref positions must be contiguous"
            )
        return cls(
            task_id=payload["task_id"],
            task_version=payload["task_version"],
            name=payload["name"],
            prompt=payload["prompt"],
            command=payload["command"],
            cron_expression=payload["cron_expression"],
            timezone_name=payload["timezone_name"],
            runtime_id=payload["runtime_id"],
            runtime_options_json=runtime_options_json,
            required_runtime_capabilities_json=(
                required_runtime_capabilities_json
            ),
            working_directory=payload["working_directory"],
            skill_refs=refs,
        )


@dataclass(frozen=True)
class ScheduledTaskRun:
    id: int
    event_id: str
    scheduled_task_id: int
    trigger_kind: str
    scheduled_for: datetime
    dispatch_status: str
    skip_or_error_reason: str
    snapshot: ScheduledTaskSnapshot
    execution_kind: str
    execution_id: str
    lease_owner: str
    lease_expires_at: datetime | None
    created_at: datetime
    dispatched_at: datetime | None
