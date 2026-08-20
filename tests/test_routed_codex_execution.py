from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
)
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
)
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
CAPABILITIES = frozenset({"structured_output", "reviewed_read_tools"})


def failed_session_probe(*_args):
    raise OSError("session evidence unavailable")


class FakeAdapter:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str | None, str, bool]] = []

    def build_command(
        self,
        route,
        prompt,
        session_id,
        image_paths,
        output_schema_path,
        use_output_schema,
        approval_policy,
        developer_instructions,
        use_approval_bypass,
    ):
        self.commands.append(
            (route.name, session_id, approval_policy, use_approval_bypass)
        )
        return ["codex-test", route.name, session_id or "fresh"]

    def build_env(self, route):
        return {"ROUTE": route.name}

    def classify_failure(self, stdout, stderr, returncode, **kwargs):
        return RuntimeFailure(
            failure_class=RuntimeFailureClass.AUTHENTICATION,
            code="codex_login_required",
            detail="redacted provider failure",
            failover_permitted=True,
            route_pause_required=False,
        )


def seed_structured_parent(store: AutoReplyStore, request_id: int = 12) -> str:
    with store._connect() as db:
        db.execute(
            """
            insert into okr_review_requests (
                id, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, period_label, period_start,
                period_end, status
            ) values (?, ?, 'title', ?, 'sender', 'text', 'period',
                      'start', 'end', 'processing')
            """,
            (request_id, f"cid-{request_id}", f"msg-{request_id}"),
        )
    return str(request_id)


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "routed-codex.sqlite3")


@pytest.fixture
def config():
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_KEY": "configured-secret",
        }
    )


def make_router(store, config, *, snapshots=None):
    current = (
        snapshots
        if snapshots is not None
        else {
            route.name: RuntimeCapabilitySnapshot(
                route_name=route.name,
                capabilities=CAPABILITIES,
                healthy=True,
                checked_at="2026-08-20 09:59:00",
                expires_at="2026-08-20 10:05:00",
            )
            for route in config.routes
        }
    )
    return AgentRuntimeRouter(
        routes=config.routes,
        store=store,
        snapshots=current,
        now=lambda: NOW,
    )


def test_read_only_execution_fails_over_from_oauth_to_api(store, config):
    key = seed_structured_parent(store)
    adapter = FakeAdapter()
    calls = []

    def executor(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if kwargs["env"]["ROUTE"] == "codex_oauth":
            return ProcessRunResult(1, "", "login failed")
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "api-session"}),
                json.dumps({"type": "result", "value": 42}),
            ]
        )
        return ProcessRunResult(0, stdout, "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
        session_line_counter=lambda session_id: 7,
    )

    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze",
        command_factory=ApprovedCodexCommandFactory.read_only(
            developer_instructions="reviewed reads only"
        ),
        parser=lambda raw: json.loads(raw.splitlines()[-1])["value"],
        conversation_id="cid-12",
        required_capabilities=CAPABILITIES,
    )

    assert result.value == 42
    assert result.route_name == "codex_api"
    assert len(calls) == 2
    assert adapter.commands == [
        ("codex_oauth", None, "never", False),
        ("codex_api", None, "never", False),
    ]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert attempts[0].failure_code == "codex_login_required"
    assert attempts[1].session_id == "api-session"
    assert attempts[1].transcript_start == 0
    assert attempts[1].transcript_end == 7
    assert (
        store.get_conversation_runtime_session("cid-12", "codex_api") == "api-session"
    )


def test_effectful_execution_records_start_and_never_fails_over(store, config):
    key = seed_structured_parent(store)
    adapter = FakeAdapter()
    calls = []

    def executor(command, **kwargs):
        calls.append(command)
        return ProcessRunResult(1, "", "provider unavailable")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="write",
            command_factory=ApprovedCodexCommandFactory.effectful(
                developer_instructions="reviewed write"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    attempts = store.list_runtime_operation_attempts("structured", key)
    assert len(calls) == 1
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].first_effect_started_at

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effectful_replay_blocked"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="write again",
            command_factory=ApprovedCodexCommandFactory.effectful(
                developer_instructions="reviewed write"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )
    assert len(calls) == 1


def test_active_attempt_start_fence_allows_only_one_process(store, config):
    key = seed_structured_parent(store)
    route = config.routes[0]
    claimed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind.value,
        route.credential_mode.value,
        route.model,
    )
    store.mark_agent_runtime_attempt_running_once(claimed.id)
    called = False

    def executor(command, **kwargs):
        nonlocal called
        called = True
        return ProcessRunResult(0, "{}", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_attempt_active"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )
    assert called is False


def test_read_only_policy_detects_effect_event_and_blocks_failover(store, config):
    key = seed_structured_parent(store)
    calls = []

    def executor(command, **kwargs):
        calls.append(command)
        kwargs["on_stdout_line"](
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"metadata": {"effect": "effectful"}},
                }
            )
        )
        return ProcessRunResult(1, "", "provider unavailable")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effect_policy_violation"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    assert len(calls) == 1
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert attempts[0].first_effect_started_at
    assert attempts[0].status == "failed"


def test_read_only_policy_abort_terminates_child_before_rejected_work_runs(
    store, config, tmp_path
):
    key = seed_structured_parent(store)
    marker = tmp_path / "rejected-child-continued"

    class ChildAdapter(FakeAdapter):
        def build_command(self, **_kwargs):
            event = json.dumps(
                {
                    "type": "item.started",
                    "item": {"metadata": {"effect": "effectful"}},
                }
            )
            code = (
                "import pathlib,time; "
                f"print({event!r}, flush=True); "
                "time.sleep(2); "
                f"pathlib.Path({str(marker)!r}).write_text('continued')"
            )
            return [sys.executable, "-c", code]

        def build_env(self, route):
            return os.environ.copy()

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=ChildAdapter(),
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effect_policy_violation"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    assert marker.exists() is False
    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert attempt.status == "failed"
    assert attempt.failure_code == "runtime_effect_policy_violation"
    assert attempt.first_effect_started_at


def test_thread_started_is_persisted_before_executor_failure(store, config):
    key = seed_structured_parent(store)
    callback_observed_persistence = False

    def executor(command, **kwargs):
        nonlocal callback_observed_persistence
        kwargs["on_stdout_line"](
            json.dumps({"type": "thread.started", "thread_id": "early-session"})
        )
        current = store.list_runtime_operation_attempts("structured", key)[0]
        callback_observed_persistence = (
            current.session_id == "early-session"
            and current.transcript_reference == "codex_session:early-session"
        )
        raise OSError("executor lost connection after session start")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_executor_failed"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert callback_observed_persistence is True
    assert attempt.status == "failed"
    assert attempt.session_id == "early-session"
    assert attempt.transcript_reference == "codex_session:early-session"
    assert attempt.transcript_end >= 1


@pytest.mark.parametrize(
    ("failure_stage", "expected_failure_code", "expected_error"),
    [
        ("build", "runtime_command_build_failed", "runtime_post_start_failed"),
        (
            "classifier",
            "runtime_failure_classification_failed",
            "runtime_post_start_failed",
        ),
        (
            "counter",
            "runtime_transcript_evidence_failed",
            "runtime_post_start_failed",
        ),
        ("pause", "runtime_route_pause_failed", "runtime_post_start_failed"),
        ("parser", "runtime_result_invalid", "runtime_result_invalid"),
    ],
)
def test_post_start_exception_terminalizes_attempt_and_retry_stays_bounded(
    store,
    config,
    monkeypatch,
    failure_stage,
    expected_failure_code,
    expected_error,
):
    key = seed_structured_parent(store)
    adapter = FakeAdapter()
    process_calls = 0

    def raise_stage_error(*_args, **_kwargs):
        raise OSError(f"{failure_stage} unavailable")

    if failure_stage == "build":
        monkeypatch.setattr(adapter, "build_command", raise_stage_error)
    elif failure_stage == "classifier":
        monkeypatch.setattr(adapter, "classify_failure", raise_stage_error)
    elif failure_stage == "pause":
        monkeypatch.setattr(
            adapter,
            "classify_failure",
            lambda *_args, **_kwargs: RuntimeFailure(
                failure_class=RuntimeFailureClass.CAPACITY,
                code="provider_paused",
                detail="redacted",
                failover_permitted=True,
                route_pause_required=True,
            ),
        )
        monkeypatch.setattr(store, "open_runtime_route_pause", raise_stage_error)

    def executor(command, **kwargs):
        nonlocal process_calls
        process_calls += 1
        if failure_stage == "counter":
            kwargs["on_stdout_line"](
                json.dumps({"type": "thread.started", "thread_id": "counter-session"})
            )
        if failure_stage == "parser":
            return ProcessRunResult(0, json.dumps({"type": "result"}), "")
        return ProcessRunResult(1, "", "provider unavailable")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
        session_line_counter=(
            raise_stage_error if failure_stage == "counter" else lambda _session: 0
        ),
    )
    parser = raise_stage_error if failure_stage == "parser" else lambda raw: raw
    execution_args = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "read",
        "command_factory": ApprovedCodexCommandFactory.read_only(
            developer_instructions="reviewed reads only"
        ),
        "parser": parser,
        "required_capabilities": CAPABILITIES,
    }

    with pytest.raises(RoutedCodexExecutionError, match=expected_error):
        routed.execute(**execution_args)

    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert attempt.status == "failed"
    assert attempt.failure_code == expected_failure_code
    assert attempt.failover_permitted is False
    assert not any(
        item.status in {"starting", "running"}
        for item in store.list_runtime_operation_attempts("structured", key)
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_route_unavailable"):
        routed.execute(**execution_args)
    assert process_calls == (0 if failure_stage == "build" else 1)


@pytest.mark.parametrize(
    "session_probe", [lambda *_: True, lambda *_: None, failed_session_probe]
)
def test_hidden_or_ambiguous_local_session_blocks_read_only_failover(
    store, config, session_probe
):
    key = seed_structured_parent(store)
    calls = []

    def executor(command, **kwargs):
        calls.append(command)
        return ProcessRunResult(
            1,
            json.dumps({"type": "thread.started", "thread_id": "hidden-session"}),
            "provider unavailable",
        )

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_line_counter=lambda session_id: 4,
        session_effect_probe=session_probe,
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effect_policy_violation"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    assert len(calls) == 1
    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert attempt.first_effect_started_at
    assert attempt.session_id == "hidden-session"
    assert attempt.transcript_end == 4
    with pytest.raises(RoutedCodexExecutionError, match="runtime_route_unavailable"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="retry read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )
    assert len(calls) == 1


def test_no_eligible_route_or_terminal_parent_never_starts_process(store, config):
    key = seed_structured_parent(store)
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config, snapshots={}),
        adapter=FakeAdapter(),
        executor=lambda *args, **kwargs: pytest.fail("process must not start"),
    )
    with pytest.raises(RoutedCodexExecutionError, match="runtime_route_unavailable"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )

    with store._connect() as db:
        db.execute("update okr_review_requests set status='failed' where id=12")
    with pytest.raises(ValueError, match="parent"):
        RoutedCodexExecution(
            store=store,
            config=config,
            router=make_router(store, config),
            adapter=FakeAdapter(),
            executor=lambda *args, **kwargs: pytest.fail("process must not start"),
        ).execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            required_capabilities=CAPABILITIES,
        )
