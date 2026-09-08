"""Independent Console API for user-configured Agent Cron tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent_cron.models import (
    ScheduledTask,
    ScheduledTaskRun,
    ScheduledTaskSkillRef,
    ScheduledTaskVersionConflictError,
)
from app.agent_cron.options import ScheduledTaskOptionService
from app.agent_cron.schedule import CronSchedule
from app.store import AutoReplyStore
from app.web_api.common import json_safe, snapshot_at


class ScheduledTaskSkillRefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    skill_source: Literal["managed", "operation"]
    skill_name: str = Field(min_length=1)
    managed_skill_id: int | None = Field(default=None, gt=0)
    managed_revision_id: int | None = Field(default=None, gt=0)
    position: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_source_identity(self) -> "ScheduledTaskSkillRefPayload":
        managed_ids = (self.managed_skill_id, self.managed_revision_id)
        if self.skill_source == "managed" and any(
            value is None for value in managed_ids
        ):
            raise ValueError("managed Skill ref requires an exact revision")
        if self.skill_source == "operation" and any(
            value is not None for value in managed_ids
        ):
            raise ValueError("operation Skill ref must not include managed identifiers")
        return self


class ScheduledTaskRuntimeOptionsPayload(BaseModel):
    """Non-sensitive per-run choices supported by the current Agent runners."""

    model_config = ConfigDict(extra="forbid", strict=True)

    thinking: Literal["low", "medium", "high", "xhigh"] | None = None


class ScheduledTaskCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    cron_expression: str = Field(min_length=1)
    timezone_name: str = Field(min_length=1)
    runtime_id: str = Field(min_length=1)
    runtime_options: ScheduledTaskRuntimeOptionsPayload = Field(
        default_factory=ScheduledTaskRuntimeOptionsPayload
    )
    working_directory: str = ""
    enabled: bool = True
    skill_refs: list[ScheduledTaskSkillRefPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ref_positions(self) -> "ScheduledTaskCreatePayload":
        if [ref.position for ref in self.skill_refs] != list(range(len(self.skill_refs))):
            raise ValueError("Skill ref positions must be contiguous")
        return self


class ScheduledTaskUpdatePayload(ScheduledTaskCreatePayload):
    version: int = Field(gt=0)


class ScheduledTaskVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(gt=0)


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": code, "message": message, "details": {}},
        status_code=status_code,
    )


def _skill_ref_payload(ref: ScheduledTaskSkillRef) -> dict[str, object]:
    return {
        "skill_source": ref.skill_source,
        "skill_name": ref.skill_name,
        "managed_skill_id": ref.managed_skill_id,
        "managed_revision_id": ref.managed_revision_id,
        "position": ref.position,
    }


def _run_payload(run: ScheduledTaskRun) -> dict[str, object]:
    return {
        "id": run.id,
        "event_id": run.event_id,
        "scheduled_task_id": run.scheduled_task_id,
        "trigger_kind": run.trigger_kind,
        "scheduled_for": _utc_text(run.scheduled_for),
        "dispatch_status": run.dispatch_status,
        "skip_or_error_reason": run.skip_or_error_reason,
        "execution_kind": run.execution_kind,
        "execution_id": run.execution_id,
        "created_at": _utc_text(run.created_at),
        "dispatched_at": _utc_text(run.dispatched_at),
        "snapshot": {
            "task_id": run.snapshot.task_id,
            "task_version": run.snapshot.task_version,
            "name": run.snapshot.name,
            "prompt": run.snapshot.prompt,
            "cron_expression": run.snapshot.cron_expression,
            "timezone_name": run.snapshot.timezone_name,
            "runtime_id": run.snapshot.runtime_id,
            "runtime_options": _safe_runtime_options(run.snapshot.runtime_options),
            "working_directory": run.snapshot.working_directory,
            "skill_refs": [
                _skill_ref_payload(ref) for ref in run.snapshot.skill_refs
            ],
        },
    }


def _task_payload(
    task: ScheduledTask,
    *,
    now: datetime,
    recent_run: ScheduledTaskRun | None,
) -> dict[str, object]:
    schedule = CronSchedule.parse(task.cron_expression, task.timezone_name)
    return {
        "id": task.id,
        "migration_key": task.migration_key,
        "name": task.name,
        "prompt": task.prompt,
        "cron_expression": task.cron_expression,
        "timezone_name": task.timezone_name,
        "schedule_description": schedule.describe(),
        "next_run_at": _utc_text(schedule.next_after(now)) if task.enabled else None,
        "runtime_id": task.runtime_id,
        "runtime_options": _safe_runtime_options(task.runtime_options),
        "working_directory": task.working_directory,
        "enabled": task.enabled,
        "version": task.version,
        "skill_refs": [_skill_ref_payload(ref) for ref in task.skill_refs],
        "recent_run": _run_payload(recent_run) if recent_run is not None else None,
        "created_at": _utc_text(task.created_at),
        "updated_at": _utc_text(task.updated_at),
        "deleted_at": _utc_text(task.deleted_at),
    }


def _item_envelope(item: object) -> dict[str, object]:
    return {"item": item, "meta": {"snapshot_at": snapshot_at()}}


def _safe_runtime_options(value: object) -> dict[str, object]:
    try:
        options = ScheduledTaskRuntimeOptionsPayload.model_validate(value)
    except ValidationError:
        return {}
    return options.model_dump(mode="json", exclude_none=True)


def _refs(payload: ScheduledTaskCreatePayload) -> tuple[ScheduledTaskSkillRef, ...]:
    return tuple(
        ScheduledTaskSkillRef(
            skill_source=ref.skill_source,
            skill_name=ref.skill_name,
            managed_skill_id=ref.managed_skill_id,
            managed_revision_id=ref.managed_revision_id,
            position=ref.position,
        )
        for ref in payload.skill_refs
    )


def _validate_choices(
    payload: ScheduledTaskCreatePayload,
    service: ScheduledTaskOptionService,
) -> None:
    runtime_options = {
        option.route_name: option for option in service.list_runtime_options()
    }
    if payload.runtime_id not in runtime_options:
        raise ValueError(f"runtime route {payload.runtime_id}: runtime_not_configured")
    thinking = payload.runtime_options.thinking
    if thinking is not None and thinking not in runtime_options[payload.runtime_id].supported_thinking:
        raise ValueError(
            f"runtime route {payload.runtime_id}: thinking_not_supported"
        )

    managed_options = {
        (skill.skill_id, skill.name, revision.revision_id)
        for skill in service.list_managed_skill_options()
        for revision in skill.revisions
    }
    operation_options = {
        option.name
        for option in service.list_operation_skill_options()
        if option.available
    }
    for ref in payload.skill_refs:
        if ref.skill_source == "managed":
            identity = (ref.managed_skill_id, ref.skill_name, ref.managed_revision_id)
            if identity not in managed_options:
                raise ValueError("managed Skill ref does not identify an exact revision")
        elif ref.skill_name not in operation_options:
            raise ValueError("operation Skill ref is unavailable")


def register_scheduled_task_routes(
    app: FastAPI,
    store_factory: Callable[[], AutoReplyStore],
    *,
    option_service_factory: Callable[[], ScheduledTaskOptionService],
    wake_callback: Callable[[], None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    current_time = now or (lambda: datetime.now(UTC))
    wake = wake_callback or (lambda: None)

    async def validated_payload(
        request: Request,
        model: type[BaseModel],
    ) -> BaseModel | JSONResponse:
        if "application/json" not in request.headers.get("content-type", ""):
            return _error("validation_error", "JSON Content-Type required", 415)
        try:
            raw = await request.json()
            return model.model_validate(raw)
        except (ValidationError, ValueError, TypeError) as exc:
            return _error("validation_error", str(exc), 422)

    def task_or_404(task_id: int, *, include_deleted: bool = False):
        task = store_factory().get_scheduled_task(
            task_id,
            include_deleted=include_deleted,
        )
        if task is None:
            return _error("not_found", "scheduled task does not exist", 404)
        return task

    def rendered_task(
        task: ScheduledTask,
        recent_run: ScheduledTaskRun | None,
    ) -> dict[str, object]:
        return _task_payload(
            task,
            now=current_time(),
            recent_run=recent_run,
        )

    def latest_run(task_id: int) -> ScheduledTaskRun | None:
        return store_factory().latest_scheduled_task_runs((task_id,)).get(task_id)

    @app.get("/api/console/scheduled-tasks")
    def scheduled_tasks_list() -> dict[str, object]:
        tasks = store_factory().list_scheduled_tasks()
        recent = store_factory().latest_scheduled_task_runs(
            tuple(task.id for task in tasks)
        )
        return {
            "items": [rendered_task(task, recent.get(task.id)) for task in tasks],
            "meta": {
                "snapshot_at": snapshot_at(),
                "total": len(tasks),
            },
        }

    @app.post("/api/console/scheduled-tasks", status_code=201, response_model=None)
    async def scheduled_task_create(
        request: Request,
    ) -> dict[str, object] | JSONResponse:
        parsed = await validated_payload(request, ScheduledTaskCreatePayload)
        if isinstance(parsed, JSONResponse):
            return parsed
        assert isinstance(parsed, ScheduledTaskCreatePayload)
        payload = parsed
        try:
            schedule = CronSchedule.parse(
                payload.cron_expression,
                payload.timezone_name,
            )
            _validate_choices(payload, option_service_factory())
            task = store_factory().create_scheduled_task(
                name=payload.name,
                prompt=payload.prompt,
                cron_expression=schedule.expression,
                timezone_name=schedule.timezone_name,
                runtime_id=payload.runtime_id,
                runtime_options=payload.runtime_options.model_dump(exclude_none=True),
                working_directory=payload.working_directory,
                skill_refs=_refs(payload),
                enabled=payload.enabled,
                now=current_time(),
            )
        except ValueError as exc:
            return _error("validation_error", str(exc), 422)
        return _item_envelope(rendered_task(task, None))

    @app.get("/api/console/scheduled-tasks/{task_id}", response_model=None)
    def scheduled_task_detail(task_id: int) -> dict[str, object] | JSONResponse:
        task = task_or_404(task_id)
        if isinstance(task, JSONResponse):
            return task
        return _item_envelope(rendered_task(task, latest_run(task.id)))

    @app.put("/api/console/scheduled-tasks/{task_id}", response_model=None)
    async def scheduled_task_update(
        task_id: int,
        request: Request,
    ) -> dict[str, object] | JSONResponse:
        current = task_or_404(task_id)
        if isinstance(current, JSONResponse):
            return current
        parsed = await validated_payload(request, ScheduledTaskUpdatePayload)
        if isinstance(parsed, JSONResponse):
            return parsed
        assert isinstance(parsed, ScheduledTaskUpdatePayload)
        payload = parsed
        if payload.enabled != current.enabled:
            return _error(
                "validation_error",
                "use the enable or disable endpoint to change enabled state",
                422,
            )
        try:
            schedule = CronSchedule.parse(
                payload.cron_expression,
                payload.timezone_name,
            )
            _validate_choices(payload, option_service_factory())
            task = store_factory().update_scheduled_task(
                task_id,
                expected_version=payload.version,
                name=payload.name,
                prompt=payload.prompt,
                cron_expression=schedule.expression,
                timezone_name=schedule.timezone_name,
                runtime_id=payload.runtime_id,
                runtime_options=payload.runtime_options.model_dump(exclude_none=True),
                working_directory=payload.working_directory,
                skill_refs=_refs(payload),
                now=current_time(),
            )
        except ScheduledTaskVersionConflictError as exc:
            return _error("conflict", str(exc), 409)
        except ValueError as exc:
            if str(exc) == "scheduled task does not exist":
                return _error("not_found", str(exc), 404)
            return _error("validation_error", str(exc), 422)
        return _item_envelope(rendered_task(task, latest_run(task.id)))

    def set_enabled(
        task_id: int,
        payload: ScheduledTaskVersionPayload,
        *,
        enabled: bool,
    ) -> dict[str, object] | JSONResponse:
        try:
            task = store_factory().set_scheduled_task_enabled(
                task_id,
                enabled=enabled,
                expected_version=payload.version,
                now=current_time(),
            )
        except ScheduledTaskVersionConflictError as exc:
            return _error("conflict", str(exc), 409)
        except ValueError as exc:
            return _error("not_found", str(exc), 404)
        return _item_envelope(rendered_task(task, latest_run(task.id)))

    @app.post(
        "/api/console/scheduled-tasks/{task_id}/enable", response_model=None
    )
    async def scheduled_task_enable(
        task_id: int,
        request: Request,
    ) -> dict[str, object] | JSONResponse:
        parsed = await validated_payload(request, ScheduledTaskVersionPayload)
        if isinstance(parsed, JSONResponse):
            return parsed
        assert isinstance(parsed, ScheduledTaskVersionPayload)
        payload = parsed
        return set_enabled(task_id, payload, enabled=True)

    @app.post(
        "/api/console/scheduled-tasks/{task_id}/disable", response_model=None
    )
    async def scheduled_task_disable(
        task_id: int,
        request: Request,
    ) -> dict[str, object] | JSONResponse:
        parsed = await validated_payload(request, ScheduledTaskVersionPayload)
        if isinstance(parsed, JSONResponse):
            return parsed
        assert isinstance(parsed, ScheduledTaskVersionPayload)
        payload = parsed
        return set_enabled(task_id, payload, enabled=False)

    @app.post(
        "/api/console/scheduled-tasks/{task_id}/run",
        status_code=201,
        response_model=None,
    )
    def scheduled_task_run(task_id: int) -> dict[str, object] | JSONResponse:
        if isinstance(task_or_404(task_id), JSONResponse):
            return _error("not_found", "scheduled task does not exist", 404)
        instant = current_time()
        try:
            run = store_factory().create_scheduled_task_run(
                task_id,
                trigger_kind="manual",
                scheduled_for=instant,
                now=instant,
            )
        except ValueError as exc:
            return _error("not_found", str(exc), 404)
        wake()
        return _item_envelope(_run_payload(run))

    @app.delete("/api/console/scheduled-tasks/{task_id}", response_model=None)
    def scheduled_task_delete(
        task_id: int,
        version: int = Query(gt=0),
    ) -> dict[str, object] | JSONResponse:
        try:
            task = store_factory().delete_scheduled_task(
                task_id,
                expected_version=version,
                now=current_time(),
            )
        except ScheduledTaskVersionConflictError as exc:
            return _error("conflict", str(exc), 409)
        except ValueError as exc:
            return _error("not_found", str(exc), 404)
        return _item_envelope(rendered_task(task, latest_run(task.id)))

    @app.get("/api/console/scheduled-tasks/{task_id}/runs", response_model=None)
    def scheduled_task_runs(
        task_id: int,
        cursor: int | None = Query(default=None, gt=0),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any] | JSONResponse:
        task = task_or_404(task_id, include_deleted=True)
        if isinstance(task, JSONResponse):
            return task
        page = store_factory().list_scheduled_task_runs_page(
            task_id,
            before_id=cursor,
            limit=page_size + 1,
        )
        has_more = len(page) > page_size
        runs = page[:page_size]
        return {
            "scheduled_task": rendered_task(task, latest_run(task.id)),
            "items": [_run_payload(run) for run in runs],
            "meta": {
                "snapshot_at": snapshot_at(),
                "page_size": page_size,
                "next_cursor": str(runs[-1].id) if has_more and runs else "",
                "has_more": has_more,
            },
        }

    @app.get("/api/console/scheduled-task-options")
    def scheduled_task_options() -> dict[str, object]:
        service = option_service_factory()
        return {
            "runtime_options": json_safe(
                [asdict(option) for option in service.list_runtime_options()]
            ),
            "managed_skill_options": json_safe(
                [asdict(option) for option in service.list_managed_skill_options()]
            ),
            "operation_skill_options": json_safe(
                [asdict(option) for option in service.list_operation_skill_options()]
            ),
            "meta": {"snapshot_at": snapshot_at()},
        }
