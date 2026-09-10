from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
)
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    CodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionCancelled,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.friday_runtime_adapter import FridayExecutionResult, FridayRuntimeError
from app.process_runner import ProcessRunResult
from app.store import MAX_RUNTIME_RESULT_ENVELOPE_BYTES, AgentRole, AutoReplyStore

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
CAPABILITIES = frozenset({"structured_output", "reviewed_read_tools"})
INT_CODEC = RoutedResultCodec.integer(schema_id="test.integer.v1")
TEXT_CODEC = RoutedResultCodec.text(schema_id="test.text.v1")


class FakeFridayAdapter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


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
        sandbox_mode=None,
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


def seed_agent_run_parent(store: AutoReplyStore, task_id: int = 901) -> int:
    generation = f"generation-{task_id}"
    with store._connect() as db:
        db.execute(
            """
            insert into reply_tasks (
                id, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (?, ?, 'WeChat', 1, ?, '2026-08-20 10:00:00',
                      'sender', 'hello', ?, 'processing')
            """,
            (task_id, f"wechat-{task_id}", f"message-{task_id}", generation),
        )
    claim = store.claim_agent_run(
        task_id,
        generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="decision-owner",
    )
    assert claim.claimed is True
    return claim.run.id


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
                checked_at="2026-08-20T09:59:00+00:00",
                expires_at="2026-08-20T10:05:00+00:00",
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


def _friday_config(monkeypatch, routes):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", routes)
    monkeypatch.setenv("CEO_CODEX_API_KEY", "configured-secret")
    monkeypatch.setenv("CEO_CLAUDE_API_KEY", "configured-claude-secret")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_PROJECT_ID", "ceo-agent")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_MODEL", "MiniMax-M3")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "1")
    return load_runtime_config(dict(os.environ))


def _friday_snapshots(config):
    return {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=CAPABILITIES,
            healthy=True,
            checked_at="2026-08-20T09:59:00+00:00",
            expires_at="2026-08-20T10:05:00+00:00",
        )
        for route in config.routes
    }


def test_runtime_falls_back_to_friday_in_same_agent_run(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "friday-fallback.sqlite3")
    run_id = seed_agent_run_parent(store, task_id=991)
    config = _friday_config(monkeypatch, "codex_oauth,codex_api,friday_runtime")
    friday = FakeFridayAdapter(
        result=FridayExecutionResult(
            text="7", thread_id="thread-1", turn_id="turn-1",
            operation_id="operation-1", artifact={"final_message": "7"},
        )
    )
    adapter = FakeAdapter()

    def executor(*args, **kwargs):
        return ProcessRunResult(1, "", "provider unavailable")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=AgentRuntimeRouter(
            routes=config.routes, store=store, snapshots=_friday_snapshots(config), now=lambda: NOW
        ),
        adapter=adapter,
        friday_adapter=friday,
        executor=executor,
        now=lambda: NOW,
    )
    result = routed.execute(
        workload_kind="agent_run", workload_key=str(run_id), prompt="return 7",
        command_factory=CodexCommandFactory.standard(developer_instructions="test"),
        parser=int, result_codec=INT_CODEC,
    )

    assert result.value == 7
    assert [a.route_name for a in store.list_agent_runtime_attempts(run_id)] == [
        "codex_oauth", "codex_api", "friday_runtime"
    ]
    assert len(friday.calls) == 1
    assert store.get_agent_run(run_id).status == "running"


def test_friday_unreachable_continues_to_next_configured_route(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "friday-unreachable.sqlite3")
    run_id = seed_agent_run_parent(store, task_id=992)
    config = _friday_config(monkeypatch, "codex_oauth,friday_runtime,claude_api")
    friday = FakeFridayAdapter(
        error=FridayRuntimeError(
            "friday_runtime_unreachable",
            "connection refused",
            retryable=True,
            thread_id="failed-thread",
            turn_id="failed-turn",
            operation_id="failed-operation",
        )
    )
    adapter = FakeAdapter()

    def executor(command, **kwargs):
        if command[1] == "claude_api":
            return ProcessRunResult(0, "9", "")
        return ProcessRunResult(1, "", "provider unavailable")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=AgentRuntimeRouter(
            routes=config.routes, store=store, snapshots=_friday_snapshots(config), now=lambda: NOW
        ),
        adapter=adapter,
        friday_adapter=friday,
        executor=executor,
        now=lambda: NOW,
    )
    result = routed.execute(
        workload_kind="agent_run", workload_key=str(run_id), prompt="return 9",
        command_factory=CodexCommandFactory.standard(developer_instructions="test"),
        parser=int, result_codec=INT_CODEC,
    )

    assert result.value == 9
    attempts = store.list_agent_runtime_attempts(run_id)
    assert [a.route_name for a in attempts] == ["codex_oauth", "friday_runtime", "claude_api"]
    assert attempts[1].failure_code == "friday_runtime_unreachable"
    assert attempts[1].session_id == "friday_thread:failed-thread"
    assert attempts[1].transcript_reference == "friday_operation:failed-operation"


def test_standard_factory_uses_runtime_auto_review_without_app_isolation(
    config, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_SANDBOX", "danger-full-access")
    adapter = CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")
    factory = CodexCommandFactory.standard(
        developer_instructions="normal runtime capabilities"
    )

    command, _env = factory.build(
        adapter=adapter,
        route=config.routes[0],
        prompt="read",
        session_id=None,
    )

    assert "--sandbox" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert 'approval_policy="on-failure"' in command
    assert 'approvals_reviewer="auto_review"' in command
    with pytest.raises((AttributeError, TypeError)):
        factory.developer_instructions = "replace instructions"
    with pytest.raises((AttributeError, TypeError)):
        factory.build = lambda **_kwargs: (["unsafe"], {})


def test_result_codec_enforces_utf8_byte_limit_at_multibyte_boundary():
    empty_size = len(TEXT_CODEC.encode("").encode("utf-8"))
    multibyte_count = (MAX_RUNTIME_RESULT_ENVELOPE_BYTES - empty_size) // 3
    boundary_value = "界" * multibyte_count

    boundary_envelope = TEXT_CODEC.encode(boundary_value)

    assert len(boundary_envelope.encode("utf-8")) <= MAX_RUNTIME_RESULT_ENVELOPE_BYTES
    with pytest.raises(ValueError, match="size limit"):
        TEXT_CODEC.encode(boundary_value + "界")


def test_standard_execution_fails_over_from_oauth_to_api(store, config):
    key = seed_structured_parent(store)
    adapter = FakeAdapter()
    calls = []

    def executor(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if kwargs["env"]["ROUTE"] == "codex_oauth":
            return ProcessRunResult(
                1,
                json.dumps({"type": "thread.started", "thread_id": "oauth-session"}),
                "login failed",
            )
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
        command_factory=CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        parser=lambda raw: json.loads(raw.splitlines()[-1])["value"],
        result_codec=INT_CODEC,
        conversation_id="cid-12",
        required_capabilities=CAPABILITIES,
    )

    assert result.value == 42
    assert result.route_name == "codex_api"
    assert len(calls) == 2
    assert adapter.commands == [
            ("codex_oauth", None, "on-failure", False),
            ("codex_api", None, "on-failure", False),
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


def test_standard_execution_forwards_native_output_to_ui_projection(store, config):
    key = seed_structured_parent(store, 18)
    observed: list[str] = []

    def executor(_command, **kwargs):
        lines = [
            json.dumps({"type": "thread.started", "thread_id": "session-18"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "done"},
                }
            ),
        ]
        for line in lines:
            kwargs["on_stdout_line"](line)
        return ProcessRunResult(0, "\n".join(lines), "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_line_counter=lambda _session_id: 2,
    )

    routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze",
        command_factory=CodexCommandFactory.standard(
            developer_instructions="normal service runtime"
        ),
        parser=lambda _raw: "done",
        result_codec=TEXT_CODEC,
        on_stdout_line=observed.append,
    )

    assert [json.loads(line)["type"] for line in observed] == [
        "thread.started",
        "item.completed",
    ]


def test_user_stop_terminalizes_current_attempt_without_route_failover(store, config):
    key = seed_structured_parent(store, 19)
    stopped = False
    calls = []

    def executor(command, **_kwargs):
        nonlocal stopped
        calls.append(command)
        stopped = True
        return ProcessRunResult(-15, "", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionCancelled):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="normal service runtime"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            cancel_requested=lambda: stopped,
        )

    assert len(calls) == 1
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].failure_code == "runtime_cancelled"


@pytest.mark.parametrize(
    ("pause_code", "deferrable"),
    [("codex_provider_overloaded", True), ("codex_login_required", False)],
)
def test_all_routes_paused_before_selection_is_deferred_only_for_transient_pauses(
    store, config, pause_code, deferrable
):
    run_id = seed_agent_run_parent(store, task_id=945)
    for route in config.routes:
        store.open_runtime_route_pause(
            route.name, pause_code, retry_at="2099-01-01T00:00:00+00:00"
        )
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, "42", ""),
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed") as info:
        routed.execute(
            workload_kind="agent_run",
            workload_key=str(run_id),
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    assert info.value.retryable_external_dependency is deferrable
    assert info.value.runtime_unavailable is deferrable
    assert store.list_agent_runtime_attempts(run_id) == []


def test_pause_opened_after_selection_prevents_attempt_and_child(store, config):
    run_id = seed_agent_run_parent(store, task_id=944)
    calls = []
    delegate = make_router(store, config)

    class RacingRouter:
        def first_route_decision(self, **kwargs):
            decision = delegate.first_route_decision(**kwargs)
            assert decision.route is not None
            store.open_runtime_route_pause(
                decision.route.name,
                "probe_failed",
                retry_at="2099-01-01T00:00:00+00:00",
            )
            return decision

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=RacingRouter(),
        adapter=FakeAdapter(),
        executor=lambda *_args, **_kwargs: (
            calls.append("child") or ProcessRunResult(0, "42", "")
        ),
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        routed.execute(
            workload_kind="agent_run",
            workload_key=str(run_id),
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    assert calls == []
    assert store.list_agent_runtime_attempts(run_id) == []




def test_result_validation_retry_repeats_same_route_once_with_corrected_prompt(
    store, config
):
    key = seed_structured_parent(store, 71)
    adapter = FakeAdapter()
    prompts = []

    def executor(command, **kwargs):
        prompts.append(kwargs["prompt"])
        value = 0 if len(prompts) == 1 else 42
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "thread.started",
                            "thread_id": "session-1",
                        }
                    ),
                    json.dumps({"type": "result", "value": value}),
                ]
            ),
            "",
        )

    def parse(raw):
        value = json.loads(raw.splitlines()[-1])["value"]
        if value != 42:
            raise RoutedResultValidationError("expected complete KR coverage")
        return value

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
        session_line_counter=lambda _session_id: 2,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze all KRs",
        command_factory=CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        parser=parse,
        result_codec=INT_CODEC,
        required_capabilities=CAPABILITIES,
        result_validation_retry=RoutedResultValidationRetry.exactly_once(
            correction_instructions="Return every KR and revalidate the full result."
        ),
    )

    assert result.value == 42
    assert adapter.commands == [
            ("codex_oauth", None, "on-failure", False),
            ("codex_oauth", "session-1", "on-failure", False),
    ]
    assert prompts[0] == "analyze all KRs"
    assert "Return every KR" in prompts[1]
    assert "expected complete KR coverage" in prompts[1]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_oauth",
    ]
    assert attempts[0].failure_code == "runtime_result_validation_failed"
    assert [attempt.session_mode for attempt in attempts] == ["fresh", "resume"]
    assert [attempt.attempt_purpose for attempt in attempts] == [
        "normal",
        "result_validation_correction",
    ]
    assert attempts[0].validation_retry_policy_id == ""
    assert attempts[1].validation_retry_policy_id
    assert attempts[1].validation_result_schema_id == INT_CODEC.schema_id


def test_result_validation_retry_can_resume_same_persisted_session_once(store, config):
    key = seed_structured_parent(store, 171)
    adapter = FakeAdapter()
    prompts = []

    def executor(command, **kwargs):
        prompts.append(kwargs["prompt"])
        value = 0 if len(prompts) == 1 else 42
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "session-171"}),
                    json.dumps({"type": "result", "value": value}),
                ]
            ),
            "",
        )

    def parse(raw):
        value = json.loads(raw.splitlines()[-1])["value"]
        if value != 42:
            raise RoutedResultValidationError("invalid", raw_output=raw)
        return value

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
        session_line_counter=lambda _session_id: 2,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze",
        command_factory=CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        parser=parse,
        result_codec=INT_CODEC,
        required_capabilities=CAPABILITIES,
        result_validation_retry=RoutedResultValidationRetry.same_session_exactly_once(
            correction_prompt=lambda raw: f"repair exactly: {raw}"
        ),
    )

    assert result.value == 42
    assert adapter.commands == [
            ("codex_oauth", None, "on-failure", False),
            ("codex_oauth", "session-171", "on-failure", False),
    ]
    assert prompts[1].startswith("repair exactly:")
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.session_mode for attempt in attempts] == ["fresh", "resume"]
    assert [attempt.source_session_id for attempt in attempts] == ["", "session-171"]


def test_persisted_result_validation_failure_resumes_one_same_route_correction(
    store, config
):
    key = seed_structured_parent(store, 75)
    route = config.routes[0]
    owner = "result-validation-recovery-test"
    failed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind,
        route.credential_mode,
        route.model,
        owner=owner,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(
        failed.id,
        owner=owner,
        now=NOW,
    )
    store.fail_agent_runtime_attempt(
        failed.id,
        RuntimeFailureClass.RESULT.value,
        "runtime_result_validation_failed",
        False,
        session_id="persisted-validation-session",
        transcript_reference="codex_session:persisted-validation-session",
        transcript_start=0,
        transcript_end=1,
        owner=owner,
        now=NOW,
    )
    prompts = []
    adapter = FakeAdapter()

    def executor(command, **kwargs):
        prompts.append(kwargs["prompt"])
        return ProcessRunResult(0, json.dumps({"value": 42}), "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=executor,
        session_id_parser=lambda _raw: "persisted-validation-session",
        owner=owner,
        now=lambda: NOW,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze all KRs",
        command_factory=CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        parser=lambda raw: json.loads(raw)["value"],
        result_codec=INT_CODEC,
        required_capabilities=CAPABILITIES,
        result_validation_retry=RoutedResultValidationRetry.exactly_once(
            correction_instructions="Return every KR and revalidate the full result."
        ),
    )

    assert result.value == 42
    assert adapter.commands == [
        ("codex_oauth", "persisted-validation-session", "on-failure", False)
    ]
    assert len(prompts) == 1
    assert "Return every KR" in prompts[0]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_oauth",
    ]
    assert attempts[0].failure_code == "runtime_result_validation_failed"


def test_same_session_validation_retry_recovers_after_persisted_failure(store, config):
    key = seed_structured_parent(store, 175)
    route = config.routes[0]
    owner = "same-session-result-recovery"
    failed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind,
        route.credential_mode,
        route.model,
        owner=owner,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(failed.id, owner=owner, now=NOW)
    store.fail_agent_runtime_attempt(
        failed.id,
        RuntimeFailureClass.RESULT.value,
        "runtime_result_validation_failed",
        False,
        session_id="persisted-session-175",
        transcript_reference="codex_session:persisted-session-175",
        transcript_start=3,
        transcript_end=7,
        owner=owner,
        now=NOW,
    )
    adapter = FakeAdapter()
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=adapter,
        executor=lambda command, **kwargs: ProcessRunResult(
            0, json.dumps({"value": 42}), ""
        ),
        session_line_counter=lambda _session_id: 7,
        owner=owner,
        now=lambda: NOW,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze",
        command_factory=CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        parser=lambda raw: json.loads(raw)["value"],
        result_codec=INT_CODEC,
        required_capabilities=CAPABILITIES,
        result_validation_retry=RoutedResultValidationRetry.same_session_exactly_once(
            correction_prompt=lambda raw: f"repair persisted: {raw}"
        ),
    )

    assert result.value == 42
    assert adapter.commands == [
        ("codex_oauth", "persisted-session-175", "on-failure", False)
    ]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.session_mode for attempt in attempts] == ["fresh", "resume"]


def test_result_validation_retry_is_consumed_after_exactly_one_repeat(store, config):
    key = seed_structured_parent(store, 72)
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(0, json.dumps({"value": 0}), "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_id_parser=lambda _raw: "validation-consumed-session",
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_result_validation_failed"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda _raw: (_ for _ in ()).throw(
                RoutedResultValidationError("still incomplete")
            ),
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
            result_validation_retry=RoutedResultValidationRetry.exactly_once(
                correction_instructions="Return the complete result."
            ),
        )

    assert calls == 2
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "failed"]
    assert [attempt.failure_code for attempt in attempts] == [
        "runtime_result_validation_failed",
        "runtime_result_validation_failed",
    ]
    with pytest.raises(
        RoutedCodexExecutionError,
        match="runtime_result_validation_retry_consumed",
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda _raw: 42,
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
            result_validation_retry=RoutedResultValidationRetry.exactly_once(
                correction_instructions="Return the complete result."
            ),
        )
    assert calls == 2


def test_expired_persisted_correction_attempt_never_starts_third_prompt_or_failover(
    store, config
):
    key = seed_structured_parent(store, 76)
    route = config.routes[0]
    owner = "expired-correction-owner"
    first = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind,
        route.credential_mode,
        route.model,
        owner=owner,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(first.id, owner=owner, now=NOW)
    store.fail_agent_runtime_attempt(
        first.id,
        RuntimeFailureClass.RESULT.value,
        "runtime_result_validation_failed",
        False,
        owner=owner,
        now=NOW,
    )
    correction = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind,
        route.credential_mode,
        route.model,
        attempt_purpose="result_validation_correction",
        validation_retry_policy_id="result_validation_retry.v1:test",
        validation_result_schema_id=INT_CODEC.schema_id,
        owner=owner,
        lease_seconds=1,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_superseded(first.id)
    store.mark_agent_runtime_attempt_running_once(
        correction.id,
        owner=owner,
        lease_seconds=1,
        now=NOW,
    )
    calls = 0

    def executor(_command, **_kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(0, json.dumps({"value": 42}), "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        owner="recovery-owner",
        now=lambda: NOW + timedelta(minutes=1),
    )

    with pytest.raises(
        RoutedCodexExecutionError,
        match="runtime_result_validation_retry_consumed",
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="original prompt must never execute again",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: json.loads(raw)["value"],
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
            result_validation_retry=RoutedResultValidationRetry.exactly_once(
                correction_instructions="Return the complete result."
            ),
        )

    assert calls == 0
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert len(attempts) == 2
    assert attempts[-1].attempt_purpose == "result_validation_correction"
    assert attempts[-1].failure_code == "runtime_lease_expired"




def test_exhausted_transport_failure_exposes_structured_external_retry_metadata(
    store, config
):
    key = seed_structured_parent(store, 173)

    class TransportAdapter(FakeAdapter):
        def classify_failure(self, stdout, stderr, returncode, **kwargs):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.TRANSPORT,
                code="codex_transport_disconnected",
                detail="redacted",
                retryable_on_same_route=True,
                failover_permitted=True,
            )

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=TransportAdapter(),
        executor=lambda command, **kwargs: ProcessRunResult(1, "", "failed"),
        session_id_parser=lambda _raw: "transport-session",
    )

    with pytest.raises(RoutedCodexExecutionError) as raised:
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    assert raised.value.failure_class is RuntimeFailureClass.TRANSPORT
    assert raised.value.failure_code == "codex_transport_disconnected"
    assert raised.value.retryable_external_dependency is True


def test_exhausted_auth_failure_is_not_external_dependency_retryable(store, config):
    key = seed_structured_parent(store, 174)
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda command, **kwargs: ProcessRunResult(1, "", "failed"),
        session_id_parser=lambda _raw: "auth-session",
    )

    with pytest.raises(RoutedCodexExecutionError) as raised:
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    assert raised.value.failure_class is RuntimeFailureClass.AUTHENTICATION
    assert raised.value.failure_code == "codex_login_required"
    assert raised.value.retryable_external_dependency is False






def test_effectful_start_fence_atomically_records_no_replay_evidence(store, config):
    key = seed_structured_parent(store)
    route = config.routes[0]
    claimed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind.value,
        route.credential_mode.value,
        route.model,
        owner="effect-owner",
        lease_seconds=30,
        now=NOW,
    )

    running = store.mark_agent_runtime_attempt_running_once(
        claimed.id,
        owner="effect-owner",
        lease_seconds=30,
        effectful=True,
        now=NOW,
    )

    assert running.status == "running"
    assert running.first_effect_started_at == "2026-08-20 10:00:00"


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
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )
    assert called is False






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
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert callback_observed_persistence is True
    assert attempt.status == "failed"
    assert attempt.session_id == "early-session"
    assert attempt.transcript_reference == "codex_session:early-session"
    assert attempt.transcript_end >= 1


def test_conflicting_streamed_session_id_aborts_without_mixing_evidence(store, config):
    key = seed_structured_parent(store)

    def executor(command, **kwargs):
        kwargs["on_stdout_line"](
            json.dumps({"type": "thread.started", "thread_id": "session-one"})
        )
        kwargs["on_stdout_line"](
            json.dumps({"type": "thread.started", "thread_id": "session-two"})
        )
        return ProcessRunResult(0, "{}", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_session_conflict"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert attempt.status == "failed"
    assert attempt.failure_code == "runtime_session_conflict"
    assert attempt.session_id == "session-one"
    assert attempt.transcript_reference == "codex_session:session-one"


def test_conflicting_buffered_session_id_cannot_replace_streamed_session(store, config):
    key = seed_structured_parent(store)

    def executor(command, **kwargs):
        kwargs["on_stdout_line"](
            json.dumps({"type": "thread.started", "thread_id": "session-one"})
        )
        return ProcessRunResult(
            0,
            json.dumps({"type": "thread.started", "thread_id": "session-two"}),
            "",
        )

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_session_conflict"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    attempt = store.list_runtime_operation_attempts("structured", key)[0]
    assert attempt.status == "failed"
    assert attempt.failure_code == "runtime_session_conflict"
    assert attempt.session_id == "session-one"






def test_no_eligible_route_or_terminal_parent_never_starts_process(store, config):
    key = seed_structured_parent(store)
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config, snapshots={}),
        adapter=FakeAdapter(),
        executor=lambda *args, **kwargs: pytest.fail("process must not start"),
    )
    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
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
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )


def test_completed_result_is_recovered_by_matching_codec_without_child(store, config):
    key = seed_structured_parent(store)
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps(
                        {"type": "thread.started", "thread_id": "result-session"}
                    ),
                    '{"value":42}',
                ]
            ),
            "",
        )

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_line_counter=lambda _session: 2,
    )
    arguments = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "read",
        "command_factory": CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        "parser": lambda raw: json.loads(raw.splitlines()[-1])["value"],
        "result_codec": INT_CODEC,
        "conversation_id": "cid-12",
        "required_capabilities": CAPABILITIES,
    }

    first = routed.execute(**arguments)
    # Simulate the caller crashing after durable completion but before using value.
    second = routed.execute(**arguments)

    assert first.value == second.value == 42
    assert first.attempt_id == second.attempt_id
    assert calls == 1
    attempt = store.get_agent_runtime_attempt(first.attempt_id)
    assert attempt is not None
    assert attempt.result_schema_id == "test.integer.v1"
    assert "read" not in attempt.result_envelope_json
    assert (
        store.get_conversation_runtime_session("cid-12", "codex_oauth")
        == "result-session"
    )
    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_result_schema_mismatch"
    ):
        routed.execute(
            **{
                **arguments,
                "result_codec": RoutedResultCodec.integer(schema_id="test.integer.v2"),
            }
        )
    assert calls == 1


def test_completed_effectful_result_is_recovered_without_replay(store, config):
    key = seed_structured_parent(store)
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(0, "42", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
    )
    arguments = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "write once",
        "command_factory": CodexCommandFactory.standard(
            developer_instructions="reviewed write"
        ),
        "parser": lambda raw: int(raw),
        "result_codec": INT_CODEC,
        "required_capabilities": CAPABILITIES,
    }

    assert routed.execute(**arguments).value == 42
    assert routed.execute(**arguments).value == 42
    assert calls == 1


def test_agent_run_parent_routes_and_recovers_completed_result(store, config):
    run_id = seed_agent_run_parent(store)
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps(
                        {"type": "thread.started", "thread_id": "agent-session"}
                    ),
                    '{"decision":"no_reply"}',
                ]
            ),
            "",
        )

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_line_counter=lambda _session: 2,
    )
    arguments = {
        "workload_kind": "agent_run",
        "workload_key": str(run_id),
        "prompt": "decide",
        "command_factory": CodexCommandFactory.standard(
            developer_instructions="read-only decision"
        ),
        "parser": lambda raw: raw.splitlines()[-1],
        "result_codec": TEXT_CODEC,
        "required_capabilities": CAPABILITIES,
    }

    first = routed.execute(**arguments)
    second = routed.execute(**arguments)

    assert first.value == second.value == '{"decision":"no_reply"}'
    assert first.attempt_id == second.attempt_id
    assert calls == 1
    attempts = store.list_agent_runtime_attempts(run_id)
    assert len(attempts) == 1
    assert attempts[0].status == "completed"


def test_oversize_result_terminalizes_before_durable_completion(store, config):
    key = seed_structured_parent(store)
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0, "界" * MAX_RUNTIME_RESULT_ENVELOPE_BYTES, ""
        ),
        session_id_parser=lambda _raw: "oversize-session",
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_result_invalid"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    [attempt] = store.list_runtime_operation_attempts("structured", key)
    assert attempt.status == "failed"
    assert attempt.failure_class == RuntimeFailureClass.RESULT.value
    assert attempt.failure_code == "runtime_result_persistence_failed"
    assert attempt.result_envelope_json == ""


def test_oversize_persisted_result_is_rejected_without_child(store, config):
    key = seed_structured_parent(store)
    calls = 0

    def executor(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(0, "ok", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_id_parser=lambda _raw: "persisted-oversize-session",
    )
    arguments = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "read",
        "command_factory": CodexCommandFactory.standard(
            developer_instructions="reviewed reads only"
        ),
        "parser": lambda raw: raw,
        "result_codec": TEXT_CODEC,
        "required_capabilities": CAPABILITIES,
    }
    result = routed.execute(**arguments)
    corrupt = json.dumps(
        {"schema_id": TEXT_CODEC.schema_id, "value": "界" * 30_000},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(corrupt.encode("utf-8")) > MAX_RUNTIME_RESULT_ENVELOPE_BYTES
    with store._connect() as db:
        db.execute(
            "update agent_runtime_attempts set result_envelope_json=? where id=?",
            (corrupt, result.attempt_id),
        )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_result_invalid"):
        routed.execute(**arguments)
    assert calls == 1


def test_live_silent_process_cannot_be_reclaimed_after_nominal_lease(store, config):
    key = seed_structured_parent(store)
    current = [NOW]
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        current[0] += timedelta(seconds=2)
        competing = RoutedCodexExecution(
            store=store,
            config=config,
            router=make_router(store, config),
            adapter=FakeAdapter(),
            executor=lambda *_args, **_kwargs: pytest.fail("must not reclaim"),
            owner="competing-owner",
            lease_seconds=1,
            total_timeout_seconds=30,
            now=lambda: current[0],
        )
        with pytest.raises(RoutedCodexExecutionError, match="runtime_attempt_active"):
            competing.execute(
                workload_kind="structured",
                workload_key=key,
                prompt="read",
                command_factory=CodexCommandFactory.standard(
                    developer_instructions="reviewed reads only"
                ),
                parser=lambda raw: int(raw),
                result_codec=INT_CODEC,
                required_capabilities=CAPABILITIES,
            )
        return ProcessRunResult(0, "42", "")

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=executor,
        session_id_parser=lambda _raw: "live-silent-session",
        owner="live-owner",
        lease_seconds=1,
        total_timeout_seconds=30,
        now=lambda: current[0],
    )

    assert (
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: int(raw),
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
        ).value
        == 42
    )
    assert calls == 1


def test_concurrent_executors_start_exactly_one_child(store, config):
    key = seed_structured_parent(store)
    child_started = threading.Event()
    release_child = threading.Event()
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        child_started.set()
        assert release_child.wait(timeout=5)
        return ProcessRunResult(0, "42", "")

    def execute(owner):
        return RoutedCodexExecution(
            store=store,
            config=config,
            router=make_router(store, config),
            adapter=FakeAdapter(),
            executor=executor,
            session_id_parser=lambda _raw: "concurrent-session",
            owner=owner,
        ).execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: int(raw),
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, "owner-one")
        assert child_started.wait(timeout=5)
        second = pool.submit(execute, "owner-two")
        with pytest.raises(RoutedCodexExecutionError, match="runtime_attempt_active"):
            second.result(timeout=5)
        release_child.set()
        assert first.result(timeout=5).value == 42
    assert calls == 1


def test_route_pause_opened_during_selection_is_rechecked_before_start(
    store, config, monkeypatch
):
    key = seed_structured_parent(store)
    original_claim = store.claim_runtime_operation_attempt

    def pause_then_claim(*args, **kwargs):
        store.open_runtime_route_pause(
            "codex_oauth", "late_pause", datetime.now(UTC) + timedelta(minutes=5)
        )
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_runtime_operation_attempt", pause_then_claim)
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda *_args, **_kwargs: pytest.fail("must not start"),
        now=lambda: NOW,
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=CodexCommandFactory.standard(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )
    assert store.list_runtime_operation_attempts("structured", key) == []
