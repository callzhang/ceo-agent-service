from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import ContextManager, Protocol

from app.universal_context import UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalPlanExecution,
    build_universal_action_execution,
)
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
    def execute(self, execution: UniversalActionExecution) -> bool: ...


class UniversalConsumerOutcome(StrEnum):
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    DRY_RUN = "dry_run"
    VALIDATION_BLOCKED = "validation_blocked"
    ACTION_FAILED = "action_failed"
    ACTION_UNKNOWN = "action_unknown"
    NONTERMINAL_BLOCKED = "nonterminal_blocked"


@dataclass(frozen=True)
class UniversalConsumerResult:
    completed: bool
    reason: str
    executed_actions: tuple[PlannedAction, ...]
    outcome: UniversalConsumerOutcome
    authorization_required: bool = False


class UniversalConsumerOrchestrator:
    def __init__(
        self,
        planner: _UniversalPlanner,
        validator_context_factory: Callable[
            [UniversalTaskContext, tuple[str, ...]], dict[str, DependencyStatus]
        ],
        existing_terminal_attempt: Callable[[UniversalTaskContext], bool],
        existing_sent_reply: Callable[[UniversalTaskContext], bool],
        load_plan_execution: Callable[
            [UniversalTaskContext], UniversalPlanExecution | None
        ],
        create_plan_execution: Callable[
            [UniversalTaskContext, UniversalPlan], UniversalPlanExecution
        ],
        action_execution_state: Callable[
            [UniversalActionExecution], UniversalActionExecutionState
        ],
        session_id: Callable[[UniversalTaskContext], str | None],
        executor: _UniversalExecutor,
        planning_lock: Callable[
            [UniversalTaskContext], ContextManager[None]
        ] | None = None,
    ) -> None:
        self.planner = planner
        self.validator_context_factory = validator_context_factory
        self.existing_terminal_attempt = existing_terminal_attempt
        self.existing_sent_reply = existing_sent_reply
        self.load_plan_execution = load_plan_execution
        self.create_plan_execution = create_plan_execution
        self.action_execution_state = action_execution_state
        self.session_id = session_id
        self.executor = executor
        self.planning_lock = planning_lock or (lambda context: nullcontext())
        self.validator = UniversalValidator()

    def process(self, context: UniversalTaskContext) -> UniversalConsumerResult:
        loaded_action_states: dict[int, UniversalActionExecutionState] = {}
        loaded_plan_execution = self.load_plan_execution(context)
        active_plan_incomplete = False
        if loaded_plan_execution is not None:
            loaded_plan_execution = self._copy_plan_execution(
                loaded_plan_execution,
                context,
            )
            for action_index, action in enumerate(loaded_plan_execution.plan.actions):
                execution = build_universal_action_execution(
                    context,
                    loaded_plan_execution,
                    action,
                    action_index,
                )
                state = self.action_execution_state(deepcopy(execution))
                loaded_action_states[action_index] = state
                if state is not UniversalActionExecutionState.SUCCEEDED:
                    active_plan_incomplete = True

        has_terminal_attempt = self.existing_terminal_attempt(context)
        has_sent_reply = self.existing_sent_reply(context)
        if not active_plan_incomplete and (has_terminal_attempt or has_sent_reply):
            return UniversalConsumerResult(
                completed=True,
                reason="duplicate_trigger_already_terminal",
                executed_actions=(),
                outcome=UniversalConsumerOutcome.DUPLICATE,
            )

        dependency_status = (
            dict(self.validator_context_factory(context, context.required_dependencies))
            if context.required_dependencies
            else {}
        )
        dependency_failure = self._dependency_failure(
            context.required_dependencies, dependency_status
        )
        if dependency_failure is not None:
            return dependency_failure

        plan_execution: UniversalPlanExecution | None = None
        candidate_plan = True
        if loaded_plan_execution is not None:
            plan_execution = loaded_plan_execution
            candidate_plan = False

        if plan_execution is None:
            with self.planning_lock(context):
                plan = self.planner.plan(
                    context,
                    session_id=self.session_id(context),
                )
        else:
            plan = plan_execution.plan
        plan = self._with_context_action_targets(plan, context)

        if candidate_plan:
            has_terminal_attempt = self.existing_terminal_attempt(context)
            has_sent_reply = self.existing_sent_reply(context)
        if candidate_plan and (has_terminal_attempt or has_sent_reply):
            return UniversalConsumerResult(
                completed=True,
                reason="duplicate_trigger_already_terminal",
                executed_actions=(),
                outcome=UniversalConsumerOutcome.DUPLICATE,
            )

        if candidate_plan:
            plan_execution = self._copy_plan_execution(
                self.create_plan_execution(
                    context,
                    plan.model_copy(deep=True),
                ),
                context,
            )
            plan = plan_execution.plan

        required_dependencies = self._ordered_dependencies(
            context.required_dependencies,
            plan.execution_dependencies(),
        )
        unresolved_dependencies = tuple(
            dependency
            for dependency in required_dependencies
            if dependency not in dependency_status
        )
        if unresolved_dependencies:
            dependency_status.update(
                self.validator_context_factory(context, unresolved_dependencies)
            )
        dependency_failure = self._dependency_failure(
            required_dependencies, dependency_status
        )
        if dependency_failure is not None:
            return dependency_failure

        validated = self.validator.validate(
            plan,
            UniversalValidationContext(
                conversation_id=context.conversation_id,
                trigger_message_id=context.trigger_message_id,
                dependency_status=dependency_status,
                existing_terminal_attempt=(
                    has_terminal_attempt and not active_plan_incomplete
                ),
                existing_sent_reply=has_sent_reply and not active_plan_incomplete,
                dry_run=context.dry_run,
                required_dependencies=context.required_dependencies,
                trusted_document_url=context.trusted_document_url,
            ),
        )
        if not validated.allowed:
            if validated.block_reason == "dry_run":
                outcome = UniversalConsumerOutcome.DRY_RUN
            elif validated.terminal:
                outcome = UniversalConsumerOutcome.COMPLETED
            else:
                outcome = UniversalConsumerOutcome.VALIDATION_BLOCKED
            return UniversalConsumerResult(
                completed=validated.terminal,
                reason=validated.block_reason,
                executed_actions=(),
                outcome=outcome,
            )

        if plan_execution is None:
            raise RuntimeError("validated plan has no execution scope")

        executed_actions: list[PlannedAction] = []
        for action_index, action in enumerate(validated.actions):
            execution = build_universal_action_execution(
                context,
                plan_execution,
                action,
                action_index,
            )
            execution_state = loaded_action_states.get(action_index)
            if execution_state is None:
                execution_state = self.action_execution_state(deepcopy(execution))
            if execution_state is UniversalActionExecutionState.SUCCEEDED:
                continue
            if execution_state is UniversalActionExecutionState.UNKNOWN:
                if action.kind is not PlannedActionKind.MEMORY_WRITE:
                    return UniversalConsumerResult(
                        completed=False,
                        reason=f"action_execution_unknown:{execution.execution_id}",
                        executed_actions=tuple(executed_actions),
                        outcome=UniversalConsumerOutcome.ACTION_UNKNOWN,
                    )
            if (
                execution_state is not UniversalActionExecutionState.NOT_STARTED
                and not (
                    execution_state is UniversalActionExecutionState.UNKNOWN
                    and action.kind is PlannedActionKind.MEMORY_WRITE
                )
            ):
                raise ValueError(
                    f"Unsupported universal action execution state: {execution_state!r}"
                )

            audit_action = execution.action.model_copy(deep=True)
            try:
                action_completed = self.executor.execute(deepcopy(execution))
            except Exception:
                if (
                    self.action_execution_state(deepcopy(execution))
                    is UniversalActionExecutionState.UNKNOWN
                ):
                    return UniversalConsumerResult(
                        completed=False,
                        reason=f"action_execution_unknown:{execution.execution_id}",
                        executed_actions=tuple(executed_actions),
                        outcome=UniversalConsumerOutcome.ACTION_UNKNOWN,
                    )
                raise
            if not action_completed:
                return UniversalConsumerResult(
                    completed=False,
                    reason=f"action_execution_failed:{action.kind.value}",
                    executed_actions=tuple(executed_actions),
                    outcome=UniversalConsumerOutcome.ACTION_FAILED,
                )
            executed_actions.append(audit_action)

        nonterminal_blocked = (
            len(validated.actions) == 1
            and validated.actions[0].kind is PlannedActionKind.BLOCKED
            and not validated.terminal
        )
        return UniversalConsumerResult(
            completed=not nonterminal_blocked,
            reason=plan.reason,
            executed_actions=tuple(executed_actions),
            outcome=(
                UniversalConsumerOutcome.NONTERMINAL_BLOCKED
                if nonterminal_blocked
                else UniversalConsumerOutcome.COMPLETED
            ),
        )

    @staticmethod
    def _ordered_dependencies(
        required_dependencies: tuple[str, ...],
        plan_dependencies: tuple[str, ...],
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for dependency in (*required_dependencies, *plan_dependencies):
            if dependency not in seen:
                seen.add(dependency)
                ordered.append(dependency)
        return tuple(ordered)

    @staticmethod
    def _dependency_failure(
        required_dependencies: tuple[str, ...],
        dependency_status: dict[str, DependencyStatus],
    ) -> UniversalConsumerResult | None:
        for dependency in required_dependencies:
            status = dependency_status.get(dependency)
            if status is None:
                return UniversalConsumerResult(
                    completed=False,
                    reason=f"dependency_status_missing:{dependency}",
                    executed_actions=(),
                    outcome=UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY,
                )
            if not status.ready:
                return UniversalConsumerResult(
                    completed=False,
                    reason=status.reason.strip() or f"{dependency}_unavailable",
                    executed_actions=(),
                    outcome=UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY,
                    authorization_required=status.authorization_required,
                )
        return None

    @staticmethod
    def _with_context_action_targets(
        plan: UniversalPlan,
        context: UniversalTaskContext,
    ) -> UniversalPlan:
        context_target = {
            "conversation_id": context.conversation_id,
            "trigger_message_id": context.trigger_message_id,
        }
        normalized_actions: list[PlannedAction] = []
        changed = False
        for action in plan.actions:
            if action.kind not in {
                PlannedActionKind.SEND_REPLY,
                PlannedActionKind.ASK_CLARIFYING_QUESTION,
                PlannedActionKind.QUEUE_OKR_REVIEW,
            }:
                normalized_actions.append(action)
                continue

            target = dict(action.target)
            for field_name, field_value in context_target.items():
                if not target.get(field_name):
                    target[field_name] = field_value
                    changed = True
            normalized_actions.append(action.model_copy(update={"target": target}))

        if not changed:
            return plan
        return plan.model_copy(update={"actions": normalized_actions})

    @staticmethod
    def _copy_plan_execution(
        plan_execution: UniversalPlanExecution,
        context: UniversalTaskContext,
    ) -> UniversalPlanExecution:
        if not isinstance(plan_execution, UniversalPlanExecution):
            raise TypeError(
                "plan execution callback must return UniversalPlanExecution"
            )
        copied = UniversalPlanExecution(
            plan_execution.execution_scope_id,
            plan_execution.execution_generation,
            plan_execution.plan,
        )
        if copied.execution_generation != context.execution_generation:
            raise ValueError("execution generation mismatch")
        return copied
