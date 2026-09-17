"""Give every open TODO a deadline, then mirror it to DingTalk Todo.

Derek, 2026-09-17: every TODO must have a deadline. New TODOs are held to that
at creation (`_validate_task_agent_decision`). This backfill applies the same
rule to TODOs created before it existed: use the deadline the TODO states,
otherwise infer a reasonable one from its scope and urgency.

A TODO without a deadline is never mirrored to DingTalk Todo, so giving one a
deadline is also what makes it reach its owner. The mirror goes through the
same `task_todo_sync_outbox` path as every other TODO, so delivery keeps its
idempotency and retry behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.task_models import TodoStatus, WorkProject, WorkTodo
from app.todo_sync import _deadline_to_iso, _parse_datetime

TODO_DEADLINE_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "todo_deadline_decision.schema.json"
)
OPEN_TODO_STATUSES = (TodoStatus.OPEN.value, TodoStatus.WAITING_OWNER.value)


class TodoDeadlineDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_at: str = Field(
        min_length=1,
        description="ISO 8601 datetime with timezone, later than the current time.",
    )
    reason: str = Field(
        min_length=1,
        description="One sentence: stated in the source, or inferred from scope and urgency.",
    )


@dataclass
class TodoDeadlineBackfillResult:
    inspected: int = 0
    deadlines_set: int = 0
    mirrors_queued: int = 0
    failed: int = 0
    dry_run: bool = True
    decisions: list[dict[str, object]] = field(default_factory=list)


def list_open_todos_without_deadline(store, *, limit: int | None = None) -> list[WorkTodo]:
    todos = [
        todo
        for todo in store.list_work_todos(statuses=OPEN_TODO_STATUSES)
        if not _deadline_to_iso(todo.deadline_at)
    ]
    return todos if limit is None else todos[:limit]


def build_todo_deadline_prompt(
    *, todo: WorkTodo, project: WorkProject | None, now: str
) -> str:
    payload = {
        "todo": {
            "title": todo.title,
            "description": todo.description,
            "owner_name": todo.owner_name,
            "priority": todo.priority.value,
            "status": todo.status.value,
            "created_at": todo.created_at,
            "next_follow_up_at": todo.next_follow_up_at,
            "follow_up_question": todo.follow_up_question,
            "blocker": todo.blocker,
        },
        "project": (
            None
            if project is None
            else {
                "title": project.title,
                "status": project.status.value,
                "priority": project.priority.value,
            }
        ),
    }
    return f"""你是 CEO Agent 的 TODO 截止日期补全 agent。

当前时间：{now}

规则：
- 这个 TODO 仍未完成，必须给出一个截止日期 deadline_at（ISO 8601，带时区，例如 2026-09-30T18:00:00+08:00）。
- 如果标题、描述或跟进问题里写明了截止日期，用它。
- 没写明时，根据工作量和紧急程度推一个合理日期；参考 priority、项目状态、created_at 和 next_follow_up_at。
- 截止日期必须晚于当前时间：这是一项仍在进行的工作，过去的日期没有意义。
- reason 用一句话说明日期从哪来（原文写明 / 按工作量推断）。
- 只输出 TodoDeadlineDecision JSON，不要修改其他字段，不要发送消息。

TODO:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def validate_todo_deadline(decision: TodoDeadlineDecision, *, now: str) -> str:
    """Return the normalized deadline, or raise when it is unusable."""
    deadline = _deadline_to_iso(decision.deadline_at)
    if not deadline:
        raise ValueError(f"deadline_at is not a datetime: {decision.deadline_at!r}")
    parsed = _parse_datetime(deadline)
    current = _parse_datetime(now)
    if parsed is not None and current is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if parsed <= current:
            raise ValueError(f"deadline_at is not after now: {deadline}")
    return deadline


def parse_todo_deadline_decision(raw: str) -> TodoDeadlineDecision:
    """Read the decision from a Codex JSONL stream or a plain-text reply.

    Same approach as the task agent's parser: collect deadline-shaped objects
    from Codex event texts and from the whole reply, then take the last one
    that validates, so a later final answer outranks an earlier draft.
    """
    from pydantic import ValidationError

    from app.agent_result import agent_message_json_objects

    stripped = raw.strip()
    candidates: list[object] = []
    for line in stripped.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(payload)
        candidates.extend(_nested_text_objects(payload))
    candidates.extend(agent_message_json_objects(stripped))
    for candidate in reversed(candidates):
        if not (isinstance(candidate, dict) and "deadline_at" in candidate):
            continue
        try:
            return TodoDeadlineDecision.model_validate(candidate)
        except ValidationError:
            continue
    raise ValueError("no TodoDeadlineDecision JSON found")


def _nested_text_objects(payload: object) -> list[object]:
    from app.agent_result import agent_message_json_objects

    found: list[object] = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, str) and "deadline_at" in value:
                found.extend(agent_message_json_objects(value))
            elif isinstance(value, (dict, list)):
                found.extend(_nested_text_objects(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(_nested_text_objects(value))
    return found


def _deadline_repair_prompt(raw_output: str, *, now: str) -> str:
    """Tell the model which rule its last deadline broke."""
    try:
        decision = parse_todo_deadline_decision(raw_output)
    except ValueError:
        detail = (
            "- the reply contained no TodoDeadlineDecision JSON object with "
            "deadline_at and reason"
        )
    else:
        try:
            validate_todo_deadline(decision, now=now)
        except ValueError as exc:
            detail = f"- {exc}"
        else:
            detail = "- the decision could not be stored as returned"
    return (
        "The previous output was not accepted as a TodoDeadlineDecision. "
        "Return exactly one JSON object with deadline_at and reason.\n\n"
        f"Problems in the previous output:\n{detail}\n\n"
        f"deadline_at must be an ISO 8601 datetime with a timezone and must be "
        f"later than {now}."
    )


class TodoDeadlineCodexRunner:
    def __init__(self, *, store, workspace: Path, timeout_seconds: int, idle_timeout_seconds: int):
        from app.agent_runtime_production import build_production_routed_codex_execution

        self.routed_execution = build_production_routed_codex_execution(
            store=store,
            workspace=workspace,
            total_timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        )

    def infer(self, *, todo: WorkTodo, project: WorkProject | None, now: str) -> TodoDeadlineDecision:
        from app.agent_runtime_router import (
            CodexCommandFactory,
            RoutedResultCodec,
            RoutedResultValidationError,
            RoutedResultValidationRetry,
        )

        def parse_and_validate(raw: str) -> str:
            # A rejection is raised as a result-validation failure so the reason
            # reaches the service log and the turn gets its one correction.
            try:
                decision = parse_todo_deadline_decision(raw)
                validate_todo_deadline(decision, now=now)
            except ValueError as exc:
                raise RoutedResultValidationError(str(exc), raw_output=raw) from exc
            return decision.model_dump_json()

        result = self.routed_execution.execute(
            workload_kind="task",
            workload_key=f"{todo.id}:deadline_backfill",
            prompt=build_todo_deadline_prompt(todo=todo, project=project, now=now),
            command_factory=CodexCommandFactory.standard(
                developer_instructions=(
                    "Infer one deadline for an existing open TODO and return the "
                    "requested structured decision. Do not write data or send messages."
                ),
                output_schema_path=TODO_DEADLINE_DECISION_SCHEMA_PATH,
                use_output_schema=True,
            ),
            parser=parse_and_validate,
            result_codec=RoutedResultCodec.text(schema_id="todo_deadline_decision.v1"),
            conversation_id=None,
            required_capabilities=frozenset({"structured_output"}),
            result_validation_retry=RoutedResultValidationRetry.same_session_exactly_once(
                correction_prompt=lambda raw: _deadline_repair_prompt(raw, now=now)
            ),
        )
        return TodoDeadlineDecision.model_validate_json(result.value)


def backfill_todo_deadlines(
    store,
    runner,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    now: str = "",
) -> TodoDeadlineBackfillResult:
    effective_now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = TodoDeadlineBackfillResult(dry_run=dry_run)
    for todo in list_open_todos_without_deadline(store, limit=limit):
        result.inspected += 1
        project = store.get_work_project(todo.project_id)
        try:
            decision = runner.infer(todo=todo, project=project, now=effective_now)
            deadline = validate_todo_deadline(decision, now=effective_now)
        except Exception as exc:  # noqa: BLE001 - one TODO must not stop the batch
            result.failed += 1
            result.decisions.append({"todo_id": todo.id, "error": str(exc)[:300]})
            if not dry_run:
                # A dry run must leave no trace, including in Attention.
                store.record_error(
                    None, None, "todo_deadline_backfill", f"todo_id={todo.id}: {exc}"
                )
            continue
        will_mirror = bool(todo.owner_user_id.strip())
        result.decisions.append({
            "todo_id": todo.id,
            "title": todo.title,
            "owner_name": todo.owner_name,
            "deadline_at": deadline,
            "reason": decision.reason,
            "mirrors_to_dingtalk": will_mirror,
        })
        if dry_run:
            continue
        store.update_work_todo(todo.id, deadline_at=deadline)
        result.deadlines_set += 1
        if will_mirror:
            store.enqueue_task_todo_sync_outbox(
                operation_key=f"deadline-backfill:{todo.id}:create",
                work_todo_id=todo.id,
                operation="create",
            )
            result.mirrors_queued += 1
    return result
