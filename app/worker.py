import json
import logging
import os
import re
import shutil
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from app.codex_decision import (
    REPLY_AGENT_ENVELOPE_SCHEMA_HINT,
    append_signature,
    codex_decision_from_envelope,
)
from app.config import (
    assistant_signature,
    broadcast_mention_aliases,
    env_duration,
    fast_path_unread_backoff_duration,
    feedback_spike_vercel_base_url,
    handoff_ack,
    message_recovery_interval,
    notification_bridge_base_url,
    principal_display_name,
    single_chat_read_recovery_limit,
    single_chat_read_recovery_window,
)
from app.dws_client import (
    DINGTALK_MESSAGE_TIME_ZONE,
    DwsCalendarEvent,
    DwsClient,
    DwsDocumentSearchResult,
    DwsError,
    native_reply_delivery_payload,
)
from app.feedback_spike import append_feedback_links, prepare_outgoing_reply_text
from app.feedback_events import sync_feedback_events_for_sent_replies
from app.feedback_policy import (
    FEEDBACK_REQUIRED_LINK_PREFIX,
    requires_feedback_block,
    requires_feedback_reminder,
)
from app.corpus import (
    MEDIA_OR_LINK_PATTERN,
    CorpusRecord,
    count_information_units,
    extract_retrieval_keywords,
    retrieve_similar_examples,
)
from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
)
from app.leak_check import (
    FORBIDDEN_MARKERS,
    contains_forbidden_leak,
)
from app.message_split import split_dingtalk_text
from app.notification import send_macos_notification
from app.oa_approval import extract_oa_url
from app.okr_review import current_quarter_period
from app.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    refresh_org_cache,
)
from app.permission import PermissionAction, PermissionGate
from app.prompt import LinkedDocumentContext, MaterialReferenceContext, build_turn_prompt
from app.store import (
    FAST_PATH_UNREAD_BACKOFF_TASK_ERROR,
    AutoReplyStore,
    ReplyAttempt,
    ReplyTask,
)
from app.task_models import WorkItem

logger = logging.getLogger(__name__)

HANDOFF_ACK = handoff_ack()
HANDOFF_TEXT_EMOTION = "我去叫"
# Historical auto-ack marker. Keep filtering it from context, but do not send
# new processing acknowledgements before final replies.
PROCESSING_ACK = "收到，我正在处理（by 分身）"
CODEX_LOGIN_REQUIRED_PREFIX = "codex_login_required"
CRITICAL_INFO_UNAVAILABLE_PREFIX = "critical_info_unavailable:"
DEFAULT_TEXT_EMOTION_BACKGROUND_ID = "im_bg_5"
LEAK_CHECK_REGENERATION_SCHEMA = REPLY_AGENT_ENVELOPE_SCHEMA_HINT
SPLIT_PERSON_SIGNATURE = assistant_signature()
STALE_PROCESSING_TASK_SECONDS = 30 * 60
MAX_REPLY_TASK_ATTEMPTS = 3
REPLY_TASK_RETRY_BASE_DELAY_SECONDS = 60
REPLY_TASK_RETRY_MAX_DELAY_SECONDS = 15 * 60
STALE_CODEX_RESUME_ATTEMPTS = 2
CALENDAR_PENDING_INVITE_LOOKAHEAD_DAYS = 14
CALENDAR_PENDING_INVITE_EVENT_MATCH_SECONDS = 5 * 60
CALENDAR_PENDING_INVITE_NO_CHANGE_TIME_START_LOOKAHEAD = timedelta(hours=24)
CALENDAR_CONTEXT_MATCH_MIN_SCORE = 0.05
CALENDAR_CONTEXT_MATCH_LOOKBACK = timedelta(minutes=10)
CALENDAR_ORGANIZER_RESPONSE_ERROR = "Cannot change response status of event organizer"
CALENDAR_BURST_REPLY_SUPPRESSION_WINDOW = timedelta(minutes=30)
DWS_TRANSIENT_ERROR_STATE_PREFIX = "dws_transient_error_count:"
DWS_TRANSIENT_NOTIFY_THRESHOLD = 3
T = TypeVar("T")
CALENDAR_ACTION_SEND_STATUS = "calendar"
TEXT_MESSAGE_TYPES = {"text"}
RENDERED_NON_TEXT_PREFIXES = (
    "[文件]",
    "[图片]",
    "[视频]",
    "[日程]",
)
RENDERED_NON_TEXT_PREFIX_PATTERN = re.compile(
    r"^\s*[\[［【]\s*(?:文件|图片|视频|日程)\s*[\]］】]",
    re.IGNORECASE,
)
DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN = re.compile(
    r"dingtalk://|https?://[^\s)]*dingtalk\.com|\[(?:文件|图片|视频|日程)\]",
    re.IGNORECASE,
)
DINGTALK_APPROVAL_LINK_PATTERN = re.compile(
    r"aflow\.dingtalk\.com|dinghash(?:=|%3D)approval|swfrom(?:=|%3D)oa",
    re.IGNORECASE,
)
DINGTALK_APPROVAL_REMINDER_PATTERN = re.compile(
    r"^\s*\[Ding]\S{1,40}提醒您审批", re.IGNORECASE
)
ORDINARY_EXTERNAL_LINK_PATTERN = re.compile(
    r"https?://(?![^\s)]*dingtalk\.com)\S+",
    re.IGNORECASE,
)
SYSTEM_STATUS_NOTIFICATION_PATTERN = re.compile(
    r"""
    ^\s*(?:
        (?:AI\s*)?自动同步(?:完成|成功|失败)(?:[:：]\S.*)?
        |已同步到(?:知识库|文档|项目)(?:[:：]\S.*)
        |(?:文件|文档)[^\n，,。；;？?]{0,40}(?:已上传|已更新|上传完成|更新完成)(?:[:：]\S.*)?
        |已更新文档(?:[:：]\S.*)?
        |(?:项目立项|流程|审批)[^\n，,。；;？?]{0,40}(?:已提交|已通过|被退回|已退回|已撤回|已流转)(?:[:：]\S.*)?
    )\s*$
    """,
    re.VERBOSE,
)
QUESTION_MARK_PATTERN = re.compile(r"[?？]")
FIELD_LINE_PATTERN = re.compile(r"^\s*[^:：\n]{1,60}[:：]\s*\S+")
MENTION_PATTERN = re.compile(
    r"@[^\s@()（），,。；;：:、?？!！]+"
    r"(?:\s+[A-Za-z][^\s@()（），,。；;：:、?？!！]*)?"
    r"(?:[（(](?:[^()（）]|[（(][^()（）]*[）)])*[）)])?"
)
DINGTALK_DOC_URL_PATTERN = re.compile(
    r"https://(?:alidocs|docs)\.dingtalk\.com/i/nodes/[^\s)\]]+"
)
DINGTALK_MINUTES_LINK_PATTERN = re.compile(
    r"(?:dingtalk://[^\s)\]]*flash_minutes_detail[^\s)\]]*|"
    r"https://shanji\.dingtalk\.com/app/transcribes/[^\s)\]]+)",
    re.IGNORECASE,
)
DINGTALK_SHANJI_DOC_SELECTOR_PATTERN = re.compile(
    r"https://alidocs\.dingtalk\.com/i/u/dingdocSelectorV4/save\?[^\s)\]]*"
    r"resourceType=SHANJI[^\s)\]]*",
    re.IGNORECASE,
)


def _is_codex_login_required_error(reason: str) -> bool:
    normalized = reason.lower()
    return (
        "failed to refresh token" in normalized
        and "session has ended" in normalized
    ) or "token_invalidated" in normalized


def _extract_text_emotion_id(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("emotionId", "emotion_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _extract_text_emotion_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _extract_text_emotion_id(value)
            if found:
                return found
    return ""


def _extract_text_emotion_background_id(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("backgroundId", "background_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _extract_text_emotion_background_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _extract_text_emotion_background_id(value)
            if found:
                return found
    return ""


MINUTES_SUMMARY_MAX_CHARS = 5000
MINUTES_TRANSCRIPTION_PARAGRAPH_LIMIT = 20
FILE_MESSAGE_PATTERN = re.compile(r"^\s*\[文件]\s*(?P<name>.+?)\s*$")
IMAGE_MESSAGE_MEDIA_ID_PATTERN = re.compile(r"\[图片消息]\(mediaId=(?P<media_id>[^)]+)\)")
MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*]\((?P<url>https?://[^)]+)\)")
DINGTALK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GROUP_CONTEXT_RECOVERY_WINDOW = timedelta(hours=24)
RECENT_REPLY_WINDOW = timedelta(hours=24)
REFERENCED_FILE_CONTEXT_WINDOW = timedelta(minutes=10)
DOWNLOADED_FILE_MAX_BYTES = 50 * 1024 * 1024
DOWNLOADED_IMAGE_MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
PDF_TEXT_PAGE_LIMIT = 30
DWS_UPGRADE_CHECKED_DATE_STATE_KEY = "dws_upgrade_checked_date"
MESSAGE_RECOVERY_CHECKED_AT_STATE_KEY = "message_recovery_checked_at"
MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY = "message_fast_path_checked_at"
DWS_AUTH_LOGIN_STATE_KEY = "dws_auth_login"
DWS_AUTH_BACKUP_STATE_KEY = "dws_auth_backup"
DWS_AUTH_BACKUP_INTERVAL = env_duration(
    "CEO_DWS_AUTH_BACKUP_INTERVAL",
    timedelta(minutes=30),
)
DWS_AUTH_BACKUP_STATUS_FIELDS = (
    "authenticated",
    "token_valid",
    "refresh_token_valid",
    "expires_at",
    "refresh_expires_at",
)
DWS_AUTH_LOGIN_REQUEST_SUPPRESSION_WINDOW = timedelta(hours=1)
DWS_FORBIDDEN_CONVERSATIONS_STATE_KEY = "dws_forbidden_conversations"
DWS_FORBIDDEN_CONVERSATION_COOLDOWN = timedelta(minutes=5)
ORG_CACHE_REFRESH_INTERVAL = timedelta(days=7)
AITABLE_TABLE_PREVIEW_LIMIT = 5
AITABLE_RECORD_PREVIEW_LIMIT = 10
MESSAGE_RECOVERY_INTERVAL = message_recovery_interval()
FAST_PATH_UNREAD_BACKOFF = fast_path_unread_backoff_duration()
SINGLE_CHAT_READ_RECOVERY_WINDOW = single_chat_read_recovery_window()
SINGLE_CHAT_READ_RECOVERY_LIMIT = single_chat_read_recovery_limit()


@dataclass(frozen=True)
class CalendarConflictContext:
    invite: DwsCalendarEvent
    conflicts: list[DwsCalendarEvent]


@dataclass(frozen=True)
class ReplyAtTarget:
    user_id: str = ""
    open_dingtalk_id: str = ""
    name: str = ""


class ReplyDeliveryError(RuntimeError):
    """Raised after recording a delivery failure so queued tasks can retry."""


class ReplyTaskProcessingError(RuntimeError):
    """Raised after recording a processing failure so queued tasks can retry."""


class CriticalInformationUnavailableError(ReplyTaskProcessingError):
    """Raised when required material/tool output is unavailable and retrying is unsafe."""


class DingTalkAutoReplyWorker:
    def __init__(
        self,
        store: AutoReplyStore,
        dws,
        codex,
        dry_run: bool = False,
        style_profile: str = "",
        style_records: list[CorpusRecord] | None = None,
        style_example_limit: int = 4,
        send_attempts: int = 2,
        max_task_attempts: int = MAX_REPLY_TASK_ATTEMPTS,
        now_provider: Callable[[], datetime] | None = None,
        oa_approval_handler=None,
    ):
        self.store = store
        self.dws = dws
        self.codex = codex
        self.dry_run = dry_run
        self.style_profile = style_profile.strip()
        self.style_records = style_records or []
        self.style_example_limit = style_example_limit
        self.send_attempts = send_attempts
        self.max_task_attempts = max_task_attempts
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())
        self.permission_gate = PermissionGate(dws)
        self.oa_approval_handler = oa_approval_handler
        self._dws_auth_login_process = None

    def run_once(self, max_batches: int | None = None) -> None:
        try:
            self.produce_once(max_tasks=max_batches)
            self.consume_once(max_tasks=max_batches)
        finally:
            self._cleanup_image_attachment_cache()

    def _cleanup_image_attachment_cache(self) -> None:
        image_dir = self.store.path.parent / "image-attachments"
        if image_dir.exists():
            shutil.rmtree(image_dir)

    def _call_dws(
        self,
        kind: str,
        call: Callable[[], T],
        *,
        conversation_id: str | None = None,
        message_id: str | None = None,
        notify_title: str | None = None,
        raise_authorization: bool = False,
        record_forbidden_error: bool = True,
        default: T,
    ) -> T:
        try:
            result = call()
            self._clear_dws_transient_error(kind)
            if conversation_id:
                self._clear_dws_read_forbidden(conversation_id)
            self._maybe_backup_dws_auth()
            return result
        except Exception as exc:
            if raise_authorization and self._is_authorization_error(exc):
                raise
            if self._is_dws_login_error(exc):
                restored_result = self._restore_dws_auth_backup_and_retry(
                    kind,
                    call,
                    conversation_id=conversation_id,
                )
                if restored_result is not None:
                    return restored_result
                if self._ensure_dws_auth_login(exc):
                    return default
            is_forbidden_read = bool(
                conversation_id and self._is_dws_forbidden_read_error(exc)
            )
            if is_forbidden_read:
                self._mark_dws_read_forbidden(conversation_id)
            should_notify = bool(notify_title)
            should_record_error = record_forbidden_error or not is_forbidden_read
            if notify_title and self._is_dws_transient_error(exc):
                should_notify = self._record_dws_transient_error(kind, str(exc))
                should_record_error = should_notify
            if should_record_error:
                self.store.record_error(conversation_id, message_id, kind, str(exc))
            if should_notify and notify_title:
                self._notify(
                    title=notify_title,
                    message=str(exc)[:120],
                )
            return default

    @staticmethod
    def _is_dws_transient_error(exc: Exception) -> bool:
        return isinstance(exc, DwsError) and exc.code in DwsClient.RETRYABLE_ERROR_CODES

    def _record_dws_transient_error(self, kind: str, detail: str) -> bool:
        key = f"{DWS_TRANSIENT_ERROR_STATE_PREFIX}{kind}"
        current = self.store.get_service_state(key)
        count = 0
        if current:
            try:
                payload = json.loads(current)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                count = int(payload.get("count") or 0)
        count += 1
        self.store.set_service_state(
            key,
            json.dumps(
                {
                    "count": count,
                    "last_error": detail[:500],
                    "updated_at": self._now().astimezone(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
        )
        return count == DWS_TRANSIENT_NOTIFY_THRESHOLD

    def _clear_dws_transient_error(self, kind: str) -> None:
        key = f"{DWS_TRANSIENT_ERROR_STATE_PREFIX}{kind}"
        current = self.store.get_service_state(key)
        if not current:
            return
        self.store.set_service_state(
            key,
            json.dumps(
                {
                    "count": 0,
                    "last_error": "",
                    "updated_at": self._now().astimezone(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
        )

    def _read_conversation_messages(
        self,
        kind: str,
        conversation: DingTalkConversation,
        reader: Callable[[], T],
        *,
        message_id: str | None = None,
        raise_authorization: bool = False,
        default: T,
    ) -> T:
        if self._is_dws_read_forbidden(conversation.open_conversation_id):
            return default
        return self._call_dws(
            kind,
            reader,
            conversation_id=conversation.open_conversation_id,
            message_id=message_id,
            raise_authorization=raise_authorization,
            record_forbidden_error=False,
            default=default,
        )

    def produce_once(self, max_tasks: int | None = None) -> int:
        if max_tasks == 0:
            return 0
        self._maybe_upgrade_dws_once_per_day()
        self._maybe_refresh_org_cache_once_per_week()
        fast_path_checked_at = self._now().astimezone(timezone.utc)
        recovery_due = self._should_run_recent_message_recovery()
        queued_tasks = 0
        conversations = self._call_dws(
            "list_unread_conversations",
            lambda: self.dws.list_unread_conversations(count=50),
            notify_title="CEO read unread conversations failed",
            default=None,
        )
        if conversations is None:
            return 0
        self._mark_dws_auth_healthy()
        if not recovery_due:
            conversations = self._conversations_due_for_fast_path(conversations)
        unread_conversation_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        mentioned_messages = self._mentioned_messages_by_conversation(conversations)
        broadcast_messages = self._broadcast_messages_by_conversation()
        addressed_messages = self._merge_message_groups(
            mentioned_messages,
            broadcast_messages,
        )
        conversations = self._conversations_with_mentions(
            conversations,
            addressed_messages,
        )
        conversations, recovery_conversation_ids = (
            self._conversations_with_due_recent_recovery(
                conversations,
                recovery_due=recovery_due,
            )
        )
        for conversation in conversations:
            self.store.upsert_conversation(
                conversation_id=conversation.open_conversation_id,
                title=conversation.title,
                single_chat=conversation.single_chat,
                codex_session_id=None,
            )
            conversation_mentions = addressed_messages.get(
                conversation.open_conversation_id, []
            )
            context_messages = []
            should_read_recent = self._should_read_recent_messages(
                conversation,
                conversation_mentions,
                recovery_due=recovery_due,
                recovery_conversation_ids=recovery_conversation_ids,
            )
            if should_read_recent:
                context_messages = self._read_conversation_messages(
                    "read_recent_messages",
                    conversation,
                    lambda: self.dws.read_recent_messages(conversation),
                    default=[],
                )
            unread_messages = []
            candidate_unread_messages = []
            should_read_unread = self._should_read_unread_messages(
                conversation,
                conversation_mentions,
                recovery_due=recovery_due,
                unread_conversation_ids=unread_conversation_ids,
            )
            if should_read_unread:
                unread_messages = self._read_conversation_messages(
                    "read_unread_messages",
                    conversation,
                    lambda: self.dws.read_unread_messages(conversation),
                    default=None,
                )
                if unread_messages is None:
                    unread_messages = []
                    candidate_unread_messages = context_messages
                else:
                    candidate_unread_messages = unread_messages
            if (
                not context_messages
                and not unread_messages
                and not conversation_mentions
            ):
                continue
            candidate_source_messages = self._candidate_source_messages(
                conversation,
                context_messages,
                candidate_unread_messages,
                conversation_mentions,
            )
            candidates = self._candidate_messages(
                conversation,
                candidate_source_messages,
            )
            new_messages = [
                message
                for message in candidates
                if not self.store.has_seen(message.open_message_id)
            ]
            if not new_messages:
                continue
            new_messages = self._skip_messages_outside_recent_window(
                conversation,
                new_messages,
            )
            if not new_messages:
                continue
            new_messages = self._skip_system_or_notification_messages(
                conversation,
                new_messages,
            )
            if not new_messages:
                continue
            trigger_messages = self._reply_task_trigger_messages(
                conversation,
                new_messages,
                source_messages=candidate_source_messages,
            )
            for message in trigger_messages:
                available_at = ""
                error = ""
                if (
                    FAST_PATH_UNREAD_BACKOFF > timedelta(0)
                    and not recovery_due
                    and conversation.open_conversation_id in unread_conversation_ids
                ):
                    available_at = self._sqlite_timestamp(
                        fast_path_checked_at + FAST_PATH_UNREAD_BACKOFF
                    )
                    error = FAST_PATH_UNREAD_BACKOFF_TASK_ERROR
                if self._enqueue_reply_task(
                    conversation,
                    message,
                    context_messages=self._prompt_context_messages(
                        context_messages,
                        unread_messages,
                    ),
                    available_at=available_at,
                    error=error,
                    replace_pending_single_chat=len(trigger_messages) == 1,
                ):
                    queued_tasks += 1
                if max_tasks is not None and queued_tasks >= max_tasks:
                    self.store.set_service_state(
                        MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY,
                        fast_path_checked_at.isoformat(),
                    )
                    return queued_tasks
        self.store.set_service_state(
            MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY,
            fast_path_checked_at.isoformat(),
        )
        return queued_tasks

    @staticmethod
    def _should_read_unread_messages(
        conversation: DingTalkConversation,
        conversation_mentions: list[DingTalkMessage],
        *,
        recovery_due: bool,
        unread_conversation_ids: set[str],
    ) -> bool:
        return conversation.open_conversation_id in unread_conversation_ids

    @staticmethod
    def _should_read_recent_messages(
        conversation: DingTalkConversation,
        conversation_mentions: list[DingTalkMessage],
        *,
        recovery_due: bool,
        recovery_conversation_ids: set[str],
    ) -> bool:
        if conversation.open_conversation_id in recovery_conversation_ids:
            return True
        if not recovery_due:
            return False
        if conversation.single_chat:
            return True
        return bool(conversation_mentions)

    @staticmethod
    def _reply_task_message(task: ReplyTask) -> DingTalkMessage | None:
        try:
            return DingTalkMessage.model_validate_json(task.trigger_message_json)
        except ValueError:
            return None

    def _calendar_card_message_with_pending_invite(
        self,
        message: DingTalkMessage,
        event: DwsCalendarEvent,
    ) -> DingTalkMessage:
        title = event.title.strip() or "未命名日程"
        return message.model_copy(
            update={
                "message_type": message.message_type or "calendar",
                "content": f"[日程] {title}",
                "raw_payload": self._calendar_event_raw_payload(event),
            }
        )

    def _calendar_card_message_with_available_details(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
        context_messages: list[DingTalkMessage] | None = None,
    ) -> DingTalkMessage:
        if not self._is_calendar_message(message):
            return message
        invite = self._calendar_invite_from_message_or_sender(
            conversation,
            message,
            context_messages=context_messages,
            include_resolved=True,
        )
        if invite is None:
            return message
        return self._calendar_card_message_with_pending_invite(message, invite)

    def _calendar_invite_from_message_or_sender(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
        *,
        context_messages: list[DingTalkMessage] | None = None,
        include_resolved: bool = False,
    ) -> DwsCalendarEvent | None:
        calendar_invite_from_message = getattr(
            self.dws,
            "calendar_invite_from_message",
            None,
        )
        if calendar_invite_from_message is None:
            return None
        invite = calendar_invite_from_message(message)
        if invite is not None:
            return invite
        invite = self._calendar_invite_from_existing_attempt(conversation, message)
        if invite is not None:
            return invite
        list_calendar_events = getattr(self.dws, "list_calendar_events", None)
        if list_calendar_events is None:
            return None
        return self._calendar_pending_invite_from_sender(
            message,
            list_calendar_events,
            context_messages=context_messages,
            include_resolved=include_resolved,
        )

    @staticmethod
    def _calendar_event_is_active(event: DwsCalendarEvent) -> bool:
        return event.status.strip().lower() != "cancelled"

    @staticmethod
    def _calendar_event_has_attendee(event: DwsCalendarEvent, attendee_name: str) -> bool:
        expected = attendee_name.strip()
        if not expected:
            return False
        return any(attendee.strip() == expected for attendee in event.attendees)

    @staticmethod
    def _calendar_event_raw_payload(event: DwsCalendarEvent) -> dict[str, Any]:
        return {
            "id": event.event_id,
            "summary": event.title,
            "description": event.description,
            "comments": event.comments,
            "organizer": {"displayName": event.organizer},
            "start": {"dateTime": event.start_time},
            "end": {"dateTime": event.end_time},
            "attendees": [
                {
                    "displayName": attendee,
                    "responseStatus": (
                        event.self_response_status
                        if attendee == principal_display_name()
                        else ""
                    ),
                    "self": attendee == principal_display_name(),
                }
                for attendee in event.attendees
            ],
            "status": event.status,
            "created": event.created_ms,
            "updated": event.updated_ms,
        }

    def _conversations_with_recent_single_chat_recovery(
        self,
        conversations: list[DingTalkConversation],
    ) -> list[DingTalkConversation]:
        existing_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        since_utc = (
            self.now_provider().astimezone(timezone.utc)
            - SINGLE_CHAT_READ_RECOVERY_WINDOW
        ).strftime("%Y-%m-%d %H:%M:%S")
        recovered = []
        for record in self.store.list_recent_single_chat_conversations(
            since_utc,
            limit=SINGLE_CHAT_READ_RECOVERY_LIMIT,
        ):
            if record.conversation_id in existing_ids:
                continue
            existing_ids.add(record.conversation_id)
            recovered.append(
                DingTalkConversation(
                    open_conversation_id=record.conversation_id,
                    title=record.title,
                    single_chat=True,
                    unread_point=0,
                )
            )
        return [*conversations, *recovered]

    def _conversations_with_due_recent_recovery(
        self,
        conversations: list[DingTalkConversation],
        *,
        recovery_due: bool | None = None,
    ) -> tuple[list[DingTalkConversation], set[str]]:
        should_recover = (
            self._should_run_recent_message_recovery()
            if recovery_due is None
            else recovery_due
        )
        if not should_recover:
            return conversations, set()
        existing_ids = {
            conversation.open_conversation_id
            for conversation in conversations
        }
        recovered = self._conversations_with_recent_single_chat_recovery(conversations)
        recovery_conversation_ids = {
            conversation.open_conversation_id
            for conversation in recovered
            if conversation.open_conversation_id not in existing_ids
        }
        self.store.set_service_state(
            MESSAGE_RECOVERY_CHECKED_AT_STATE_KEY,
            self._now().astimezone(timezone.utc).isoformat(),
        )
        return recovered, recovery_conversation_ids

    def _conversations_updated_since_fast_path_check(
        self,
        conversations: list[DingTalkConversation],
    ) -> list[DingTalkConversation]:
        checked_at = self._service_state_datetime(
            MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY
        )
        if checked_at is None:
            return conversations
        return [
            conversation
            for conversation in conversations
            if self._conversation_updated_after(conversation, checked_at)
        ]

    @staticmethod
    def _conversation_updated_after(
        conversation: DingTalkConversation,
        checked_at: datetime,
    ) -> bool:
        if conversation.last_message_create_at is None:
            return True
        updated_at = datetime.fromtimestamp(
            conversation.last_message_create_at / 1000,
            timezone.utc,
        )
        return updated_at > checked_at.astimezone(timezone.utc)

    def _conversations_due_for_fast_path(
        self,
        conversations: list[DingTalkConversation],
    ) -> list[DingTalkConversation]:
        return self._conversations_updated_since_fast_path_check(conversations)

    @staticmethod
    def _is_dws_forbidden_read_error(exc: Exception) -> bool:
        detail = str(exc).lower()
        if "forbidden request" not in detail:
            return False
        if isinstance(exc, DwsError):
            return exc.code in {None, "1001"}
        return True

    def _mark_dws_read_forbidden(self, conversation_id: str) -> None:
        forbidden_until = (
            self._now().astimezone(timezone.utc)
            + DWS_FORBIDDEN_CONVERSATION_COOLDOWN
        ).isoformat()
        state = self._dws_forbidden_conversations()
        state[conversation_id] = forbidden_until
        self.store.set_service_state(
            DWS_FORBIDDEN_CONVERSATIONS_STATE_KEY,
            json.dumps(state, ensure_ascii=False, sort_keys=True),
        )

    def _is_dws_read_forbidden(self, conversation_id: str) -> bool:
        state = self._dws_forbidden_conversations()
        forbidden_until_text = state.get(conversation_id)
        if not forbidden_until_text:
            return False
        forbidden_until = self._parse_service_state_datetime(forbidden_until_text)
        if forbidden_until is None:
            self._clear_dws_read_forbidden(conversation_id)
            return False
        now_utc = self._now().astimezone(timezone.utc)
        forbidden_until_utc = forbidden_until.astimezone(timezone.utc)
        if forbidden_until_utc <= now_utc:
            self._clear_dws_read_forbidden(conversation_id)
            return False
        if forbidden_until_utc - now_utc > DWS_FORBIDDEN_CONVERSATION_COOLDOWN:
            self._clear_dws_read_forbidden(conversation_id)
            return False
        return True

    def _clear_dws_read_forbidden(self, conversation_id: str) -> None:
        state = self._dws_forbidden_conversations()
        if conversation_id not in state:
            return
        del state[conversation_id]
        self.store.set_service_state(
            DWS_FORBIDDEN_CONVERSATIONS_STATE_KEY,
            json.dumps(state, ensure_ascii=False, sort_keys=True),
        )

    def _dws_forbidden_conversations(self) -> dict[str, str]:
        raw = self.store.get_service_state(DWS_FORBIDDEN_CONVERSATIONS_STATE_KEY)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, str)
        }

    @staticmethod
    def _parse_service_state_datetime(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _sqlite_timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _service_state_datetime(self, key: str) -> datetime | None:
        value = self.store.get_service_state(key)
        if not value:
            return None
        return self._parse_service_state_datetime(value)

    def _should_run_recent_message_recovery(self) -> bool:
        checked_at = self.store.get_service_state(MESSAGE_RECOVERY_CHECKED_AT_STATE_KEY)
        if not checked_at:
            return True
        try:
            last_checked = datetime.fromisoformat(
                checked_at.replace("Z", "+00:00")
            )
        except ValueError:
            return True
        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=timezone.utc)
        return (
            self._now().astimezone(timezone.utc) - last_checked.astimezone(timezone.utc)
        ) >= MESSAGE_RECOVERY_INTERVAL

    def _maybe_upgrade_dws_once_per_day(self) -> None:
        today = self._now().date().isoformat()
        if self.store.get_service_state(DWS_UPGRADE_CHECKED_DATE_STATE_KEY) == today:
            return
        try:
            upgrade_check = self.dws.check_upgrade()
            if upgrade_check.get("needs_upgrade") is True:
                current_version = str(upgrade_check.get("current_version") or "")
                latest_version = str(upgrade_check.get("latest_version") or "")
                self.dws.upgrade()
                message = latest_version or "latest version"
                if current_version and latest_version:
                    message = f"{current_version} -> {latest_version}"
                self._notify(title="CEO DWS upgraded", message=message)
        except Exception as exc:
            self.store.record_error(None, None, "dws_upgrade", str(exc))
            self._notify(title="CEO DWS upgrade failed", message=str(exc)[:120])
        finally:
            self.store.set_service_state(DWS_UPGRADE_CHECKED_DATE_STATE_KEY, today)

    def _maybe_refresh_org_cache_once_per_week(self) -> None:
        today = self._now().date()
        last_refreshed_date = self.store.get_service_state(
            ORG_CACHE_REFRESHED_DATE_STATE_KEY
        )
        if last_refreshed_date:
            try:
                refreshed_date = datetime.strptime(
                    last_refreshed_date, "%Y-%m-%d"
                ).date()
            except ValueError:
                refreshed_date = None
            if (
                refreshed_date is not None
                and today - refreshed_date < ORG_CACHE_REFRESH_INTERVAL
            ):
                return
        try:
            refresh_org_cache(store=self.store, dws=self.dws)
        except Exception as exc:
            self.store.record_error(None, None, "org_cache_refresh", str(exc))
            self._notify(
                title="CEO org cache refresh failed",
                message=str(exc)[:120],
            )
        finally:
            self.store.set_service_state(
                ORG_CACHE_REFRESHED_DATE_STATE_KEY,
                today.isoformat(),
            )

    @staticmethod
    def _is_dws_login_error(exc: Exception) -> bool:
        return isinstance(exc, DwsError) and exc.needs_login

    def _dws_auth_login_state(self) -> dict[str, Any]:
        raw = self.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _set_dws_auth_login_state(self, state: dict[str, Any]) -> None:
        self.store.set_service_state(
            DWS_AUTH_LOGIN_STATE_KEY,
            json.dumps(state, ensure_ascii=False, sort_keys=True),
        )

    def _dws_auth_backup_path(self) -> Path:
        return self.store.path.parent / "dws-auth-backup" / "dws-auth.tar.gz"

    def _dws_auth_backup_state(self) -> dict[str, Any]:
        raw = self.store.get_service_state(DWS_AUTH_BACKUP_STATE_KEY)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _set_dws_auth_backup_state(self, state: dict[str, Any]) -> None:
        self.store.set_service_state(
            DWS_AUTH_BACKUP_STATE_KEY,
            json.dumps(state, ensure_ascii=False, sort_keys=True),
        )

    def _dws_auth_status_for_backup(self) -> dict[str, Any] | None:
        auth_status = getattr(self.dws, "auth_status", None)
        if not callable(auth_status):
            return None
        try:
            payload = auth_status()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _dws_auth_backup_status_fields(
        auth_status: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not auth_status:
            return {}
        return {
            key: value
            for key in DWS_AUTH_BACKUP_STATUS_FIELDS
            if isinstance((value := auth_status.get(key)), str | bool)
        }

    def _dws_auth_backup_due(
        self,
        state: dict[str, Any],
        archive_path: Path,
        auth_status: dict[str, Any] | None = None,
    ) -> bool:
        refresh_expires_at = self._dws_auth_backup_status_fields(auth_status).get(
            "refresh_expires_at"
        )
        if (
            isinstance(refresh_expires_at, str)
            and state.get("refresh_expires_at") != refresh_expires_at
        ):
            return True
        last_attempt_at = state.get("backed_up_at") or state.get("updated_at")
        if not isinstance(last_attempt_at, str):
            return True
        last_attempt = self._parse_service_state_datetime(last_attempt_at)
        if last_attempt is None:
            return True
        age = self._now().astimezone(timezone.utc) - last_attempt.astimezone(
            timezone.utc
        )
        if age < DWS_AUTH_BACKUP_INTERVAL:
            return False
        if not archive_path.exists():
            return True
        return True

    def _maybe_backup_dws_auth(self) -> None:
        archive_path = self._dws_auth_backup_path()
        state = self._dws_auth_backup_state()
        auth_status = self._dws_auth_status_for_backup()
        auth_status_fields = self._dws_auth_backup_status_fields(auth_status)
        if not self._dws_auth_backup_due(state, archive_path, auth_status):
            return
        now = self._now().astimezone(timezone.utc).isoformat()
        try:
            self.dws.export_auth_archive(archive_path)
            os.chmod(archive_path, 0o600)
        except Exception as exc:
            if self._is_dws_auth_export_unsupported(exc):
                self._set_dws_auth_backup_state(
                    {
                        **state,
                        "status": "unsupported",
                        "reason": "dws_keychain_export_unsupported",
                        "error": str(exc)[:240],
                        "updated_at": now,
                        "archive_path": str(archive_path),
                    }
                )
                return
            self.store.record_error(None, None, "dws_auth_backup", str(exc))
            self._set_dws_auth_backup_state(
                {
                    **state,
                    "status": "failed",
                    "error": str(exc)[:240],
                    "updated_at": now,
                    "archive_path": str(archive_path),
                }
            )
            return
        next_state = {
            "status": "backed_up",
            "backed_up_at": now,
            "archive_path": str(archive_path),
            **auth_status_fields,
        }
        if isinstance(state.get("restored_at"), str):
            next_state["restored_at"] = state["restored_at"]
        self._set_dws_auth_backup_state(next_state)

    def _is_dws_auth_export_unsupported(self, exc: Exception) -> bool:
        if not isinstance(exc, DwsError):
            return False
        if exc.code != "3":
            return False
        return "DWS_DISABLE_KEYCHAIN=1" in str(exc)

    def _restore_dws_auth_backup_and_retry(
        self,
        kind: str,
        call: Callable[[], T],
        *,
        conversation_id: str | None = None,
    ) -> T | None:
        archive_path = self._dws_auth_backup_path()
        state = self._dws_auth_backup_state()
        now = self._now().astimezone(timezone.utc).isoformat()
        if not archive_path.exists():
            self._set_dws_auth_backup_state(
                {
                    **state,
                    "status": "missing",
                    "updated_at": now,
                    "archive_path": str(archive_path),
                }
            )
            return None
        try:
            self.dws.import_auth_archive(archive_path)
            result = call()
        except Exception as exc:
            self.store.record_error(None, None, "dws_auth_restore", str(exc))
            self._set_dws_auth_backup_state(
                {
                    **state,
                    "status": "restore_failed",
                    "error": str(exc)[:240],
                    "updated_at": now,
                    "archive_path": str(archive_path),
                }
            )
            return None
        self._clear_dws_transient_error(kind)
        if conversation_id:
            self._clear_dws_read_forbidden(conversation_id)
        self._set_dws_auth_backup_state(
            {
                **state,
                "status": "restored",
                "restored_at": now,
                "archive_path": str(archive_path),
            }
        )
        self._mark_dws_auth_healthy()
        self._maybe_backup_dws_auth()
        return result

    def _monitor_dws_auth_login(self, state: dict[str, Any]) -> dict[str, Any]:
        process = self._dws_auth_login_process
        if process is None:
            if state.get("status") == "running" and not self._dws_auth_login_pid_alive(
                state
            ):
                state = {
                    **state,
                    "status": "stale",
                    "error": "dws auth login process is no longer running",
                    "updated_at": self._now().astimezone(timezone.utc).isoformat(),
                }
                self._set_dws_auth_login_state(state)
            return state
        exit_code = process.poll()
        if exit_code is None:
            if state.get("status") != "running":
                state = {
                    **state,
                    "status": "running",
                    "updated_at": self._now().astimezone(timezone.utc).isoformat(),
                }
                self._set_dws_auth_login_state(state)
            return state
        self._dws_auth_login_process = None
        status = "completed" if exit_code == 0 else "failed"
        state = {
            **state,
            "status": status,
            "exit_code": exit_code,
            "updated_at": self._now().astimezone(timezone.utc).isoformat(),
        }
        self._set_dws_auth_login_state(state)
        self._notify(
            title=f"CEO DWS auth login {status}",
            message=f"dws auth login exited with code {exit_code}",
        )
        return state

    @staticmethod
    def _dws_auth_login_pid_alive(state: dict[str, Any]) -> bool:
        pid = state.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _dws_auth_login_request_is_recent(self, state: dict[str, Any]) -> bool:
        if state.get("status") not in {"running", "stale", "failed"}:
            return False
        if not isinstance(state.get("pid"), int):
            return False
        started_at = state.get("started_at")
        if not isinstance(started_at, str):
            return False
        started = self._parse_service_state_datetime(started_at)
        if started is None:
            return False
        age = self._now().astimezone(timezone.utc) - started.astimezone(timezone.utc)
        return timedelta(0) <= age < DWS_AUTH_LOGIN_REQUEST_SUPPRESSION_WINDOW

    def _ensure_dws_auth_login(self, exc: Exception) -> bool:
        state = self._monitor_dws_auth_login(self._dws_auth_login_state())
        if state.get("status") == "running" or self._dws_auth_login_request_is_recent(
            state
        ):
            return True
        try:
            process = self.dws.start_auth_login()
        except Exception as start_exc:
            self.store.record_error(None, None, "dws_auth_login", str(start_exc))
            self._set_dws_auth_login_state(
                {
                    "status": "failed",
                    "reason": str(exc),
                    "error": str(start_exc),
                    "started_at": self._now().astimezone(timezone.utc).isoformat(),
                }
            )
            self._notify(
                title="CEO DWS auth login failed",
                message=str(start_exc)[:120],
            )
            return False
        self._dws_auth_login_process = process
        self._set_dws_auth_login_state(
            {
                "status": "running",
                "pid": process.pid,
                "reason": str(exc),
                "started_at": self._now().astimezone(timezone.utc).isoformat(),
            }
        )
        self._notify(
            title="CEO DWS auth login required",
            message="Started dws auth login. Please complete DingTalk login.",
        )
        return True

    def _mark_dws_auth_healthy(self) -> None:
        state = self._dws_auth_login_state()
        if state.get("status") == "authenticated":
            return
        self._set_dws_auth_login_state(
            {
                "status": "authenticated",
                "checked_at": self._now().astimezone(timezone.utc).isoformat(),
            }
        )

    def _skip_messages_outside_recent_window(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        remaining = []
        skipped = []
        cutoff = self._now() - RECENT_REPLY_WINDOW
        for message in messages:
            message_time = self._message_create_time_as_instant(message)
            if message_time >= cutoff:
                remaining.append(message)
                continue
            skipped.append(message)
            self._record_stale_message_skip(conversation, message)
        self._mark_seen(skipped)
        return remaining

    def _now(self) -> datetime:
        current = self.now_provider()
        if current.tzinfo is None:
            return current.astimezone()
        return current

    @staticmethod
    def _message_create_time_as_instant(message: DingTalkMessage) -> datetime:
        return datetime.strptime(message.create_time, DINGTALK_TIME_FORMAT).replace(
            tzinfo=DINGTALK_MESSAGE_TIME_ZONE
        )

    def consume_once(self, max_tasks: int | None = None) -> int:
        if max_tasks == 0:
            return 0
        limit = max_tasks if max_tasks is not None else 50
        processed_tasks = 0
        stale_tasks = self.store.list_stale_processing_reply_tasks(
            STALE_PROCESSING_TASK_SECONDS
        )
        reset_count = self.store.reset_stale_processing_reply_tasks(
            STALE_PROCESSING_TASK_SECONDS
        )
        if reset_count:
            for stale_task in stale_tasks:
                self.store.record_error(
                    stale_task.conversation_id,
                    stale_task.trigger_message_id,
                    "reply_task_stale",
                    (
                        "requeued stale processing task: "
                        f"task={stale_task.id} "
                        f"conversation={stale_task.conversation_title} "
                        f"message={stale_task.trigger_message_id} "
                        f"locked_at={stale_task.locked_at}"
                    ),
                )
            self._notify(
                title="CEO task retrying stale tasks",
                message=f"requeued {reset_count} stale task(s)",
            )
        for task in self.store.claim_reply_tasks(
            limit,
            now=self._sqlite_timestamp(self._now()),
        ):
            conversation = DingTalkConversation(
                open_conversation_id=task.conversation_id,
                title=task.conversation_title,
                single_chat=task.single_chat,
                unread_point=1,
            )
            try:
                should_complete_task = self._process_queued_task(conversation, task)
            except CriticalInformationUnavailableError as exc:
                error = str(exc)
                self.store.fail_reply_task(task.id, error)
                self.store.record_error(
                    task.conversation_id,
                    task.trigger_message_id,
                    "reply_task_critical_info_unavailable",
                    error,
                )
                self._notify(
                    title=f"CEO task failed: {task.conversation_title}",
                    message=error[:120],
                    conversation=conversation,
                )
                continue
            except Exception as exc:
                error = str(exc)
                if self._is_authorization_error(exc):
                    self.store.defer_reply_task_for_authorization(task.id, error)
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "reply_task_authorization",
                        error,
                    )
                    self._notify(
                        title=f"CEO task waiting for authorization: {task.conversation_title}",
                        message=error[:120],
                        conversation=conversation,
                    )
                    continue
                if task.attempts < self.max_task_attempts:
                    self.store.requeue_reply_task(
                        task.id,
                        error,
                        available_at=self._reply_task_retry_available_at(
                            task.attempts
                        ),
                    )
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "reply_task_retry",
                        error,
                    )
                    continue
                self.store.fail_reply_task(task.id, error)
                self.store.record_error(
                    task.conversation_id,
                    task.trigger_message_id,
                    "reply_task",
                    error,
                )
                self._notify(
                    title=f"CEO task failed: {task.conversation_title}",
                    message=error[:120],
                    conversation=conversation,
                )
                continue
            if should_complete_task:
                if not self.store.reply_task_is_done(task.id):
                    self.store.complete_reply_task(task.id)
                self._complete_superseded_reply_tasks(conversation, task)
                processed_tasks += 1
            else:
                self.store.defer_reply_task(task.id, "dry_run")
        return processed_tasks

    def _reply_task_retry_available_at(self, attempts: int) -> str:
        delay_seconds = min(
            REPLY_TASK_RETRY_BASE_DELAY_SECONDS * (2 ** max(attempts - 1, 0)),
            REPLY_TASK_RETRY_MAX_DELAY_SECONDS,
        )
        return self._sqlite_timestamp(
            self._now().astimezone(timezone.utc) + timedelta(seconds=delay_seconds)
        )

    def _complete_superseded_reply_tasks(
        self,
        conversation: DingTalkConversation,
        task: ReplyTask,
    ) -> None:
        completed_by_id: dict[int, ReplyTask] = {}
        if conversation.single_chat:
            for completed_task in self.store.complete_unfinished_reply_tasks_before_trigger(
                conversation_id=task.conversation_id,
                trigger_create_time=task.trigger_create_time,
                exclude_task_id=task.id,
            ):
                completed_by_id[completed_task.id] = completed_task
        for completed_task in self.store.complete_unfinished_reply_tasks_for_messages(
            conversation_id=task.conversation_id,
            trigger_message_ids=self._coalesced_trigger_message_ids(task),
            exclude_task_id=task.id,
        ):
            completed_by_id[completed_task.id] = completed_task
        for completed_task in completed_by_id.values():
            self.store.record_error(
                completed_task.conversation_id,
                completed_task.trigger_message_id,
                "reply_task_superseded",
                (
                    "completed superseded reply task: "
                    f"task={completed_task.id} "
                    f"new_task={task.id} "
                    f"new_message={task.trigger_message_id}"
                ),
            )

    def _coalesced_trigger_message_ids(self, task: ReplyTask) -> list[str]:
        payload = json.loads(task.trigger_message_json or "{}")
        raw_payload = payload.get("raw_payload")
        if not isinstance(raw_payload, dict):
            return []
        coalesced = raw_payload.get("coalesced_message_ids")
        if not isinstance(coalesced, list):
            return []
        message_ids = []
        for message_id in coalesced:
            if isinstance(message_id, str) and message_id != task.trigger_message_id:
                message_ids.append(message_id)
        return message_ids

    @staticmethod
    def _is_authorization_error(exc: Exception) -> bool:
        if getattr(exc, "needs_authorization", False):
            return True
        cause = exc.__cause__
        while cause is not None:
            if getattr(cause, "needs_authorization", False):
                return True
            cause = cause.__cause__
        return False

    def _process_queued_task(
        self, conversation: DingTalkConversation, task: ReplyTask
    ) -> bool:
        trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
        if not self._queued_trigger_is_still_actionable(conversation, trigger):
            self._record_trigger_recalled_after_backoff_skip(conversation, trigger)
            self._mark_seen([trigger])
            return True
        context_messages, prompt_context_messages = (
            self._queued_task_prompt_context_messages(conversation, trigger)
        )
        if (
            task.error == FAST_PATH_UNREAD_BACKOFF_TASK_ERROR
            and self._has_current_user_reply_after_trigger(context_messages, trigger)
        ):
            self._record_current_user_replied_during_backoff_skip(
                conversation,
                trigger,
            )
            self._mark_seen([trigger])
            return True
        if self._handle_minutes_permission_request_if_actionable(
            conversation,
            trigger,
            raise_on_delivery_failure=True,
        ):
            return True
        if self._handle_calendar_invite_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
            raise_on_delivery_failure=True,
            complete_task_id=task.id,
        ):
            return True
        if self._handle_oa_approval_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
        ):
            return not self.dry_run
        if self._is_system_or_notification_message(trigger):
            self._record_system_or_notification_skip(conversation, trigger)
            self._mark_seen([trigger])
            return True
        self._process_batch(
            conversation,
            [trigger],
            prompt_context_messages,
            ignore_existing_attempt=self._should_regenerate_after_processing_failure(
                conversation,
                trigger,
                task,
            ),
            raise_on_delivery_failure=True,
        )
        return True

    def _should_regenerate_after_processing_failure(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        task: ReplyTask,
    ) -> bool:
        if task.attempts <= 1:
            return False
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            trigger.open_message_id,
        )
        if attempt is None:
            return False
        return (
            attempt.send_status == "failed"
            and attempt.action == CodexAction.STOP_WITH_ERROR.value
            and not attempt.final_reply_text.strip()
        )

    def _queued_trigger_is_still_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> bool:
        list_messages_by_ids = getattr(self.dws, "list_messages_by_ids", None)
        if list_messages_by_ids is None:
            return True
        current_messages = self._call_dws(
            "list_messages_by_ids",
            lambda: list_messages_by_ids([trigger.open_message_id]),
            conversation_id=conversation.open_conversation_id,
            message_id=trigger.open_message_id,
            raise_authorization=True,
            default=None,
        )
        if current_messages is None:
            return True
        if not current_messages:
            return False
        return not any(message.is_recalled() for message in current_messages)

    def _queued_task_prompt_context_messages(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> tuple[list[DingTalkMessage], list[DingTalkMessage]]:
        context_messages: list[DingTalkMessage] = []
        unread_messages: list[DingTalkMessage] = []
        context_messages = self._read_conversation_messages(
            "read_recent_messages_fallback",
            conversation,
            lambda: self.dws.read_recent_messages(conversation),
            message_id=trigger.open_message_id,
            raise_authorization=True,
            default=[],
        )
        unread_messages = self._read_conversation_messages(
            "read_unread_messages_fallback",
            conversation,
            lambda: self.dws.read_unread_messages(conversation),
            message_id=trigger.open_message_id,
            raise_authorization=True,
            default=[],
        )
        return context_messages, self._prompt_context_messages(
            context_messages,
            unread_messages,
        )

    def _enqueue_reply_task(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        *,
        context_messages: list[DingTalkMessage] | None = None,
        available_at: str = "",
        error: str = "",
        replace_pending_single_chat: bool = True,
    ) -> bool:
        if self._is_calendar_message(trigger):
            full_context_messages = self._read_conversation_messages(
                "read_recent_messages_calendar_context",
                conversation,
                lambda: self.dws.read_recent_messages(conversation),
                message_id=trigger.open_message_id,
                default=[],
            )
            if full_context_messages:
                context_messages = full_context_messages
        trigger = self._calendar_card_message_with_available_details(
            conversation,
            trigger,
            context_messages,
        )
        if self._should_suppress_burst_calendar_reply(conversation, trigger):
            self.store.record_reply_attempt(
                conversation_id=conversation.open_conversation_id,
                conversation_title=conversation.title,
                trigger_message_id=trigger.open_message_id,
                trigger_sender=trigger.sender_name,
                trigger_text=trigger.content,
                action=CodexAction.NO_REPLY.value,
                sensitivity_kind="calendar",
                codex_reason=(
                    "Suppressed a same-topic calendar card because a recent "
                    "calendar reply was already sent in this single chat."
                ),
                send_status="skipped",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_burst_reply_suppressed",
                (
                    "suppressed same-topic calendar reply after recent sent "
                    f"calendar reply: message={trigger.open_message_id}"
                ),
            )
            self._mark_seen([trigger])
            return False
        if conversation.single_chat and replace_pending_single_chat:
            updated = self.store.replace_pending_single_chat_reply_task_trigger(
                conversation_id=conversation.open_conversation_id,
                trigger_message_id=trigger.open_message_id,
                trigger_create_time=trigger.create_time,
                trigger_sender=trigger.sender_name,
                trigger_text=trigger.content,
                trigger_message_json=trigger.model_dump_json(),
                available_at=available_at,
                error=error,
            )
            if updated:
                return True
        inserted = self.store.enqueue_reply_task(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            trigger_message_id=trigger.open_message_id,
            trigger_create_time=trigger.create_time,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
            available_at=available_at,
            error=error,
        )
        if inserted:
            return True
        updated = self.store.update_pending_reply_task_trigger_for_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
        )
        return updated > 0

    def _should_suppress_burst_calendar_reply(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> bool:
        if not conversation.single_chat or not self._is_calendar_message(trigger):
            return False
        trigger_topic = self._calendar_reply_topic(trigger)
        if not trigger_topic:
            return False
        trigger_time = self._message_create_time_as_instant(trigger)
        trigger_sender = trigger.sender_name.strip()
        for attempt in self.store.list_reply_attempts_for_conversation(
            conversation.open_conversation_id,
            limit=20,
        ):
            if attempt.trigger_message_id == trigger.open_message_id:
                continue
            if attempt.action != CodexAction.SEND_REPLY.value:
                continue
            if attempt.send_status != "sent":
                continue
            if not self._is_calendar_text(attempt.trigger_text):
                continue
            if self._calendar_reply_topic_text(attempt.trigger_text) != trigger_topic:
                continue
            if trigger_sender and attempt.trigger_sender.strip() != trigger_sender:
                continue
            attempt_time = self._parse_service_state_datetime(
                attempt.updated_at or attempt.created_at
            )
            if attempt_time is None:
                continue
            delta = abs(trigger_time - attempt_time.astimezone(trigger_time.tzinfo))
            if delta <= CALENDAR_BURST_REPLY_SUPPRESSION_WINDOW:
                return True
        return False

    @staticmethod
    def _calendar_reply_topic(message: DingTalkMessage) -> str:
        return DingTalkAutoReplyWorker._calendar_reply_topic_text(message.content)

    @staticmethod
    def _calendar_reply_topic_text(text: str) -> str:
        topic = DingTalkAutoReplyWorker._strip_calendar_prefix(text).strip()
        topic = re.sub(r"^[【\[].*?[】\]]\s*", "", topic)
        topic = re.split(r"\s[-—–:：]\s|[-—–]", topic, maxsplit=1)[0]
        return re.sub(r"\s+", "", topic).strip().lower()

    @staticmethod
    def _strip_calendar_prefix(text: str) -> str:
        return re.sub(r"^\s*[\[［【]\s*日程\s*[\]］】]\s*", "", text, count=1)

    @staticmethod
    def _is_calendar_text(text: str) -> bool:
        return bool(
            re.match(r"^\s*[\[［【]\s*日程\s*[\]］】]", text.strip())
        )

    def _reply_task_trigger_messages(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
        *,
        source_messages: list[DingTalkMessage] | None = None,
    ) -> list[DingTalkMessage]:
        if not messages:
            return []
        if conversation.single_chat:
            if source_messages is not None:
                return self._latest_candidate_messages_preserving_context_boundaries(
                    messages,
                    source_messages,
                )
            return [self._latest_trigger_message(messages)]
        return DingTalkAutoReplyWorker._group_chat_trigger_messages(messages)

    def _latest_candidate_messages_preserving_context_boundaries(
        self,
        messages: list[DingTalkMessage],
        source_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        candidate_by_id = {message.open_message_id: message for message in messages}
        groups: list[list[DingTalkMessage]] = []
        current_group: list[DingTalkMessage] = []
        current_sender_key = ""

        def flush_group() -> None:
            nonlocal current_group, current_sender_key
            if current_group:
                groups.append(current_group)
            current_group = []
            current_sender_key = ""

        for source_message in sorted(
            source_messages,
            key=lambda message: message.create_time,
        ):
            candidate = candidate_by_id.get(source_message.open_message_id)
            if candidate is None:
                flush_group()
                continue
            sender_key = self._message_sender_key(candidate)
            if current_group and sender_key == current_sender_key:
                current_group.append(candidate)
            else:
                flush_group()
                current_group.append(candidate)
                current_sender_key = sender_key
        flush_group()
        return [
            DingTalkAutoReplyWorker._latest_trigger_message(group)
            for group in groups
        ]

    @staticmethod
    def _group_chat_trigger_messages(
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        groups: list[list[DingTalkMessage]] = []
        thread_group_by_key: dict[str, list[DingTalkMessage]] = {}
        for message in sorted(
            messages,
            key=DingTalkAutoReplyWorker._message_create_time_as_instant,
        ):
            thread_key = DingTalkAutoReplyWorker._message_thread_key(message)
            if thread_key:
                group = thread_group_by_key.get(thread_key)
                if group is None:
                    group = []
                    groups.append(group)
                    thread_group_by_key[thread_key] = group
                group.append(message)
                continue
            sender_key = DingTalkAutoReplyWorker._message_sender_key(message)
            if (
                groups
                and not DingTalkAutoReplyWorker._message_thread_key(groups[-1][-1])
                and DingTalkAutoReplyWorker._message_sender_key(groups[-1][-1])
                == sender_key
            ):
                groups[-1].append(message)
            else:
                groups.append([message])
        triggers = [
            DingTalkAutoReplyWorker._latest_trigger_message(group)
            for group in groups
        ]
        return sorted(
            triggers,
            key=DingTalkAutoReplyWorker._message_create_time_as_instant,
        )

    @staticmethod
    def _message_thread_key(message: DingTalkMessage) -> str:
        return message.quoted_message_id or ""

    @staticmethod
    def _message_sender_key(message: DingTalkMessage) -> str:
        return (
            message.sender_user_id
            or message.sender_open_dingtalk_id
            or message.sender_name
        )

    @staticmethod
    def _latest_trigger_message(messages: list[DingTalkMessage]) -> DingTalkMessage:
        latest = max(
            messages,
            key=DingTalkAutoReplyWorker._message_create_time_as_instant,
        )
        if len(messages) == 1:
            return latest
        ordered_messages = sorted(
            messages,
            key=DingTalkAutoReplyWorker._message_create_time_as_instant,
        )
        raw_payload = dict(latest.raw_payload)
        raw_payload["coalesced_message_ids"] = [
            message.open_message_id
            for message in ordered_messages
        ]
        raw_payload["coalesced_messages"] = [
            {
                "open_message_id": message.open_message_id,
                "create_time": message.create_time,
                "sender_name": message.sender_name,
                "content": message.content,
            }
            for message in ordered_messages
        ]
        return latest.model_copy(update={"raw_payload": raw_payload})

    def _mentioned_messages_by_conversation(
        self, conversations: list[DingTalkConversation]
    ) -> dict[str, list[DingTalkMessage]]:
        del conversations
        messages = self._call_dws(
            "read_mentioned_messages",
            lambda: self.dws.read_mentioned_messages(limit=100),
            notify_title="CEO read mentioned messages failed",
            default=[],
        )
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
            grouped.setdefault(message.open_conversation_id, []).append(message)
        return grouped

    def _broadcast_messages_by_conversation(self) -> dict[str, list[DingTalkMessage]]:
        messages = self._call_dws(
            "read_broadcast_messages",
            lambda: self.dws.read_broadcast_messages(
                broadcast_mention_aliases(),
                limit=100,
                lookback_hours=24,
            ),
            notify_title="CEO read broadcast messages failed",
            default=[],
        )
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
            if self._is_current_user_message_for_candidate_filter(message):
                continue
            grouped.setdefault(message.open_conversation_id, []).append(message)
        return grouped

    @staticmethod
    def _merge_message_groups(
        *groups: dict[str, list[DingTalkMessage]],
    ) -> dict[str, list[DingTalkMessage]]:
        result: dict[str, list[DingTalkMessage]] = {}
        seen_message_ids: set[str] = set()
        for group in groups:
            for conversation_id, messages in group.items():
                for message in messages:
                    if message.open_message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message.open_message_id)
                    result.setdefault(conversation_id, []).append(message)
        return result

    @staticmethod
    def _conversations_with_mentions(
        conversations: list[DingTalkConversation],
        mentioned_messages: dict[str, list[DingTalkMessage]],
    ) -> list[DingTalkConversation]:
        result = list(conversations)
        known_conversation_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        for conversation_id, messages in sorted(mentioned_messages.items()):
            if conversation_id in known_conversation_ids or not messages:
                continue
            latest_message = max(messages, key=lambda message: message.create_time)
            result.append(
                DingTalkConversation(
                    open_conversation_id=conversation_id,
                    title=latest_message.conversation_title or conversation_id,
                    single_chat=latest_message.single_chat,
                    unread_point=0,
                    last_message_create_at=None,
                )
            )
        return result

    def rerun_message(
        self,
        conversation: DingTalkConversation,
        message_id: str,
        *,
        force_new_decision: bool = False,
        oa_url: str = "",
    ) -> str:
        context_messages = self._read_conversation_messages(
            "read_recent_messages_rerun",
            conversation,
            lambda: self.dws.read_recent_messages(conversation),
            default=[],
        )
        unread_messages = self._read_conversation_messages(
            "read_unread_messages_rerun",
            conversation,
            lambda: self.dws.read_unread_messages(conversation),
            default=[],
        )
        prompt_context_messages = self._prompt_context_messages(
            context_messages, unread_messages
        )
        candidates = [
            message
            for message in prompt_context_messages
            if message.open_message_id == message_id
        ]
        trigger = candidates[-1] if candidates else self._lookup_rerun_message_by_id(
            conversation,
            message_id,
        )
        if trigger is None:
            raise ValueError(
                f"message not found in recent DingTalk context: {message_id}"
            )
        if trigger.is_recalled():
            self._record_trigger_recalled_after_backoff_skip(conversation, trigger)
            self._mark_seen([trigger])
            return trigger.open_message_id
        if self._handle_minutes_permission_request_if_actionable(
            conversation,
            trigger,
            ignore_existing_attempt=force_new_decision,
        ):
            return trigger.open_message_id
        if self._handle_calendar_invite_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
            ignore_existing_attempt=force_new_decision,
            include_resolved_calendar_invites=force_new_decision,
            allow_duplicate_send=force_new_decision,
        ):
            return trigger.open_message_id
        if self._handle_oa_approval_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
            ignore_existing_attempt=force_new_decision,
            oa_url_override=oa_url,
        ):
            return trigger.open_message_id
        if self._is_system_or_notification_message(trigger):
            self._record_system_or_notification_skip(conversation, trigger)
            self._mark_seen([trigger])
            return trigger.open_message_id
        self._process_batch(
            conversation,
            [trigger],
            prompt_context_messages,
            ignore_existing_attempt=force_new_decision,
            allow_duplicate_send=force_new_decision,
        )
        return trigger.open_message_id

    def _lookup_rerun_message_by_id(
        self,
        conversation: DingTalkConversation,
        message_id: str,
    ) -> DingTalkMessage | None:
        list_messages_by_ids = getattr(self.dws, "list_messages_by_ids", None)
        if list_messages_by_ids is None:
            return None
        messages = self._call_dws(
            "list_messages_by_ids_rerun",
            lambda: list_messages_by_ids([message_id]),
            conversation_id=conversation.open_conversation_id,
            message_id=message_id,
            raise_authorization=True,
            default=[],
        )
        for message in messages:
            if message.open_message_id != message_id:
                continue
            if (
                message.open_conversation_id
                and message.open_conversation_id != conversation.open_conversation_id
            ):
                continue
            return message.model_copy(
                update={
                    "open_conversation_id": conversation.open_conversation_id,
                    "conversation_title": message.conversation_title
                    or conversation.title,
                    "single_chat": conversation.single_chat,
                }
            )
        return None

    def _skip_system_or_notification_messages(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        remaining = []
        skipped = []
        for message in messages:
            if self._is_system_or_notification_message(message):
                if self._minutes_permission_request(message) is not None:
                    remaining.append(message)
                    continue
                if self._is_calendar_message(message):
                    remaining.append(message)
                    continue
                try:
                    calendar_context = self._calendar_invite_context(
                        conversation, message
                    )
                except Exception as exc:
                    self.store.record_error(
                        conversation.open_conversation_id,
                        message.open_message_id,
                        "calendar_conflict_check",
                        str(exc),
                    )
                    remaining.append(message)
                    continue
                if calendar_context is not None:
                    remaining.append(message)
                else:
                    skipped.append(message)
                    self._record_system_or_notification_skip(conversation, message)
            else:
                remaining.append(message)
        self._mark_seen(skipped)
        return remaining

    def _handle_minutes_permission_request_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        *,
        ignore_existing_attempt: bool = False,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        request = self._minutes_permission_request(trigger)
        if request is None:
            return False
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            [trigger],
            ignore_system_notification_skip=True,
        ):
            return True
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action=CodexAction.NO_REPLY.value,
            sensitivity_kind="general",
            codex_reason="ai_minutes_permission_auto_approved",
            audit_summary="已自动通过 AI 听记权限申请，无需聊天回复。",
        )
        if self.dry_run:
            self.store.update_reply_attempt(attempt_id, send_status="dry_run")
            return True
        try:
            self.dws.add_minutes_member_permission(request)
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "ai_minutes_permission",
                str(exc),
            )
            self._notify(
                title=f"CEO AI minutes permission failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return True
        self.store.update_reply_attempt(
            attempt_id,
            send_status="skipped",
            send_error="no_reply",
        )
        self._mark_seen([trigger])
        return True

    def _minutes_permission_request(self, message: DingTalkMessage):
        minutes_permission_request_from_message = getattr(
            self.dws,
            "minutes_permission_request_from_message",
            None,
        )
        if minutes_permission_request_from_message is None:
            return None
        return minutes_permission_request_from_message(message)

    @staticmethod
    def _queue_okr_review_actions(decision: CodexDecision) -> list[dict]:
        return [
            action
            for action in decision.system_actions
            if isinstance(action, dict) and action.get("type") == "queue_okr_review"
        ]

    def _queue_okr_review_from_decision(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        *,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        period = current_quarter_period()
        if not hasattr(self, "okr_live_source"):
            error = "OKR live source is not configured"
            self.store.update_reply_attempt(
                attempt_id,
                action="okr_review",
                send_status="failed",
                send_error=error,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "okr_review_source",
                error,
            )
            raise RuntimeError(error)
        try:
            okr_payload = self.okr_live_source.fetch_user_okr(
                user_id=trigger.sender_user_id
                or self.dws.resolve_message_sender(trigger),
                period_label=period.period_label,
            )
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                action="okr_review",
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "okr_review_source",
                str(exc),
            )
            raise
        request_id = self.store.create_okr_review_request(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_sender_user_id=trigger.sender_user_id or "",
            trigger_text=trigger.content,
            period_label=period.period_label,
            period_start=period.period_start,
            period_end=period.period_end,
            okr_source_json=json.dumps(okr_payload, ensure_ascii=False),
        )
        reply_text = (
            f"已受理 {period.period_label} OKR 审核请求，"
            "正在实时核实 KR 进度和证据。"
        )
        self.store.update_reply_attempt(
            attempt_id,
            action="okr_review",
            send_status="dry_run" if self.dry_run else "pending",
            final_reply_text=reply_text,
        )
        if not self.dry_run:
            delivered = self._deliver_trigger_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                attempt_id=attempt_id,
                reply_text=reply_text,
                feedback_token="",
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            if not delivered and raise_on_delivery_failure:
                raise ReplyDeliveryError("OKR review acknowledgement delivery failed")
        else:
            self._mark_seen([trigger])
        return True

    def _handle_oa_approval_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
        *,
        ignore_existing_attempt: bool = False,
        oa_url_override: str = "",
    ) -> bool:
        if self.oa_approval_handler is None:
            return False
        if not self._is_oa_approval_message(trigger):
            return False
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            [trigger],
            ignore_system_notification_skip=True,
        ):
            return True
        oa_url = oa_url_override.strip() or extract_oa_url(trigger.content)
        approval_detail_text = self._oa_approval_detail_text(trigger, oa_url)
        result = self.oa_approval_handler.handle(
            trigger_text=trigger.content,
            context_text=self._oa_approval_context_text(context_messages),
            oa_url=oa_url,
            approval_detail_text=approval_detail_text,
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            execute=False,
        )
        url_process_instance_id = self._oa_process_instance_id_from_url(oa_url)
        url_task_id = self._oa_task_id_from_url(oa_url)
        effective_oa_process_instance_id = (
            result.process_instance_id.strip() or url_process_instance_id
        )
        effective_oa_task_id = result.task_id.strip() or url_task_id
        effective_oa_url = result.oa_url.strip() or oa_url
        target_status = self._oa_target_status_for_current_user(
            approval_detail_text,
            effective_oa_task_id,
        )
        target_error = ""
        if target_status is False:
            effective_oa_task_id = ""
            target_error = "oa_task_not_current_user"
        action_result = {}
        send_status = "dry_run"
        send_error = ""
        if not self.dry_run:
            has_approval_target = bool(
                effective_oa_process_instance_id.strip()
                and effective_oa_task_id.strip()
            )
            if has_approval_target:
                if result.oa_action == "退回":
                    try:
                        action_result = self.dws.comment_oa_approval(
                            effective_oa_process_instance_id,
                            result.oa_remark,
                        )
                        send_status = "commented"
                    except Exception as exc:
                        send_status = "failed"
                        send_error = str(exc)
                else:
                    try:
                        action_result = self.dws.execute_oa_approval_action(
                            effective_oa_process_instance_id,
                            effective_oa_task_id,
                            result.oa_action,
                            result.oa_remark,
                        )
                        send_status = "skipped"
                    except Exception as exc:
                        send_status = "failed"
                        send_error = str(exc)
            else:
                send_status = "skipped"
                send_error = target_error or "missing_oa_approval_target"
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action="oa_approval",
            sensitivity_kind="internal_personnel",
            codex_reason=result.oa_action,
            draft_reply_text=result.oa_remark,
            codex_session_id=getattr(self.oa_approval_handler, "last_session_id", "")
            or "",
            codex_transcript_start_line=getattr(
                self.oa_approval_handler, "last_transcript_start_line", 0
            ),
            codex_transcript_end_line=getattr(
                self.oa_approval_handler, "last_transcript_end_line", 0
            ),
            audit_documents_json=json.dumps(
                result.audit_documents,
                ensure_ascii=False,
            ),
            audit_tool_events_json=json.dumps(
                getattr(self.oa_approval_handler, "last_audit_tool_events", []),
                ensure_ascii=False,
            ),
            audit_summary=result.audit_summary,
            oa_process_instance_id=effective_oa_process_instance_id,
            oa_task_id=effective_oa_task_id,
            oa_url=effective_oa_url,
            oa_action=result.oa_action,
            oa_remark=result.oa_remark,
            oa_action_result_json=json.dumps(
                action_result,
                ensure_ascii=False,
            ),
            send_status=send_status,
        )
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=result.oa_remark,
            send_error=send_error or target_error,
        )
        if send_error and send_error not in {
            "missing_oa_approval_target",
            "oa_task_not_current_user",
        }:
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "oa_approval_action",
                send_error,
            )
            self._notify(
                title=f"CEO OA approval action failed: {conversation.title}",
                message=send_error[:120],
                conversation=conversation,
            )
            raise ReplyDeliveryError(send_error)
        self._mark_seen([trigger])
        return True

    @staticmethod
    def _oa_target_status_for_current_user(
        approval_detail_text: str,
        task_id: str,
    ) -> bool | None:
        if not task_id:
            return None
        try:
            documents = json.loads(approval_detail_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(documents, dict):
            return None
        current_user_id = str(documents.get("current_user_id") or "")
        if not current_user_id:
            return None
        tasks = DingTalkAutoReplyWorker._oa_detail_tasks(documents)
        if not tasks:
            return None
        for task in tasks:
            candidate_task_id = str(
                task.get("taskid")
                or task.get("taskId")
                or task.get("task_id")
                or task.get("id")
                or ""
            )
            if candidate_task_id != task_id:
                continue
            status = str(
                task.get("task_status")
                or task.get("taskStatus")
                or task.get("status")
                or ""
            ).upper()
            user_id = str(
                task.get("userid")
                or task.get("userId")
                or task.get("user_id")
                or ""
            )
            return status == "RUNNING" and user_id == current_user_id
        return False

    @staticmethod
    def _oa_detail_tasks(documents: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        openapi_process = documents.get("openapi_detail")
        if isinstance(openapi_process, dict):
            process = openapi_process.get("process_instance")
            if isinstance(process, dict):
                openapi_tasks = process.get("tasks")
                if isinstance(openapi_tasks, list):
                    tasks.extend(
                        task for task in openapi_tasks if isinstance(task, dict)
                    )
        dws_tasks = documents.get("dws_tasks")
        if isinstance(dws_tasks, dict):
            result = dws_tasks.get("result")
            if isinstance(result, dict):
                for key in ("tasks", "taskList", "taskIdList"):
                    values = result.get(key)
                    if isinstance(values, list):
                        tasks.extend(task for task in values if isinstance(task, dict))
        return tasks

    @staticmethod
    def _oa_approval_context_text(messages: list[DingTalkMessage]) -> str:
        lines = []
        for message in messages:
            lines.append(
                f"{message.create_time} {message.sender_name}: {message.content}"
            )
        return "\n".join(lines)

    def _oa_approval_detail_text(self, trigger: DingTalkMessage, oa_url: str) -> str:
        process_instance_id = self._oa_process_instance_id_from_url(oa_url)
        if not process_instance_id:
            process_instance_id = self._find_pending_oa_process_instance_id(trigger)
        if not process_instance_id:
            return "未能从消息或待办列表定位审批实例。"
        documents: dict[str, Any] = {"process_instance_id": process_instance_id}
        try:
            documents["current_user_id"] = self.dws.get_current_user_id()
        except Exception as exc:
            documents["current_user_id_error"] = self._dws_tool_error_payload(exc)
        for key, reader in (
            ("dws_detail", self.dws.read_oa_approval_detail),
            ("dws_records", self.dws.read_oa_approval_records),
            ("dws_tasks", self.dws.read_oa_approval_tasks),
        ):
            try:
                documents[key] = reader(process_instance_id)
            except Exception as exc:
                documents[key] = self._dws_tool_error_payload(exc)
        try:
            documents["openapi_detail"] = self.dws.read_oa_process_instance_openapi(
                process_instance_id
            )
        except Exception as exc:
            if self._oa_detail_needs_openapi(documents.get("dws_detail")):
                documents["openapi_detail"] = self._dws_tool_error_payload(exc)
        self._append_oa_attachment_fallbacks(documents)
        self._annotate_oa_detail_recovery(documents)
        self._annotate_oa_tool_status(documents)
        return json.dumps(documents, ensure_ascii=False)

    def _append_oa_attachment_fallbacks(self, documents: dict[str, Any]) -> None:
        openapi_detail = documents.get("openapi_detail")
        if not isinstance(openapi_detail, dict):
            return
        process = openapi_detail.get("process_instance")
        if not isinstance(process, dict):
            return
        attachments = self._oa_attachment_records(
            process.get("form_component_values")
        )
        if not attachments:
            return
        process_instance_id = str(documents.get("process_instance_id") or "")
        documents["oa_attachment_fallbacks"] = [
            self._oa_attachment_fallback(process_instance_id, attachment)
            for attachment in attachments[:12]
        ]

    def _oa_attachment_fallback(
        self,
        process_instance_id: str,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        file_name = str(attachment.get("fileName") or attachment.get("file_name") or "")
        file_id = str(attachment.get("fileId") or attachment.get("file_id") or "")
        fallback: dict[str, Any] = {
            "file_name": file_name,
            "file_id": file_id,
            "space_id": str(
                attachment.get("spaceId") or attachment.get("space_id") or ""
            ),
            "file_type": str(
                attachment.get("fileType") or attachment.get("file_type") or ""
            ),
        }
        if process_instance_id and file_id:
            try:
                data = self.dws.download_oa_process_attachment(
                    process_instance_id,
                    file_id,
                )
                fallback["downloaded_attachment"] = {
                    "bytes": len(data),
                    "text": self._oa_attachment_text(file_name, data)[:12000],
                }
            except Exception as exc:
                fallback["download_error"] = self._dws_tool_error_payload(exc)
        query = self._oa_attachment_search_query(file_name)
        fallback["search_query"] = query
        if not query:
            fallback["search_error"] = "missing attachment file name"
            return fallback
        try:
            matches = self.dws.search_documents(query, page_size=5)
        except Exception as exc:
            fallback["search_error"] = self._dws_tool_error_payload(exc)
            return fallback
        fallback["matches"] = [
            {
                "node_id": match.node_id,
                "name": match.name,
                "extension": match.extension,
                "content_type": match.content_type,
                "doc_url": match.doc_url,
            }
            for match in matches[:5]
        ]
        readable = next(
            (
                match
                for match in matches
                if match.extension.lower() == "adoc"
                or match.content_type.upper() == "ALIDOC"
            ),
            None,
        )
        if readable is None:
            return fallback
        try:
            content = self.dws.read_doc(readable.node_id)
        except Exception as exc:
            fallback["read_error"] = self._dws_tool_error_payload(exc)
            return fallback
        fallback["read_document"] = {
            "node_id": readable.node_id,
            "name": readable.name,
            "markdown": str(content.get("markdown") or content.get("content") or "")[
                :12000
            ],
        }
        return fallback

    @classmethod
    def _oa_attachment_text(cls, file_name: str, data: bytes) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".docx":
            return cls._docx_text(data)
        if suffix == ".xlsx":
            return cls._xlsx_text(data)
        if suffix == ".pdf":
            return cls._pdf_text(data)
        return ""

    @staticmethod
    def _docx_text(data: bytes) -> str:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
        root = ET.fromstring(document_xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespace):
            text = "".join(
                node.text or "" for node in paragraph.findall(".//w:t", namespace)
            ).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)

    @staticmethod
    def _xlsx_text(data: bytes) -> str:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.itertext())
                    for node in root.findall(
                        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
                    )
                ]
            sheet_names = [
                name
                for name in archive.namelist()
                if re.match(r"xl/worksheets/sheet\d+\.xml$", name)
            ]
            rows = []
            namespace = {
                "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
            }
            for sheet_name in sheet_names[:5]:
                root = ET.fromstring(archive.read(sheet_name))
                for row in root.findall(".//x:row", namespace)[:120]:
                    values = []
                    for cell in row.findall("x:c", namespace):
                        value_node = cell.find("x:v", namespace)
                        inline_node = cell.find("x:is/x:t", namespace)
                        if inline_node is not None and inline_node.text:
                            values.append(inline_node.text)
                            continue
                        if value_node is None or value_node.text is None:
                            continue
                        value = value_node.text
                        if cell.get("t") == "s" and value.isdigit():
                            index = int(value)
                            if 0 <= index < len(shared_strings):
                                value = shared_strings[index]
                        values.append(value)
                    if values:
                        rows.append("\t".join(values))
        return "\n".join(rows)

    @staticmethod
    def _pdf_text(data: bytes) -> str:
        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages[:20]:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text.strip())
        return "\n\n".join(pages)

    @staticmethod
    def _oa_attachment_search_query(file_name: str) -> str:
        stem = Path(file_name).stem.strip()
        stem = re.sub(r"(?:\([^)]*\))+$", "", stem).strip()
        return stem or file_name.strip()

    @classmethod
    def _oa_attachment_records(cls, value: Any) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        cls._collect_oa_attachment_records(value, attachments)
        deduped: list[dict[str, Any]] = []
        seen = set()
        for attachment in attachments:
            file_name = str(
                attachment.get("fileName") or attachment.get("file_name") or ""
            )
            file_id = str(attachment.get("fileId") or attachment.get("file_id") or "")
            key = (file_id, file_name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(attachment)
        return deduped

    @classmethod
    def _collect_oa_attachment_records(
        cls,
        value: Any,
        attachments: list[dict[str, Any]],
    ) -> None:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return
            cls._collect_oa_attachment_records(parsed, attachments)
            return
        if isinstance(value, list):
            for item in value:
                cls._collect_oa_attachment_records(item, attachments)
            return
        if not isinstance(value, dict):
            return
        if value.get("fileName") or value.get("fileId"):
            attachments.append(value)
        component_type = str(
            value.get("componentType") or value.get("component_type") or ""
        )
        if component_type == "DDAttachment":
            cls._collect_oa_attachment_records(value.get("value"), attachments)
        for nested_key in ("value", "extendValue", "rowValue"):
            nested = value.get(nested_key)
            if isinstance(nested, (dict, list, str)):
                cls._collect_oa_attachment_records(nested, attachments)

    def _dws_tool_error_payload(self, exc: Exception) -> dict[str, str]:
        if self._is_dws_login_error(exc):
            self._ensure_dws_auth_login(exc)
            return {
                "error_kind": "dws_login_required",
                "message": str(exc),
            }
        if self._is_authorization_error(exc):
            return {
                "error_kind": "dws_authorization_required",
                "message": str(exc),
            }
        return {
            "error_kind": "dws_error",
            "message": str(exc),
        }

    @staticmethod
    def _annotate_oa_detail_recovery(documents: dict[str, Any]) -> None:
        dws_detail = documents.get("dws_detail")
        openapi_detail = documents.get("openapi_detail")
        if not (
            isinstance(dws_detail, dict)
            and dws_detail.get("error_kind") == "dws_error"
            and isinstance(openapi_detail, dict)
            and not openapi_detail.get("error_kind")
        ):
            return
        documents.pop("dws_detail", None)
        documents["dws_detail_status"] = {
            "status": "recovered_by_openapi",
            "message": (
                "dws oa approval detail failed because the DWS detail command "
                "could not parse this process instance. The worker already "
                "recovered the approval detail through OpenAPI. Use openapi_detail, "
                "dws_records, dws_tasks, and oa_attachment_fallbacks as the "
                "source of truth. Do not call dws oa approval detail again, "
                "and do not try --format raw or --fields variants for this instance."
            ),
        }

    @staticmethod
    def _oa_detail_needs_openapi(payload: Any) -> bool:
        return DingTalkAutoReplyWorker._dws_tool_payload_is_error(
            payload
        ) or DingTalkAutoReplyWorker._oa_detail_has_empty_form(payload)

    @staticmethod
    def _dws_tool_payload_is_error(payload: Any) -> bool:
        return isinstance(payload, dict) and bool(payload.get("error_kind"))

    @staticmethod
    def _annotate_oa_tool_status(documents: dict[str, Any]) -> None:
        error_kinds = {
            value.get("error_kind")
            for value in documents.values()
            if isinstance(value, dict) and isinstance(value.get("error_kind"), str)
        }
        if "dws_login_required" in error_kinds:
            documents["tool_status"] = "dws_login_required"
            documents["tool_issue"] = (
                "DWS 未登录或登录态失效，当前不是审批材料缺失。"
            )
        elif "dws_authorization_required" in error_kinds:
            documents["tool_status"] = "dws_authorization_required"
            documents["tool_issue"] = (
                "DWS 权限不足，当前不是审批材料缺失。"
            )

    @staticmethod
    def _oa_process_instance_id_from_url(oa_url: str) -> str:
        if not oa_url:
            return ""
        parsed = urlparse(oa_url)
        query = parse_qs(parsed.query)
        for key in ("procInstId", "processInstanceId", "process_instance_id"):
            values = query.get(key)
            if values:
                return values[0]
        return ""

    @staticmethod
    def _oa_task_id_from_url(oa_url: str) -> str:
        if not oa_url:
            return ""
        parsed = urlparse(oa_url)
        query = parse_qs(parsed.query)
        for key in ("taskId", "task_id"):
            values = query.get(key)
            if values:
                return values[0]
        return ""

    def _find_pending_oa_process_instance_id(self, trigger: DingTalkMessage) -> str:
        try:
            candidates = self.dws.list_pending_oa_approvals(page=1, size=30)
        except Exception:
            return ""
        trigger_units = self._oa_matching_units(
            " ".join((trigger.sender_name, trigger.content))
        )
        best_score = 0
        best_process_instance_id = ""
        for candidate in candidates:
            candidate_units = self._oa_matching_units(
                " ".join((candidate.title, candidate.process_name))
            )
            score = len(trigger_units & candidate_units)
            if score > best_score:
                best_score = score
                best_process_instance_id = candidate.process_instance_id
        return best_process_instance_id if best_score else ""

    @staticmethod
    def _oa_matching_units(text: str) -> set[str]:
        units = set()
        current = []
        for char in text:
            if char.isascii() and char.isalnum():
                current.append(char.lower())
                continue
            if current:
                units.add("".join(current))
                current = []
            if "\u4e00" <= char <= "\u9fff":
                units.add(char)
        if current:
            units.add("".join(current))
        return {unit for unit in units if unit}

    @staticmethod
    def _oa_detail_has_empty_form(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return True
        result = payload.get("result")
        if not isinstance(result, dict):
            return True
        form_values = result.get("formValueVOS")
        if not isinstance(form_values, list) or not form_values:
            return True
        for item in form_values:
            if not isinstance(item, dict):
                continue
            details = item.get("details")
            if isinstance(details, list) and details:
                return False
        return True

    @staticmethod
    def _is_oa_approval_message(message: DingTalkMessage) -> bool:
        content = message.content.strip()
        return bool(
            DINGTALK_APPROVAL_LINK_PATTERN.search(content)
            or DINGTALK_APPROVAL_REMINDER_PATTERN.search(content)
        )

    def _handle_calendar_invite_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
        *,
        ignore_existing_attempt: bool = False,
        include_resolved_calendar_invites: bool = False,
        raise_on_delivery_failure: bool = False,
        allow_duplicate_send: bool = False,
        complete_task_id: int | None = None,
    ) -> bool:
        calendar_context = self._calendar_invite_context(
            conversation,
            trigger,
            context_messages,
            include_resolved_invites=include_resolved_calendar_invites,
        )
        if calendar_context is None:
            if self._is_calendar_message(trigger):
                if not ignore_existing_attempt and self._handle_existing_attempt(
                    conversation,
                    trigger,
                    [trigger],
                    ignore_system_notification_skip=True,
                ):
                    return True
                reply_text = self._calendar_unreadable_reply()
                attempt_id = self.store.record_reply_attempt_for_trigger(
                    conversation_id=conversation.open_conversation_id,
                    conversation_title=conversation.title,
                    trigger_message_id=trigger.open_message_id,
                    trigger_sender=trigger.sender_name,
                    trigger_text=trigger.content,
                    action=CodexAction.ASK_CLARIFYING_QUESTION.value,
                    sensitivity_kind="general",
                    codex_reason="calendar_detail_unreadable",
                    draft_reply_text=reply_text,
                    audit_summary="收到日程消息但未能读取日程详情；按日历规则追问可读信息。",
                )
                self._send_reply(
                    conversation=conversation,
                    trigger=trigger,
                    new_messages=[trigger],
                    reply_text=reply_text,
                    reason="calendar_detail_unreadable",
                    attempt_id=attempt_id,
                    raise_on_delivery_failure=raise_on_delivery_failure,
                    allow_duplicate_send=allow_duplicate_send,
                )
                return True
            return False
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            [trigger],
            ignore_system_notification_skip=True,
        ):
            return True
        if not calendar_context.conflicts:
            calendar_prompt_message = self._calendar_invite_prompt_message(
                conversation, trigger, calendar_context
            )
            self._process_batch(
                conversation,
                [trigger],
                [
                    *context_messages,
                    calendar_prompt_message,
                ],
                ignore_existing_attempt=True,
                raise_on_delivery_failure=raise_on_delivery_failure,
                calendar_response_event=calendar_context.invite,
                comment_target_messages=[trigger, calendar_prompt_message],
                allow_duplicate_send=allow_duplicate_send,
                complete_task_id=complete_task_id,
            )
            return True
        calendar_prompt_message = self._calendar_conflict_prompt_message(
            conversation, trigger, calendar_context
        )
        self._process_batch(
            conversation,
            [trigger],
            [
                *context_messages,
                calendar_prompt_message,
            ],
            ignore_existing_attempt=True,
            raise_on_delivery_failure=raise_on_delivery_failure,
            calendar_response_event=calendar_context.invite,
            comment_target_messages=[trigger, calendar_prompt_message],
            allow_duplicate_send=allow_duplicate_send,
            complete_task_id=complete_task_id,
        )
        return True

    def _calendar_invite_context(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
        context_messages: list[DingTalkMessage] | None = None,
        *,
        include_resolved_invites: bool = False,
    ) -> CalendarConflictContext | None:
        if not self._is_calendar_message(message):
            return None
        list_calendar_events = getattr(self.dws, "list_calendar_events", None)
        if list_calendar_events is None:
            return None
        invite = self._calendar_invite_from_message_or_sender(
            conversation,
            message,
            context_messages=context_messages,
            include_resolved=include_resolved_invites,
        )
        if invite is None:
            return None
        events = list_calendar_events(invite.start_time, invite.end_time)
        conflicts = [
            event
            for event in events
            if self._calendar_events_conflict(invite, event)
            and not self._same_calendar_event(invite, event)
            and self._calendar_event_is_active(event)
            and self._calendar_event_blocks_time(event)
        ]
        return CalendarConflictContext(invite=invite, conflicts=conflicts)

    def _calendar_invite_from_existing_attempt(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> DwsCalendarEvent | None:
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            message.open_message_id,
        )
        if attempt is None:
            return None
        event_id = attempt.calendar_event_id.strip()
        if not event_id:
            return None
        get_calendar_event = getattr(self.dws, "get_calendar_event", None)
        if get_calendar_event is None:
            return None
        return get_calendar_event(event_id)

    def _calendar_pending_invite_from_sender(
        self,
        message: DingTalkMessage,
        list_calendar_events: Callable[[str, str], list[DwsCalendarEvent]],
        *,
        context_messages: list[DingTalkMessage] | None = None,
        include_resolved: bool = False,
    ) -> DwsCalendarEvent | None:
        sender_name = message.sender_name.strip()
        if not sender_name:
            return None
        start, end = self._calendar_pending_invite_search_window(message)
        events = list_calendar_events(start, end)
        candidates = self._calendar_pending_invite_candidates(
            events,
            include_resolved=include_resolved,
        )
        if not message.single_chat:
            matched = self._calendar_pending_invite_from_context(
                candidates,
                message,
                context_messages or [],
            )
            if matched is not None:
                return matched
        sender_candidates = [
            event for event in candidates if event.organizer.strip() == sender_name
        ]
        matched = self._calendar_pending_invite_from_candidates(
            sender_candidates,
            message,
        )
        if matched is not None:
            return matched
        if message.single_chat:
            sender_attendee_candidates = [
                event
                for event in candidates
                if self._calendar_event_has_attendee(event, sender_name)
            ]
            matched = self._calendar_pending_invite_from_candidates(
                sender_attendee_candidates,
                message,
            )
            if matched is not None:
                return matched
            return self._calendar_pending_invite_from_context(
                candidates,
                message,
                context_messages or [],
            )
        return None

    def _calendar_pending_invite_candidates(
        self,
        events: list[DwsCalendarEvent],
        *,
        include_resolved: bool = False,
    ) -> list[DwsCalendarEvent]:
        return [
            event
            for event in events
            if self._calendar_event_is_active(event)
            and (
                self._calendar_event_is_self_pending(event)
                or (
                    include_resolved
                    and self._calendar_event_has_self_response(event)
                )
            )
        ]

    def _calendar_pending_invite_from_candidates(
        self,
        candidates: list[DwsCalendarEvent],
        message: DingTalkMessage,
    ) -> DwsCalendarEvent | None:
        candidates = self._calendar_pending_invite_candidates_with_details(candidates)
        near_message_candidates = [
            event
            for event in candidates
            if self._calendar_event_changed_near_message(event, message)
        ]
        if len(near_message_candidates) == 1:
            return near_message_candidates[0]
        if len(near_message_candidates) > 1:
            return self._closest_calendar_event_changed_near_message(
                near_message_candidates,
                message,
            )
        upcoming_candidate = (
            self._closest_upcoming_calendar_event_without_change_time(
                candidates,
                message,
            )
        )
        if upcoming_candidate is not None:
            return upcoming_candidate
        if len(candidates) == 1 and not self._calendar_event_has_change_time(
            candidates[0]
        ):
            return candidates[0]
        return None

    def _calendar_pending_invite_from_context(
        self,
        candidates: list[DwsCalendarEvent],
        message: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> DwsCalendarEvent | None:
        context_keywords = self._calendar_context_matching_keywords(
            message,
            context_messages,
        )
        context_time_markers = self._calendar_context_time_markers(
            message,
            context_messages,
        )
        if not context_keywords and not context_time_markers:
            return None
        detailed_candidates = self._calendar_pending_invite_candidates_with_details(
            candidates
        )
        scored: list[tuple[float, DwsCalendarEvent]] = []
        for event in detailed_candidates:
            event_keywords = self._calendar_event_matching_keywords(event)
            score = self._calendar_keyword_overlap(context_keywords, event_keywords)
            event_time_markers = self._calendar_event_time_markers(event)
            score += 0.75 * len(context_time_markers & event_time_markers)
            if score >= CALENDAR_CONTEXT_MATCH_MIN_SCORE:
                scored.append((score, event))
        if not scored:
            return None
        pending_scored = [
            (score, event)
            for score, event in scored
            if self._calendar_event_is_self_pending(event)
        ]
        if pending_scored:
            scored = pending_scored
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score = scored[0][0]
        best_candidates = [event for score, event in scored if score == best_score]
        if len(best_candidates) == 1:
            return best_candidates[0]
        return self._closest_upcoming_calendar_event(best_candidates, message)

    @classmethod
    def _calendar_context_matching_keywords(
        cls,
        message: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> dict[str, float]:
        message_time = cls._message_create_time_as_instant(message)
        text_parts: list[str] = []
        for context_message in context_messages:
            if context_message.open_message_id == message.open_message_id:
                continue
            try:
                context_time = cls._message_create_time_as_instant(context_message)
            except ValueError:
                continue
            delta = message_time - context_time
            if not timedelta() <= delta <= CALENDAR_CONTEXT_MATCH_LOOKBACK:
                continue
            text_parts.append(context_message.content)
        return cls._calendar_non_numeric_keywords(" ".join(text_parts))

    @staticmethod
    def _calendar_event_matching_keywords(
        event: DwsCalendarEvent,
    ) -> dict[str, float]:
        return DingTalkAutoReplyWorker._calendar_non_numeric_keywords(
            " ".join(
                (
                    event.title,
                    event.description,
                    event.organizer,
                )
            ),
        )

    @staticmethod
    def _calendar_non_numeric_keywords(text: str) -> dict[str, float]:
        return {
            keyword: score
            for keyword, score in extract_retrieval_keywords(text, limit=50).items()
            if not keyword.isdigit()
        }

    @classmethod
    def _calendar_context_time_markers(
        cls,
        message: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> set[str]:
        message_time = cls._message_create_time_as_instant(message)
        markers: set[str] = set()
        for context_message in context_messages:
            if context_message.open_message_id == message.open_message_id:
                continue
            try:
                context_time = cls._message_create_time_as_instant(context_message)
            except ValueError:
                continue
            delta = message_time - context_time
            if not timedelta() <= delta <= CALENDAR_CONTEXT_MATCH_LOOKBACK:
                continue
            markers.update(cls._calendar_text_time_markers(context_message.content))
        return markers

    @staticmethod
    def _calendar_text_time_markers(text: str) -> set[str]:
        markers = set(re.findall(r"周[一二三四五六日天]", text))
        markers.update(re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", text))
        return markers

    @staticmethod
    def _calendar_event_time_markers(event: DwsCalendarEvent) -> set[str]:
        start_time = DingTalkAutoReplyWorker._parse_calendar_time(event.start_time)
        end_time = DingTalkAutoReplyWorker._parse_calendar_time(event.end_time)
        if start_time is None:
            return set()
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
        start_time = start_time.astimezone(DINGTALK_MESSAGE_TIME_ZONE)
        markers = {
            f"周{DingTalkAutoReplyWorker._weekday_name(start_time.weekday())}",
            start_time.strftime("%H:%M"),
        }
        if end_time is not None:
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
            end_time = end_time.astimezone(DINGTALK_MESSAGE_TIME_ZONE)
            markers.add(end_time.strftime("%H:%M"))
        return markers

    @staticmethod
    def _weekday_name(weekday: int) -> str:
        names = ("一", "二", "三", "四", "五", "六", "日")
        if 0 <= weekday < len(names):
            return names[weekday]
        return ""

    @staticmethod
    def _calendar_keyword_overlap(
        left: dict[str, float],
        right: dict[str, float],
    ) -> float:
        return sum(left[keyword] * right[keyword] for keyword in left if keyword in right)

    def _calendar_pending_invite_candidates_with_details(
        self,
        candidates: list[DwsCalendarEvent],
    ) -> list[DwsCalendarEvent]:
        get_calendar_event = getattr(self.dws, "get_calendar_event", None)
        if get_calendar_event is None:
            return candidates
        result: list[DwsCalendarEvent] = []
        for event in candidates:
            if self._calendar_event_has_change_time(event):
                result.append(event)
                continue
            detailed_event = get_calendar_event(event.event_id)
            result.append(detailed_event or event)
        return result

    @staticmethod
    def _calendar_event_is_self_pending(event: DwsCalendarEvent) -> bool:
        self_response_status = event.self_response_status.strip().lower()
        return self_response_status in {
            "needsaction",
            "needs_action",
            "needs-action",
            "tentative",
        }

    @staticmethod
    def _calendar_event_has_self_response(event: DwsCalendarEvent) -> bool:
        self_response_status = event.self_response_status.strip().lower()
        return self_response_status in {
            "accepted",
            "tentative",
            "declined",
            "rejected",
        }

    @classmethod
    def _closest_calendar_event_changed_near_message(
        cls,
        events: list[DwsCalendarEvent],
        message: DingTalkMessage,
    ) -> DwsCalendarEvent | None:
        scored_events = [
            (delta_ms, event)
            for event in events
            if (delta_ms := cls._calendar_event_change_delta_ms(event, message))
            is not None
        ]
        if not scored_events:
            return None
        scored_events.sort(key=lambda item: item[0])
        if len(scored_events) > 1 and scored_events[0][0] == scored_events[1][0]:
            return None
        return scored_events[0][1]

    @staticmethod
    def _calendar_event_changed_near_message(
        event: DwsCalendarEvent,
        message: DingTalkMessage,
    ) -> bool:
        delta_ms = DingTalkAutoReplyWorker._calendar_event_change_delta_ms(
            event,
            message,
        )
        if delta_ms is None:
            return False
        return delta_ms <= CALENDAR_PENDING_INVITE_EVENT_MATCH_SECONDS * 1000

    @staticmethod
    def _calendar_event_has_change_time(event: DwsCalendarEvent) -> bool:
        return event.created_ms > 0 or event.updated_ms > 0

    @classmethod
    def _closest_upcoming_calendar_event_without_change_time(
        cls,
        events: list[DwsCalendarEvent],
        message: DingTalkMessage,
    ) -> DwsCalendarEvent | None:
        message_time = cls._message_create_time_as_instant(message)
        scored_events: list[tuple[timedelta, DwsCalendarEvent]] = []
        for event in events:
            if cls._calendar_event_has_change_time(event):
                continue
            start_time = cls._parse_calendar_time(event.start_time)
            if start_time is None:
                continue
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
            start_time = start_time.astimezone(message_time.tzinfo or timezone.utc)
            delta = start_time - message_time
            if (
                timedelta()
                <= delta
                <= CALENDAR_PENDING_INVITE_NO_CHANGE_TIME_START_LOOKAHEAD
            ):
                scored_events.append((delta, event))
        if not scored_events:
            return None
        scored_events.sort(key=lambda item: item[0])
        if len(scored_events) > 1 and scored_events[0][0] == scored_events[1][0]:
            return None
        return scored_events[0][1]

    @classmethod
    def _closest_upcoming_calendar_event(
        cls,
        events: list[DwsCalendarEvent],
        message: DingTalkMessage,
    ) -> DwsCalendarEvent | None:
        message_time = cls._message_create_time_as_instant(message)
        scored_events: list[tuple[timedelta, DwsCalendarEvent]] = []
        for event in events:
            start_time = cls._parse_calendar_time(event.start_time)
            if start_time is None:
                continue
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
            start_time = start_time.astimezone(message_time.tzinfo or timezone.utc)
            delta = start_time - message_time
            if delta >= timedelta():
                scored_events.append((delta, event))
        if not scored_events:
            return None
        scored_events.sort(key=lambda item: item[0])
        if len(scored_events) > 1 and scored_events[0][0] == scored_events[1][0]:
            return None
        return scored_events[0][1]

    @staticmethod
    def _calendar_event_change_delta_ms(
        event: DwsCalendarEvent,
        message: DingTalkMessage,
    ) -> int | None:
        message_time_ms = int(
            DingTalkAutoReplyWorker._message_create_time_as_instant(
                message
            ).timestamp()
            * 1000
        )
        deltas = [
            abs(event_time_ms - message_time_ms)
            for event_time_ms in (event.created_ms, event.updated_ms)
            if event_time_ms > 0
        ]
        return min(deltas) if deltas else None


    def _calendar_pending_invite_search_window(
        self,
        message: DingTalkMessage,
    ) -> tuple[str, str]:
        message_time = self._message_create_time_as_instant(message).astimezone(
            DINGTALK_MESSAGE_TIME_ZONE
        )
        now = self._now().astimezone(DINGTALK_MESSAGE_TIME_ZONE)
        start = min(message_time, now) - timedelta(hours=1)
        end = start + timedelta(days=CALENDAR_PENDING_INVITE_LOOKAHEAD_DAYS)
        return (
            start.isoformat(timespec="seconds"),
            end.isoformat(timespec="seconds"),
        )

    def _calendar_conflict_context(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> CalendarConflictContext | None:
        context = self._calendar_invite_context(conversation, message)
        if context is None or not context.conflicts:
            return None
        return context

    @staticmethod
    def _is_calendar_message(message: DingTalkMessage) -> bool:
        message_type = (message.message_type or "").strip().lower()
        content = message.content.strip()
        decoded_content = unquote(content)
        return message_type in {
            "calendar",
            "schedule",
        } or content.startswith("[日程]") or any(
            marker in decoded_content
            for marker in (
                "newCalendar=1",
                "calendarDetail",
                "uniqueId=",
            )
        )

    @staticmethod
    def _calendar_events_conflict(
        invite: DwsCalendarEvent,
        existing: DwsCalendarEvent,
    ) -> bool:
        invite_start = DingTalkAutoReplyWorker._parse_calendar_time(invite.start_time)
        invite_end = DingTalkAutoReplyWorker._parse_calendar_time(invite.end_time)
        existing_start = DingTalkAutoReplyWorker._parse_calendar_time(
            existing.start_time
        )
        existing_end = DingTalkAutoReplyWorker._parse_calendar_time(existing.end_time)
        if not all((invite_start, invite_end, existing_start, existing_end)):
            return False
        return invite_start < existing_end and existing_start < invite_end

    @staticmethod
    def _calendar_event_blocks_time(event: DwsCalendarEvent) -> bool:
        self_response_status = event.self_response_status.strip().lower()
        return self_response_status not in {
            "declined",
            "rejected",
            "needsaction",
            "needs_action",
            "needs-action",
        }

    @staticmethod
    def _same_calendar_event(left: DwsCalendarEvent, right: DwsCalendarEvent) -> bool:
        if left.event_id and right.event_id:
            return left.event_id == right.event_id
        return (
            bool(left.title and right.title)
            and left.title == right.title
            and left.start_time == right.start_time
            and left.end_time == right.end_time
        )

    @staticmethod
    def _parse_calendar_time(value: str) -> datetime | None:
        if not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            try:
                return datetime.strptime(normalized, DINGTALK_TIME_FORMAT)
            except ValueError:
                return None

    @staticmethod
    def _calendar_unreadable_reply() -> str:
        return (
            "我这边只看到日程卡片，但没有读到会议标题、时间和描述。请补充一下参加理由、"
            "希望我决策或输入的内容，以及为什么需要我参加。"
        )

    @staticmethod
    def _calendar_conflict_prompt_message(
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context: CalendarConflictContext,
    ) -> DingTalkMessage:
        lines = [
            "日历冲突检查：",
            "有人发来新的日程邀请，且时间已经被已有日程占用。",
            "请先结合最近上下文事项、会议标题、时间、组织者、会议描述、会议评论和重叠会议判断是否有必要参加；会议描述为空不是自动追问条件。",
            "如果新会议标题、描述或会议评论显示这是静默会、异步评审、材料审阅或明确要求处理事项，这条规则优先于普通文档批阅转交规则；不能只接受日历，也不能只要求对方改去文档里 @，必须把日程标题、描述、会议评论、链接材料和冲突会议当作待处理事项直接处理，输出处理结论或需要补充的材料。",
            "最近聊天上下文只能用于理解背景和判断参加价值，不能替代会议描述、会议评论或链接材料成为静默会任务来源；如果会议内容和评论没有给出可处理材料，应要求补充具体缺失材料。",
            "只有当日程不是静默会/异步评审/材料审阅/明确处理事项，且只是邀请批阅、review、反馈或评论某个文档但没有提供足够可处理材料时，user_response.text 才使用：请直接@我文档让我批阅即可，只有存疑再约会。",
            "如果最近事项和标题已经能判断本人有必要参加，user_response.mode 输出 send_reply，user_response.text 用一两句话说明接受理由，并设置 domain_payload.calendar_response_status 为 accepted。",
            "如果最近事项和标题能判断没有必要参加或仅需保留观察，可以 user_response.mode 输出 no_reply，并设置 domain_payload.calendar_response_status 为 declined 或 tentative。",
            "这类材料处理可以同时设置 domain_payload.calendar_response_status；如果材料链接支持评论，服务会优先写入原材料评论，不能评论时再回退到原消息回复。",
            "如果理由充分但需要聊天同步，回复中说明建议接受这场会议并调整或拒绝哪个重叠会议；如果信息不足，再回复对方原因并请补充。",
            "",
            f"新会议：{context.invite.title or '未命名日程'}",
            f"时间：{context.invite.start_time} - {context.invite.end_time}",
            f"组织者：{context.invite.organizer or trigger.sender_name}",
            f"会议描述：{context.invite.description.strip() or '无'}",
            f"会议评论：{DingTalkAutoReplyWorker._calendar_comments_text(context.invite)}",
            "重叠会议：",
        ]
        for event in context.conflicts:
            lines.append(
                "- "
                f"{event.title or '未命名日程'} | "
                f"{event.start_time} - {event.end_time} | "
                f"描述：{event.description.strip() or '无'}"
            )
        return DingTalkMessage(
            open_conversation_id=conversation.open_conversation_id,
            open_message_id=f"{trigger.open_message_id}:calendar-conflict-context",
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            sender_name="CEO系统",
            create_time=trigger.create_time,
            content="\n".join(lines),
        )

    @staticmethod
    def _calendar_invite_prompt_message(
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context: CalendarConflictContext,
    ) -> DingTalkMessage:
        lines = [
            "日历规则判断：",
            "有人发来新的日程邀请，当前未发现同时间段已有日程冲突。",
            "请先结合最近上下文事项、会议标题、时间、组织者、会议描述和会议评论判断是否有必要参加；会议描述为空不是自动追问条件。",
            "如果新会议标题、描述或会议评论显示这是静默会、异步评审、材料审阅或明确要求处理事项，这条规则优先于普通文档批阅转交规则；不能只接受日历，也不能只要求对方改去文档里 @，必须把日程标题、描述、会议评论和链接材料当作待处理事项直接处理，输出处理结论或需要补充的材料。",
            "最近聊天上下文只能用于理解背景和判断参加价值，不能替代会议描述、会议评论或链接材料成为静默会任务来源；如果会议内容和评论没有给出可处理材料，应要求补充具体缺失材料。",
            "只有当日程不是静默会/异步评审/材料审阅/明确处理事项，且只是邀请批阅、review、反馈或评论某个文档但没有提供足够可处理材料时，user_response.text 才使用：请直接@我文档让我批阅即可，只有存疑再约会。",
            "这类材料处理可以同时设置 domain_payload.calendar_response_status；如果材料链接支持评论，服务会优先写入原材料评论，不能评论时再回退到原消息回复。",
            f"如果最近事项和标题足以判断 {principal_display_name()} 本人有必要参加，user_response.mode 输出 send_reply，user_response.text 用一两句话说明接受理由，并设置 domain_payload.calendar_response_status 为 accepted。",
            "如果最近事项和标题足以判断先保留但不确认，user_response.mode 输出 no_reply，并设置 domain_payload.calendar_response_status 为 tentative。",
            "如果最近事项和标题足以判断本人参加无价值，user_response.mode 输出 no_reply，并设置 domain_payload.calendar_response_status 为 declined。",
            "如果结合最近事项、标题、时间、组织者、描述和评论后仍不足以判断，再追问补充信息或 handoff。",
            "",
            f"新会议：{context.invite.title or '未命名日程'}",
            f"时间：{context.invite.start_time} - {context.invite.end_time}",
            f"组织者：{context.invite.organizer or trigger.sender_name}",
            f"会议描述：{context.invite.description.strip() or '无'}",
            f"会议评论：{DingTalkAutoReplyWorker._calendar_comments_text(context.invite)}",
        ]
        return DingTalkMessage(
            open_conversation_id=conversation.open_conversation_id,
            open_message_id=f"{trigger.open_message_id}:calendar-invite-context",
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            sender_name="CEO系统",
            create_time=trigger.create_time,
            content="\n".join(lines),
        )

    @staticmethod
    def _calendar_comments_text(event: DwsCalendarEvent) -> str:
        comments = [comment.strip() for comment in event.comments if comment.strip()]
        return "\n".join(comments) if comments else "无"

    def _respond_calendar_invite(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        event: DwsCalendarEvent,
        response_status: str,
        attempt_id: int,
        reason: str,
        raise_on_delivery_failure: bool = False,
        allow_duplicate_send: bool = False,
        complete_task_id: int | None = None,
    ) -> None:
        succeeded = self._execute_calendar_response(
            conversation=conversation,
            trigger=trigger,
            event=event,
            response_status=response_status,
            attempt_id=attempt_id,
            mark_attempt_terminal=True,
            raise_on_delivery_failure=raise_on_delivery_failure,
            complete_task_id=complete_task_id,
        )
        if not succeeded:
            return
        self._mark_seen(new_messages)
        if reason:
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_response",
                f"{response_status}: {reason}",
            )

    @staticmethod
    def _calendar_response_is_organizer_noop(exc: Exception) -> bool:
        return CALENDAR_ORGANIZER_RESPONSE_ERROR in str(exc)

    def _mark_calendar_response_noop(
        self,
        *,
        attempt_id: int,
        event_id: str,
        response_status: str,
        send_status: str | None,
        send_error: str,
        complete_task_id: int | None = None,
    ) -> None:
        result = {
            "success": True,
            "noop_reason": "calendar_event_organizer",
            "message": CALENDAR_ORGANIZER_RESPONSE_ERROR,
        }
        updates: dict[str, Any] = {
            "calendar_event_id": event_id,
            "calendar_response_status": response_status,
            "calendar_response_result_json": json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "send_error": send_error,
        }
        if send_status is not None:
            updates["send_status"] = send_status
        if complete_task_id is not None and send_status == CALENDAR_ACTION_SEND_STATUS:
            self.store.update_reply_attempt_and_complete_task(
                attempt_id,
                complete_task_id,
                **updates,
            )
        else:
            self.store.update_reply_attempt(attempt_id, **updates)

    def _execute_calendar_response(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        event: DwsCalendarEvent,
        response_status: str,
        attempt_id: int,
        mark_attempt_terminal: bool,
        raise_on_delivery_failure: bool = False,
        complete_task_id: int | None = None,
    ) -> bool:
        if not event.event_id.strip():
            error = "missing_calendar_event_id"
            updates: dict[str, Any] = {
                "calendar_event_id": event.event_id,
                "calendar_response_status": response_status,
                "send_error": error,
            }
            if mark_attempt_terminal:
                updates["send_status"] = "failed"
            self.store.update_reply_attempt(attempt_id, **updates)
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_response",
                error,
            )
            self._notify(
                title=f"CEO calendar response failed: {conversation.title}",
                message=error,
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(error)
            return False
        if self.dry_run:
            updates = {
                "calendar_event_id": event.event_id,
                "calendar_response_status": response_status,
            }
            if mark_attempt_terminal:
                updates["send_status"] = "dry_run"
            self.store.update_reply_attempt(attempt_id, **updates)
            return True
        try:
            action_result = self.dws.respond_calendar_event(
                event.event_id, response_status
            )
        except Exception as exc:
            if self._calendar_response_is_organizer_noop(exc):
                self._mark_calendar_response_noop(
                    attempt_id=attempt_id,
                    event_id=event.event_id,
                    response_status=response_status,
                    send_status=CALENDAR_ACTION_SEND_STATUS
                    if mark_attempt_terminal
                    else None,
                    send_error="calendar_event_organizer_noop",
                    complete_task_id=complete_task_id,
                )
                return True
            updates = {
                "calendar_event_id": event.event_id,
                "calendar_response_status": response_status,
                "send_error": str(exc),
            }
            if mark_attempt_terminal:
                updates["send_status"] = "failed"
            self.store.update_reply_attempt(attempt_id, **updates)
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_response",
                str(exc),
            )
            self._notify(
                title=f"CEO calendar response failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return False
        updates = {
            "calendar_event_id": event.event_id,
            "calendar_response_status": response_status,
            "calendar_response_result_json": json.dumps(
                action_result,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "send_error": "",
        }
        if mark_attempt_terminal:
            updates["send_status"] = CALENDAR_ACTION_SEND_STATUS
        if complete_task_id is not None and mark_attempt_terminal:
            self.store.update_reply_attempt_and_complete_task(
                attempt_id,
                complete_task_id,
                **updates,
            )
        else:
            self.store.update_reply_attempt(attempt_id, **updates)
        return True

    def _record_system_or_notification_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        self._log_producer_skip(
            conversation,
            message,
            reason="system_or_notification_message",
            audit_summary="系统类或通知类消息，无需自动回复。",
        )

    def _record_current_user_replied_during_backoff_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        self._log_producer_skip(
            conversation,
            message,
            reason="current_user_replied_during_backoff",
            audit_summary=(
                "快路径首次发现未读后已等待；等待窗口内检测到本人已在该 trigger "
                "之后回复，因此不再由 agent 自动回复。"
            ),
        )

    def _record_trigger_recalled_after_backoff_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        self._log_producer_skip(
            conversation,
            message,
            reason="trigger_message_recalled_after_backoff",
            audit_summary=(
                "快路径等待窗口结束后复核原 trigger；DWS 返回该消息已撤回或不再可见，"
                "因此不再由 agent 自动回复。"
            ),
        )

    def _record_stale_message_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        self._log_producer_skip(
            conversation,
            message,
            reason="message_older_than_24h",
            audit_summary="消息超过最近 24 小时窗口，不自动回复。",
        )

    def _log_producer_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
        *,
        reason: str,
        audit_summary: str,
    ) -> None:
        logger.info(
            "producer skipped message status=skipped reason=%s "
            "conversation_id=%s message_id=%s conversation_title=%r "
            "sender=%r audit_summary=%s",
            reason,
            conversation.open_conversation_id,
            message.open_message_id,
            conversation.title,
            message.sender_name,
            audit_summary,
        )

    @staticmethod
    def _is_system_or_notification_message(message: DingTalkMessage) -> bool:
        if (
            message.message_type
            and message.message_type.lower() not in TEXT_MESSAGE_TYPES
        ):
            return True
        content = message.content.strip()
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_dingtalk_minutes_link(content):
            return DingTalkAutoReplyWorker._is_bare_dingtalk_minutes_link_message(
                content
            )
        if DingTalkAutoReplyWorker._has_rendered_non_text_prefix(content):
            return True
        if content.startswith("[dingtalk://"):
            return True
        if DingTalkAutoReplyWorker._is_link_caption_only(content):
            return True
        if DingTalkAutoReplyWorker._is_structured_link_card(content):
            return True
        if DingTalkAutoReplyWorker._is_system_status_notification(content):
            return True
        return False

    @staticmethod
    def _is_system_status_notification(content: str) -> bool:
        if not SYSTEM_STATUS_NOTIFICATION_PATTERN.match(content):
            return False
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if ORDINARY_EXTERNAL_LINK_PATTERN.search(content):
            return False
        return not DingTalkAutoReplyWorker._has_question_outside_links(content)

    @staticmethod
    def _has_rendered_non_text_prefix(content: str) -> bool:
        return content.startswith(
            RENDERED_NON_TEXT_PREFIXES
        ) or RENDERED_NON_TEXT_PREFIX_PATTERN.match(content) is not None

    @staticmethod
    def _has_dingtalk_minutes_link(content: str) -> bool:
        return any(
            DingTalkAutoReplyWorker._minutes_task_uuid_from_url(match.group(0))
            for match in DINGTALK_MINUTES_LINK_PATTERN.finditer(content)
        )

    @staticmethod
    def _is_bare_dingtalk_minutes_link_message(content: str) -> bool:
        if DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.search(content):
            return False
        text_without_links = MEDIA_OR_LINK_PATTERN.sub(" ", content)
        text_without_mentions = MENTION_PATTERN.sub(" ", text_without_links)
        if QUESTION_MARK_PATTERN.search(text_without_mentions):
            return False
        return count_information_units(text_without_mentions) == 0

    @staticmethod
    def _is_link_caption_only(content: str) -> bool:
        if not MEDIA_OR_LINK_PATTERN.search(content):
            return False
        if not DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN.search(content):
            return False
        if DINGTALK_DOC_URL_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_dingtalk_minutes_link(content):
            return False
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_question_outside_links(content):
            return False
        text_without_links = MEDIA_OR_LINK_PATTERN.sub(" ", content)
        text_without_mentions = MENTION_PATTERN.sub(" ", text_without_links)
        return count_information_units(text_without_mentions) <= 2

    @staticmethod
    def _is_structured_link_card(content: str) -> bool:
        if not MEDIA_OR_LINK_PATTERN.search(content):
            return False
        if not DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN.search(content):
            return False
        if DINGTALK_DOC_URL_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_dingtalk_minutes_link(content):
            return False
        if DINGTALK_APPROVAL_LINK_PATTERN.search(content):
            return False
        if DingTalkAutoReplyWorker._has_question_outside_links(content):
            return False
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < 4:
            return False
        field_line_count = sum(1 for line in lines if FIELD_LINE_PATTERN.match(line))
        return field_line_count >= 3 and field_line_count / len(lines) >= 0.45

    @staticmethod
    def _has_question_outside_links(content: str) -> bool:
        return bool(
            QUESTION_MARK_PATTERN.search(MEDIA_OR_LINK_PATTERN.sub(" ", content))
        )

    def _candidate_messages(
        self,
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        if conversation.single_chat:
            eligible_messages = messages
            latest_current_user_message_time = None
        else:
            current_user_message_times = [
                message.create_time
                for message in messages
                if self._is_current_user_message_for_candidate_filter(message)
                and not self._is_split_person_auto_reply_message(message)
                and not self._is_processing_ack_message(message)
                and not self._is_system_or_notification_message(message)
            ]
            latest_current_user_message_time = (
                max(current_user_message_times) if current_user_message_times else None
            )
            eligible_messages = [
                message
                for message in messages
                if message.addresses_principal()
            ]
        candidates = [
            message
            for message in eligible_messages
            if not self._is_current_user_message_for_candidate_filter(message)
            and (
                latest_current_user_message_time is None
                or message.create_time > latest_current_user_message_time
            )
        ]
        return sorted(candidates, key=lambda message: message.create_time)

    def _has_current_user_reply_after_trigger(
        self,
        messages: list[DingTalkMessage],
        trigger: DingTalkMessage,
    ) -> bool:
        for message in messages:
            if message.open_message_id == trigger.open_message_id:
                continue
            if message.create_time <= trigger.create_time:
                continue
            if not self._is_current_user_message_for_candidate_filter(message):
                continue
            if self._is_split_person_auto_reply_message(message):
                continue
            if self._is_processing_ack_message(message):
                continue
            if self._is_system_or_notification_message(message):
                continue
            return True
        return False

    def _candidate_source_messages(
        self,
        conversation: DingTalkConversation,
        context_messages: list[DingTalkMessage],
        unread_messages: list[DingTalkMessage],
        mentioned_messages: list[DingTalkMessage] | None = None,
    ) -> list[DingTalkMessage]:
        if conversation.single_chat:
            return self._single_chat_candidate_source_messages(
                context_messages,
                unread_messages,
            )
        if not unread_messages and not mentioned_messages:
            return self._group_recovered_candidate_source_messages(context_messages)
        mentioned_message_ids = {
            message.open_message_id for message in mentioned_messages or []
        }
        recovery_start_time = (
            DingTalkAutoReplyWorker._group_context_recovery_start_time(unread_messages)
        )
        unread_message_ids = {message.open_message_id for message in unread_messages}
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for message in [*context_messages, *unread_messages]:
            if message.open_message_id in seen_message_ids:
                continue
            if (
                not mentioned_message_ids
                and message.open_message_id not in unread_message_ids
                and (
                    recovery_start_time is None
                    or message.create_time < recovery_start_time
                )
            ):
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        for message in sorted(
            mentioned_messages or [], key=lambda item: item.create_time
        ):
            if message.open_message_id in seen_message_ids:
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        return result

    def _group_recovered_candidate_source_messages(
        self,
        context_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        latest_seen_context_time: str | None = None
        for message in context_messages:
            if self.store.has_seen(message.open_message_id):
                latest_seen_context_time = max(
                    latest_seen_context_time or message.create_time,
                    message.create_time,
                )
        if latest_seen_context_time is None:
            return []
        return sorted(
            [
                message
                for message in context_messages
                if message.create_time > latest_seen_context_time
                and not self.store.has_seen(message.open_message_id)
            ],
            key=lambda message: message.create_time,
        )

    def _single_chat_candidate_source_messages(
        self,
        context_messages: list[DingTalkMessage],
        unread_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()

        def add(message: DingTalkMessage) -> None:
            if message.open_message_id in seen_message_ids:
                return
            seen_message_ids.add(message.open_message_id)
            result.append(message)

        for message in unread_messages:
            add(message)

        has_seen_context = any(
            self.store.has_seen(message.open_message_id)
            for message in context_messages
        )
        if not has_seen_context:
            return sorted(result, key=lambda message: message.create_time)

        for message in context_messages:
            if self.store.has_seen(message.open_message_id):
                continue
            add(message)
        return sorted(result, key=lambda message: message.create_time)

    @staticmethod
    def _group_context_recovery_start_time(
        unread_messages: list[DingTalkMessage],
    ) -> str | None:
        if not unread_messages:
            return None
        earliest_unread_time = min(
            datetime.strptime(message.create_time, DINGTALK_TIME_FORMAT)
            for message in unread_messages
        )
        return (earliest_unread_time - GROUP_CONTEXT_RECOVERY_WINDOW).strftime(
            DINGTALK_TIME_FORMAT
        )

    def _is_current_user_message_for_candidate_filter(
        self, message: DingTalkMessage
    ) -> bool:
        current_user_id = self.store.get_current_user_id()
        if current_user_id and message.sender_user_id:
            return message.sender_user_id == current_user_id
        if current_user_id and message.sender_open_dingtalk_id:
            profile = self.store.find_org_user_by_open_dingtalk_id(
                message.sender_open_dingtalk_id
            )
            return profile is not None and profile.user_id == current_user_id
        return False

    @staticmethod
    def _is_split_person_auto_reply_message(message: DingTalkMessage) -> bool:
        return SPLIT_PERSON_SIGNATURE in message.content

    @staticmethod
    def _is_processing_ack_message(message: DingTalkMessage) -> bool:
        return message.content.strip() == PROCESSING_ACK

    @staticmethod
    def _prompt_context_messages(
        previous_messages: list[DingTalkMessage],
        unread_messages: list[DingTalkMessage],
        previous_limit: int = 20,
    ) -> list[DingTalkMessage]:
        previous_messages = sorted(
            previous_messages,
            key=lambda message: datetime.strptime(
                message.create_time, DINGTALK_TIME_FORMAT
            ),
        )
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for message in [*previous_messages[-previous_limit:], *unread_messages]:
            if DingTalkAutoReplyWorker._is_processing_ack_message(message):
                continue
            if message.open_message_id in seen_message_ids:
                continue
            seen_message_ids.add(message.open_message_id)
            result.append(message)
        return result

    def _process_batch(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        *,
        ignore_existing_attempt: bool = False,
        raise_on_delivery_failure: bool = False,
        calendar_response_event: DwsCalendarEvent | None = None,
        comment_target_messages: list[DingTalkMessage] | None = None,
        allow_duplicate_send: bool = False,
        complete_task_id: int | None = None,
    ) -> None:
        trigger = new_messages[-1]
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            new_messages,
            raise_on_delivery_failure=raise_on_delivery_failure,
        ):
            return
        material_messages = comment_target_messages or new_messages
        material_references = self._material_references(
            material_messages, context_messages
        )
        image_paths, image_download_errors = self._collect_image_paths(
            material_messages,
            context_messages,
        )
        linked_documents: list[LinkedDocumentContext] = []
        if calendar_response_event is not None:
            linked_documents = self._read_calendar_linked_documents(
                material_messages, context_messages
            )
        session_id = None
        if not ignore_existing_attempt:
            session_id = self.store.get_codex_session_id(
                conversation.open_conversation_id
            )
        prompt_context_messages = (
            self._resume_prompt_context_messages(context_messages, new_messages)
            if session_id
            else context_messages
        )
        prompt = self._build_prompt(
            conversation,
            new_messages,
            prompt_context_messages,
            include_thread_prompt=session_id is None,
            linked_documents=linked_documents,
            material_references=material_references,
            image_download_errors=image_download_errors,
        )
        before_session_id = getattr(self.codex, "last_session_id", None)
        decision = self.codex.decide(
            prompt=prompt,
            session_id=session_id,
            image_paths=image_paths,
        )
        decision = self._normalize_codex_decision(decision)
        resume_attempts = 1
        while (
            resume_attempts < STALE_CODEX_RESUME_ATTEMPTS
            and self._is_stale_codex_resume(decision, session_id)
        ):
            resume_attempts += 1
            before_session_id = getattr(self.codex, "last_session_id", None)
            decision = self.codex.decide(
                prompt=prompt,
                session_id=session_id,
                image_paths=image_paths,
            )
            decision = self._normalize_codex_decision(decision)
        if self._is_stale_codex_resume(decision, session_id):
            self.store.clear_codex_session(conversation.open_conversation_id)
            session_id = None
            prompt = self._build_prompt(
                conversation,
                new_messages,
                context_messages,
                include_thread_prompt=True,
                linked_documents=linked_documents,
                material_references=material_references,
                image_download_errors=image_download_errors,
            )
            before_session_id = getattr(self.codex, "last_session_id", None)
            decision = self.codex.decide(
                prompt=prompt,
                session_id=None,
                image_paths=image_paths,
            )
            decision = self._normalize_codex_decision(decision)
        if self._single_chat_material_no_reply_needs_retry(
            conversation,
            material_references,
            decision,
        ):
            decision = self.codex.decide(
                prompt=self._single_chat_material_retry_prompt(),
                session_id=getattr(self.codex, "last_session_id", None) or session_id,
                image_paths=image_paths,
            )
            decision = self._normalize_codex_decision(decision)
        after_session_id = getattr(self.codex, "last_session_id", None)
        self._persist_codex_session_id(
            conversation,
            before_session_id=before_session_id,
            after_session_id=after_session_id,
        )
        attempt_session_id = after_session_id or session_id or ""
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action=decision.action.value,
            sensitivity_kind=decision.sensitivity_kind.value,
            codex_reason=decision.reason,
            draft_reply_text=decision.reply_text,
            codex_session_id=attempt_session_id,
            codex_transcript_start_line=getattr(
                self.codex, "last_transcript_start_line", 0
            ),
            codex_transcript_end_line=getattr(
                self.codex, "last_transcript_end_line", 0
            ),
            audit_documents_json=json.dumps(
                decision.audit_documents,
                ensure_ascii=False,
            ),
            audit_tool_events_json=json.dumps(
                getattr(self.codex, "last_audit_tool_events", []),
                ensure_ascii=False,
            ),
            audit_summary=decision.audit_summary,
            calendar_event_id=(
                calendar_response_event.event_id if calendar_response_event else ""
            ),
        )

        calendar_response_status = decision.calendar_response_status.value
        if calendar_response_event is not None and calendar_response_status:
            if decision.action == CodexAction.NO_REPLY:
                if calendar_response_status == "accepted":
                    accepted = self._execute_calendar_response(
                        conversation=conversation,
                        trigger=trigger,
                        event=calendar_response_event,
                        response_status=calendar_response_status,
                        attempt_id=attempt_id,
                        mark_attempt_terminal=True,
                        raise_on_delivery_failure=raise_on_delivery_failure,
                        complete_task_id=None,
                    )
                    if not accepted:
                        return
                    self.store.update_reply_attempt(
                        attempt_id,
                        action=CodexAction.SEND_REPLY.value,
                    )
                    self._send_reply(
                        conversation=conversation,
                        trigger=trigger,
                        new_messages=new_messages,
                        reply_text=decision.reason or "已接受这个日程。",
                        reason=decision.reason,
                        attempt_id=attempt_id,
                        system_actions=decision.system_actions,
                        comment_target_messages=comment_target_messages,
                        raise_on_delivery_failure=raise_on_delivery_failure,
                        allow_duplicate_send=allow_duplicate_send,
                    )
                    return
                self._respond_calendar_invite(
                    conversation=conversation,
                    trigger=trigger,
                    new_messages=new_messages,
                    event=calendar_response_event,
                    response_status=calendar_response_status,
                    attempt_id=attempt_id,
                    reason=decision.reason,
                    raise_on_delivery_failure=raise_on_delivery_failure,
                    allow_duplicate_send=allow_duplicate_send,
                    complete_task_id=complete_task_id,
                )
                return
            calendar_response_succeeded = self._execute_calendar_response(
                conversation=conversation,
                trigger=trigger,
                event=calendar_response_event,
                response_status=calendar_response_status,
                attempt_id=attempt_id,
                mark_attempt_terminal=True,
                raise_on_delivery_failure=raise_on_delivery_failure,
                complete_task_id=None,
            )
            if not calendar_response_succeeded:
                return

        if self._queue_okr_review_actions(decision):
            self._queue_okr_review_from_decision(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                attempt_id=attempt_id,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            return

        if decision.action == CodexAction.NO_REPLY:
            if self._message_reaction_actions(decision):
                self._execute_message_reactions(
                    conversation=conversation,
                    trigger=trigger,
                    new_messages=new_messages,
                    attempt_id=attempt_id,
                    decision=decision,
                    raise_on_delivery_failure=raise_on_delivery_failure,
                )
                return
            self.store.update_reply_attempt(
                attempt_id,
                send_status="skipped",
                send_error="no_reply",
            )
            self._mark_seen(new_messages)
            return
        if decision.action == CodexAction.STOP_WITH_ERROR:
            login_required = _is_codex_login_required_error(decision.reason)
            critical_info_unavailable = self._is_critical_info_unavailable_reason(
                decision.reason
            )
            send_error = (
                f"{CODEX_LOGIN_REQUIRED_PREFIX}: {decision.reason}"
                if login_required
                else decision.reason
            )
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked" if login_required else "failed",
                send_error=send_error,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "codex",
                send_error,
            )
            if critical_info_unavailable and raise_on_delivery_failure:
                raise CriticalInformationUnavailableError(send_error)
            self._notify(
                title=(
                    f"CEO agent blocked: {conversation.title}"
                    if login_required
                    else f"CEO agent error: {conversation.title}"
                ),
                message=send_error[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyTaskProcessingError(send_error)
            return
        if decision.action == CodexAction.HANDOFF_TO_HUMAN:
            if self.dry_run:
                self.store.update_reply_attempt(
                    attempt_id,
                    send_status="dry_run",
                    send_error="message_reaction",
                )
                self._notify(
                    title=f"CEO handoff: {conversation.title}",
                    message=trigger.content[:120],
                    conversation=conversation,
                )
                return
            reacted = self._execute_message_reactions(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                attempt_id=attempt_id,
                decision=self._handoff_reaction_decision(decision),
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            if not reacted:
                return
            handoff_notified_locally = self._notify_handoff(
                conversation=conversation,
                trigger=trigger,
                context_messages=context_messages,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "handoff",
                decision.reason,
            )
            if not handoff_notified_locally:
                self._notify(
                    title=f"CEO handoff: {conversation.title}",
                    message=trigger.content[:120],
                    conversation=conversation,
                )
            return

        permission = self.permission_gate.evaluate(decision, trigger)
        self.store.update_reply_attempt(
            attempt_id,
            permission_action=permission.action.value,
            permission_reason=permission.reason,
        )
        if permission.action == PermissionAction.ERROR:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=permission.reason,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "permission",
                permission.reason,
            )
            self._notify(
                title=f"CEO permission error: {conversation.title}",
                message=permission.reason[:120],
                conversation=conversation,
            )
            return
        if permission.action == PermissionAction.REPLY:
            self._send_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                reply_text=permission.reply_text,
                reason=permission.reason,
                attempt_id=attempt_id,
                system_actions=decision.system_actions,
                comment_target_messages=comment_target_messages,
                raise_on_delivery_failure=raise_on_delivery_failure,
                allow_duplicate_send=allow_duplicate_send,
            )
            return

        self._send_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            reply_text=decision.reply_text,
            reason=decision.reason,
            attempt_id=attempt_id,
            system_actions=decision.system_actions,
            comment_target_messages=comment_target_messages,
            raise_on_delivery_failure=raise_on_delivery_failure,
            allow_duplicate_send=allow_duplicate_send,
        )

    @staticmethod
    def _message_reaction_actions(decision: CodexDecision) -> list[dict]:
        return [
            action
            for action in decision.system_actions
            if isinstance(action, dict)
            and action.get("type") == "dws_message_reaction"
        ]

    @staticmethod
    def _handoff_reaction_decision(decision: CodexDecision) -> CodexDecision:
        return CodexDecision(
            action=CodexAction.NO_REPLY,
            reason=decision.reason,
            sensitivity_kind=decision.sensitivity_kind,
            system_actions=[
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "text_emotion",
                    "text": HANDOFF_TEXT_EMOTION,
                }
            ],
            audit_documents=decision.audit_documents,
            audit_summary=decision.audit_summary,
        )

    def _execute_message_reactions(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        decision: CodexDecision,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        actions = self._message_reaction_actions(decision)
        if not actions:
            return False
        if self.dry_run:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="dry_run",
                send_error="message_reaction",
            )
            return True
        events: list[dict[str, str]] = []
        summaries: list[str] = []
        try:
            for index, action in enumerate(actions, start=1):
                call_id = f"message_reaction_{index}"
                result = self._execute_message_reaction(
                    conversation=conversation,
                    trigger=trigger,
                    action=action,
                    call_id=call_id,
                    events=events,
                )
                summaries.append(self._message_reaction_summary(action))
                events.append(
                    {
                        "tool": "tool_output",
                        "call_id": call_id,
                        "output": json.dumps(result or {}, ensure_ascii=False),
                    }
                )
        except Exception as exc:
            self._append_attempt_audit_tool_events(attempt_id, events)
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "message_reaction",
                str(exc),
            )
            self._notify(
                title=f"CEO message reaction failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
                attempt_id=attempt_id,
            )
            if raise_on_delivery_failure:
                raise ReplyTaskProcessingError(str(exc)) from exc
            return False
        self._append_attempt_audit_tool_events(attempt_id, events)
        self.store.update_reply_attempt(
            attempt_id,
            send_status="reacted",
            send_error=", ".join(summaries),
            retry_count=0,
        )
        self._mark_seen(new_messages)
        return True

    def _execute_message_reaction(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        action: dict,
        call_id: str,
        events: list[dict[str, str]],
    ) -> dict:
        reaction_type = str(action.get("reaction_type") or "emoji").strip()
        if reaction_type == "emoji":
            emoji = str(action.get("emoji") or "").strip()
            command = [
                "dws",
                "chat",
                "message",
                "add-emoji",
                "--group",
                conversation.open_conversation_id,
                "--msg-id",
                trigger.open_message_id,
                "--emoji",
                emoji,
                "--format",
                "json",
                "--yes",
            ]
            events.append(
                {
                    "tool": "dws",
                    "call_id": call_id,
                    "command": " ".join(command),
                    "input": json.dumps(
                        {
                            "conversation_id": conversation.open_conversation_id,
                            "message_id": trigger.open_message_id,
                            "emoji": emoji,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return self.dws.add_message_emoji(
                conversation.open_conversation_id,
                trigger.open_message_id,
                emoji,
            )
        if reaction_type == "text_emotion":
            text = str(action.get("text") or "").strip()
            emotion_id = str(action.get("emotion_id") or "").strip()
            emotion_name = str(action.get("emotion_name") or "").strip() or text
            background_id = (
                str(action.get("background_id") or "").strip()
                or DEFAULT_TEXT_EMOTION_BACKGROUND_ID
            )
            if not emotion_id:
                create_command = [
                    "dws",
                    "chat",
                    "message",
                    "create-text-emotion",
                    "--text",
                    text,
                    "--emotion-name",
                    emotion_name,
                    "--background-id",
                    background_id,
                    "--format",
                    "json",
                    "--yes",
                ]
                events.append(
                    {
                        "tool": "dws",
                        "call_id": f"{call_id}_create",
                        "command": " ".join(create_command),
                        "input": json.dumps(
                            {
                                "text": text,
                                "emotion_name": emotion_name,
                                "background_id": background_id,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                create_result = self.dws.create_message_text_emotion(
                    text=text,
                    emotion_name=emotion_name,
                    background_id=background_id,
                )
                events[-1]["output"] = json.dumps(
                    create_result,
                    ensure_ascii=False,
                )
                emotion_id = _extract_text_emotion_id(create_result)
                if not emotion_id:
                    raise ValueError("create-text-emotion returned no emotion id")
                background_id = (
                    _extract_text_emotion_background_id(create_result)
                    or background_id
                )
            command = [
                "dws",
                "chat",
                "message",
                "add-text-emotion",
                "--group",
                conversation.open_conversation_id,
                "--msg-id",
                trigger.open_message_id,
                "--text",
                text,
                "--emotion-id",
                emotion_id,
                "--emotion-name",
                emotion_name,
                "--background-id",
                background_id,
                "--format",
                "json",
                "--yes",
            ]
            events.append(
                {
                    "tool": "dws",
                    "call_id": call_id,
                    "command": " ".join(command),
                    "input": json.dumps(
                        {
                            "conversation_id": conversation.open_conversation_id,
                            "message_id": trigger.open_message_id,
                            "text": text,
                            "emotion_id": emotion_id,
                            "emotion_name": emotion_name,
                            "background_id": background_id,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return self.dws.add_message_text_emotion(
                conversation.open_conversation_id,
                trigger.open_message_id,
                text=text,
                emotion_id=emotion_id,
                emotion_name=emotion_name,
                background_id=background_id,
            )
        raise ValueError(f"unsupported message reaction type: {reaction_type}")

    @staticmethod
    def _message_reaction_summary(action: dict) -> str:
        reaction_type = str(action.get("reaction_type") or "emoji").strip()
        if reaction_type == "emoji":
            return f"emoji: {str(action.get('emoji') or '').strip()}"
        return f"text_emotion: {str(action.get('text') or '').strip()}"

    def _append_attempt_audit_tool_events(
        self,
        attempt_id: int,
        events: list[dict[str, str]],
    ) -> None:
        if not events:
            return
        attempt = self.store.get_reply_attempt(attempt_id)
        if attempt is None:
            return
        try:
            existing = json.loads(attempt.audit_tool_events_json or "[]")
        except json.JSONDecodeError:
            existing = []
        if not isinstance(existing, list):
            existing = []
        self.store.update_reply_attempt(
            attempt_id,
            audit_tool_events_json=json.dumps(
                [*existing, *events],
                ensure_ascii=False,
            ),
        )

    def _handle_existing_attempt(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        *,
        ignore_system_notification_skip: bool = False,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        sent_reply = self.store.get_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
        )
        if sent_reply is not None:
            self._mark_seen(new_messages)
            return True
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            trigger.open_message_id,
        )
        if attempt is None:
            return False
        if (
            ignore_system_notification_skip
            and attempt.send_status == "skipped"
            and attempt.codex_reason == "system_or_notification_message"
        ):
            return False
        if attempt.send_status in {
            "sent",
            "skipped",
            "blocked",
            "commented",
            CALENDAR_ACTION_SEND_STATUS,
        }:
            self._mark_seen(new_messages)
            return True
        if attempt.send_status == "dry_run":
            if self.dry_run:
                return True
            return self._retry_existing_reply_attempt(
                conversation,
                trigger,
                new_messages,
                attempt,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
        if attempt.send_status in {"failed", "pending"}:
            if self._retry_existing_reply_attempt(
                conversation,
                trigger,
                new_messages,
                attempt,
                raise_on_delivery_failure=raise_on_delivery_failure,
            ):
                return True
            if raise_on_delivery_failure:
                raise ReplyTaskProcessingError(
                    attempt.send_error or attempt.codex_reason or attempt.action
                )
            return False
        return False

    def _read_calendar_linked_documents(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[LinkedDocumentContext]:
        documents: list[LinkedDocumentContext] = []
        referenced_messages = self._referenced_document_messages(
            new_messages, context_messages
        )
        for task_uuid in self._dingtalk_minutes_ids(referenced_messages):
            try:
                documents.append(self._read_linked_minutes(task_uuid))
            except Exception as exc:
                documents.append(self._linked_document_read_failure_context(task_uuid, exc))
        for url in self._dingtalk_doc_urls(referenced_messages):
            try:
                documents.append(self._read_linked_alidocs_node(url))
            except Exception as exc:
                documents.append(self._linked_document_read_failure_context(url, exc))
        for file_name in self._referenced_file_names(new_messages, context_messages):
            try:
                document = self._read_referenced_file(file_name)
            except Exception as exc:
                documents.append(self._linked_document_read_failure_context(file_name, exc))
                continue
            if document is not None:
                documents.append(document)
        return documents

    @staticmethod
    def _linked_document_read_failure_context(
        reference: str, error: Exception
    ) -> LinkedDocumentContext:
        return LinkedDocumentContext(
            url=reference,
            title="钉钉材料读取失败",
            markdown=(
                "材料读取失败: 当前账号未能读取这份钉钉材料。\n"
                "处理要求: agent 不能臆测材料内容；如果判断依赖材料正文，"
                "应说明权限或读取问题，并要求补充正文或开放权限。\n"
                f"错误: {str(error)}"
            ),
        )

    def _material_references(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[MaterialReferenceContext]:
        references: list[MaterialReferenceContext] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, reference: str, message: DingTalkMessage) -> None:
            if not reference:
                return
            key = (kind, reference)
            if key in seen:
                return
            seen.add(key)
            references.append(
                MaterialReferenceContext(
                    kind=kind,
                    reference=reference,
                    source_message_id=message.open_message_id,
                    source_sender=message.sender_name,
                    source_time=message.create_time,
                )
            )

        for message in self._referenced_document_messages(
            new_messages, context_messages
        ):
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_DOC_URL_PATTERN.finditer(text):
                    add(
                        "dingtalk_doc",
                        self._canonical_doc_url(match.group(0)),
                        message,
                    )
                for match in DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.finditer(text):
                    add(
                        "dingtalk_minutes",
                        self._minutes_task_uuid_from_selector_url(match.group(0)),
                        message,
                    )
                for match in DINGTALK_MINUTES_LINK_PATTERN.finditer(text):
                    add(
                        "dingtalk_minutes",
                        self._minutes_task_uuid_from_url(match.group(0)),
                        message,
                    )

        file_names = self._referenced_file_names(new_messages, context_messages)
        if file_names:
            file_source_by_name = self._referenced_file_source_messages(
                new_messages, context_messages
            )
            fallback_source = new_messages[-1] if new_messages else None
            for file_name in file_names:
                source = file_source_by_name.get(file_name) or fallback_source
                if source is not None:
                    add("dingtalk_file", file_name, source)
        return references

    @classmethod
    def _referenced_file_source_messages(
        cls,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> dict[str, DingTalkMessage]:
        sources: dict[str, DingTalkMessage] = {}

        def add_from_text(text: str | None, source: DingTalkMessage) -> None:
            if not text:
                return
            match = FILE_MESSAGE_PATTERN.match(text.strip())
            if not match:
                return
            file_name = match.group("name").strip()
            if file_name and file_name not in sources:
                sources[file_name] = source

        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }
        trigger = new_messages[-1] if new_messages else None
        for message in new_messages:
            add_from_text(message.content, message)
            if (
                message.quoted_message_id
                and message.quoted_message_id in context_by_message_id
            ):
                add_from_text(
                    context_by_message_id[message.quoted_message_id].content,
                    context_by_message_id[message.quoted_message_id],
                )
            else:
                add_from_text(message.quoted_content, message)

        if trigger is None:
            return sources

        trigger_time = datetime.strptime(trigger.create_time, DINGTALK_TIME_FORMAT)
        window_start = trigger_time - REFERENCED_FILE_CONTEXT_WINDOW
        for message in context_messages:
            if message.sender_name != trigger.sender_name:
                continue
            try:
                message_time = datetime.strptime(
                    message.create_time, DINGTALK_TIME_FORMAT
                )
            except ValueError:
                continue
            if window_start <= message_time <= trigger_time:
                add_from_text(message.content, message)
        return sources

    def _collect_image_paths(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> tuple[list[Path], list[str]]:
        image_paths: list[Path] = []
        image_download_errors: list[str] = []
        seen_sources: set[str] = set()
        for message in self._referenced_document_messages(new_messages, context_messages):
            for source_key, payload in self._message_image_sources(message):
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                try:
                    image_path = self._download_message_image(message, payload)
                except Exception as exc:
                    detail = self._image_download_error_detail(message, payload, str(exc))
                    self.store.record_error(
                        message.open_conversation_id,
                        message.open_message_id,
                        "image_download",
                        detail,
                    )
                    image_download_errors.append(detail)
                    continue
                if image_path is None:
                    detail = self._image_download_error_detail(
                        message,
                        payload,
                        "no download URL returned",
                    )
                    self.store.record_error(
                        message.open_conversation_id,
                        message.open_message_id,
                        "image_download",
                        detail,
                    )
                    image_download_errors.append(detail)
                    continue
                image_paths.append(image_path)
        return image_paths, image_download_errors

    @staticmethod
    def _image_download_error_detail(
        message: DingTalkMessage,
        payload: dict[str, str],
        error: str,
    ) -> str:
        source = payload.get("media_id") or payload.get("download_code") or payload.get("url")
        source_text = f" resource {source}" if source else ""
        return f"{message.open_message_id}:{source_text} error {error}"

    def _message_image_sources(
        self,
        message: DingTalkMessage,
    ) -> list[tuple[str, dict[str, str]]]:
        sources: list[tuple[str, dict[str, str]]] = []
        for text in (message.content, message.quoted_content or ""):
            for match in IMAGE_MESSAGE_MEDIA_ID_PATTERN.finditer(text):
                media_id = match.group("media_id").strip()
                if media_id:
                    sources.append(
                        (
                            f"media:{message.open_message_id}:{media_id}",
                            {"kind": "media_id", "media_id": media_id},
                        )
                    )
            for match in MARKDOWN_IMAGE_URL_PATTERN.finditer(text):
                url = match.group("url").strip()
                if url:
                    sources.append(
                        (
                            f"url:{url}",
                            {"kind": "url", "url": url},
                        )
                    )
        for download_code in self._download_codes_from_payload(message.raw_payload):
            sources.append(
                (
                    f"download_code:{message.open_message_id}:{download_code}",
                    {"kind": "download_code", "download_code": download_code},
                )
            )
        return sources

    def _download_message_image(
        self,
        message: DingTalkMessage,
        payload: dict[str, str],
    ) -> Path | None:
        kind = payload.get("kind")
        if kind == "url":
            url = payload["url"]
        elif kind == "media_id":
            download_payload = self.dws.get_resource_download_url(
                message.open_conversation_id,
                message.open_message_id,
                payload["media_id"],
                "mediaId",
            )
            local_path = self._local_path_from_payload(download_payload)
            if local_path:
                try:
                    data = local_path.read_bytes()
                finally:
                    local_path.unlink(missing_ok=True)
                return self._write_message_image(message, str(local_path), data)
            url = self._download_url_from_payload(download_payload)
        elif kind == "download_code":
            download_payload = self.dws.download_robot_message_file(
                payload["download_code"]
            )
            url = self._download_url_from_payload(download_payload)
        else:
            return None
        if not url:
            return None
        data = self._download_image_bytes(url)
        return self._write_message_image(message, url, data)

    @classmethod
    def _download_codes_from_payload(cls, payload: object) -> list[str]:
        codes: list[str] = []

        def walk(value: object) -> None:
            if isinstance(value, dict):
                code = value.get("downloadCode") or value.get("pictureDownloadCode")
                if isinstance(code, str) and code.strip():
                    codes.append(code.strip())
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return codes

    @staticmethod
    def _download_url_from_payload(payload: object) -> str:
        if isinstance(payload, dict):
            for key in ("downloadUrl", "resourceUrl", "url"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in payload.values():
                url = DingTalkAutoReplyWorker._download_url_from_payload(value)
                if url:
                    return url
        if isinstance(payload, list):
            for value in payload:
                url = DingTalkAutoReplyWorker._download_url_from_payload(value)
                if url:
                    return url
        return ""

    @staticmethod
    def _local_path_from_payload(payload: object) -> Path | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get("localPath")
        if not isinstance(value, str) or not value.strip():
            return None
        path = Path(value)
        if not path.exists() or not path.is_file():
            return None
        if path.stat().st_size > DOWNLOADED_IMAGE_MAX_BYTES:
            raise DwsError("dingtalk_image_too_large")
        return path

    def _download_image_bytes(self, url: str) -> bytes:
        data = self._download_resource_bytes(url, {})
        if len(data) > DOWNLOADED_IMAGE_MAX_BYTES:
            raise DwsError("dingtalk_image_too_large")
        return data

    def _write_message_image(
        self,
        message: DingTalkMessage,
        url: str,
        data: bytes,
    ) -> Path:
        image_dir = self.store.path.parent / "image-attachments"
        image_dir.mkdir(parents=True, exist_ok=True)
        suffix = self._image_suffix(url, data)
        safe_message_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", message.open_message_id)
        path = image_dir / f"{safe_message_id}_{len(data)}{suffix}"
        path.write_bytes(data)
        return path

    @staticmethod
    def _image_suffix(url: str, data: bytes) -> str:
        path = urlsplit(url).path.lower()
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            if path.endswith(suffix):
                return suffix
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return ".gif"
        return ".img"

    def _read_linked_alidocs_node(self, url: str) -> LinkedDocumentContext:
        info = self.dws.doc_info(url)
        extension = str(info.get("extension") or "").lower()
        content_type = str(info.get("contentType") or "").upper()
        if content_type == "ALIDOC" and extension == "adoc":
            payload = self.dws.read_doc(url)
            title = str(payload.get("title") or info.get("name") or "钉钉文档")
            markdown = str(payload.get("markdown") or "")
            if not markdown.strip():
                raise DwsError(f"DingTalk doc read returned empty markdown: {url}")
            return LinkedDocumentContext(url=url, title=title, markdown=markdown)
        if content_type == "ALIDOC" and extension == "able":
            return self._read_linked_aitable(url, info)
        return LinkedDocumentContext(
            url=url,
            title=str(info.get("name") or "钉钉材料"),
            markdown=(
                "该链接不是钉钉在线文档，不能使用文档正文读取。\n"
                f"材料类型: {content_type or 'unknown'}\n"
                f"扩展名: {extension or 'unknown'}\n"
                "如果新消息要求审核或判断该材料正文，需要取得对应类型的可读内容后再回复。"
            ),
        )

    def _read_linked_aitable(
        self, url: str, info: dict[str, object]
    ) -> LinkedDocumentContext:
        base_id = str(info.get("nodeId") or self._alidocs_node_id(url))
        base_payload = self.dws.get_aitable_base(base_id)
        base_data = self._payload_data(base_payload)
        base_name = str(base_data.get("baseName") or info.get("name") or "AI表格")
        table_summaries = self._aitable_tables_from_payload(base_payload)
        table_ids = [
            str(table.get("tableId"))
            for table in table_summaries
            if table.get("tableId")
        ][:AITABLE_TABLE_PREVIEW_LIMIT]
        table_payload = self.dws.get_aitable_tables(base_id, table_ids or None)
        tables = self._aitable_tables_from_payload(table_payload) or table_summaries
        lines = [f"AI表格: {base_name}", "说明: 该链接是 AI 表格，不是钉钉在线文档。"]
        for table in tables[:AITABLE_TABLE_PREVIEW_LIMIT]:
            table_id = str(table.get("tableId") or "")
            table_name = str(table.get("tableName") or "未命名数据表")
            description = str(table.get("description") or table.get("tableDescription") or "")
            fields = table.get("fields") if isinstance(table.get("fields"), list) else []
            field_names = [
                str(field.get("fieldName"))
                for field in fields
                if isinstance(field, dict) and field.get("fieldName")
            ]
            lines.append(f"\n数据表: {table_name}")
            if description:
                lines.append(f"描述: {description}")
            if field_names:
                lines.append(f"字段: {', '.join(field_names)}")
            if table_id:
                records_payload = self.dws.query_aitable_records(
                    base_id,
                    table_id,
                    limit=AITABLE_RECORD_PREVIEW_LIMIT,
                )
                record_lines = self._format_aitable_records(records_payload, fields)
                if record_lines:
                    lines.append("记录预览:")
                    lines.extend(record_lines)
        return LinkedDocumentContext(
            url=url,
            title=base_name,
            markdown="\n".join(lines),
        )

    def _read_linked_minutes(self, task_uuid: str) -> LinkedDocumentContext:
        info = self.dws.get_minutes_info(task_uuid)
        summary = self.dws.get_minutes_summary(task_uuid)
        todos = self.dws.get_minutes_todos(task_uuid)
        transcription = self.dws.get_minutes_transcription(task_uuid)
        markdown = self._format_minutes_material(
            task_uuid,
            info,
            summary,
            todos,
            transcription,
        )
        title = self._minutes_title(info) or f"AI 听记 {task_uuid}"
        return LinkedDocumentContext(
            url=self._minutes_url(info),
            title=title,
            markdown=markdown,
        )

    @classmethod
    def _format_minutes_material(
        cls,
        task_uuid: str,
        info: dict[str, object],
        summary: dict[str, object],
        todos: dict[str, object],
        transcription: dict[str, object],
    ) -> str:
        lines = [
            "AI 听记材料:",
            f"taskUuid: {task_uuid}",
        ]
        title = cls._minutes_title(info)
        if title:
            lines.append(f"标题: {title}")
        url = cls._minutes_url(info)
        if url:
            lines.append(f"链接: {url}")
        summary_text = cls._minutes_summary_text(summary)
        if summary_text:
            lines.extend(["", "摘要:", summary_text[:MINUTES_SUMMARY_MAX_CHARS]])
        todo_lines = cls._minutes_todo_lines(todos)
        if todo_lines:
            lines.extend(["", "处理事项:"])
            lines.extend(todo_lines)
        transcription_lines = cls._minutes_transcription_lines(transcription)
        if transcription_lines:
            lines.extend(["", "文字稿预览:"])
            lines.extend(transcription_lines)
        return "\n".join(lines)

    @classmethod
    def _minutes_title(cls, payload: dict[str, object]) -> str:
        data = cls._payload_data(payload)
        title = data.get("title")
        return str(title).strip() if title else ""

    @classmethod
    def _minutes_url(cls, payload: dict[str, object]) -> str:
        data = cls._payload_data(payload)
        url = data.get("url")
        return str(url).strip() if url else ""

    @classmethod
    def _minutes_summary_text(cls, payload: dict[str, object]) -> str:
        data = cls._payload_data(payload)
        for key in ("fullSummary", "summary", "markdown", "content", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _minutes_todo_lines(cls, payload: dict[str, object]) -> list[str]:
        data = cls._payload_data(payload)
        values: list[str] = []
        actions = data.get("actions")
        if isinstance(actions, list):
            for action in actions:
                text = cls._minutes_todo_text(action)
                if text:
                    values.append(text)
        todo_list = data.get("dingtalkTodoList")
        if isinstance(todo_list, list):
            for item in todo_list:
                text = cls._minutes_todo_text(item)
                if text:
                    values.append(text)
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(f"- {value}")
        return result

    @classmethod
    def _minutes_todo_text(cls, value: object) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    return stripped
                return cls._minutes_todo_text(decoded)
            return stripped
        if isinstance(value, dict):
            for key in ("title", "value", "content", "text"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    @classmethod
    def _minutes_transcription_lines(cls, payload: dict[str, object]) -> list[str]:
        data = cls._payload_data(payload)
        paragraphs = data.get("paragraphList")
        if not isinstance(paragraphs, list):
            return []
        lines: list[str] = []
        for paragraph in paragraphs[:MINUTES_TRANSCRIPTION_PARAGRAPH_LIMIT]:
            if not isinstance(paragraph, dict):
                continue
            text = str(paragraph.get("paragraph") or "").strip()
            if not text:
                continue
            speaker = str(paragraph.get("nickName") or "发言人").strip()
            lines.append(f"- {speaker}: {text}")
        return lines

    @staticmethod
    def _referenced_document_messages(
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }
        for message in new_messages:
            if message.open_message_id not in seen_message_ids:
                result.append(message)
                seen_message_ids.add(message.open_message_id)
            if (
                message.quoted_message_id
                and message.quoted_message_id in context_by_message_id
                and message.quoted_message_id not in seen_message_ids
            ):
                quoted = context_by_message_id[message.quoted_message_id]
                result.append(quoted)
                seen_message_ids.add(quoted.open_message_id)
        return result

    def _resume_prompt_context_messages(
        self,
        context_messages: list[DingTalkMessage],
        new_messages: list[DingTalkMessage],
        limit: int = 20,
    ) -> list[DingTalkMessage]:
        latest_seen_time: str | None = None
        for message in context_messages:
            if self.store.has_seen(message.open_message_id):
                latest_seen_time = max(
                    latest_seen_time or message.create_time,
                    message.create_time,
                )
        if latest_seen_time is None:
            return self._prompt_context_messages(context_messages, new_messages, limit)
        candidates = [
            message
            for message in [*context_messages, *new_messages]
            if message.create_time > latest_seen_time
        ]
        return self._prompt_context_messages([], candidates, limit)

    @classmethod
    def _referenced_file_names(
        cls,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[str]:
        names: list[str] = []
        seen_names: set[str] = set()

        def add_from_text(text: str | None) -> None:
            if not text:
                return
            match = FILE_MESSAGE_PATTERN.match(text.strip())
            if not match:
                return
            file_name = match.group("name").strip()
            if file_name and file_name not in seen_names:
                seen_names.add(file_name)
                names.append(file_name)

        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }
        trigger = new_messages[-1] if new_messages else None
        for message in new_messages:
            add_from_text(message.content)
            add_from_text(message.quoted_content)
            if (
                message.quoted_message_id
                and message.quoted_message_id in context_by_message_id
            ):
                add_from_text(context_by_message_id[message.quoted_message_id].content)

        if trigger is None:
            return names

        trigger_time = datetime.strptime(trigger.create_time, DINGTALK_TIME_FORMAT)
        window_start = trigger_time - REFERENCED_FILE_CONTEXT_WINDOW
        for message in context_messages:
            if message.sender_name != trigger.sender_name:
                continue
            try:
                message_time = datetime.strptime(
                    message.create_time, DINGTALK_TIME_FORMAT
                )
            except ValueError:
                continue
            if window_start <= message_time <= trigger_time:
                add_from_text(message.content)
        return names

    def _read_referenced_file(self, file_name: str) -> LinkedDocumentContext | None:
        matches = self._matching_document_search_results(
            file_name,
            self.dws.search_documents(file_name, page_size=5),
        )
        if not matches:
            return LinkedDocumentContext(
                url="",
                title=file_name,
                markdown="钉钉文件消息已在上下文中出现，但没有搜索到可访问的文件正文。",
            )
        if len(matches) > 1:
            titles = ", ".join(self._document_display_name(match) for match in matches)
            return LinkedDocumentContext(
                url="",
                title=file_name,
                markdown=f"钉钉文件消息已在上下文中出现，但同名可访问文件不唯一：{titles}。",
            )
        match = matches[0]
        if match.content_type.upper() == "ALIDOC" and match.extension.lower() == "adoc":
            payload = self.dws.read_doc(match.node_id)
            markdown = str(payload.get("markdown") or "")
            if markdown.strip():
                return LinkedDocumentContext(
                    url=match.doc_url,
                    title=str(
                        payload.get("title") or self._document_display_name(match)
                    ),
                    markdown=markdown,
                )
        payload = self.dws.download_doc(match.node_id)
        markdown = self._downloaded_file_markdown(match, payload)
        if markdown.strip():
            return LinkedDocumentContext(
                url=match.doc_url,
                title=self._document_display_name(match) or file_name,
                markdown=markdown,
            )
        return LinkedDocumentContext(
            url=match.doc_url,
            title=self._document_display_name(match) or file_name,
            markdown=(
                "钉钉普通文件已定位，但正文未能读取。"
                f"node_id: {match.node_id}\n"
                f"extension: {match.extension or 'unknown'}\n"
                f"content_type: {match.content_type or 'unknown'}\n"
                "如果新消息要求对文件内容 comments、审核、总结或判断，不能只凭文件名回复。"
            ),
        )

    def _downloaded_file_markdown(
        self, match: DwsDocumentSearchResult, payload: dict
    ) -> str:
        for key in ("markdown", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value

        resource_url = str(payload.get("resourceUrl") or "")
        if not resource_url:
            return ""
        data = self._download_resource_bytes(resource_url, payload.get("headers"))
        extension = match.extension.lower()
        if extension in {"txt", "md", "markdown", "csv", "json"}:
            return self._decode_text_file(data)
        if extension == "pdf":
            return self._extract_pdf_text(data)
        if extension == "docx":
            return self._extract_docx_text(data)
        return ""

    @staticmethod
    def _download_resource_bytes(url: str, headers: object) -> bytes:
        normalized_headers = headers if isinstance(headers, dict) else {}
        request = urllib.request.Request(
            url,
            headers={str(key): str(value) for key, value in normalized_headers.items()},
        )
        with urllib.request.urlopen(
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > DOWNLOADED_FILE_MAX_BYTES:
                raise DwsError("dingtalk_file_too_large")
            data = response.read(DOWNLOADED_FILE_MAX_BYTES + 1)
        if len(data) > DOWNLOADED_FILE_MAX_BYTES:
            raise DwsError("dingtalk_file_too_large")
        return data

    @staticmethod
    def _decode_text_file(data: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @classmethod
    def _extract_pdf_text(cls, data: bytes) -> str:
        reader = PdfReader(BytesIO(data))
        chunks: list[str] = []
        for page_number, page in enumerate(reader.pages[:PDF_TEXT_PAGE_LIMIT], start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(f"第 {page_number} 页:\n{text}")
        if len(reader.pages) > PDF_TEXT_PAGE_LIMIT:
            chunks.append(f"[PDF 超过 {PDF_TEXT_PAGE_LIMIT} 页，后续页面未预读]")
        return "\n\n".join(chunks)

    @staticmethod
    def _extract_docx_text(data: bytes) -> str:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        text = re.sub(r"<w:tab[^>]*/>", "\t", xml)
        text = re.sub(r"</w:p>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return text

    @classmethod
    def _matching_document_search_results(
        cls,
        file_name: str,
        results: list[DwsDocumentSearchResult],
    ) -> list[DwsDocumentSearchResult]:
        expected = cls._normalized_document_name(file_name)
        matches = []
        for result in results:
            candidates = {
                cls._normalized_document_name(result.name),
                cls._normalized_document_name(cls._document_display_name(result)),
            }
            if expected in candidates:
                matches.append(result)
        return matches

    @staticmethod
    def _document_display_name(result: DwsDocumentSearchResult) -> str:
        if not result.extension:
            return result.name
        suffix = f".{result.extension.lstrip('.')}"
        if result.name.endswith(suffix):
            return result.name
        return f"{result.name}{suffix}"

    @staticmethod
    def _normalized_document_name(value: str) -> str:
        return " ".join(value.strip().split()).casefold()

    @classmethod
    def _dingtalk_doc_urls(cls, messages: list[DingTalkMessage]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for message in messages:
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_DOC_URL_PATTERN.finditer(text):
                    url = cls._canonical_doc_url(match.group(0))
                    if url in seen:
                        continue
                    seen.add(url)
                    urls.append(url)
        return urls

    @classmethod
    def _dingtalk_minutes_ids(cls, messages: list[DingTalkMessage]) -> list[str]:
        task_uuids: list[str] = []
        seen: set[str] = set()
        for message in messages:
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.finditer(text):
                    task_uuid = cls._minutes_task_uuid_from_selector_url(
                        match.group(0)
                    )
                    if not task_uuid or task_uuid in seen:
                        continue
                    seen.add(task_uuid)
                    task_uuids.append(task_uuid)
                for match in DINGTALK_MINUTES_LINK_PATTERN.finditer(text):
                    task_uuid = cls._minutes_task_uuid_from_url(match.group(0))
                    if not task_uuid or task_uuid in seen:
                        continue
                    seen.add(task_uuid)
                    task_uuids.append(task_uuid)
        return task_uuids

    @staticmethod
    def _minutes_task_uuid_from_selector_url(url: str) -> str:
        cleaned = DingTalkAutoReplyWorker._clean_link_url(url)
        parsed = urlsplit(cleaned)
        query = parse_qs(parsed.query)
        resource_id = query.get("resourceId", [""])[0]
        return resource_id.strip()

    @classmethod
    def _minutes_comment_target(cls, messages: list[DingTalkMessage]) -> str:
        shanji_urls: list[str] = []
        transcription_urls: list[str] = []
        for message in messages:
            for text in (message.content, message.quoted_content or ""):
                for match in DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.finditer(text):
                    url = cls._clean_link_url(match.group(0))
                    if url and url not in shanji_urls:
                        shanji_urls.append(url)
                for match in DINGTALK_MINUTES_LINK_PATTERN.finditer(text):
                    url = cls._clean_link_url(match.group(0))
                    if (
                        url.startswith("https://shanji.dingtalk.com/")
                        and url not in transcription_urls
                    ):
                        transcription_urls.append(url)
        return (shanji_urls or transcription_urls or [""])[0]

    @staticmethod
    def _clean_link_url(url: str) -> str:
        return url.rstrip(".,;，。；")

    @staticmethod
    def _minutes_task_uuid_from_url(url: str) -> str:
        cleaned = DingTalkAutoReplyWorker._clean_link_url(url)
        parsed = urlsplit(cleaned)
        query = parse_qs(parsed.query)
        minutes_id = query.get("minutesId", [""])[0]
        if minutes_id.strip():
            return minutes_id.strip()
        path = parsed.path.rstrip("/")
        if "/app/transcribes/" in path:
            return path.rsplit("/", 1)[-1].strip()
        return ""

    @staticmethod
    def _payload_data(payload: dict[str, object]) -> dict[str, object]:
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        result = payload.get("result")
        return result if isinstance(result, dict) else payload

    @classmethod
    def _aitable_tables_from_payload(
        cls, payload: dict[str, object]
    ) -> list[dict[str, object]]:
        data = cls._payload_data(payload)
        tables = data.get("tables")
        if isinstance(tables, list):
            return [table for table in tables if isinstance(table, dict)]
        table = data.get("table")
        if isinstance(table, dict):
            return [table]
        return []

    @classmethod
    def _format_aitable_records(
        cls,
        payload: dict[str, object],
        fields: object,
    ) -> list[str]:
        data = cls._payload_data(payload)
        records = data.get("records")
        if not isinstance(records, list):
            return []
        field_names = cls._aitable_field_names(fields)
        lines: list[str] = []
        for index, record in enumerate(records[:AITABLE_RECORD_PREVIEW_LIMIT], start=1):
            if not isinstance(record, dict):
                continue
            cells = record.get("cells")
            if not isinstance(cells, dict) or not cells:
                continue
            cell_parts = []
            for field_id, value in cells.items():
                name = field_names.get(str(field_id), str(field_id))
                rendered = cls._render_aitable_cell(value)
                if rendered:
                    cell_parts.append(f"{name}: {rendered}")
            if cell_parts:
                lines.append(f"- 记录 {index}: " + "；".join(cell_parts))
        return lines

    @staticmethod
    def _aitable_field_names(fields: object) -> dict[str, str]:
        if not isinstance(fields, list):
            return {}
        names: dict[str, str] = {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = field.get("fieldId")
            field_name = field.get("fieldName")
            if field_id and field_name:
                names[str(field_id)] = str(field_name)
        return names

    @classmethod
    def _render_aitable_cell(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (bool, int, float)):
            return str(value)
        if isinstance(value, dict):
            if value.get("name"):
                return str(value["name"])
            if value.get("text"):
                return str(value["text"])
            if value.get("userId") or value.get("corpId"):
                return "用户"
            return cls._compact_json(value)
        if isinstance(value, list):
            rendered = [cls._render_aitable_cell(item) for item in value]
            return ", ".join(item for item in rendered if item)
        return str(value)

    @staticmethod
    def _compact_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _alidocs_node_id(url: str) -> str:
        path = urlsplit(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1]

    @staticmethod
    def _canonical_doc_url(url: str) -> str:
        parts = urlsplit(url.rstrip(".,;，。；"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def _record_linked_document_error(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        error: Exception,
        *,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        error_text = str(error)
        if self._is_linked_document_permission_error(error):
            reason = f"linked_dingtalk_doc_permission_required: {error_text}"
            attempt_id = self.store.record_reply_attempt_for_trigger(
                conversation_id=conversation.open_conversation_id,
                conversation_title=conversation.title,
                trigger_message_id=trigger.open_message_id,
                trigger_sender=trigger.sender_name,
                trigger_text=trigger.content,
                action=CodexAction.ASK_CLARIFYING_QUESTION.value,
                sensitivity_kind="general",
                codex_reason=reason,
                audit_summary="新消息引用的钉钉材料读取权限不足；已请求对方开放权限或重发正文。",
            )
            self._send_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=[trigger],
                reply_text=self._linked_document_permission_request_reply(),
                reason=reason,
                attempt_id=attempt_id,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            return True
        reason = f"linked_dingtalk_doc_read_failed: {error_text}"
        reason = f"{CRITICAL_INFO_UNAVAILABLE_PREFIX} {reason}"
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action=CodexAction.STOP_WITH_ERROR.value,
            sensitivity_kind="general",
            codex_reason=reason,
            audit_summary="新消息包含钉钉文档链接，但读取文档正文失败；按照规则不生成回复。",
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="failed",
            send_error=reason,
        )
        self.store.record_error(
            conversation.open_conversation_id,
            trigger.open_message_id,
            "linked_dingtalk_doc_read",
            error_text,
        )
        if raise_on_delivery_failure:
            raise CriticalInformationUnavailableError(reason)
        self._notify(
            title=f"CEO doc read failed: {conversation.title}",
            message=error_text[:120],
            conversation=conversation,
        )
        return False

    @staticmethod
    def _is_linked_document_permission_error(error: Exception) -> bool:
        permission_codes = {"B_PERMISSION_NoPermission", "forbidden.accessDenied"}
        if isinstance(error, DwsError) and error.code in permission_codes:
            return True
        error_text = str(error)
        return any(code in error_text for code in permission_codes)

    @staticmethod
    def _linked_document_permission_request_reply() -> str:
        return (
            "我这边没有权限读取你引用的材料。麻烦把听记/文档权限开给我，"
            "或者把正文和关键结论直接发过来，我再继续处理。"
        )

    def _retry_existing_reply_attempt(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt: ReplyAttempt,
        *,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        if attempt.calendar_event_id.strip() and attempt.calendar_response_status.strip():
            return self._retry_existing_calendar_attempt(
                conversation,
                trigger,
                new_messages,
                attempt,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
        if attempt.action not in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            return False
        if not attempt.final_reply_text.strip():
            return False
        try:
            at_users = self._reply_at_users(trigger)
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt.id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "reply_at_users",
                str(exc),
            )
            self._notify(
                title=f"CEO reply recipient failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return True
        direct_user_id = at_users[0] if conversation.single_chat and at_users else None
        final_reply_text = self._format_reply_delivery_text(
            attempt.final_reply_text,
        )
        self.store.update_reply_attempt(
            attempt.id,
            final_reply_text=final_reply_text,
            direct_user_id=direct_user_id or "",
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else "",
        )
        self._deliver_trigger_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt.id,
            reply_text=final_reply_text,
            feedback_token="",
            at_users=at_users,
            failure_error_kind="send",
            failure_notify_title=f"CEO auto reply failed: {conversation.title}",
            raise_on_delivery_failure=raise_on_delivery_failure,
        )
        return True

    def _retry_existing_calendar_attempt(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt: ReplyAttempt,
        *,
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        event_id = attempt.calendar_event_id.strip()
        response_status = attempt.calendar_response_status.strip()
        if not event_id or not response_status:
            return False
        try:
            action_result = self.dws.respond_calendar_event(event_id, response_status)
        except Exception as exc:
            if self._calendar_response_is_organizer_noop(exc):
                self._mark_calendar_response_noop(
                    attempt_id=attempt.id,
                    event_id=event_id,
                    response_status=response_status,
                    send_status=CALENDAR_ACTION_SEND_STATUS,
                    send_error="calendar_event_organizer_noop",
                )
                self._mark_seen(new_messages)
                return True
            self.store.update_reply_attempt(
                attempt.id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_response",
                str(exc),
            )
            self._notify(
                title=f"CEO calendar response failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return True
        self.store.update_reply_attempt(
            attempt.id,
            calendar_response_result_json=json.dumps(
                action_result,
                ensure_ascii=False,
                sort_keys=True,
            ),
            send_status=CALENDAR_ACTION_SEND_STATUS,
            send_error="",
        )
        self._mark_seen(new_messages)
        if attempt.codex_reason:
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_response",
                f"{response_status}: {attempt.codex_reason}",
            )
        return True

    def _handoff_ding_text(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> str:
        previous_split_reply = self._previous_split_person_reply(
            context_messages, trigger
        )
        return (
            f"{conversation.title}\n"
            f"{trigger.sender_name}: {trigger.content[:300]}\n"
            f"previous split-person reply: {previous_split_reply}"
        )

    def _notify_handoff(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> bool:
        handoff_text = self._handoff_ding_text(
            conversation=conversation,
            trigger=trigger,
            context_messages=context_messages,
        )
        try:
            self._ding_self(handoff_text)
        except Exception:
            self._notify(
                title=f"CEO handoff: {conversation.title}",
                message=(
                    "DING unavailable; delivered by local notification. "
                    f"{handoff_text}"
                )[:120],
                conversation=conversation,
            )
            return True
        return False

    def _previous_split_person_reply(
        self,
        context_messages: list[DingTalkMessage],
        trigger: DingTalkMessage,
    ) -> str:
        for message in reversed(context_messages):
            if message.open_message_id == trigger.open_message_id:
                continue
            if message.create_time > trigger.create_time:
                continue
            if SPLIT_PERSON_SIGNATURE in message.content:
                return message.content[:300]
        return "none"

    def _send_reply(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        reply_text: str,
        reason: str,
        attempt_id: int,
        system_actions: list[dict] | None = None,
        comment_target_messages: list[DingTalkMessage] | None = None,
        raise_on_delivery_failure: bool = False,
        allow_duplicate_send: bool = False,
    ) -> None:
        if not reply_text.strip():
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked",
                send_error=f"empty_reply: {reason}",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "empty_reply",
                reason,
            )
            self._notify(
                title=f"CEO agent empty reply: {conversation.title}",
                message=reason[:120],
                conversation=conversation,
                attempt_id=attempt_id,
            )
            return
        try:
            explicit_at_targets = self._explicit_reply_at_targets(trigger, reply_text)
            at_targets = explicit_at_targets or self._default_reply_at_targets(trigger)
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "reply_at_users",
                str(exc),
            )
            self._notify(
                title=f"CEO reply recipient failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
                attempt_id=attempt_id,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return
        at_users = [target.user_id for target in at_targets if target.user_id]
        at_open_dingtalk_ids = (
            [
                target.open_dingtalk_id
                for target in at_targets
                if target.open_dingtalk_id
            ]
            if not conversation.single_chat
            else []
        )
        at_open_dingtalk_names = (
            [
                target.name
                for target in at_targets
                if target.open_dingtalk_id and target.name
            ]
            if not conversation.single_chat
            else []
        )
        reply_at_names = self._reply_at_display_names(
            conversation,
            at_targets,
        )
        direct_user_id = at_users[0] if conversation.single_chat and at_users else None
        reply_text = append_signature(reply_text)
        reply_text = self._format_reply_delivery_text(
            reply_text,
        )
        if contains_forbidden_leak(reply_text):
            regenerated_reply_text = self._regenerate_reply_after_leak_check(
                blocked_reply_text=reply_text,
            )
            if regenerated_reply_text:
                reply_text = append_signature(regenerated_reply_text)
                reply_text = self._format_reply_delivery_text(
                    reply_text,
                )
        self._deliver_final_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt_id,
            final_reply_text=reply_text,
            at_users=at_users,
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else None,
            reply_at_names=reply_at_names,
            system_actions=system_actions or [],
            comment_target_messages=comment_target_messages,
            raise_on_delivery_failure=raise_on_delivery_failure,
            allow_duplicate_send=allow_duplicate_send,
        )

    def _regenerate_reply_after_leak_check(
        self,
        *,
        blocked_reply_text: str,
    ) -> str:
        feedback_prompt = self._leak_check_feedback_prompt(blocked_reply_text)
        decision = self.codex.decide(
            prompt=feedback_prompt,
            session_id=getattr(self.codex, "last_session_id", None),
        )
        decision = self._normalize_codex_decision(decision)
        if decision.action not in {
            CodexAction.SEND_REPLY,
            CodexAction.ASK_CLARIFYING_QUESTION,
        }:
            return ""
        return decision.reply_text.strip()

    @staticmethod
    def _leak_check_feedback_prompt(blocked_reply_text: str) -> str:
        forbidden_terms = "、".join(f"`{marker}`" for marker in FORBIDDEN_MARKERS)
        return (
            "上一版 reply_text 被发送安全检查拦截，不能发送。\n"
            "请基于同一个上下文重新输出合法 AgentEnvelope JSON，"
            "只改写 user_response.text，不要解释。\n"
            '本次 kind 必须是 reply，例如 "kind":"reply"。\n'
            "user_response.text 不要引用来源、不要加脚注编号、不要写参考文献，"
            f"也不要出现这些会被发送安全检查拦截的字符串：{forbidden_terms}。\n"
            "如果业务上需要表达产品能力或判断依据，改用普通中文描述，不要照搬上述字符串。\n"
            "上一版最终回复如下，仅用于改写，不要原样复制：\n"
            f"{blocked_reply_text[:1200]}\n"
            f"{LEAK_CHECK_REGENERATION_SCHEMA}"
        )

    def _deliver_final_reply(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        final_reply_text: str,
        at_users: list[str],
        at_open_dingtalk_ids: list[str],
        at_open_dingtalk_names: list[str],
        direct_user_id: str | None,
        direct_open_dingtalk_id: str | None,
        reply_at_names: list[str] | None = None,
        system_actions: list[dict] | None = None,
        comment_target_messages: list[DingTalkMessage] | None = None,
        raise_on_delivery_failure: bool = False,
        allow_duplicate_send: bool = False,
    ) -> None:
        reply_text = self._native_reply_body(final_reply_text)
        feedback_base_url = feedback_spike_vercel_base_url()
        if feedback_base_url:
            self._sync_recent_feedback_events_for_conversation(
                conversation.open_conversation_id
            )
        feedback_stats = self.store.feedback_pressure_stats(
            conversation.open_conversation_id,
            now_utc=self._sqlite_timestamp(self._now()),
        )
        feedback_block = bool(feedback_base_url) and requires_feedback_block(
            feedback_stats
        )
        feedback_link_prefix = (
            FEEDBACK_REQUIRED_LINK_PREFIX
            if bool(feedback_base_url)
            and (
                feedback_block
                or requires_feedback_reminder(feedback_stats)
            )
            else "反馈："
        )
        if feedback_base_url:
            outgoing_text = prepare_outgoing_reply_text(
                reply_text=reply_text,
                original_text=trigger.content,
                attempt_id=attempt_id,
                feedback_base_url=feedback_base_url,
                feedback_link_prefix=feedback_link_prefix,
                feedback_link_appender=append_feedback_links,
            )
            reply_text = outgoing_text.text
            feedback_token = outgoing_text.feedback_token
        else:
            feedback_token = ""
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            direct_user_id=direct_user_id or "",
            direct_open_dingtalk_id=direct_open_dingtalk_id or "",
        )
        if contains_forbidden_leak(reply_text):
            regenerated_reply_text = self._regenerate_reply_after_leak_check(
                blocked_reply_text=reply_text,
            )
            if regenerated_reply_text:
                reply_text = append_signature(regenerated_reply_text)
                reply_text = self._format_reply_delivery_text(reply_text)
                outgoing_text = prepare_outgoing_reply_text(
                    reply_text=reply_text,
                    original_text=trigger.content,
                    attempt_id=attempt_id,
                    feedback_base_url=feedback_base_url,
                    feedback_link_prefix=feedback_link_prefix,
                    feedback_link_appender=append_feedback_links,
                )
                reply_text = outgoing_text.text
                feedback_token = outgoing_text.feedback_token
                self.store.update_reply_attempt(
                    attempt_id,
                    final_reply_text=reply_text,
                    direct_user_id=direct_user_id or "",
                    direct_open_dingtalk_id=direct_open_dingtalk_id or "",
                )
        reply_text = self._apply_reply_at_mentions(reply_text, reply_at_names or [])
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            direct_user_id=direct_user_id or "",
            direct_open_dingtalk_id=direct_open_dingtalk_id or "",
        )
        if contains_forbidden_leak(reply_text):
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked",
                send_error="leak_check",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "leak_check",
                reply_text,
            )
            self._notify(
                title=f"CEO agent blocked leak: {conversation.title}",
                message=reply_text[:120],
                conversation=conversation,
                attempt_id=attempt_id,
            )
            return

        minutes_comment_target = self._minutes_comment_target(
            comment_target_messages or new_messages
        )
        if minutes_comment_target:
            self._deliver_minutes_comment(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                attempt_id=attempt_id,
                reply_text=reply_text,
                target_url=minutes_comment_target,
                feedback_token=feedback_token,
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            return

        self._notify(
            title=f"CEO auto reply: {conversation.title}",
            message=reply_text,
            conversation=conversation,
            attempt_id=attempt_id,
        )
        self._deliver_trigger_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt_id,
            reply_text=reply_text,
            feedback_token=feedback_token,
            at_users=at_users,
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
            reply_at_names=reply_at_names,
            system_actions=system_actions or [],
            failure_error_kind="send",
            failure_notify_title=f"CEO auto reply failed: {conversation.title}",
            raise_on_delivery_failure=raise_on_delivery_failure,
            allow_duplicate_send=allow_duplicate_send,
        )

    def _sync_recent_feedback_events_for_conversation(
        self,
        conversation_id: str,
    ) -> None:
        sent_replies = self.store.list_sent_replies_with_feedback_tokens_for_conversation(
            conversation_id,
            limit=10,
        )
        if not sent_replies:
            return
        synced = sync_feedback_events_for_sent_replies(
            self.store,
            sent_replies,
            timeout_seconds=1,
        )
        if synced:
            logger.info(
                "synced feedback events before pressure check conversation_id=%s count=%s",
                conversation_id,
                synced,
            )

    def _deliver_minutes_comment(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        reply_text: str,
        target_url: str,
        feedback_token: str,
        raise_on_delivery_failure: bool = False,
    ) -> None:
        self._notify(
            title=f"CEO minutes comment: {conversation.title}",
            message=reply_text,
            conversation=conversation,
            attempt_id=attempt_id,
        )
        if self.dry_run:
            self.store.update_reply_attempt(attempt_id, send_status="dry_run")
            return
        try:
            send_result = self.dws.create_doc_comment(target_url, reply_text)
        except Exception as exc:
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "minutes_comment",
                str(exc),
            )
            self._notify(
                title=f"CEO minutes comment unavailable: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
                attempt_id=attempt_id,
            )
            self._deliver_minutes_comment_fallback_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=new_messages,
                attempt_id=attempt_id,
                reply_text=reply_text,
                feedback_token=feedback_token,
                comment_error=str(exc),
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            return
        self.store.update_reply_attempt(
            attempt_id,
            send_status="sent",
            retry_count=0,
        )
        self.store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json=json.dumps(send_result or {}, ensure_ascii=False),
            recall_key=DwsClient.extract_recall_key(send_result),
            feedback_token=feedback_token,
        )
        self._mark_seen(new_messages)

    def _deliver_minutes_comment_fallback_reply(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        reply_text: str,
        feedback_token: str,
        comment_error: str,
        raise_on_delivery_failure: bool = False,
    ) -> None:
        self._deliver_trigger_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt_id,
            reply_text=reply_text,
            feedback_token=feedback_token,
            send_result_json_builder=lambda send_result: {
                "fallback": "chat_reply",
                "minutes_comment_error": comment_error,
                "send_result": send_result or {},
            },
            failure_error_kind="send",
            failure_send_error=lambda exc: (
                f"minutes_comment_failed: {comment_error}; reply_failed: {exc}"
            ),
            failure_notify_title=f"CEO minutes fallback reply failed: {conversation.title}",
            raise_on_delivery_failure=raise_on_delivery_failure,
        )

    def _deliver_trigger_reply(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        attempt_id: int,
        reply_text: str,
        feedback_token: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        reply_at_names: list[str] | None = None,
        system_actions: list[dict] | None = None,
        send_result_json_builder=None,
        failure_error_kind: str = "send",
        failure_send_error=None,
        failure_notify_title: str = "",
        raise_on_delivery_failure: bool = False,
        allow_duplicate_send: bool = False,
    ) -> bool:
        if self.dry_run:
            self.store.update_reply_attempt(attempt_id, send_status="dry_run")
            return True
        if at_users is None:
            try:
                explicit_at_targets = self._explicit_reply_at_targets(
                    trigger, reply_text
                )
                at_targets = explicit_at_targets or self._default_reply_at_targets(
                    trigger
                )
            except Exception as exc:
                self.store.update_reply_attempt(
                    attempt_id,
                    send_status="failed",
                    send_error=str(exc),
                )
                self.store.record_error(
                    conversation.open_conversation_id,
                    trigger.open_message_id,
                    "reply_at_users",
                    str(exc),
                )
                if raise_on_delivery_failure:
                    raise ReplyDeliveryError(str(exc)) from exc
                if failure_notify_title:
                    self._notify(
                        title=failure_notify_title,
                        message=str(exc)[:120],
                        conversation=conversation,
                        attempt_id=attempt_id,
                    )
                return False
            at_users = [target.user_id for target in at_targets if target.user_id]
            at_open_dingtalk_ids = (
                [
                    target.open_dingtalk_id
                    for target in at_targets
                    if target.open_dingtalk_id
                ]
                if not conversation.single_chat
                else []
            )
            at_open_dingtalk_names = (
                [
                    target.name
                    for target in at_targets
                    if target.open_dingtalk_id and target.name
                ]
                if not conversation.single_chat
                else []
            )
            reply_at_names = self._reply_at_display_names(
                conversation,
                at_targets,
            )
        at_open_dingtalk_ids = at_open_dingtalk_ids or []
        at_open_dingtalk_names = at_open_dingtalk_names or []
        reply_text = self._apply_reply_at_mentions(reply_text, reply_at_names or [])
        self.store.update_reply_attempt(attempt_id, final_reply_text=reply_text)
        if not allow_duplicate_send and self.store.has_sent_reply_for_trigger(
            conversation.open_conversation_id,
            trigger.open_message_id,
        ):
            self.store.update_reply_attempt(
                attempt_id,
                send_status="blocked",
                send_error="duplicate_sent_reply_for_trigger",
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "duplicate_sent_reply_for_trigger",
                "A sent reply already exists for this trigger; skipped duplicate delivery.",
            )
            self._mark_seen(new_messages)
            return False
        document_delivery_payload = None
        try:
            document_delivery_payload = self._create_markdown_document_reply_if_needed(
                conversation=conversation,
                reply_text=reply_text,
                system_actions=system_actions or [],
                editor_user_ids=at_users or [trigger.sender_user_id or ""],
            )
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
                retry_count=0,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "doc_reply_create",
                str(exc),
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            if failure_notify_title:
                self._notify(
                    title=failure_notify_title,
                    message=str(exc)[:120],
                    conversation=conversation,
                    attempt_id=attempt_id,
                )
            return False
        if document_delivery_payload is not None:
            reply_text = document_delivery_payload["reply_text"]
            self.store.update_reply_attempt(attempt_id, final_reply_text=reply_text)
        try:
            retry_count, send_result = self._send_reply_to_trigger_with_retry(
                conversation,
                trigger,
                reply_text,
                at_users=at_users,
                at_open_dingtalk_ids=at_open_dingtalk_ids,
                at_open_dingtalk_names=at_open_dingtalk_names,
            )
        except Exception as exc:
            send_error = (
                failure_send_error(exc)
                if failure_send_error is not None
                else str(exc)
            )
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=send_error,
                retry_count=0
                if getattr(exc, "needs_authorization", False)
                else max(self.send_attempts - 1, 0),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                failure_error_kind,
                str(exc),
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            if failure_notify_title:
                self._notify(
                    title=failure_notify_title,
                    message=str(exc)[:120],
                    conversation=conversation,
                    attempt_id=attempt_id,
                )
            return False
        send_result_json_payload = (
            send_result_json_builder(send_result)
            if send_result_json_builder is not None
            else (send_result or {})
        )
        native_reply_extra = (
            dict(send_result_json_payload)
            if send_result_json_builder is not None
            else {
                "at_user_ids": at_users,
                "at_open_dingtalk_ids": at_open_dingtalk_ids,
                "at_open_dingtalk_names": at_open_dingtalk_names,
            }
        )
        if document_delivery_payload is not None:
            native_reply_extra["markdown_document_reply"] = {
                key: value
                for key, value in document_delivery_payload.items()
                if key != "reply_text"
            }
        if send_result_json_builder is not None:
            native_reply_extra["at_user_ids"] = at_users
            native_reply_extra["at_open_dingtalk_ids"] = at_open_dingtalk_ids
            native_reply_extra["at_open_dingtalk_names"] = at_open_dingtalk_names
        delivery_kind = (
            "native_reply_visibility_unconfirmed"
            if self._send_result_has_unconfirmed_visibility(send_result)
            else "native_reply"
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="sent",
            send_error="",
            retry_count=retry_count,
        )
        self.store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json=json.dumps(
                native_reply_delivery_payload(
                    conversation,
                    trigger,
                    send_result,
                    extra=native_reply_extra,
                    delivery_kind=delivery_kind,
                ),
                ensure_ascii=False,
            ),
            recall_key=DwsClient.extract_recall_key(send_result),
            feedback_token=feedback_token,
        )
        attempt = self.store.get_reply_attempt(attempt_id)
        if attempt is not None:
            self._enqueue_conversation_work_item(
                attempt_id=attempt_id,
                conversation=conversation,
                trigger=trigger,
                action=attempt.action,
                audit_summary=attempt.audit_summary,
                final_reply_text=attempt.final_reply_text,
            )
        self._mark_seen(new_messages)
        return True

    def _create_markdown_document_reply_if_needed(
        self,
        *,
        conversation: DingTalkConversation,
        reply_text: str,
        system_actions: list[dict],
        editor_user_ids: list[str],
    ) -> dict[str, Any] | None:
        action = self._markdown_document_reply_action(system_actions)
        chunks = split_dingtalk_text(reply_text)
        if action is None and len(chunks) <= 1:
            return None
        title = self._markdown_document_reply_title(conversation, action)
        doc_result = self.dws.create_markdown_doc(title, reply_text)
        doc_url = self._markdown_document_url(doc_result)
        if not doc_url:
            raise RuntimeError("dws doc create did not return a document URL")
        doc_node_id = self._markdown_document_node_id(doc_result, doc_url)
        if not doc_node_id:
            raise RuntimeError("dws doc create did not return a document nodeId")
        normalized_editor_user_ids = self._document_editor_user_ids(editor_user_ids)
        if not normalized_editor_user_ids:
            raise RuntimeError("dws doc reply has no recipient userId for permission")
        permission_result = self.dws.add_doc_editor_permission(
            doc_node_id,
            normalized_editor_user_ids,
        )
        intro = (
            "内容我写成了文档："
            if action is not None
            else "内容较长，我写成了文档："
        )
        return {
            "title": title,
            "url": doc_url,
            "reason": "requested_document" if action is not None else "message_too_long",
            "doc_result": doc_result,
            "node_id": doc_node_id,
            "editor_user_ids": normalized_editor_user_ids,
            "permission_result": permission_result,
            "reply_text": append_signature(f"{intro}{title}\n{doc_url}"),
        }

    @staticmethod
    def _markdown_document_reply_action(
        system_actions: list[dict],
    ) -> dict[str, Any] | None:
        for action in system_actions:
            if (
                isinstance(action, dict)
                and action.get("type") == "dws_markdown_document_reply"
            ):
                return action
        return None

    def _markdown_document_reply_title(
        self,
        conversation: DingTalkConversation,
        action: dict[str, Any] | None,
    ) -> str:
        if action is not None:
            title = str(action.get("title") or "").strip()
            if title:
                return title[:80]
        timestamp = self._now().astimezone().strftime("%Y%m%d-%H%M")
        source = conversation.title.strip() or "DingTalk"
        return f"CEO回复-{source[:40]}-{timestamp}"

    @staticmethod
    def _markdown_document_url(payload: object) -> str:
        if not isinstance(payload, dict):
            return ""
        candidates = [
            payload.get("url"),
            payload.get("docUrl"),
            payload.get("doc_url"),
        ]
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.extend(
                [
                    result.get("url"),
                    result.get("docUrl"),
                    result.get("doc_url"),
                    result.get("nodeUrl"),
                ]
            )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @classmethod
    def _markdown_document_node_id(cls, payload: object, doc_url: str = "") -> str:
        if isinstance(payload, dict):
            candidates: list[object] = [
                payload.get("nodeId"),
                payload.get("node_id"),
                payload.get("dentryUuid"),
            ]
            result = payload.get("result")
            if isinstance(result, dict):
                candidates.extend(
                    [
                        result.get("nodeId"),
                        result.get("node_id"),
                        result.get("dentryUuid"),
                    ]
                )
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        match = re.search(r"/nodes/([^/?#]+)", doc_url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _document_editor_user_ids(user_ids: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for user_id in user_ids:
            normalized = str(user_id).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _enqueue_conversation_work_item(
        self,
        *,
        attempt_id: int,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        action: str,
        audit_summary: str,
        final_reply_text: str,
    ) -> None:
        if action not in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }:
            return
        summary_parts = [
            trigger.content.strip(),
            audit_summary.strip(),
            final_reply_text.strip(),
        ]
        summary = "\n".join(part for part in summary_parts if part)
        if not summary.strip():
            return
        project_name = conversation.title.strip() if not conversation.single_chat else ""
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "reply_attempt",
                    "ref": str(attempt_id),
                    "title": conversation.title,
                    "conversation_id": conversation.open_conversation_id,
                    "conversation_title": conversation.title,
                    "created_at": trigger.create_time,
                },
                "summary": summary,
                "project_name": project_name,
                "context": {
                    "sender": trigger.sender_name,
                    "participants": [trigger.sender_name],
                    "source_conversation_kind": "direct"
                    if conversation.single_chat
                    else "group",
                    "source_conversation_title": conversation.title,
                },
            }
        )
        self.store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )

    def _send_reply_to_trigger_with_retry(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        *,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> tuple[int, dict | None]:
        chunks = split_dingtalk_text(text)
        if not chunks:
            raise RuntimeError("empty DingTalk reply text")
        max_retry_count = 0
        chunk_results = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_at_users = at_users if index == 1 else []
            chunk_at_open_dingtalk_ids = at_open_dingtalk_ids if index == 1 else []
            chunk_at_open_dingtalk_names = at_open_dingtalk_names if index == 1 else []
            retry_count, send_result = self._send_single_reply_to_trigger_with_retry(
                conversation,
                trigger,
                chunk,
                at_users=chunk_at_users,
                at_open_dingtalk_ids=chunk_at_open_dingtalk_ids,
                at_open_dingtalk_names=chunk_at_open_dingtalk_names,
            )
            max_retry_count = max(max_retry_count, retry_count)
            chunk_results.append(
                {"index": index, "text": chunk, "send_result": send_result}
            )
        result: dict[str, Any] = {"chunks": chunk_results}
        if (
            not conversation.single_chat
            and not self._sent_chunks_visible(conversation, chunks)
        ):
            result["visibility"] = "native_reply_not_confirmed"
        return max_retry_count, result

    def _sent_chunks_visible(
        self,
        conversation: DingTalkConversation,
        chunks: list[str],
    ) -> bool:
        if not chunks:
            return False
        if self._recent_messages_contain_chunks(conversation, chunks):
            return True
        time.sleep(float(os.getenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "2")))
        return self._recent_messages_contain_chunks(conversation, chunks)

    def _recent_messages_contain_chunks(
        self,
        conversation: DingTalkConversation,
        chunks: list[str],
    ) -> bool:
        try:
            recent_messages = self.dws.read_recent_messages(conversation)
        except Exception as exc:
            del exc
            return True
        recent_texts = [
            self._normalize_visible_message_text(message.content)
            for message in recent_messages
        ]
        return all(
            self._normalize_visible_message_text(chunk) in recent_texts
            for chunk in chunks
        )

    @staticmethod
    def _normalize_visible_message_text(text: str) -> str:
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    @staticmethod
    def _send_result_has_unconfirmed_visibility(send_result: dict | None) -> bool:
        return (
            isinstance(send_result, dict)
            and send_result.get("visibility") == "native_reply_not_confirmed"
        )

    def _send_single_reply_to_trigger_with_retry(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        *,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> tuple[int, dict | None]:
        errors: list[str] = []
        for attempt_number in range(1, self.send_attempts + 1):
            try:
                send_result = self.dws.send_reply_to_trigger(
                    conversation,
                    trigger,
                    text,
                    at_users=at_users,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    at_open_dingtalk_names=at_open_dingtalk_names,
                )
                return attempt_number - 1, send_result
            except Exception as exc:
                if getattr(exc, "needs_authorization", False):
                    raise exc
                errors.append(f"attempt {attempt_number}: {exc}")
        raise RuntimeError(" | ".join(errors))

    def _ding_self(self, text: str) -> None:
        if self.dry_run:
            return
        self.dws.ding_self(text)

    def _explicit_reply_at_targets(
        self, trigger: DingTalkMessage, reply_text: str
    ) -> list[ReplyAtTarget]:
        targets: list[ReplyAtTarget] = []
        for mention_name in self._visible_mention_names(reply_text):
            target = self._reply_at_target_for_name(trigger, mention_name)
            if target is not None:
                self._append_reply_at_target(targets, target)
        return targets

    def _default_reply_at_targets(self, trigger: DingTalkMessage) -> list[ReplyAtTarget]:
        current_user_id = self.store.get_current_user_id()
        targets: list[ReplyAtTarget] = []
        sender_user_id = trigger.sender_user_id
        if not sender_user_id:
            try:
                sender_user_id = self.dws.resolve_message_sender(trigger)
            except Exception:
                if trigger.sender_open_dingtalk_id:
                    self._append_reply_at_target(
                        targets,
                        ReplyAtTarget(
                            open_dingtalk_id=trigger.sender_open_dingtalk_id,
                            name=trigger.sender_name,
                        ),
                    )
                else:
                    raise
        for user_id in [sender_user_id or "", *trigger.mentioned_user_ids]:
            if not user_id:
                continue
            if current_user_id and user_id == current_user_id:
                continue
            self._append_reply_at_target(
                targets,
                self._reply_at_target_for_user_id(user_id, trigger),
            )
        return targets

    def _reply_at_users(self, trigger: DingTalkMessage) -> list[str]:
        return [
            target.user_id
            for target in self._default_reply_at_targets(trigger)
            if target.user_id
        ]

    @staticmethod
    def _reply_at_display_names(
        conversation: DingTalkConversation, targets: list[ReplyAtTarget]
    ) -> list[str]:
        if conversation.single_chat:
            return []
        names: list[str] = []
        for target in targets:
            name = target.name.strip()
            if not name:
                continue
            names.append(name)
        return names

    @staticmethod
    def _apply_reply_at_mentions(reply_text: str, names: list[str]) -> str:
        cleaned_names: list[str] = []
        for name in names:
            clean_name = name.strip()
            if clean_name and clean_name not in cleaned_names:
                cleaned_names.append(clean_name)
        if not cleaned_names:
            return reply_text

        text = reply_text.strip()
        missing_names: list[str] = []
        for name in cleaned_names:
            if f"@{name}" in text:
                continue
            stripped = text.lstrip()
            leading = text[: len(text) - len(stripped)]
            if stripped.startswith(name):
                tail = stripped[len(name) :].lstrip()
                if tail:
                    text = f"{leading}@{name} {tail}"
                else:
                    text = f"{leading}@{name}"
                continue
            missing_names.append(name)

        if not missing_names:
            return text
        prefix = " ".join(f"@{name}" for name in missing_names)
        return f"{prefix} {text.lstrip()}"

    @staticmethod
    def _visible_mention_names(text: str) -> list[str]:
        names: list[str] = []
        for match in MENTION_PATTERN.finditer(text):
            token = match.group().strip()
            if not token.startswith("@"):
                continue
            name = token[1:].strip()
            for separator in ("(", "（"):
                position = name.find(separator)
                if position >= 0:
                    name = name[:position].strip()
            if name and name not in names:
                names.append(name)
        return names

    def _reply_at_target_for_name(
        self, trigger: DingTalkMessage, name: str
    ) -> ReplyAtTarget | None:
        if name == trigger.sender_name and trigger.sender_open_dingtalk_id:
            return ReplyAtTarget(
                user_id=trigger.sender_user_id or "",
                open_dingtalk_id=trigger.sender_open_dingtalk_id,
                name=trigger.sender_name,
            )
        matches = self.store.find_org_users_by_name(name)
        if len(matches) != 1:
            return None
        profile = matches[0]
        current_user_id = self.store.get_current_user_id()
        if current_user_id and profile.user_id == current_user_id:
            return None
        return ReplyAtTarget(
            user_id=profile.user_id,
            open_dingtalk_id=profile.open_dingtalk_id or "",
            name=profile.name,
        )

    def _reply_at_target_for_user_id(
        self, user_id: str, trigger: DingTalkMessage
    ) -> ReplyAtTarget:
        if trigger.sender_user_id == user_id:
            return ReplyAtTarget(
                user_id=user_id,
                open_dingtalk_id=trigger.sender_open_dingtalk_id or "",
                name=trigger.sender_name,
            )
        profile = self.store.get_org_user_profile(user_id)
        if profile is None:
            return ReplyAtTarget(user_id=user_id)
        return ReplyAtTarget(
            user_id=profile.user_id,
            open_dingtalk_id=profile.open_dingtalk_id or "",
            name=profile.name,
        )

    @staticmethod
    def _append_reply_at_target(
        targets: list[ReplyAtTarget], target: ReplyAtTarget
    ) -> None:
        for existing in targets:
            if target.open_dingtalk_id and (
                existing.open_dingtalk_id == target.open_dingtalk_id
            ):
                return
            if target.user_id and existing.user_id == target.user_id:
                return
        targets.append(target)

    @staticmethod
    def _format_reply_delivery_text(
        reply_text: str,
    ) -> str:
        return DingTalkAutoReplyWorker._native_reply_body(reply_text)

    @staticmethod
    def _native_reply_body(reply_text: str) -> str:
        stripped = reply_text.strip()
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("> ") and not lines[1].strip():
            stripped = "\n".join(lines[2:]).lstrip()
        while stripped.startswith("<@"):
            end = stripped.find(">")
            if end < 0:
                break
            stripped = stripped[end + 1 :].lstrip()
        return stripped

    def _notify(
        self,
        title: str,
        message: str,
        conversation: DingTalkConversation | None = None,
        attempt_id: int | None = None,
    ) -> None:
        send_macos_notification(
            title=title,
            message=message,
            url=self._notification_url(conversation, attempt_id=attempt_id),
        )

    def _notification_url(
        self,
        conversation: DingTalkConversation | None,
        *,
        attempt_id: int | None = None,
    ) -> str | None:
        if conversation is None:
            return None
        open_conversation_id = conversation.open_conversation_id.strip()
        if not open_conversation_id:
            return None
        query = f"conversation_id={quote(open_conversation_id, safe='')}"
        if attempt_id is not None:
            query = f"{query}&attempt_id={int(attempt_id)}"
        return (
            f"{notification_bridge_base_url()}/open-dingtalk"
            f"?{query}"
        )

    def _mark_seen(self, messages: list[DingTalkMessage]) -> None:
        if self.dry_run:
            return
        for message in messages:
            self.store.mark_seen(message.open_message_id, message.open_conversation_id)
            for message_id in message.raw_payload.get("coalesced_message_ids", []):
                if isinstance(message_id, str) and message_id.strip():
                    self.store.mark_seen(message_id, message.open_conversation_id)

    def _persist_codex_session_id(
        self,
        conversation: DingTalkConversation,
        before_session_id: str | None,
        after_session_id: str | None,
    ) -> None:
        if not after_session_id or after_session_id == before_session_id:
            return
        self.store.upsert_conversation(
            conversation_id=conversation.open_conversation_id,
            title=conversation.title,
            single_chat=conversation.single_chat,
            codex_session_id=after_session_id,
        )

    @staticmethod
    def _single_chat_material_no_reply_needs_retry(
        conversation: DingTalkConversation,
        material_references: list[MaterialReferenceContext],
        decision: CodexDecision,
    ) -> bool:
        return (
            conversation.single_chat
            and any(
                not DingTalkAutoReplyWorker._is_minutes_material_reference(reference)
                for reference in material_references
            )
            and decision.action == CodexAction.NO_REPLY
        )

    @staticmethod
    def _is_minutes_material_reference(reference: MaterialReferenceContext) -> bool:
        return reference.kind == "dingtalk_minutes"

    @staticmethod
    def _normalize_codex_decision(decision: Any) -> CodexDecision:
        if hasattr(decision, "kind") and hasattr(decision, "user_response"):
            return codex_decision_from_envelope(decision)
        return decision

    @staticmethod
    def _is_critical_info_unavailable_reason(reason: str) -> bool:
        return reason.strip().startswith(CRITICAL_INFO_UNAVAILABLE_PREFIX)

    @staticmethod
    def _single_chat_material_retry_prompt() -> str:
        return (
            "上一次输出了 no_reply，但当前是私聊，且消息里包含钉钉材料引用。"
            "请重新判断是否需要用 DWS 读取这些材料；如果需要，先读取材料再给出处理结果。"
            "如果材料足够，action 用 send_reply，reply_text 给出结论、修改意见、风险、下一步或需要补充的具体问题；"
            "如果材料不足，action 用 ask_clarifying_question 或 stop_with_error。"
            "不要因为对方只发送文档、没有额外写“请处理/请 review”就 no_reply。"
            "只输出合法 JSON。"
        )

    @staticmethod
    def _is_stale_codex_resume(decision: CodexDecision, session_id: str | None) -> bool:
        if not session_id or decision.action != CodexAction.STOP_WITH_ERROR:
            return False
        reason = decision.reason
        return (
            (
                "thread/resume failed" in reason
                and "no rollout found for thread id" in reason
            )
            or (
                "codex_rollout::list" in reason
                and "state db returned stale rollout path" in reason
            )
        )

    def _build_prompt(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        *,
        include_thread_prompt: bool = True,
        linked_documents: list[LinkedDocumentContext] | None = None,
        material_references: list[MaterialReferenceContext] | None = None,
        image_download_errors: list[str] | None = None,
    ) -> str:
        return build_turn_prompt(
            conversation,
            new_messages,
            context_messages,
            style_lines=self._style_prompt_lines(
                conversation,
                new_messages,
                context_messages,
            ),
            include_thread_prompt=include_thread_prompt,
            linked_documents=linked_documents,
            material_references=material_references,
            image_download_errors=image_download_errors,
            known_people_lines=self._known_people_prompt_lines(
                new_messages,
                context_messages,
            ),
            sender_org_lines=self._sender_org_prompt_lines(new_messages),
        )

    def _sender_org_prompt_lines(
        self,
        new_messages: list[DingTalkMessage],
        limit: int = 10,
    ) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        for message in new_messages:
            user_id = self._resolve_sender_user_id_for_prompt(message)
            if not user_id or user_id in seen:
                continue
            seen.add(user_id)
            profile = self._get_or_cache_org_profile_for_prompt(user_id, message)
            if profile is None:
                continue

            record: dict[str, Any] = {
                "name": profile.name or message.sender_name or user_id,
                "user_id": profile.user_id,
            }
            if profile.title:
                record["title"] = profile.title
            if profile.org_labels:
                record["org_labels"] = sorted(profile.org_labels)
            if profile.manager_user_id:
                manager_profile = self.store.get_org_user_profile(profile.manager_user_id)
                if manager_profile is not None and manager_profile.name:
                    record["manager"] = {
                        "name": manager_profile.name,
                        "user_id": manager_profile.user_id,
                    }
                elif profile.manager_name:
                    record["manager"] = {
                        "name": profile.manager_name,
                        "user_id": profile.manager_user_id,
                    }
                else:
                    record["manager"] = {"user_id": profile.manager_user_id}
            if profile.department_ids:
                record["departments"] = self._department_context_records(profile)
            if profile.has_subordinate is not None:
                record["has_subordinate"] = profile.has_subordinate
            lines.extend(json.dumps(record, ensure_ascii=False, indent=2).splitlines())
            if len(lines) >= limit:
                break
        return lines

    def _resolve_sender_user_id_for_prompt(self, message: DingTalkMessage) -> str | None:
        if message.sender_user_id:
            return message.sender_user_id
        try:
            return self.dws.resolve_message_sender(message)
        except Exception:
            return None

    def _get_or_cache_org_profile_for_prompt(
        self,
        user_id: str,
        message: DingTalkMessage,
    ):
        profile = self.store.get_org_user_profile(user_id)
        if profile is not None:
            return profile
        try:
            fetched_profile = self.dws.get_user_profile(user_id)
        except Exception:
            return None
        self.store.upsert_org_user_profile(
            user_id=fetched_profile.user_id,
            name=fetched_profile.name or message.sender_name,
            title=fetched_profile.title,
            open_dingtalk_id=fetched_profile.open_dingtalk_id
            or message.sender_open_dingtalk_id,
            manager_user_id=fetched_profile.manager_user_id,
            manager_name=fetched_profile.manager_name,
            department_ids=fetched_profile.department_ids,
            department_names=fetched_profile.department_names,
            org_labels=fetched_profile.org_labels,
            has_subordinate=fetched_profile.has_subordinate,
        )
        return self.store.get_org_user_profile(user_id)

    @staticmethod
    def _format_department_context(profile) -> str:
        department_ids = sorted(profile.department_ids)
        department_names = sorted(profile.department_names)
        if department_names:
            return (
                f"{', '.join(department_names)} "
                f"[ids: {', '.join(department_ids)}]"
            )
        return ", ".join(department_ids)

    @staticmethod
    def _department_context_records(profile) -> list[dict[str, str]]:
        department_ids = sorted(profile.department_ids)
        department_names = sorted(profile.department_names)
        if not department_names:
            return [{"id": department_id} for department_id in department_ids]
        records: list[dict[str, str]] = []
        for index, department_id in enumerate(department_ids):
            record = {"id": department_id}
            if index < len(department_names):
                record["name"] = department_names[index]
            records.append(record)
        return records

    def _known_people_prompt_lines(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
        limit: int = 20,
    ) -> list[str]:
        messages = [*new_messages, *context_messages]
        combined_text = "\n".join(
            part
            for message in messages
            for part in (
                message.sender_name,
                message.content,
                message.quoted_content or "",
            )
        )
        people: dict[str, str] = {}
        for message in messages:
            if message.sender_user_id and message.sender_name.strip():
                people.setdefault(message.sender_user_id, message.sender_name.strip())

        for user_id in self.store.list_org_user_ids():
            if len(people) >= limit:
                break
            profile = self.store.get_org_user_profile(user_id)
            if profile is None or not self._profile_name_matches_text(
                profile.name,
                combined_text,
            ):
                continue
            people.setdefault(profile.user_id, profile.name)

        return [f"- {name}: user_id={user_id}" for user_id, name in people.items()]

    @staticmethod
    def _profile_name_matches_text(name: str, text: str) -> bool:
        normalized_name = name.strip()
        if not normalized_name:
            return False
        if normalized_name in text:
            return True
        if len(normalized_name) >= 3 and normalized_name[1:] in text:
            return True
        return False

    def _style_prompt_lines(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[str]:
        lines: list[str] = []
        examples = retrieve_similar_examples(
            self._style_query(conversation, new_messages, context_messages),
            self.style_records,
            limit=self.style_example_limit,
        )
        if examples:
            lines.append(
                "相似历史回复风格例子（只学习语气、判断顺序和句式结构；不要复用例子里的事实、人名、项目名、客户名、数字或结论；不要引用这些例子）:"
            )
            for index, example in enumerate(examples, start=1):
                lines.append(
                    f"- 例{index}: {self._style_example_text(example.principal_reply)}"
                )

        feedback_examples = self._review_feedback_prompt_lines(
            self._style_query(conversation, new_messages, context_messages)
        )
        lines.extend(feedback_examples)
        return lines

    def _review_feedback_prompt_lines(
        self, query: str, *, limit: int = 1, candidate_limit: int = 50
    ) -> list[str]:
        examples = self._retrieve_review_feedback_examples(
            query,
            self.store.list_reviewed_reply_attempts(limit=candidate_limit),
            limit=limit,
        )
        if not examples:
            return []

        lines = [
            f"相似人工纠偏样本（优先学习 {principal_display_name()} 对错误回复的修正方向；不要复用人名、项目名、客户名、凭证、数字或旧事实；只有当前场景一致时才复用动作边界）:"
        ]
        for index, attempt in enumerate(examples, start=1):
            feedback = self._style_example_text(attempt.reviewer_feedback, 160)
            corrected = self._style_example_text(attempt.corrected_reply_text, 160)
            if corrected:
                lines.append(f"- 纠偏{index}: {feedback} 建议回复: {corrected}")
            else:
                lines.append(f"- 纠偏{index}: {feedback}")
        return lines

    @staticmethod
    def _retrieve_review_feedback_examples(
        query: str, attempts: list[ReplyAttempt], *, limit: int
    ) -> list[ReplyAttempt]:
        query_keywords = extract_retrieval_keywords(query)
        if not query_keywords:
            return []

        attempt_keyword_maps = {
            attempt.id: DingTalkAutoReplyWorker._review_feedback_keyword_maps(attempt)
            for attempt in attempts
        }
        keyword_document_counts: dict[str, int] = {}
        for field_maps in attempt_keyword_maps.values():
            attempt_keywords: set[str] = set()
            for field_keywords, _field_weight in field_maps:
                attempt_keywords.update(field_keywords)
            for keyword in attempt_keywords:
                keyword_document_counts[keyword] = (
                    keyword_document_counts.get(keyword, 0) + 1
                )
        specific_keyword_limit = max(1, len(attempts) // 12)
        scored: list[tuple[float, ReplyAttempt]] = []
        for attempt in attempts:
            score = DingTalkAutoReplyWorker._review_feedback_keyword_score(
                query_keywords,
                attempt_keyword_maps.get(attempt.id, []),
                keyword_document_counts,
                candidate_count=len(attempts),
            )
            specific_overlap = (
                DingTalkAutoReplyWorker._review_feedback_specific_keyword_overlap(
                    query_keywords,
                    attempt_keyword_maps.get(attempt.id, []),
                    keyword_document_counts,
                    specific_keyword_limit=specific_keyword_limit,
                )
            )
            if score >= 1.6 and specific_overlap >= 2:
                scored.append((score, attempt))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return []

        minimum_score = max(1.6, scored[0][0] * 0.75)
        return [attempt for score, attempt in scored if score >= minimum_score][:limit]

    @staticmethod
    def _review_feedback_keyword_maps(
        attempt: ReplyAttempt,
    ) -> list[tuple[dict[str, float], float]]:
        field_weights = [
            (attempt.trigger_text, 3.0),
            (attempt.codex_reason, 2.0),
            (attempt.conversation_title, 0.8),
            (attempt.reviewer_feedback, 0.7),
            (attempt.corrected_reply_text, 0.4),
        ]
        return [
            (field_keywords, field_weight)
            for text, field_weight in field_weights
            if (field_keywords := extract_retrieval_keywords(text))
        ]

    @staticmethod
    def _review_feedback_keyword_score(
        query_keywords: dict[str, float],
        field_keyword_maps: list[tuple[dict[str, float], float]],
        keyword_document_counts: dict[str, int],
        *,
        candidate_count: int,
    ) -> float:
        score = 0.0
        for field_keywords, field_weight in field_keyword_maps:
            score += sum(
                query_weight
                * field_keywords[keyword]
                * field_weight
                * DingTalkAutoReplyWorker._feedback_keyword_rarity_weight(
                    keyword,
                    keyword_document_counts,
                    candidate_count=candidate_count,
                )
                for keyword, query_weight in query_keywords.items()
                if keyword in field_keywords
            )
        return score

    @staticmethod
    def _feedback_keyword_rarity_weight(
        keyword: str,
        keyword_document_counts: dict[str, int],
        *,
        candidate_count: int,
    ) -> float:
        if candidate_count <= 0:
            return 0.0
        if candidate_count <= 2:
            return 1.0
        frequency = keyword_document_counts.get(keyword, 0) / candidate_count
        return max(0.05, 1.0 - frequency)

    @staticmethod
    def _review_feedback_specific_keyword_overlap(
        query_keywords: dict[str, float],
        field_keyword_maps: list[tuple[dict[str, float], float]],
        keyword_document_counts: dict[str, int],
        *,
        specific_keyword_limit: int,
    ) -> int:
        candidate_keywords = set()
        for field_keywords, _field_weight in field_keyword_maps:
            candidate_keywords.update(field_keywords)
        return sum(
            1
            for keyword in query_keywords
            if keyword in candidate_keywords
            and keyword_document_counts.get(keyword, 0) <= specific_keyword_limit
        )

    def _style_query(
        self,
        conversation: DingTalkConversation,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> str:
        query_parts = [conversation.title]
        query_parts.extend(message.content for message in new_messages)
        query_parts.extend(message.content for message in context_messages[-5:])
        return "\n".join(query_parts)

    @staticmethod
    def _style_example_text(text: str, max_characters: int = 120) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= max_characters:
            return normalized
        return f"{normalized[:max_characters]}..."
