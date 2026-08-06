import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.agent_result import AgentOutcome, EffectKind
from app.agent_runner import (
    AGENT_RESULT_SCHEMA_PATH,
    AgentConversationLockedError,
    AgentRunUnavailableError,
    DirectAgentRunner,
    McpToolEffectRegistry,
    direct_agent_developer_instructions,
)
from app.process_runner import ProcessRunResult
from app.service_codex_config import (
    ServiceMcpConfigError,
    ServiceMcpConfigIssue,
)
from app.store import AgentRole, AutoReplyStore


def test_mcp_effect_registry_qualifies_operation_with_server():
    registry = McpToolEffectRegistry(
        {
            ("server_a", "write"): EffectKind.EFFECTFUL,
            ("server_b", "write"): EffectKind.EFFECTFUL,
        }
    )

    first = registry.classify(
        {
            "type": "mcp_tool_call",
            "server": "server_a",
            "tool": "write",
            "arguments": {"value": "same"},
        }
    )
    second = registry.classify(
        {
            "type": "mcp_tool_call",
            "server": "server_b",
            "tool": "write",
            "arguments": {"value": "same"},
        }
    )

    assert first is not None and second is not None
    assert first.server == "server_a"
    assert second.server == "server_b"
    assert first.operation == second.operation == "write"


def _task(store: AutoReplyStore):
    store.enqueue_reply_task(
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid",
        trigger_create_time="2026-07-28 12:00:00",
        trigger_sender="ET",
        trigger_text="修复并验证服务",
        execution_generation="generation-1",
    )
    return store.list_reply_tasks(statuses=("pending",), limit=1)[0]


def _get_direct_run(
    store: AutoReplyStore,
    task_id: int,
    execution_generation: str,
):
    return store.get_agent_run_for_turn(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


def _context(task_id: int) -> AgentTaskContext:
    return AgentTaskContext(
        task_id=task_id,
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid",
        trigger_sender="ET",
        trigger_text="修复并验证服务",
        trigger_create_time="2026-07-28 12:00:00",
        messages=(),
        materials=(),
        prior_receipts=(),
    )


def _result_line(
    *,
    outcome: str = "completed",
    summary: str = "修复已执行并验证。",
    code: str = "",
    retryable: bool = False,
) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "outcome": outcome,
                        "summary": summary,
                        "error": {
                            "code": code,
                            "retryable": retryable,
                            "authorization_required": False,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        },
        ensure_ascii=False,
    )


def _jsonl(*, session_id: str = "session-1", outcome: str = "completed") -> str:
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": session_id}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "read-1",
                        "type": "web_search_call",
                        "query": "current service status",
                    },
                }
            ),
            _result_line(
                outcome=outcome,
                summary="材料暂时不可用。" if outcome == "failed" else "修复已执行并验证。",
                code="material_unavailable" if outcome == "failed" else "",
                retryable=outcome == "failed",
            ),
        )
    )


class RecordingExecutor:
    def __init__(
        self,
        output: str,
        *,
        returncode: int = 0,
        timed_out: bool = False,
    ) -> None:
        self.output = output
        self.returncode = returncode
        self.timed_out = timed_out
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, command, *, prompt, on_stdout_line, **kwargs):
        self.commands.append(command)
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        for line in self.output.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(
            returncode=self.returncode,
            stdout=self.output,
            stderr="process failed" if self.returncode else "",
            timed_out=self.timed_out,
            timeout_kind="total" if self.timed_out else "",
            timeout_reason="process timed out" if self.timed_out else "",
        )


@pytest.fixture
def store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "reply.sqlite3")


def test_direct_runner_never_disables_or_imports_personal_mcp_servers(
    tmp_path: Path,
    store: AutoReplyStore,
    monkeypatch,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.brightdata]",
                'url = "https://personal.example/brightdata"',
                "[mcp_servers.crm_connector]",
                'url = "https://personal.example/crm"',
                "[mcp_servers.fundflow]",
                'url = "https://personal.example/fundflow"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    result = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
    ).run(task, _context(task.id))

    command = executor.commands[0]
    command_text = " ".join(command)
    assert command[:2] == ["codex", "exec"]
    assert "resume" not in command
    assert "--ignore-user-config" in command
    assert "enabled_tools=" not in command_text
    assert not any("mcp_servers.brightdata" in item for item in command)
    assert not any("mcp_servers.crm_connector" in item for item in command)
    assert not any("mcp_servers.fundflow" in item for item in command)
    assert not any("personal.example" in item for item in command)
    assert "mcp_servers.brightdata.enabled=false" not in command
    assert "mcp_servers.crm_connector.enabled=false" not in command
    assert "mcp_servers.fundflow.enabled=false" not in command
    assert "features.plugins=false" not in command_text
    assert "features.apps=false" not in command_text
    assert "reconciliation_cli" not in command_text
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--output-schema" not in command
    assert str(AGENT_RESULT_SCHEMA_PATH) not in command
    assert result.result.outcome is AgentOutcome.COMPLETED
    assert result.events == ()
    assert result.receipts == ()


def test_direct_agent_requires_oa_applicant_notification_after_confirmed_action():
    instructions = direct_agent_developer_instructions()

    assert "outcome, summary, and error" in instructions
    assert "completed, no_action, needs_human, or failed" in instructions
    assert "error is always an object" in instructions
    assert "oa_action_receipt.result" in instructions
    assert "notify that applicant through DingTalk before returning AgentResult" in instructions
    assert "real originator identifier" in instructions
    assert "does not approve, reject, or return the approval" in instructions


def test_direct_runner_reuses_one_codex_session_for_the_conversation(
    tmp_path: Path,
    store: AutoReplyStore,
):
    store.upsert_conversation("cid", "产品群", False, "conversation-session")
    task = _task(store)
    executor = RecordingExecutor(_jsonl(session_id="conversation-session"))
    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
        codex_session_exists=lambda _session_id: True,
    ).run(task, _context(task.id))

    assert executor.commands[0][1:3] == ["exec", "resume"]
    assert executor.commands[0][-2:] == ["conversation-session", "-"]
    run = _get_direct_run(store,
        task.id,
        task.execution_generation,
    )
    assert run.codex_session_id == "conversation-session"


def test_direct_runner_serializes_same_conversation_before_claiming_second_run(
    tmp_path: Path,
    store: AutoReplyStore,
):
    first = _task(store)
    store.enqueue_reply_task(
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid-2",
        trigger_create_time="2026-07-28 12:01:00",
        trigger_sender="ET",
        trigger_text="继续处理",
        execution_generation="generation-2",
    )
    second = store.get_reply_task_for_message("cid", "mid-2")
    assert second is not None
    second_context = replace(
        _context(second.id),
        trigger_message_id="mid-2",
        trigger_text="继续处理",
        trigger_create_time="2026-07-28 12:01:00",
    )

    second_runner = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(_jsonl(session_id="second-session")),
    )

    class ReentrantExecutor(RecordingExecutor):
        def __call__(self, command, *, prompt, on_stdout_line, **kwargs):
            with pytest.raises(AgentConversationLockedError):
                second_runner.run(second, second_context)
            return super().__call__(
                command,
                prompt=prompt,
                on_stdout_line=on_stdout_line,
                **kwargs,
            )

    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=ReentrantExecutor(_jsonl(session_id="first-session")),
    ).run(first, _context(first.id))

    assert (
        _get_direct_run(store,
            second.id,
            second.execution_generation,
        )
        is None
    )
    assert store.acquire_codex_session_lock("cid", "post-run") is True


def test_direct_runner_persists_sanitized_process_failure_detail(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)
    executor = RecordingExecutor(_jsonl(), returncode=1)

    with pytest.raises(RuntimeError, match="codex_process_failed"):
        DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
            task,
            _context(task.id),
        )

    run = _get_direct_run(store,
        task.id,
        task.execution_generation,
    )
    assert run is not None
    assert json.loads(run.structured_error_json)["detail"] == "process failed"


def test_direct_runner_does_not_reinject_unrelated_user_mcp_servers(
    tmp_path: Path,
    store: AutoReplyStore,
    monkeypatch: pytest.MonkeyPatch,
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "[mcp_servers.unrelated_plugin]\n"
        'url = "https://example.invalid/mcp"\n'
        "enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task,
        _context(task.id),
    )

    command = executor.commands[0]
    assert "mcp_servers.unrelated_plugin.enabled=false" not in command


def test_direct_runner_persists_new_session_for_later_conversation_messages(
    tmp_path: Path,
    store: AutoReplyStore,
):
    first = _task(store)
    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(_jsonl(session_id="conversation-session")),
    ).run(first, _context(first.id))

    store.enqueue_reply_task(
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid-2",
        trigger_create_time="2026-07-28 12:01:00",
        trigger_sender="ET",
        trigger_text="继续处理",
        execution_generation="generation-2",
    )
    second = store.get_reply_task_for_message("cid", "mid-2")
    second_context = replace(
        _context(second.id),
        trigger_message_id="mid-2",
        trigger_text="继续处理",
        trigger_create_time="2026-07-28 12:01:00",
    )
    executor = RecordingExecutor(_jsonl(session_id="conversation-session"))

    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
        codex_session_exists=lambda _session_id: True,
    ).run(second, second_context)

    assert store.get_codex_session_id("cid") == "conversation-session"
    assert executor.commands[0][1:3] == ["exec", "resume"]
    assert executor.commands[0][-2:] == ["conversation-session", "-"]


def test_direct_runner_starts_fresh_when_conversation_session_is_missing(
    tmp_path: Path,
    store: AutoReplyStore,
):
    store.upsert_conversation("cid", "产品群", False, "missing-session")
    task = _task(store)
    executor = RecordingExecutor(_jsonl(session_id="replacement-session"))

    result = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
        codex_session_exists=lambda _session_id: False,
    ).run(task, _context(task.id))

    assert executor.commands[0][1] == "exec"
    assert "resume" not in executor.commands[0]
    assert store.get_codex_session_id("cid") == "replacement-session"
    assert store.get_agent_run(result.run_id).codex_session_id == "replacement-session"


def test_direct_runner_only_persists_codex_session_pointer_not_tool_event_copy(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)

    result = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(_jsonl(session_id="native-audit-session")),
    ).run(task, _context(task.id))

    run = store.get_agent_run(result.run_id)
    assert run.codex_session_id == "native-audit-session"
    assert run.tool_events == []
    assert result.transcript_end_line >= result.transcript_start_line + 3


def test_direct_runner_renews_lease_when_stream_reports_progress(
    tmp_path: Path,
    store: AutoReplyStore,
    monkeypatch: pytest.MonkeyPatch,
):
    claimed_at = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    progress_at = datetime(2026, 7, 29, 0, 35, tzinfo=timezone.utc)
    times = iter((claimed_at, *([progress_at] * 12)))

    def controlled_store_time(_now=None):
        value = next(times, progress_at)
        return value, value.strftime("%Y-%m-%d %H:%M:%S")

    monkeypatch.setattr("app.store._utc_store_time", controlled_store_time)
    task = _task(store)
    output = _jsonl(session_id="progress-session")
    observed_leases: list[str] = []

    def executor(command, *, prompt, on_stdout_line, **_kwargs):
        for line in output.splitlines():
            on_stdout_line(line)
        run = _get_direct_run(store,
            task.id,
            task.execution_generation,
        )
        assert run is not None
        observed_leases.append(run.lease_expires_at)
        return ProcessRunResult(returncode=0, stdout=output, stderr="")

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task,
        _context(task.id),
    )

    assert observed_leases == ["2026-07-29 01:15:00"]


def test_read_only_run_uses_native_tools_with_never_approval_policy(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
    ).run(task, _context(task.id), read_only=True)

    command_text = " ".join(executor.commands[0])
    assert 'approval_policy="never"' in command_text
    assert "--dangerously-bypass-approvals-and-sandbox" not in executor.commands[0]
    assert "reconciliation_cli" not in command_text
    assert "enabled_tools=" not in command_text
    assert "Read-only invocation" in executor.prompts[0]


def test_structured_failed_result_is_returned_and_persisted(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)

    result = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(_jsonl(outcome="failed")),
    ).run(task, _context(task.id))

    assert result.result.outcome is AgentOutcome.FAILED
    run = store.get_agent_run(result.run_id)
    assert run.status == "failed"
    assert "material_unavailable" in run.structured_error_json


@pytest.mark.parametrize(
    ("executor", "error_code"),
    [
        (RecordingExecutor(_jsonl(), returncode=1), "codex_process_failed"),
        (RecordingExecutor(_jsonl(), timed_out=True), "codex_process_timeout"),
        (RecordingExecutor("not-json"), "codex_result_invalid"),
    ],
)
def test_runtime_failures_are_persisted_as_regular_failures(
    tmp_path: Path,
    store: AutoReplyStore,
    executor: RecordingExecutor,
    error_code: str,
):
    task = _task(store)

    with pytest.raises(RuntimeError, match=error_code):
        DirectAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
        ).run(task, _context(task.id))

    run = _get_direct_run(store,
        task.id,
        task.execution_generation,
    )
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    assert error_code in run.structured_error_json


def test_service_mcp_config_failure_does_not_leave_claimed_run_running(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)
    runner = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(_jsonl()),
    )

    def fail_build_command(**kwargs):
        del kwargs
        raise ServiceMcpConfigError(
            path=tmp_path / "service-mcp.json",
            issues=(
                ServiceMcpConfigIssue(
                    server_name="xiaoqing_interview",
                    field="command",
                    reason="service transport command is not configured",
                ),
            ),
        )

    runner.codex.build_command = fail_build_command

    with pytest.raises(RuntimeError, match="service_mcp_config_invalid"):
        runner.run(task, _context(task.id))

    run = _get_direct_run(store,
        task.id,
        task.execution_generation,
    )
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    assert json.loads(run.structured_error_json) == {
        "code": "service_mcp_config_invalid",
        "retryable": True,
    }
