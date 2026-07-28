import json
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.agent_result import AgentOutcome
from app.agent_runner import AGENT_RESULT_SCHEMA_PATH, DirectAgentRunner
from app.process_runner import ProcessRunResult
from app.store import AgentRunLeaseLostError, AutoReplyStore
from app.dws_client import DWS_AGENT_CODE_ENV


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


def _jsonl(*, session_id: str = "session-1") -> str:
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": session_id}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "read-1",
                        "type": "command_execution",
                        "effect": "read_only",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(
                            {
                                "outcome": "completed",
                                "summary": "修复已执行并验证。",
                                "error": {
                                    "code": "",
                                    "retryable": False,
                                    "authorization_required": False,
                                    "side_effect_state": "none",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        )
    )


class RecordingExecutor:
    def __init__(self, output: str, *, returncode: int = 0, timed_out: bool = False):
        self.output = output
        self.returncode = returncode
        self.timed_out = timed_out
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []
        self.kwargs: list[dict] = []

    def __call__(self, command, *, prompt, **kwargs):
        self.commands.append(command)
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        callback = kwargs["on_stdout_line"]
        for line in self.output.splitlines():
            callback(line)
        return ProcessRunResult(
            returncode=self.returncode,
            stdout=self.output,
            stderr="process failed" if self.returncode else "",
            timed_out=self.timed_out,
            timeout_kind="total" if self.timed_out else "",
            timeout_reason="process timed out after 1200 seconds" if self.timed_out else "",
        )


@pytest.fixture
def store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "reply.sqlite3")


def test_direct_runner_uses_native_codex_and_never_ignores_user_config(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    executor = RecordingExecutor(_jsonl())
    runner = DirectAgentRunner(store=store, workspace=tmp_path, executor=executor)

    result = runner.run(task, _context(task.id))

    command = executor.commands[0]
    assert command[:2] == ["codex", "exec"]
    assert "resume" not in command
    assert "--ignore-user-config" not in command
    assert str(AGENT_RESULT_SCHEMA_PATH) in command
    assert result.result.outcome is AgentOutcome.COMPLETED
    assert executor.kwargs[0]["total_timeout_seconds"] == 1200
    assert executor.kwargs[0]["idle_timeout_seconds"] == 900
    assert DWS_AGENT_CODE_ENV not in executor.kwargs[0]["env"]


def test_direct_runner_preserves_local_cli_and_codex_environment(
    tmp_path: Path, store: AutoReplyStore, monkeypatch
):
    monkeypatch.setenv("LARK_CLI_AUTH_HOME", "/safe/lark-auth")
    monkeypatch.setenv("CODEX_LOGIN_MARKER", "native-codex-session")
    monkeypatch.setenv(DWS_AGENT_CODE_ENV, "legacy-agent-code")
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task, _context(task.id)
    )

    env = executor.kwargs[0]["env"]
    assert env["LARK_CLI_AUTH_HOME"] == "/safe/lark-auth"
    assert env["CODEX_LOGIN_MARKER"] == "native-codex-session"
    assert DWS_AGENT_CODE_ENV not in env


def test_direct_runner_never_uses_custom_model_provider(
    tmp_path: Path, store: AutoReplyStore, monkeypatch
):
    monkeypatch.setenv("CEO_CODEX_MODEL", "codex-MiniMax-M2.7")
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "minimax")
    executor = RecordingExecutor(_jsonl())
    task = _task(store)

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task, _context(task.id)
    )

    command_text = " ".join(executor.commands[0])
    assert "m27" not in command_text.casefold()
    assert "minimax" not in command_text.casefold()
    assert "model_provider" not in command_text


def test_direct_runner_resumes_only_the_claimed_run_session(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        owner="seed",
        lease_seconds=1,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        claim.run.id,
        "existing-session",
        owner="seed",
        now="2026-07-29 00:00:00",
    )
    executor = RecordingExecutor(_jsonl(session_id="existing-session"))
    runner = DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=executor,
        owner="worker-2",
    )

    runner.run(task, _context(task.id), now="2026-07-29 00:00:02")

    command = executor.commands[0]
    assert command[:3] == ["codex", "exec", "resume"]
    assert "existing-session" in command


def test_direct_runner_persists_each_jsonl_event_before_final_parse(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    observed_counts: list[int] = []

    def executor(command, *, prompt, on_stdout_line, **kwargs):
        lines = _jsonl().splitlines()
        for line in lines:
            on_stdout_line(line)
            run = store.get_agent_run_for_task_generation(
                task.id, task.execution_generation
            )
            observed_counts.append(len(run.tool_events))
        return ProcessRunResult(returncode=0, stdout="\n".join(lines), stderr="")

    result = DirectAgentRunner(
        store=store, workspace=tmp_path, executor=executor
    ).run(task, _context(task.id))

    persisted = store.get_agent_run(result.run_id)
    assert persisted.codex_session_id == "session-1"
    assert observed_counts == [1, 2, 3]
    assert len(result.events) == 3
    assert result.transcript_start_line == 0
    assert result.transcript_end_line == 3


def test_read_only_run_uses_never_policy_and_no_write_instruction(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task, _context(task.id), read_only=True
    )

    command_text = " ".join(executor.commands[0])
    assert 'approval_policy="never"' in command_text
    assert "--dangerously-bypass-approvals-and-sandbox" not in executor.commands[0]
    assert "read-only" in executor.prompts[0].casefold()
    assert "external write" in executor.prompts[0].casefold()


@pytest.mark.parametrize(
    ("executor", "error_code"),
    (
        (RecordingExecutor(_jsonl(), returncode=1), "codex_process_failed"),
        (RecordingExecutor(_jsonl(), timed_out=True), "codex_process_timeout"),
        (RecordingExecutor('{"type":"thread.started","thread_id":"s"}\n{'), "codex_stream_invalid"),
    ),
)
def test_runner_fails_closed_for_process_and_stream_errors(
    tmp_path: Path, store: AutoReplyStore, executor: RecordingExecutor, error_code: str
):
    task = _task(store)

    with pytest.raises(RuntimeError, match=error_code):
        DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
            task, _context(task.id)
        )

    persisted = store.get_agent_run_for_task_generation(
        task.id, task.execution_generation
    )
    assert persisted.status == "failed"
    assert error_code in persisted.structured_error_json
    assert "process failed" not in persisted.structured_error_json


def test_runner_stops_when_lease_is_lost_during_event_persistence(
    tmp_path: Path, store: AutoReplyStore, monkeypatch
):
    task = _task(store)
    original = store.append_agent_run_event

    def lose_lease(*args, **kwargs):
        raise AgentRunLeaseLostError("lost")

    monkeypatch.setattr(store, "append_agent_run_event", lose_lease)

    with pytest.raises(AgentRunLeaseLostError):
        DirectAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=RecordingExecutor(_jsonl()),
        ).run(task, _context(task.id))

    monkeypatch.setattr(store, "append_agent_run_event", original)


def test_corrupt_stream_after_effect_start_marks_run_unknown(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    output = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-1"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "id": "write-1",
                        "type": "command_execution",
                        "effect": "effectful",
                    },
                }
            ),
            "{",
        )
    )

    with pytest.raises(RuntimeError, match="codex_stream_invalid"):
        DirectAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=RecordingExecutor(output),
        ).run(task, _context(task.id))

    persisted = store.get_agent_run_for_task_generation(
        task.id, task.execution_generation
    )
    assert persisted.status == "unknown"
    assert persisted.side_effect_state == "unknown"


def test_persisted_events_redact_secret_values(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    secret = "super-secret-token"
    output = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": f"tool --token {secret} --api-key={secret}",
                        "output": f"Authorization: Bearer {secret}",
                    },
                }
            ),
            _jsonl().splitlines()[-1],
        )
    )

    DirectAgentRunner(
        store=store,
        workspace=tmp_path,
        executor=RecordingExecutor(output),
    ).run(task, _context(task.id))

    persisted = store.get_agent_run_for_task_generation(
        task.id, task.execution_generation
    )
    assert secret not in json.dumps(persisted.tool_events)


def test_agent_prompt_never_instructs_auth_commands(
    tmp_path: Path, store: AutoReplyStore
):
    task = _task(store)
    executor = RecordingExecutor(_jsonl())

    DirectAgentRunner(store=store, workspace=tmp_path, executor=executor).run(
        task, _context(task.id)
    )

    prompt = executor.prompts[0]
    assert "Never run authentication login, reset, or logout commands" in prompt
    assert "run dws auth login" not in prompt.casefold()
