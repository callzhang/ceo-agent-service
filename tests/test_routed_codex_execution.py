from __future__ import annotations

import json
import os
import sys
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
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import ProcessRunResult
from app.store import MAX_RUNTIME_RESULT_ENVELOPE_BYTES, AutoReplyStore

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
CAPABILITIES = frozenset({"structured_output", "reviewed_read_tools"})
INT_CODEC = RoutedResultCodec.integer(schema_id="test.integer.v1")
TEXT_CODEC = RoutedResultCodec.text(schema_id="test.text.v1")


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


def test_read_only_factory_forces_sandbox_and_is_immutable(
    config, tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_SANDBOX", "danger-full-access")
    adapter = CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")
    factory = ApprovedCodexCommandFactory.read_only(
        developer_instructions="reviewed reads only"
    )

    command, _env = factory.build(
        adapter=adapter,
        route=config.routes[0],
        prompt="read",
        session_id=None,
    )

    assert ["--sandbox", "read-only"] == command[2:4]
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    with pytest.raises((AttributeError, TypeError)):
        factory._developer_instructions = "allow writes"
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
        session_effect_probe=lambda *_args: False,
    )

    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze",
        command_factory=ApprovedCodexCommandFactory.read_only(
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
                        {"type": "thread.started", "thread_id": f"session-{len(prompts)}"}
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
        session_effect_probe=lambda *_args: False,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze all KRs",
        command_factory=ApprovedCodexCommandFactory.read_only(
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
        ("codex_oauth", None, "never", False),
        ("codex_oauth", None, "never", False),
    ]
    assert prompts[0] == "analyze all KRs"
    assert "Return every KR" in prompts[1]
    assert "expected complete KR coverage" in prompts[1]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert [attempt.route_name for attempt in attempts] == ["codex_oauth", "codex_oauth"]
    assert attempts[0].failure_code == "runtime_result_validation_failed"
    assert [attempt.session_mode for attempt in attempts] == ["fresh", "fresh"]


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
        owner=owner,
        now=lambda: NOW,
    )
    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="analyze all KRs",
        command_factory=ApprovedCodexCommandFactory.read_only(
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
    assert adapter.commands == [("codex_oauth", None, "never", False)]
    assert len(prompts) == 1
    assert "Return every KR" in prompts[0]
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_oauth",
    ]
    assert attempts[0].failure_code == "runtime_result_validation_failed"


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
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_result_validation_failed"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=ApprovedCodexCommandFactory.read_only(
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
    with pytest.raises(RoutedCodexExecutionError, match="runtime_route_unavailable"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=ApprovedCodexCommandFactory.read_only(
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


def test_process_failure_after_result_validation_retry_does_not_fail_over(
    store, config
):
    key = seed_structured_parent(store, 73)
    adapter = FakeAdapter()
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ProcessRunResult(0, json.dumps({"value": 0}), "")
        return ProcessRunResult(1, "", "login failed")

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
            prompt="analyze",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda _raw: (_ for _ in ()).throw(
                RoutedResultValidationError("incomplete")
            ),
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
            result_validation_retry=RoutedResultValidationRetry.exactly_once(
                correction_instructions="Return the complete result."
            ),
        )

    assert adapter.commands == [
        ("codex_oauth", None, "never", False),
        ("codex_oauth", None, "never", False),
    ]


def test_result_validation_retry_stops_when_session_effect_is_not_proven_absent(
    store, config
):
    key = seed_structured_parent(store, 74)
    calls = 0

    def executor(command, **kwargs):
        nonlocal calls
        calls += 1
        return ProcessRunResult(
            0,
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "session-74"}),
                    json.dumps({"value": 0}),
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
        session_line_counter=lambda _session_id: 2,
        session_effect_probe=lambda *_args: None,
    )

    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effect_policy_violation"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="analyze",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda _raw: (_ for _ in ()).throw(
                RoutedResultValidationError("incomplete")
            ),
            result_codec=INT_CODEC,
            required_capabilities=CAPABILITIES,
            result_validation_retry=RoutedResultValidationRetry.exactly_once(
                correction_instructions="Return the complete result."
            ),
        )

    assert calls == 1
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert attempts[0].failure_code == "runtime_effect_policy_violation"
    assert attempts[0].first_effect_started_at


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
            result_codec=TEXT_CODEC,
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
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )
    assert len(calls) == 1


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
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
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
            result_codec=TEXT_CODEC,
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
            result_codec=TEXT_CODEC,
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
            command_factory=ApprovedCodexCommandFactory.read_only(
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
            command_factory=ApprovedCodexCommandFactory.read_only(
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
        "result_codec": TEXT_CODEC,
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
            result_codec=TEXT_CODEC,
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
            result_codec=TEXT_CODEC,
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
            command_factory=ApprovedCodexCommandFactory.read_only(
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
        session_effect_probe=lambda *_args: False,
    )
    arguments = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "read",
        "command_factory": ApprovedCodexCommandFactory.read_only(
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
        "command_factory": ApprovedCodexCommandFactory.effectful(
            developer_instructions="reviewed write"
        ),
        "parser": lambda raw: int(raw),
        "result_codec": INT_CODEC,
        "required_capabilities": CAPABILITIES,
    }

    assert routed.execute(**arguments).value == 42
    assert routed.execute(**arguments).value == 42
    assert calls == 1


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
    )

    with pytest.raises(RoutedCodexExecutionError, match="runtime_result_invalid"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
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
    )
    arguments = {
        "workload_kind": "structured",
        "workload_key": key,
        "prompt": "read",
        "command_factory": ApprovedCodexCommandFactory.read_only(
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
                command_factory=ApprovedCodexCommandFactory.read_only(
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
            command_factory=ApprovedCodexCommandFactory.read_only(
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
            owner=owner,
        ).execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
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

    with pytest.raises(RoutedCodexExecutionError, match="runtime_route_unavailable"):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="read",
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions="reviewed reads only"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )
    assert store.list_runtime_operation_attempts("structured", key) == []


def test_expired_read_only_crash_is_terminalized_then_routes_once(store, config):
    key = seed_structured_parent(store)
    route = config.routes[0]
    crashed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind.value,
        route.credential_mode.value,
        route.model,
        owner="dead-owner",
        lease_seconds=5,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(
        crashed.id, owner="dead-owner", lease_seconds=5, now=NOW
    )
    later = NOW + timedelta(seconds=6)
    calls = []

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda command, **kwargs: (
            calls.append(command) or ProcessRunResult(0, "42", "")
        ),
        owner="replacement-owner",
        now=lambda: later,
    )

    result = routed.execute(
        workload_kind="structured",
        workload_key=key,
        prompt="read",
        command_factory=ApprovedCodexCommandFactory.read_only(
            developer_instructions="reviewed reads only"
        ),
        parser=lambda raw: int(raw),
        result_codec=INT_CODEC,
        required_capabilities=CAPABILITIES,
    )

    assert result.route_name == "codex_api"
    attempts = store.list_runtime_operation_attempts("structured", key)
    assert [item.status for item in attempts] == ["superseded", "completed"]
    assert attempts[0].failure_code == "runtime_lease_expired"
    assert len(calls) == 1


def test_expired_effect_fence_is_never_reclaimed(store, config):
    key = seed_structured_parent(store)
    route = config.routes[0]
    crashed = store.claim_runtime_operation_attempt(
        "structured",
        key,
        route.name,
        route.runtime_kind.value,
        route.credential_mode.value,
        route.model,
        owner="dead-owner",
        lease_seconds=5,
        now=NOW,
    )
    store.mark_agent_runtime_attempt_running_once(
        crashed.id, owner="dead-owner", lease_seconds=5, now=NOW
    )
    store.note_runtime_attempt_effect_started(crashed.id, owner="dead-owner", at=NOW)

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=make_router(store, config),
        adapter=FakeAdapter(),
        executor=lambda *_args, **_kwargs: pytest.fail("must not replay"),
        owner="replacement-owner",
        now=lambda: NOW + timedelta(seconds=6),
    )
    with pytest.raises(
        RoutedCodexExecutionError, match="runtime_effectful_replay_blocked"
    ):
        routed.execute(
            workload_kind="structured",
            workload_key=key,
            prompt="write",
            command_factory=ApprovedCodexCommandFactory.effectful(
                developer_instructions="reviewed write"
            ),
            parser=lambda raw: raw,
            result_codec=TEXT_CODEC,
            required_capabilities=CAPABILITIES,
        )
