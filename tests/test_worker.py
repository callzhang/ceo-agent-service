from datetime import datetime
from datetime import timedelta
from io import BytesIO
import importlib
import json
from pathlib import Path
import sqlite3
import zipfile
from zoneinfo import ZoneInfo

import pytest

from app.agent_envelope import AgentEnvelope
import app.worker as worker_module
from app.codex_decision import CodexDecisionRunner, append_signature
from app.corpus import CorpusRecord
from app.developer_prompt import read_developer_prompt_template
from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from app.dws_client import (
    DwsCalendarEvent,
    DwsClient,
    DwsDocumentSearchResult,
    DwsError,
    DwsMinutesPermissionRequest,
    DwsOaApprovalCandidate,
    DwsUserProfile,
)
from app.feedback_spike import FeedbackReplyText
from app.oa_approval import OaApprovalResult
from app.store import AutoReplyStore
from app.worker import (
    DWS_AUTH_LOGIN_STATE_KEY,
    PROCESSING_ACK,
    DingTalkAutoReplyWorker,
)


CONTEXT_HEADER = "上下文消息（自上次回复后的新信息，最多 20 条）:"


class FakeAuthLoginProcess:
    def __init__(self, pid: int = 1234, returncode: int | None = None):
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def fixed_worker_now() -> datetime:
    return datetime(2026, 5, 13, 10, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def test_worker_recovery_runtime_config_reads_environment(monkeypatch):
    monkeypatch.setenv("MESSAGE_RECOVERY_INTERVAL", "15m")
    monkeypatch.setenv("FAST_PATH_UNREAD_BACKOFF", "2m")
    monkeypatch.setenv("SINGLE_CHAT_READ_RECOVERY_WINDOW", "6h")
    monkeypatch.setenv("SINGLE_CHAT_READ_RECOVERY_LIMIT", "11")

    importlib.reload(worker_module)

    assert worker_module.MESSAGE_RECOVERY_INTERVAL == timedelta(minutes=15)
    assert worker_module.FAST_PATH_UNREAD_BACKOFF == timedelta(minutes=2)
    assert worker_module.SINGLE_CHAT_READ_RECOVERY_WINDOW == timedelta(hours=6)
    assert worker_module.SINGLE_CHAT_READ_RECOVERY_LIMIT == 11
    for name in (
        "MESSAGE_RECOVERY_INTERVAL",
        "FAST_PATH_UNREAD_BACKOFF",
        "SINGLE_CHAT_READ_RECOVERY_WINDOW",
        "SINGLE_CHAT_READ_RECOVERY_LIMIT",
    ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("FAST_PATH_UNREAD_BACKOFF", "0s")
    importlib.reload(worker_module)


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
        client_cids: dict[str, str] | None = None,
    ):
        self.conversations = conversations
        self.messages = self._messages_by_conversation(messages)
        self.unread_messages = self._messages_by_conversation(
            unread_messages or messages
        )
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
        self.resource_download_urls: dict[
            tuple[str, str, str, str],
            dict | Exception,
        ] = {}
        self.robot_message_file_downloads: dict[str, dict | Exception] = {}
        self.doc_info_calls: list[str] = []
        self.read_doc_calls: list[str] = []
        self.get_aitable_base_calls: list[str] = []
        self.get_aitable_tables_calls: list[tuple[str, tuple[str, ...] | None]] = []
        self.query_aitable_record_calls: list[tuple[str, str, int]] = []
        self.search_document_calls: list[tuple[str, int]] = []
        self.download_doc_calls: list[str] = []
        self.resource_download_url_calls: list[tuple[str, str, str, str]] = []
        self.robot_message_file_download_calls: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.reply_messages: list[tuple[str, str, str, str]] = []
        self.created_markdown_docs: list[tuple[str, str]] = []
        self.doc_editor_permissions: list[tuple[str, list[str]]] = []
        self.doc_editor_permission_error: Exception | None = None
        self.send_visible = True
        self.reply_visible = True
        self.message_emojis: list[tuple[str, str, str]] = []
        self.message_text_emotions: list[tuple[str, str, str, str, str, str]] = []
        self.created_text_emotions: list[tuple[str, str, str]] = []
        self.sent_at_users: list[list[str]] = []
        self.direct_user_ids: list[str | None] = []
        self.direct_open_dingtalk_ids: list[str | None] = []
        self.send_attempt_count = 0
        self.dings: list[str] = []
        self.mentioned_messages: dict[str, list[DingTalkMessage]] = {
            conversation_id: [
                message
                for message in messages
                if "@Alex Chen" in message.content
                or "@所有人" in message.content
                or "@All" in message.content
            ]
            for conversation_id, messages in self.unread_messages.items()
        }
        self.broadcast_messages: dict[str, list[DingTalkMessage]] = {
            conversation_id: [
                message
                for message in messages
                if "@所有人" in message.content or "@All" in message.content
            ]
            for conversation_id, messages in self.unread_messages.items()
        }
        self.user_departments: dict[str, set[str]] = {}
        self.user_profiles: dict[str, DwsUserProfile] = {}
        self.user_profile_calls: list[str] = []
        self.recent_message_reads: list[str] = []
        self.unread_message_reads: list[str] = []
        self.messages_by_id_reads: list[list[str]] = []
        self.hr_users: set[str] = set()
        self.manager_chains: dict[str, list[str]] = {}
        self.resolved_senders: dict[str, str] = {}
        self.current_user_id = "principal-user-1"
        self.current_user_checks: list[str] = []
        self.calendar_invites: dict[str, DwsCalendarEvent | None] = {}
        self.calendar_events: dict[str, list[DwsCalendarEvent]] = {}
        self.calendar_event_details: dict[str, DwsCalendarEvent | None] = {}
        self.calendar_event_detail_calls: list[str] = []
        self.calendar_responses: list[tuple[str, str]] = []
        self.calendar_response_error: Exception | None = None
        self.minutes_permission_requests: dict[
            str, DwsMinutesPermissionRequest | None
        ] = {}
        self.added_minutes_permissions: list[DwsMinutesPermissionRequest] = []
        self.minutes_infos: dict[str, dict | Exception] = {}
        self.minutes_summaries: dict[str, dict] = {}
        self.minutes_todos: dict[str, dict] = {}
        self.minutes_transcriptions: dict[str, dict] = {}
        self.minutes_info_calls: list[str] = []
        self.minutes_summary_calls: list[str] = []
        self.minutes_todo_calls: list[str] = []
        self.minutes_transcription_calls: list[tuple[str, str]] = []
        self.doc_comments: list[tuple[str, str]] = []
        self.doc_comment_result: dict = {"result": {"commentKey": "comment-1"}}
        self.doc_comment_error: Exception | None = None
        self.oa_approval_actions: list[tuple[str, str, str, str]] = []
        self.oa_approval_action_result: dict = {"errcode": 0, "errmsg": "ok"}
        self.oa_approval_action_error: Exception | None = None
        self.oa_approval_comments: list[tuple[str, str]] = []
        self.oa_approval_comment_result: dict = {"errcode": 0, "errmsg": "ok"}
        self.oa_approval_comment_error: Exception | None = None
        self.pending_oa_approvals: list[DwsOaApprovalCandidate] = []
        self.oa_approval_details: dict[str, dict | Exception] = {}
        self.oa_approval_records: dict[str, dict | Exception] = {}
        self.oa_approval_tasks: dict[str, dict | Exception] = {}
        self.openapi_oa_details: dict[str, dict | Exception] = {}
        self.oa_attachment_downloads: dict[tuple[str, str], bytes | Exception] = {}
        self.download_oa_attachment_calls: list[tuple[str, str]] = []
        self.upgrade_check_response: dict = {"needs_upgrade": False}
        self.upgrade_error: Exception | None = None
        self.upgrade_check_calls = 0
        self.upgrade_calls = 0
        self.auth_login_processes: list[FakeAuthLoginProcess] = [
            FakeAuthLoginProcess()
        ]
        self.auth_login_starts = 0
        self.client_cids = client_cids or {}
        self.client_cid_calls: list[str] = []

    @staticmethod
    def _messages_by_conversation(
        messages: dict[str, list[DingTalkMessage]],
    ) -> dict[str, list[DingTalkMessage]]:
        return {
            conversation_id: [
                message.model_copy(
                    update={"open_conversation_id": conversation_id}
                )
                for message in conversation_messages
            ]
            for conversation_id, conversation_messages in messages.items()
        }

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

    def start_auth_login(self) -> FakeAuthLoginProcess:
        self.auth_login_starts += 1
        return self.auth_login_processes.pop(0)

    def get_current_user_id(self) -> str:
        return self.current_user_id

    def search_department_ids(self, query: str) -> set[str]:
        del query
        return {"hr-dept"}

    def client_conversation_id(self, open_conversation_id: str) -> str:
        self.client_cid_calls.append(open_conversation_id)
        return self.client_cids.get(open_conversation_id, "")

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
        self.recent_message_reads.append(conversation.open_conversation_id)
        if conversation.open_conversation_id in self.read_errors:
            raise self.read_errors[conversation.open_conversation_id]
        return self.messages.get(conversation.open_conversation_id, [])

    def read_unread_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        self.unread_message_reads.append(conversation.open_conversation_id)
        if conversation.open_conversation_id in self.unread_errors:
            raise self.unread_errors[conversation.open_conversation_id]
        return self.unread_messages.get(conversation.open_conversation_id, [])

    def list_messages_by_ids(self, message_ids: list[str]) -> list[DingTalkMessage]:
        self.messages_by_id_reads.append(list(message_ids))
        wanted = set(message_ids)
        seen: set[str] = set()
        result: list[DingTalkMessage] = []
        sources = (
            self.messages,
            self.unread_messages,
            self.mentioned_messages,
            self.broadcast_messages,
        )
        for source in sources:
            for messages in source.values():
                for message in messages:
                    if (
                        message.open_message_id in wanted
                        and message.open_message_id not in seen
                    ):
                        result.append(message)
                        seen.add(message.open_message_id)
        return [
            message
            for message_id in message_ids
            for message in result
            if message.open_message_id == message_id
        ]

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
            result = self.doc_infos[node]
            if isinstance(result, DwsError):
                raise result
            return result
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

    def get_resource_download_url(
        self,
        open_conversation_id: str,
        open_message_id: str,
        resource_id: str,
        resource_type: str,
    ) -> dict:
        key = (
            open_conversation_id,
            open_message_id,
            resource_id,
            resource_type,
        )
        self.resource_download_url_calls.append(key)
        result = self.resource_download_urls.get(key)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def download_robot_message_file(self, download_code: str) -> dict:
        self.robot_message_file_download_calls.append(download_code)
        result = self.robot_message_file_downloads.get(download_code)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> None:
        del at_open_dingtalk_names
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.sent.append((conversation_id or "", text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(user_id)
        self.direct_open_dingtalk_ids.append(open_dingtalk_id)
        if conversation_id and self.send_visible:
            self._append_visible_message(conversation_id, text)
        return self.send_result

    def reply_message(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> None:
        del at_open_dingtalk_names
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.reply_messages.append(
            (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
        )
        self.sent.append((conversation_id, text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(None)
        self.direct_open_dingtalk_ids.append(None)
        if self.reply_visible:
            self._append_visible_message(conversation_id, text)
        return self.send_result

    def create_markdown_doc(self, name: str, content: str) -> dict:
        self.created_markdown_docs.append((name, content))
        index = len(self.created_markdown_docs)
        return {
            "result": {
                "nodeId": f"doc-{index}",
                "url": f"https://alidocs.dingtalk.com/i/nodes/doc-{index}",
                "name": name,
            }
        }

    def add_doc_editor_permission(self, node: str, user_ids: list[str]) -> dict:
        if self.doc_editor_permission_error:
            raise self.doc_editor_permission_error
        self.doc_editor_permissions.append((node, user_ids))
        return {"success": True, "nodeId": node, "userIds": user_ids}

    def _append_visible_message(self, conversation_id: str, text: str) -> None:
        visible = DingTalkMessage(
            open_conversation_id=conversation_id,
            open_message_id=f"sent-{len(self.sent)}",
            conversation_title="CEO-2 管理群",
            single_chat=False,
            sender_name="磊哥",
            sender_open_dingtalk_id="principal-open-1",
            create_time="2026-05-13 18:00:00",
            content=text,
        )
        self.messages.setdefault(conversation_id, []).insert(0, visible)

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> None:
        return self.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
            at_users=at_users,
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
        )

    def add_message_emoji(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str,
    ) -> dict:
        self.message_emojis.append((conversation_id, message_id, emoji))
        return {"success": True}

    def add_message_text_emotion(
        self,
        conversation_id: str,
        message_id: str,
        *,
        text: str,
        emotion_id: str,
        emotion_name: str,
        background_id: str,
    ) -> dict:
        self.message_text_emotions.append(
            (conversation_id, message_id, text, emotion_id, emotion_name, background_id)
        )
        return {"success": True}

    def create_message_text_emotion(
        self,
        *,
        text: str,
        emotion_name: str,
        background_id: str = "",
    ) -> dict:
        self.created_text_emotions.append((text, emotion_name, background_id))
        return {
            "emotionId": f"created-{len(self.created_text_emotions)}",
            "backgroundId": "created-bg",
        }

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
        self.current_user_checks.append(message.sender_name)
        if self.current_user_error:
            raise self.current_user_error
        return message.sender_user_id == self.current_user_id

    def calendar_invite_from_message(
        self, message: DingTalkMessage
    ) -> DwsCalendarEvent | None:
        if message.raw_payload:
            event = DwsClient._find_calendar_event_in_payload(message.raw_payload)
            if event is not None:
                return event
        return self.calendar_invites.get(message.open_message_id)

    def list_calendar_events(self, start: str, end: str) -> list[DwsCalendarEvent]:
        return self.calendar_events.get(f"{start}|{end}", [])

    def get_calendar_event(self, event_id: str) -> DwsCalendarEvent | None:
        self.calendar_event_detail_calls.append(event_id)
        return self.calendar_event_details.get(event_id)

    def respond_calendar_event(self, event_id: str, response_status: str) -> dict:
        self.calendar_responses.append((event_id, response_status))
        if self.calendar_response_error:
            raise self.calendar_response_error
        return {"success": True}

    def minutes_permission_request_from_message(
        self, message: DingTalkMessage
    ) -> DwsMinutesPermissionRequest | None:
        return self.minutes_permission_requests.get(message.open_message_id)

    def add_minutes_member_permission(
        self, request: DwsMinutesPermissionRequest
    ) -> dict:
        self.added_minutes_permissions.append(request)
        return {"success": True}

    def get_minutes_info(self, task_uuid: str) -> dict:
        self.minutes_info_calls.append(task_uuid)
        result = self.minutes_infos.get(task_uuid)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        return self.minutes_infos.get(
            task_uuid,
            {"result": {"taskUuid": task_uuid, "title": "静默会"}},
        )

    def get_minutes_summary(self, task_uuid: str) -> dict:
        self.minutes_summary_calls.append(task_uuid)
        return self.minutes_summaries.get(task_uuid, {"result": {}})

    def get_minutes_todos(self, task_uuid: str) -> dict:
        self.minutes_todo_calls.append(task_uuid)
        return self.minutes_todos.get(task_uuid, {"result": {}})

    def get_minutes_transcription(
        self,
        task_uuid: str,
        *,
        next_token: str = "",
    ) -> dict:
        self.minutes_transcription_calls.append((task_uuid, next_token))
        return self.minutes_transcriptions.get(task_uuid, {"result": {}})

    def create_doc_comment(self, node_id: str, content: str) -> dict:
        self.doc_comments.append((node_id, content))
        if self.doc_comment_error:
            raise self.doc_comment_error
        return self.doc_comment_result

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

    def comment_oa_approval(
        self,
        process_instance_id: str,
        text: str,
    ) -> dict:
        self.oa_approval_comments.append((process_instance_id, text))
        if self.oa_approval_comment_error:
            raise self.oa_approval_comment_error
        return self.oa_approval_comment_result

    def list_pending_oa_approvals(
        self, page: int = 1, size: int = 30
    ) -> list[DwsOaApprovalCandidate]:
        del page, size
        return self.pending_oa_approvals

    def read_oa_approval_detail(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_details.get(
            process_instance_id,
            {"result": {"formValueVOS": [{"details": []}]}},
        )
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_approval_records(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_records.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_approval_tasks(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_tasks.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_process_instance_openapi(self, process_instance_id: str) -> dict:
        payload = self.openapi_oa_details.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def download_oa_process_attachment(
        self,
        process_instance_id: str,
        file_id: str,
    ) -> bytes:
        self.download_oa_attachment_calls.append((process_instance_id, file_id))
        payload = self.oa_attachment_downloads.get((process_instance_id, file_id), b"")
        if isinstance(payload, Exception):
            raise payload
        return payload


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
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.image_bytes_calls: list[list[bytes]] = []

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ) -> CodexDecision:
        if self.before_decide is not None:
            self.before_decide(prompt, session_id)
        paths = image_paths or []
        self.image_bytes_calls.append([path.read_bytes() for path in paths])
        self.calls.append((prompt, session_id, paths))
        if self.next_session_id is not None:
            self.last_session_id = self.next_session_id
        return self.decision


class FakeEnvelopeCodex:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.last_session_id = "session-envelope"
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ):
        self.calls.append((prompt, session_id, image_paths or []))
        return self.envelope


class SequencedFakeCodex:
    def __init__(self, decisions: list[CodexDecision]):
        self.decisions = decisions
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ) -> CodexDecision:
        self.calls.append((prompt, session_id, image_paths or []))
        self.last_session_id = session_id or self.last_session_id or "session-1"
        return self.decisions[len(self.calls) - 1]


class FakeOaApprovalHandler:
    def __init__(self):
        self.calls: list[tuple[str, str, str, bool]] = []
        self.approval_detail_texts: list[str] = []
        self.last_session_id = "oa-session-1"
        self.last_transcript_start_line = 12
        self.last_transcript_end_line = 34
        self.last_audit_tool_events = [{"tool": "dws", "action": "oa_review"}]

    def handle(
        self,
        trigger_text: str,
        context_text: str,
        oa_url: str,
        approval_detail_text: str = "",
        conversation_id: str = "",
        conversation_title: str = "",
        single_chat: bool = True,
        execute: bool = True,
    ) -> OaApprovalResult:
        del conversation_id, conversation_title, single_chat
        self.calls.append((trigger_text, context_text, oa_url, execute))
        self.approval_detail_texts.append(approval_detail_text)
        return OaApprovalResult(
            process_instance_id="proc-1",
            task_id="task-1",
            oa_url=oa_url
            or "https://aflow.dingtalk.com/dingtalk/pc/query/pchomepage.htm?procInstId=proc-1&taskId=task-1",
            oa_action="拒绝",
            oa_remark="请补充预算来源和项目归属后重新提交。",
            action_result={},
            audit_summary="缺少预算来源和项目归属，按审批规则拒绝。",
            audit_documents=[{"title": "OA 审批单", "url": oa_url}],
        )


class ReturnOaApprovalHandler(FakeOaApprovalHandler):
    def handle(
        self,
        trigger_text: str,
        context_text: str,
        oa_url: str,
        approval_detail_text: str = "",
        conversation_id: str = "",
        conversation_title: str = "",
        single_chat: bool = True,
        execute: bool = True,
    ) -> OaApprovalResult:
        del conversation_id, conversation_title, single_chat
        self.calls.append((trigger_text, context_text, oa_url, execute))
        self.approval_detail_texts.append(approval_detail_text)
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


class MissingTargetOaApprovalHandler(FakeOaApprovalHandler):
    def handle(
        self,
        trigger_text: str,
        context_text: str,
        oa_url: str,
        approval_detail_text: str = "",
        conversation_id: str = "",
        conversation_title: str = "",
        single_chat: bool = True,
        execute: bool = True,
    ) -> OaApprovalResult:
        del conversation_id, conversation_title, single_chat
        self.calls.append((trigger_text, context_text, oa_url, execute))
        self.approval_detail_texts.append(approval_detail_text)
        return OaApprovalResult(
            process_instance_id="",
            task_id="",
            oa_url="",
            oa_action="退回",
            oa_remark="材料不足，暂不执行审批动作。",
            action_result={},
            audit_summary="未取得审批详情，只记录材料不足。",
            audit_documents=[],
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


def principal_message(
    content: str,
    message_id: str = "principal-msg-1",
    create_time: str = "2026-05-13 18:00:01",
) -> DingTalkMessage:
    msg = message(
        content=content,
        message_id=message_id,
        sender_user_id="principal-user-1",
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
    oa_approval_handler=None,
    fast_path_unread_backoff: timedelta = timedelta(0),
) -> DingTalkAutoReplyWorker:
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    monkeypatch.setattr(
        "app.worker.FAST_PATH_UNREAD_BACKOFF",
        fast_path_unread_backoff,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user-1")
    return DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=dry_run,
        style_profile=style_profile,
        style_records=style_records,
        now_provider=fixed_worker_now,
        max_task_attempts=max_task_attempts,
        oa_approval_handler=oa_approval_handler,
    )


def test_notification_url_includes_attempt_id(tmp_path, monkeypatch):
    dws = FakeDws(
        conversations=[conversation(single_chat=True)],
        messages={},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    url = worker._notification_url(conversation(single_chat=True), attempt_id=123)

    assert url == (
        "http://127.0.0.1:8765/open-dingtalk"
        "?conversation_id=cid-1&attempt_id=123"
    )


def test_run_once_with_zero_batches_is_noop(tmp_path, monkeypatch):
    dws = FakeDws(
        conversations=[conversation(single_chat=True)],
        messages={},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    worker.run_once(max_batches=0)

    assert dws.upgrade_check_calls == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.list_errors() == []


def developer_instructions_from_command(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return json.loads(value.split("=", 1)[1])
    raise AssertionError("developer_instructions config missing")


def write_profile_for_consumer_test(tmp_path: Path, monkeypatch) -> str:
    profile = tmp_path / "profiles" / "work_profile.md"
    content = """# Alex Work Profile

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

- 不替 Alex 做最终人事、审批、财务、法律或客户关键承诺。
- 不声称 Alex 已经做了现实动作。
- 材料不足时不编造结论。
"""
    profile.parent.mkdir(parents=True)
    profile.write_text(content, encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    return content


def test_consumer_codex_command_injects_work_profile_content(
    tmp_path: Path, monkeypatch
):
    profile_content = write_profile_for_consumer_test(tmp_path, monkeypatch)
    seen_instructions = []

    def executor(command: list[str], prompt: str) -> str:
        seen_instructions.append(developer_instructions_from_command(command))
        return AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "ask_clarifying_question",
                    "text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                    "sensitivity_kind": "external_candidate",
                },
                "system_actions": [],
                "domain_payload": {
                    "candidate_context_known": True,
                    "candidate_department_ids": ["dept-candidate"],
                },
                "audit": {
                    "summary": "仅根据当前消息判断，材料不足，需要追问。",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        ).model_dump_json()

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人可以推进吗？")]},
    )
    dws.user_departments["sender-user-1"] = {"dept-candidate"}
    codex = CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        codex_home=tmp_path,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(seen_instructions) == 1
    instructions = seen_instructions[0]
    assert "明哥 工作人格 Profile" in instructions
    assert "Profile 内容:" in instructions
    assert profile_content in instructions
    assert str(tmp_path / "profiles" / "work_profile.md") not in instructions
    assert "材料不完整时先追问，不拍板" in instructions
    assert final_sent(dws)


def test_consumer_uses_profile_to_ask_for_missing_candidate_materials(
    tmp_path: Path, monkeypatch
):
    write_profile_for_consumer_test(tmp_path, monkeypatch)

    def executor(command: list[str], prompt: str) -> str:
        instructions = developer_instructions_from_command(command)
        assert "Profile 内容:" in instructions
        assert "材料不完整时先追问，不拍板" in instructions
        assert "这个候选人可以推进吗" in prompt
        return AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "ask_clarifying_question",
                    "text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                    "sensitivity_kind": "external_candidate",
                },
                "system_actions": [],
                "domain_payload": {
                    "candidate_context_known": True,
                    "candidate_department_ids": ["dept-candidate"],
                },
                "audit": {
                    "summary": "仅根据当前消息判断，缺少岗位要求和简历内容，按 profile 先追问材料。",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        ).model_dump_json()

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人可以推进吗？")]},
    )
    dws.user_departments["sender-user-1"] = {"dept-candidate"}
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
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        "cid-1",
        "msg-1",
    )
    assert attempt is not None
    assert attempt.action == CodexAction.ASK_CLARIFYING_QUESTION.value
    assert "按 profile 先追问材料" in attempt.audit_summary


def test_group_without_principal_mention_does_not_call_codex_or_send(
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
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.count_errors() == 0
    assert notifications == [
        {
            "title": "CEO DWS auth login required",
            "message": "Started dws auth login. Please complete DingTalk login.",
            "url": None,
        }
    ]
    assert codex.calls == []


def test_produce_once_suppresses_transient_list_unread_notification_until_threshold(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("transient discovery timeout", code="6"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0
    assert notifications == []
    assert worker.store.count_errors() == 0

    assert worker.produce_once() == 0

    assert notifications == [
        {
            "title": "CEO read unread conversations failed",
            "message": "transient discovery timeout",
            "url": None,
        }
    ]
    assert worker.store.count_errors() == 1
    state = json.loads(
        worker.store.get_service_state(
            "dws_transient_error_count:list_unread_conversations"
        )
        or "{}"
    )
    assert state["count"] == 3


def test_produce_once_clears_transient_list_unread_error_after_success(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("transient discovery timeout", code="6"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    dws.list_error = None
    assert worker.produce_once() == 0

    assert notifications == []
    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state(
            "dws_transient_error_count:list_unread_conversations"
        )
        or "{}"
    )
    assert state["count"] == 0
    assert state["last_error"] == ""


def test_produce_once_starts_dws_auth_login_once_for_login_error(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    auth_notifications = [
        notification
        for notification in notifications
        if notification["title"] == "CEO DWS auth login required"
    ]
    assert auth_notifications == [
        {
            "title": "CEO DWS auth login required",
            "message": "Started dws auth login. Please complete DingTalk login.",
            "url": None,
        }
    ]
    assert codex.calls == []


def test_produce_once_restarts_stale_persisted_dws_auth_login(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": "running", "pid": 99999999}),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    assert any(
        notification["title"] == "CEO DWS auth login required"
        for notification in notifications
    )


def test_produce_once_does_not_start_second_dws_auth_login_for_recent_request(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps(
            {
                "status": "running",
                "pid": 99999999,
                "started_at": "2026-05-13T16:45:00+00:00",
            }
        ),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 0
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "stale"
    assert state["pid"] == 99999999
    assert notifications == []


@pytest.mark.parametrize("previous_status", ["completed", "failed", "authenticated"])
def test_produce_once_restarts_dws_auth_login_after_previous_terminal_state(
    tmp_path: Path, monkeypatch, previous_status: str
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": previous_status, "pid": 1234}),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    assert any(
        notification["title"] == "CEO DWS auth login required"
        for notification in notifications
    )


def test_produce_once_marks_dws_auth_healthy_after_success(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": "completed", "pid": 1234}),
    )

    assert worker.produce_once() == 0

    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "authenticated"


def test_produce_once_continues_when_mention_recovery_fails(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_errors() == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert dws.unread_message_reads[0] == "cid-1"
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert codex.calls == []
    assert dws.sent == []


def test_produce_once_fast_path_reads_only_unread_messages_without_recent_context(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("历史上下文", message_id="msg-context")]},
    )
    dws.unread_messages = {"cid-1": [trigger]}
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert "cid-1" in dws.unread_message_reads
    assert dws.recent_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_fast_path_enqueues_pending_before_backoff(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert queued == 1
    assert "cid-1" in dws.unread_message_reads
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-unread"
    assert tasks[0].available_at == "2026-05-13 17:05:00"
    assert tasks[0].error == "waiting_fast_path_unread_backoff"
    assert worker.consume_once() == 0
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_fast_path_skips_bare_minutes_link_before_backoff(
    tmp_path: Path, monkeypatch, caplog
):
    caplog.set_level("INFO", logger="app.worker")
    minutes_id = "76327569643331373139373932355f313131333531383337385f30"
    trigger = message(
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8&creator=1113518378]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8&creator=1113518378)\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        message_id="msg-minutes-only",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.unread_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert queued == 0
    assert worker.store.count_reply_tasks() == 0
    assert attempts == []
    assert worker.store.has_seen("msg-minutes-only") is True
    assert "producer skipped message" in caplog.text
    assert "system_or_notification_message" in caplog.text
    assert codex.calls == []


def test_produce_once_fast_path_task_is_claimable_after_backoff(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )
    assert worker.produce_once() == 1

    claimed_before_backoff = worker.store.claim_reply_tasks(
        limit=1,
        now="2026-05-13 17:04:59",
    )
    claimed_after_backoff = worker.store.claim_reply_tasks(
        limit=1,
        now="2026-05-13 17:05:00",
    )

    assert claimed_before_backoff == []
    assert len(claimed_after_backoff) == 1
    assert claimed_after_backoff[0].status == "processing"
    assert claimed_after_backoff[0].error == "waiting_fast_path_unread_backoff"
    assert claimed_after_backoff[0].available_at == ""


def test_calendar_card_task_is_enriched_with_matching_pending_invite(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=True,
        message_type="calendar",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和组织者足以判断需要参加。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Vivian Memorial Park",
        start_time="2026-05-13T15:00:00-07:00",
        end_time="2026-05-13T16:00:00-07:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        attendees=["Alex Chen(明哥)", trigger.sender_name],
        status="confirmed",
    )
    search_start, search_end = worker._calendar_pending_invite_search_window(trigger)
    dws.calendar_events[f"{search_start}|{search_end}"] = [invite]

    queued = worker.produce_once()

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert queued == 1
    assert [task.trigger_message_id for task in tasks] == ["msg-calendar-card"]
    assert tasks[0].trigger_text == "[日程] Vivian Memorial Park"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.sender_open_dingtalk_id == "sender-1"
    assert merged.raw_payload["id"] == "invite-1"


def test_producer_enriches_bare_calendar_card_task_with_invite_details(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=True,
        message_type="calendar",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="测试开发岗位人选画像圆桌",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="讨论测试开发岗位画像和候选人结论。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        attendees=["Alex Chen(明哥)", trigger.sender_name],
        comments=["Alan: 请先看第一位弱不推荐候选人的材料。"],
        status="confirmed",
    )
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 1

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert [task.trigger_message_id for task in tasks] == ["msg-calendar-card"]
    assert tasks[0].trigger_text == "[日程] 测试开发岗位人选画像圆桌"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.raw_payload["id"] == "invite-1"
    assert merged.raw_payload["description"] == "讨论测试开发岗位画像和候选人结论。"
    assert merged.raw_payload["comments"] == [
        "Alan: 请先看第一位弱不推荐候选人的材料。"
    ]


def test_group_calendar_card_without_explicit_mention_is_ignored(
    tmp_path: Path, monkeypatch
):
    intro = message(
        "静默会，请大家先认真阅读会议描述，谢谢",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    intro.sender_name = "Claire"
    trigger.sender_name = "Claire"
    dws = FakeDws([conversation(single_chat=False)], {"cid-1": [intro, trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="官网反馈静默会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="请阅读官网反馈材料并给出修改建议。",
        organizer="Claire",
        self_response_status="needsAction",
        status="confirmed",
    )
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert tasks == []
    assert worker.store.has_seen("msg-calendar-intro") is False


def test_group_calendar_card_without_explicit_mention_does_not_use_context(
    tmp_path: Path, monkeypatch
):
    older_noise = message(
        "官网 review 需要关注客户表达、产品定位、agent 体验和上线风险。",
        message_id="msg-older-noise",
        single_chat=False,
    )
    older_noise.create_time = "2026-05-13 17:30:00"
    intro = message(
        "欢迎有兴趣的同学参与周二 09:00-10:00 的领先性讨论周会，"
        "下周二议题是产品部和售前团队分享。",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    intro.create_time = "2026-05-13 17:59:33"
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    intro.sender_name = "Robin"
    trigger.sender_name = "Robin"
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [older_noise, intro, trigger]},
        unread_messages={"cid-1": [trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.mark_seen("msg-older-noise", "cid-1")
    worker.store.mark_seen("msg-calendar-intro", "cid-1")
    unrelated = DwsCalendarEvent(
        event_id="invite-unrelated",
        title="官网 review",
        start_time="2026-05-14T12:00:00+08:00",
        end_time="2026-05-14T12:30:00+08:00",
        description="客户表达、产品定位、agent 体验和上线风险。",
        organizer="Claire",
        self_response_status="accepted",
        status="confirmed",
    )
    similar_accepted = DwsCalendarEvent(
        event_id="invite-similar-accepted",
        title="产品前瞻性和领先性讨论",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品领先性讨论。",
        organizer="Principal",
        self_response_status="accepted",
        status="confirmed",
    )
    sender_owned_unrelated = DwsCalendarEvent(
        event_id="invite-sender-unrelated",
        title="Friday memory MCP 安装",
        start_time="2026-05-15T18:00:00+08:00",
        end_time="2026-05-15T19:00:00+08:00",
        description="讨论 MCP 安装路径。",
        organizer="Robin",
        self_response_status="accepted",
        status="confirmed",
    )
    invite = DwsCalendarEvent(
        event_id="invite-context",
        title="领先性讨论周会（每周一收集、每周二讨论）",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品部和售前团队分享新的市场、客户需求和技术落地路径。",
        organizer="Alex Chen",
        self_response_status="tentative",
        status="confirmed",
    )
    later_recurrence = invite.model_copy(
        update={
            "event_id": "invite-context-next",
            "start_time": "2026-05-23T09:00:00+08:00",
            "end_time": "2026-05-23T10:00:00+08:00",
        }
    )
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [unrelated, similar_accepted, sender_owned_unrelated, invite, later_recurrence]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert tasks == []
    assert dws.recent_message_reads == []


def test_group_calendar_card_without_explicit_mention_does_not_refresh_pending_task(
    tmp_path: Path, monkeypatch
):
    intro = message(
        "欢迎参与周二 09:00-10:00 的领先性讨论周会，下周二议题是产品部分享。",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    intro.create_time = "2026-05-13 17:59:33"
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [intro, trigger]},
        unread_messages={"cid-1": [trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-calendar-card",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text="[日程]",
        trigger_message_json=trigger.model_dump_json(),
    )
    invite = DwsCalendarEvent(
        event_id="invite-context",
        title="领先性讨论周会（每周一收集、每周二讨论）",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品部分享新的市场和技术落地路径。",
        organizer="Alex Chen",
        self_response_status="tentative",
        status="confirmed",
    )
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(tasks) == 1
    assert tasks[0].trigger_text == "[日程]"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.raw_payload == {}


def test_fast_path_backoff_processes_trigger_when_unread_clears_without_user_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，先推进")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    assert worker.run_once() is None

    attempts = worker.store.list_reply_attempts(limit=10)
    assert "cid-1" in dws.unread_message_reads
    assert worker.store.count_reply_tasks(status="done") == 1
    assert len(attempts) == 1
    assert attempts[0].action == "send_reply"
    assert attempts[0].send_status == "sent"
    assert len(codex.calls) == 1
    assert final_sent(dws) == [
        (
            "cid-1",
            "@周俊杰 可以，先推进（by明哥分身）",
        )
    ]
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-unread",
            "sender-1",
            "@周俊杰 可以，先推进（by明哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [["sender-user-1"]]


def test_reply_agent_envelope_send_reply_is_delivered(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 帮我看下", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "可以，我看一下。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {"summary": "普通回复。", "documents": [], "confidence": 0.8},
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    assert final_sent(dws)[0] == ("cid-1", "可以，我看一下。（by明哥分身）")


def test_no_reply_agent_envelope_reaction_adds_emoji_without_text_reply(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message(
        "[群公告]群公告@所有人 咱们大问题都改的差不多了，日清并重新打包。",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "emoji",
                    "emoji": "👍",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "群公告无需正式回复，但适合用表情表示支持。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "reacted"
    assert dws.message_emojis == [("cid-1", "msg-1", "👍")]
    assert final_sent(dws) == []


def test_no_reply_agent_envelope_text_emotion_creates_and_adds_reaction(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) Hello磊哥，有后端开发工程师面试，我们线上等您哈")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "text_emotion",
                    "text": "我去摇人",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "只是呼叫本人进入会议，用文字表情轻量承接。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "reacted"
    assert attempt.send_error == "text_emotion: 我去摇人"
    assert dws.created_text_emotions == [("我去摇人", "我去摇人", "im_bg_5")]
    assert dws.message_text_emotions == [
        ("cid-1", "msg-1", "我去摇人", "created-1", "我去摇人", "created-bg")
    ]
    assert final_sent(dws) == []


def test_worker_creates_markdown_doc_for_long_reply_before_sending(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 帮我看下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    sent = final_sent(dws)
    sent_at_users = final_sent_at_users(dws)
    assert len(dws.created_markdown_docs) == 1
    assert dws.created_markdown_docs[0][0].startswith("CEO回复-Friday-")
    assert dws.created_markdown_docs[0][1].startswith("@周俊杰 " + "A" * 50)
    assert dws.doc_editor_permissions == [("doc-1", ["sender-user-1"])]
    assert len(sent) == 1
    assert "内容较长，我写成了文档：" in sent[0][1]
    assert "https://alidocs.dingtalk.com/i/nodes/doc-1" in sent[0][1]
    assert "【1/" not in sent[0][1]
    assert sent_at_users == [["sender-user-1"]]


def test_worker_creates_markdown_doc_when_decision_requests_document_reply(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="# 方案\n\n先按 A 路径推进。",
            sensitivity_kind=SensitivityKind.GENERAL,
            system_actions=[
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
                {
                    "type": "dws_markdown_document_reply",
                    "reply_text_ref": "user_response.text",
                    "title": "方案建议",
                },
            ],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    sent = final_sent(dws)
    assert dws.created_markdown_docs == [
        ("方案建议", "@周俊杰 # 方案\n\n先按 A 路径推进。（by明哥分身）")
    ]
    assert dws.doc_editor_permissions == [("doc-1", ["sender-user-1"])]
    assert len(sent) == 1
    assert "内容我写成了文档：方案建议" in sent[0][1]
    assert "https://alidocs.dingtalk.com/i/nodes/doc-1" in sent[0][1]


def test_worker_does_not_send_markdown_doc_link_when_permission_fails(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_editor_permission_error = DwsError("doc permission add failed")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert dws.created_markdown_docs
    assert final_sent(dws) == []
    assert attempts[-1].send_status == "failed"
    assert "doc permission add failed" in attempts[-1].send_error


def test_worker_does_not_fallback_group_send_when_native_reply_visibility_unconfirmed(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "0")
    trigger = message("@Alex Chen(明哥) 帮我看下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.reply_visible = False
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    assert len(dws.created_markdown_docs) == 1
    assert len(dws.reply_messages) == 1
    sent = final_sent(dws)
    assert len(sent) == 1
    assert sent[0][1].startswith("内容较长，我写成了文档：CEO回复-Friday-")
    assert final_sent_at_users(dws) == [["sender-user-1"]]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "sent"
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert "native_reply_visibility_unconfirmed" in sent_reply.send_result_json


def test_queued_task_falls_back_to_trigger_when_context_read_fails(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 这是新的工作流，效率可以提升很多",
        message_id="msg-context-error",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    dws.read_errors["cid-1"] = DwsError("forbidden request", code="1001")
    dws.unread_errors["cid-1"] = DwsError("forbidden request", code="1001")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="这个方向可以")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="MKT core",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once() == 1

    assert len(codex.calls) == 1
    assert "这是新的工作流" in codex.calls[0][0]
    assert final_sent(dws) == [("cid-1", "@周俊杰 这个方向可以（by明哥分身）")]
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        "cid-1",
        "msg-context-error",
    )
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    errors = worker.store.list_errors(limit=10)
    assert errors == []
    assert dws.recent_message_reads[0] == "cid-1"
    assert dws.recent_message_reads.count("cid-1") == 2
    assert dws.unread_message_reads == []


def test_fast_path_backoff_skips_when_current_user_replied_after_trigger(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    manual_reply = principal_message(
        "我已经处理了",
        message_id="msg-principal-after",
        create_time="2026-05-13 18:01:00",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    dws.messages = {"cid-1": [trigger, manual_reply]}
    dws.unread_messages = {"cid-1": []}
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    assert worker.run_once() is None

    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.list_reply_attempts(limit=10) == []
    assert worker.store.has_seen("msg-unread") is True
    assert codex.calls == []
    assert final_sent(dws) == []


def test_fast_path_backoff_skips_when_trigger_was_recalled_after_wait(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    recalled_trigger = trigger.model_copy(
        update={"raw_payload": {"messageStatus": "recalled"}}
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    dws.messages = {"cid-1": [recalled_trigger]}
    dws.unread_messages = {"cid-1": []}
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    assert worker.run_once() is None

    assert dws.messages_by_id_reads == [["msg-unread"]]
    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.list_reply_attempts(limit=10) == []
    assert worker.store.has_seen("msg-unread") is True
    assert codex.calls == []
    assert final_sent(dws) == []


def test_produce_once_fast_path_skips_unread_conversations_unchanged_since_last_check(
    tmp_path: Path, monkeypatch
):
    old_conversation = DingTalkConversation(
        open_conversation_id="cid-old",
        title="旧未读",
        single_chat=False,
        unread_point=2,
        last_message_create_at=1778662800000,
    )
    new_conversation = DingTalkConversation(
        open_conversation_id="cid-new",
        title="新未读",
        single_chat=False,
        unread_point=1,
        last_message_create_at=1778666400000,
    )
    new_trigger = message(
        "@Alex Chen(明哥) 新问题",
        message_id="msg-new",
    )
    new_trigger.open_conversation_id = "cid-new"
    dws = FakeDws(
        [old_conversation, new_conversation],
        {
            "cid-old": [message("@Alex Chen(明哥) 旧问题", message_id="msg-old")],
            "cid-new": [new_trigger],
        },
        unread_messages={"cid-new": [new_trigger]},
    )
    dws.mentioned_messages = {"cid-new": [new_trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )
    worker.store.set_service_state(
        "message_fast_path_checked_at",
        "2026-05-13T09:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert dws.unread_message_reads == ["cid-new"]
    assert dws.recent_message_reads == []


def test_produce_once_skips_recent_conversation_recovery_between_hourly_fallbacks(
    tmp_path: Path, monkeypatch
):
    recovered_conversation = DingTalkConversation(
        open_conversation_id="cid-recovered",
        title="最近处理过的单聊",
        single_chat=True,
        unread_point=0,
    )
    dws = FakeDws([], {"cid-recovered": [message("补充一下", message_id="msg-new")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id=recovered_conversation.open_conversation_id,
        title=recovered_conversation.title,
        single_chat=True,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen", recovered_conversation.open_conversation_id)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_produce_once_runs_recent_conversation_recovery_once_per_hour(
    tmp_path: Path, monkeypatch
):
    recovered_conversation = DingTalkConversation(
        open_conversation_id="cid-recovered",
        title="最近处理过的单聊",
        single_chat=True,
        unread_point=0,
    )
    old_message = message("之前处理过", message_id="msg-seen", single_chat=True)
    new_message = message("补充一下", message_id="msg-new", single_chat=True)
    new_message.create_time = "2026-05-13 18:05:00"
    dws = FakeDws([], {"cid-recovered": [old_message, new_message]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id=recovered_conversation.open_conversation_id,
        title=recovered_conversation.title,
        single_chat=True,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen", recovered_conversation.open_conversation_id)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert dws.recent_message_reads == ["cid-recovered"]
    assert dws.unread_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert (
        worker.store.get_service_state("message_recovery_checked_at")
        == "2026-05-13T17:00:00+00:00"
    )


def test_produce_once_does_not_recover_recent_group_conversations(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {"cid-group": [message("群里补充一下")]})
    dws.read_errors["cid-group"] = DwsError("forbidden request", code="1001")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id="cid-group",
        title="最近处理过的群聊",
        single_chat=False,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen-group", "cid-group")
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.list_errors() == []
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_current_user_candidate_filter_uses_only_local_identity_cache(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {}, current_user_error=RuntimeError("remote lookup"))
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    current_user_message = message(
        "我自己发的",
        sender_user_id="principal-user-1",
    )
    unknown_sender_message = message(
        "未知 sender",
        sender_user_id=None,
    )

    assert worker._is_current_user_message_for_candidate_filter(current_user_message)
    assert (
        worker._is_current_user_message_for_candidate_filter(unknown_sender_message)
        is False
    )
    assert dws.current_user_checks == []


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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个旧消息不用处理？")
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
    trigger = message("@Alex Chen(明哥) 这个消息还在24小时内？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    assert dws.sent == [("cid-1", "@周俊杰 先按A方案走（by明哥分身）")]


def test_repeated_produce_once_does_not_duplicate_pending_task(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_errors() == 1
    assert notifications == []
    assert codex.calls == []


def test_produce_once_suppresses_repeated_forbidden_unread_reads(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_errors={"cid-1": DwsError("forbidden request", code="1001")},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.get_service_state("dws_forbidden_conversations")

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_forbidden_read_cache_only_suppresses_during_short_cooldown(
    tmp_path: Path, monkeypatch
):
    trigger = message("窗口打开时也要能恢复", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    forbidden_until = (
        fixed_worker_now().astimezone(ZoneInfo("UTC"))
        + worker_module.DWS_FORBIDDEN_CONVERSATION_COOLDOWN
        - timedelta(seconds=1)
    ).isoformat()
    worker.store.set_service_state(
        "dws_forbidden_conversations",
        json.dumps({"cid-1": forbidden_until}),
    )

    messages = worker._read_conversation_messages(
        "read_recent_messages",
        conversation(single_chat=True),
        lambda: dws.read_recent_messages(conversation(single_chat=True)),
        default=[],
    )

    assert messages == []
    assert dws.recent_message_reads == []


def test_stale_forbidden_read_cache_does_not_block_recovered_single_chat(
    tmp_path: Path, monkeypatch
):
    trigger = message("窗口打开时也要能恢复", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    forbidden_until = (
        fixed_worker_now().astimezone(ZoneInfo("UTC"))
        + worker_module.DWS_FORBIDDEN_CONVERSATION_COOLDOWN
        + timedelta(hours=1)
    ).isoformat()
    worker.store.set_service_state(
        "dws_forbidden_conversations",
        json.dumps({"cid-1": forbidden_until}),
    )

    messages = worker._read_conversation_messages(
        "read_recent_messages",
        conversation(single_chat=True),
        lambda: dws.read_recent_messages(conversation(single_chat=True)),
        default=[],
    )

    assert [item.open_message_id for item in messages] == [trigger.open_message_id]
    assert dws.recent_message_reads == ["cid-1"]
    assert json.loads(
        worker.store.get_service_state("dws_forbidden_conversations") or "{}"
    ) == {}


def test_produce_once_does_not_notify_when_only_recent_context_read_fails(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_messages={"cid-1": [trigger]},
        read_errors={"cid-1": DwsError("temporary SYSTEM_ERROR", code="SYSTEM_ERROR")},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_errors() == 1
    assert notifications == []
    assert codex.calls == []


def test_consume_once_processes_queued_task(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", raising=False)
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert worker.store.count_reply_tasks(status="done") == 1
    assert final_sent(dws) == [("cid-1", "@周俊杰 先按A方案走（by明哥分身）")]


def test_sent_reply_enqueues_conversation_work_item(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen 这个项目需要 Alex 三天内给进展")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="请 Alex 三天内给进展。",
            reason="形成项目推进要求。",
            sensitivity_kind=SensitivityKind.GENERAL,
            audit_summary="上下文形成 P1 项目进展要求。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker._process_batch(
        conversation(),
        [trigger],
        [],
        ignore_existing_attempt=True,
    )

    claimed = worker.store.claim_work_summary_inputs(limit=1)
    attempts = worker.store.list_reply_attempts()
    assert len(claimed) == 1
    assert claimed[0].source_type == "reply_attempt"
    assert "Alex 三天内给进展" in claimed[0].payload_json
    assert append_signature("请 Alex 三天内给进展。") in attempts[0].final_reply_text


def test_consume_once_appends_feedback_links_when_configured(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    sent_text = final_sent(dws)[0][1]
    assert sent_text.startswith("@周俊杰 先按A方案走（by明哥分身）")
    assert "反馈：[👍](https://feedback.example.com/api/dingtalk-feedback-spike" in sent_text
    assert "rating=up" in sent_text
    assert "rating=down" in sent_text
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.feedback_token.startswith("spike_")
    assert sent_reply.feedback_token in sent_text
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.final_reply_text == sent_text


def test_consume_once_uses_required_feedback_prefix_after_unanswered_week(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.record_sent_reply(
        "cid-1",
        "old-msg-1",
        "旧回复",
        feedback_token="token-old",
    )
    with sqlite3.connect(worker.store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-05 18:00:00", "old-msg-1"),
        )
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    sent_text = final_sent(dws)[0][1]
    assert "请对我的服务提供反馈，长期不评价将跳过：" in sent_text
    assert "反馈：[👍]" not in sent_text
    assert "先按A方案走" in sent_text
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.feedback_token in sent_text


def test_consume_once_blocks_reply_after_unanswered_feedback_deadline(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.record_sent_reply(
        "cid-1",
        "old-msg-1",
        "旧回复",
        feedback_token="token-old",
    )
    with sqlite3.connect(worker.store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-02 18:00:00", "old-msg-1"),
        )
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    sent_text = final_sent(dws)[0][1]
    assert "请对我提供反馈后再提问" in sent_text
    assert "先按A方案走" not in sent_text
    assert "/api/dingtalk-feedback-spike" not in sent_text
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.feedback_token == ""


def test_consume_once_retries_task_failure_before_final_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_error=RuntimeError("temporary dws send failure"),
    )
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
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    error_kinds = [error.kind for error in worker.store.list_errors(limit=10)]
    assert "reply_task_retry" in error_kinds
    assert "reply_task" in error_kinds
    assert "CEO task failed: Friday" in [
        notification["title"] for notification in notifications
    ]


def test_consume_once_records_stale_processing_tasks_before_requeue(
    tmp_path: Path, monkeypatch
):
    notifications = []
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-29 11:26:41",
        trigger_sender="ET",
        trigger_text="@Alex Chen 这个怎么处理？",
    )
    claimed = store.claim_reply_tasks(limit=1)
    assert claimed[0].status == "processing"
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed[0].id,),
        )
    dws = FakeDws([conversation()], {"cid-1": []})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, audit_summary="无需回复。"))
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.consume_once(max_tasks=1)

    errors = store.list_errors()
    assert any(error.kind == "reply_task_stale" for error in errors)
    stale_error = next(error for error in errors if error.kind == "reply_task_stale")
    assert "Friday" in stale_error.detail
    assert "msg-1" in stale_error.detail
    assert notifications[0]["title"] == "CEO task retrying stale tasks"


def test_consume_once_authorization_failure_waits_without_final_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
        "app.worker.send_macos_notification",
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


def test_single_chat_rendered_schedule_asks_for_readable_calendar_detail(
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
    assert len(final_sent(dws)) == 1
    assert "只看到日程卡片" in final_sent(dws)[0][1]
    assert "请补充" in final_sent(dws)[0][1]
    assert dws.dings == []
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt is not None
    assert attempt.action == "ask_clarifying_question"
    assert attempt.send_status == "sent"
    assert attempt.codex_reason == "calendar_detail_unreadable"


def test_non_text_calendar_without_detail_asks_for_readable_calendar_detail(
    tmp_path: Path, monkeypatch
):
    trigger = message("日程卡片", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert len(final_sent(dws)) == 1
    assert "只看到日程卡片" in final_sent(dws)[0][1]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.codex_reason == "calendar_detail_unreadable"


def test_calendar_link_message_is_handled_as_calendar_invite(tmp_path: Path, monkeypatch):
    trigger = message(
        "好的明哥 dingtalk://dingtalkclient/action/open_mini_app?"
        "page=pages%2Fdetail%2Findex%3FuniqueId%3Dinvite-1%26recurrenceId%3D",
        single_chat=True,
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="国寿Demo思路",
        start_time="2026-05-30T14:00:00+08:00",
        end_time="2026-05-30T15:00:00+08:00",
        description="",
        organizer="韩露",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "国寿Demo思路" in prompt
    assert "会议描述：无" in prompt
    assert len(final_sent(dws)) == 1
    assert "请补充" in final_sent(dws)[0][1]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.codex_reason == "calendar_agent_needs_more_context"


def test_calendar_invite_still_injects_calendar_context_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "明哥看下这个日程 dingtalk://dingtalkclient/action/open_mini_app?"
        "page=pages%2Fdetail%2Findex%3FuniqueId%3Dinvite-1%26recurrenceId%3D",
        single_chat=True,
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Hyperion 客户复盘会",
        start_time="2026-05-30T14:00:00+08:00",
        end_time="2026-05-30T15:00:00+08:00",
        description="复盘 Hyperion 客户反馈，并确认下周跟进材料。",
        organizer="韩露",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="日程上下文足够判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "Hyperion 客户复盘会" in prompt
    assert "复盘 Hyperion 客户反馈，并确认下周跟进材料。" in prompt
    assert dws.read_doc_calls == []
    assert dws.download_doc_calls == []
    assert dws.search_document_calls == []


def test_bare_calendar_card_uses_unique_pending_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Preseen x Walmart",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和组织者足以判断需要参加客户会议。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程；标题和组织者足以判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "Preseen x Walmart" in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "标题和组织者足以判断需要参加客户会议。（by明哥分身）")
    ]
    assert dws.calendar_responses == [("invite-1", "accepted")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "标题和组织者足以判断需要参加客户会议。"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"
    assert attempt.calendar_response_result_json == '{"success": true}'


def test_calendar_response_organizer_error_is_terminal_noop(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="项目组反馈讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    dws.calendar_response_error = DwsError(
        "code: 300000, developerMessage: Cannot change response status of event organizer.",
        code="300000",
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="组织者本人不需要文字回复。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [("cid-1", "组织者本人不需要文字回复。（by明哥分身）")]
    assert dws.calendar_responses == [("invite-1", "accepted")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""
    assert attempt.calendar_response_status == "accepted"
    assert json.loads(attempt.calendar_response_result_json) == {
        "message": "Cannot change response status of event organizer",
        "noop_reason": "calendar_event_organizer",
        "success": True,
    }


def test_send_reply_calendar_response_failure_does_not_send_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户续约方案讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_response_error = DwsError("calendar accept failed", code="500")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="我先参加，重点看续约方案。",
            reason="客户续约会议有明确业务价值，应该参加。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker._process_batch(
        dws.conversations[0],
        [trigger],
        [trigger],
        calendar_response_event=invite,
    )

    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.send_status == "failed"
    assert attempt.send_error == "calendar accept failed"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"
    assert worker.store.has_seen("msg-1") is False


def test_rendered_calendar_card_without_message_type_uses_unique_pending_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="MB 营销proposal 终版确认",
        start_time="2026-06-04T10:00:00+08:00",
        end_time="2026-06-04T11:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题足以判断先暂定。",
            calendar_response_status="tentative",
            audit_summary="已按唯一待响应日程匹配裸日程卡片。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "MB 营销proposal 终版确认" in codex.calls[0][0]
    assert final_sent(dws) == []
    assert dws.calendar_responses == [("invite-1", "tentative")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "no_reply"
    assert attempt.codex_reason == "标题足以判断先暂定。"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "tentative"


def test_bare_calendar_card_enriches_sender_pending_invites_to_match_recent_create_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="吴柯欣 - 招聘专员 - 三面",
        start_time="2026-06-07T13:30:00+08:00",
        end_time="2026-06-07T14:30:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    enriched_invite = invite.model_copy(
        update={
            "description": "候选人：吴柯欣\n岗位：招聘专员\n轮次：三面",
            "created_ms": int(
                datetime(
                    2026,
                    5,
                    13,
                    18,
                    0,
                    0,
                    tzinfo=ZoneInfo("Asia/Shanghai"),
                ).timestamp()
                * 1000
            ),
        }
    )
    older_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="HR 周例会",
        start_time="2026-06-08T13:30:00+08:00",
        end_time="2026-06-08T14:45:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite, older_invite]
    dws.calendar_event_details["invite-1"] = enriched_invite
    dws.calendar_event_details["invite-2"] = older_invite
    dws.calendar_events[f"{enriched_invite.start_time}|{enriched_invite.end_time}"] = [
        enriched_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="已读取候选人面试日程并接受。",
            calendar_response_status="accepted",
            audit_summary="已通过详情接口读取刚创建的待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.calendar_event_detail_calls == ["invite-1", "invite-2"]
    assert len(codex.calls) == 1
    assert "吴柯欣 - 招聘专员 - 三面" in codex.calls[0][0]
    assert "候选人：吴柯欣" in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "已读取候选人面试日程并接受。（by明哥分身）")
    ]
    assert dws.calendar_responses == [("invite-1", "accepted")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"


def test_existing_dry_run_calendar_response_is_executed_without_rerunning_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不应该重新生成",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="标题足以判断需要接受。",
        calendar_event_id="invite-1",
        calendar_response_status="accepted",
        send_status="dry_run",
    )

    worker.run_once()

    assert codex.calls == []
    assert dws.calendar_responses == [("invite-1", "accepted")]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "calendar"
    assert attempt.send_error == ""
    assert attempt.calendar_response_result_json == '{"success": true}'
    assert worker.store.has_seen(trigger.open_message_id) is True


def test_calendar_response_respects_worker_dry_run(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户方案确认",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题足以判断需要接受。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"
    assert attempt.calendar_response_result_json == ""


def test_bare_calendar_card_uses_already_accepted_invite_as_context(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="主持会议",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="主持人需要参加。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个日程已经接受，后面按会议主题准备。",
            reason="日程已经接受，标题和描述足够判断。",
            audit_summary="已按消息时间匹配同一发送人刚创建的日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "主持会议" in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "这个日程已经接受，后面按会议主题准备。（by明哥分身）")
    ]
    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "日程已经接受，标题和描述足够判断。"
    assert attempt.calendar_event_id == "invite-1"


def test_bare_calendar_card_uses_unique_future_accepted_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【圆桌讨论】测试开发岗位人选画像",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="讨论测试开发岗位画像和候选人结论。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        comments=["Alan: 第一位候选人弱不推荐，需要会上定取舍。"],
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="已看到圆桌会，按测试岗位画像和候选人结论来准备。",
            reason="同发送人的唯一未来日程已经匹配。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "【圆桌讨论】测试开发岗位人选画像" in codex.calls[0][0]
    assert "讨论测试开发岗位画像和候选人结论" in codex.calls[0][0]
    assert "第一位候选人弱不推荐，需要会上定取舍" in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "已看到圆桌会，按测试岗位画像和候选人结论来准备。（by明哥分身）")
    ]
    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.calendar_event_id == "invite-1"


def test_bare_calendar_card_uses_closest_recent_pending_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    matched_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="售前候选人二面",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    nearby_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="管理工作讨论",
        start_time="2026-05-17T09:00:00+08:00",
        end_time="2026-05-17T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms + 2 * 60 * 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [matched_invite, nearby_invite]
    dws.calendar_events[f"{matched_invite.start_time}|{matched_invite.end_time}"] = [
        matched_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="候选人二面需要参加。",
            calendar_response_status="accepted",
            audit_summary="已按消息时间匹配最近创建的待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "售前候选人二面" in codex.calls[0][0]
    assert "管理工作讨论" not in codex.calls[0][0]
    assert dws.calendar_responses == [("invite-1", "accepted")]


def test_bare_calendar_card_uses_single_chat_sender_attendee_invite(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="管理工作讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="会议主要结论",
        organizer="系统日历",
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
        attendees=[trigger.sender_name],
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和描述足以判断需要参加。",
            calendar_response_status="accepted",
            audit_summary="已按消息时间匹配刚创建的本人待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "管理工作讨论" in codex.calls[0][0]
    assert len(final_sent(dws)) == 1
    assert final_sent(dws)[0][1] == "标题和描述足以判断需要参加。（by明哥分身）"
    assert dws.calendar_responses == [("invite-1", "accepted")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.calendar_event_id == "invite-1"


def test_bare_calendar_card_ignores_sender_pending_invite_changed_too_early(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="过早创建的会议",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 6 * 60 * 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [invite]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert len(final_sent(dws)) == 1
    assert "只看到日程卡片" in final_sent(dws)[0][1]
    assert "过早创建的会议" not in final_sent(dws)[0][1]
    assert dws.calendar_responses == []


def test_bare_calendar_card_does_not_guess_multiple_pending_invites(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [
        DwsCalendarEvent(
            event_id="invite-1",
            title="客户会 A",
            start_time="2026-05-16T09:00:00+08:00",
            end_time="2026-05-16T10:00:00+08:00",
            organizer=trigger.sender_name,
            self_response_status="needsAction",
            status="confirmed",
        ),
        DwsCalendarEvent(
            event_id="invite-2",
            title="客户会 B",
            start_time="2026-05-17T09:00:00+08:00",
            end_time="2026-05-17T10:00:00+08:00",
            organizer=trigger.sender_name,
            self_response_status="needsAction",
            status="confirmed",
        ),
    ]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert len(final_sent(dws)) == 1
    assert "只看到日程卡片" in final_sent(dws)[0][1]
    assert "客户会 A" not in final_sent(dws)[0][1]
    assert "客户会 B" not in final_sent(dws)[0][1]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.codex_reason == "calendar_detail_unreadable"


def test_bare_calendar_card_uses_near_upcoming_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    near_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】审工资",
        start_time="2026-05-14T14:30:00+08:00",
        end_time="2026-05-14T15:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    later_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="管理周会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [near_invite, later_invite]
    dws.calendar_events[f"{near_invite.start_time}|{near_invite.end_time}"] = [
        near_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和时间足以判断需要接受这次静默会。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "【静默会】审工资" in codex.calls[0][0]
    assert "管理周会" not in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "标题和时间足以判断需要接受这次静默会。（by明哥分身）")
    ]
    assert dws.calendar_responses == [("invite-1", "accepted")]


def test_bare_calendar_card_uses_pending_invite_created_near_message(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    older_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户会 A",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 2 * 24 * 60 * 60 * 1000,
    )
    matched_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="Mike项目结项会",
        start_time="2026-05-17T09:00:00+08:00",
        end_time="2026-05-17T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events[
        "2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"
    ] = [older_invite, matched_invite]
    dws.calendar_events[
        f"{matched_invite.start_time}|{matched_invite.end_time}"
    ] = [matched_invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "Mike项目结项会" in codex.calls[0][0]
    assert "客户会 A" not in codex.calls[0][0]
    assert len(final_sent(dws)) == 1
    assert "请补充" in final_sent(dws)[0][1]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.codex_reason == "calendar_agent_needs_more_context"


def test_calendar_retry_ignores_old_system_notification_skip(
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
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    old_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Chat",
        trigger_message_id="msg-1",
        trigger_sender="sender",
        trigger_text="[日程]",
        action=CodexAction.NO_REPLY.value,
        sensitivity_kind="general",
        codex_reason="system_or_notification_message",
        send_status="skipped",
    )
    worker.store.update_reply_attempt(old_attempt_id, send_error="no_reply")

    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Chat",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    worker.consume_once()

    assert len(codex.calls) == 1
    assert len(final_sent(dws)) == 1
    assert "请补充" in final_sent(dws)[0][1]
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id == old_attempt_id
    assert latest.action == "ask_clarifying_question"
    assert latest.codex_reason == "calendar_agent_needs_more_context"
    assert worker.store.count_reply_attempts() == 1


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
        self_response_status="accepted",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite, existing]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="这场和产品周会冲突，请补充为什么需要优先于现有日程。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "日历冲突检查" in prompt
    assert "客户复盘" in prompt
    assert "产品周会" in prompt
    assert "会议描述：无" in prompt
    assert len(final_sent(dws)) == 1
    assert "请补充" in final_sent(dws)[0][1]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "ask_clarifying_question"
    assert attempt.codex_reason == "calendar_agent_needs_more_context"
    assert attempt.send_status == "sent"


def test_calendar_invite_ignores_declined_overlapping_event(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目结项会",
        start_time="2026-06-05T10:30:00+08:00",
        end_time="2026-06-05T11:30:00+08:00",
        description="",
        organizer="王天浩",
    )
    declined_existing = DwsCalendarEvent(
        event_id="event-1",
        title="销售周会",
        start_time="2026-06-05T10:00:00+08:00",
        end_time="2026-06-05T12:00:00+08:00",
        description="到期续约",
        status="confirmed",
        self_response_status="declined",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [
        invite,
        declined_existing,
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="已拒绝过重叠会议，标题足以判断新会议可接受。",
            calendar_response_status="accepted",
            audit_summary="已读取日程；重叠会议是 declined，不构成冲突。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "Mike项目结项会" in codex.calls[0][0]
    assert "销售周会" not in codex.calls[0][0]
    assert final_sent(dws) == [
        ("cid-1", "已拒绝过重叠会议，标题足以判断新会议可接受。（by明哥分身）")
    ]
    assert dws.calendar_responses == [("invite-1", "accepted")]


def test_calendar_invite_ignores_pending_overlapping_event(
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
    pending_existing = DwsCalendarEvent(
        event_id="event-1",
        title="待确认会议",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="待确认",
        status="confirmed",
        self_response_status="needsAction",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [
        invite,
        pending_existing,
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert "客户复盘" in codex.calls[0][0]
    assert "待确认会议" not in codex.calls[0][0]
    assert len(final_sent(dws)) == 1
    assert "请补充" in final_sent(dws)[0][1]


def test_calendar_invite_without_description_can_be_tentative_without_conflict(
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
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题看起来相关但价值不够明确，先暂定。",
            calendar_response_status="tentative",
            audit_summary="已读取日程；标题足以判断先暂定，不需要聊天追问。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "客户复盘" in prompt
    assert "2026-05-14T10:00:00+08:00" in prompt
    assert "会议描述：无" in prompt
    assert "最近上下文事项" in prompt
    assert "标题足以判断" in prompt
    assert "本人有必要参加" in prompt
    assert final_sent(dws) == []
    assert dws.calendar_responses == [("invite-1", "tentative")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "no_reply"
    assert attempt.codex_reason == "标题看起来相关但价值不够明确，先暂定。"


def test_calendar_invite_with_description_asks_codex_to_evaluate_conflict(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户升级问题决策",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="需要 Alex 判断是否承诺本周交付，客户 CEO 会参加。",
        organizer="Mina",
    )
    existing = DwsCalendarEvent(
        event_id="event-1",
        title="产品周会",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="固定例会",
        self_response_status="accepted",
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
    assert "如果信息不足，再回复对方原因并请补充" in prompt
    assert len(final_sent(dws)) == 1
    assert "客户升级问题优先级更高" in final_sent(dws)[0][1]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "calendar_conflict_evaluated"


def test_calendar_invite_for_document_review_replies_to_use_document_comment(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="官网文档批阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="请 Alex 批阅官网文档并反馈修改意见。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="请直接@我文档让我批阅即可，只有存疑再约会。",
            reason="calendar_document_review_redirect",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "日历规则判断" in prompt
    assert "请直接@我文档让我批阅即可，只有存疑再约会。" in prompt
    assert "请直接@我文档让我批阅即可，只有存疑再约会。" in final_sent(dws)[0][1]
    assert dws.calendar_responses == []


def test_calendar_static_review_description_must_process_task_before_document_redirect(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】官网反馈",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=(
            "请根据官网反馈截图和评论直接给处理结论："
            "上线前必须改、后续可优化，并具体到把 A 改成 B。"
        ),
        organizer="Mina",
        comments=["Mina: 重点看首屏定位和客户案例模块，处理完请评论会议。"],
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text=(
                "可以，这个静默会我直接处理：上线前先收敛首屏 CTA 和表单跳转；"
                "后续再优化客户案例的排序。"
            ),
            reason="calendar_static_review_task_processed",
            calendar_response_status="accepted",
            audit_summary="根据静默会描述直接处理官网反馈任务。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "这条规则优先于普通文档批阅转交规则" in prompt
    assert "不能只接受日历" in prompt
    assert "不能只要求对方改去文档里 @" in prompt
    assert "上线前必须改、后续可优化" in prompt
    assert "会议评论：Mina: 重点看首屏定位和客户案例模块，处理完请评论会议。" in prompt
    assert "只有当日程不是静默会" in prompt
    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert "请直接@我文档让我批阅即可" not in final_sent(dws)[0][1]
    assert "上线前先收敛首屏 CTA" in final_sent(dws)[0][1]


def test_calendar_response_accepts_agent_envelope_domain_payload(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="产品周会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="讨论客户升级问题。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "这个会议需要参加，我会按客户升级问题准备。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {"calendar_response_status": "accepted"},
            "audit": {
                "summary": "根据日程标题和描述判断需要参加。",
                "documents": [],
                "confidence": 0.8,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert "客户升级问题" in final_sent(dws)[0][1]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.calendar_response_status == "accepted"


def test_calendar_static_review_reads_minutes_accepts_and_comments_material(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    target_url = (
        "https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true"
    )
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】测试开发工程师 - 候选人A 作业题审阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=f"请阅读听记和作业材料后给处理结论：{target_url}",
        organizer=trigger.sender_name,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.minutes_infos[minutes_id] = {
        "result": {
            "taskUuid": minutes_id,
            "title": "候选人A测试开发技术面记录",
            "url": f"https://shanji.dingtalk.com/app/transcribes/{minutes_id}",
        }
    }
    dws.minutes_summaries[minutes_id] = {
        "result": {"fullSummary": "候选人测试开发基础较完整，但 Agent 工程化偏浅。"}
    }
    dws.minutes_todos[minutes_id] = {
        "result": {"actions": ['{"value":"Alex 给出是否推进录用的处理结论"}']}
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不建议直接推进，建议补充作业后再判断。",
            reason="静默会材料已处理，并接受日程。",
            calendar_response_status="accepted",
            audit_summary="读取静默会听记后给出处理结论。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.minutes_info_calls == [minutes_id]
    assert dws.minutes_summary_calls == [minutes_id]
    assert dws.minutes_todo_calls == [minutes_id]
    prompt = codex.calls[0][0]
    assert "不能只接受日历" in prompt
    assert "AI 听记材料:" in prompt
    assert "候选人测试开发基础较完整" in prompt
    assert "Alex 给出是否推进录用的处理结论" in prompt
    signed_reply = "不建议直接推进，建议补充作业后再判断。（by明哥分身）"
    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert dws.doc_comments == [(target_url, signed_reply)]
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"
    assert attempt.calendar_response_result_json == '{"success": true}'


def test_calendar_material_read_failure_is_passed_to_codex(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/no-access"
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】材料审阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=f"请阅读材料后给处理结论：{doc_url}",
        organizer=trigger.sender_name,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.doc_infos[doc_url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在没有权限读取这份材料，麻烦补充正文或开权限。",
            reason="calendar_material_unreadable",
            calendar_response_status="accepted",
            audit_summary="静默会材料读取失败，已说明不能判断正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.doc_info_calls == [doc_url]
    assert dws.read_doc_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "不能只接受日历" in prompt
    assert "已获取的钉钉材料:" in prompt
    assert "材料读取失败" in prompt
    assert "不能臆测材料内容" in prompt
    assert "forbidden.accessDenied" in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "ask_clarifying_question"
    assert attempt.send_status == "sent"
    assert attempt.codex_reason == "calendar_material_unreadable"


def test_calendar_invite_with_clear_value_auto_accepts_without_chat_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="关键客户交付决策",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="客户 CEO 参加，需要 Alex 判断本周交付承诺。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="Alex 参与有明确业务价值",
            calendar_response_status="accepted",
            audit_summary="日程描述明确，且需要 Alex 做关键客户交付判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert final_sent(dws) == [("cid-1", "Alex 参与有明确业务价值（by明哥分身）")]
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "Alex 参与有明确业务价值"
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"
    assert attempt.calendar_response_result_json == '{"success": true}'


def test_rerun_calendar_card_recovers_event_from_existing_attempt(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目同步",
        start_time="2026-06-08T12:30:00+08:00",
        end_time="2026-06-08T13:00:00+08:00",
        description="客户拜访前同步当前项目情况和后续计划。",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_event_details["invite-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="客户拜访前需要同步项目情况和后续计划，有必要参加。",
            calendar_response_status="accepted",
            audit_summary="已从既有 attempt 恢复日历详情并判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="no_reply",
        sensitivity_kind="general",
        calendar_event_id="invite-1",
        calendar_response_status="accepted",
        send_status="calendar",
    )

    processed_message_id = worker.rerun_message(
        conversation(single_chat=True),
        trigger.open_message_id,
        force_new_decision=True,
    )

    assert processed_message_id == trigger.open_message_id
    assert dws.calendar_event_detail_calls == ["invite-1"]
    assert len(codex.calls) == 1
    assert "Mike项目同步" in codex.calls[0][0]
    assert "客户拜访前同步当前项目情况和后续计划" in codex.calls[0][0]
    assert "没有读到会议标题" not in final_sent(dws)[0][1]
    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert final_sent(dws) == [
        ("cid-1", "客户拜访前需要同步项目情况和后续计划，有必要参加。（by明哥分身）")
    ]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"


def test_rerun_calendar_card_matches_already_accepted_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目同步",
        start_time="2026-05-14T12:30:00+08:00",
        end_time="2026-05-14T13:00:00+08:00",
        description="客户拜访前同步当前项目情况和后续计划。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="客户拜访前需要同步项目情况和后续计划，有必要参加。",
            calendar_response_status="accepted",
            audit_summary="已从同发送人的已接受日程恢复详情并判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    search_start, search_end = worker._calendar_pending_invite_search_window(trigger)
    dws.calendar_events[f"{search_start}|{search_end}"] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="ask_clarifying_question",
        sensitivity_kind="general",
        codex_reason="calendar_detail_unreadable",
        send_status="sent",
    )

    processed_message_id = worker.rerun_message(
        conversation(single_chat=True),
        trigger.open_message_id,
        force_new_decision=True,
    )

    assert processed_message_id == trigger.open_message_id
    assert len(codex.calls) == 1
    assert "Mike项目同步" in codex.calls[0][0]
    assert dws.calendar_responses == [("invite-1", "accepted")]
    assert final_sent(dws) == [
        ("cid-1", "客户拜访前需要同步项目情况和后续计划，有必要参加。（by明哥分身）")
    ]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.calendar_event_id == "invite-1"
    assert attempt.calendar_response_status == "accepted"


def test_calendar_invite_no_reply_without_auto_accept_reason_does_not_accept(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="not relevant",
            audit_summary="不需要处理。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "no_reply"


def test_calendar_invite_agent_can_decline_without_chat_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="状态同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息，不需要 Alex 输入。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="会议只是状态同步，不需要本人参加。",
            calendar_response_status="declined",
            audit_summary="已读取日程；描述显示只是同步信息，不需要本人输入。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert dws.calendar_responses == [("invite-1", "declined")]
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "no_reply"
    assert attempt.codex_reason == "会议只是状态同步，不需要本人参加。"
    assert attempt.send_status == "calendar"
    assert attempt.send_error == ""


def test_queued_calendar_response_completes_task_with_terminal_attempt_update(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="状态同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息，不需要 Alex 输入。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="会议只是状态同步，不需要本人参加。",
            calendar_response_status="declined",
            audit_summary="已读取日程；描述显示只是同步信息，不需要本人输入。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    def fail_outer_task_completion(task_id: int) -> None:
        raise AssertionError(
            "calendar terminal attempt should complete the queued task"
        )

    monkeypatch.setattr(worker.store, "complete_reply_task", fail_outer_task_completion)

    assert worker.consume_once(max_tasks=1) == 1

    assert dws.calendar_responses == [("invite-1", "declined")]
    assert worker.store.count_reply_tasks(status="done") == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.send_status == "calendar"
    assert attempt.calendar_response_status == "declined"


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
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_single_chat_alidocs_card_reaches_codex_as_material_reference(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/weekly123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/weekly123"
    trigger = message(
        "\n".join(
            [
                "总裁办每周讨论-20260531",
                "![image](https://gw.alicdn.com/imgextra/i4/example.png)",
                "字段一: A",
                "字段二: B",
                f"[{doc_url}]({doc_url})",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这份周会材料需要先读材料再判断。",
            audit_summary="私聊文档卡片已进入 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_doc_url in prompt
    assert "dws doc read --node" in prompt
    assert "本周重点：处理项目 owner" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert "先读材料再判断" in attempt.final_reply_text
    assert final_sent(dws) == [
        (
            "cid-1",
            "这份周会材料需要先读材料再判断。（by明哥分身）",
        )
    ]
    assert attempt.audit_summary == "私聊文档卡片已进入 agent 判断。"


def test_structured_approval_card_is_processed_by_oa_handler(
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
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_handler.calls) == 1
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.sensitivity_kind == "internal_personnel"
    assert attempt.send_status == "skipped"
    assert attempt.draft_reply_text == "请补充预算来源和项目归属后重新提交。"
    assert attempt.final_reply_text == "请补充预算来源和项目归属后重新提交。"
    assert attempt.audit_summary == "缺少预算来源和项目归属，按审批规则拒绝。"
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
    assert attempt.oa_action == "拒绝"
    assert attempt.oa_remark == "请补充预算来源和项目归属后重新提交。"
    assert oa_handler.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "拒绝",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert json.loads(attempt.oa_action_result_json) == {
        "errcode": 0,
        "errmsg": "ok",
    }


def test_oa_return_action_is_left_as_approval_comment_instead_of_reject(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]张静提醒您审批他的费用报销 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_handler = ReturnOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    assert dws.oa_approval_comments == [
        ("proc-1", "请补充预算来源和项目归属后重新提交。")
    ]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.oa_action == "退回"
    assert attempt.send_status == "commented"
    assert attempt.send_error == ""
    assert json.loads(attempt.oa_action_result_json) == {
        "errcode": 0,
        "errmsg": "ok",
    }


def test_existing_commented_oa_attempt_is_terminal(tmp_path: Path, monkeypatch):
    trigger = message(
        "[Ding]张静提醒您审批他的录用申请 https://aflow.dingtalk.com/dingtalk/pc/query"
        "/pchomepage.htm?procInstId=proc-1&taskId=task-1&swfrom=oa",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
        oa_approval_handler=oa_handler,
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="oa_approval",
        sensitivity_kind="internal_finance",
        codex_reason="退回",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补充预算来源。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )

    worker.run_once()

    assert codex.calls == []
    assert oa_handler.calls == []
    assert dws.oa_approval_actions == []
    assert dws.oa_approval_comments == []
    assert worker.store.has_seen("msg-1") is True


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
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


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
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


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
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


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
    trigger = message("@明哥 https://example.com/a", single_chat=True)
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
        "@明哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1]"
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
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


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


def test_ding_approval_reminder_is_processed_by_oa_handler(
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
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_handler.calls) == 1
    assert oa_handler.calls[0][2] == ""
    assert oa_handler.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "拒绝",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 1
    assert worker.store.get_reply_attempt(1).action == "oa_approval"


def test_oa_approval_missing_target_records_review_without_executing_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_handler = MissingTargetOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "skipped"
    assert attempt.oa_process_instance_id == ""
    assert attempt.oa_task_id == ""
    assert attempt.final_reply_text == "材料不足，暂不执行审批动作。"
    assert attempt.send_error == "missing_oa_approval_target"


def test_oa_approval_uses_worker_url_target_when_agent_omits_identifiers(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_handler = MissingTargetOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "commented"
    assert attempt.send_error == ""
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url == (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    assert dws.oa_approval_comments == [
        ("proc-1", "材料不足，暂不执行审批动作。")
    ]


def test_oa_approval_does_not_execute_task_that_is_not_current_user(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "tasks": [
                {"taskid": "task-1", "task_status": "CANCELED", "userid": "principal-user-1"},
                {"taskid": "task-2", "task_status": "RUNNING", "userid": "other-user"},
            ]
        }
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == ""
    assert attempt.send_error == "oa_task_not_current_user"


def test_ding_approval_reminder_injects_openapi_detail_when_dws_form_is_empty(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="刘瑞安提交的录用申请",
            process_name="录用申请",
        )
    ]
    dws.oa_approval_details["proc-1"] = {
        "result": {"formValueVOS": [{"details": []}]}
    }
    dws.oa_approval_records["proc-1"] = {"result": {"operationRecords": []}}
    dws.oa_approval_tasks["proc-1"] = {"result": {"taskIdList": [{"taskId": 1}]}}
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "刘瑞安提交的录用申请",
            "form_component_values": [
                {"name": "试用期工作内容和转正要求", "value": "3个月内完成 Friday 场景闭环"}
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail_text = oa_handler.approval_detail_texts[0]
    assert "\"process_instance_id\": \"proc-1\"" in detail_text
    assert "openapi_detail" in detail_text
    assert "试用期工作内容和转正要求" in detail_text
    assert "3个月内完成 Friday 场景闭环" in detail_text


def test_oa_approval_detail_always_includes_openapi_comments(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]郑威格提醒您审批他的项目立项 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = {
        "result": {
            "formValueVOS": [
                {"name": "项目名称", "value": "奥迪第三曲线项目"}
            ]
        }
    }
    dws.oa_approval_records["proc-1"] = {
        "result": {
            "operationRecords": [
                {"operationType": "ADD_REMARK", "userId": "principal-user-1"}
            ]
        }
    }
    dws.oa_approval_tasks["proc-1"] = {
        "result": {"taskIdList": [{"taskId": "task-1"}]}
    }
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "郑威格提交的项目立项全流程（第三曲线）",
            "form_component_values": [
                {"name": "项目名称", "value": "奥迪第三曲线项目"}
            ],
            "operation_records": [
                {
                    "operation_type": "ADD_REMARK",
                    "userid": "principal-user-1",
                    "remark": "证据不严谨，需要补充模型对比结论。",
                }
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert detail["dws_detail"]["result"]["formValueVOS"][0]["value"] == (
        "奥迪第三曲线项目"
    )
    assert detail["openapi_detail"]["process_instance"]["operation_records"][0][
        "remark"
    ] == "证据不严谨，需要补充模型对比结论。"


def test_oa_approval_detail_param_error_is_recovered_by_openapi(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]郑威格提醒您审批他的项目立项 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError(
        "dws command failed: server_error_code=PARAM_ERROR",
        code="1",
    )
    dws.oa_approval_records["proc-1"] = {
        "result": {"operationRecords": [{"operationType": "START_PROCESS_INSTANCE"}]}
    }
    dws.oa_approval_tasks["proc-1"] = {
        "result": {"taskIdList": [{"taskId": "task-1"}]}
    }
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "郑威格提交的项目立项全流程（第三曲线）",
            "form_component_values": [
                {"name": "项目名称", "value": "奥迪第三曲线项目"}
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert "dws_detail" not in detail
    assert detail["openapi_detail"]["process_instance"]["title"] == (
        "郑威格提交的项目立项全流程（第三曲线）"
    )
    assert detail["dws_detail_status"]["status"] == "recovered_by_openapi"
    assert "worker already recovered the approval detail through OpenAPI" in detail[
        "dws_detail_status"
    ][
        "message"
    ]
    assert "Do not call dws oa approval detail again" in detail[
        "dws_detail_status"
    ]["message"]
    assert "--format raw" in detail["dws_detail_status"]["message"]
    assert "--fields" in detail["dws_detail_status"]["message"]


def docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        "<w:p><w:r><w:t>"
        + paragraph
        + "</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_oa_approval_detail_searches_and_reads_openapi_attachments(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]郑威格提醒您审批他的项目立项", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="郑威格提交的项目立项全流程（第三曲线）",
            process_name="项目立项全流程（第三曲线）",
        )
    ]
    dws.oa_approval_details["proc-1"] = {
        "result": {"formValueVOS": [{"details": []}]}
    }
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "郑威格提交的项目立项全流程（第三曲线）",
            "form_component_values": [
                {
                    "name": "项目实施计划文档链接",
                    "component_type": "DDAttachment",
                    "value": json.dumps(
                        [
                            {
                                "fileName": "项目实施计划（第三曲线大模型解决方案）(2)(2)(1).docx",
                                "fileId": "224596585916",
                                "spaceId": "671896910",
                                "fileType": "docx",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                }
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    dws.document_search_results["项目实施计划（第三曲线大模型解决方案）"] = [
        DwsDocumentSearchResult(
            node_id="doc-1",
            name="项目实施计划（第三曲线大模型解决方案）",
            extension="adoc",
            content_type="ALIDOC",
        )
    ]
    dws.docs["doc-1"] = {
        "markdown": "## 项目范围\n\n"
    }
    dws.oa_attachment_downloads[("proc-1", "224596585916")] = docx_bytes(
        [
            "项目范围",
            "本项目包含奥迪 ADAS 场景挖掘、搜索界面开发和标注平台优化。",
            "项目里程碑 T+30 交付全量切片数据，T+60 交付完整搜索网页。",
        ]
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert dws.download_oa_attachment_calls == [("proc-1", "224596585916")]
    assert dws.search_document_calls == [
        ("项目实施计划（第三曲线大模型解决方案）", 5)
    ]
    assert dws.read_doc_calls == ["doc-1"]
    fallback = detail["oa_attachment_fallbacks"][0]
    assert fallback["file_id"] == "224596585916"
    assert "T+60 交付完整搜索网页" in fallback["downloaded_attachment"]["text"]
    assert fallback["matches"][0]["node_id"] == "doc-1"
    assert fallback["read_document"]["markdown"] == "## 项目范围\n\n"


def test_oa_approval_detail_login_error_is_reported_as_tool_issue(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError("not authenticated", code="2")
    dws.oa_approval_records["proc-1"] = DwsError("not authenticated", code="2")
    dws.oa_approval_tasks["proc-1"] = DwsError("not authenticated", code="2")
    dws.openapi_oa_details["proc-1"] = DwsError("not authenticated", code="2")
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    detail = json.loads(oa_handler.approval_detail_texts[0])
    assert detail["tool_status"] == "dws_login_required"
    assert detail["tool_issue"] == "DWS 未登录或登录态失效，当前不是审批材料缺失。"
    assert detail["dws_detail"]["error_kind"] == "dws_login_required"
    assert "not authenticated" in detail["dws_detail"]["message"]
    assert dws.auth_login_starts == 1
    assert {
        "title": "CEO DWS auth login required",
        "message": "Started dws auth login. Please complete DingTalk login.",
        "url": None,
    } in notifications


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
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=True,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_handler.calls) == 1
    assert oa_handler.calls[0][3] is False
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "dry_run"
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="done") == 0

    live_runner = FakeOaApprovalHandler()
    live_worker = DingTalkAutoReplyWorker(
        store=worker.store,
        dws=dws,
        codex=codex,
        dry_run=False,
        now_provider=fixed_worker_now,
        oa_approval_handler=live_runner,
    )

    live_worker.run_once()

    assert len(live_runner.calls) == 1
    assert live_runner.calls[0][3] is False
    assert dws.oa_approval_actions == [
        (
            "proc-1",
            "task-1",
            "拒绝",
            "请补充预算来源和项目归属后重新提交。",
        )
    ]
    assert worker.store.has_seen("msg-1") is True
    assert worker.store.count_reply_attempts() == 1
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="done") == 1
    live_attempt = worker.store.get_reply_attempt(1)
    assert live_attempt is not None
    assert live_attempt.action == "oa_approval"
    assert live_attempt.send_status == "skipped"


def test_bare_dingtalk_approval_wrapper_is_not_skipped_before_oa_handler(
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
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.run_once()

    assert codex.calls == []
    assert len(oa_handler.calls) == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"


def test_group_mention_sends_signed_reply(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Alex Chen(明哥) @晓民 这个怎么处理？",
        quoted_content="这个ACL表看一下",
    )
    trigger.mentioned_user_ids = ["principal-user-1", "mentioned-user-1"]
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
            "@周俊杰 先按A方案走（by明哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [["sender-user-1", "mentioned-user-1"]]
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "sender-1",
            "@周俊杰 先按A方案走（by明哥分身）",
        )
    ]
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "当前待处理消息:" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Alex 的钉钉自动回复分身" not in prompt
    assert "会话: Friday" in prompt
    assert "@Alex Chen(明哥) @晓民 这个怎么处理？" in prompt
    assert "引用: 这个ACL表看一下" in prompt
    assert "前面上下文" in prompt


def test_group_reply_structures_explicit_reply_mentions(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 这几个融资支持待办看一下",
        sender_user_id=None,
    )
    trigger.sender_name = "Lily"
    trigger.sender_open_dingtalk_id = "open-lily"
    group = conversation()
    dws = FakeDws([group], {"cid-1": [trigger]})
    codex = FakeCodex([])
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_org_user_profile(
        user_id="user-et",
        name="ET",
        title="",
        open_dingtalk_id="open-et",
        manager_user_id="",
        manager_name="",
        department_ids=set(),
        department_names=set(),
        org_labels=[],
        has_subordinate=None,
    )
    worker.store.upsert_org_user_profile(
        user_id="user-roy",
        name="Roy Han",
        title="",
        open_dingtalk_id="open-roy",
        manager_user_id="",
        manager_name="",
        department_ids=set(),
        department_names=set(),
        org_labels=[],
        has_subordinate=None,
    )

    attempt_id = worker.store.record_reply_attempt(
        conversation_id=group.open_conversation_id,
        conversation_title=group.title,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="@ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。",
        send_status="processing",
    )

    worker._send_reply(
        conversation=group,
        trigger=trigger,
        new_messages=[trigger],
        reply_text="@ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。",
        reason="test",
        attempt_id=attempt_id,
    )

    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "open-lily",
            "@ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。（by明哥分身）",
        )
    ]
    assert final_sent(dws) == [
        (
            "cid-1",
            "@ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。（by明哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [["user-et", "user-roy"]]
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    send_result = json.loads(sent_reply.send_result_json)
    assert send_result["delivery"]["kind"] == "native_reply"
    assert send_result["delivery"]["ref_message_id"] == "msg-1"
    assert send_result["at_open_dingtalk_ids"] == ["open-et", "open-roy"]
    assert send_result["at_open_dingtalk_names"] == ["ET", "Roy Han"]


def test_group_reply_replaces_leading_name_with_structured_at(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 帮忙看一下")
    trigger.sender_name = "ET"
    trigger.sender_open_dingtalk_id = "open-et"
    group = conversation()
    dws = FakeDws([group], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="ET你要再往下收一层",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "open-et",
            "@ET 你要再往下收一层（by明哥分身）",
        )
    ]
    assert final_sent(dws) == [
        (
            "cid-1",
            "@ET 你要再往下收一层（by明哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [["sender-user-1"]]
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    send_result = json.loads(sent_reply.send_result_json)
    assert send_result["delivery"]["kind"] == "native_reply"
    assert send_result["delivery"]["ref_message_id"] == "msg-1"
    assert send_result["at_open_dingtalk_ids"] == ["open-et"]
    assert send_result["at_open_dingtalk_names"] == ["ET"]


def test_success_notification_keeps_full_reply_text(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reply_body = "我倾向于按这个方向收敛：" + "先看行业经验和交付闭环，" * 12
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text=reply_body)
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert len(notifications[0]["message"]) > 120
    assert notifications == [
            {
                "title": "CEO auto reply: Friday",
                "message": final_sent(dws)[0][1],
                "url": (
                    "http://127.0.0.1:8765/open-dingtalk"
                    "?conversation_id=cid-1&attempt_id=1"
                ),
            }
        ]


def test_success_notification_prepares_dingtalk_open_conversation_url(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert dws.client_cid_calls == []
    assert notifications[0] == {
        "title": "CEO auto reply: Friday",
        "message": final_sent(dws)[0][1],
        "url": (
            "http://127.0.0.1:8765/open-dingtalk"
            "?conversation_id=cid-1&attempt_id=1"
        ),
    }


def test_leak_check_feedback_regenerates_reply_before_blocking(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="参考 [1]，先按A方案推进",
                audit_summary="只需上下文判断，当前消息已足够确认。",
            ),
            AgentEnvelope.model_validate(
                {
                    "kind": "reply",
                    "user_response": {
                        "mode": "send_reply",
                        "text": "先按A方案推进",
                        "sensitivity_kind": "general",
                    },
                    "system_actions": [
                        {
                            "type": "send_dingtalk_reply",
                            "reply_text_ref": "user_response.text",
                        }
                    ],
                    "domain_payload": {},
                    "audit": {
                        "summary": "收到安全反馈后，改写为不带来源引用的回复。",
                        "documents": [],
                        "confidence": 0.8,
                    },
                }
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert len(codex.calls) == 2
    assert codex.calls[1][1] == "session-1"
    assert "发送安全检查拦截" in codex.calls[1][0]
    assert "不要引用来源" in codex.calls[1][0]
    assert '"kind":"reply"' in codex.calls[1][0]
    assert '"mode":"send_reply|ask_clarifying_question|handoff_to_human|no_reply"' in codex.calls[1][0]
    assert worker.store.count_errors() == 0
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"
    assert attempt.send_error == ""
    assert "参考 [1]" not in attempt.final_reply_text
    assert "先按A方案推进" in attempt.final_reply_text


def test_live_send_regenerates_once_when_delivery_text_leaks(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="先按A方案推进",
                audit_summary="当前消息已足够确认。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="改写后继续推进",
                audit_summary="发送安全检查反馈后改写。",
            ),
        ]
    )
    feedback_calls = []

    def fake_append_feedback_links(**kwargs):
        feedback_calls.append(kwargs)
        if len(feedback_calls) == 1:
            return FeedbackReplyText(
                feedback_token="token-1",
                text=f"{kwargs['reply_text']}\n\n反馈：参考 [1]",
            )
        return FeedbackReplyText(
            feedback_token="token-2",
            text=f"{kwargs['reply_text']}\n\n反馈：OK",
        )

    monkeypatch.setattr("app.worker.append_feedback_links", fake_append_feedback_links)
    monkeypatch.setattr(
        "app.worker.feedback_spike_vercel_base_url",
        lambda: "https://feedback.example.com",
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 2
    assert "发送安全检查拦截" in codex.calls[1][0]
    assert len(feedback_calls) == 2
    assert final_sent(dws) == [
        ("cid-1", "@周俊杰 改写后继续推进（by明哥分身）\n\n反馈：OK")
    ]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""
    assert "参考 [1]" not in attempt.final_reply_text


def test_dingtalk_material_links_are_passed_to_codex_without_worker_reading(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    minutes_id = "7632756964333134343836383736303334325f3435313431363430365f35"
    trigger = message(
        "\n".join(
            [
                f"文档: {doc_url}",
                f"听记: dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId={minutes_id}&from=8",
                "@Alex Chen(明哥) 判断这个材料是否能推进",
            ]
        )
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先读材料再判断")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.minutes_info_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_doc_url in prompt
    assert minutes_id in prompt
    assert "dws doc read --node" in prompt
    assert "dws minutes get info --id" in prompt


def test_dingtalk_doc_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    trigger = message(f"{doc_url} @Alex Chen(明哥) 看下根因和解法")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="按协作方式拆分")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert final_sent(dws) == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_doc_url in prompt
    assert "dws doc read --node" in prompt
    assert "根因是协作方式不对" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"


def test_single_chat_doc_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private"
    trigger = message(
        f"{doc_url}\n帮我看下这个方案",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读材料再判断方案。",
                audit_summary="私聊材料引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(codex.calls) == 2
    first_prompt = codex.calls[0][0]
    retry_prompt = codex.calls[1][0]
    assert "待读取材料（由 agent 判断是否读取）:" in first_prompt
    assert canonical_doc_url in first_prompt
    assert "已获取的钉钉材料:" not in first_prompt
    assert "私聊" in retry_prompt
    assert "材料引用" in retry_prompt
    assert "DWS" in retry_prompt
    assert "已获取" not in retry_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "dry_run"


def test_single_chat_file_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "帮我看下这个文件",
        quoted_content="[文件] 02_下一步推进建议.md",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读取文件再判断。",
                audit_summary="私聊文件材料引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    assert len(codex.calls) == 2
    first_prompt = codex.calls[0][0]
    retry_prompt = codex.calls[1][0]
    assert "待读取材料（由 agent 判断是否读取）:" in first_prompt
    assert "类型: dingtalk_file" in first_prompt
    assert "02_下一步推进建议.md" in first_prompt
    assert "私聊" in retry_prompt
    assert "材料引用" in retry_prompt
    assert "DWS" in retry_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "dry_run"


def test_single_chat_mixed_minutes_and_doc_material_retries_for_doc(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private"
    trigger = message(
        "听记和方案一起看：\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)\n"
        f"{doc_url}",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为听记单独场景。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会结合方案材料判断。",
                audit_summary="文档材料触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.minutes_info_calls == []
    assert dws.doc_info_calls == []
    assert len(codex.calls) == 2
    first_prompt = codex.calls[0][0]
    assert "类型: dingtalk_minutes" in first_prompt
    assert "类型: dingtalk_doc" in first_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"


def test_dingtalk_doc_permission_setup_is_irrelevant_to_worker_material_references(
    tmp_path: Path, monkeypatch
):
    blocked_url = "https://alidocs.dingtalk.com/i/nodes/blocked123"
    readable_url = "https://alidocs.dingtalk.com/i/nodes/readable456?utm_source=im"
    canonical_blocked_url = "https://alidocs.dingtalk.com/i/nodes/blocked123"
    canonical_readable_url = "https://alidocs.dingtalk.com/i/nodes/readable456"
    trigger = message(
        "\n".join(
            [
                f"第一份材料：{blocked_url}",
                f"第二份材料：{readable_url}",
                "@Alex Chen(明哥) 按第二份材料判断主叙事",
            ]
        )
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[canonical_blocked_url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    dws.docs[canonical_readable_url] = {
        "title": "OpenAI 合作建议补充版",
        "markdown": "核心结论：Stardust 应主打 Expert Signal Flywheel。",
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="主打 Expert Signal Flywheel",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_blocked_url in prompt
    assert canonical_readable_url in prompt
    assert "钉钉材料权限不足" not in prompt
    assert "OpenAI 合作建议补充版" not in prompt
    assert "Expert Signal Flywheel" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"
    assert attempt.action == "send_reply"


def test_dingtalk_aitable_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    aitable_url = "https://alidocs.dingtalk.com/i/nodes/base123?utm_source=im"
    canonical_url = "https://alidocs.dingtalk.com/i/nodes/base123"
    trigger = message(f"{aitable_url} @Alex Chen(明哥) 看下进展")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="优先验证关系排序")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.get_aitable_base_calls == []
    assert dws.get_aitable_tables_calls == []
    assert dws.query_aitable_record_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_url in prompt
    assert "dws doc read --node" in prompt
    assert "AI表格: 算法迭代看板" not in prompt
    assert "迭代名称: 关系排序优化" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "dry_run"


def test_docs_dingtalk_aitable_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    aitable_url = "https://docs.dingtalk.com/i/nodes/base-private?utm_source=im"
    canonical_url = "https://docs.dingtalk.com/i/nodes/base-private"
    trigger = message(
        f"{aitable_url}\n帮我看下这个表格",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读表格材料再判断。",
                audit_summary="私聊 AI 表格引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.get_aitable_base_calls == []
    assert len(codex.calls) == 2
    first_prompt = codex.calls[0][0]
    retry_prompt = codex.calls[1][0]
    assert "待读取材料（由 agent 判断是否读取）:" in first_prompt
    assert canonical_url in first_prompt
    assert "私聊" in retry_prompt
    assert "材料引用" in retry_prompt
    assert "DWS" in retry_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "dry_run"


def test_dingtalk_doc_link_in_context_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context"
    context_doc = message(
        f"[文档] 方案: {doc_url}",
        message_id="doc-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        message_id="msg-2",
        quoted_content=f"[文档] 方案: {doc_url}",
    )
    dws = FakeDws([conversation()], {"cid-1": [context_doc, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先收敛需求")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_doc_url in prompt
    assert "下一步建议：先做客户需求收敛" not in prompt


def test_referenced_file_message_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        message_id="msg-2",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    trigger.quoted_message_id = "file-msg-1"
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert "02_下一步推进建议.md" in prompt
    assert "普通文件" in prompt
    assert "建议正文：先明确客户边界" not in prompt


def test_referenced_file_context_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        message_id="msg-2",
    )
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert "类型: dingtalk_file" in prompt
    assert "02_下一步推进建议.md" in prompt
    assert "来源消息: file-msg-1" in prompt


def test_referenced_file_reference_does_not_download_or_expose_credentials(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在只能看到文件名，麻烦贴一下正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    prompt = codex.calls[0][0]
    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    assert "02_下一步推进建议.md" in prompt
    assert "文件正文：这里是可审阅内容。" not in prompt
    assert "authorizationUrl" not in prompt


def test_minutes_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    trigger = message(
        "[https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true]"
        "(https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true)\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这几个事项我看到了，先按材料方向推进。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.minutes_info_calls == []
    assert dws.minutes_summary_calls == []
    assert dws.minutes_todo_calls == []
    assert dws.minutes_transcription_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert minutes_id in prompt
    assert "dws minutes get info --id" in prompt
    assert "AI 听记材料:" not in prompt
    assert "韩露周三前完成自动驾驶能力图谱初版大纲" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert dws.reply_messages == []
    assert dws.doc_comments == [
        (
            "https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
            f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true",
            "这几个事项我看到了，先按材料方向推进。（by明哥分身）",
        )
    ]


def test_single_chat_minutes_no_reply_does_not_trigger_material_retry(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    trigger = message(
        "这是一条听记链接\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="单独听记链接按上下文判断无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="这次不应该被调用。",
                audit_summary="听记不应触发普通材料重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert dws.minutes_info_calls == []
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert minutes_id in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"


def test_minutes_comment_failure_falls_back_to_original_message_reply(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    target_url = (
        "https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true"
    )
    trigger = message(
        f"[{target_url}]({target_url})\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.doc_comment_error = RuntimeError("AI Minutes comments unsupported")
    dws.minutes_infos[minutes_id] = {
        "result": {
            "taskUuid": minutes_id,
            "title": "测试开发三面",
            "url": f"https://shanji.dingtalk.com/app/transcribes/{minutes_id}",
        }
    }
    dws.minutes_summaries[minutes_id] = {"result": {"fullSummary": "候选人风险偏高。"}}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不建议直接推进，建议补充作业后再判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    signed_reply = "不建议直接推进，建议补充作业后再判断。（by明哥分身）"
    assert dws.doc_comments == [(target_url, signed_reply)]
    assert dws.reply_messages == [("cid-1", "msg-1", "sender-1", signed_reply)]
    assert final_sent_at_users(dws) == [["sender-user-1"]]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert '"fallback": "chat_reply"' in sent_reply.send_result_json
    assert "AI Minutes comments unsupported" in sent_reply.send_result_json
    errors = worker.store.list_errors()
    assert len(errors) == 1
    assert errors[0].kind == "minutes_comment"
    assert "AI Minutes comments unsupported" in errors[0].detail


def test_media_id_image_is_downloaded_and_passed_to_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.resource_download_urls[
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ] = {"downloadUrl": "https://signed.example/message-image.png"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="image reviewed",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    monkeypatch.setattr(
        DingTalkAutoReplyWorker,
        "_download_resource_bytes",
        staticmethod(lambda url, headers: b"\x89PNG\r\n\x1a\nimage-bytes"),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.resource_download_url_calls == [
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ]
    image_paths = codex.calls[0][2]
    assert len(image_paths) == 1
    assert image_paths[0].suffix == ".png"
    assert codex.image_bytes_calls == [[b"\x89PNG\r\n\x1a\nimage-bytes"]]
    assert image_paths[0].exists() is False
    assert not (tmp_path / "image-attachments").exists()


def test_media_id_image_uses_dws_local_download_path(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws_local_path = tmp_path / "dws-downloaded-image.png"
    dws_local_path.write_bytes(b"\x89PNG\r\n\x1a\nlocal-image")
    dws.resource_download_urls[
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ] = {
        "localPath": str(dws_local_path),
        "response": {
            "content": {
                "result": {"downloadUrl": "https://signed.example/message-image.png"}
            }
        },
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="image reviewed",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    image_paths = codex.calls[0][2]
    assert len(image_paths) == 1
    assert codex.image_bytes_calls == [[b"\x89PNG\r\n\x1a\nlocal-image"]]
    assert image_paths[0].exists() is False
    assert dws_local_path.exists() is False


def test_media_id_image_reads_nested_dws_download_url_response(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.resource_download_urls[
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ] = {
        "response": {
            "content": {
                "result": {"downloadUrl": "https://signed.example/message-image.png"}
            }
        }
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="image reviewed",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    monkeypatch.setattr(
        DingTalkAutoReplyWorker,
        "_download_resource_bytes",
        staticmethod(lambda url, headers: b"\x89PNG\r\n\x1a\nnested-image"),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    image_paths = codex.calls[0][2]
    assert len(image_paths) == 1
    assert codex.image_bytes_calls == [[b"\x89PNG\r\n\x1a\nnested-image"]]
    assert image_paths[0].exists() is False


def test_robot_download_code_image_is_downloaded_and_passed_to_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图",
        message_id="msg-image-1",
    )
    trigger.raw_payload = {
        "msgtype": "picture",
        "content": {"downloadCode": "download-code-1"},
    }
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.robot_message_file_downloads["download-code-1"] = {
        "downloadUrl": "https://signed.example/message-image.jpeg"
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="image reviewed",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    monkeypatch.setattr(
        DingTalkAutoReplyWorker,
        "_download_resource_bytes",
        staticmethod(lambda url, headers: b"\xff\xd8\xffimage-bytes"),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.robot_message_file_download_calls == ["download-code-1"]
    image_paths = codex.calls[0][2]
    assert len(image_paths) == 1
    assert image_paths[0].suffix == ".jpeg"
    assert codex.image_bytes_calls == [[b"\xff\xd8\xffimage-bytes"]]
    assert image_paths[0].exists() is False


def test_image_download_failure_is_passed_to_codex_prompt(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.resource_download_urls[
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ] = DwsError("resource download unavailable")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我这边图片读取失败，你发一个可查看版本我再看。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.resource_download_url_calls == [
        ("cid-1", "msg-image-1", "@img-token-1", "mediaId")
    ]
    assert len(codex.calls) == 1
    prompt, _session_id, image_paths = codex.calls[0]
    assert image_paths == []
    assert "图片读取状态:" in prompt
    assert "msg-image-1" in prompt
    assert "resource download unavailable" in prompt
    assert "如果当前问题依赖图片内容，不能臆测图片细节" in prompt
    attempts = worker.store.list_reply_attempts()
    assert len(attempts) == 1
    assert attempts[0].action == CodexAction.ASK_CLARIFYING_QUESTION.value
    assert attempts[0].send_status == "sent"
    errors = worker.store.list_errors()
    image_error = next(error for error in errors if error.kind == "image_download")
    assert "resource download unavailable" in image_error.detail


def test_dingtalk_doc_read_failure_setup_does_not_block_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/missing @Alex Chen(明哥) 看下"
    )
    canonical_url = "https://alidocs.dingtalk.com/i/nodes/missing"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[canonical_url] = {
        "contentType": "ALIDOC",
        "extension": "adoc",
        "name": "缺失文档",
        "nodeId": "missing",
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我先读材料")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert canonical_url in prompt
    assert final_sent(dws) == [("cid-1", "@周俊杰 我先读材料（by明哥分身）")]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""


def test_minutes_permission_setup_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    minutes_id = "7632756964333134343836383736303334325f3435313431363430365f35"
    trigger = message(
        "这些初筛的数据，尤其是没有通过的，我就不给你开放读取了。\n"
        f"[听记](dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_infos[minutes_id] = DwsError(
        "B_PERMISSION_NoPermission",
        code="B_PERMISSION_NoPermission",
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.minutes_info_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert minutes_id in prompt
    assert "B_PERMISSION_NoPermission" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert worker.store.list_errors() == []


def test_alidocs_permission_setup_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    url = "https://alidocs.dingtalk.com/i/nodes/XPwkYGxZV3BqnwQ0I3dbwZDlWAgozOKL"
    trigger = message(f"@Alex Chen(明哥) 看下这个材料包：{url}")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    assert "待读取材料（由 agent 判断是否读取）:" in prompt
    assert url in prompt
    assert "forbidden.accessDenied" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert worker.store.list_errors() == []


def test_codex_stop_with_error_sends_macos_notification(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
        "app.worker.send_macos_notification",
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
        "url": "http://127.0.0.1:8765/open-dingtalk?conversation_id=cid-1",
    }


def test_codex_login_required_stop_with_error_is_blocked(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = (
        "Failed to refresh token: 400 Bad Request: "
        "Your session has ended. Please log in again."
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason=reason,
            macos_notify=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "blocked"
    assert attempt.send_error.startswith("codex_login_required:")
    assert notifications[0] == {
        "title": "CEO agent blocked: Friday",
        "message": f"codex_login_required: {reason}"[:120],
        "url": "http://127.0.0.1:8765/open-dingtalk?conversation_id=cid-1",
    }


def test_codex_stop_with_error_keeps_queued_task_retryable(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec timed out after 300 seconds",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
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


def test_stale_processing_task_with_terminal_attempt_is_requeued_not_completed(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    trigger = message("[日程] 晚饭", message_id="msg-calendar", message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重跑")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Melody",
        single_chat=True,
        trigger_message_id="msg-calendar",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Melody",
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    claimed = worker.store.claim_reply_tasks(limit=1)[0]
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Melody",
        trigger_message_id="msg-calendar",
        trigger_sender="Melody",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="calendar",
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    monkeypatch.setattr(
        worker.store,
        "reset_stale_processing_reply_tasks",
        lambda _max_age_seconds: 0,
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_tasks(status="processing") == 1
    assert worker.store.count_errors() == 0
    assert codex.calls == []


def test_critical_info_unavailable_stop_with_error_fails_queued_task(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 帮忙看一下这个审批材料")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = (
        "critical_info_unavailable: dws oa approval detail failed and "
        "required approval material is unavailable"
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason=reason,
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="done") == 0
    task = worker.store.list_reply_tasks(statuses=("failed",), limit=1)[0]
    assert task.error == reason
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "stop_with_error"
    assert attempt.send_status == "failed"
    assert attempt.send_error == reason
    assert final_sent(dws) == []
    assert [
        notification["title"]
        for notification in notifications
        if "failed" in notification["title"]
    ] == ["CEO task failed: Friday"]


def test_queued_stop_with_error_retry_does_not_create_duplicate_attempt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec timed out after 300 seconds",
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **_: None,
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_attempts() == 1

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_attempts() == 1
    assert len(codex.calls) == 1


def test_queued_failed_non_send_attempt_does_not_create_duplicate_attempt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="handoff_to_human",
        sensitivity_kind="general",
        send_status="failed",
        codex_reason="handoff delivery failed",
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_attempts() == 1
    assert codex.calls == []


def test_native_reply_body_strips_legacy_quote_and_at_placeholders():
    sent_text = DingTalkAutoReplyWorker._native_reply_body(
        "> 周俊杰: 如果是私有化的POC都是走产研评估流程的，如果...\n\n"
        "<@sender-user-1> 流程方向没问题（by明哥分身）"
    )

    assert sent_text == "流程方向没问题（by明哥分身）"


def test_resume_prompt_only_includes_turn_message_without_repeating_thread_prompt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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

    prompt, session_id, _image_paths = codex.calls[0]
    assert session_id == "session-1"
    assert "当前待处理消息" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Alex 的钉钉自动回复分身" not in prompt
    assert "回答任何问题前，先检索本地 workspace" not in prompt
    assert "graphify query" not in prompt
    assert "@Alex Chen(明哥) 这个怎么处理？" in prompt


def test_stale_codex_resume_retries_same_thread_before_opening_new_thread(
    tmp_path: Path, monkeypatch
):
    class SequencedCodex:
        def __init__(self):
            self.calls: list[tuple[str, str | None, list[Path]]] = []
            self.last_session_id: str | None = None
            self.last_audit_tool_events: list[dict[str, str]] = []
            self.last_transcript_start_line = 0
            self.last_transcript_end_line = 0

        def decide(
            self,
            prompt: str,
            session_id: str | None,
            image_paths: list[Path] | None = None,
        ) -> CodexDecision:
            self.calls.append((prompt, session_id, image_paths or []))
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

    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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

    assert [session_id for _, session_id, _ in codex.calls] == [
        "session-1",
        "session-1",
    ]
    assert codex.calls[1][0] == codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[0][0]
    assert "你是 Alex 的钉钉自动回复分身" not in codex.calls[0][0]
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
            "/Users/principal/.codex/sessions/2026/05/18/rollout-session-1.jsonl"
        ),
    ],
)
def test_stale_codex_resume_clears_session_and_retries_with_new_user_message(
    tmp_path: Path, monkeypatch, stale_reason: str
):
    class SequencedCodex:
        def __init__(self):
            self.calls: list[tuple[str, str | None, list[Path]]] = []
            self.last_session_id: str | None = None
            self.last_audit_tool_events: list[dict[str, str]] = []
            self.last_transcript_start_line = 0
            self.last_transcript_end_line = 0

        def decide(
            self,
            prompt: str,
            session_id: str | None,
            image_paths: list[Path] | None = None,
        ) -> CodexDecision:
            self.calls.append((prompt, session_id, image_paths or []))
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

    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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

    assert [session_id for _, session_id, _ in codex.calls] == [
        "session-1",
        "session-1",
        None,
    ]
    assert codex.calls[1][0] == codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[0][0]
    assert "CEO Agent Prompt" not in codex.calls[2][0]
    assert "当前待处理消息:" in codex.calls[2][0]
    assert worker.store.get_codex_session_id("cid-1") == "session-2"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert "thread/resume failed" not in attempt.codex_reason
    assert worker.store.count_errors() == 0


def test_sent_reply_records_recall_key_from_send_result(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
        final_reply_text="> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）",
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 1


def test_failed_send_retries_existing_final_reply_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
        "> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）"
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
    assert final_sent(dws) == [("cid-1", "先按A方案走（by明哥分身）")]
    assert final_sent_at_users(dws) == [["sender-user-1"]]
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "sender-1",
            "先按A方案走（by明哥分身）",
        )
    ]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert worker.store.get_sent_reply("cid-1", "msg-1") is not None
    assert worker.store.has_seen("msg-1") is True


def test_sent_reply_prevents_retry_when_latest_attempt_failed(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    final_reply = (
        "> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）"
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
    assert final_sent(dws) == [("cid-1", "先按A方案走（by明哥分身）")]
    assert worker.store.get_reply_attempt(attempt_id).send_status == "sent"


def test_rerun_message_cleans_legacy_group_reply_wrappers(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
            "<@sender-user-1> 先按A方案走（by明哥分身）"
        ),
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert codex.calls == []
    assert final_sent(dws) == [("cid-1", "先按A方案走（by明哥分身）")]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.final_reply_text == "先按A方案走（by明哥分身）"
    assert attempt.send_status == "sent"


def test_rerun_message_can_force_new_codex_decision(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
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
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(old_attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.draft_reply_text == "改走B方案"
    assert attempt.final_reply_text == "@周俊杰 改走B方案（by明哥分身）"
    assert final_sent(dws) == [
        (
            "cid-1",
            "@周俊杰 改走B方案（by明哥分身）",
        )
    ]


def test_rerun_message_looks_up_trigger_by_id_when_recent_context_expired(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_messages={"cid-1": []},
    )
    dws.mentioned_messages["cid-1"] = [trigger]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    processed = worker.rerun_message(
        conversation(),
        "msg-1",
        force_new_decision=True,
    )

    assert processed == "msg-1"
    assert dws.recent_message_reads[0] == "cid-1"
    assert dws.recent_message_reads.count("cid-1") == 2
    assert dws.unread_message_reads == ["cid-1"]
    assert dws.messages_by_id_reads == [["msg-1"]]
    assert len(codex.calls) == 1
    assert final_sent(dws) == [("cid-1", "@周俊杰 改走B方案（by明哥分身）")]


def test_rerun_message_does_not_resend_when_trigger_already_has_sent_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
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
        send_status="sent",
    )
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )

    worker.rerun_message(conversation(), "msg-1")

    assert len(codex.calls) == 0
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert worker.store.count_sent_replies() == 1
    assert worker.store.has_seen("msg-1") is True


def test_force_new_rerun_can_resend_when_trigger_already_has_sent_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
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
        send_status="sent",
    )
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert len(codex.calls) == 1
    assert final_sent(dws) == [("cid-1", "@周俊杰 改走B方案（by明哥分身）")]
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.draft_reply_text == "改走B方案"
    assert attempt.final_reply_text == "@周俊杰 改走B方案（by明哥分身）"
    assert attempt.send_status == "sent"
    assert attempt.send_error == ""
    assert worker.store.count_sent_replies() == 2


def test_force_new_rerun_starts_fresh_codex_session(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", False, "old-session")

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert codex.calls[0][1] is None
    assert "当前待处理消息:" in codex.calls[0][0]
    assert "你是 Alex 的钉钉自动回复分身" not in codex.calls[0][0]


def test_rerun_message_uses_explicit_oa_url_when_trigger_has_no_link(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "刘瑞安提交的录用申请",
            "form_component_values": [
                {"name": "试用期工作内容和转正要求", "value": "完成 PM 关键项目交付"}
            ],
            "tasks": [
                {"taskid": "task-1", "task_status": "RUNNING", "userid": "principal-user-1"}
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    oa_handler = FakeOaApprovalHandler()
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        oa_approval_handler=oa_handler,
    )

    worker.rerun_message(
        conversation(single_chat=True),
        "msg-1",
        force_new_decision=True,
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )

    assert len(oa_handler.calls) == 1
    assert oa_handler.calls[0][2] == (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    assert "\"current_user_id\": \"principal-user-1\"" in oa_handler.approval_detail_texts[0]
    assert "\"process_instance_id\": \"proc-1\"" in oa_handler.approval_detail_texts[0]
    assert "完成 PM 关键项目交付" in oa_handler.approval_detail_texts[0]


def test_reply_attempt_records_codex_audit_fields(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人是否推进？")]},
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
                "command": "rg -n 岗位 /Users/principal/Documents/memory/面试",
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


def test_prompt_includes_dynamic_similar_corpus_examples_without_static_style_profile(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个项目排期怎么处理？")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    style_records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="项目排期要不要改",
            principal_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
            message_id="style-1",
            conversation_id="cid-style-1",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="HR",
            timestamp="2026-05-13",
            context="候选人怎么样",
            principal_reply="先看岗位匹配，再看负责范围和是否真正承担过结果。",
            message_id="style-2",
            conversation_id="cid-style-2",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="技术部",
            timestamp="2026-05-13",
            context="项目排期风险",
            principal_reply="先把风险拆成产品、算法和交付三类，每类只留一个负责人和一个截止时间。",
            message_id="style-3",
            conversation_id="cid-style-3",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="项目群",
            timestamp="2026-05-13",
            context="项目排期延期怎么拆",
            principal_reply="先判断延期是不是影响客户承诺，再决定砍范围、加资源还是换里程碑。",
            message_id="style-4",
            conversation_id="cid-style-4",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="研发群",
            timestamp="2026-05-13",
            context="项目排期和负责人不清楚",
            principal_reply="先把负责人写到任务上，再把验收口径写清楚，否则排期没有意义。",
            message_id="style-5",
            conversation_id="cid-style-5",
            speaker_name="明哥",
            metadata_json="{}",
        ),
    ]
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        style_profile="# Alex Style Profile\n- 先结论，再解释原因。",
        style_records=style_records,
    )

    worker.run_once()

    prompt = codex.calls[0][0]
    assert "Alex 语气规则:" not in prompt
    assert "- 先结论，再解释原因。" not in prompt
    assert "相似历史回复风格例子" in prompt
    assert "只学习语气、判断顺序和句式结构" in prompt
    assert "不要复用例子里的事实、人名、项目名、客户名、数字或结论" in prompt
    assert "先定优先级，再确认谁负责、什么时候交付、怎么验收。" in prompt
    assert prompt.count("- 例") == 4
    assert "先判断延期是不是影响客户承诺" in prompt
    assert "先把负责人写到任务上" in prompt
    assert "先看岗位匹配" not in prompt
    assert "cid-style-1" not in prompt
    assert "Friday" in prompt


def test_prompt_includes_similar_human_feedback_examples(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {
            "cid-1": [
                message(
                    "明哥，这个本地工具我跑通过，你先安装试试。",
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
        codex_reason="要求 Alex 安装本地工具，应交给本人。",
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
    assert "优先学习 明哥 对错误回复的修正方向" in prompt
    assert "不要直接交给本人" in prompt
    assert "你把代码提交一下" in prompt
    assert "msg-old" not in prompt
    assert "cid-old" not in prompt


def test_review_feedback_examples_require_relevant_keywords(tmp_path: Path, monkeypatch):
    dws = FakeDws([], {})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    okr_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-okr",
        conversation_title="公司大群",
        trigger_message_id="msg-okr",
        trigger_sender="Mina",
        trigger_text=(
            "@所有人 请填写 Q2 OKR 进展和 OKR 月度复盘，"
            "HRBP 会安排部门负责人与管理层 1:1。"
        ),
        action="send_reply",
        sensitivity_kind="internal_personnel",
        codex_reason="错误地对 OKR 复盘广播补充管理口径。",
    )
    worker.store.record_reply_feedback(
        okr_attempt_id,
        feedback=(
            "群聊@所有人的 OKR 复盘或会议安排广播，如果没有点名要求本人"
            "处理、确认或决策，应该 no_reply，不要主动插嘴。"
        ),
        corrected_reply_text="这个不应该回复",
    )

    unrelated_attempts = [
        (
            "cid-tool",
            "工具安装",
            "你先安装试试这个本地工具",
            "本地工具安装请求不要直接交给本人。",
        ),
        (
            "cid-hiring",
            "候选人评估",
            "这个候选人简历是否推进",
            "候选人问题先看岗位匹配和简历材料。",
        ),
        (
            "cid-doc",
            "文档审核",
            "帮忙看一下客户合同条款",
            "文档审核必须先读正文材料。",
        ),
    ]
    for conversation_id, title, trigger_text, feedback in unrelated_attempts:
        attempt_id = worker.store.record_reply_attempt(
            conversation_id=conversation_id,
            conversation_title=title,
            trigger_message_id=f"msg-{conversation_id}",
            trigger_sender="同事",
            trigger_text=trigger_text,
            action="send_reply",
            sensitivity_kind="general",
            codex_reason=feedback,
        )
        worker.store.record_reply_feedback(
            attempt_id,
            feedback=feedback,
            corrected_reply_text="按对应场景重新判断",
        )

    examples = worker._retrieve_review_feedback_examples(
        "@所有人 请更新第二季度 OKR 进展和月度复盘，本周安排管理层 1:1。",
        worker.store.list_reviewed_reply_attempts(limit=50),
        limit=3,
    )

    assert [example.id for example in examples] == [okr_attempt_id]


def test_review_feedback_examples_skip_generic_old_corrections(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    old_corrections = [
        (
            "官网迭代群",
            "@All 新的官网更新一共16页，请大家打开每一个html文档给文字comment",
            "官网是 marketing 重要内容，CEO 直接相关；这类 @All 官网审核消息需要处理。",
            "Claire，我这边已按文字内容做了一轮完整审核。",
        ),
        (
            "Helix discussion",
            "@Derek Zen 这里哈",
            "重要客户合同/终止谈判消息虽然超过24小时仍需回复，不能只因过期窗口跳过。",
            "Claire，我看了。这个方向可以发，但建议再收紧三点。",
        ),
        (
            "HR&管理层合作及季度会",
            "@Derek Zen @曹宇航 这版稿子不要再按场景列表往下堆",
            "用户要求补回 ET 连续多条 @Derek 未完整回复的问题。",
            "@张毅倜 @曹宇航 对，这个补充是关键。",
        ),
    ]
    for index, (title, trigger_text, feedback, corrected) in enumerate(
        old_corrections,
        start=1,
    ):
        attempt_id = worker.store.record_reply_attempt(
            conversation_id=f"cid-old-{index}",
            conversation_title=title,
            trigger_message_id=f"msg-old-{index}",
            trigger_sender="同事",
            trigger_text=trigger_text,
            action="send_reply",
            sensitivity_kind="general",
            codex_reason=feedback,
        )
        worker.store.record_reply_feedback(
            attempt_id,
            feedback=feedback,
            corrected_reply_text=corrected,
        )

    examples = worker._retrieve_review_feedback_examples(
        (
            "@Derek Zen Alex，我跟晓民哥讨论了二次查询方案，"
            "memory_recall 返回可用上下文，二次 memory_get 会污染上下文。"
        ),
        worker.store.list_reviewed_reply_attempts(limit=50),
        limit=3,
    )

    assert examples == []


def test_group_name_reference_without_direct_at_does_not_queue(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@张晓民(Xiaomin张晓民) 这个和明哥预期一致")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_algorithm_owner_multi_mention_is_framed_as_principal_responsibility(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                message(
                    "@ET(张毅倜(ET)) @Alex Chen(明哥) "
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

    assert final_sent(dws) == [("cid-1", "@周俊杰 可以，算法这边应该参与（by明哥分身）")]
    prompt = codex.calls[0][0]
    assert "aijam是否可以把算法大神们纳入进来？" in prompt
    assert "当前待处理消息:" in prompt


def test_group_direct_mention_found_in_recent_context_is_queued(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
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
    dws.mentioned_messages = {"cid-1": [old_direct_mention]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert final_sent(dws) == [("cid-1", "@周俊杰 我看一下（by明哥分身）")]


def test_group_seen_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
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
        "明哥，晓民的转正时间快到了。",
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
        manager_name="Alex Chen",
        manager_user_id="principal-user-1",
        department_ids={"dept-hr", "dept-recruiting"},
        department_names={"人力资源部", "招聘组"},
        org_labels=["职务: HR负责人", "岗位: 管理层"],
        has_subordinate=True,
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_org_user_profile(
        user_id="principal-user-1",
        name="Alex Chen",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids={"dept-exec"},
    )
    trigger = message(
        "明哥，晓民的转正时间快到了。",
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
    assert "发信人组织信息(JSON):" in prompt
    assert '"name": "Mina 邹"' in prompt
    assert '"user_id": "sender-user-1"' in prompt
    assert '"title": "首席人力资源专家兼HRVP"' in prompt
    assert '"org_labels": [' in prompt
    assert '"职务: HR负责人"' in prompt
    assert '"岗位: 管理层"' in prompt
    assert '"manager": {' in prompt
    assert '"name": "Alex Chen"' in prompt
    assert '"departments": [' in prompt
    assert '"name": "人力资源部"' in prompt
    assert '"has_subordinate": true' in prompt


def test_group_stale_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    stale_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
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


def test_okr_review_request_is_enqueued_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {"fetch_user_okr": lambda self, user_id, period_label: {"objectives": []}},
    )()

    worker.run_once()

    assert len(codex.calls) == 1
    request = worker.store.claim_okr_review_requests(1)[0]
    assert request.trigger_text == "帮我审核 OKR"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "okr_review"
    assert "已受理" in attempt.final_reply_text


def test_okr_mentions_without_agent_queue_action_do_not_fetch_okr_source(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) Q3 OKR 季度会请大家准备，AI 打分只是材料同步。",
        single_chat=False,
    )
    dws = FakeDws([conversation(single_chat=False)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="通知同步"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {
            "fetch_user_okr": lambda self, user_id, period_label: (_ for _ in ()).throw(
                AssertionError("OKR source should not be called")
            )
        },
    )()

    worker.run_once()

    assert len(codex.calls) == 1
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == CodexAction.NO_REPLY.value
    assert attempt.send_status == "skipped"
    assert final_sent(dws) == []


def test_okr_review_missing_live_source_fails_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=1)

    worker.run_once()

    assert len(codex.calls) == 1
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "okr_review"
    assert attempt.send_status == "failed"
    assert "OKR live source is not configured" in attempt.send_error
    assert worker.store.count_reply_tasks(status="failed") == 1
    errors = worker.store.list_errors(limit=10)
    assert [error.kind for error in errors] == ["reply_task", "okr_review_source"]
    assert "OKR live source is not configured" in errors[0].detail
    assert "OKR live source is not configured" in errors[1].detail
    assert final_sent(dws) == []


def test_okr_review_live_source_error_fails_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=1)
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {
            "fetch_user_okr": lambda self, user_id, period_label: (_ for _ in ()).throw(
                RuntimeError("okr unavailable")
            )
        },
    )()

    worker.run_once()

    assert len(codex.calls) == 1
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "okr_review"
    assert attempt.send_status == "failed"
    assert "okr unavailable" in attempt.send_error
    assert worker.store.count_reply_tasks(status="failed") == 1
    errors = worker.store.list_errors(limit=10)
    assert [error.kind for error in errors] == ["reply_task", "okr_review_source"]
    assert "okr unavailable" in errors[0].detail
    assert "okr unavailable" in errors[1].detail
    assert final_sent(dws) == []


def test_queued_okr_review_ack_delivery_failure_requeues_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [trigger]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {"fetch_user_okr": lambda self, user_id, period_label: {"objectives": []}},
    )()
    worker.store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert len(codex.calls) == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    retried = worker.store.claim_reply_tasks(limit=1)
    assert retried[0].attempts == 2
    assert "send failed" in retried[0].error
    request = worker.store.claim_okr_review_requests(1)[0]
    assert request.trigger_text == "帮我审核 OKR"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "okr_review"
    assert attempt.send_status == "failed"
    assert "send failed" in attempt.send_error


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
    sent_reply = principal_message(
        "对，paper不要等所有数据都齐了再启动。",
        message_id="msg-principal-reply",
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


def test_single_chat_recovery_processes_unseen_gap_before_later_seen_anchor(
    tmp_path: Path, monkeypatch
):
    handled = message("前面已经处理过", message_id="msg-seen-old", single_chat=True)
    handled.create_time = "2026-05-13 16:50:00"
    missed = message(
        "这条如果窗口开着也要处理",
        message_id="msg-missed-gap",
        single_chat=True,
    )
    missed.create_time = "2026-05-13 17:10:00"
    manual_context = principal_message(
        "后面我手动说了另一件事",
        message_id="msg-principal-after-gap",
        create_time="2026-05-13 17:20:00",
    )
    later_seen = message("后面这条已经处理", message_id="msg-seen-new", single_chat=True)
    later_seen.create_time = "2026-05-13 17:30:00"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                later_seen,
                manual_context,
                missed,
                handled,
            ]
        },
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我会处理这条。")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "韩露", True, None)
    worker.store.mark_seen("msg-seen-old", "cid-1")
    worker.store.mark_seen("msg-seen-new", "cid-1")

    worker.run_once()

    assert len(codex.calls) == 1
    prompt = codex.calls[0][0]
    new_messages_section = prompt.split("新消息:", 1)[1].split(CONTEXT_HEADER, 1)[0]
    assert "这条如果窗口开着也要处理" in new_messages_section
    assert "后面我手动说了另一件事" not in new_messages_section
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-missed-gap"


def test_single_chat_recovery_does_not_coalesce_across_current_user_context(
    tmp_path: Path, monkeypatch
):
    seen_anchor = message("已经处理过", message_id="msg-seen-anchor", single_chat=True)
    seen_anchor.create_time = "2026-05-13 16:50:00"
    first_missed = message("第一段要处理", message_id="msg-first-missed", single_chat=True)
    first_missed.create_time = "2026-05-13 17:10:00"
    current_user = principal_message(
        "中间我说了另一件事",
        message_id="msg-current-user-between",
        create_time="2026-05-13 17:20:00",
    )
    second_missed = message("第二段也要处理", message_id="msg-second-missed", single_chat=True)
    second_missed.create_time = "2026-05-13 17:30:00"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                second_missed,
                current_user,
                first_missed,
                seen_anchor,
            ]
        },
        unread_messages={"cid-1": []},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    worker.store.upsert_conversation("cid-1", "韩露", True, None)
    worker.store.mark_seen("msg-seen-anchor", "cid-1")

    assert worker.produce_once() == 2

    tasks = sorted(worker.store.list_reply_tasks(limit=10), key=lambda task: task.id)
    assert [task.trigger_message_id for task in tasks] == [
        "msg-first-missed",
        "msg-second-missed",
    ]


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


def test_initial_prompt_context_includes_previous_20_plus_unread_tail(
    tmp_path: Path, monkeypatch
):
    old_messages = [
        message(f"历史上下文 {index:02d}", message_id=f"old-{index:02d}")
        for index in range(25)
    ]
    for index, old_message in enumerate(old_messages):
        old_message.create_time = f"2026-05-13 17:{index:02d}:00"
    trigger = message("@Alex Chen(明哥) 这个需要你看一下", message_id="trigger-msg")
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
    assert "@Alex Chen(明哥) 这个需要你看一下" in new_messages_section
    assert "我已经处理好了" not in new_messages_section
    assert "@Alex Chen(明哥) 这个需要你看一下" in context_section
    assert "我已经处理好了" in context_section


def test_resumed_prompt_context_only_includes_messages_after_last_seen(
    tmp_path: Path, monkeypatch
):
    before_seen = message("旧上下文，不应重复", message_id="old-before")
    before_seen.create_time = "2026-05-13 17:00:00"
    last_seen = message("上次已经处理到这里", message_id="old-seen")
    last_seen.create_time = "2026-05-13 17:10:00"
    after_seen = message("上次回复后的补充信息", message_id="after-seen")
    after_seen.create_time = "2026-05-13 17:20:00"
    trigger = message("@Alex Chen(明哥) 结合上面的补充再看一下", message_id="trigger-msg")
    trigger.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [before_seen, last_seen, after_seen]},
        unread_messages={"cid-1": [trigger]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )
    worker.store.mark_seen("old-seen", "cid-1")

    worker.run_once()

    prompt, session_id, _image_paths = codex.calls[0]
    context_section = prompt.split(CONTEXT_HEADER, 1)[1]
    assert session_id == "session-1"
    assert "旧上下文，不应重复" not in context_section
    assert "上次已经处理到这里" not in context_section
    assert "上次回复后的补充信息" in context_section
    assert "结合上面的补充再看一下" in context_section


def test_no_reply_action_does_not_send(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    trigger = message("@Alex Chen(明哥) cc一下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
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


def test_handoff_adds_text_emotion_dings_self_and_records_reaction(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    trigger = message("@Alex Chen(明哥) 不要分身，真人看一下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.reply_messages == []
    assert dws.created_text_emotions == [("我去叫", "我去叫", "im_bg_5")]
    assert dws.message_text_emotions == [
        ("cid-1", "msg-1", "我去叫", "created-1", "我去叫", "created-bg")
    ]
    assert len(dws.dings) == 1
    assert "Friday" in dws.dings[0]
    assert "不要分身" in dws.dings[0]
    assert "previous split-person reply: none" in dws.dings[0]
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.final_reply_text == ""
    assert attempt.send_status == "reacted"
    assert attempt.send_error == "text_emotion: 我去叫"
    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is None


def test_new_principal_mention_is_processed(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "26年董事会筹备组", False, None)
    latest = message(
        "@Melody Xu（Melody） @Alex Chen（明哥）请明哥看一下2026年的战略主线这样写是否合适？[图片消息]",
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
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert codex.calls
    assert final_sent(dws) == [("cid-1", "@Melody 战略主线建议这样调整（by明哥分身）")]
    assert store.has_seen("msg-after-handoff") is True
    assert notifications == [
            {
                "title": "CEO auto reply: 26年董事会筹备组",
                "message": "@Melody 战略主线建议这样调整（by明哥分身）",
                "url": (
                    "http://127.0.0.1:8765/open-dingtalk"
                    "?conversation_id=cid-1&attempt_id=1"
                ),
            }
        ]


def test_group_unread_without_principal_mention_is_ignored(
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
        "app.worker.send_macos_notification",
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


def test_group_unread_without_principal_mention_reads_unread_tail_but_does_not_queue(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    latest = message(
        "无关同步",
        message_id="msg-unmentioned",
    )
    latest.create_time = "2026-05-13 18:10:00"
    group = conversation()
    group.title = "无关群"
    group.single_chat = False
    group.unread_point = 1
    dws = FakeDws(
        [group],
        {"cid-1": [latest]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    worker.run_once()

    assert dws.unread_message_reads[0] == "cid-1"
    assert store.list_errors() == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_recovery_due_group_unread_without_principal_mention_reads_unread_tail_but_does_not_queue(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    latest = message(
        "无关同步",
        message_id="msg-unmentioned",
    )
    latest.create_time = "2026-05-13 18:10:00"
    group = conversation()
    group.title = "无关群"
    group.single_chat = False
    group.unread_point = 1
    dws = FakeDws(
        [group],
        {"cid-1": [latest]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    worker.run_once()

    assert dws.unread_message_reads == ["cid-1"]
    assert store.list_errors() == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_dry_run_group_unread_without_principal_mention_is_ignored(
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
        "app.worker.send_macos_notification",
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
            "cid-1",
            "可以，先推进（by明哥分身）",
        )
    ]
    assert final_sent_at_users(dws) == [["sender-user-1"]]
    assert final_direct_user_ids(dws) == [None]
    assert final_direct_open_dingtalk_ids(dws) == [None]
    assert dws.reply_messages == [
        (
            "cid-1",
            "msg-1",
            "sender-1",
            "可以，先推进（by明哥分身）",
        )
    ]
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert attempt.direct_user_id == "sender-user-1"
    assert attempt.direct_open_dingtalk_id == "sender-1"
    assert attempt.final_reply_text == "可以，先推进（by明哥分身）"


def test_user_runtime_term_in_trigger_does_not_block_safe_reply(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("明哥，你是怎么解决codex上下文压缩失败的问题的？", single_chat=True)]},
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
    assert sent_text == "我会把长任务拆小，每一步都留清楚验收口径。（by明哥分身）"
    assert worker.store.get_reply_attempt(1).send_status == "sent"


def test_single_chat_current_user_message_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [principal_message("AI自动抓取，用于会议纪要整理")]},
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
            "cid-1": [message("@Alex Chen(明哥) 第一个问题", message_id="msg-1")],
            "cid-2": [message("@Alex Chen(明哥) 第二个问题", message_id="msg-2")],
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


def test_single_chat_same_display_name_without_current_user_id_still_calls_codex(
    tmp_path: Path, monkeypatch
):
    same_name_message = message(
        "这个事情你怎么看？",
        single_chat=True,
        sender_user_id=None,
    )
    same_name_message.sender_name = "明哥"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [same_name_message]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert len(codex.calls) == 1
    assert final_sent(dws) == []


def test_message_before_current_user_reply_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    requester = message(
        "@Alex Chen(明哥) push了",
        message_id="msg-before-self",
    )
    requester.create_time = "2026-05-13 08:45:50"
    manual_reply = principal_message(
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
    manual_reply = principal_message(
        "这个ACL表@张晓民(Xiaomin张晓民) 看一下",
        message_id="msg-self-before",
        create_time="2026-05-13 15:15:14",
    )
    requester = message(
        "@Alex Chen(明哥) 我和俊杰聊下",
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
    assert "@Alex Chen(明哥) 我和俊杰聊下" in codex.calls[0][0]
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
        "@Alex Chen(明哥) 这个怎么处理？",
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
            "@周俊杰 先按A方案走（by明哥分身）",
        )
    ]


def test_group_mention_from_unread_conversation_is_processed_when_unread_tail_misses_it(
    tmp_path: Path, monkeypatch
):
    unread_tail = message("后续同步进展", message_id="msg-tail")
    unread_tail.create_time = "2026-05-25 17:53:12"
    missed_mention = message(
        "@Alex Chen(明哥) 要不现在对一下",
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


def test_group_mention_from_unread_payload_is_processed_when_mention_lookup_misses_it(
    tmp_path: Path, monkeypatch
):
    unread_mention = message(
        "@Alex Chen(明哥) 官网反馈这条帮忙看一下",
        message_id="msg-unread-mention",
    )
    unread_mention.create_time = "2026-05-25 17:53:12"
    conv = conversation()
    conv.unread_point = 4
    dws = FakeDws(
        [conv],
        {"cid-1": [unread_mention]},
        unread_messages={"cid-1": [unread_mention]},
    )
    dws.mentioned_messages = {}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="这条我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert dws.unread_message_reads[0] == "cid-1"
    assert len(codex.calls) == 1
    assert attempts[0].trigger_message_id == "msg-unread-mention"
    assert attempts[0].send_status == "dry_run"


def test_produce_once_triggers_only_latest_consecutive_group_mention_from_same_sender(
    tmp_path: Path, monkeypatch
):
    first = message(
        "@Alex Chen(明哥) 先看第一点",
        message_id="msg-mentioned-1",
    )
    first.create_time = "2026-05-28 13:21:54"
    second = message(
        "@曹宇航(Yuhang Cao) @Alex Chen(明哥) 再看第二点",
        message_id="msg-mentioned-2",
    )
    second.create_time = "2026-05-28 13:24:02"
    third = message(
        "@Alex Chen(明哥) @曹宇航(Yuhang Cao) 最后总结一下",
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
    assert tasks[0].trigger_text == "@Alex Chen(明哥) @曹宇航(Yuhang Cao) 最后总结一下"
    assert codex.calls == []


def test_produce_once_triggers_only_latest_single_chat_message(
    tmp_path: Path, monkeypatch
):
    first = message("先看第一点", message_id="msg-single-1", single_chat=True)
    first.create_time = "2026-05-28 13:21:54"
    second = message("再看第二点", message_id="msg-single-2", single_chat=True)
    second.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [first, second]},
        unread_messages={"cid-1": [first, second]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-single-2"
    assert tasks[0].trigger_text == "再看第二点"


def test_produce_once_replaces_pending_single_chat_task_with_latest_message(
    tmp_path: Path, monkeypatch
):
    first = message("先看第一点", message_id="msg-single-1", single_chat=True)
    first.create_time = "2026-05-28 13:21:54"
    second = message("再看第二点", message_id="msg-single-2", single_chat=True)
    second.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [first]},
        unread_messages={"cid-1": [first]},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )

    assert worker.produce_once() == 1

    dws.messages = {"cid-1": [first, second]}
    dws.unread_messages = {"cid-1": [first, second]}
    assert worker.produce_once() == 1

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-single-2"
    assert tasks[0].trigger_text == "再看第二点"


def test_produce_once_triggers_only_latest_group_thread_reply(
    tmp_path: Path, monkeypatch
):
    first_thread_reply = message(
        "@Alex Chen(明哥) 这个 thread 先看第一点",
        message_id="msg-thread-1",
        quoted_content="同一个 thread",
        sender_user_id="sender-user-1",
    )
    first_thread_reply.create_time = "2026-05-28 13:21:54"
    other_topic = message(
        "@Alex Chen(明哥) 另一个话题",
        message_id="msg-other-topic",
        sender_user_id="sender-user-2",
    )
    other_topic.create_time = "2026-05-28 13:22:54"
    latest_thread_reply = message(
        "@Alex Chen(明哥) 这个 thread 最后看这里",
        message_id="msg-thread-2",
        quoted_content="同一个 thread",
        sender_user_id="sender-user-3",
    )
    latest_thread_reply.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [first_thread_reply, other_topic, latest_thread_reply]},
        unread_messages={"cid-1": [first_thread_reply, other_topic, latest_thread_reply]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = sorted(
        worker.store.claim_reply_tasks(limit=10),
        key=lambda task: task.trigger_create_time,
    )
    assert queued == 2
    assert [task.trigger_message_id for task in tasks] == [
        "msg-other-topic",
        "msg-thread-2",
    ]
    assert tasks[1].trigger_text == "@Alex Chen(明哥) 这个 thread 最后看这里"


def test_single_chat_oa_card_followup_triggers_followup_only(
    tmp_path: Path, monkeypatch
):
    oa_card = message(
        "Roy Han's 招聘需求申请\n"
        "申请人: Roy Han\n"
        "招聘岗位: 大模型数据项目实习生\n"
        "[dingtalk://dingtalkclient/action/open_platform_link?"
        "pcLink=https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1%26swfrom%3Doa"
        "%26dinghash%3Dapproval](dingtalk://dingtalkclient/action/open_platform_link)",
        message_id="msg-oa-card",
        single_chat=True,
    )
    oa_card.create_time = "2026-06-08 18:36:39"
    followup = message(
        "磊哥请你的分身审核一遍，并判断这个需求是否必要，以及是否有其他建议",
        message_id="msg-followup",
        single_chat=True,
    )
    followup.create_time = "2026-06-08 18:36:57"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [oa_card, followup]},
        unread_messages={"cid-1": [oa_card, followup]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-followup"
    assert tasks[0].trigger_text == "磊哥请你的分身审核一遍，并判断这个需求是否必要，以及是否有其他建议"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.open_message_id == "msg-followup"


def test_mark_seen_tracks_all_latest_trigger_message_ids(tmp_path: Path, monkeypatch):
    first = message("@Alex Chen(明哥) 先看第一点", message_id="msg-mentioned-1")
    second = message("@Alex Chen(明哥) 再看第二点", message_id="msg-mentioned-2")
    third = message("@Alex Chen(明哥) 最后总结一下", message_id="msg-mentioned-3")
    dws = FakeDws([conversation()], {"cid-1": [first, second, third]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="no action needed")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    trigger = DingTalkAutoReplyWorker._latest_trigger_message([first, second, third])

    worker._mark_seen([trigger])

    assert worker.store.has_seen("msg-mentioned-1") is True
    assert worker.store.has_seen("msg-mentioned-2") is True
    assert worker.store.has_seen("msg-mentioned-3") is True


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
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
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
        sender_user_id="principal-user-1",
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


def test_broadcast_filter_does_not_resolve_sender_without_stable_identity(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@所有人 系统通知",
        message_id="msg-system-all",
    )
    broadcast.sender_name = "数据小蜜"
    broadcast.sender_user_id = None
    broadcast.sender_open_dingtalk_id = None
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    dws = FakeDws([], {"cid-website": [broadcast]})
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="not relevant")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker._broadcast_messages_by_conversation() == {"cid-website": [broadcast]}
    assert dws.current_user_checks == []


def test_read_group_mention_is_skipped_when_later_current_user_text_replied(
    tmp_path: Path, monkeypatch
):
    mentioned = message(
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-mkt-mention",
    )
    mentioned.open_conversation_id = "cid-mkt"
    mentioned.conversation_title = "MKT core"
    mentioned.create_time = "2026-05-25 19:21:56"
    manual_reply = principal_message(
        "会的，晚上也会处理需要回复的消息",
        message_id="msg-principal-text",
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


def test_read_group_mention_after_seen_message_is_processed_from_mentions(
    tmp_path: Path, monkeypatch
):
    handled = message(
        "@Alex Chen(明哥) 客户问 Hyperion 怎么讲？",
        message_id="msg-handled",
    )
    handled.open_conversation_id = "cid-hyperion"
    handled.conversation_title = "奔驰北美-Hyperion需求"
    handled.create_time = "2026-05-29 14:19:12"
    bot_reply = principal_message(
        "不能讲成 persona 报告，要讲成 marketing 决策盲区。",
        message_id="msg-bot-reply",
        create_time="2026-05-29 14:32:48",
    )
    bot_reply.open_conversation_id = "cid-hyperion"
    bot_reply.conversation_title = "奔驰北美-Hyperion需求"
    follow_up = message(
        "@Alex Chen(明哥) 这个好。@何耘光(Jack He(Yunguang He)) 我喜欢明哥分身的答案，更抓客户胃口",
        message_id="msg-follow-up",
    )
    follow_up.open_conversation_id = "cid-hyperion"
    follow_up.conversation_title = "奔驰北美-Hyperion需求"
    follow_up.create_time = "2026-05-29 14:35:51"
    conversation_record = conversation()
    conversation_record.open_conversation_id = "cid-hyperion"
    conversation_record.title = "奔驰北美-Hyperion需求"
    conversation_record.unread_point = 0

    dws = FakeDws(
        [],
        {"cid-hyperion": [handled, bot_reply, follow_up]},
        unread_messages={"cid-hyperion": []},
    )
    dws.mentioned_messages = {"cid-hyperion": [follow_up]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    worker.store.upsert_conversation(
        "cid-hyperion",
        "奔驰北美-Hyperion需求",
        False,
        None,
    )
    worker.store.mark_seen("msg-handled", "cid-hyperion")

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-follow-up"
    assert "我喜欢明哥分身的答案" in tasks[0].trigger_text
    assert dws.recent_message_reads == ["cid-hyperion"]


def test_split_person_auto_reply_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    handled = message(
        "@Alex Chen(明哥) 和我迭代一下材料",
        message_id="msg-handled",
    )
    handled.open_conversation_id = "cid-iter"
    handled.conversation_title = "迭代群"
    handled.create_time = "2026-05-29 21:53:36"
    missed = message(
        "@Alex Chen(明哥) 这个分身能读群历史和群文件吗？",
        message_id="msg-missed",
    )
    missed.open_conversation_id = "cid-iter"
    missed.conversation_title = "迭代群"
    missed.create_time = "2026-05-29 21:55:10"
    auto_reply = principal_message(
        "可以，别先把我屏蔽了。（by明哥分身）",
        message_id="msg-auto-reply",
        create_time="2026-05-29 21:55:41",
    )
    auto_reply.open_conversation_id = "cid-iter"
    auto_reply.conversation_title = "迭代群"
    conversation_record = conversation()
    conversation_record.open_conversation_id = "cid-iter"
    conversation_record.title = "迭代群"
    conversation_record.unread_point = 0

    dws = FakeDws(
        [],
        {"cid-iter": [handled, missed, auto_reply]},
        unread_messages={"cid-iter": []},
    )
    dws.mentioned_messages = {"cid-iter": [missed]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    worker.store.upsert_conversation("cid-iter", "迭代群", False, None)
    worker.store.mark_seen("msg-handled", "cid-iter")

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-missed"
    assert "能读群历史和群文件吗" in tasks[0].trigger_text
    assert dws.recent_message_reads == ["cid-iter"]


def test_group_mentions_are_processed_by_message_time_not_fetch_order(
    tmp_path: Path, monkeypatch
):
    older_mention = message(
        "@Alex Chen(明哥) 怎么规避客户拿给别的 vendor 比价？",
        message_id="msg-older-mention",
    )
    older_mention.create_time = "2026-05-26 07:54:36"
    newer_mention = message(
        "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见",
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
    assert attempts[0].trigger_text == "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见"


def test_current_user_file_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-25 19:21:56"
    self_file = principal_message(
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
        "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-26 08:34:57"
    ack = principal_message(
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


def test_internal_personnel_question_missing_subject_refuses_instead_of_asking(
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
        ("cid-1", "这个涉及其他人的人事信息，我不能直接回答。（by明哥分身）")
    ]


def test_internal_personnel_question_allows_private_self_subject(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("我转正怎么看？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="你这次转正材料看起来可以，但后续要补齐闭环。",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="sender-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        ("cid-1", "你这次转正材料看起来可以，但后续要补齐闭环。（by明哥分身）")
    ]


def test_internal_personnel_question_allows_private_hr_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.hr_users.add("sender-user-1")
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
        ("cid-1", "先按事实反馈（by明哥分身）")
    ]


def test_internal_personnel_question_does_not_auto_allow_manager(
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
        ("cid-1", "这个涉及其他人的人事信息，我不能直接回答。（by明哥分身）")
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
        ("cid-1", "这个涉及其他人的人事信息，我不能直接回答。（by明哥分身）")
    ]


def test_internal_personnel_question_never_replies_sensitive_detail_in_group(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [message("@Alex Chen(明哥) 我绩效怎么定？", single_chat=False)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="你这次可以按高绩效处理",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="sender-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert final_sent(dws) == [
        ("cid-1", "@周俊杰 这个涉及个人敏感信息，不适合在群里展开，单独同步我。（by明哥分身）")
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
        ("cid-1", "这个候选人是哪个岗位/部门的？（by明哥分身）")
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

    assert final_sent(dws) == [("cid-1", "可以推进（by明哥分身）")]


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
        ("cid-1", "这个候选人信息只回答相关部门的人。（by明哥分身）")
    ]


def test_permission_lookup_failure_records_error_and_does_not_send(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
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
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()], {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]}
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
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
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
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
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
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
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
        "app.worker.send_macos_notification",
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
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
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


def test_handoff_ding_failure_does_not_block_ack(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 不要分身，真人看一下")]},
        ding_error=RuntimeError("ding failed"),
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.message_text_emotions == [
        ("cid-1", "msg-1", "我去叫", "created-1", "我去叫", "created-bg")
    ]
    assert store.has_seen("msg-1") is True
    assert store.count_errors() == 1
    assert store.count_reply_tasks(status="done") == 1
    assert len(notifications) == 1
    assert notifications[0]["title"] == "CEO handoff: Friday"
    assert (
        notifications[0]["url"]
        == "http://127.0.0.1:8765/open-dingtalk?conversation_id=cid-1"
    )
    assert notifications[0]["message"].startswith(
        "DING unavailable; delivered by local notification. Friday\n"
    )
    assert "不要分身" in notifications[0]["message"]


def test_persists_codex_last_session_id_after_decision(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws([conversation()], {"cid-1": [message("@Alex Chen(明哥) cc一下")]})
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
        "app.worker.send_macos_notification", lambda **_: None
    )
    dws = FakeDws([conversation()], {"cid-1": [message("@Alex Chen(明哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        last_session_id="stale-session",
    )
    worker = DingTalkAutoReplyWorker(
        store=store, dws=dws, codex=codex, now_provider=fixed_worker_now
    )

    worker.run_once()

    assert store.get_codex_session_id("cid-1") is None
