from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.universal_context import UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalActionExecutor,
    build_universal_action_execution,
)
from app.universal_plan import PlannedAction, PlannedActionKind
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


def make_context(task_id: int = 42) -> UniversalTaskContext:
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
        target = {"mailbox": "ceo@example.com", "message_id": "mail-1"}
        payload = {"content": "Mail reply"}

    return PlannedAction(
        kind=kind,
        reason="Execute the planned action",
        target=target,
        payload=payload,
    )


def make_execution(
    kind: PlannedActionKind, *, action_index: int = 0
) -> UniversalActionExecution:
    return build_universal_action_execution(
        make_context(), make_action(kind), action_index
    )


def test_execution_id_is_stable_for_canonical_action_json_and_isolates_action() -> None:
    first_action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Remember this",
        target={"b": "2", "a": "1"},
        payload={"nested": {"z": 3, "a": 1}},
    )
    reordered_action = PlannedAction(
        kind=PlannedActionKind.MEMORY_WRITE,
        reason="Remember this",
        target={"a": "1", "b": "2"},
        payload={"nested": {"a": 1, "z": 3}},
    )

    first = build_universal_action_execution(make_context(), first_action, 0)
    repeated = build_universal_action_execution(make_context(), reordered_action, 0)

    assert first.execution_id == repeated.execution_id
    assert len(first.execution_id) == 64
    assert all(character in "0123456789abcdef" for character in first.execution_id)
    assert first.action is not first_action
    first_action.target["a"] = "mutated"
    assert first.action.target["a"] == "1"


def test_execution_id_changes_with_task_index_or_action() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    baseline = build_universal_action_execution(make_context(), action, 0)
    different_task = build_universal_action_execution(
        make_context(task_id=43), action, 0
    )
    different_index = build_universal_action_execution(make_context(), action, 1)
    changed_action = action.model_copy(deep=True)
    changed_action.payload["content"] = "different"
    different_action = build_universal_action_execution(
        make_context(), changed_action, 0
    )

    assert (
        len(
            {
                baseline.execution_id,
                different_task.execution_id,
                different_index.execution_id,
                different_action.execution_id,
            }
        )
        == 4
    )


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


@pytest.mark.parametrize("method_name", sorted(set(WORKER_METHOD_BY_KIND.values())))
def test_worker_universal_methods_require_capability_executor(
    method_name: str,
) -> None:
    worker = object.__new__(DingTalkAutoReplyWorker)
    method = getattr(worker, method_name)

    with pytest.raises(
        NotImplementedError,
        match="wire capability executor before enabling universal consumer",
    ):
        method(make_execution(PlannedActionKind.NO_REPLY))
