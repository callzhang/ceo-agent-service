from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.universal_context import UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalActionExecutor,
    UniversalPlanExecution,
    build_universal_action_execution,
    canonical_universal_action_json,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.worker import DingTalkAutoReplyWorker


WORKER_METHOD_BY_KIND = {
    PlannedActionKind.SEND_REPLY: "execute_universal_send_reply",
    PlannedActionKind.ASK_CLARIFYING_QUESTION: "execute_universal_send_reply",
    PlannedActionKind.OA_APPROVAL: "execute_universal_oa_approval",
    PlannedActionKind.MAIL_REPLY: "execute_universal_mail_reply",
    PlannedActionKind.CALENDAR_RESPONSE: "execute_universal_calendar_response",
    PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY: ("execute_universal_document_reply"),
    PlannedActionKind.DWS_MESSAGE_REACTION: ("execute_universal_message_reaction"),
    PlannedActionKind.MEMORY_WRITE: "execute_universal_memory_write",
    PlannedActionKind.NO_REPLY: "execute_universal_terminal_action",
    PlannedActionKind.HANDOFF_TO_HUMAN: "execute_universal_terminal_action",
    PlannedActionKind.BLOCKED: "execute_universal_terminal_action",
    PlannedActionKind.STOP_WITH_ERROR: "execute_universal_terminal_action",
}


class FakeWorker:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, UniversalActionExecution]] = []

    def _execute(self, method_name: str, execution: UniversalActionExecution) -> bool:
        self.calls.append((method_name, execution))
        return self.result

    def execute_universal_send_reply(self, execution: UniversalActionExecution) -> bool:
        return self._execute("execute_universal_send_reply", execution)

    def execute_universal_oa_approval(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_oa_approval", execution)

    def execute_universal_mail_reply(self, execution: UniversalActionExecution) -> bool:
        return self._execute("execute_universal_mail_reply", execution)

    def execute_universal_calendar_response(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_calendar_response", execution)

    def execute_universal_document_reply(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_document_reply", execution)

    def execute_universal_message_reaction(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_message_reaction", execution)

    def execute_universal_memory_write(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_memory_write", execution)

    def execute_universal_terminal_action(
        self, execution: UniversalActionExecution
    ) -> bool:
        return self._execute("execute_universal_terminal_action", execution)


def make_context(
    task_id: int = 42, execution_generation: str = "initial"
) -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=task_id,
        conversation_id="conversation-1",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please handle this.",
        context_messages=(),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
        execution_generation=execution_generation,
    )


def make_action(kind: PlannedActionKind) -> PlannedAction:
    target: dict[str, str] = {}
    payload: dict[str, str] = {}
    if kind in {
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    }:
        payload = {"text": "Reply text"}
    elif kind is PlannedActionKind.OA_APPROVAL:
        payload = {"action": "comment", "remark": "Needs review"}
    elif kind is PlannedActionKind.MAIL_REPLY:
        target = {
            "mailbox": "ceo@example.com",
            "message_id": "mail-1",
            "subject": "Subject",
        }
        payload = {"content": "Mail reply"}
    elif kind is PlannedActionKind.CALENDAR_RESPONSE:
        target = {"event_id": "event-1"}
        payload = {"response_status": "accepted"}
    elif kind is PlannedActionKind.MEMORY_WRITE:
        payload = {"data": "Persist the durable decision.", "type": "text"}

    return PlannedAction(
        kind=kind,
        reason="Execute the planned action",
        sensitivity_kind=(
            "general"
            if kind
            in {
                PlannedActionKind.SEND_REPLY,
                PlannedActionKind.ASK_CLARIFYING_QUESTION,
            }
            else None
        ),
        target=target,
        payload=payload,
    )


def make_plan(action: PlannedAction | None = None) -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason="Handle the task",
        actions=[action or make_action(PlannedActionKind.NO_REPLY)],
        audit=UniversalAudit(summary="Plan the task", confidence=0.9),
    )


def make_execution(
    kind: PlannedActionKind,
    *,
    action_index: int = 0,
    execution_scope_id: str = "scope-1",
) -> UniversalActionExecution:
    action = make_action(kind)
    return build_universal_action_execution(
        make_context(),
        UniversalPlanExecution(
            execution_scope_id=execution_scope_id,
            execution_generation="initial",
            plan=make_plan(action),
        ),
        action,
        action_index,
    )


def test_action_hash_is_stable_for_canonical_json_and_isolates_action() -> None:
    first_action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Remember this",
        target={"b": "2", "a": "1", "nested": {"z": 3, "a": 1}},
        payload={"data": "Remember this.", "type": "text"},
    )
    reordered_action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Remember this",
        target={"nested": {"a": 1, "z": 3}, "a": "1", "b": "2"},
        payload={"type": "text", "data": "Remember this."},
    )

    plan_execution = UniversalPlanExecution(
        "scope-1", "initial", make_plan(first_action)
    )
    first = build_universal_action_execution(
        make_context(), plan_execution, first_action, 0
    )
    repeated = build_universal_action_execution(
        make_context(), plan_execution, reordered_action, 0
    )

    assert first.execution_id == repeated.execution_id
    assert first.action_hash == repeated.action_hash
    assert len(first.execution_id) == 64
    assert len(first.action_hash) == 64
    assert all(character in "0123456789abcdef" for character in first.execution_id)
    assert first.action is not first_action
    first_action.target["a"] = "mutated"
    assert first.action.target["a"] == "1"


def test_canonical_action_json_is_stable_and_sorted() -> None:
    action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Remember this",
        target={"b": "2", "a": "1", "nested": {"z": 3, "a": 1}},
        payload={"data": "Remember this.", "type": "text"},
    )

    assert canonical_universal_action_json(action) == (
        '{"candidate_context_known":false,"candidate_department_ids":[],'
        '"kind":"memory_write","payload":{"data":"Remember this.","type":"text"},'
        '"personnel_subject_user_id":null,"reason":"Remember this",'
        '"sensitivity_kind":null,"target":{"a":"1","b":"2","nested":{"a":1,"z":3}}}'
    )


def test_same_scope_and_index_keep_id_when_action_changes_but_hash_changes() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    plan_execution = UniversalPlanExecution("scope-1", "initial", make_plan(action))
    baseline = build_universal_action_execution(
        make_context(), plan_execution, action, 0
    )
    changed_action = action.model_copy(deep=True)
    changed_action.reason = "A changed audit reason"
    changed = build_universal_action_execution(
        make_context(), plan_execution, changed_action, 0
    )

    assert baseline.execution_id == changed.execution_id
    assert baseline.action_hash != changed.action_hash


def test_execution_id_changes_with_scope_or_action_index() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    first_scope = UniversalPlanExecution("scope-1", "initial", make_plan(action))
    second_scope = UniversalPlanExecution("scope-2", "initial", make_plan(action))

    baseline = build_universal_action_execution(make_context(), first_scope, action, 0)
    different_index = build_universal_action_execution(
        make_context(), first_scope, action, 1
    )
    different_scope = build_universal_action_execution(
        make_context(), second_scope, action, 0
    )

    assert (
        len(
            {
                baseline.execution_id,
                different_index.execution_id,
                different_scope.execution_id,
            }
        )
        == 3
    )


def test_execution_id_changes_across_generations_even_when_scope_is_reused() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    first_generation = UniversalPlanExecution(
        "reused-scope", "generation-1", make_plan(action)
    )
    second_generation = UniversalPlanExecution(
        "reused-scope", "generation-2", make_plan(action)
    )

    first = build_universal_action_execution(
        make_context(execution_generation="generation-1"),
        first_generation,
        action,
        0,
    )
    second = build_universal_action_execution(
        make_context(execution_generation="generation-2"),
        second_generation,
        action,
        0,
    )

    assert first.execution_id != second.execution_id


def test_execution_identity_has_no_scope_generation_delimiter_alias() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    first_plan = UniversalPlanExecution("a:b", "c", make_plan(action))
    second_plan = UniversalPlanExecution("a", "b:c", make_plan(action))

    first = build_universal_action_execution(
        make_context(execution_generation="c"),
        first_plan,
        action,
        0,
    )
    second = build_universal_action_execution(
        make_context(execution_generation="b:c"),
        second_plan,
        action,
        0,
    )

    assert first.execution_id != second.execution_id


def test_plan_execution_is_frozen_and_deep_copies_plan() -> None:
    plan = make_plan()
    plan_execution = UniversalPlanExecution("scope-1", "initial", plan)

    assert plan_execution.plan is not plan
    plan.reason = "Mutated plan"
    plan.actions[0].payload["changed"] = True
    assert plan_execution.plan.reason == "Handle the task"
    assert plan_execution.plan.actions[0].payload == {}
    with pytest.raises(FrozenInstanceError):
        plan_execution.execution_scope_id = "scope-2"  # type: ignore[misc]


def test_build_rejects_plan_execution_from_another_generation() -> None:
    action = make_action(PlannedActionKind.NO_REPLY)
    plan_execution = UniversalPlanExecution(
        "scope-1", "manual-rerun-2", make_plan(action)
    )

    with pytest.raises(ValueError, match="execution generation mismatch"):
        build_universal_action_execution(
            make_context(),
            plan_execution,
            action,
            0,
        )


def test_plan_execution_rejects_empty_generation() -> None:
    with pytest.raises(ValueError, match="execution_generation must be non-empty"):
        UniversalPlanExecution("scope-1", "   ", make_plan())


def test_execution_states_are_complete_and_stable() -> None:
    assert [state.value for state in UniversalActionExecutionState] == [
        "not_started",
        "succeeded",
        "unknown",
    ]


def test_execution_shell_is_frozen() -> None:
    execution = make_execution(PlannedActionKind.NO_REPLY)

    with pytest.raises(FrozenInstanceError):
        execution.action_index = 2  # type: ignore[misc]


@pytest.mark.parametrize("kind", list(PlannedActionKind))
@pytest.mark.parametrize("worker_result", [True, False])
def test_execute_dispatches_every_kind_with_same_execution(
    kind: PlannedActionKind,
    worker_result: bool,
) -> None:
    execution = make_execution(kind)
    worker = FakeWorker(worker_result)

    result = UniversalActionExecutor(worker).execute(execution)

    assert result is worker_result
    assert len(worker.calls) == 1
    method_name, received_execution = worker.calls[0]
    assert method_name == WORKER_METHOD_BY_KIND[kind]
    assert received_execution is execution


def test_dispatch_table_covers_the_complete_planned_action_enum() -> None:
    assert set(WORKER_METHOD_BY_KIND) == set(PlannedActionKind)


@pytest.mark.parametrize(
    "unsupported_kind",
    [object(), PlannedActionKind.SEND_REPLY.value],
)
def test_execute_rejects_an_unsupported_kind(unsupported_kind: object) -> None:
    worker = FakeWorker(True)
    execution = SimpleNamespace(action=SimpleNamespace(kind=unsupported_kind))

    with pytest.raises(ValueError, match="Unsupported planned action kind"):
        UniversalActionExecutor(worker).execute(execution)  # type: ignore[arg-type]

    assert worker.calls == []


@pytest.mark.parametrize(
    "method_name",
    sorted(
        set(WORKER_METHOD_BY_KIND.values())
        - {
            "execute_universal_oa_approval",
            "execute_universal_send_reply",
            "execute_universal_mail_reply",
                "execute_universal_calendar_response",
                "execute_universal_memory_write",
                "execute_universal_terminal_action",
        }
    ),
)
def test_unimplemented_worker_universal_methods_require_capability_executor(
    method_name: str,
) -> None:
    worker = object.__new__(DingTalkAutoReplyWorker)
    method = getattr(worker, method_name)

    with pytest.raises(
        NotImplementedError,
        match="wire capability executor before enabling universal consumer",
    ):
        method(make_execution(PlannedActionKind.NO_REPLY))
