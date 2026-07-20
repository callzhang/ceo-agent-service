from types import SimpleNamespace

import pytest

from app.universal_executor import UniversalActionExecutor
from app.universal_plan import PlannedAction, PlannedActionKind
from app.worker import DingTalkAutoReplyWorker


WORKER_METHOD_BY_KIND = {
    PlannedActionKind.SEND_REPLY: "execute_universal_send_reply",
    PlannedActionKind.ASK_CLARIFYING_QUESTION: "execute_universal_send_reply",
    PlannedActionKind.OA_APPROVAL: "execute_universal_oa_approval",
    PlannedActionKind.MAIL_REPLY: "execute_universal_mail_reply",
    PlannedActionKind.CALENDAR_RESPONSE: "execute_universal_calendar_response",
    PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY: (
        "execute_universal_document_reply"
    ),
    PlannedActionKind.DWS_MESSAGE_REACTION: (
        "execute_universal_message_reaction"
    ),
    PlannedActionKind.MEMORY_WRITE: "execute_universal_memory_write",
    PlannedActionKind.NO_REPLY: "execute_universal_terminal_action",
    PlannedActionKind.HANDOFF_TO_HUMAN: "execute_universal_terminal_action",
    PlannedActionKind.BLOCKED: "execute_universal_terminal_action",
    PlannedActionKind.STOP_WITH_ERROR: "execute_universal_terminal_action",
}


class FakeWorker:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, PlannedAction]] = []

    def _execute(self, method_name: str, action: PlannedAction) -> bool:
        self.calls.append((method_name, action))
        return self.result

    def execute_universal_send_reply(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_send_reply", action)

    def execute_universal_oa_approval(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_oa_approval", action)

    def execute_universal_mail_reply(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_mail_reply", action)

    def execute_universal_calendar_response(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_calendar_response", action)

    def execute_universal_document_reply(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_document_reply", action)

    def execute_universal_message_reaction(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_message_reaction", action)

    def execute_universal_memory_write(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_memory_write", action)

    def execute_universal_terminal_action(self, action: PlannedAction) -> bool:
        return self._execute("execute_universal_terminal_action", action)


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


@pytest.mark.parametrize("kind", list(PlannedActionKind))
@pytest.mark.parametrize("worker_result", [True, False])
def test_execute_dispatches_every_kind_without_rewriting_action(
    kind: PlannedActionKind,
    worker_result: bool,
) -> None:
    action = make_action(kind)
    worker = FakeWorker(worker_result)

    result = UniversalActionExecutor(worker).execute(action)

    assert result is worker_result
    assert len(worker.calls) == 1
    method_name, received_action = worker.calls[0]
    assert method_name == WORKER_METHOD_BY_KIND[kind]
    assert received_action is action


def test_dispatch_table_covers_the_complete_planned_action_enum() -> None:
    assert set(WORKER_METHOD_BY_KIND) == set(PlannedActionKind)


@pytest.mark.parametrize(
    "unsupported_kind",
    [object(), PlannedActionKind.SEND_REPLY.value],
)
def test_execute_rejects_an_unsupported_kind(unsupported_kind: object) -> None:
    worker = FakeWorker(True)
    action = SimpleNamespace(kind=unsupported_kind)

    with pytest.raises(ValueError, match="Unsupported planned action kind"):
        UniversalActionExecutor(worker).execute(action)  # type: ignore[arg-type]

    assert worker.calls == []


@pytest.mark.parametrize("method_name", sorted(set(WORKER_METHOD_BY_KIND.values())))
def test_worker_universal_methods_are_explicit_task_7_placeholders(
    method_name: str,
) -> None:
    worker = object.__new__(DingTalkAutoReplyWorker)
    method = getattr(worker, method_name)

    with pytest.raises(
        NotImplementedError,
        match="wire in Task 7 after orchestrator stores attempt context",
    ):
        method(make_action(PlannedActionKind.NO_REPLY))
