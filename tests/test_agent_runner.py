import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.agent_result import AgentOutcome
from app.agent_runner import (
    AGENT_RESULT_SCHEMA_PATH,
    AgentRunUnavailableError,
    DirectAgentRunner,
)
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore


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


def test_direct_runner_uses_native_codex_config_without_mcp_whitelist(
    tmp_path: Path,
    store: AutoReplyStore,
):
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
    assert "--ignore-user-config" not in command
    assert "enabled_tools=" not in command_text
    assert ".enabled=false" not in command_text
    assert "features.plugins=false" not in command_text
    assert "features.apps=false" not in command_text
    assert "reconciliation_cli" not in command_text
    assert str(AGENT_RESULT_SCHEMA_PATH) in command
    assert result.result.outcome is AgentOutcome.COMPLETED
    assert result.events == ()
    assert result.receipts == ()


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
    ).run(task, _context(task.id))

    assert executor.commands[0][1:3] == ["exec", "resume"]
    assert executor.commands[0][-2:] == ["conversation-session", "-"]
    run = store.get_agent_run_for_task_generation(
        task.id,
        task.execution_generation,
    )
    assert run.codex_session_id == "conversation-session"


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
    ).run(second, second_context)

    assert store.get_codex_session_id("cid") == "conversation-session"
    assert executor.commands[0][1:3] == ["exec", "resume"]
    assert executor.commands[0][-2:] == ["conversation-session", "-"]


def test_direct_runner_serializes_runs_for_the_same_conversation(
    tmp_path: Path,
    store: AutoReplyStore,
):
    task = _task(store)
    assert store.acquire_codex_session_lock("cid", "other-worker")

    with pytest.raises(AgentRunUnavailableError, match="codex session locked"):
        DirectAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=RecordingExecutor(_jsonl()),
            owner="current-worker",
        ).run(task, _context(task.id))


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

    run = store.get_agent_run_for_task_generation(
        task.id,
        task.execution_generation,
    )
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    assert error_code in run.structured_error_json
