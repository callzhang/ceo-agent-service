import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.agent_result import AgentError, AgentOutcome, AgentResult, SideEffectState
from app.agent_runner import DirectAgentRunner, DirectAgentRunResult
from app.channel_gate import ChannelGateResult, ChannelGateState
from app.dingtalk_models import DingTalkMessage
from app.store import AutoReplyStore
from app.process_runner import ProcessRunResult
from app.worker import DingTalkAutoReplyWorker


NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)


class ReadyGate:
    def __init__(self, channel: str) -> None:
        self.channel_name = channel

    def check(self) -> ChannelGateResult:
        return ChannelGateResult(
            channel=self.channel_name,
            state=ChannelGateState.READY,
            reason_code="ready",
        )


class ContextOnlyDws:
    dws_bin = "dws"

    def __init__(self, messages: list[DingTalkMessage]) -> None:
        self.messages = messages
        self.recent_reads = 0
        self.unread_reads = 0
        self.forbidden_material_reads: list[str] = []

    def read_recent_messages(self, _conversation) -> list[DingTalkMessage]:
        self.recent_reads += 1
        return list(self.messages)

    def read_unread_messages(self, _conversation) -> list[DingTalkMessage]:
        self.unread_reads += 1
        return list(self.messages)

    def __getattr__(self, name: str):
        if name.startswith(
            (
                "read_doc",
                "read_minutes",
                "download",
                "get_aitable",
                "query_aitable",
                "read_oa",
                "search_document",
            )
        ):
            def forbidden(*_args, **_kwargs):
                self.forbidden_material_reads.append(name)
                raise AssertionError(f"service material read is forbidden: {name}")

            return forbidden
        raise AttributeError(name)


def _effect_event(call_id: str, status: str) -> dict[str, object]:
    return {
        "type": f"item.{status}",
        "item": {
            "id": call_id,
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }


def _read_event(call_id: str = "read-1") -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "id": call_id,
            "type": "mcp_tool_call",
            "metadata": {"effect": "read_only"},
        },
    }


def _result(
    outcome: AgentOutcome = AgentOutcome.COMPLETED,
    *,
    summary: str = "任务已完成。",
    retryable: bool = False,
    side_effect_state: SideEffectState = SideEffectState.NONE,
    code: str = "",
) -> AgentResult:
    return AgentResult(
        outcome=outcome,
        summary=summary,
        error=AgentError(
            code=code,
            retryable=retryable,
            side_effect_state=side_effect_state,
        ),
    )


@dataclass
class ScriptedRun:
    result: AgentResult
    events: tuple[dict[str, object], ...] = ()
    session_id: str = "session-1"
    receipts: tuple["PersistedCommandReceipt", ...] = ()


@dataclass(frozen=True)
class PersistedCommandReceipt:
    operation_id: str
    command_digest: str
    cli: str = "dws"
    command_path: str = "chat message send"


def _receipt(
    operation_id: str,
    *,
    command_digest: str = "a" * 64,
    command_path: str = "chat message send",
) -> PersistedCommandReceipt:
    return PersistedCommandReceipt(
        operation_id=operation_id,
        command_digest=command_digest,
        command_path=command_path,
    )


class ScriptedDirectAgentRunner:
    def __init__(self, store: AutoReplyStore, scripts: list[ScriptedRun]) -> None:
        self.store = store
        self.scripts = scripts
        self.calls: list[tuple[int, str, AgentTaskContext]] = []
        self.resume_session_ids: list[str] = []
        self.read_only_values: list[bool] = []
        self.owner = "scripted-agent"

    def run(self, task, context, **kwargs) -> DirectAgentRunResult:
        self.read_only_values.append(bool(kwargs.get("read_only")))
        claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            owner=self.owner,
            lease_seconds=1800,
            now=NOW,
        )
        assert claim.claimed
        self.calls.append((task.id, task.execution_generation, context))
        script = self.scripts.pop(0)
        run = claim.run
        if run.codex_session_id:
            self.resume_session_ids.append(run.codex_session_id)
        else:
            run = self.store.set_agent_run_session(
                run.id,
                script.session_id,
                owner=self.owner,
                now=NOW,
            )
        for event in script.events:
            run = self.store.append_agent_run_event(
                run.id,
                event,
                owner=self.owner,
                now=NOW,
            )
        for receipt in script.receipts:
            self.store.record_agent_execution_receipt(
                run.id,
                receipt_id=f"native:{receipt.operation_id}:{receipt.command_digest}",
                operation_id=receipt.operation_id,
                cli=receipt.cli,
                command_path=receipt.command_path,
                command_digest=receipt.command_digest,
                exit_code=0,
                now=NOW,
            )
        if script.result.error.side_effect_state is SideEffectState.UNKNOWN:
            self.store.mark_agent_run_unknown(
                run.id,
                {"code": script.result.error.code or "agent_side_effect_unknown"},
                owner=self.owner,
                transcript_end_line=len(script.events),
                now=NOW,
            )
        elif script.result.outcome is AgentOutcome.FAILED:
            self.store.fail_agent_run(
                run.id,
                script.result.error.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=len(script.events),
                now=NOW,
            )
        else:
            evidence_state = (
                "confirmed"
                if script.receipts
                or any(
                    event.get("type") == "item.completed"
                    and isinstance(event.get("item"), dict)
                    and event["item"].get("metadata") == {"effect": "effectful"}
                    for event in script.events
                )
                else "none"
            )
            self.store.complete_agent_run(
                run.id,
                script.result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=evidence_state,
                transcript_end_line=len(script.events),
                now=NOW,
            )
        return DirectAgentRunResult(
            run_id=run.id,
            result=script.result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=len(script.events),
            events=script.events,
        )


def _prompt_json_section(prompt: str, heading: str):
    start = prompt.index(heading) + len(heading)
    value, _end = json.JSONDecoder().raw_decode(prompt[start:].lstrip())
    return value


def _agent_result_event(result: AgentResult) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": result.model_dump_json(),
        },
    }


def _command_event(
    event_type: str,
    call_id: str,
    command: str,
    *,
    output: str = "",
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": call_id,
        "type": "command_execution",
        "command": command,
    }
    if event_type == "item.completed":
        item.update(
            {
                "exit_code": 0,
                "status": "completed",
                "aggregated_output": output,
            }
        )
    return {"type": event_type, "item": item}


class ProtocolCodexExecutor:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, _command, *, prompt: str, **kwargs) -> ProcessRunResult:
        self.prompts.append(prompt)
        records = [
            {"type": "thread.started", "thread_id": "protocol-session"},
            *self.records(prompt),
        ]
        output = "\n".join(json.dumps(record) for record in records)
        callback = kwargs["on_stdout_line"]
        for line in output.splitlines():
            callback(line)
        return ProcessRunResult(returncode=0, stdout=output, stderr="")

    def records(self, prompt: str) -> list[dict[str, object]]:
        raise NotImplementedError


class ConfirmedFactProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, fact_value: str) -> None:
        super().__init__()
        self.fact_value = fact_value
        self.fact_was_present = False

    def records(self, prompt: str) -> list[dict[str, object]]:
        messages = _prompt_json_section(prompt, "Recent conversation context\n")
        self.fact_was_present = any(
            self.fact_value in str(message.get("text") or "")
            for message in messages
            if isinstance(message, dict)
        )
        result = (
            _result(
                AgentOutcome.NO_ACTION,
                summary=f"Reused confirmed context value {self.fact_value}.",
            )
            if self.fact_was_present
            else _result(
                AgentOutcome.NEEDS_HUMAN,
                summary="A required confirmed context value is missing.",
                code="confirmed_fact_missing",
            )
        )
        return [_agent_result_event(result)]


class NativeCommandStub:
    def __init__(self, read_output: dict[str, object]) -> None:
        self.read_output = read_output
        self.calls: list[str] = []
        self.write_calls: list[str] = []

    def __call__(self, command: str) -> str:
        self.calls.append(command)
        if command.startswith("dws oa approval detail "):
            return json.dumps(self.read_output)
        if command.startswith("dws oa approval approve "):
            self.write_calls.append(command)
            return json.dumps({"success": True})
        if command.startswith(("dws doc info ", "dws doc read ")):
            return json.dumps({"content": "diagnostic evidence"})
        raise AssertionError(f"unexpected native command: {command}")


class OaProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, native_executor: NativeCommandStub) -> None:
        super().__init__()
        self.native_executor = native_executor
        self.read_commands: list[str] = []

    def records(self, prompt: str) -> list[dict[str, object]]:
        materials = _prompt_json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        oa_material = next(
            material
            for material in materials
            if isinstance(material, dict) and material.get("kind") == "dingtalk_oa"
        )
        reference = json.loads(str(oa_material["reference"]))
        records: list[dict[str, object]] = []
        live_results: list[dict[str, object]] = []
        for index, command in enumerate(oa_material["read_commands"]):
            self.read_commands.append(command)
            output = self.native_executor(command)
            records.extend(
                (
                    _command_event("item.started", f"oa-read-{index}", command),
                    _command_event(
                        "item.completed",
                        f"oa-read-{index}",
                        command,
                        output=output,
                    ),
                )
            )
            live_results.append(json.loads(output))

        tasks = live_results[-1].get("tasks") if live_results else []
        live_tasks = tasks if isinstance(tasks, list) else []
        if len(live_tasks) != 1:
            result = _result(
                AgentOutcome.NEEDS_HUMAN,
                summary="Live OA detail has more than one candidate task.",
                code="oa_target_ambiguous",
            )
        else:
            live_task = live_tasks[0] if isinstance(live_tasks[0], dict) else {}
            status = str(live_task.get("status") or "").lower()
            if status == "completed":
                result = _result(
                    AgentOutcome.NO_ACTION,
                    summary="Live OA task is already completed.",
                )
            elif live_task.get("current_user") is not True:
                result = _result(
                    AgentOutcome.NEEDS_HUMAN,
                    summary="Live OA task does not belong to the current user.",
                    code="oa_task_not_current_user",
                )
            else:
                task_id = str(live_task.get("task_id") or "")
                process_id = str(reference.get("process_instance_id") or "")
                write_command = (
                    "dws oa approval approve --instance-id "
                    f"{process_id} --task-id {task_id} "
                    "--remark 'Reviewed by protocol agent' --format json --yes"
                )
                output = self.native_executor(write_command)
                records.extend(
                    (
                        _command_event("item.started", "oa-write", write_command),
                        _command_event(
                            "item.completed",
                            "oa-write",
                            write_command,
                            output=output,
                        ),
                    )
                )
                result = _result(
                    summary="Live OA task was reviewed and completed.",
                    side_effect_state=SideEffectState.CONFIRMED,
                )
        records.append(_agent_result_event(result))
        return records


class DiagnosisOnlyProtocolExecutor(ProtocolCodexExecutor):
    def __init__(self, native_executor: NativeCommandStub) -> None:
        super().__init__()
        self.native_executor = native_executor

    def records(self, prompt: str) -> list[dict[str, object]]:
        materials = _prompt_json_section(
            prompt,
            "Raw material references and exact read commands\n",
        )
        command = next(
            command
            for material in materials
            if isinstance(material, dict)
            for command in material.get("read_commands", [])
        )
        output = self.native_executor(command)
        return [
            _command_event("item.started", "diagnostic-read", command),
            _command_event(
                "item.completed",
                "diagnostic-read",
                command,
                output=output,
            ),
            _agent_result_event(
                _result(
                    summary="Diagnosed the requested repair but did not execute it.",
                    side_effect_state=SideEffectState.CONFIRMED,
                )
            ),
        ]


class FailedWriteProtocolExecutor(ProtocolCodexExecutor):
    def records(self, _prompt: str) -> list[dict[str, object]]:
        command = "dws chat message send --group cid-1 --text 'hello' --yes"
        failed = _command_event("item.completed", "send-failed", command)
        failed["item"].update({"exit_code": 1, "status": "failed"})
        return [
            _command_event("item.started", "send-failed", command),
            failed,
            _agent_result_event(
                _result(
                    AgentOutcome.FAILED,
                    summary="The native write returned a nonzero exit code.",
                    retryable=True,
                    code="native_write_failed",
                )
            ),
        ]


def _message(
    text: str = "@CEO Agent 请处理",
    *,
    message_id: str = "msg-1",
    raw_payload: dict[str, object] | None = None,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="测试群",
        single_chat=False,
        sender_name="ET",
        sender_user_id="user-1",
        create_time="2026-07-29 16:55:00",
        content=text,
        raw_payload=raw_payload or {},
    )


def _enqueue(
    store: AutoReplyStore,
    trigger: DingTalkMessage,
    *,
    generation: str = "g1",
    oa_url: str = "",
) -> int:
    assert store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        execution_generation=generation,
        oa_url=oa_url,
    )
    task = store.get_reply_task_for_message(
        trigger.open_conversation_id,
        trigger.open_message_id,
    )
    assert task is not None
    return task.id


def _worker(
    tmp_path: Path,
    messages: list[DingTalkMessage],
    scripts: list[ScriptedRun],
) -> tuple[DingTalkAutoReplyWorker, ScriptedDirectAgentRunner, ContextOnlyDws]:
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws(messages)
    runner = ScriptedDirectAgentRunner(store, scripts)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        direct_agent_runner=runner,
        channel_gates={
            "dingtalk": ReadyGate("dingtalk"),
            "lark": ReadyGate("lark"),
        },
        now_provider=lambda: NOW,
    )
    return worker, runner, dws


def _worker_with_protocol_executor(
    tmp_path: Path,
    messages: list[DingTalkMessage],
    executor: ProtocolCodexExecutor,
    *,
    max_task_attempts: int = 3,
) -> tuple[DingTalkAutoReplyWorker, ContextOnlyDws]:
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    dws = ContextOnlyDws(messages)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=object(),
        direct_agent_runner=DirectAgentRunner(
            store=store,
            workspace=tmp_path,
            executor=executor,
            owner="protocol-agent",
        ),
        channel_gates={
            "dingtalk": ReadyGate("dingtalk"),
            "lark": ReadyGate("lark"),
        },
        now_provider=lambda: NOW,
        max_task_attempts=max_task_attempts,
    )
    return worker, dws


def test_queued_task_runs_agent_once_and_records_completed_attempt(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    summary="已回复并确认发送成功。",
                    side_effect_state=SideEffectState.CONFIRMED,
                ),
                receipts=(_receipt("send-1"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 1
    assert [(task, generation) for task, generation, _ in runner.calls] == [(task_id, "g1")]
    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.codex_reason == "已回复并确认发送成功。"
    assert attempt.audit_summary == "已回复并确认发送成功。"


def test_dry_run_invokes_direct_agent_in_read_only_mode(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION, summary="只读检查完成。"))],
    )
    worker.dry_run = True
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert runner.read_only_values == [True]


@pytest.mark.parametrize(
    ("script", "task_status", "attempt_status"),
    [
        (ScriptedRun(_result(AgentOutcome.NO_ACTION, summary="无需动作。")), "done", "skipped"),
        (
            ScriptedRun(
                _result(
                    AgentOutcome.NEEDS_HUMAN,
                    summary="需要人工补充权限。",
                    code="permission_missing",
                )
            ),
            "done",
            "blocked",
        ),
        (
            ScriptedRun(
                _result(
                    AgentOutcome.FAILED,
                    summary="暂时无法读取。",
                    retryable=True,
                    code="temporary_read_failure",
                )
            ),
            "pending",
            "failed",
        ),
        (
            ScriptedRun(
                _result(
                    AgentOutcome.FAILED,
                    summary="材料永久缺失。",
                    code="material_missing",
                )
            ),
            "failed",
            "failed",
        ),
    ],
)
def test_agent_result_maps_to_task_and_attempt(
    tmp_path: Path,
    script: ScriptedRun,
    task_status: str,
    attempt_status: str,
):
    trigger = _message()
    worker, _runner, _dws = _worker(tmp_path, [trigger], [script])
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == task_status
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == attempt_status
    if attempt_status == "blocked":
        assert "permission_missing" in attempt.send_error


def test_retryable_failure_reuses_generation_and_session_then_succeeds(
    tmp_path: Path,
):
    trigger = _message("请读取材料后完成回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    AgentOutcome.FAILED,
                    summary="临时读取失败。",
                    retryable=True,
                    code="temporary_read_failure",
                ),
                session_id="session-retry",
            ),
            ScriptedRun(
                _result(AgentOutcome.NO_ACTION, summary="已完成复核，无需回复。"),
                session_id="unused",
            ),
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)
    first = worker.store.get_reply_task(task_id)
    assert first is not None and first.status == "pending"
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task_id,),
        )

    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 2
    assert [generation for _task, generation, _context in runner.calls] == [
        "g1",
        "g1",
    ]
    assert runner.resume_session_ids == ["session-retry"]
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None and run.status == "completed"
    assert worker.store.get_reply_task(task_id).status == "done"


def test_retryable_failure_stops_at_worker_attempt_limit(tmp_path: Path):
    trigger = _message("请读取材料后完成回复")
    scripts = [
        ScriptedRun(
            _result(
                AgentOutcome.FAILED,
                summary="临时读取失败。",
                retryable=True,
                code="temporary_read_failure",
            ),
            session_id="session-retry",
        )
        for _ in range(3)
    ]
    worker, runner, _dws = _worker(tmp_path, [trigger], scripts)
    worker.max_task_attempts = 2
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)
    with worker.store._connect() as db:
        db.execute("update reply_tasks set available_at='' where id=?", (task_id,))
    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 2
    assert worker.store.get_reply_task(task_id).status == "failed"


def test_completed_confirmed_without_effectful_evidence_is_rejected(tmp_path: Path):
    trigger = _message("请修复服务")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                (_read_event(),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "failed"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "completion_evidence" in attempt.send_error


def test_diagnosis_only_for_requested_execution_is_blocked_by_agent_result(
    tmp_path: Path,
):
    trigger = _message("请执行修复并验证")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    AgentOutcome.NEEDS_HUMAN,
                    summary="已定位问题，但当前没有执行权限。",
                    code="execution_not_performed",
                ),
                (_read_event("diagnosis-read"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "done"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "execution_not_performed"


def test_no_action_with_persisted_effect_receipt_is_rejected(tmp_path: Path):
    trigger = _message("请检查是否需要处理")
    worker, _runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(AgentOutcome.NO_ACTION, summary="无需动作。"),
                receipts=(_receipt("unexpected-write"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert worker.store.get_reply_task(task_id).status == "failed"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "completion_evidence_inconsistent"


def test_incomplete_effect_is_unknown_and_never_replayed(tmp_path: Path):
    trigger = _message("请发送回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    AgentOutcome.FAILED,
                    summary="发送结果未知。",
                    code="send_interrupted",
                    side_effect_state=SideEffectState.UNKNOWN,
                ),
                (_effect_event("send-1", "started"),),
            )
        ],
    )
    task_id = _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 1
    assert worker.store.get_reply_task(task_id).status == "failed"
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None and run.status == "unknown"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "blocked"


def test_manual_rerun_rotates_generation_and_allows_changed_work(tmp_path: Path):
    trigger = _message("请给目标 A 发送第一版")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                receipts=(
                    _receipt("send-a", command_digest="a" * 64),
                ),
            ),
            ScriptedRun(
                _result(side_effect_state=SideEffectState.CONFIRMED),
                receipts=(
                    _receipt("send-b", command_digest="b" * 64),
                ),
            ),
        ],
    )
    first_id = _enqueue(worker.store, trigger)
    assert worker.consume_once(max_tasks=1) == 1
    first_generation = worker.store.get_reply_task(first_id).execution_generation

    rerun = worker.store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text="请改为给目标 B 发送修订版",
        trigger_message_json=trigger.model_copy(
            update={"content": "请改为给目标 B 发送修订版"}
        ).model_dump_json(),
        attempt_id=1,
    )
    assert worker.consume_once(max_tasks=1) == 1

    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', available_at='', locked_at='' "
            "where id=?",
            (first_id,),
        )
    assert worker.consume_once(max_tasks=1) == 1

    assert rerun.execution_generation != first_generation
    assert [generation for _task, generation, _context in runner.calls] == [
        first_generation,
        rerun.execution_generation,
    ]
    assert runner.calls[0][2].trigger_text == "请给目标 A 发送第一版"
    assert runner.calls[1][2].trigger_text == "请改为给目标 B 发送修订版"
    first_run = worker.store.get_agent_run_for_task_generation(
        first_id,
        first_generation,
    )
    second_run = worker.store.get_agent_run_for_task_generation(
        first_id,
        rerun.execution_generation,
    )
    assert first_run is not None and second_run is not None
    assert [
        receipt.operation_id
        for receipt in worker.store.list_agent_execution_receipts(first_run.id)
    ] == ["send-a"]
    assert [
        receipt.operation_id
        for receipt in worker.store.list_agent_execution_receipts(second_run.id)
    ] == ["send-b"]


def test_completed_generation_is_not_executed_again(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION))],
    )
    task_id = _enqueue(worker.store, trigger)
    assert worker.consume_once(max_tasks=1) == 1
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set status='pending', available_at='', locked_at='' "
            "where id=?",
            (task_id,),
        )

    assert worker.consume_once(max_tasks=1) == 1

    assert len(runner.calls) == 1
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None and run.status == "completed"


def test_stale_processing_resumes_same_generation_and_session(tmp_path: Path):
    trigger = _message()
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION), session_id="unused")],
    )
    task_id = _enqueue(worker.store, trigger)
    task = worker.store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    claim = worker.store.claim_agent_run(
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-stale",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    worker.consume_once(max_tasks=1)

    assert runner.resume_session_ids == ["session-stale"]
    assert [generation for _task, generation, _context in runner.calls] == ["g1"]
    assert worker.store.get_reply_task(task_id).status == "done"


def test_stale_retryable_failed_run_resumes_same_generation_and_session(
    tmp_path: Path,
):
    trigger = _message("请读取材料后完成回复")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION), session_id="unused")],
    )
    task_id = _enqueue(worker.store, trigger)
    task = worker.store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    claim = worker.store.claim_agent_run(
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-retry-after-restart",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    worker.store.fail_agent_run(
        claim.run.id,
        {"code": "temporary_read_failure", "retryable": True},
        owner="dead-worker",
        now="2026-07-28 07:00:01",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    assert worker.consume_once(max_tasks=1) == 0
    recovered_task = worker.store.get_reply_task(task_id)
    assert recovered_task is not None and recovered_task.status == "pending"
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='' where id=?",
            (task.id,),
        )
    assert worker.consume_once(max_tasks=1) == 1

    assert runner.resume_session_ids == ["session-retry-after-restart"]
    assert [generation for _task, generation, _context in runner.calls] == ["g1"]
    assert worker.store.get_reply_task(task_id).status == "done"


def test_stale_processing_with_incomplete_effect_becomes_unknown_without_rerun(
    tmp_path: Path,
):
    trigger = _message("请发送一次通知")
    worker, runner, _dws = _worker(tmp_path, [trigger], [])
    task_id = _enqueue(worker.store, trigger)
    task = worker.store.claim_reply_task(task_id, now="2026-07-28 07:00:00")
    assert task is not None
    claim = worker.store.claim_agent_run(
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-07-28 07:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-stale",
        owner="dead-worker",
        now="2026-07-28 07:00:00",
    )
    worker.store.append_agent_run_event(
        claim.run.id,
        _effect_event("send-1", "started"),
        owner="dead-worker",
        now="2026-07-28 07:00:01",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-28 07:00:00' where id=?",
            (task.id,),
        )

    assert worker.consume_once(max_tasks=1) == 0

    assert runner.calls == []
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None
    assert run.status == "unknown"
    assert run.side_effect_state == "unknown"
    assert worker.store.get_reply_task(task_id).status == "failed"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "blocked"


def test_context_reuses_confirmed_fact_and_does_not_pre_read_material(tmp_path: Path):
    fact_value = "value-4827-zeta"
    context_fact = _message(
        json.dumps({"confirmed_field": fact_value}),
        message_id="msg-fact",
    )
    trigger = _message(
        "请复用上下文中的已确认字段，不要再次询问。",
        message_id="msg-2",
    )
    executor = ConfirmedFactProtocolExecutor(fact_value)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [context_fact, trigger],
        executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert executor.fact_was_present is True
    assert fact_value in executor.prompts[0]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-2")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == ""
    assert dws.forbidden_material_reads == []


def test_confirmed_fact_protocol_agent_asks_only_when_fact_is_absent(tmp_path: Path):
    fact_value = "value-4827-zeta"
    trigger = _message("请复用上下文中的已确认字段。")
    executor = ConfirmedFactProtocolExecutor(fact_value)
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert executor.fact_was_present is False
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "confirmed_fact_missing"


@pytest.mark.parametrize(
    ("action", "send_status"),
    [("agent_run", "skipped"), ("no_action", "completed")],
)
def test_skipped_attempt_is_not_exposed_as_completed_prior_receipt(
    tmp_path: Path,
    action: str,
    send_status: str,
):
    trigger = _message("无需执行外部动作")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION, summary="无需动作。"))],
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="测试群",
        trigger_message_id="msg-1",
        trigger_sender="ET",
        trigger_text=trigger.content,
        action=action,
        sensitivity_kind="general",
        codex_reason="No external action was required.",
        audit_summary="No external action was required.",
        send_status=send_status,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    context = runner.calls[0][2]
    assert context.prior_receipts == ()
    assert "No external action was required" not in context.render()


def test_calendar_context_passes_raw_event_id_and_exact_live_read_command(
    tmp_path: Path,
):
    trigger = _message(
        "dingtalk://dingtalkclient/action/open_mini_app?page=detail%3FuniqueId%3Devent-1",
        raw_payload={"eventId": "event-1"},
    )
    worker, runner, dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION, summary="已检查日程。"))],
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    material = next(
        item
        for item in runner.calls[0][2].materials
        if item.kind == "dingtalk_calendar"
    )
    assert '"event_id": "event-1"' in material.reference
    assert material.read_commands == (
        "dws calendar event get --id event-1 --format json",
    )
    assert dws.forbidden_material_reads == []


@pytest.mark.parametrize(
    ("oa_state", "raw_payload", "live_output", "attempt_status", "effectful"),
    [
        (
            "complete_form",
            {"processInstanceId": "proc-1", "taskId": "task-1"},
            {"tasks": [{"task_id": "task-1", "status": "running", "current_user": True}]},
            "completed",
            True,
        ),
        (
            "instance_id_only",
            {"processInstanceId": "proc-1"},
            {"tasks": [{"task_id": "task-live", "status": "running", "current_user": True}]},
            "completed",
            True,
        ),
        (
            "ambiguous_candidates",
            {"processInstanceId": "proc-1"},
            {
                "tasks": [
                    {"task_id": "task-a", "status": "running", "current_user": True},
                    {"task_id": "task-b", "status": "running", "current_user": True},
                ]
            },
            "blocked",
            False,
        ),
        (
            "task_completed",
            {"processInstanceId": "proc-1"},
            {"tasks": [{"task_id": "task-1", "status": "completed", "current_user": True}]},
            "skipped",
            False,
        ),
        (
            "task_not_current_user",
            {"processInstanceId": "proc-1"},
            {"tasks": [{"task_id": "task-1", "status": "running", "current_user": False}]},
            "blocked",
            False,
        ),
    ],
)
def test_oa_runtime_agent_executes_live_read_commands_and_decides_from_output(
    tmp_path: Path,
    oa_state: str,
    raw_payload: dict[str, object],
    live_output: dict[str, object],
    attempt_status: str,
    effectful: bool,
):
    trigger = _message(
        "请审核这个审批",
        raw_payload=raw_payload,
    )
    native_executor = NativeCommandStub(live_output)
    codex_executor = OaProtocolExecutor(native_executor)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        codex_executor,
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    assert "proc-1" in " ".join(codex_executor.read_commands)
    assert any(
        "dws oa approval detail" in command
        for command in codex_executor.read_commands
    )
    assert native_executor.calls[: len(codex_executor.read_commands)] == (
        codex_executor.read_commands
    )
    assert dws.forbidden_material_reads == []
    task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    task_id = task.id
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None
    assert bool(native_executor.write_calls) is effectful
    assert bool(worker.store.list_agent_execution_receipts(run.id)) is effectful
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == attempt_status
    if oa_state == "instance_id_only":
        assert "task-live" in native_executor.write_calls[0]


def test_diagnosis_only_completed_claim_is_rejected_by_real_runner_protocol(
    tmp_path: Path,
):
    trigger = _message(
        "请修复并验证这个文档关联的问题 "
        "https://alidocs.dingtalk.com/i/nodes/diagnostic-doc"
    )
    native_executor = NativeCommandStub({})
    codex_executor = DiagnosisOnlyProtocolExecutor(native_executor)
    worker, dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        codex_executor,
        max_task_attempts=1,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.get_reply_task(task_id)
    assert task is not None and task.status == "failed"
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "codex_result_invalid"
    assert len(native_executor.calls) == 1
    assert native_executor.write_calls == []
    assert dws.forbidden_material_reads == []


def test_nonzero_native_write_uses_failed_retry_path_in_real_runner_protocol(
    tmp_path: Path,
):
    trigger = _message("请发送一次通知")
    executor = FailedWriteProtocolExecutor()
    worker, _dws = _worker_with_protocol_executor(
        tmp_path,
        [trigger],
        executor,
    )
    task_id = _enqueue(worker.store, trigger)

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.get_reply_task(task_id)
    assert task is not None and task.status == "pending"
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None
    assert run.status == "failed"
    assert run.side_effect_state == "none"
    assert worker.store.list_agent_execution_receipts(run.id) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "native_write_failed"
