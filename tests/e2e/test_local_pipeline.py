from datetime import datetime

from app.agent_contracts import AuditAgentResult, ConsumerAgentResult
from app.agent_orchestrator import OrchestrationResult
from app.channel_gate import ChannelGateResult, ChannelGateState
from app.dingtalk_models import DingTalkMessage
from app.store import AgentRole, AutoReplyStore
from app.dws_client import DwsCalendarAttendee, DwsCalendarEvent
from app.audit_web import render_attempt_list
from app.meeting_alignment import (
    consume_meeting_alignment_jobs,
    produce_meeting_alignment_jobs,
)
from app.meeting_alignment_models import MeetingAlignmentDecision
from app.worker import DingTalkAutoReplyWorker


def _get_audit_run(store, task_id: int, execution_generation: str):
    return store.get_agent_run_for_turn(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


class FakeMeetingDws:
    def __init__(self):
        self.sent = []
        self.meeting = {
            "taskUuid": "minutes-e2e-1",
            "title": "上线评审",
            "startTimeISO": "2026-07-14T09:00:00+08:00",
            "endTimeISO": "2026-07-14T10:00:00+08:00",
            "status": "ended",
        }
        self.calendar_event = DwsCalendarEvent(
            event_id="event-e2e-1",
            title="上线评审",
            start_time="2026-07-14T09:00:00+08:00",
            end_time="2026-07-14T10:00:00+08:00",
            status="confirmed",
            attendee_details=[
                DwsCalendarAttendee(
                    display_name="Derek",
                    is_self=True,
                    user_id="u-derek",
                    open_dingtalk_id="open-derek",
                ),
                DwsCalendarAttendee(
                    display_name="A",
                    user_id="u-a",
                    open_dingtalk_id="open-a",
                ),
                DwsCalendarAttendee(
                    display_name="B",
                    user_id="u-b",
                    open_dingtalk_id="open-b",
                ),
            ],
        )

    def list_minutes_page(self, *, limit, cursor, start, end):
        return {"items": [self.meeting], "has_more": False, "next_token": ""}

    def get_minutes_info(self, meeting_id):
        assert meeting_id == "minutes-e2e-1"
        return self.meeting

    def get_current_user_id(self):
        return "u-derek"

    def list_calendar_events_page(self, *, start, end, limit, cursor):
        return {
            "events": [self.calendar_event],
            "has_more": False,
            "next_cursor": "",
        }

    def get_minutes_summary(self, meeting_id):
        return {"result": {"fullSummary": "A 主张全量，B 主张灰度，尚未一致。"}}

    def get_all_minutes_transcription(self, meeting_id):
        return {
            "paragraphs": [
                {"nickName": "A", "paragraph": "全量上线效率最高。"},
                {"nickName": "B", "paragraph": "灰度上线风险更低。"},
                {"nickName": "Derek", "paragraph": "先明确风险预算。"},
            ]
        }

    def get_conversation_info(self, conversation_id):
        assert conversation_id == "cid-first"
        return {
            "openConversationId": "cid-first",
            "title": "项目群",
            "singleChat": False,
            "memberCount": 3,
        }

    def list_group_member_open_dingtalk_ids(self, conversation_id):
        assert conversation_id == "cid-first"
        return {"open-derek", "open-a", "open-b"}

    def search_user_profiles(self, query):
        return []

    def read_recent_messages(self, conversation, limit=50):
        return []

    def send_message(self, conversation_id, text, **kwargs):
        self.sent.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return {"success": True, "result": {"openMessageId": "msg-meeting-e2e"}}

    def verify_message_send_result(self, send_result):
        return {"state": "sent", "open_task_id": "", "status_result": {}}


class FakeMeetingRunner:
    last_session_id = "meeting-e2e-session"
    last_transcript_start_line = 0
    last_transcript_end_line = 10
    last_audit_tool_events = []

    def decide(self, *, prompt, run_id):
        assert run_id > 0
        return MeetingAlignmentDecision.model_validate(
            {
                "action": "send",
                "trigger_reasons": ["unresolved_disagreement"],
                "topics": [
                    {
                        "title": "上线范围",
                        "state": "unresolved",
                        "views": [
                            {"speaker": "A", "view": "全量", "reason": "效率"},
                            {"speaker": "B", "view": "灰度", "reason": "风险"},
                        ],
                        "conclusion": "",
                        "alignment_reason": "",
                    }
                ],
                "derek_viewpoint": None,
                "key_questions": [
                    {
                        "question": "可接受的最大故障半径是多少？",
                        "answer_owner_names": ["A", "B"],
                    }
                ],
                "mention_names": ["A", "B"],
                "target": {
                    "kind": "group",
                    "conversation_id": "cid-first",
                    "direct_user_id": "",
                    "title": "项目群",
                    "candidates": [
                        {
                            "conversation_id": "cid-first",
                            "title": "项目群",
                            "evidence": ["参会人和主题最匹配"],
                        },
                        {
                            "conversation_id": "cid-second",
                            "title": "备用群",
                            "evidence": ["参会人部分重合"],
                        },
                    ],
                },
                "final_message": "会后对齐：@A @B 可接受的最大故障半径是多少？",
                "audit_summary": "发现上线范围分歧，尚未明确对齐。",
                "confidence": 0.9,
            }
        )


def test_meeting_alignment_pipeline_sends_once_and_appears_in_history(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeMeetingDws()
    runner = FakeMeetingRunner()
    now = datetime.fromisoformat("2026-07-14T10:11:00+08:00")

    assert produce_meeting_alignment_jobs(store, dws, now=now) == 1
    assert consume_meeting_alignment_jobs(store, dws, runner, now=now, limit=1) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-e2e-1")
    assert job is not None
    assert job.status == "sent"
    assert len(store.list_meeting_alignment_runs(job.id)) == 1
    assert len(dws.sent) == 1
    assert dws.sent[0]["conversation_id"] == "cid-first"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-a", "open-b"]
    assert "会后对齐" in render_attempt_list(store)

    assert produce_meeting_alignment_jobs(store, dws, now=now) == 0
    assert consume_meeting_alignment_jobs(store, dws, runner, now=now, limit=1) == 0
    assert len(dws.sent) == 1


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

    def __init__(self, trigger: DingTalkMessage) -> None:
        self.trigger = trigger

    def read_recent_messages(self, _conversation):
        return [self.trigger]

    def read_unread_messages(self, _conversation):
        return [self.trigger]

    def __getattr__(self, name: str):
        if name.startswith(("send", "reply", "ding", "add_message")):
            raise AssertionError(f"service-side external write is forbidden: {name}")
        raise AttributeError(name)


class LocalPipelineOrchestrator:
    def __init__(self, store: AutoReplyStore, *, no_action: bool = False) -> None:
        self.store = store
        self.no_action = no_action

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        del context, refresh_context
        consumer_claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="local-pipeline",
        )
        consumer = ConsumerAgentResult.model_validate(
            {
                "outcome": "no_action" if self.no_action else "proposal",
                "summary": "No action required."
                if self.no_action
                else "Send the reply.",
                "proposal": None
                if self.no_action
                else {
                    "objective": "Send the reply.",
                    "actions": [
                        {
                            "description": "Send the reply.",
                            "capability": "agent_cli.dws",
                            "operation": "chat message send",
                            "target": {"conversation_id": task.conversation_id},
                            "payload": {"text": "Confirmed."},
                            "expected_verification": "Message is visible.",
                        }
                    ],
                    "sourced_facts": [],
                    "authored_judgment": "",
                },
                "error": {},
            }
        )
        self.store.set_agent_run_session(
            consumer_claim.run.id,
            "local-consumer-session",
            owner="local-pipeline",
        )
        consumer_run = self.store.complete_agent_run(
            consumer_claim.run.id,
            consumer.model_dump(mode="json"),
            owner="local-pipeline",
        )
        if self.no_action:
            return OrchestrationResult(
                status="no_action",
                final_run_id=consumer_run.id,
                final_role=AgentRole.CONSUMER,
                summary=consumer.summary,
                error=consumer.error,
                feedback_cycles=0,
                consumer_result=consumer,
            )

        operation_id = f"agent-task:{task.id}:{task.execution_generation}:proposal:0"
        audit_claim = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=consumer_run.id,
            operation_id=operation_id,
            owner="local-pipeline",
        )
        self.store.set_agent_run_session(
            audit_claim.run.id,
            "local-audit-session",
            owner="local-pipeline",
        )
        audit = AuditAgentResult.model_validate(
            {
                "outcome": "executed",
                "summary": "Reply sent and verified.",
                "proposal_revision": 0,
                "feedback": None,
                "external_result": {
                    "operation_id": operation_id,
                    "verification_summary": "Message is visible.",
                    "live_result_reference": {"message_id": "sent-1"},
                },
                "error": {},
            }
        )
        audit_run = self.store.complete_agent_run(
            audit_claim.run.id,
            audit.model_dump(mode="json"),
            owner="local-pipeline",
        )
        return OrchestrationResult(
            status="executed",
            final_run_id=audit_run.id,
            final_role=AgentRole.AUDIT,
            summary=audit.summary,
            error=audit.error,
            feedback_cycles=0,
            audit_result=audit,
        )


def _audit_pipeline(
    tmp_path,
    *,
    no_action: bool = False,
):
    store = AutoReplyStore(tmp_path / "audit-agent.sqlite3")
    trigger = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="产品群",
        single_chat=False,
        sender_name="ET",
        sender_user_id="user-et",
        create_time="2026-07-29 16:00:00",
        content="@CEO Agent 请处理",
    )
    store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        execution_generation="g1",
    )
    orchestrator = LocalPipelineOrchestrator(store, no_action=no_action)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=ContextOnlyDws(trigger),
        codex=object(),
        agent_orchestrator=orchestrator,
        channel_gates={
            "dingtalk": ReadyGate("dingtalk"),
            "lark": ReadyGate("lark"),
        },
        now_provider=lambda: datetime.fromisoformat("2026-07-29T16:01:00+08:00"),
    )
    return worker, store


def test_audit_local_pipeline_send_uses_codex_session_audit(tmp_path):
    worker, store = _audit_pipeline(tmp_path)

    assert worker.consume_once(max_tasks=1) == 1

    task = store.get_reply_task_for_message("cid-1", "msg-1")
    run = _get_audit_run(store, task.id, "g1")
    assert task.status == "done"
    assert store.list_agent_execution_receipts(run.id) == []
    assert run.codex_session_id


def test_audit_local_pipeline_handoff_uses_codex_session_audit(
    tmp_path,
):
    worker, store = _audit_pipeline(tmp_path, no_action=True)

    assert worker.consume_once(max_tasks=1) == 1

    task = store.get_reply_task_for_message("cid-1", "msg-1")
    run = store.get_agent_run_for_turn(
        task.id,
        "g1",
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
    )
    assert task.status == "done"
    assert store.list_agent_execution_receipts(run.id) == []
    assert run.codex_session_id
