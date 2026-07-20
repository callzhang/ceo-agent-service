import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.universal_context import UniversalTaskContext
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalPlan


@dataclass(frozen=True)
class UniversalPlanExecution:
    execution_scope_id: str
    execution_generation: str
    plan: UniversalPlan

    def __post_init__(self) -> None:
        if (
            not isinstance(self.execution_scope_id, str)
            or not self.execution_scope_id.strip()
        ):
            raise ValueError("execution_scope_id must be non-empty")
        if (
            not isinstance(self.execution_generation, str)
            or not self.execution_generation.strip()
        ):
            raise ValueError("execution_generation must be non-empty")
        if not isinstance(self.plan, UniversalPlan):
            raise TypeError("plan must be UniversalPlan")
        object.__setattr__(self, "plan", self.plan.model_copy(deep=True))


@dataclass(frozen=True)
class UniversalActionExecution:
    execution_id: str
    execution_scope_id: str
    action_hash: str
    context: UniversalTaskContext
    action_index: int
    action: PlannedAction


class UniversalActionExecutionState(StrEnum):
    NOT_STARTED = "not_started"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


def build_universal_action_execution(
    context: UniversalTaskContext,
    plan_execution: UniversalPlanExecution,
    action: PlannedAction,
    action_index: int,
) -> UniversalActionExecution:
    if plan_execution.execution_generation != context.execution_generation:
        raise ValueError("execution generation mismatch")
    canonical_action = json.dumps(
        action.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    action_hash = hashlib.sha256(canonical_action.encode("utf-8")).hexdigest()
    execution_key = f"{plan_execution.execution_scope_id}:{action_index}"
    return UniversalActionExecution(
        execution_id=hashlib.sha256(execution_key.encode("utf-8")).hexdigest(),
        execution_scope_id=plan_execution.execution_scope_id,
        action_hash=action_hash,
        context=context,
        action_index=action_index,
        action=action.model_copy(deep=True),
    )


class UniversalActionExecutor:
    def __init__(self, worker: Any) -> None:
        self.worker = worker

    def execute(self, execution: UniversalActionExecution) -> bool:
        action = execution.action
        if not isinstance(action.kind, PlannedActionKind):
            raise ValueError(f"Unsupported planned action kind: {action.kind!r}")
        if action.kind in {
            PlannedActionKind.SEND_REPLY,
            PlannedActionKind.ASK_CLARIFYING_QUESTION,
        }:
            return self.worker.execute_universal_send_reply(execution)
        if action.kind is PlannedActionKind.OA_APPROVAL:
            return self.worker.execute_universal_oa_approval(execution)
        if action.kind is PlannedActionKind.MAIL_REPLY:
            return self.worker.execute_universal_mail_reply(execution)
        if action.kind is PlannedActionKind.CALENDAR_RESPONSE:
            return self.worker.execute_universal_calendar_response(execution)
        if action.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
            return self.worker.execute_universal_document_reply(execution)
        if action.kind is PlannedActionKind.DWS_MESSAGE_REACTION:
            return self.worker.execute_universal_message_reaction(execution)
        if action.kind is PlannedActionKind.MEMORY_WRITE:
            return self.worker.execute_universal_memory_write(execution)
        if action.kind in {
            PlannedActionKind.NO_REPLY,
            PlannedActionKind.HANDOFF_TO_HUMAN,
            PlannedActionKind.BLOCKED,
            PlannedActionKind.STOP_WITH_ERROR,
        }:
            return self.worker.execute_universal_terminal_action(execution)
        raise ValueError(f"Unsupported planned action kind: {action.kind!r}")
