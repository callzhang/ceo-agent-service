import pytest

from app.universal_consumer import (
    UniversalConsumerOrchestrator,
    UniversalConsumerResult,
)
from app.universal_context import UniversalTaskContext
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
        force_new_decision=False,
        dry_run=dry_run,
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
    return PlannedAction(
        kind=kind,
        reason=f"Execute {kind.value}",
        target=target,
        payload=payload,
    )


def make_plan(*actions: PlannedAction, reason: str = "Plan completed") -> UniversalPlan:
    return UniversalPlan(
        task_kind="message_handling",
        reason=reason,
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
        self.calls: list[PlannedAction] = []

    def execute(self, action: PlannedAction) -> bool:
        self.calls.append(action)
        return self.results.pop(0) if self.results else True


class CallbackRecorder:
    def __init__(
        self,
        *,
        dependency_status: dict[str, DependencyStatus] | None = None,
        terminal: bool = False,
        sent: bool = False,
        session: str | None = "session-1",
    ) -> None:
        self.dependency_status = (
            {"dws": DependencyStatus(ready=True)}
            if dependency_status is None
            else dependency_status
        )
        self.terminal = terminal
        self.sent = sent
        self.session = session
        self.calls = {"dependencies": 0, "terminal": 0, "sent": 0, "session": 0}

    def dependencies(
        self, context: UniversalTaskContext
    ) -> dict[str, DependencyStatus]:
        self.calls["dependencies"] += 1
        return self.dependency_status

    def existing_terminal(self, context: UniversalTaskContext) -> bool:
        self.calls["terminal"] += 1
        return self.terminal

    def existing_sent(self, context: UniversalTaskContext) -> bool:
        self.calls["sent"] += 1
        return self.sent

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
        callbacks.session_id,
        action_executor,
    )
    return orchestrator, planner, action_executor


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
    )
    assert planner.calls == []
    assert executor.calls == []
    assert callbacks.calls["dependencies"] == 0
    assert callbacks.calls["terminal"] == 1
    assert callbacks.calls["sent"] == 1
    assert callbacks.calls["session"] == 0


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
    )
    assert planner.calls == []
    assert executor.calls == []


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (DependencyStatus(ready=False, reason="mail_auth_required"), "mail_auth_required"),
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
    )
    assert len(planner.calls) == 1
    assert executor.calls == []


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
    )
    assert executor.calls == [action]


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
    )
    assert executor.calls == [first, second]


def test_nonterminal_blocked_action_remains_incomplete() -> None:
    action = make_action(PlannedActionKind.BLOCKED, terminal=False)
    callbacks = CallbackRecorder()
    orchestrator, _, _ = make_orchestrator(make_plan(action), callbacks)

    result = orchestrator.process(make_context())

    assert result.completed is False
    assert result.reason == "Plan completed"
    assert result.executed_actions == (action,)


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
    assert result.executed_actions == (action,)


def test_callbacks_are_called_once_and_cached_for_validator() -> None:
    callbacks = CallbackRecorder()
    orchestrator, planner, executor = make_orchestrator(
        make_plan(make_action()), callbacks
    )

    orchestrator.process(make_context())

    assert callbacks.calls == {
        "dependencies": 1,
        "terminal": 1,
        "sent": 1,
        "session": 1,
    }
    assert len(planner.calls) == 1
    assert len(executor.calls) == 1


def test_executor_exception_propagates() -> None:
    class RaisingExecutor(RecordingExecutor):
        def execute(self, action: PlannedAction) -> bool:
            raise RuntimeError("executor exploded")

    callbacks = CallbackRecorder()
    orchestrator, _, _ = make_orchestrator(
        make_plan(make_action()), callbacks, RaisingExecutor()
    )

    with pytest.raises(RuntimeError, match="executor exploded"):
        orchestrator.process(make_context())
