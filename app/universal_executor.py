from typing import Any

from app.universal_plan import PlannedAction, PlannedActionKind


class UniversalActionExecutor:
    def __init__(self, worker: Any) -> None:
        self.worker = worker

    def execute(self, action: PlannedAction) -> bool:
        if not isinstance(action.kind, PlannedActionKind):
            raise ValueError(f"Unsupported planned action kind: {action.kind!r}")
        if action.kind in {
            PlannedActionKind.SEND_REPLY,
            PlannedActionKind.ASK_CLARIFYING_QUESTION,
        }:
            return self.worker.execute_universal_send_reply(action)
        if action.kind is PlannedActionKind.OA_APPROVAL:
            return self.worker.execute_universal_oa_approval(action)
        if action.kind is PlannedActionKind.MAIL_REPLY:
            return self.worker.execute_universal_mail_reply(action)
        if action.kind is PlannedActionKind.CALENDAR_RESPONSE:
            return self.worker.execute_universal_calendar_response(action)
        if action.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
            return self.worker.execute_universal_document_reply(action)
        if action.kind is PlannedActionKind.DWS_MESSAGE_REACTION:
            return self.worker.execute_universal_message_reaction(action)
        if action.kind is PlannedActionKind.MEMORY_WRITE:
            return self.worker.execute_universal_memory_write(action)
        if action.kind in {
            PlannedActionKind.NO_REPLY,
            PlannedActionKind.HANDOFF_TO_HUMAN,
            PlannedActionKind.BLOCKED,
            PlannedActionKind.STOP_WITH_ERROR,
        }:
            return self.worker.execute_universal_terminal_action(action)
        raise ValueError(f"Unsupported planned action kind: {action.kind!r}")
