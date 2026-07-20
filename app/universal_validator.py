from dataclasses import dataclass

from app.universal_plan import PlannedAction, PlannedActionKind, UniversalPlan


@dataclass(frozen=True)
class DependencyStatus:
    ready: bool
    reason: str = ""
    authorization_required: bool = False

    def __post_init__(self) -> None:
        if type(self.ready) is not bool:
            raise TypeError("ready must be bool")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be str")
        if type(self.authorization_required) is not bool:
            raise TypeError("authorization_required must be bool")


@dataclass(frozen=True)
class UniversalValidationContext:
    conversation_id: str
    trigger_message_id: str
    dependency_status: dict[str, DependencyStatus]
    existing_terminal_attempt: bool
    existing_sent_reply: bool
    dry_run: bool
    required_dependencies: tuple[str, ...] = ("dws",)
    trusted_document_url: str = ""


@dataclass(frozen=True)
class ValidatedUniversalPlan:
    allowed: bool
    actions: tuple[PlannedAction, ...]
    block_reason: str
    terminal: bool


_EXTERNAL_ACTION_KINDS = {
    PlannedActionKind.SEND_REPLY,
    PlannedActionKind.ASK_CLARIFYING_QUESTION,
    PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
    PlannedActionKind.DWS_MESSAGE_REACTION,
    PlannedActionKind.OA_APPROVAL,
    PlannedActionKind.MAIL_REPLY,
    PlannedActionKind.CALENDAR_RESPONSE,
}
_REPLY_ACTION_KINDS = {
    PlannedActionKind.SEND_REPLY,
    PlannedActionKind.ASK_CLARIFYING_QUESTION,
}
_TERMINAL_ACTION_KINDS = {
    PlannedActionKind.NO_REPLY,
    PlannedActionKind.HANDOFF_TO_HUMAN,
    PlannedActionKind.BLOCKED,
    PlannedActionKind.STOP_WITH_ERROR,
}


class UniversalValidator:
    def validate(
        self, plan: UniversalPlan, context: UniversalValidationContext
    ) -> ValidatedUniversalPlan:
        if context.existing_terminal_attempt or context.existing_sent_reply:
            return self._blocked_result(
                context,
                kind=PlannedActionKind.NO_REPLY,
                reason="duplicate_trigger_already_terminal",
                terminal=True,
            )

        for dependency in self._ordered_dependencies(plan, context):
            status = context.dependency_status.get(dependency)
            if status is None:
                return self._blocked_result(
                    context,
                    kind=PlannedActionKind.BLOCKED,
                    reason=f"dependency_status_missing:{dependency}",
                    terminal=False,
                )
            if not status.ready:
                return self._blocked_result(
                    context,
                    kind=PlannedActionKind.BLOCKED,
                    reason=status.reason.strip() or f"{dependency}_unavailable",
                    terminal=False,
                )

        target_block = self._target_block_reason(plan, context)
        if target_block:
            return self._blocked_result(
                context,
                kind=PlannedActionKind.BLOCKED,
                reason=target_block,
                terminal=False,
            )

        if len(plan.actions) != 1 and any(
            action.kind in _TERMINAL_ACTION_KINDS for action in plan.actions
        ):
            return self._blocked_result(
                context,
                kind=PlannedActionKind.BLOCKED,
                reason="conflicting_terminal_actions",
                terminal=False,
            )

        actions = tuple(action.model_copy(deep=True) for action in plan.actions)
        if context.dry_run:
            return ValidatedUniversalPlan(
                allowed=False,
                actions=actions,
                block_reason="dry_run",
                terminal=False,
            )

        return ValidatedUniversalPlan(
            allowed=True,
            actions=actions,
            block_reason="",
            terminal=self._is_terminal(actions),
        )

    @staticmethod
    def _ordered_dependencies(
        plan: UniversalPlan, context: UniversalValidationContext
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for dependency in (*context.required_dependencies, *plan.dependencies):
            name = str(dependency)
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return tuple(ordered)

    @staticmethod
    def _target_block_reason(
        plan: UniversalPlan, context: UniversalValidationContext
    ) -> str:
        expected_target = {
            "conversation_id": context.conversation_id,
            "trigger_message_id": context.trigger_message_id,
        }
        for action in plan.actions:
            if action.kind not in _EXTERNAL_ACTION_KINDS:
                continue
            if any(
                field_name in action.target
                and action.target[field_name] != expected_value
                for field_name, expected_value in expected_target.items()
            ):
                return "action_target_mismatch"
            if action.kind in _REPLY_ACTION_KINDS and any(
                not action.target.get(field_name) for field_name in expected_target
            ):
                return "missing_action_target"
            if (
                action.kind is PlannedActionKind.DWS_MESSAGE_REACTION
                and action.target.get("message_id") != context.trigger_message_id
            ):
                return "action_target_mismatch"
            if action.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
                document_url = str(action.target.get("document_url") or "").strip()
                if document_url != context.trusted_document_url.strip():
                    return "action_target_mismatch"
        return ""

    @staticmethod
    def _is_terminal(actions: tuple[PlannedAction, ...]) -> bool:
        if len(actions) != 1:
            return False
        action = actions[0]
        if action.kind is PlannedActionKind.BLOCKED:
            return action.payload.get("terminal", False) is True
        return action.kind in {
            PlannedActionKind.NO_REPLY,
            PlannedActionKind.HANDOFF_TO_HUMAN,
            PlannedActionKind.STOP_WITH_ERROR,
        }

    @staticmethod
    def _blocked_result(
        context: UniversalValidationContext,
        *,
        kind: PlannedActionKind,
        reason: str,
        terminal: bool,
    ) -> ValidatedUniversalPlan:
        action = PlannedAction(
            kind=kind,
            reason=reason,
            target={
                "conversation_id": context.conversation_id,
                "trigger_message_id": context.trigger_message_id,
            },
            payload={"blocker": reason, "terminal": terminal},
        )
        return ValidatedUniversalPlan(
            allowed=False,
            actions=(action,),
            block_reason=reason,
            terminal=terminal,
        )
