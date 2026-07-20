import pytest

from app.universal_consumer import (
    UniversalConsumerOutcome,
    UniversalConsumerOrchestrator,
    UniversalConsumerResult,
)
from app.universal_context import UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    UniversalActionExecutor,
    UniversalPlanExecution,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.universal_validator import DependencyStatus


def make_context(
    *,
    required_dependencies: tuple[str, ...] = ("dws",),
    dry_run: bool = False,
    force_new_decision: bool = False,
    execution_generation: str = "initial",
) -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=42,
        conversation_id="conversation-1",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please handle this.",
        context_messages=(),
        required_dependencies=required_dependencies,
        force_new_decision=force_new_decision,
        dry_run=dry_run,
        execution_generation=execution_generation,
    )


def make_action(
    kind: PlannedActionKind = PlannedActionKind.SEND_REPLY,
    *,
    terminal: bool = False,
) -> PlannedAction:
    target = {}
    payload = {}
    if kind is PlannedActionKind.SEND_REPLY:
        target = {
            "conversation_id": "conversation-1",
            "trigger_message_id": "trigger-1",
        }
        payload = {"text": "Done."}
    elif kind is PlannedActionKind.BLOCKED:
        payload = {"terminal": terminal}
    elif kind is PlannedActionKind.MEMORY_WRITE:
        payload = {"data": "The durable decision is approved.", "type": "text"}
    return PlannedAction(
        kind=kind,
        reason=f"Execute {kind.value}",
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


def make_plan(
    *actions: PlannedAction,
    reason: str = "Plan completed",
    dependencies: tuple[str, ...] = (),
) -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason=reason,
        dependencies=list(dependencies),
        actions=list(actions),
        audit=UniversalAudit(summary="Plan the task", confidence=0.9),
    )


class RecordingPlanner:
    def __init__(self, plan: UniversalPlan) -> None:
        self.result = plan
        self.calls: list[tuple[UniversalTaskContext, str | None]] = []

    def plan(
        self, context: UniversalTaskContext, session_id: str | None = None
    ) -> UniversalPlan:
        self.calls.append((context, session_id))
        return self.result


class RecordingExecutor:
    def __init__(self, results: list[bool] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[UniversalActionExecution] = []

    def execute(self, execution: UniversalActionExecution) -> bool:
        self.calls.append(execution)
        return self.results.pop(0) if self.results else True


class CallbackRecorder:
    def __init__(
        self,
        *,
        dependency_status: dict[str, DependencyStatus] | None = None,
        terminal: bool = False,
        sent: bool = False,
        terminal_results: list[bool] | None = None,
        sent_results: list[bool] | None = None,
        session: str | None = "session-1",
        action_states: dict[int, UniversalActionExecutionState] | None = None,
        loaded_plan_execution: UniversalPlanExecution | None = None,
        create_scope_ids: list[str] | None = None,
        created_plan_override: UniversalPlan | None = None,
        load_misses: int = 0,
    ) -> None:
        self.dependency_status = (
            {"dws": DependencyStatus(ready=True)}
            if dependency_status is None
            else dependency_status
        )
        self.terminal = terminal
        self.sent = sent
        self.terminal_results = list(terminal_results or [])
        self.sent_results = list(sent_results or [])
        self.session = session
        self.action_states = action_states or {}
        self.plan_executions: dict[tuple[int, str], UniversalPlanExecution] = {}
        self.scope_owner: dict[str, tuple[int, str]] = {}
        if loaded_plan_execution is not None:
            loaded_key = (42, loaded_plan_execution.execution_generation)
            self.plan_executions[loaded_key] = loaded_plan_execution
            self.scope_owner[loaded_plan_execution.execution_scope_id] = loaded_key
        self.create_scope_ids = (
            list(create_scope_ids) if create_scope_ids is not None else None
        )
        self.created_plan_override = created_plan_override
        self.load_misses = load_misses
        self.created_plan_executions: list[UniversalPlanExecution] = []
        self.dependency_requests: list[tuple[str, ...]] = []
        self.action_state_calls: list[UniversalActionExecution] = []
        self.calls = {
            "dependencies": 0,
            "terminal": 0,
            "sent": 0,
            "session": 0,
            "action_state": 0,
            "load_plan": 0,
            "create_plan": 0,
        }

    def dependencies(
        self, context: UniversalTaskContext, dependencies: tuple[str, ...]
    ) -> dict[str, DependencyStatus]:
        self.calls["dependencies"] += 1
        self.dependency_requests.append(dependencies)
        return {
            dependency: self.dependency_status[dependency]
            for dependency in dependencies
            if dependency in self.dependency_status
        }

    def existing_terminal(self, context: UniversalTaskContext) -> bool:
        self.calls["terminal"] += 1
        if self.terminal_results:
            return self.terminal_results.pop(0)
        return self.terminal

    def existing_sent(self, context: UniversalTaskContext) -> bool:
        self.calls["sent"] += 1
        if self.sent_results:
            return self.sent_results.pop(0)
        return self.sent

    def action_execution_state(
        self, execution: UniversalActionExecution
    ) -> UniversalActionExecutionState:
        self.calls["action_state"] += 1
        self.action_state_calls.append(execution)
        return self.action_states.get(
            execution.action_index,
            UniversalActionExecutionState.NOT_STARTED,
        )

    def load_plan_execution(
        self, context: UniversalTaskContext
    ) -> UniversalPlanExecution | None:
        self.calls["load_plan"] += 1
        if self.load_misses:
            self.load_misses -= 1
            return None
        return self.plan_executions.get((context.task_id, context.execution_generation))

    def create_plan_execution(
        self, context: UniversalTaskContext, plan: UniversalPlan
    ) -> UniversalPlanExecution:
        self.calls["create_plan"] += 1
        key = (context.task_id, context.execution_generation)
        existing = self.plan_executions.get(key)
        if existing is not None:
            self.created_plan_executions.append(existing)
            return existing
        if self.create_scope_ids is None:
            scope_id = f"scope-{self.calls['create_plan']}"
        else:
            scope_id = self.create_scope_ids.pop(0)
        scope_owner = self.scope_owner.get(scope_id)
        if scope_owner is not None and scope_owner != key:
            raise ValueError("execution scope belongs to another generation")
        plan_execution = UniversalPlanExecution(
            scope_id,
            context.execution_generation,
            self.created_plan_override or plan,
        )
        self.plan_executions[key] = plan_execution
        self.scope_owner[scope_id] = key
        self.created_plan_executions.append(plan_execution)
        return plan_execution

    def session_id(self, context: UniversalTaskContext) -> str | None:
        self.calls["session"] += 1
        return self.session


def make_orchestrator(
    plan: UniversalPlan,
    callbacks: CallbackRecorder,
    executor: RecordingExecutor | None = None,
) -> tuple[UniversalConsumerOrchestrator, RecordingPlanner, RecordingExecutor]:
    planner = RecordingPlanner(plan)
    action_executor = executor or RecordingExecutor()
    orchestrator = UniversalConsumerOrchestrator(
        planner,
        callbacks.dependencies,
        callbacks.existing_terminal,
        callbacks.existing_sent,
        callbacks.load_plan_execution,
        callbacks.create_plan_execution,
        callbacks.action_execution_state,
        callbacks.session_id,
        action_executor,
    )
    return orchestrator, planner, action_executor


def test_memory_unknown_is_delegated_to_memory_executor_for_exact_recovery() -> None:
    action = make_action(PlannedActionKind.MEMORY_WRITE)
    callbacks = CallbackRecorder(
        dependency_status={"memory": DependencyStatus(ready=True)},
        action_states={0: UniversalActionExecutionState.UNKNOWN},
    )
    orchestrator, _, executor = make_orchestrator(
        make_plan(action, dependencies=("memory",)),
        callbacks,
    )

    result = orchestrator.process(
        make_context(required_dependencies=("memory",))
    )

    assert result.outcome is UniversalConsumerOutcome.COMPLETED
    assert [call.action.kind for call in executor.calls] == [
        PlannedActionKind.MEMORY_WRITE
    ]


def test_non_memory_unknown_is_not_replayed() -> None:
    callbacks = CallbackRecorder(
        action_states={0: UniversalActionExecutionState.UNKNOWN},
    )
    orchestrator, _, executor = make_orchestrator(
        make_plan(make_action()),
        callbacks,
    )

    result = orchestrator.process(make_context())

    assert result.outcome is UniversalConsumerOutcome.ACTION_UNKNOWN
    assert executor.calls == []


@pytest.mark.parametrize(("terminal", "sent"), [(True, False), (False, True)])
def test_duplicate_precedes_dependency_check_and_all_other_work(
    terminal: bool, sent: bool
) -> None:
    callbacks = CallbackRecorder(
        dependency_status={"dws": DependencyStatus(ready=False)},
        terminal=terminal,
        sent=sent,
    )
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=True,
        reason="duplicate_trigger_already_terminal",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.DUPLICATE,
    )
    assert planner.calls == []
    assert executor.calls == []
    assert callbacks.calls["dependencies"] == 0
    assert callbacks.calls["terminal"] == 1
    assert callbacks.calls["sent"] == 1
    assert callbacks.calls["session"] == 0
    assert callbacks.calls["action_state"] == 0
    assert callbacks.calls["load_plan"] == 0
    assert callbacks.calls["create_plan"] == 0


def test_missing_required_dependency_stops_before_planner() -> None:
    callbacks = CallbackRecorder(dependency_status={})
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context(required_dependencies=("dws", "mail")))

    assert result == UniversalConsumerResult(
        completed=False,
        reason="dependency_status_missing:dws",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY,
    )
    assert planner.calls == []
    assert executor.calls == []
    assert callbacks.calls["load_plan"] == 0
    assert callbacks.calls["create_plan"] == 0


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (
            DependencyStatus(ready=False, reason="mail_auth_required"),
            "mail_auth_required",
        ),
        (DependencyStatus(ready=False), "mail_unavailable"),
    ],
)
def test_unready_dependency_uses_explicit_or_default_reason(
    status: DependencyStatus, expected_reason: str
) -> None:
    callbacks = CallbackRecorder(
        dependency_status={
            "dws": DependencyStatus(ready=True),
            "mail": status,
        }
    )
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context(required_dependencies=("dws", "mail")))

    assert result.reason == expected_reason
    assert result.completed is False
    assert result.outcome is UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY
    assert result.executed_actions == ()
    assert planner.calls == []
    assert executor.calls == []


def test_planner_receives_context_and_session_once() -> None:
    context = make_context()
    callbacks = CallbackRecorder(session="session-42")
    orchestrator, planner, _ = make_orchestrator(
        make_plan(make_action(PlannedActionKind.NO_REPLY)), callbacks
    )

    orchestrator.process(context)

    assert planner.calls == [(context, "session-42")]
    assert callbacks.calls["session"] == 1
    assert callbacks.calls["load_plan"] == 1
    assert callbacks.calls["create_plan"] == 1


def test_normal_retry_loads_plan_and_unknown_execution_is_not_replayed() -> None:
    context = make_context()
    persisted_plan = make_plan(make_action(), reason="Persisted plan reason")
    persisted = UniversalPlanExecution("persisted-scope", "initial", persisted_plan)
    changed_candidate = make_plan(make_action(), reason="Planner drifted reason")
    callbacks = CallbackRecorder(
        loaded_plan_execution=persisted,
        action_states={0: UniversalActionExecutionState.UNKNOWN},
    )
    orchestrator, planner, executor = make_orchestrator(changed_candidate, callbacks)

    first = orchestrator.process(context)
    second = orchestrator.process(context)

    expected = build_universal_action_execution(
        context,
        persisted,
        persisted.plan.actions[0],
        0,
    )
    assert planner.calls == []
    assert callbacks.calls["session"] == 0
    assert callbacks.calls["load_plan"] == 2
    assert callbacks.calls["create_plan"] == 0
    assert [execution.execution_id for execution in callbacks.action_state_calls] == [
        expected.execution_id,
        expected.execution_id,
    ]
    assert first.reason == f"action_execution_unknown:{expected.execution_id}"
    assert second.reason == first.reason
    assert first.outcome is UniversalConsumerOutcome.ACTION_UNKNOWN
    assert executor.calls == []


def test_force_new_retry_same_generation_loads_scope_and_blocks_unknown_replay() -> (
    None
):
    action = make_action()
    callbacks = CallbackRecorder(
        create_scope_ids=["manual-scope"],
        action_states={0: UniversalActionExecutionState.UNKNOWN},
    )
    orchestrator, planner, executor = make_orchestrator(make_plan(action), callbacks)
    context = make_context(
        force_new_decision=True,
        execution_generation="manual-rerun-1",
    )

    first = orchestrator.process(context)
    second = orchestrator.process(context)

    assert callbacks.calls["load_plan"] == 2
    assert callbacks.calls["create_plan"] == 1
    assert len(planner.calls) == 1
    assert callbacks.calls["session"] == 1
    assert len(callbacks.action_state_calls) == 2
    assert callbacks.action_state_calls[0].execution_id == (
        callbacks.action_state_calls[1].execution_id
    )
    assert first.outcome is UniversalConsumerOutcome.ACTION_UNKNOWN
    assert second.reason == first.reason
    assert executor.calls == []


def test_different_execution_generations_create_different_action_ids() -> None:
    action = make_action()
    callbacks = CallbackRecorder(create_scope_ids=["scope-1", "scope-2"])
    orchestrator, planner, executor = make_orchestrator(make_plan(action), callbacks)

    orchestrator.process(
        make_context(force_new_decision=True, execution_generation="rerun-1")
    )
    orchestrator.process(
        make_context(force_new_decision=True, execution_generation="rerun-2")
    )

    assert callbacks.calls["load_plan"] == 2
    assert callbacks.calls["create_plan"] == 2
    assert len(planner.calls) == 2
    assert [execution.execution_scope_id for execution in executor.calls] == [
        "scope-1",
        "scope-2",
    ]
    assert executor.calls[0].execution_id != executor.calls[1].execution_id


def test_persistent_store_rejects_scope_reuse_across_generations() -> None:
    callbacks = CallbackRecorder(create_scope_ids=["same-scope", "same-scope"])
    orchestrator, _, executor = make_orchestrator(make_plan(make_action()), callbacks)

    orchestrator.process(make_context(execution_generation="generation-1"))
    with pytest.raises(
        ValueError, match="execution scope belongs to another generation"
    ):
        orchestrator.process(make_context(execution_generation="generation-2"))

    assert len(executor.calls) == 1


def test_new_orchestrator_loads_scope_from_shared_persistent_store() -> None:
    callbacks = CallbackRecorder(create_scope_ids=["shared-scope"])
    context = make_context(execution_generation="shared-generation")
    first_orchestrator, first_planner, first_executor = make_orchestrator(
        make_plan(make_action(), reason="Persisted reason"), callbacks
    )
    second_orchestrator, second_planner, second_executor = make_orchestrator(
        make_plan(make_action(), reason="Drifted reason"), callbacks
    )

    first_orchestrator.process(context)
    second_result = second_orchestrator.process(context)

    assert len(first_planner.calls) == 1
    assert second_planner.calls == []
    assert callbacks.calls["create_plan"] == 1
    assert first_executor.calls[0].execution_id == second_executor.calls[0].execution_id
    assert second_result.reason == "Persisted reason"


def test_loaded_plan_still_runs_current_validator_without_creating_scope() -> None:
    action = make_action()
    action.target["conversation_id"] = "wrong-conversation"
    callbacks = CallbackRecorder(
        loaded_plan_execution=UniversalPlanExecution(
            "persisted-scope", "initial", make_plan(action)
        )
    )
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context())

    assert result.outcome is UniversalConsumerOutcome.VALIDATION_BLOCKED
    assert result.reason == "action_target_mismatch"
    assert planner.calls == []
    assert callbacks.calls["create_plan"] == 0
    assert executor.calls == []


def test_loaded_plan_rechecks_current_plan_dependencies() -> None:
    callbacks = CallbackRecorder(
        dependency_status={"dws": DependencyStatus(ready=True)},
        loaded_plan_execution=UniversalPlanExecution(
            "persisted-scope",
            "initial",
            make_plan(make_action(), dependencies=("mail",)),
        ),
    )
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context())

    assert result.outcome is UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY
    assert result.reason == "dependency_status_missing:mail"
    assert callbacks.dependency_requests == [("dws",), ("mail",)]
    assert planner.calls == []
    assert callbacks.calls["create_plan"] == 0
    assert executor.calls == []


def test_loaded_plan_from_another_generation_fails_closed() -> None:
    class WrongGenerationCallbacks(CallbackRecorder):
        def load_plan_execution(
            self, context: UniversalTaskContext
        ) -> UniversalPlanExecution | None:
            self.calls["load_plan"] += 1
            return UniversalPlanExecution(
                "wrong-scope",
                "another-generation",
                make_plan(make_action()),
            )

    callbacks = WrongGenerationCallbacks()
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    with pytest.raises(ValueError, match="execution generation mismatch"):
        orchestrator.process(make_context(execution_generation="initial"))

    assert planner.calls == []
    assert executor.calls == []


def test_created_plan_from_another_generation_fails_closed() -> None:
    class WrongGenerationCallbacks(CallbackRecorder):
        def create_plan_execution(
            self, context: UniversalTaskContext, plan: UniversalPlan
        ) -> UniversalPlanExecution:
            self.calls["create_plan"] += 1
            return UniversalPlanExecution(
                "wrong-scope",
                "another-generation",
                plan,
            )

    callbacks = WrongGenerationCallbacks()
    orchestrator, _, executor = make_orchestrator(make_plan(make_action()), callbacks)

    with pytest.raises(ValueError, match="execution generation mismatch"):
        orchestrator.process(make_context(execution_generation="initial"))

    assert executor.calls == []


def test_empty_created_scope_fails_closed() -> None:
    callbacks = CallbackRecorder(create_scope_ids=["   "])
    orchestrator, _, executor = make_orchestrator(make_plan(make_action()), callbacks)

    with pytest.raises(ValueError, match="execution_scope_id must be non-empty"):
        orchestrator.process(make_context())

    assert executor.calls == []


def test_atomic_create_returning_existing_same_plan_is_accepted() -> None:
    plan = make_plan(make_action(), reason="Candidate plan")
    callbacks = CallbackRecorder(
        loaded_plan_execution=UniversalPlanExecution("existing-scope", "initial", plan),
        load_misses=1,
    )
    orchestrator, planner, executor = make_orchestrator(plan, callbacks)

    result = orchestrator.process(make_context())

    assert result.outcome is UniversalConsumerOutcome.COMPLETED
    assert len(planner.calls) == 1
    assert callbacks.calls["create_plan"] == 1
    assert executor.calls[0].execution_scope_id == "existing-scope"


def test_atomic_create_returning_existing_different_plan_fails_closed() -> None:
    persisted = make_plan(make_action(), reason="Persisted plan")
    callbacks = CallbackRecorder(
        loaded_plan_execution=UniversalPlanExecution(
            "existing-scope", "initial", persisted
        ),
        load_misses=1,
    )
    orchestrator, _, executor = make_orchestrator(
        make_plan(make_action(), reason="Candidate plan"), callbacks
    )

    with pytest.raises(ValueError, match="created plan does not match candidate"):
        orchestrator.process(make_context())

    assert callbacks.calls["create_plan"] == 1
    assert callbacks.calls["action_state"] == 0
    assert executor.calls == []


def test_post_plan_dependencies_are_resolved_in_order_without_refetching() -> None:
    callbacks = CallbackRecorder(
        dependency_status={
            "dws": DependencyStatus(ready=True),
            "mail": DependencyStatus(ready=True),
            "calendar": DependencyStatus(ready=True),
            "memory": DependencyStatus(ready=True),
        }
    )
    plan = make_plan(
        make_action(),
        dependencies=("mail", "calendar", "dws", "memory"),
    )
    orchestrator, planner, executor = make_orchestrator(plan, callbacks)

    result = orchestrator.process(make_context(required_dependencies=("dws", "mail")))

    assert result.outcome is UniversalConsumerOutcome.COMPLETED
    assert callbacks.dependency_requests == [
        ("dws", "mail"),
        ("calendar", "memory"),
    ]
    assert len(planner.calls) == 1
    assert len(executor.calls) == 1


def test_post_plan_missing_dependency_waits_without_execution() -> None:
    callbacks = CallbackRecorder(
        dependency_status={"dws": DependencyStatus(ready=True)}
    )
    orchestrator, _, executor = make_orchestrator(
        make_plan(make_action(), dependencies=("mail",)), callbacks
    )

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=False,
        reason="dependency_status_missing:mail",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.WAITING_FOR_DEPENDENCY,
    )
    assert callbacks.dependency_requests == [("dws",), ("mail",)]
    assert executor.calls == []


def test_empty_dependency_requests_do_not_call_factory() -> None:
    callbacks = CallbackRecorder(dependency_status={})
    orchestrator, _, _ = make_orchestrator(
        make_plan(make_action(PlannedActionKind.NO_REPLY)), callbacks
    )

    result = orchestrator.process(make_context(required_dependencies=()))

    assert result.outcome is UniversalConsumerOutcome.COMPLETED
    assert callbacks.dependency_requests == []


def test_duplicate_created_during_planning_stops_before_execution() -> None:
    callbacks = CallbackRecorder(
        terminal_results=[False, True],
        sent_results=[False, False],
    )
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=True,
        reason="duplicate_trigger_already_terminal",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.DUPLICATE,
    )
    assert len(planner.calls) == 1
    assert executor.calls == []
    assert callbacks.calls["terminal"] == 2
    assert callbacks.calls["sent"] == 2
    assert callbacks.calls["action_state"] == 0
    assert callbacks.calls["create_plan"] == 0


def test_dry_run_is_validated_without_execution() -> None:
    callbacks = CallbackRecorder()
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    result = orchestrator.process(make_context(dry_run=True))

    assert result == UniversalConsumerResult(
        completed=False,
        reason="dry_run",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.DRY_RUN,
    )
    assert len(planner.calls) == 1
    assert executor.calls == []
    assert callbacks.calls["create_plan"] == 0


def test_valid_action_executes_and_returns_plan_reason() -> None:
    action = make_action()
    callbacks = CallbackRecorder()
    orchestrator, _, executor = make_orchestrator(
        make_plan(action, reason="Reply delivered"), callbacks
    )

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=True,
        reason="Reply delivered",
        executed_actions=(action,),
        outcome=UniversalConsumerOutcome.COMPLETED,
    )
    assert [execution.action for execution in executor.calls] == [action]
    assert callbacks.calls["create_plan"] == 1


def test_execution_failure_stops_and_returns_only_successful_actions() -> None:
    first = make_action(PlannedActionKind.MEMORY_WRITE)
    second = make_action(PlannedActionKind.MEMORY_WRITE)
    third = make_action(PlannedActionKind.MEMORY_WRITE)
    callbacks = CallbackRecorder()
    executor = RecordingExecutor([True, False, True])
    orchestrator, _, _ = make_orchestrator(
        make_plan(first, second, third), callbacks, executor
    )

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=False,
        reason="action_execution_failed:memory_write",
        executed_actions=(first,),
        outcome=UniversalConsumerOutcome.ACTION_FAILED,
    )
    assert [execution.action for execution in executor.calls] == [first, second]


def test_partial_retry_skips_previously_completed_action() -> None:
    first = make_action(PlannedActionKind.MEMORY_WRITE)
    first.payload["data"] = "first"
    second = make_action(PlannedActionKind.MEMORY_WRITE)
    second.payload["data"] = "second"
    callbacks = CallbackRecorder(
        action_states={0: UniversalActionExecutionState.SUCCEEDED}
    )
    orchestrator, _, executor = make_orchestrator(make_plan(first, second), callbacks)

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=True,
        reason="Plan completed",
        executed_actions=(second,),
        outcome=UniversalConsumerOutcome.COMPLETED,
    )
    assert [execution.action_index for execution in callbacks.action_state_calls] == [
        0,
        1,
    ]
    assert [execution.action for execution in executor.calls] == [second]


def test_unknown_action_execution_stops_without_replay() -> None:
    callbacks = CallbackRecorder(
        action_states={0: UniversalActionExecutionState.UNKNOWN}
    )
    orchestrator, _, executor = make_orchestrator(make_plan(make_action()), callbacks)

    result = orchestrator.process(make_context())

    execution = callbacks.action_state_calls[0]
    assert result == UniversalConsumerResult(
        completed=False,
        reason=f"action_execution_unknown:{execution.execution_id}",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.ACTION_UNKNOWN,
    )
    assert executor.calls == []
    assert callbacks.calls["create_plan"] == 1


def test_callback_and_worker_mutations_are_isolated_from_audit_action() -> None:
    class MutatingCallbacks(CallbackRecorder):
        def action_execution_state(
            self, execution: UniversalActionExecution
        ) -> UniversalActionExecutionState:
            state = super().action_execution_state(execution)
            execution.action.target["conversation_id"] = "callback-corrupted"
            execution.action.payload["text"] = "Callback corrupted reply."
            return state

    class MutatingWorker:
        def __init__(self) -> None:
            self.calls: list[UniversalActionExecution] = []
            self.target_before_mutation = ""

        def execute_universal_send_reply(
            self, execution: UniversalActionExecution
        ) -> bool:
            self.calls.append(execution)
            self.target_before_mutation = execution.action.target["conversation_id"]
            execution.action.target["conversation_id"] = "executor-corrupted"
            execution.action.payload["text"] = "Executor corrupted reply."
            return True

    action = make_action()
    callbacks = MutatingCallbacks()
    worker = MutatingWorker()
    planner = RecordingPlanner(make_plan(action))
    orchestrator = UniversalConsumerOrchestrator(
        planner,
        callbacks.dependencies,
        callbacks.existing_terminal,
        callbacks.existing_sent,
        callbacks.load_plan_execution,
        callbacks.create_plan_execution,
        callbacks.action_execution_state,
        callbacks.session_id,
        UniversalActionExecutor(worker),
    )

    result = orchestrator.process(make_context())

    audited_action = result.executed_actions[0]
    assert audited_action.target["conversation_id"] == "conversation-1"
    assert audited_action.payload["text"] == "Done."
    callback_execution = callbacks.action_state_calls[0]
    worker_execution = worker.calls[0]
    assert callback_execution.execution_id == worker_execution.execution_id
    assert callback_execution is not worker_execution
    assert callback_execution.action is not worker_execution.action
    assert worker.target_before_mutation == "conversation-1"
    assert audited_action is not worker_execution.action


def test_nonterminal_blocked_action_remains_incomplete() -> None:
    action = make_action(PlannedActionKind.BLOCKED, terminal=False)
    callbacks = CallbackRecorder()
    orchestrator, _, _ = make_orchestrator(make_plan(action), callbacks)

    result = orchestrator.process(make_context())

    assert result.completed is False
    assert result.outcome is UniversalConsumerOutcome.NONTERMINAL_BLOCKED
    assert result.reason == "Plan completed"
    assert result.executed_actions == (action,)


def test_validator_rejection_has_distinct_outcome() -> None:
    action = make_action()
    action.target["conversation_id"] = "wrong-conversation"
    callbacks = CallbackRecorder()
    orchestrator, _, executor = make_orchestrator(make_plan(action), callbacks)

    result = orchestrator.process(make_context())

    assert result == UniversalConsumerResult(
        completed=False,
        reason="action_target_mismatch",
        executed_actions=(),
        outcome=UniversalConsumerOutcome.VALIDATION_BLOCKED,
    )
    assert executor.calls == []
    assert callbacks.calls["create_plan"] == 0


@pytest.mark.parametrize(
    "action",
    [
        make_action(PlannedActionKind.NO_REPLY),
        make_action(PlannedActionKind.HANDOFF_TO_HUMAN),
        make_action(PlannedActionKind.STOP_WITH_ERROR),
        make_action(PlannedActionKind.BLOCKED, terminal=True),
    ],
)
def test_sole_terminal_action_completes(action: PlannedAction) -> None:
    callbacks = CallbackRecorder()
    orchestrator, _, _ = make_orchestrator(make_plan(action), callbacks)

    result = orchestrator.process(make_context())

    assert result.completed is True
    assert result.outcome is UniversalConsumerOutcome.COMPLETED
    assert result.executed_actions == (action,)


def test_callbacks_use_expected_counts_across_successful_processing() -> None:
    callbacks = CallbackRecorder()
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    orchestrator.process(make_context())

    assert callbacks.calls == {
        "dependencies": 1,
        "terminal": 2,
        "sent": 2,
        "session": 1,
        "action_state": 1,
        "load_plan": 1,
        "create_plan": 1,
    }
    assert len(planner.calls) == 1
    assert len(executor.calls) == 1


def test_executor_exception_propagates() -> None:
    class RaisingExecutor(RecordingExecutor):
        def execute(self, execution: UniversalActionExecution) -> bool:
            raise RuntimeError("executor exploded")

    callbacks = CallbackRecorder()
    orchestrator, _, _ = make_orchestrator(
        make_plan(make_action()), callbacks, RaisingExecutor()
    )

    with pytest.raises(RuntimeError, match="executor exploded"):
        orchestrator.process(make_context())


def test_consumer_outcomes_are_stable_string_values() -> None:
    assert [outcome.value for outcome in UniversalConsumerOutcome] == [
        "completed",
        "duplicate",
        "waiting_for_dependency",
        "dry_run",
        "validation_blocked",
        "action_failed",
        "action_unknown",
        "nonterminal_blocked",
    ]
