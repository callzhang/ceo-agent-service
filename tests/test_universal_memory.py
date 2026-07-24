import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from app.codex_memory_write import (
    CodexMemoryWriteAuthorizationRequired,
    MemoryWriteResult,
    run_codex_memory_write,
)
from app.store import AutoReplyStore
from app.universal_consumer import UniversalConsumerOrchestrator
from app.universal_context import UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecutionState,
    UniversalActionExecutor,
    build_universal_action_execution,
)
from app.universal_plan import PlannedAction, UniversalAudit, UniversalPlan
from app.universal_validator import DependencyStatus
from app.worker import DingTalkAutoReplyWorker


class RecordingPlanner:
    def __init__(self, plan: UniversalPlan) -> None:
        self.plan_result = plan
        self.calls = 0

    def plan(self, context, session_id=None):
        self.calls += 1
        return self.plan_result


class FakeMemoryWriteRunner:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class BlockingMemoryWriteRunner(FakeMemoryWriteRunner):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.set()
        assert self.release.wait(timeout=5)
        return MemoryWriteResult("episode-1", "queued", False)


class ReadyDws:
    def auth_status(self):
        return {
            "authenticated": True,
            "token_valid": True,
            "refresh_token_valid": True,
        }


def memory_plan() -> UniversalPlan:
    return UniversalPlan(
        task_kind="memory",
        reason="Persist the durable decision",
        dependencies=["memory"],
        actions=[
            PlannedAction(
                kind="memory_write",
                reason="Persist the durable decision",
                payload={"data": "The launch decision is approved.", "type": "text"},
            )
        ],
        audit=UniversalAudit(summary="Persist durable state", confidence=0.9),
    )


def build_execution(tmp_path, *, memory_write_runner: FakeMemoryWriteRunner):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-memory",
        conversation_title="Strategy",
        single_chat=False,
        trigger_message_id="msg-memory",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Remember the launch decision",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        context_messages=(),
        required_dependencies=("dws", "memory"),
        force_new_decision=False,
        dry_run=False,
        trigger_create_time=task.trigger_create_time,
    )
    plan_execution = store.create_universal_plan_execution(context, memory_plan())
    execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[0], 0
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=object(),
        codex=object(),
        memory_write_runner=memory_write_runner,
    )
    return store, context, execution, worker


def test_memory_dependency_status_uses_inherited_codex_mcp_without_client_preflight(
    tmp_path: Path,
) -> None:
    context = UniversalTaskContext(
        task_id=1,
        conversation_id="cid",
        conversation_title="Strategy",
        single_chat=False,
        trigger_message_id="msg",
        trigger_sender="Derek",
        trigger_text="Handle it",
        context_messages=(),
        required_dependencies=("memory",),
        force_new_decision=False,
        dry_run=False,
        trigger_create_time="2026-07-20 10:00:00",
    )
    planner = RecordingPlanner(memory_plan())
    runner = FakeMemoryWriteRunner()
    worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=object(),
        codex=object(),
        memory_write_runner=runner,
    )
    del planner

    statuses = worker.universal_dependency_status(context, ("memory",))

    assert statuses == {"memory": DependencyStatus(ready=True)}
    assert runner.calls == []


def test_codex_mcp_memory_write_runner_inherits_codex_mcp_for_memory_write(
    tmp_path: Path,
):
    output = {
        "structured_content": {
            "result": json.dumps(
                {
                    "ok": True,
                    "episode_uuid": "episode-1",
                    "processing_status": "completed",
                }
            )
        }
    }
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "tool": "memory_write",
                        "arguments": {
                            "data": "Durable decision",
                            "type": "text",
                            "created_at": "2026-07-20T10:00:00+08:00",
                        },
                        "result": output,
                    },
                }
            ),
            json.dumps({"status": "attempted"}),
        ]
    )

    captured = {}

    def executor(command, prompt):
        captured["command"] = command
        captured["prompt"] = prompt
        return raw

    result = run_codex_memory_write(
        workspace=tmp_path,
        data="Durable decision",
        type="text",
        created_at="2026-07-20T10:00:00+08:00",
        source_description="source",
        executor=executor,
    )

    assert result == MemoryWriteResult("episode-1", "completed", False)
    assert "--ignore-user-config" not in captured["command"]
    disabled_features = [
        captured["command"][index + 1]
        for index, value in enumerate(captured["command"][:-1])
        if value == "--disable"
    ]
    assert disabled_features == ["hooks"]
    developer_options = [
        captured["command"][index + 1]
        for index, value in enumerate(captured["command"][:-1])
        if value == "-c"
        and captured["command"][index + 1].startswith("developer_instructions=")
    ]
    assert len(developer_options) == 1
    assert "service-owned Memory write" in developer_options[0]
    assert 'mcp_servers.memory_connector.enabled_tools=["memory_write"]' in captured[
        "command"
    ]


def test_codex_mcp_memory_write_runner_write_surfaces_codex_authorization_errors(
    tmp_path: Path,
):
    with pytest.raises(
        Exception,
        match="expected one memory_write tool call",
    ):
        run_codex_memory_write(
            workspace=tmp_path,
            data="Durable decision",
            type="text",
            created_at="2026-07-20T10:00:00+08:00",
            source_description="source",
            executor=lambda _command, _prompt: "\n".join(
                [
                    json.dumps({"type": "error", "message": "authorization required"}),
                    json.dumps({"status": "attempted"}),
                ]
            ),
        )


def test_dependency_status_checks_dws_and_memory_before_planner(tmp_path) -> None:
    runner = FakeMemoryWriteRunner()
    worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=ReadyDws(),
        codex=object(),
        memory_write_runner=runner,
    )
    context = UniversalTaskContext(
        task_id=1,
        conversation_id="cid",
        conversation_title="Strategy",
        single_chat=False,
        trigger_message_id="msg",
        trigger_sender="Derek",
        trigger_text="Handle it",
        context_messages=(),
        required_dependencies=("dws", "memory"),
        force_new_decision=False,
        dry_run=False,
        trigger_create_time="2026-07-20 10:00:00",
    )

    statuses = worker.universal_dependency_status(
        context,
        context.required_dependencies,
    )

    assert statuses == {
        "dws": DependencyStatus(ready=True),
        "memory": DependencyStatus(ready=True),
    }


def test_memory_executor_uses_trigger_time_and_stable_source_description(tmp_path) -> None:
    runner = FakeMemoryWriteRunner(
        [MemoryWriteResult("episode-1", "queued", False)]
    )
    store, context, execution, worker = build_execution(
        tmp_path, memory_write_runner=runner
    )

    assert worker.execute_universal_memory_write(execution) is True

    expected_hash = hashlib.sha256(
        json.dumps(
            [
                context.conversation_id,
                context.trigger_message_id,
                "text",
                "The launch decision is approved.",
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert runner.calls == [
        {
            "data": "The launch decision is approved.",
            "type": "text",
            "created_at": "2026-07-20T10:00:00+08:00",
            "source_description": f"ceo-agent-memory:{expected_hash}",
        }
    ]
    attempt = store.list_reply_attempts(limit=1)[0]
    assert json.loads(attempt.audit_summary) == {
        "duplicate": False,
        "episode_uuid": "episode-1",
        "processing_status": "queued",
    }
    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "select status, result_json from universal_action_executions"
        ).fetchone()
    assert row[0] == "succeeded"
    assert json.loads(row[1])["episode_uuid"] == "episode-1"


def test_memory_timeout_can_resume_only_with_same_frozen_payload(tmp_path) -> None:
    first_runner = FakeMemoryWriteRunner([TimeoutError("network timeout")])
    store, _, execution, worker = build_execution(
        tmp_path, memory_write_runner=first_runner
    )

    with pytest.raises(TimeoutError):
        worker.execute_universal_memory_write(execution)
    assert store.get_universal_action_execution_state(execution).value == "unknown"

    second_runner = FakeMemoryWriteRunner(
        [MemoryWriteResult("episode-existing", "duplicate", True)]
    )
    resumed_worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(store.path),
        dws=object(),
        codex=object(),
        memory_write_runner=second_runner,
    )

    assert resumed_worker.execute_universal_memory_write(execution) is True
    assert len(second_runner.calls) == 1
    attempts = resumed_worker.store.list_reply_attempts(limit=10)
    assert len(attempts) == 1
    assert json.loads(attempts[0].audit_summary)["duplicate"] is True


def test_memory_unknown_recovery_rejects_tampered_frozen_payload(tmp_path) -> None:
    runner = FakeMemoryWriteRunner([TimeoutError("network timeout")])
    store, _, execution, worker = build_execution(tmp_path, memory_write_runner=runner)
    with pytest.raises(TimeoutError):
        worker.execute_universal_memory_write(execution)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update universal_action_executions set canonical_payload_json='{}'"
        )

    resumed = DingTalkAutoReplyWorker(
        store=AutoReplyStore(store.path),
        dws=object(),
        codex=object(),
        memory_write_runner=FakeMemoryWriteRunner(
            [MemoryWriteResult("episode-1", "queued", False)]
        ),
    )
    with pytest.raises(ValueError, match="memory payload identity mismatch"):
        resumed.execute_universal_memory_write(execution)


def test_memory_recovers_when_receipt_was_audited_before_completion_commit(
    tmp_path,
    monkeypatch,
) -> None:
    first_runner = FakeMemoryWriteRunner(
        [MemoryWriteResult("episode-1", "queued", False)]
    )
    store, _, execution, worker = build_execution(
        tmp_path, memory_write_runner=first_runner
    )

    def fail_completion(*args, **kwargs):
        raise OSError("database unavailable")

    monkeypatch.setattr(
        store,
        "complete_universal_memory_action_execution",
        fail_completion,
    )
    with pytest.raises(OSError, match="database unavailable"):
        worker.execute_universal_memory_write(execution)
    assert store.get_universal_action_execution_state(execution).value == "unknown"

    with sqlite3.connect(store.path) as db:
        db.execute(
            "update universal_action_executions set lease_expires_at='2000-01-01 00:00:00'"
        )

    resumed = DingTalkAutoReplyWorker(
        store=AutoReplyStore(store.path),
        dws=object(),
        codex=object(),
        memory_write_runner=FakeMemoryWriteRunner(
            [MemoryWriteResult("episode-1", "duplicate", True)]
        ),
    )

    assert resumed.execute_universal_memory_write(execution) is True
    assert len(resumed.store.list_reply_attempts(limit=10)) == 1


def test_concurrent_memory_workers_share_one_mcp_execution_lease(tmp_path) -> None:
    store, _, execution, failed_worker = build_execution(
        tmp_path,
        memory_write_runner=FakeMemoryWriteRunner([TimeoutError("network timeout")]),
    )
    with pytest.raises(TimeoutError):
        failed_worker.execute_universal_memory_write(execution)
    assert store.get_universal_action_execution_state(execution).value == "unknown"

    first_runner = BlockingMemoryWriteRunner()
    first_worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(store.path),
        dws=object(),
        codex=object(),
        memory_write_runner=first_runner,
    )
    second_runner = FakeMemoryWriteRunner(
        [MemoryWriteResult("episode-duplicate", "duplicate", True)]
    )
    second_worker = DingTalkAutoReplyWorker(
        store=AutoReplyStore(store.path),
        dws=object(),
        codex=object(),
        memory_write_runner=second_runner,
    )
    first_errors: list[BaseException] = []

    def run_first() -> None:
        try:
            first_worker.execute_universal_memory_write(execution)
        except BaseException as exc:  # pragma: no cover - surfaced below
            first_errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert first_runner.entered.wait(timeout=5)

    with pytest.raises(RuntimeError, match="memory action lease is active"):
        second_worker.execute_universal_memory_write(execution)
    assert second_runner.calls == []

    first_runner.release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_errors == []
    assert len(first_runner.calls) == 1
