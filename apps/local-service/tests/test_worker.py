from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

import ceo_agent_service.worker as worker_module
from ceo_agent_service.codex_decision import CodexDecisionRunner
from ceo_agent_service.corpus import CorpusRecord
from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from ceo_agent_service.dws_client import (
    DwsCalendarEvent,
    DwsDocumentSearchResult,
    DwsError,
    DwsMinutesPermissionRequest,
    DwsUserProfile,
)
from ceo_agent_service.oa_approval import OaApprovalResult
from ceo_agent_service.store import AutoReplyStore
from ceo_agent_service.worker import (
    HANDOFF_ACK,
    PROCESSING_ACK,
    DingTalkAutoReplyWorker,
)


CONTEXT_HEADER = "上下文消息（前 20 条 + 后续到当前）:"


def fixed_worker_now() -> datetime:
    return datetime(2026, 5, 13, 10, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeDws:
    def __init__(
        self,
        conversations: list[DingTalkConversation],
        messages: dict[str, list[DingTalkMessage]],
        unread_messages: dict[str, list[DingTalkMessage]] | None = None,
        read_errors: dict[str, Exception] | None = None,
        unread_errors: dict[str, Exception] | None = None,
        list_error: Exception | None = None,
        mentioned_error: Exception | None = None,
        send_error: Exception | None = None,
        ding_error: Exception | None = None,
        current_user_error: Exception | None = None,
        send_result: dict | None = None,
    ):
        self.conversations = conversations
        self.messages = messages
        self.unread_messages = unread_messages or messages
        self.read_errors = read_errors or {}
        self.unread_errors = unread_errors or {}
        self.list_error = list_error
        self.mentioned_error = mentioned_error
        self.send_error = send_error
        self.ding_error = ding_error
        self.current_user_error = current_user_error
        self.send_result = send_result
        self.docs: dict[str, dict] = {}
        self.doc_infos: dict[str, dict] = {}
        self.aitable_bases: dict[str, dict] = {}
        self.aitable_tables: dict[tuple[str, tuple[str, ...]], dict] = {}
        self.aitable_records: dict[tuple[str, str], dict] = {}
        self.document_search_results: dict[str, list[DwsDocumentSearchResult]] = {}
        self.download_docs: dict[str, dict | Exception] = {}
        self.doc_info_calls: list[str] = []
        self.read_doc_calls: list[str] = []
        self.get_aitable_base_calls: list[str] = []
        self.get_aitable_tables_calls: list[tuple[str, tuple[str, ...] | None]] = []
        self.query_aitable_record_calls: list[tuple[str, str, int]] = []
        self.search_document_calls: list[tuple[str, int]] = []
        self.download_doc_calls: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.reply_messages: list[tuple[str, str, str, str]] = []
        self.sent_at_users: list[list[str]] = []
        self.direct_user_ids: list[str | None] = []
        self.direct_open_dingtalk_ids: list[str | None] = []
        self.send_attempt_count = 0
        self.dings: list[str] = []
        self.mentioned_messages: dict[str, list[DingTalkMessage]] = {}
        self.broadcast_messages: dict[str, list[DingTalkMessage]] = {}
        self.user_departments: dict[str, set[str]] = {}
        self.user_profiles: dict[str, DwsUserProfile] = {}
        self.user_profile_calls: list[str] = []
        self.hr_users: set[str] = set()
        self.manager_chains: dict[str, list[str]] = {}
        self.resolved_senders: dict[str, str] = {}
        self.current_user_id = "derek-user-1"
        self.calendar_invites: dict[str, DwsCalendarEvent | None] = {}
        self.calendar_events: dict[str, list[DwsCalendarEvent]] = {}
        self.minutes_permission_requests: dict[
            str, DwsMinutesPermissionRequest | None
        ] = {}
        self.added_minutes_permissions: list[DwsMinutesPermissionRequest] = []
        self.oa_approval_actions: list[tuple[str, str, str, str]] = []
        self.oa_approval_action_result: dict = {"errcode": 0, "errmsg": "ok"}
        self.oa_approval_action_error: Exception | None = None
        self.upgrade_check_response: dict = {"needs_upgrade": False}
        self.upgrade_error: Exception | None = None
        self.upgrade_check_calls = 0
        self.upgrade_calls = 0

    def list_unread_conversations(self, count: int) -> list[DingTalkConversation]:
        assert count == 50
        if self.list_error:
            raise self.list_error
        return self.conversations

    def check_upgrade(self) -> dict:
        self.upgrade_check_calls += 1
        if self.upgrade_error:
            raise self.upgrade_error
        return self.upgrade_check_response

    def upgrade(self) -> str:
        self.upgrade_calls += 1
        if self.upgrade_error:
            raise self.upgrade_error
        return "upgraded"

    def get_current_user_id(self) -> str:
        return self.current_user_id

    def search_department_ids(self, query: str) -> set[str]:
        del query
        return {"hr-dept"}

    def list_department_member_profiles(
        self, department_ids: list[str]
    ) -> list[DwsUserProfile]:
        del department_ids
        return [
            profile
            for profile in self.user_profiles.values()
            if "hr-dept" in profile.department_ids
        ]

    def get_user_profiles(self, user_ids: list[str]) -> list[DwsUserProfile]:
        return [
            self.user_profiles.get(
                user_id,
                DwsUserProfile(
                    user_id=user_id,
                    name=user_id,
                    department_ids={"dept-1"},
                ),
            )
            for user_id in user_ids
        ]

    def read_recent_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        if conversation.open_conversation_id in self.read_errors:
            raise self.read_errors[conversation.open_conversation_id]
        return self.messages.get(conversation.open_conversation_id, [])

    def read_unread_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        if conversation.open_conversation_id in self.unread_errors:
            raise self.unread_errors[conversation.open_conversation_id]
        return self.unread_messages.get(conversation.open_conversation_id, [])

    def read_mentioned_messages(
        self,
        conversation: DingTalkConversation | None = None,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        if self.mentioned_error:
            raise self.mentioned_error
        if conversation is None:
            return [
                message
                for messages in self.mentioned_messages.values()
                for message in messages
            ]
        return self.mentioned_messages.get(conversation.open_conversation_id, [])

    def read_broadcast_messages(
        self,
        aliases: tuple[str, ...],
        limit: int = 100,
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        del aliases, limit, lookback_hours
        return [
            message
            for messages in self.broadcast_messages.values()
            for message in messages
        ]

    def read_doc(self, node: str) -> dict:
        self.read_doc_calls.append(node)
        if node not in self.docs:
            raise DwsError(f"doc not found: {node}")
        return self.docs[node]

    def doc_info(self, node: str) -> dict:
        self.doc_info_calls.append(node)
        if node in self.doc_infos:
            return self.doc_infos[node]
        if node in self.docs:
            return {
                "contentType": "ALIDOC",
                "extension": "adoc",
                "name": self.docs[node].get("title", "钉钉文档"),
                "nodeId": node.rsplit("/", 1)[-1],
            }
        raise DwsError(f"doc info not found: {node}")

    def get_aitable_base(self, base_id: str) -> dict:
        self.get_aitable_base_calls.append(base_id)
        if base_id not in self.aitable_bases:
            raise DwsError(f"aitable base not found: {base_id}")
        return self.aitable_bases[base_id]

    def get_aitable_tables(
        self, base_id: str, table_ids: list[str] | None = None
    ) -> dict:
        key = (base_id, tuple(table_ids or ()))
        self.get_aitable_tables_calls.append((base_id, tuple(table_ids) if table_ids else None))
        if key not in self.aitable_tables:
            raise DwsError(f"aitable table not found: {base_id}")
        return self.aitable_tables[key]

    def query_aitable_records(
        self, base_id: str, table_id: str, limit: int = 10
    ) -> dict:
        self.query_aitable_record_calls.append((base_id, table_id, limit))
        return self.aitable_records.get((base_id, table_id), {"data": {"records": []}})

    def search_documents(
        self, query: str, page_size: int = 5
    ) -> list[DwsDocumentSearchResult]:
        self.search_document_calls.append((query, page_size))
        return self.document_search_results.get(query, [])

    def download_doc(self, node: str) -> dict:
        self.download_doc_calls.append(node)
        result = self.download_docs.get(node)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> None:
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.sent.append((conversation_id or "", text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(user_id)
        self.direct_open_dingtalk_ids.append(open_dingtalk_id)
        return self.send_result

    def reply_message(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
    ) -> None:
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.reply_messages.append(
            (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
        )
        self.sent.append((conversation_id, text))
        self.sent_at_users.append([])
        self.direct_user_ids.append(None)
        self.direct_open_dingtalk_ids.append(None)
        return self.send_result

    def ding_self(self, text: str) -> None:
        if self.ding_error:
            raise self.ding_error
        self.dings.append(text)

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        if message.sender_user_id:
            return message.sender_user_id
        if message.sender_open_dingtalk_id in self.resolved_senders:
            return self.resolved_senders[message.sender_open_dingtalk_id]
        raise RuntimeError("sender not resolved")

    def get_user_profile(self, user_id: str) -> DwsUserProfile:
        self.user_profile_calls.append(user_id)
        if user_id not in self.user_profiles:
            raise DwsError(f"user profile not found: {user_id}")
        return self.user_profiles[user_id]

    def is_hr_user(self, user_id: str) -> bool:
        return user_id in self.hr_users

    def user_in_manager_chain(self, manager_user_id: str, subject_user_id: str) -> bool:
        return manager_user_id in self.manager_chains.get(subject_user_id, [])

    def get_user_department_ids(self, user_id: str) -> set[str]:
        if user_id not in self.user_departments:
            raise RuntimeError("department not resolved")
        return self.user_departments[user_id]

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        if self.current_user_error:
            raise self.current_user_error
        return message.sender_user_id == self.current_user_id

    def calendar_invite_from_message(
        self, message: DingTalkMessage
    ) -> DwsCalendarEvent | None:
        return self.calendar_invites.get(message.open_message_id)

    def list_calendar_events(self, start: str, end: str) -> list[DwsCalendarEvent]:
        return self.calendar_events.get(f"{start}|{end}", [])

    def minutes_permission_request_from_message(
        self, message: DingTalkMessage
    ) -> DwsMinutesPermissionRequest | None:
        return self.minutes_permission_requests.get(message.open_message_id)

    def add_minutes_member_permission(
        self, request: DwsMinutesPermissionRequest
    ) -> dict:
        self.added_minutes_permissions.append(request)
        return {"success": True}

    def execute_oa_approval_action(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ) -> dict:
        self.oa_approval_actions.append(
            (process_instance_id, task_id, action, remark)
        )
        if self.oa_approval_action_error:
            raise self.oa_approval_action_error
        return self.oa_approval_action_result


class FakeCodex:
    def __init__(
        self,
        decision: CodexDecision,
        last_session_id: str | None = None,
        next_session_id: str | None = None,
        audit_tool_events: list[dict[str, str]] | None = None,
        transcript_start_line: int = 0,
        transcript_end_line: int = 0,
        before_decide=None,
    ):
        self.decision = decision
        self.last_session_id = last_session_id
        self.next_session_id = next_session_id
        self.last_audit_tool_events = audit_tool_events or []
        self.last_transcript_start_line = transcript_start_line
        self.last_transcript_end_line = transcript_end_line
        self.before_decide = before_decide
        self.calls: list[tuple[str, str | None]] = []

    def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
        if self.before_decide is not None:
            self.before_decide(prompt, session_id)
        self.calls.append((prompt, session_id))
        if self.next_session_id is not None:
            self.last_session_id = self.next_session_id
        return self.decision


class SequencedFakeCodex:
    def __init__(self, decisions: list[CodexDecision]):
        self.decisions = decisions
        self.calls: list[tuple[str, str | None]] = []
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
        self.calls.append((prompt, session_id))
        self.last_session_id = session_id or self.last_session_id or "session-1"
        return self.decisions[len(self.calls) - 1]


class FakeOaApprovalRunner:
    def __init__(self):
        self.calls: list[tuple[str, str, str, bool]] = []
        self.last_session_id = "oa-session-1"
        self.last_transcript_start_line = 12
        self.last_transcript_end_line = 34
        self.last_audit_tool_events = [{"tool": "dws", "action": "oa_review"}]

    def handle(
        self,
        trigger_text: str,
        context_text: str,
        oa_url: str,
        execute: bool = True,
    ) -> OaApprovalResult:
        self.calls.append((trigger_text, context_text, oa_url, execute))
        return OaApprovalResult(
            process_instance_id="proc-1",
            task_id="task-1",
            oa_url=oa_url
            or "https://aflow.dingtalk.com/dingtalk/pc/query/pchomepage.htm?procInstId=proc-1&taskId=task-1",
            oa_action="退回",
            oa_remark="请补充预算来源和项目归属后重新提交。",
            action_result={},
            audit_summary="缺少预算来源和项目归属，按审批规则退回补充。",
            audit_documents=[{"title": "OA 审批单", "url": oa_url}],
        )


def final_sent(dws: FakeDws) -> list[tuple[str, str]]:
    return [sent for sent in dws.sent if sent[1] != PROCESSING_ACK]


def final_sent_at_users(dws: FakeDws) -> list[list[str]]:
    return [
        at_users
        for sent, at_users in zip(dws.sent, dws.sent_at_users)
        if sent[1] != PROCESSING_ACK
    ]


def final_direct_user_ids(dws: FakeDws) -> list[str | None]:
    return [
        user_id
        for sent, user_id in zip(dws.sent, dws.direct_user_ids)
        if sent[1] != PROCESSING_ACK
    ]


def final_direct_open_dingtalk_ids(dws: FakeDws) -> list[str | None]:
    return [
        open_dingtalk_id
        for sent, open_dingtalk_id in zip(dws.sent, dws.direct_open_dingtalk_ids)
        if sent[1] != PROCESSING_ACK
    ]


def conversation(single_chat: bool = False) -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=single_chat,
        unread_point=1,
    )


def message(
    content: str,
    message_id: str = "msg-1",
    single_chat: bool = False,
    quoted_content: str | None = None,
    sender_user_id: str | None = "sender-user-1",
    message_type: str | None = None,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="Friday",
        single_chat=single_chat,
        sender_name="周俊杰",
        sender_open_dingtalk_id="sender-1",
        sender_user_id=sender_user_id,
        message_type=message_type,
        create_time="2026-05-13 18:00:00",
        content=content,
        quoted_message_id="quoted-1" if quoted_content else None,
        quoted_content=quoted_content,
    )


def derek_message(
    content: str,
    message_id: str = "derek-msg-1",
    create_time: str = "2026-05-13 18:00:01",
) -> DingTalkMessage:
    msg = message(
        content=content,
        message_id=message_id,
        sender_user_id="derek-user-1",
    )
    msg.create_time = create_time
    return msg


def make_worker(
    tmp_path: Path,
    dws: FakeDws,
    codex: FakeCodex,
    monkeypatch,
    style_profile: str = "",
    style_records: list[CorpusRecord] | None = None,
    dry_run: bool = False,
    max_task_attempts: int = 3,
    oa_approval_runner=None,
) -> DingTalkAutoReplyWorker:
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("derek-user-1")
    return DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=dry_run,
        style_profile=style_profile,
        style_records=style_records,
        now_provider=fixed_worker_now,
        max_task_attempts=max_task_attempts,
        oa_approval_runner=oa_approval_runner,
    )


def developer_instructions_from_command(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return json.loads(value.split("=", 1)[1])
    raise AssertionError("developer_instructions config missing")


def write_profile_for_consumer_test(tmp_path: Path, monkeypatch) -> str:
    profile = tmp_path / "profiles" / "derek_work_profile.md"
    content = """# Derek Work Profile

## 核心心智模型

### 模型1: 结果闭环高于动作勤奋

**一句话**：不要基于一句话拍板，先看材料是否完整、结果是否可验证。

## 决策启发式

1. **材料不完整时先追问，不拍板**：审批、候选人、客户、方案、PPT、预算缺正文或附件时，不给最终判断。
   - 应用场景：审批、招聘、客户材料、文档 review、最终版确认。
   - 案例：需要本人确认最终版或审批时，分身只 handoff，不代替承诺。

## 表达DNA

- 节奏：先给结论，再给原因和下一步；材料不足时直接收敛到一个追问。

## 诚实边界

- 不替 Derek 做最终人事、审批、财务、法律或客户关键承诺。
- 不声称 Derek 已经做了现实动作。
- 材料不足时不编造结论。
"""
    profile.parent.mkdir(parents=True)
    profile.write_text(content, encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    return content


def test_consumer_codex_command_embeds_work_profile_content(
    tmp_path: Path, monkeypatch
):
    profile_content = write_profile_for_consumer_test(tmp_path, monkeypatch)
    seen_instructions = []

    def executor(command: list[str], prompt: str) -> str:
        seen_instructions.append(developer_instructions_from_command(command))
        return json.dumps(
            {
                "action": "ask_clarifying_question",
                "reply_text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                "reason": "profile says incomplete materials require clarification",
                "audit_summary": "仅根据当前消息判断，材料不足，需要追问。",
            },
            ensure_ascii=False,
        )

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个候选人可以推进吗？")]},
    )
    codex = CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        codex_home=tmp_path,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(seen_instructions) == 1
    instructions = seen_instructions[0]
    assert "Derek 工作人格 Profile" in instructions
    assert "Profile 内容:" in instructions
    assert profile_content in instructions
    assert "材料不完整时先追问，不拍板" in instructions
    assert final_sent(dws)


def test_consumer_uses_profile_to_ask_for_missing_candidate_materials(
    tmp_path: Path, monkeypatch
):
    write_profile_for_consumer_test(tmp_path, monkeypatch)

    def executor(command: list[str], prompt: str) -> str:
        instructions = developer_instructions_from_command(command)
        assert "材料不完整时先追问，不拍板" in instructions
        assert "这个候选人可以推进吗" in prompt
        return json.dumps(
            {
                "action": "ask_clarifying_question",
                "reply_text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                "reason": "candidate judgment lacks role and resume materials",
                "audit_summary": "仅根据当前消息判断，缺少岗位要求和简历内容，按 profile 先追问材料。",
            },
            ensure_ascii=False,
        )

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个候选人可以推进吗？")]},
    )
    codex = CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        codex_home=tmp_path,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    sent = final_sent(dws)
    assert len(sent) == 1
    assert "先把岗位要求和候选人简历补齐" in sent[0][1]
    assert "可以推进。（by" not in sent[0][1]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == CodexAction.ASK_CLARIFYING_QUESTION.value
    assert "按 profile 先追问材料" in attempt.audit_summary


def test_group_without_derek_mention_does_not_call_codex_or_send(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([conversation()], {"cid-1": [message("同步一下进展")]})
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_produce_once_records_list_unread_failure_without_crashing(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.count_errors() == 1
    assert notifications == [
        {
            "title": "CEO read unread conversations failed",
            "message": "not authenticated",
            "url": None,
        }
    ]
    assert codex.calls == []


def test_produce_once_continues_when_mention_recovery_fails(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        mentioned_error=DwsError("list mentions failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_errors() == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert notifications == [
        {
            "title": "CEO read mentioned messages failed",
            "message": "list mentions failed",
            "url": None,
        }
    ]
    assert codex.calls == []


def test_produce_once_enqueues_candidate_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_does_not_send_processing_ack_for_new_reply_task(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert codex.calls == []
    assert dws.sent == []


def test_produce_once_checks_dws_upgrade_once_per_local_day(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {})
    dws.upgrade_check_response = {
        "current_version": "v1.0.26",
        "latest_version": "v1.0.32",
        "needs_upgrade": True,
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert dws.upgrade_check_calls == 1
    assert dws.upgrade_calls == 1
    assert worker.store.get_service_state("dws_upgrade_checked_date") == "2026-05-13"


def test_produce_once_records_dws_upgrade_failure_without_blocking_messages(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.upgrade_error = RuntimeError("upgrade service unavailable")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert dws.upgrade_check_calls == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    errors = worker.store.list_errors()
    assert len(errors) == 1
    assert errors[0].kind == "dws_upgrade"
    assert "upgrade service unavailable" in errors[0].detail
    assert worker.store.get_service_state("dws_upgrade_checked_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_once_per_seven_days(
    tmp_path: Path, monkeypatch
):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert calls[0][1] is dws
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_after_seven_days(
    tmp_path: Path, monkeypatch
):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state("org_cache_refreshed_date", "2026-05-06")

    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_when_refresh_date_is_invalid(
    tmp_path: Path, monkeypatch
):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state("org_cache_refreshed_date", "invalid")

    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_records_org_cache_refresh_failure_without_blocking_messages(
    tmp_path: Path, monkeypatch
):
    def fake_refresh_org_cache(store, dws):
        raise RuntimeError("contact service unavailable")

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    errors = worker.store.list_errors()
    assert len(errors) == 1
    assert errors[0].kind == "org_cache_refresh"
    assert "contact service unavailable" in errors[0].detail
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_skips_messages_older_than_local_24_hour_window(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个旧消息不用处理？")
    trigger.create_time = "2026-05-13 00:59:59"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 0
    assert codex.calls == []
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.has_seen("msg-1") is True


def test_produce_once_uses_beijing_message_time_against_local_24_hour_window(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个消息还在24小时内？")
    trigger.create_time = "2026-05-13 01:00:00"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_repeated_produce_once_does_not_send_processing_ack(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert dws.sent == []


def test_consume_once_does_not_send_processing_ack(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    def before_decide(prompt, _session_id):
        assert PROCESSING_ACK not in prompt
        assert dws.sent == []

    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走"),
        before_decide=before_decide,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert dws.sent == [("cid-1", "先按A方案走（by磊哥分身）")]


def test_repeated_produce_once_does_not_duplicate_pending_task(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert codex.calls == []


def test_produce_once_uses_recent_context_when_unread_read_fails_for_group_mention(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        unread_messages={"cid-1": []},
        unread_errors={
            "cid-1": DwsError(
                "business error: SECURITY_CHECK_INVOKE_FAILED",
                code="SECURITY_CHECK_INVOKE_FAILED",
            )
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_errors() == 1
    assert notifications[0]["title"] == "CEO read unread messages failed: Friday"
    assert codex.calls == []


def test_consume_once_processes_queued_task(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert worker.store.count_reply_tasks(status="done") == 1
    assert final_sent(dws) == [("cid-1", "先按A方案走（by磊哥分身）")]


def test_consume_once_retries_task_failure_before_final_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()
    dws.read_errors["cid-1"] = RuntimeError("temporary dws auth failure")

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_errors() == 2
    assert [notification["title"] for notification in notifications] == [
        "CEO task failed: Friday"
    ]


def test_consume_once_authorization_failure_waits_without_final_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_error=DwsError(
            "PAT_HIGH_RISK_NO_PERMISSION authorization required",
            code="PAT_HIGH_RISK_NO_PERMISSION",
        ),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert any(
        notification["title"] == "CEO task waiting for authorization: Friday"
        for notification in notifications
    )
    assert not any(
        notification["title"] == "CEO task failed: Friday"
        for notification in notifications
    )
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        attempts = db.execute("select attempts from reply_tasks").fetchone()[0]
    assert attempts == 0


def test_unresolvable_non_candidate_sender_does_not_block_conversation(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("OA审批通知", sender_user_id=None)]},
        current_user_error=RuntimeError("sender not resolved"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_errors() == 0


def test_single_chat_rendered_schedule_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN, reason="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert dws.dings == []
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "system_or_notification_message"


def test_non_text_message_type_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message("日程卡片", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_calendar_invite_without_description_asks_for_attendance_reason(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="",
        organizer="Mina",
    )
    existing = DwsCalendarEvent(
        event_id="event-1",
        title="产品周会",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="固定例会",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite, existing]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert len(final_sent(dws)) == 1
    assert "客户复盘" in final_sent(dws)[0][1]
    assert "产品周会" in final_sent(dws)[0][1]
    assert "请补充" in final_sent(dws)[0][1]
    assert "参加理由" in final_sent(dws)[0][1]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.send_status == "sent"


def test_calendar_invite_with_description_asks_codex_to_evaluate_conflict(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户升级问题决策",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="需要 Derek 判断是否承诺本周交付，客户 CEO 会参加。",
        organizer="Mina",
    )
    existing = DwsCalendarEvent(
        event_id="event-1",
        title="产品周会",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="固定例会",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite, existing]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个会议和产品周会冲突。按描述看客户升级问题优先级更高，建议接受这场并请产品周会另约。",
            reason="calendar_conflict_evaluated",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "日历冲突检查" in prompt
    assert "客户升级问题决策" in prompt
    assert "产品周会" in prompt
    assert "如果说明不足以取消另一个重叠会议" in prompt
    assert len(final_sent(dws)) == 1
    assert "客户升级问题优先级更高" in final_sent(dws)[0][1]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "calendar_conflict_evaluated"


def test_structured_link_card_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "\n".join(
            [
                "表单标题",
                "字段一: A",
                "字段二: B",
                "字段三: C",
                "字段四: D",
                "[dingtalk://dingtalkclient/action/open_platform_link?x=1](dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_structured_approval_card_is_processed_by_oa_runner(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "\n".join(
            [
                "闫成成提交的项目立项全流程（第一曲线）",
                "项目经理: 闫成成",
                "销售经理: 曹宇航",
                "项目类型: 点云;图片;视频",
                "总预估数据量: 2546573",
                "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
                "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
                "%2Fpchomepage.htm%3Fswfrom%3Doa%26dinghash%3Dapproval]"
                "(dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="审批需要本人处理",
            audit_summary="结构化 OA 卡片需要按审批审阅原则处理。",
        )
    )
    oa_runner = FakeOaApprovalRunner()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_runner=oa_runner,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_runner.calls) == 1
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.sensitivity_kind == "internal_personnel"
    assert attempt.send_status == "skipped"
    assert attempt.draft_reply_text == "请补充预算来源和项目归属后重新提交。"
    assert attempt.final_reply_text == "请补充预算来源和项目归属后重新提交。"
    assert attempt.audit_summary == "缺少预算来源和项目归属，按审批规则退回补充。"
    assert json.loads(attempt.audit_documents_json) == [
        {"title": "OA 审批单", "url": attempt.oa_url}
    ]
    assert json.loads(attempt.audit_tool_events_json) == [
        {"tool": "dws", "action": "oa_review"}
    ]
    assert attempt.codex_session_id == "oa-session-1"
    assert attempt.codex_transcript_start_line == 12
    assert attempt.codex_transcript_end_line == 34
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url.startswith("https://aflow.dingtalk.com/")
    assert attempt.oa_action == "退回"
    assert attempt.oa_remark == "请补充预算来源和项目归属后重新提交。"
    assert oa_runner.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "退回",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert json.loads(attempt.oa_action_result_json) == {
        "errcode": 0,
        "errmsg": "ok",
    }


def test_automatic_sync_notification_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("AI 自动同步成功：董事会筹备组纪要", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_file_state_notification_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message("文档已更新：董事会材料", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_project_status_notification_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("项目立项已提交", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_status_like_message_with_followup_request_is_processed_by_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("文件已更新，帮忙看一下", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="带请求的文件状态消息需要交给 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1


def test_question_with_link_still_goes_to_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "这个链接里的方案怎么看？ https://example.com/a", single_chat=True
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1


def test_bare_external_link_is_processed_by_codex(tmp_path: Path, monkeypatch):
    trigger = message("@磊哥 https://example.com/a", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="普通外链需要交给 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_bare_dingtalk_internal_link_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@磊哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?x=1)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "no_reply"


def test_ai_minutes_permission_request_is_auto_approved_without_codex_or_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId=minutes-1&from=8]",
        single_chat=True,
    )
    request = DwsMinutesPermissionRequest(
        uuids=["minutes-1"],
        member_uids=[451416406],
        policy_id=3,
        role_sub_resource_ids=["OrigContent", "Summary"],
        cover_permission=False,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_permission_requests["msg-1"] = request
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert dws.added_minutes_permissions == [request]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "ai_minutes_permission_auto_approved"
    assert "已自动通过 AI 听记权限申请" in attempt.audit_summary


def test_ding_approval_reminder_is_processed_by_oa_runner(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]张静提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="审批需要本人处理",
            audit_summary="审批催办需要按 OA 审阅原则处理。",
        )
    )
    oa_runner = FakeOaApprovalRunner()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_runner=oa_runner,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_runner.calls) == 1
    assert oa_runner.calls[0][2] == ""
    assert oa_runner.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "退回",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 1
    assert worker.store.get_reply_attempt(1).action == "oa_approval"


def test_oa_approval_dry_run_uses_review_only_mode_and_keeps_live_retry_open(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]张静提醒您审批他的录用申请 https://aflow.dingtalk.com/dingtalk/pc/query"
        "/pchomepage.htm?procInstId=proc-1&taskId=task-1&swfrom=oa",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_runner = FakeOaApprovalRunner()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=True,
        oa_approval_runner=oa_runner,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_runner.calls) == 1
    assert oa_runner.calls[0][3] is False
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "dry_run"
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="done") == 0

    live_runner = FakeOaApprovalRunner()
    live_worker = DingTalkAutoReplyWorker(
        store=worker.store,
        dws=dws,
        codex=codex,
        dry_run=False,
        now_provider=fixed_worker_now,
        oa_approval_runner=live_runner,
    )

    live_worker.run_once()

    assert len(live_runner.calls) == 1
    assert live_runner.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "退回",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 2
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="done") == 1
    live_attempt = worker.store.get_reply_attempt(2)
    assert live_attempt is not None
    assert live_attempt.action == "oa_approval"
    assert live_attempt.send_status == "skipped"


def test_bare_dingtalk_approval_wrapper_is_not_skipped_before_oa_runner(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
        "%2Fpchomepage.htm%3Fswfrom%3Doa%26dinghash%3Dapproval]"
        "(dingtalk://dingtalkclient/action/open_platform_link?x=1)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_runner = FakeOaApprovalRunner()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_runner=oa_runner,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_runner.calls) == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"


def test_group_mention_sends_signed_reply(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Derek Zen(磊哥) @晓民 这个怎么处理？",
        quoted_content="这个ACL表看一下",
    )
    trigger.mentioned_user_ids = ["derek-user-1", "mentioned-user-1"]
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                trigger,
                message("前面上下文", message_id="msg-0"),
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "cid-1",
            "先按A方案走（by磊哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [[]]
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "sender-1",
            "先按A方案走（by磊哥分身）",
        )
    ]
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert prompt.startswith("当前待处理消息:")
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Derek 的钉钉自动回复分身" not in prompt
    assert "会话: Friday" in prompt
    assert "@Derek Zen(磊哥) @晓民 这个怎么处理？" in prompt
    assert "引用: 这个ACL表看一下" in prompt
    assert "前面上下文" in prompt


def test_success_notification_keeps_full_reply_text(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["derek-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reply_body = "我倾向于按这个方向收敛：" + "先看行业经验和交付闭环，" * 12
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text=reply_body)
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert len(notifications[0]["message"]) > 120
    assert notifications == [
        {
            "title": "CEO auto reply: Friday",
            "message": final_sent(dws)[0][1],
            "url": None,
        }
    ]


def test_leak_check_feedback_regenerates_reply_before_blocking(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    trigger.mentioned_user_ids = ["derek-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="参考 [1]，先按A方案推进",
                audit_summary="只需上下文判断，当前消息已足够确认。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="先按A方案推进",
                audit_summary="收到安全反馈后，改写为不带来源引用的回复。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert len(codex.calls) == 2
    assert codex.calls[1][1] == "session-1"
    assert "发送安全检查拦截" in codex.calls[1][0]
    assert "不要引用来源" in codex.calls[1][0]
    assert worker.store.count_errors() == 0
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"
    assert attempt.send_error == ""
    assert "参考 [1]" not in attempt.final_reply_text
    assert "先按A方案推进" in attempt.final_reply_text


def test_dingtalk_doc_link_is_read_before_codex(tmp_path: Path, monkeypatch):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    trigger = message(f"{doc_url} @Derek Zen(磊哥) 看下根因和解法")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.docs[canonical_doc_url] = {
        "title": "数据导入导出业务低效根因和最终解法",
        "markdown": (
            "核心结论：根因是协作方式不对。\n"
            "客户业务逻辑、项目配置确认、数据工具排查被混在一起。"
        ),
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="按协作方式拆分")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.read_doc_calls == [canonical_doc_url]
    assert final_sent(dws) == []
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "数据导入导出业务低效根因和最终解法" in prompt
    assert "根因是协作方式不对" in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"


def test_dingtalk_aitable_link_is_routed_to_aitable_before_codex(
    tmp_path: Path, monkeypatch
):
    aitable_url = "https://alidocs.dingtalk.com/i/nodes/base123?utm_source=im"
    canonical_url = "https://alidocs.dingtalk.com/i/nodes/base123"
    trigger = message(f"{aitable_url} @Derek Zen(磊哥) 看下进展")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[canonical_url] = {
        "contentType": "ALIDOC",
        "extension": "able",
        "name": "算法迭代看板",
        "nodeId": "base123",
    }
    dws.aitable_bases["base123"] = {
        "data": {
            "baseName": "算法迭代看板",
            "tables": [{"tableId": "tbl-1", "tableName": "算法优化看板"}],
        }
    }
    dws.aitable_tables[("base123", ("tbl-1",))] = {
        "data": {
            "tables": [
                {
                    "tableId": "tbl-1",
                    "tableName": "算法优化看板",
                    "description": "用于跟踪算法优化项目进展。",
                    "fields": [
                        {"fieldId": "name", "fieldName": "迭代名称"},
                        {"fieldId": "status", "fieldName": "优化状态"},
                        {"fieldId": "plan", "fieldName": "迭代方案"},
                    ],
                }
            ]
        }
    }
    dws.aitable_records[("base123", "tbl-1")] = {
        "data": {
            "records": [
                {
                    "recordId": "rec-1",
                    "cells": {
                        "name": "关系排序优化",
                        "status": {"name": "待验证"},
                        "plan": "移除端点重合加分。",
                    },
                }
            ]
        }
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="优先验证关系排序")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == [canonical_url]
    assert dws.read_doc_calls == []
    assert dws.get_aitable_base_calls == ["base123"]
    assert dws.get_aitable_tables_calls == [("base123", ("tbl-1",))]
    assert dws.query_aitable_record_calls == [("base123", "tbl-1", 10)]
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "AI表格: 算法迭代看板" in prompt
    assert "数据表: 算法优化看板" in prompt
    assert "迭代名称: 关系排序优化" in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"


def test_dingtalk_doc_link_in_context_is_read_before_codex(tmp_path: Path, monkeypatch):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context"
    context_doc = message(
        f"[文档] 方案: {doc_url}",
        message_id="doc-msg-1",
    )
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        message_id="msg-2",
        quoted_content=f"[文档] 方案: {doc_url}",
    )
    dws = FakeDws([conversation()], {"cid-1": [context_doc, trigger]})
    dws.docs[canonical_doc_url] = {
        "title": "推进方案",
        "markdown": "下一步建议：先做客户需求收敛，再做交付排期。",
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先收敛需求")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.read_doc_calls == [canonical_doc_url]
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "下一步建议：先做客户需求收敛" in prompt


def test_referenced_file_message_is_located_before_codex(tmp_path: Path, monkeypatch):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        message_id="msg-2",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    trigger.quoted_message_id = "file-msg-1"
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    dws.document_search_results["02_下一步推进建议.md"] = [
        DwsDocumentSearchResult(
            node_id="node-1",
            name="02_下一步推进建议",
            extension="md",
            content_type="OTHER",
            node_type="file",
            doc_url="https://alidocs.dingtalk.com/i/nodes/node-1",
        )
    ]
    dws.download_docs["node-1"] = {
        "markdown": "建议正文：先明确客户边界，再补 owner 和时间表。"
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.search_document_calls == [("02_下一步推进建议.md", 5)]
    assert dws.download_doc_calls == ["node-1"]
    prompt = codex.calls[0][0]
    assert "已获取的钉钉材料:" in prompt
    assert "建议正文：先明确客户边界" in prompt
    assert "dws doc download" not in prompt


def test_referenced_file_metadata_does_not_expose_download_credentials(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.document_search_results["02_下一步推进建议.md"] = [
        DwsDocumentSearchResult(
            node_id="node-1",
            name="02_下一步推进建议",
            extension="md",
            content_type="OTHER",
            node_type="file",
            doc_url="https://alidocs.dingtalk.com/i/nodes/node-1",
        )
    ]
    dws.download_docs["node-1"] = {
        "markdown": "文件正文：这里是可审阅内容。",
        "resourceUrl": "https://signed.example/download?authorizationUrl=secret",
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在只能看到文件名，麻烦贴一下正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    prompt = codex.calls[0][0]
    assert dws.download_doc_calls == ["node-1"]
    assert "文件正文：这里是可审阅内容。" in prompt
    assert "authorizationUrl" not in prompt


def test_dingtalk_doc_read_failure_blocks_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/missing @Derek Zen(磊哥) 看下"
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos["https://alidocs.dingtalk.com/i/nodes/missing"] = {
        "contentType": "ALIDOC",
        "extension": "adoc",
        "name": "缺失文档",
        "nodeId": "missing",
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.read_doc_calls == ["https://alidocs.dingtalk.com/i/nodes/missing"]
    assert codex.calls == []
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "failed"
    assert "linked_dingtalk_doc_read_failed" in attempt.send_error


def test_codex_stop_with_error_sends_macos_notification(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec failed",
            macos_notify=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "failed"
    assert notifications[0] == {
        "title": "CEO agent error: Friday",
        "message": "codex exec failed",
        "url": None,
    }


def test_codex_stop_with_error_keeps_queued_task_retryable(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec timed out after 300 seconds",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **_: None,
    )

    worker.run_once()

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    retried = worker.store.claim_reply_tasks(limit=1)
    assert retried[0].attempts == 2
    assert "codex exec timed out" in retried[0].error
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"


def test_long_trigger_quote_is_capped_by_twenty_information_units(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 如果是私有化的POC都是走产研评估流程的，如果是VOC需求也都是PRD评审后走我们正常Sprint迭代流程的",
    )

    sent_text = DingTalkAutoReplyWorker._format_reply_text(
        trigger,
        "流程方向没问题（by磊哥分身）",
        ["sender-user-1"],
    )

    quote, reply = sent_text.split("\n\n", 1)
    assert quote == "> 周俊杰: 如果是私有化的POC都是走产研评估流程的，如果..."
    assert reply == "<@sender-user-1> 流程方向没问题（by磊哥分身）"


def test_resume_prompt_only_includes_turn_message_without_repeating_thread_prompt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    worker.run_once()

    prompt, session_id = codex.calls[0]
    assert session_id == "session-1"
    assert "当前待处理消息" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Derek 的钉钉自动回复分身" not in prompt
    assert "回答任何问题前，先检索本地 workspace" not in prompt
    assert "graphify query" not in prompt
    assert "@Derek Zen(磊哥) 这个怎么处理？" in prompt


def test_stale_codex_resume_retries_same_thread_before_opening_new_thread(
    tmp_path: Path, monkeypatch
):
    class SequencedCodex:
        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []
            self.last_session_id: str | None = None
            self.last_audit_tool_events: list[dict[str, str]] = []
            self.last_transcript_start_line = 0
            self.last_transcript_end_line = 0

        def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
            self.calls.append((prompt, session_id))
            self.last_session_id = session_id
            if len(self.calls) == 1:
                return CodexDecision(
                    action=CodexAction.STOP_WITH_ERROR,
                    reason=(
                        "thread/resume failed: no rollout found for thread id "
                        "session-1 (code -32600)"
                    ),
                )
            return CodexDecision(
                action=CodexAction.NO_REPLY,
                reason="already handled",
                audit_summary="只需上下文判断，不需要回复。",
            )

    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedCodex()
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    worker.run_once()

    assert [session_id for _, session_id in codex.calls] == [
        "session-1",
        "session-1",
    ]
    assert codex.calls[1][0] == codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[0][0]
    assert "你是 Derek 的钉钉自动回复分身" not in codex.calls[0][0]
    assert worker.store.get_codex_session_id("cid-1") == "session-1"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert "thread/resume failed" not in attempt.codex_reason
    assert worker.store.count_errors() == 0


@pytest.mark.parametrize(
    "stale_reason",
    [
        "thread/resume failed: no rollout found for thread id session-1 (code -32600)",
        (
            "2026-05-27T02:03:54.663595Z ERROR codex_rollout::list: "
            "state db returned stale rollout path for thread session-1: "
            "/Users/derek/.codex/sessions/2026/05/18/rollout-session-1.jsonl"
        ),
    ],
)
def test_stale_codex_resume_clears_session_and_retries_with_new_user_message(
    tmp_path: Path, monkeypatch, stale_reason: str
):
    class SequencedCodex:
        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []
            self.last_session_id: str | None = None
            self.last_audit_tool_events: list[dict[str, str]] = []
            self.last_transcript_start_line = 0
            self.last_transcript_end_line = 0

        def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
            self.calls.append((prompt, session_id))
            self.last_session_id = session_id
            if len(self.calls) <= 2:
                return CodexDecision(
                    action=CodexAction.STOP_WITH_ERROR,
                    reason=stale_reason,
                )
            self.last_session_id = "session-2"
            return CodexDecision(
                action=CodexAction.NO_REPLY,
                reason="already handled",
                audit_summary="只需上下文判断，不需要回复。",
            )

    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedCodex()
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    worker.run_once()

    assert [session_id for _, session_id in codex.calls] == [
        "session-1",
        "session-1",
        None,
    ]
    assert codex.calls[1][0] == codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[2][0]
    assert codex.calls[2][0].startswith("当前待处理消息:")
    assert worker.store.get_codex_session_id("cid-1") == "session-2"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert "thread/resume failed" not in attempt.codex_reason
    assert worker.store.count_errors() == 0


def test_sent_reply_records_recall_key_from_send_result(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.recall_key == "key-1"
    assert '"processQueryKey": "key-1"' in sent_reply.send_result_json


def test_existing_dry_run_attempt_does_not_call_codex_again(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="dry_run",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text="> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）",
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 1


def test_failed_send_retries_existing_final_reply_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    final_reply = (
        "> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == [("cid-1", "先按A方案走（by磊哥分身）")]
    assert final_sent_at_users(dws) == [[]]
    assert dws.reply_messages == [
        ("cid-1", "msg-1", "sender-1", "先按A方案走（by磊哥分身）")
    ]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert worker.store.get_sent_reply("cid-1", "msg-1") is not None
    assert worker.store.has_seen("msg-1") is True


def test_sent_reply_prevents_retry_when_latest_attempt_failed(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )
    failed_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="stop_with_error",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        failed_attempt_id,
        send_error="linked document read failed",
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 1
    assert worker.store.has_seen("msg-1") is True


def test_rerun_message_retries_existing_failed_attempt_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    final_reply = (
        "> 周俊杰: @Derek Zen(磊哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by磊哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert codex.calls == []
    assert final_sent(dws) == [("cid-1", "先按A方案走（by磊哥分身）")]
    assert worker.store.get_reply_attempt(attempt_id).send_status == "sent"


def test_rerun_message_cleans_legacy_group_reply_wrappers(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=(
            "> 周俊杰: 这个怎么处理？\n\n"
            "<@sender-user-1> 先按A方案走（by磊哥分身）"
        ),
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert codex.calls == []
    assert final_sent(dws) == [("cid-1", "先按A方案走（by磊哥分身）")]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.final_reply_text == "先按A方案走（by磊哥分身）"
    assert attempt.send_status == "sent"


def test_rerun_message_can_force_new_codex_decision(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    old_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert len(codex.calls) == 1
    assert worker.store.count_reply_attempts() == 2
    assert worker.store.get_reply_attempt(old_attempt_id).send_status == "sent"
    assert final_sent(dws) == [
        (
            "cid-1",
            "改走B方案（by磊哥分身）",
        )
    ]


def test_force_new_rerun_starts_fresh_codex_session(tmp_path: Path, monkeypatch):
    trigger = message("@Derek Zen(磊哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", False, "old-session")

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert codex.calls[0][1] is None
    assert codex.calls[0][0].startswith("当前待处理消息:")
    assert "你是 Derek 的钉钉自动回复分身" not in codex.calls[0][0]


def test_reply_attempt_records_codex_audit_fields(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个候选人是否推进？")]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先补岗位画像和简历再判断",
            audit_documents=[
                {
                    "path": "面试/项目经理/岗位画像.md",
                    "title": "项目经理岗位画像",
                    "relevance": "判断候选人是否匹配",
                }
            ],
            audit_summary="缺少简历内容，因此要求补齐材料后再判断。",
        ),
        audit_tool_events=[
            {
                "tool": "exec_command",
                "command": "rg -n 岗位 /Users/derek/Documents/memory/面试",
            }
        ],
        next_session_id="session-1",
        transcript_start_line=4,
        transcript_end_line=12,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert "项目经理/岗位画像.md" in attempt.audit_documents_json
    assert "rg -n 岗位" in attempt.audit_tool_events_json
    assert attempt.audit_summary == "缺少简历内容，因此要求补齐材料后再判断。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 4
    assert attempt.codex_transcript_end_line == 12


def test_prompt_includes_style_profile_and_similar_corpus_examples(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个项目排期怎么处理？")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    style_records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="项目排期要不要改",
            derek_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
            message_id="style-1",
            conversation_id="cid-style-1",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="HR",
            timestamp="2026-05-13",
            context="候选人怎么样",
            derek_reply="先看岗位匹配，再看负责范围和是否真正承担过结果。",
            message_id="style-2",
            conversation_id="cid-style-2",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="技术部",
            timestamp="2026-05-13",
            context="项目排期风险",
            derek_reply="先把风险拆成产品、算法和交付三类，每类只留一个负责人和一个截止时间。",
            message_id="style-3",
            conversation_id="cid-style-3",
            speaker_name="磊哥",
            metadata_json="{}",
        ),
    ]
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        style_profile="# Derek Style Profile\n- 先结论，再解释原因。",
        style_records=style_records,
    )

    worker.run_once()

    prompt = codex.calls[0][0]
    assert "Derek 语气规则:" in prompt
    assert "- 先结论，再解释原因。" in prompt
    assert "相似历史回复风格例子" in prompt
    assert "只学习语气、判断顺序和句式结构" in prompt
    assert "不要复用例子里的事实、人名、项目名、客户名、数字或结论" in prompt
    assert "先定优先级，再确认谁负责、什么时候交付、怎么验收。" in prompt
    assert prompt.count("- 例") == 2
    assert "先看岗位匹配" not in prompt
    assert "cid-style-1" not in prompt
    assert "Friday" in prompt


def test_prompt_includes_similar_human_feedback_examples(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {
            "cid-1": [
                message(
                    "磊哥，这个本地工具我跑通过，你先安装试试。",
                    single_chat=True,
                )
            ]
        },
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Mina 邹",
        trigger_message_id="msg-old",
        trigger_sender="Mina 邹",
        trigger_text="你先安装试试这个本地工具",
        action="handoff_to_human",
        sensitivity_kind="general",
        codex_reason="要求 Derek 安装本地工具，应交给本人。",
    )
    worker.store.record_reply_feedback(
        attempt_id,
        feedback=(
            "这类请求不要直接交给本人；先推动可交接动作，要求对方先提交代码或整理材料。"
        ),
        corrected_reply_text="你把代码提交一下，然后代码提交了，就可以让别人帮你看了",
    )

    worker.run_once()

    prompt = codex.calls[0][0]
    assert "相似人工纠偏样本" in prompt
    assert "优先学习 Derek 对错误回复的修正方向" in prompt
    assert "不要直接交给本人" in prompt
    assert "你把代码提交一下" in prompt
    assert "msg-old" not in prompt
    assert "cid-old" not in prompt


def test_group_name_reference_without_direct_at_does_not_queue(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@张晓民(Xiaomin张晓民) 这个和磊哥预期一致")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_algorithm_owner_multi_mention_is_framed_as_derek_responsibility(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                message(
                    "@ET(张毅倜(ET)) @Derek Zen(磊哥) "
                    "aijam是否可以把算法大神们纳入进来？",
                    message_id="msg-algo-owner",
                )
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="可以，算法这边应该参与"
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [("cid-1", "可以，算法这边应该参与（by磊哥分身）")]
    prompt = codex.calls[0][0]
    assert "aijam是否可以把算法大神们纳入进来？" in prompt
    assert prompt.startswith("当前待处理消息:")


def test_group_direct_mention_found_in_recent_context_is_queued(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    old_direct_mention.create_time = "2026-05-15 18:34:47"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert final_sent(dws) == [("cid-1", "我看一下（by磊哥分身）")]


def test_group_seen_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.mark_seen("msg-old", "cid-1")

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_prompt_context_limits_after_sorting_reverse_chronological_history():
    messages = []
    for index in range(25):
        item = message(f"history {index}", message_id=f"msg-{index}")
        item.create_time = f"2026-05-13 18:{index:02d}:00"
        messages.append(item)
    reverse_chronological = list(reversed(messages))

    context = DingTalkAutoReplyWorker._prompt_context_messages(
        reverse_chronological,
        [],
        previous_limit=20,
    )

    assert [item.open_message_id for item in context] == [
        f"msg-{index}" for index in range(5, 25)
    ]


def test_build_prompt_includes_known_people_from_org_cache(tmp_path: Path, monkeypatch):
    dws = FakeDws([conversation(single_chat=True)], {})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_org_user_profile(
        user_id="subject-user-1",
        name="张晓民",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids=set(),
    )
    trigger = message(
        "磊哥，晓民的转正时间快到了。",
        single_chat=True,
        message_id="msg-personnel",
    )

    prompt = worker._build_prompt(
        conversation(single_chat=True),
        [trigger],
        [trigger],
    )

    assert "- 张晓民: user_id=subject-user-1" in prompt


def test_build_prompt_includes_sender_org_context(tmp_path: Path, monkeypatch):
    dws = FakeDws([conversation(single_chat=True)], {})
    dws.user_profiles["sender-user-1"] = DwsUserProfile(
        user_id="sender-user-1",
        name="Mina 邹",
        title="首席人力资源专家兼HRVP",
        manager_name="Derek Zen",
        manager_user_id="derek-user-1",
        department_ids={"dept-hr", "dept-recruiting"},
        department_names={"人力资源部", "招聘组"},
        org_labels=["职务: HR负责人", "岗位: 管理层"],
        has_subordinate=True,
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_org_user_profile(
        user_id="derek-user-1",
        name="Derek Zen",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids={"dept-exec"},
    )
    trigger = message(
        "磊哥，晓民的转正时间快到了。",
        single_chat=True,
        message_id="msg-personnel",
        sender_user_id="sender-user-1",
    )

    prompt = worker._build_prompt(
        conversation(single_chat=True),
        [trigger],
        [trigger],
    )

    assert dws.user_profile_calls == ["sender-user-1"]
    assert "发信人组织信息" in prompt
    assert "- Mina 邹 user_id=sender-user-1" in prompt
    assert "职位/标签: 首席人力资源专家兼HRVP; 职务: HR负责人; 岗位: 管理层" in prompt
    assert "上级: Derek Zen user_id=derek-user-1" in prompt
    assert "部门: 人力资源部, 招聘组 [ids: dept-hr, dept-recruiting]" in prompt
    assert "有下属: 是" in prompt


def test_group_stale_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    stale_direct_mention = message(
        "@Derek Zen(磊哥) 旧消息看一下",
        message_id="msg-old",
    )
    stale_direct_mention.create_time = "2026-04-30 17:34:59"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                stale_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_single_chat_old_candidate_context_does_not_become_new_question(
    tmp_path: Path, monkeypatch
):
    old_candidate_context = message(
        "这个候选人怎么样？",
        message_id="msg-old-candidate",
        single_chat=True,
    )
    old_candidate_context.create_time = "2026-05-13 17:00:00"
    latest_unread = message("好的", message_id="msg-new-ok", single_chat=True)
    latest_unread.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [old_candidate_context, latest_unread]},
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="ack only"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert "好的" in new_messages_section
    assert "这个候选人怎么样？" not in new_messages_section
    assert "这个候选人怎么样？" in context_section


def test_single_chat_recent_context_after_seen_is_processed_when_unread_empty(
    tmp_path: Path, monkeypatch
):
    handled = message("paper是不是也要开始准备了？", message_id="msg-handled", single_chat=True)
    handled.create_time = "2026-05-13 17:44:34"
    sent_reply = derek_message(
        "对，paper不要等所有数据都齐了再启动。",
        message_id="msg-derek-reply",
        create_time="2026-05-13 17:45:31",
    )
    new_peer_message = message(
        "我比较想先把hsw弄出来，目前的novelty更强一点",
        message_id="msg-new-peer-1",
        single_chat=True,
    )
    new_peer_message.create_time = "2026-05-13 17:47:44"
    latest_peer_message = message(
        "如果他们确实比较感兴趣的话能拉他们弄点合作或者挂个名之类的就更好一些",
        message_id="msg-new-peer-2",
        single_chat=True,
    )
    latest_peer_message.create_time = "2026-05-13 17:50:01"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                latest_peer_message,
                new_peer_message,
                sent_reply,
                handled,
            ]
        },
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我倾向先推 HSW。")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", True, None)
    worker.store.mark_seen("msg-handled", "cid-1")

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert "我比较想先把hsw弄出来" in context_section
    assert "拉他们弄点合作或者挂个名" in new_messages_section
    assert len(final_sent(dws)) == 1
    assert "我倾向先推 HSW。" in final_sent(dws)[0][1]
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-new-peer-2"


def test_single_chat_empty_unread_without_seen_anchor_does_not_process_old_context(
    tmp_path: Path, monkeypatch
):
    old_message = message("这个候选人怎么样？", message_id="msg-old", single_chat=True)
    old_message.create_time = "2026-05-13 17:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [old_message]},
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_prompt_context_includes_previous_20_plus_unread_tail(
    tmp_path: Path, monkeypatch
):
    old_messages = [
        message(f"历史上下文 {index:02d}", message_id=f"old-{index:02d}")
        for index in range(25)
    ]
    for index, old_message in enumerate(old_messages):
        old_message.create_time = f"2026-05-13 17:{index:02d}:00"
    trigger = message("@Derek Zen(磊哥) 这个需要你看一下", message_id="trigger-msg")
    trigger.create_time = "2026-05-13 18:00:00"
    downstream = message("我已经处理好了", message_id="downstream-msg")
    downstream.create_time = "2026-05-13 18:01:00"
    dws = FakeDws(
        [conversation()],
        {"cid-1": old_messages},
        unread_messages={"cid-1": [trigger, downstream]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert "历史上下文 04" not in context_section
    assert "历史上下文 05" in context_section
    assert "历史上下文 24" in context_section
    assert "@Derek Zen(磊哥) 这个需要你看一下" in new_messages_section
    assert "我已经处理好了" not in new_messages_section
    assert "@Derek Zen(磊哥) 这个需要你看一下" in context_section
    assert "我已经处理好了" in context_section


def test_no_reply_action_does_not_send(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.sent == []
    assert store.has_seen("msg-1") is True
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "cc only"


def test_handoff_sends_ack_dings_self_and_records_message_result(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 不要分身，真人看一下")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    expected_ack = HANDOFF_ACK
    assert final_sent(dws) == [("cid-1", expected_ack)]
    assert len(dws.dings) == 1
    assert "Friday" in dws.dings[0]
    assert "不要分身" in dws.dings[0]
    assert "previous split-person reply: none" in dws.dings[0]
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.final_reply_text == expected_ack


def test_new_derek_mention_is_processed(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "26年董事会筹备组", False, None)
    latest = message(
        "@Melody Xu（Melody） @Derek Zen（磊哥）请磊哥看一下2026年的战略主线这样写是否合适？[图片消息]",
        message_id="msg-after-handoff",
    )
    latest.create_time = "2026-05-13 18:10:00"
    latest.sender_name = "Melody"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "26年董事会筹备组"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="战略主线建议这样调整")
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert codex.calls
    assert final_sent(dws) == [("cid-1", "战略主线建议这样调整（by磊哥分身）")]
    assert store.has_seen("msg-after-handoff") is True
    assert notifications == [
        {
            "title": "CEO auto reply: 26年董事会筹备组",
            "message": "战略主线建议这样调整（by磊哥分身）",
            "url": None,
        }
    ]


def test_group_unread_without_derek_mention_is_ignored(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "MKT core", False, None)
    latest = message(
        "［文件】星尘数据B轮融资 BP_20260526.pptx-2.pptx",
        message_id="file-after-handoff",
        message_type="file",
    )
    latest.create_time = "2026-05-13 18:10:00"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "MKT core"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert store.has_seen("file-after-handoff") is False
    assert notifications == []


def test_dry_run_group_unread_without_derek_mention_is_ignored(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "26年董事会筹备组", False, None)
    latest = message(
        "可以东风集团（京东云渠道）",
        message_id="msg-after-handoff",
    )
    latest.create_time = "2026-05-13 18:10:00"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "26年董事会筹备组"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=True,
        now_provider=fixed_worker_now,
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert store.has_seen("msg-after-handoff") is False
    assert notifications == []


def test_single_chat_unread_is_processed_without_mention(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个今天能拍吗？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，先推进")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 这个今天能拍吗？\n\n可以，先推进（by磊哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [[]]
    assert final_direct_user_ids(dws) == ["sender-user-1"]
    assert final_direct_open_dingtalk_ids(dws) == [None]
    assert dws.reply_messages == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.direct_user_id == "sender-user-1"
    assert attempt.direct_open_dingtalk_id == "sender-1"
    assert attempt.final_reply_text == (
        "> 周俊杰: 这个今天能拍吗？\n\n可以，先推进（by磊哥分身）"
    )


def test_fake_quote_removes_links_and_mentions_before_truncating():
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/doc123 "
        "@Derek Zen(磊哥) @Shawn Hou(侯光焕) @Xingzu Liu(刘兴祖) "
        "@张晓民(Xiaomin张晓民) 数据导入导出业务的根因和解法"
    )

    quote = DingTalkAutoReplyWorker._fake_quote(trigger)

    assert quote == "> 周俊杰: 数据导入导出业务的根因和解法"
    assert "http" not in quote
    assert "@Derek" not in quote
    assert "@Shawn" not in quote


def test_fake_quote_keeps_text_after_compact_assistant_mention():
    trigger = message(
        "@磊哥分身，请你按照一曲线、二曲线、三曲线的流程和节点，分析缺乏owner的地方。"
    )

    quote = DingTalkAutoReplyWorker._fake_quote(trigger)

    assert quote == "> 周俊杰: 请你按照一曲线、二曲线、三曲线的流程和节点，分..."
    assert "原消息" not in quote
    assert "@磊哥分身" not in quote


def test_fake_quote_redacts_runtime_terms_from_user_text():
    trigger = message("磊哥，你是怎么解决codex上下文压缩失败的问题的？")

    quote = DingTalkAutoReplyWorker._fake_quote(trigger)

    assert quote == "> 周俊杰: 磊哥，你是怎么解决相关内容上下文压缩失败的..."
    assert "codex" not in quote.lower()
    assert "原消息" not in quote


def test_user_runtime_term_in_trigger_quote_does_not_block_safe_reply(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("磊哥，你是怎么解决codex上下文压缩失败的问题的？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="我会把长任务拆小，每一步都留清楚验收口径。",
            audit_summary="只需上下文判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    sent_text = final_sent(dws)[0][1]
    assert "codex" not in sent_text.lower()
    assert "相关内容上下文压缩失败" in sent_text
    assert worker.store.get_reply_attempt(1).send_status == "sent"


def test_single_chat_current_user_message_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [derek_message("AI自动抓取，用于会议纪要整理")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_run_once_max_batches_stops_after_limit(tmp_path: Path, monkeypatch):
    conv_1 = DingTalkConversation(
        open_conversation_id="cid-1",
        title="技术部",
        single_chat=False,
        unread_point=1,
    )
    conv_2 = DingTalkConversation(
        open_conversation_id="cid-2",
        title="产品部",
        single_chat=False,
        unread_point=1,
    )
    dws = FakeDws(
        [conv_1, conv_2],
        {
            "cid-1": [message("@Derek Zen(磊哥) 第一个问题", message_id="msg-1")],
            "cid-2": [message("@Derek Zen(磊哥) 第二个问题", message_id="msg-2")],
        },
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先推进"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once(max_batches=1)

    assert len(codex.calls) == 1
    assert len(final_sent(dws)) == 1
    assert final_sent(dws)[0][0] == "cid-1"
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.has_seen("msg-2") is False


def test_single_chat_current_user_display_name_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    self_message = message(
        "AI自动抓取，用于会议纪要整理",
        single_chat=True,
        sender_user_id=None,
    )
    self_message.sender_name = "磊哥"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [self_message]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_message_before_current_user_reply_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    requester = message(
        "@Derek Zen(磊哥) push了",
        message_id="msg-before-self",
    )
    requester.create_time = "2026-05-13 08:45:50"
    manual_reply = derek_message(
        "@周俊杰(周俊杰) 我merge了",
        message_id="msg-self-after",
        create_time="2026-05-13 11:00:03",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [requester, manual_reply]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_message_after_current_user_reply_still_calls_codex(
    tmp_path: Path, monkeypatch
):
    manual_reply = derek_message(
        "这个ACL表@张晓民(Xiaomin张晓民) 看一下",
        message_id="msg-self-before",
        create_time="2026-05-13 15:15:14",
    )
    requester = message(
        "@Derek Zen(磊哥) 我和俊杰聊下",
        message_id="msg-after-self",
    )
    requester.create_time = "2026-05-13 15:16:49"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [manual_reply, requester]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "@Derek Zen(磊哥) 我和俊杰聊下" in codex.calls[0][0]
    assert (
        "这个ACL表"
        not in codex.calls[0][0].split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    )


def test_read_failure_records_error_and_continues_next_conversation(
    tmp_path: Path, monkeypatch
):
    bad_conversation = DingTalkConversation(
        open_conversation_id="cid-bad",
        title="bad",
        single_chat=False,
        unread_point=1,
    )
    good_conversation = DingTalkConversation(
        open_conversation_id="cid-good",
        title="good",
        single_chat=False,
        unread_point=1,
    )
    good_message = message(
        "@Derek Zen(磊哥) 这个怎么处理？",
        message_id="msg-good",
    )
    good_message.open_conversation_id = "cid-good"
    dws = FakeDws(
        [bad_conversation, good_conversation],
        {"cid-good": [good_message]},
        read_errors={"cid-bad": RuntimeError("forbidden request")},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "cid-good",
            "先按A方案走（by磊哥分身）",
        )
    ]


def test_group_mention_from_unread_conversation_is_processed_when_unread_tail_misses_it(
    tmp_path: Path, monkeypatch
):
    unread_tail = message("后续同步进展", message_id="msg-tail")
    unread_tail.create_time = "2026-05-25 17:53:12"
    missed_mention = message(
        "@Derek Zen(磊哥) 要不现在对一下",
        message_id="msg-mentioned",
    )
    missed_mention.create_time = "2026-05-25 16:20:14"
    conv = conversation()
    conv.unread_point = 6
    dws = FakeDws(
        [conv],
        {"cid-1": [unread_tail]},
        unread_messages={"cid-1": [unread_tail]},
    )
    dws.mentioned_messages = {"cid-1": [missed_mention]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="现在可以对")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].trigger_message_id == "msg-mentioned"
    assert attempts[0].send_status == "dry_run"


def test_produce_once_coalesces_consecutive_group_mentions_from_same_sender(
    tmp_path: Path, monkeypatch
):
    first = message(
        "@Derek Zen(磊哥) 先看第一点",
        message_id="msg-mentioned-1",
    )
    first.create_time = "2026-05-28 13:21:54"
    second = message(
        "@曹宇航(Yuhang Cao) @Derek Zen(磊哥) 再看第二点",
        message_id="msg-mentioned-2",
    )
    second.create_time = "2026-05-28 13:24:02"
    third = message(
        "@Derek Zen(磊哥) @曹宇航(Yuhang Cao) 最后总结一下",
        message_id="msg-mentioned-3",
    )
    third.create_time = "2026-05-28 13:27:41"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [first, second, third]},
        unread_messages={"cid-1": [first, second, third]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-mentioned-3"
    assert "@Derek Zen(磊哥) 先看第一点" in tasks[0].trigger_text
    assert "@曹宇航(Yuhang Cao) @Derek Zen(磊哥) 再看第二点" in tasks[0].trigger_text
    assert "@Derek Zen(磊哥) @曹宇航(Yuhang Cao) 最后总结一下" in tasks[0].trigger_text
    assert codex.calls == []


def test_group_all_mention_from_unread_conversation_is_processed(
    tmp_path: Path, monkeypatch
):
    all_mention = message("@所有人 今天需要同步一下项目风险", message_id="msg-all")
    all_mention.create_time = "2026-05-25 17:53:12"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [all_mention]},
        unread_messages={"cid-1": [all_mention]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下风险点")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].trigger_message_id == "msg-all"
    assert attempts[0].send_status == "dry_run"


def test_group_all_mention_is_case_insensitive_for_ascii_alias(
    tmp_path: Path, monkeypatch
):
    all_mention = message("@All 请大家看一下官网更新内容", message_id="msg-all-case")
    all_mention.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [all_mention]},
        unread_messages={"cid-1": [all_mention]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].trigger_message_id == "msg-all-case"
    assert attempts[0].send_status == "dry_run"


def test_group_mention_from_read_conversation_is_processed_from_mentions(
    tmp_path: Path, monkeypatch
):
    mentioned = message(
        "@Derek Zen(磊哥) 磊哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-mkt-mention",
    )
    mentioned.open_conversation_id = "cid-mkt"
    mentioned.conversation_title = "MKT core"
    mentioned.create_time = "2026-05-25 19:21:56"
    dws = FakeDws(
        [],
        {"cid-mkt": [mentioned]},
        unread_messages={"cid-mkt": []},
    )
    dws.mentioned_messages = {"cid-mkt": [mentioned]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="会，但只处理需要回复的消息")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].conversation_title == "MKT core"
    assert attempts[0].trigger_message_id == "msg-mkt-mention"
    assert attempts[0].send_status == "dry_run"


def test_group_all_mention_from_read_conversation_is_processed_from_broadcast_search(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@All 新的官网更新一共16页，请大家打开每一个html文档",
        message_id="msg-website-all",
    )
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    broadcast.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [],
        {"cid-website": [broadcast]},
        unread_messages={"cid-website": []},
    )
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下官网内容")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].conversation_title == "官网迭代群"
    assert attempts[0].trigger_message_id == "msg-website-all"
    assert attempts[0].send_status == "dry_run"


def test_current_user_all_mention_is_filtered_from_broadcast_search(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@所有人 我已经更新完了",
        message_id="msg-self-all",
        sender_user_id="derek-user-1",
    )
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    broadcast.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [],
        {"cid-website": [broadcast]},
        unread_messages={"cid-website": []},
    )
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker._broadcast_messages_by_conversation() == {}


def test_read_group_mention_is_skipped_when_later_current_user_text_replied(
    tmp_path: Path, monkeypatch
):
    mentioned = message(
        "@Derek Zen(磊哥) 磊哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-mkt-mention",
    )
    mentioned.open_conversation_id = "cid-mkt"
    mentioned.conversation_title = "MKT core"
    mentioned.create_time = "2026-05-25 19:21:56"
    manual_reply = derek_message(
        "会的，晚上也会处理需要回复的消息",
        message_id="msg-derek-text",
        create_time="2026-05-25 19:24:00",
    )
    manual_reply.open_conversation_id = "cid-mkt"
    manual_reply.conversation_title = "MKT core"

    class ContextAwareFakeDws(FakeDws):
        def read_recent_messages(self, conversation: DingTalkConversation):
            if conversation.open_conversation_id == "cid-mkt":
                if conversation.last_message_create_at is None:
                    return [manual_reply, mentioned]
                return [mentioned]
            return super().read_recent_messages(conversation)

    dws = ContextAwareFakeDws(
        [],
        {"cid-mkt": [manual_reply, mentioned]},
        unread_messages={"cid-mkt": []},
    )
    dws.mentioned_messages = {"cid-mkt": [mentioned]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert codex.calls == []
    assert worker.store.list_reply_attempts(limit=10) == []


def test_group_mentions_are_processed_by_message_time_not_fetch_order(
    tmp_path: Path, monkeypatch
):
    older_mention = message(
        "@Derek Zen(磊哥) 怎么规避客户拿给别的 vendor 比价？",
        message_id="msg-older-mention",
    )
    older_mention.create_time = "2026-05-26 07:54:36"
    newer_mention = message(
        "@Derek Zen(磊哥) 磊哥请审一下这个文档，给一下意见",
        message_id="msg-newer-mention",
    )
    newer_mention.create_time = "2026-05-26 08:34:57"
    latest_file = message("[文件] 新版文档.docx", message_id="msg-latest-file")
    latest_file.create_time = "2026-05-26 08:57:46"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                latest_file,
                newer_mention,
                older_mention,
            ]
        },
        unread_messages={"cid-1": [latest_file]},
    )
    dws.mentioned_messages = {
        "cid-1": [
            older_mention,
            newer_mention,
        ]
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert len(attempts) == 1
    assert attempts[0].trigger_message_id == "msg-newer-mention"
    assert "怎么规避客户拿给别的 vendor 比价" in attempts[0].trigger_text
    assert "请审一下这个文档" in attempts[0].trigger_text


def test_current_user_file_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 磊哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-25 19:21:56"
    self_file = derek_message(
        "[文件] 北京星尘_B轮融资BP_图片版_19页.pdf",
        message_id="msg-self-file",
        create_time="2026-05-26 03:49:28",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [self_file, trigger]},
        unread_messages={"cid-1": [self_file]},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="会，但只处理需要回复的消息")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(codex.calls) == 1
    assert attempts[0].trigger_message_id == "msg-trigger"


def test_processing_ack_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 磊哥请审一下这个文档，给一下意见",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-26 08:34:57"
    ack = derek_message(
        PROCESSING_ACK,
        message_id="msg-processing-ack",
        create_time="2026-05-26 09:05:36",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [ack, trigger]},
        unread_messages={"cid-1": [ack]},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    prompt = codex.calls[0][0]
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-trigger"
    assert PROCESSING_ACK not in prompt
    assert "请审一下这个文档" in prompt


def test_internal_personnel_question_missing_subject_asks_clarifying_question(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个人后续怎么处理？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以晋升",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 这个人后续怎么处理？\n\n这个是关于谁的问题？（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_allows_hr_requester(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三转正怎么看？", single_chat=True)]},
    )
    dws.hr_users.add("sender-user-1")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="建议先观察一个月",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 张三转正怎么看？\n\n建议先观察一个月（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_allows_subject_manager(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.manager_chains["subject-user-1"] = ["sender-user-1"]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 张三绩效怎么定？\n\n先按事实反馈（by磊哥分身）",
        )
    ]


def test_internal_personnel_question_refuses_unrelated_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 张三绩效怎么定？\n\n"
            "这个涉及内部人事隐私，我不能回答。（by磊哥分身）",
        )
    ]


def test_candidate_question_missing_department_asks_clarifying_question(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n"
            "这个候选人是哪个岗位/部门的？（by磊哥分身）",
        )
    ]


def test_candidate_question_allows_related_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-sales"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n可以推进（by磊哥分身）",
        )
    ]


def test_candidate_question_refuses_unrelated_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-product"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        (
            "",
            "> 周俊杰: 这个候选人怎么样？\n\n"
            "这个候选人信息只回答相关部门的人。（by磊哥分身）",
        )
    ]


def test_permission_lookup_failure_records_error_and_does_not_send(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True, sender_user_id=None)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert store.count_errors() == 1
    assert store.has_seen("msg-1") is False


def test_dry_run_does_not_mutate_terminal_state(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()], {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]}
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, dry_run=True, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0


def test_send_failure_records_error_and_does_not_mark_seen(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_errors() == 2
    assert store.count_reply_tasks(status="pending") == 1
    assert dws.send_attempt_count == 2
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.retry_count == 1
    assert "attempt 1: send failed" in attempt.send_error
    assert "attempt 2: send failed" in attempt.send_error


def test_send_failure_requeues_reply_task_for_consumer_retry(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_reply_tasks(status="pending") == 1
    assert store.count_reply_tasks(status="done") == 0
    retried = store.claim_reply_tasks(limit=1)
    assert retried[0].attempts == 2
    assert "send failed" in retried[0].error
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"


def test_consumer_send_failure_emits_one_failure_notification(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    worker.consume_once(max_tasks=1)

    failure_titles = [
        notification["title"]
        for notification in notifications
        if "failed" in notification["title"]
    ]
    assert failure_titles == ["CEO task failed: Friday"]


def test_pat_authorization_error_is_recorded_as_failed_without_retry_or_url(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 这个怎么处理？")]},
        send_error=DwsError(
            "dws command failed with exit code 4: PAT_HIGH_RISK_NO_PERMISSION",
            code="PAT_HIGH_RISK_NO_PERMISSION",
        ),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert dws.send_attempt_count == 1
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.retry_count == 0
    assert "PAT_HIGH_RISK_NO_PERMISSION" in attempt.send_error
    assert "authorizationUrl" not in attempt.send_error
    assert "open-dev.dingtalk.com" not in attempt.send_error


def test_handoff_ding_failure_does_not_mark_seen(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Derek Zen(磊哥) 不要分身，真人看一下")]},
        ding_error=RuntimeError("ding failed"),
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert store.has_seen("msg-1") is False
    assert store.count_errors() == 2
    assert store.count_reply_tasks(status="pending") == 1


def test_persists_codex_last_session_id_after_decision(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        next_session_id="session-1",
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert store.get_codex_session_id("cid-1") == "session-1"


def test_stale_codex_last_session_id_is_not_persisted(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws([conversation()], {"cid-1": [message("@Derek Zen(磊哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        last_session_id="stale-session",
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert store.get_codex_session_id("cid-1") is None
