from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent_context import AgentTaskContext
from app.agent_result import AgentError, AgentOutcome, AgentResult, SideEffectState
from app.agent_runner import DirectAgentRunResult
from app.channel_gate import ChannelGateResult, ChannelGateState
from app.dingtalk_models import DingTalkMessage
from app.store import AutoReplyStore
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
                if any(
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
                (_effect_event("send-1", "started"), _effect_event("send-1", "completed")),
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
    trigger = _message("请发送第一版")
    worker, runner, _dws = _worker(
        tmp_path,
        [trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION)), ScriptedRun(_result(AgentOutcome.NO_ACTION))],
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
        trigger_text="请发送修订版",
        trigger_message_json=trigger.model_copy(update={"content": "请发送修订版"}).model_dump_json(),
        attempt_id=1,
    )
    assert worker.consume_once(max_tasks=1) == 1

    assert rerun.execution_generation != first_generation
    assert [generation for _task, generation, _context in runner.calls] == [
        first_generation,
        rerun.execution_generation,
    ]


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
    context_fact = _message("预算上限已经确认是15%。", message_id="msg-fact")
    trigger = _message(
        "请基于已确认预算更新方案 https://alidocs.dingtalk.com/i/nodes/abc",
        message_id="msg-2",
    )
    worker, runner, dws = _worker(
        tmp_path,
        [context_fact, trigger],
        [ScriptedRun(_result(AgentOutcome.NO_ACTION, summary="已复用预算事实。"))],
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    context = runner.calls[0][2]
    assert any("15%" in message.text for message in context.messages)
    assert any("dws doc" in command for material in context.materials for command in material.read_commands)
    assert dws.forbidden_material_reads == []


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
    ("oa_state", "outcome", "effectful"),
    [
        ("complete_form", AgentOutcome.COMPLETED, True),
        ("instance_id_only", AgentOutcome.COMPLETED, True),
        ("ambiguous_candidates", AgentOutcome.NEEDS_HUMAN, False),
        ("task_completed", AgentOutcome.NO_ACTION, False),
        ("task_not_current_user", AgentOutcome.NEEDS_HUMAN, False),
    ],
)
def test_oa_runtime_passes_live_read_commands_and_safe_agent_outcome(
    tmp_path: Path,
    oa_state: str,
    outcome: AgentOutcome,
    effectful: bool,
):
    trigger = _message(
        "请审核这个审批",
        raw_payload={
            "oa_state": oa_state,
            "processInstanceId": "proc-1",
            **({"taskId": "task-1"} if oa_state == "complete_form" else {}),
        },
    )
    events = (_read_event("oa-detail"),)
    side_effect = SideEffectState.NONE
    if effectful:
        events += (_effect_event("oa-write", "started"), _effect_event("oa-write", "completed"))
        side_effect = SideEffectState.CONFIRMED
    worker, runner, dws = _worker(
        tmp_path,
        [trigger],
        [
            ScriptedRun(
                _result(
                    outcome,
                    summary=f"OA scenario: {oa_state}",
                    code=("oa_target_ambiguous" if outcome is AgentOutcome.NEEDS_HUMAN else ""),
                    side_effect_state=side_effect,
                ),
                events,
            )
        ],
    )
    _enqueue(worker.store, trigger)

    worker.consume_once(max_tasks=1)

    context = runner.calls[0][2]
    commands = [command for material in context.materials for command in material.read_commands]
    assert "proc-1" in " ".join(commands)
    assert any("dws oa approval detail" in command for command in commands)
    assert dws.forbidden_material_reads == []
    task_id = runner.calls[0][0]
    run = worker.store.get_agent_run_for_task_generation(task_id, "g1")
    assert run is not None
    effectful_events = [
        event
        for event in run.tool_events
        if isinstance(event.get("item"), dict)
        and event["item"].get("metadata") == {"effect": "effectful"}
    ]
    assert bool(effectful_events) is effectful
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == ("completed" if effectful else (
        "skipped" if outcome is AgentOutcome.NO_ACTION else "blocked"
    ))
