from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.universal_context import UniversalTaskContext
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalPlan
from app.universal_validator import (
    DependencyStatus,
    UniversalValidationContext,
    UniversalValidator,
)


class _UniversalPlanner(Protocol):
    def plan(
        self,
        context: UniversalTaskContext,
        session_id: str | None = None,
    ) -> UniversalPlan: ...


class _UniversalExecutor(Protocol):
    def execute(self, action: PlannedAction) -> bool: ...


@dataclass(frozen=True)
class UniversalConsumerResult:
    completed: bool
    reason: str
    executed_actions: tuple[PlannedAction, ...]


class UniversalConsumerOrchestrator:
    def __init__(
        self,
        planner: _UniversalPlanner,
        validator_context_factory: Callable[
            [UniversalTaskContext], dict[str, DependencyStatus]
        ],
        existing_terminal_attempt: Callable[[UniversalTaskContext], bool],
        existing_sent_reply: Callable[[UniversalTaskContext], bool],
        session_id: Callable[[UniversalTaskContext], str | None],
        executor: _UniversalExecutor,
    ) -> None:
        self.planner = planner
        self.validator_context_factory = validator_context_factory
        self.existing_terminal_attempt = existing_terminal_attempt
        self.existing_sent_reply = existing_sent_reply
        self.session_id = session_id
        self.executor = executor
        self.validator = UniversalValidator()

    def process(self, context: UniversalTaskContext) -> UniversalConsumerResult:
        has_terminal_attempt = self.existing_terminal_attempt(context)
        has_sent_reply = self.existing_sent_reply(context)
        if has_terminal_attempt or has_sent_reply:
            return UniversalConsumerResult(
                completed=True,
                reason="duplicate_trigger_already_terminal",
                executed_actions=(),
            )

        dependency_status = self.validator_context_factory(context)
        for dependency in context.required_dependencies:
            status = dependency_status.get(dependency)
            if status is None:
                return UniversalConsumerResult(
                    completed=False,
                    reason=f"dependency_status_missing:{dependency}",
                    executed_actions=(),
                )
            if not status.ready:
                return UniversalConsumerResult(
                    completed=False,
                    reason=status.reason.strip() or f"{dependency}_unavailable",
                    executed_actions=(),
                )

        plan = self.planner.plan(
            context,
            session_id=self.session_id(context),
        )
        validated = self.validator.validate(
            plan,
            UniversalValidationContext(
                conversation_id=context.conversation_id,
                trigger_message_id=context.trigger_message_id,
                dependency_status=dependency_status,
                existing_terminal_attempt=has_terminal_attempt,
                existing_sent_reply=has_sent_reply,
                dry_run=context.dry_run,
                required_dependencies=context.required_dependencies,
            ),
        )
        if not validated.allowed:
            return UniversalConsumerResult(
                completed=validated.terminal,
                reason=validated.block_reason,
                executed_actions=(),
            )

        executed_actions: list[PlannedAction] = []
        for action in validated.actions:
            if not self.executor.execute(action):
                return UniversalConsumerResult(
                    completed=False,
                    reason=f"action_execution_failed:{action.kind.value}",
                    executed_actions=tuple(executed_actions),
                )
            executed_actions.append(action)

        nonterminal_blocked = (
            len(validated.actions) == 1
            and validated.actions[0].kind is PlannedActionKind.BLOCKED
            and not validated.terminal
        )
        return UniversalConsumerResult(
            completed=not nonterminal_blocked,
            reason=plan.reason,
            executed_actions=tuple(executed_actions),
        )
