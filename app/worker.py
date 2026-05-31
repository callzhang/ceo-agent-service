import json
import re
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit

from pypdf import PdfReader

from ceo_agent_service.codex_decision import append_signature
from ceo_agent_service.config import (
    assistant_signature,
    broadcast_mention_aliases,
    fast_path_unread_backoff_duration,
    group_read_recovery_limit,
    group_read_recovery_window,
    handoff_ack,
    message_recovery_interval,
    notification_bridge_base_url,
    principal_display_name,
    single_chat_read_recovery_limit,
    single_chat_read_recovery_window,
)
from ceo_agent_service.dws_client import (
    DINGTALK_MESSAGE_TIME_ZONE,
    DwsCalendarEvent,
    DwsClient,
    DwsDocumentSearchResult,
    DwsError,
)
from ceo_agent_service.corpus import (
    MEDIA_OR_LINK_PATTERN,
    CorpusRecord,
    count_information_units,
    retrieve_similar_examples,
)
from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
)
from ceo_agent_service.leak_check import (
    FORBIDDEN_MARKERS,
    contains_forbidden_leak,
    redact_forbidden_leak_markers,
)
from ceo_agent_service.notification import send_macos_notification
from ceo_agent_service.oa_approval import extract_oa_url
from ceo_agent_service.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    refresh_org_cache,
)
from ceo_agent_service.permission import PermissionAction, PermissionGate
from ceo_agent_service.prompt import LinkedDocumentContext, build_turn_prompt
from ceo_agent_service.store import AutoReplyStore, ReplyAttempt, ReplyTask

HANDOFF_ACK = handoff_ack()
# Historical auto-ack marker. Keep filtering it from context, but do not send
# new processing acknowledgements before final replies.
PROCESSING_ACK = "收到，我正在处理（by 分身）"
LEAK_CHECK_REGENERATION_SCHEMA = (
    'JSON schema: {"action":"send_reply|ask_clarifying_question|handoff_to_human|no_reply|stop_with_error",'
    '"reply_text":"","reason":"","ding_self":false,"macos_notify":true,'
    '"sensitivity_kind":"general|internal_personnel|external_candidate",'
    '"personnel_subject_user_id":null,"candidate_context_known":false,"candidate_department_ids":[],'
    '"audit_documents":[],"audit_summary":""}'
)
SPLIT_PERSON_SIGNATURE = assistant_signature()
STALE_PROCESSING_TASK_SECONDS = 30 * 60
MAX_REPLY_TASK_ATTEMPTS = 3
STALE_CODEX_RESUME_ATTEMPTS = 2
CALENDAR_PENDING_INVITE_LOOKAHEAD_DAYS = 14
CALENDAR_PENDING_INVITE_EVENT_MATCH_SECONDS = 30 * 60
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
QUOTE_MENTION_PATTERN = MENTION_PATTERN
DINGTALK_DOC_URL_PATTERN = re.compile(
    r"https://alidocs\.dingtalk\.com/i/nodes/[^\s)\]]+"
)
FILE_MESSAGE_PATTERN = re.compile(r"^\s*\[文件]\s*(?P<name>.+?)\s*$")
IMAGE_MESSAGE_MEDIA_ID_PATTERN = re.compile(r"\[图片消息]\(mediaId=(?P<media_id>[^)]+)\)")
MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*]\((?P<url>https?://[^)]+)\)")
QUOTE_WORD_OR_CJK_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*|[\u4e00-\u9fff]"
)
DINGTALK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GROUP_CONTEXT_RECOVERY_WINDOW = timedelta(hours=24)
RECENT_REPLY_WINDOW = timedelta(hours=24)
QUOTE_INFORMATION_UNIT_LIMIT = 20
REFERENCED_FILE_CONTEXT_WINDOW = timedelta(minutes=10)
DOWNLOADED_FILE_MAX_BYTES = 50 * 1024 * 1024
DOWNLOADED_IMAGE_MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
PDF_TEXT_PAGE_LIMIT = 30
DWS_UPGRADE_CHECKED_DATE_STATE_KEY = "dws_upgrade_checked_date"
MESSAGE_RECOVERY_CHECKED_AT_STATE_KEY = "message_recovery_checked_at"
MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY = "message_fast_path_checked_at"
MESSAGE_FAST_PATH_UNREAD_BACKOFF_STATE_PREFIX = "message_fast_path_unread_backoff:"
ORG_CACHE_REFRESH_INTERVAL = timedelta(days=7)
AITABLE_TABLE_PREVIEW_LIMIT = 5
AITABLE_RECORD_PREVIEW_LIMIT = 10
MESSAGE_RECOVERY_INTERVAL = message_recovery_interval()
FAST_PATH_UNREAD_BACKOFF = fast_path_unread_backoff_duration()
SINGLE_CHAT_READ_RECOVERY_WINDOW = single_chat_read_recovery_window()
SINGLE_CHAT_READ_RECOVERY_LIMIT = single_chat_read_recovery_limit()
GROUP_READ_RECOVERY_WINDOW = group_read_recovery_window()
GROUP_READ_RECOVERY_LIMIT = group_read_recovery_limit()


@dataclass(frozen=True)
class CalendarConflictContext:
    invite: DwsCalendarEvent
    conflicts: list[DwsCalendarEvent]


class ReplyDeliveryError(RuntimeError):
    """Raised after recording a delivery failure so queued tasks can retry."""


class ReplyTaskProcessingError(RuntimeError):
    """Raised after recording a processing failure so queued tasks can retry."""


class DingTalkAutoReplyWorker:
    def __init__(
        self,
        store: AutoReplyStore,
        dws,
        codex,
        dry_run: bool = False,
        style_profile: str = "",
        style_records: list[CorpusRecord] | None = None,
        style_example_limit: int = 2,
        send_attempts: int = 2,
        max_task_attempts: int = MAX_REPLY_TASK_ATTEMPTS,
        now_provider: Callable[[], datetime] | None = None,
        oa_approval_runner=None,
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
        self.oa_approval_runner = oa_approval_runner
        self._client_conversation_id_cache: dict[str, str] = {}

    def run_once(self, max_batches: int | None = None) -> None:
        self.produce_once()
        self.consume_once(max_tasks=max_batches)

    def produce_once(self, max_tasks: int | None = None) -> int:
        self._maybe_upgrade_dws_once_per_day()
        self._maybe_refresh_org_cache_once_per_week()
        fast_path_checked_at = self._now().astimezone(timezone.utc)
        recovery_due = self._should_run_recent_message_recovery()
        queued_tasks = 0
        try:
            conversations = self.dws.list_unread_conversations(count=50)
        except Exception as exc:
            self.store.record_error(None, None, "list_unread_conversations", str(exc))
            self._notify(
                title="CEO read unread conversations failed",
                message=str(exc)[:120],
            )
            return 0
        original_unread_conversation_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        delayed_unread_conversation_ids: set[str] = set()
        if not recovery_due:
            conversations = self._conversations_due_for_fast_path(conversations)
        else:
            conversations = self._conversations_after_fast_path_unread_backoff(
                conversations
            )
        if FAST_PATH_UNREAD_BACKOFF > timedelta(0):
            delayed_unread_conversation_ids = original_unread_conversation_ids - {
                conversation.open_conversation_id for conversation in conversations
            }
        unread_conversation_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        mentioned_messages = self._mentioned_messages_by_conversation(conversations)
        broadcast_messages = self._broadcast_messages_by_conversation()
        addressed_messages = self._merge_message_groups(
            mentioned_messages,
            broadcast_messages,
        )
        if delayed_unread_conversation_ids:
            addressed_messages = {
                conversation_id: messages
                for conversation_id, messages in addressed_messages.items()
                if conversation_id not in delayed_unread_conversation_ids
            }
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
            if conversation.open_conversation_id in recovery_conversation_ids:
                try:
                    context_messages = self.dws.read_recent_messages(conversation)
                except Exception as exc:
                    self.store.record_error(
                        conversation.open_conversation_id,
                        None,
                        "read_recent_messages",
                        str(exc),
                    )
            unread_messages = []
            candidate_unread_messages = []
            should_read_unread = (
                recovery_due
                or conversation.open_conversation_id in unread_conversation_ids
            )
            if should_read_unread:
                try:
                    unread_messages = self.dws.read_unread_messages(conversation)
                    candidate_unread_messages = unread_messages
                except Exception as exc:
                    self.store.record_error(
                        conversation.open_conversation_id,
                        None,
                        "read_unread_messages",
                        str(exc),
                    )
                    self._notify(
                        title=f"CEO read unread messages failed: {conversation.title}",
                        message=str(exc)[:120],
                        conversation=conversation,
                    )
                    candidate_unread_messages = context_messages
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
            )
            for message in trigger_messages:
                if self._enqueue_reply_task(conversation, message):
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
        recovered = self._conversations_with_recent_single_chat_recovery(conversations)
        recovered = self._conversations_with_recent_group_recovery(recovered)
        recovery_conversation_ids = {
            conversation.open_conversation_id
            for conversation in recovered
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
        if FAST_PATH_UNREAD_BACKOFF <= timedelta(0):
            return self._conversations_updated_since_fast_path_check(conversations)
        return self._conversations_after_fast_path_unread_backoff(conversations)

    def _conversations_after_fast_path_unread_backoff(
        self,
        conversations: list[DingTalkConversation],
    ) -> list[DingTalkConversation]:
        if FAST_PATH_UNREAD_BACKOFF <= timedelta(0):
            return conversations
        return [
            conversation
            for conversation in conversations
            if self._fast_path_unread_backoff_ready(conversation)
        ]

    def _fast_path_unread_backoff_ready(
        self,
        conversation: DingTalkConversation,
    ) -> bool:
        now = self._now().astimezone(timezone.utc)
        key = self._fast_path_unread_backoff_state_key(conversation)
        signature = self._fast_path_unread_signature(conversation)
        raw_state = self.store.get_service_state(key)
        first_seen_at = self._fast_path_unread_backoff_first_seen_at(
            raw_state,
            signature,
        )
        if first_seen_at is None:
            self.store.set_service_state(
                key,
                json.dumps(
                    {
                        "signature": signature,
                        "first_seen_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return False
        if now - first_seen_at.astimezone(timezone.utc) < FAST_PATH_UNREAD_BACKOFF:
            return False
        self.store.set_service_state(
            key,
            json.dumps(
                {
                    "signature": signature,
                    "first_seen_at": now.isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return True

    @staticmethod
    def _fast_path_unread_backoff_first_seen_at(
        raw_state: str | None,
        signature: str,
    ) -> datetime | None:
        if not raw_state:
            return None
        try:
            state = json.loads(raw_state)
        except json.JSONDecodeError:
            return None
        if state.get("signature") != signature:
            return None
        first_seen_at = state.get("first_seen_at")
        if not isinstance(first_seen_at, str):
            return None
        try:
            parsed = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _fast_path_unread_backoff_state_key(
        conversation: DingTalkConversation,
    ) -> str:
        return (
            MESSAGE_FAST_PATH_UNREAD_BACKOFF_STATE_PREFIX
            + conversation.open_conversation_id
        )

    @staticmethod
    def _fast_path_unread_signature(conversation: DingTalkConversation) -> str:
        return ":".join(
            [
                str(conversation.unread_point),
                str(conversation.last_message_create_at or ""),
            ]
        )

    def _service_state_datetime(self, key: str) -> datetime | None:
        value = self.store.get_service_state(key)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

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

    def _conversations_with_recent_group_recovery(
        self,
        conversations: list[DingTalkConversation],
    ) -> list[DingTalkConversation]:
        existing_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        since_utc = (
            self.now_provider().astimezone(timezone.utc) - GROUP_READ_RECOVERY_WINDOW
        ).strftime("%Y-%m-%d %H:%M:%S")
        recovered = []
        for record in self.store.list_recent_group_conversations(
            since_utc,
            limit=GROUP_READ_RECOVERY_LIMIT,
        ):
            if record.conversation_id in existing_ids:
                continue
            existing_ids.add(record.conversation_id)
            recovered.append(
                DingTalkConversation(
                    open_conversation_id=record.conversation_id,
                    title=record.title,
                    single_chat=False,
                    unread_point=0,
                )
            )
        return [*conversations, *recovered]

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
        for task in self.store.claim_reply_tasks(limit):
            conversation = DingTalkConversation(
                open_conversation_id=task.conversation_id,
                title=task.conversation_title,
                single_chat=task.single_chat,
                unread_point=1,
            )
            try:
                should_complete_task = self._process_queued_task(conversation, task)
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
                    self.store.requeue_reply_task(task.id, error)
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
                self.store.complete_reply_task(task.id)
                processed_tasks += 1
            else:
                self.store.defer_reply_task(task.id, "dry_run")
        return processed_tasks

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
        context_messages = self.dws.read_recent_messages(conversation)
        unread_messages = self.dws.read_unread_messages(conversation)
        prompt_context_messages = self._prompt_context_messages(
            context_messages, unread_messages
        )
        trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
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
            raise_on_delivery_failure=True,
        )
        return True

    def _enqueue_reply_task(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> bool:
        return self.store.enqueue_reply_task(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            trigger_message_id=trigger.open_message_id,
            trigger_create_time=trigger.create_time,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
        )

    @staticmethod
    def _reply_task_trigger_messages(
        conversation: DingTalkConversation,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        if not messages:
            return []
        if conversation.single_chat:
            return [messages[-1]]
        return DingTalkAutoReplyWorker._coalesce_consecutive_messages_by_sender(
            messages
        )

    @staticmethod
    def _coalesce_consecutive_messages_by_sender(
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        grouped: list[list[DingTalkMessage]] = []
        for message in messages:
            sender_key = DingTalkAutoReplyWorker._message_sender_key(message)
            if grouped and DingTalkAutoReplyWorker._message_sender_key(
                grouped[-1][-1]
            ) == sender_key:
                grouped[-1].append(message)
            else:
                grouped.append([message])
        return [
            DingTalkAutoReplyWorker._coalesced_message(group)
            for group in grouped
        ]

    @staticmethod
    def _message_sender_key(message: DingTalkMessage) -> str:
        return (
            message.sender_user_id
            or message.sender_open_dingtalk_id
            or message.sender_name
        )

    @staticmethod
    def _coalesced_message(messages: list[DingTalkMessage]) -> DingTalkMessage:
        if len(messages) == 1:
            return messages[0]
        latest = messages[-1]
        content = "\n\n".join(
            f"[{message.create_time}] {message.content}" for message in messages
        )
        raw_payload = dict(latest.raw_payload)
        raw_payload["coalesced_message_ids"] = [
            message.open_message_id for message in messages
        ]
        return latest.model_copy(
            update={
                "content": content,
                "raw_payload": raw_payload,
            }
        )

    def _mentioned_messages_by_conversation(
        self, conversations: list[DingTalkConversation]
    ) -> dict[str, list[DingTalkMessage]]:
        try:
            messages = self.dws.read_mentioned_messages(limit=100)
        except Exception as exc:
            self.store.record_error(None, None, "read_mentioned_messages", str(exc))
            self._notify(
                title="CEO read mentioned messages failed",
                message=str(exc)[:120],
            )
            return {}
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
            grouped.setdefault(message.open_conversation_id, []).append(message)
        return grouped

    def _broadcast_messages_by_conversation(self) -> dict[str, list[DingTalkMessage]]:
        try:
            messages = self.dws.read_broadcast_messages(
                broadcast_mention_aliases(),
                limit=100,
                lookback_hours=24,
            )
        except Exception as exc:
            self.store.record_error(None, None, "read_broadcast_messages", str(exc))
            self._notify(
                title="CEO read broadcast messages failed",
                message=str(exc)[:120],
            )
            return {}
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
        context_messages = self.dws.read_recent_messages(conversation)
        unread_messages = self.dws.read_unread_messages(conversation)
        prompt_context_messages = self._prompt_context_messages(
            context_messages, unread_messages
        )
        candidates = [
            message
            for message in prompt_context_messages
            if message.open_message_id == message_id
        ]
        if not candidates:
            raise ValueError(
                f"message not found in recent DingTalk context: {message_id}"
            )
        trigger = candidates[-1]
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
        )
        return trigger.open_message_id

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

    def _handle_oa_approval_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
        *,
        ignore_existing_attempt: bool = False,
        oa_url_override: str = "",
    ) -> bool:
        if self.oa_approval_runner is None:
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
        result = self.oa_approval_runner.handle(
            trigger_text=trigger.content,
            context_text=self._oa_approval_context_text(context_messages),
            oa_url=oa_url,
            approval_detail_text=approval_detail_text,
            execute=False,
        )
        target_status = self._oa_target_status_for_current_user(
            approval_detail_text,
            result.task_id,
        )
        effective_oa_task_id = result.task_id
        target_error = ""
        if target_status is False:
            effective_oa_task_id = ""
            target_error = "oa_task_not_current_user"
        action_result = {}
        send_status = "dry_run"
        send_error = ""
        if not self.dry_run:
            has_approval_target = bool(
                result.process_instance_id.strip() and effective_oa_task_id.strip()
            )
            if has_approval_target:
                try:
                    action_result = self.dws.execute_oa_approval_action(
                        result.process_instance_id,
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
            codex_session_id=getattr(self.oa_approval_runner, "last_session_id", "")
            or "",
            codex_transcript_start_line=getattr(
                self.oa_approval_runner, "last_transcript_start_line", 0
            ),
            codex_transcript_end_line=getattr(
                self.oa_approval_runner, "last_transcript_end_line", 0
            ),
            audit_documents_json=json.dumps(
                result.audit_documents,
                ensure_ascii=False,
            ),
            audit_tool_events_json=json.dumps(
                getattr(self.oa_approval_runner, "last_audit_tool_events", []),
                ensure_ascii=False,
            ),
            audit_summary=result.audit_summary,
            oa_process_instance_id=result.process_instance_id,
            oa_task_id=effective_oa_task_id,
            oa_url=result.oa_url,
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
            documents["current_user_id_error"] = str(exc)
        for key, reader in (
            ("dws_detail", self.dws.read_oa_approval_detail),
            ("dws_records", self.dws.read_oa_approval_records),
            ("dws_tasks", self.dws.read_oa_approval_tasks),
        ):
            try:
                documents[key] = reader(process_instance_id)
            except Exception as exc:
                documents[key] = {"error": str(exc)}
        if self._oa_detail_has_empty_form(documents.get("dws_detail")):
            try:
                documents["openapi_detail"] = self.dws.read_oa_process_instance_openapi(
                    process_instance_id
                )
            except Exception as exc:
                documents["openapi_detail"] = {"error": str(exc)}
        return json.dumps(documents, ensure_ascii=False)

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
        raise_on_delivery_failure: bool = False,
    ) -> bool:
        calendar_context = self._calendar_invite_context(conversation, trigger)
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
            self._process_batch(
                conversation,
                [trigger],
                [
                    *context_messages,
                    self._calendar_invite_prompt_message(
                        conversation, trigger, calendar_context
                    ),
                ],
                ignore_existing_attempt=True,
                raise_on_delivery_failure=raise_on_delivery_failure,
                calendar_accept_event=calendar_context.invite,
            )
            return True
        self._process_batch(
            conversation,
            [trigger],
            [
                *context_messages,
                self._calendar_conflict_prompt_message(
                    conversation, trigger, calendar_context
                ),
            ],
            ignore_existing_attempt=True,
            raise_on_delivery_failure=raise_on_delivery_failure,
        )
        return True

    def _calendar_invite_context(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> CalendarConflictContext | None:
        if not self._is_calendar_message(message):
            return None
        calendar_invite_from_message = getattr(
            self.dws,
            "calendar_invite_from_message",
            None,
        )
        list_calendar_events = getattr(self.dws, "list_calendar_events", None)
        if calendar_invite_from_message is None or list_calendar_events is None:
            return None
        invite = calendar_invite_from_message(message)
        if invite is None:
            invite = self._calendar_pending_invite_from_sender(
                message,
                list_calendar_events,
            )
            if invite is None:
                return None
        events = list_calendar_events(invite.start_time, invite.end_time)
        conflicts = [
            event
            for event in events
            if self._calendar_events_conflict(invite, event)
            and not self._same_calendar_event(invite, event)
            and event.status != "cancelled"
            and self._calendar_event_blocks_time(event)
        ]
        return CalendarConflictContext(invite=invite, conflicts=conflicts)

    def _calendar_pending_invite_from_sender(
        self,
        message: DingTalkMessage,
        list_calendar_events: Callable[[str, str], list[DwsCalendarEvent]],
    ) -> DwsCalendarEvent | None:
        sender_name = message.sender_name.strip()
        if not sender_name:
            return None
        start, end = self._calendar_pending_invite_search_window(message)
        events = list_calendar_events(start, end)
        candidates = [
            event
            for event in events
            if event.organizer.strip() == sender_name
            and event.status == "confirmed"
            and event.self_response_status == "needsAction"
        ]
        time_matched_candidates = [
            event
            for event in candidates
            if self._calendar_event_changed_near_message(event, message)
        ]
        if len(time_matched_candidates) == 1:
            return time_matched_candidates[0]
        if len(candidates) != 1:
            return None
        return candidates[0]

    @staticmethod
    def _calendar_event_changed_near_message(
        event: DwsCalendarEvent,
        message: DingTalkMessage,
    ) -> bool:
        message_time_ms = int(
            DingTalkAutoReplyWorker._message_create_time_as_instant(
                message
            ).timestamp()
            * 1000
        )
        tolerance_ms = CALENDAR_PENDING_INVITE_EVENT_MATCH_SECONDS * 1000
        return any(
            event_time_ms > 0 and abs(event_time_ms - message_time_ms) <= tolerance_ms
            for event_time_ms in (event.created_ms, event.updated_ms)
        )

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
    def _calendar_missing_description_reply(
        context: CalendarConflictContext,
    ) -> str:
        conflict_titles = "、".join(
            event.title or "未命名日程" for event in context.conflicts[:3]
        )
        invite_title = context.invite.title or "这场会议"
        if not conflict_titles:
            return (
                f"我这边看到「{invite_title}」没有会议描述。请补充一下参加理由、"
                "希望我决策或输入的内容，以及为什么需要我参加。"
            )
        return (
            f"我这边看到「{invite_title}」和已有日程「{conflict_titles}」时间冲突，"
            "但这场会议没有会议描述。请补充一下参加理由、希望我决策或输入的内容，以及为什么需要优先于现有日程。"
        )

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
            "请评估这场新会议的描述是否足以优先于重叠会议。",
            "如果理由充分，回复中说明建议接受这场会议并调整或拒绝哪个重叠会议；如果说明不足以取消另一个重叠会议，回复对方原因并请补充。",
            "",
            f"新会议：{context.invite.title or '未命名日程'}",
            f"时间：{context.invite.start_time} - {context.invite.end_time}",
            f"组织者：{context.invite.organizer or trigger.sender_name}",
            f"会议描述：{context.invite.description.strip()}",
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
            "请按日历规则判断是否需要聊天回复，或是否可以接受日程。",
            "如果日程是在要求审批、批阅、review、反馈或评论某个文档内容，reply_text 必须是：请直接@我文档让我批阅即可，只有存疑再约会。",
            f"如果日程描述明确，且 {principal_display_name()} 本人参与对业务判断、关键客户、关键产品、核心人事或跨部门决策有明确价值，action 输出 no_reply，reason 说明 calendar_auto_accept。",
            "如果描述或价值不明确，不要输出 no_reply；应追问补充信息或 handoff。",
            "",
            f"新会议：{context.invite.title or '未命名日程'}",
            f"时间：{context.invite.start_time} - {context.invite.end_time}",
            f"组织者：{context.invite.organizer or trigger.sender_name}",
            f"会议描述：{context.invite.description.strip()}",
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

    def _accept_calendar_invite(
        self,
        *,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        new_messages: list[DingTalkMessage],
        event: DwsCalendarEvent,
        attempt_id: int,
        reason: str,
        raise_on_delivery_failure: bool = False,
    ) -> None:
        if not event.event_id.strip():
            error = "missing_calendar_event_id"
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=error,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_accept",
                error,
            )
            self._notify(
                title=f"CEO calendar accept failed: {conversation.title}",
                message=error,
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(error)
            return
        if self.dry_run:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="dry_run",
            )
            return
        try:
            self.dws.respond_calendar_event(event.event_id, "accepted")
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_accept",
                str(exc),
            )
            self._notify(
                title=f"CEO calendar accept failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return
        self.store.update_reply_attempt(
            attempt_id,
            send_status="skipped",
            send_error="",
        )
        self._mark_seen(new_messages)
        if reason:
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "calendar_auto_accept",
                reason,
            )

    def _record_system_or_notification_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        existing_attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            message.open_message_id,
        )
        if (
            existing_attempt
            and existing_attempt.action == CodexAction.NO_REPLY.value
            and existing_attempt.send_status == "skipped"
        ):
            return
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=message.open_message_id,
            trigger_sender=message.sender_name,
            trigger_text=message.content,
            action=CodexAction.NO_REPLY.value,
            sensitivity_kind="general",
            codex_reason="system_or_notification_message",
            audit_summary="系统类或通知类消息，无需自动回复。",
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="skipped",
            send_error="no_reply",
        )

    def _record_stale_message_skip(
        self,
        conversation: DingTalkConversation,
        message: DingTalkMessage,
    ) -> None:
        existing_attempt = self.store.get_latest_reply_attempt_for_trigger(
            conversation.open_conversation_id,
            message.open_message_id,
        )
        if (
            existing_attempt
            and existing_attempt.action == CodexAction.NO_REPLY.value
            and existing_attempt.send_status == "skipped"
        ):
            return
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=message.open_message_id,
            trigger_sender=message.sender_name,
            trigger_text=message.content,
            action=CodexAction.NO_REPLY.value,
            sensitivity_kind="general",
            codex_reason="message_older_than_24h",
            audit_summary="消息超过最近 24 小时窗口，不自动回复。",
        )
        self.store.update_reply_attempt(
            attempt_id,
            send_status="skipped",
            send_error="no_reply",
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
    def _is_link_caption_only(content: str) -> bool:
        if not MEDIA_OR_LINK_PATTERN.search(content):
            return False
        if not DINGTALK_INTERNAL_OR_RENDERED_MEDIA_PATTERN.search(content):
            return False
        if DINGTALK_DOC_URL_PATTERN.search(content):
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
        if conversation.single_chat:
            eligible_messages = messages
        else:
            eligible_messages = [
                message for message in messages if message.addresses_principal()
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

        latest_seen_context_time: str | None = None
        for message in context_messages:
            if self.store.has_seen(message.open_message_id):
                latest_seen_context_time = max(
                    latest_seen_context_time or message.create_time,
                    message.create_time,
                )
        if latest_seen_context_time is None:
            return sorted(result, key=lambda message: message.create_time)

        for message in context_messages:
            if message.create_time <= latest_seen_context_time:
                continue
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
        calendar_accept_event: DwsCalendarEvent | None = None,
    ) -> None:
        trigger = new_messages[-1]
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            new_messages,
            raise_on_delivery_failure=raise_on_delivery_failure,
        ):
            return
        try:
            linked_documents = self._read_linked_documents(
                new_messages, context_messages
            )
            image_paths, image_download_errors = self._collect_image_paths(
                new_messages,
                context_messages,
            )
        except Exception as exc:
            self._record_linked_document_error(conversation, trigger, str(exc))
            if raise_on_delivery_failure:
                raise ReplyTaskProcessingError(str(exc)) from exc
            return
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
            image_download_errors=image_download_errors,
        )
        before_session_id = getattr(self.codex, "last_session_id", None)
        decision = self.codex.decide(
            prompt=prompt,
            session_id=session_id,
            image_paths=image_paths,
        )
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
        if self._is_stale_codex_resume(decision, session_id):
            self.store.clear_codex_session(conversation.open_conversation_id)
            session_id = None
            prompt = self._build_prompt(
                conversation,
                new_messages,
                context_messages,
                include_thread_prompt=True,
                linked_documents=linked_documents,
                image_download_errors=image_download_errors,
            )
            before_session_id = getattr(self.codex, "last_session_id", None)
            decision = self.codex.decide(
                prompt=prompt,
                session_id=None,
                image_paths=image_paths,
            )
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
        )

        if decision.action == CodexAction.NO_REPLY:
            if (
                calendar_accept_event is not None
                and decision.reason.strip().startswith("calendar_auto_accept")
            ):
                self._accept_calendar_invite(
                    conversation=conversation,
                    trigger=trigger,
                    new_messages=new_messages,
                    event=calendar_accept_event,
                    attempt_id=attempt_id,
                    reason=decision.reason,
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
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=decision.reason,
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "codex",
                decision.reason,
            )
            self._notify(
                title=f"CEO agent error: {conversation.title}",
                message=decision.reason[:120],
                conversation=conversation,
            )
            if raise_on_delivery_failure:
                raise ReplyTaskProcessingError(decision.reason)
            return
        if decision.action == CodexAction.HANDOFF_TO_HUMAN:
            handoff_reply_text = self._format_reply_delivery_text(
                conversation,
                trigger,
                HANDOFF_ACK,
                [],
            )
            if self.dry_run:
                self.store.update_reply_attempt(
                    attempt_id,
                    final_reply_text=handoff_reply_text,
                    send_status="dry_run",
                )
                self._notify(
                    title=f"CEO handoff: {conversation.title}",
                    message=trigger.content[:120],
                    conversation=conversation,
                )
                return
            try:
                self._ding_self(
                    self._handoff_ding_text(
                        conversation=conversation,
                        trigger=trigger,
                        context_messages=context_messages,
                    )
                )
                self._send_reply_to_trigger(
                    conversation,
                    trigger,
                    handoff_reply_text,
                    open_dingtalk_id=trigger.sender_open_dingtalk_id,
                )
            except Exception as exc:
                self.store.update_reply_attempt(
                    attempt_id,
                    final_reply_text=handoff_reply_text,
                    send_status="failed",
                    send_error=str(exc),
                )
                self.store.record_error(
                    conversation.open_conversation_id,
                    trigger.open_message_id,
                    "handoff_delivery",
                    str(exc),
                )
                self._notify(
                    title=f"CEO handoff failed: {conversation.title}",
                    message=str(exc)[:120],
                    conversation=conversation,
                )
                if raise_on_delivery_failure:
                    raise ReplyDeliveryError(str(exc)) from exc
                return
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "handoff",
                decision.reason,
            )
            self.store.update_reply_attempt(
                attempt_id,
                final_reply_text=handoff_reply_text,
                send_status="sent",
            )
            self._mark_seen(new_messages)
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
                raise_on_delivery_failure=raise_on_delivery_failure,
            )
            return

        self._send_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            reply_text=decision.reply_text,
            reason=decision.reason,
            attempt_id=attempt_id,
            raise_on_delivery_failure=raise_on_delivery_failure,
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
        if attempt.send_status in {"sent", "skipped", "blocked"}:
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

    def _read_linked_documents(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[LinkedDocumentContext]:
        documents: list[LinkedDocumentContext] = []
        referenced_messages = self._referenced_document_messages(
            new_messages, context_messages
        )
        for url in self._dingtalk_doc_urls(referenced_messages):
            documents.append(self._read_linked_alidocs_node(url))
        for file_name in self._referenced_file_names(new_messages, context_messages):
            document = self._read_referenced_file(file_name)
            if document is not None:
                documents.append(document)
        return documents

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
                "image",
            )
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
    def _download_url_from_payload(payload: dict) -> str:
        for key in ("downloadUrl", "resourceUrl", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        result = payload.get("result")
        if isinstance(result, dict):
            return DingTalkAutoReplyWorker._download_url_from_payload(result)
        return ""

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

    @staticmethod
    def _payload_data(payload: dict[str, object]) -> dict[str, object]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

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
        error: str,
    ) -> None:
        reason = f"linked_dingtalk_doc_read_failed: {error}"
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
            error,
        )
        self._notify(
            title=f"CEO doc read failed: {conversation.title}",
            message=error[:120],
            conversation=conversation,
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
        send_at_users = [] if conversation.single_chat else at_users
        final_reply_text = self._format_reply_delivery_text(
            conversation,
            trigger,
            attempt.final_reply_text,
            send_at_users,
        )
        self._deliver_final_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt.id,
            final_reply_text=final_reply_text,
            at_users=send_at_users,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else None,
            raise_on_delivery_failure=raise_on_delivery_failure,
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
        raise_on_delivery_failure: bool = False,
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
            )
            return
        try:
            at_users = self._reply_at_users(trigger)
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
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            return
        direct_user_id = at_users[0] if conversation.single_chat and at_users else None
        send_at_users = [] if conversation.single_chat else at_users
        reply_text = append_signature(reply_text)
        reply_text = self._format_reply_delivery_text(
            conversation,
            trigger,
            reply_text,
            send_at_users,
        )
        if contains_forbidden_leak(reply_text):
            regenerated_reply_text = self._regenerate_reply_after_leak_check(
                blocked_reply_text=reply_text,
            )
            if regenerated_reply_text:
                reply_text = append_signature(regenerated_reply_text)
                reply_text = self._format_reply_delivery_text(
                    conversation,
                    trigger,
                    reply_text,
                    send_at_users,
                )
        self._deliver_final_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            attempt_id=attempt_id,
            final_reply_text=reply_text,
            at_users=send_at_users,
            direct_user_id=direct_user_id,
            direct_open_dingtalk_id=trigger.sender_open_dingtalk_id
            if conversation.single_chat
            else None,
            raise_on_delivery_failure=raise_on_delivery_failure,
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
            "请基于同一个上下文重新输出合法 JSON，只改写 reply_text，不要解释。\n"
            "reply_text 不要引用来源、不要加脚注编号、不要写参考文献，"
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
        direct_user_id: str | None,
        direct_open_dingtalk_id: str | None,
        raise_on_delivery_failure: bool = False,
    ) -> None:
        reply_text = final_reply_text
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
            )
            return

        self._notify(
            title=f"CEO auto reply: {conversation.title}",
            message=reply_text,
            conversation=conversation,
        )
        if self.dry_run:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="dry_run",
            )
            return
        try:
            retry_count, send_result = self._send_reply_to_trigger_with_retry(
                conversation,
                trigger,
                reply_text,
                user_id=direct_user_id,
                open_dingtalk_id=direct_open_dingtalk_id,
            )
        except Exception as exc:
            self.store.update_reply_attempt(
                attempt_id,
                send_status="failed",
                send_error=str(exc),
                retry_count=0
                if getattr(exc, "needs_authorization", False)
                else max(self.send_attempts - 1, 0),
            )
            self.store.record_error(
                conversation.open_conversation_id,
                trigger.open_message_id,
                "send",
                str(exc),
            )
            if raise_on_delivery_failure:
                raise ReplyDeliveryError(str(exc)) from exc
            self._notify(
                title=f"CEO auto reply failed: {conversation.title}",
                message=str(exc)[:120],
                conversation=conversation,
            )
            return
        self.store.update_reply_attempt(
            attempt_id,
            send_status="sent",
            retry_count=retry_count,
        )
        self.store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json=json.dumps(send_result or {}, ensure_ascii=False),
            recall_key=DwsClient.extract_recall_key(send_result),
        )
        self._mark_seen(new_messages)

    def _send(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ):
        if self.dry_run:
            return None
        return self.dws.send_message(
            conversation_id,
            text,
            at_users=at_users,
            user_id=user_id,
            open_dingtalk_id=open_dingtalk_id,
        )

    def _send_reply_to_trigger(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ):
        if self.dry_run:
            return None
        if conversation.single_chat:
            return self._send(
                None,
                text,
                user_id=user_id,
                open_dingtalk_id=None
                if user_id
                else open_dingtalk_id or trigger.sender_open_dingtalk_id,
            )
        return self.dws.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
        )

    def _send_reply_to_trigger_with_retry(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> tuple[int, dict | None]:
        errors: list[str] = []
        for attempt_number in range(1, self.send_attempts + 1):
            try:
                send_result = self._send_reply_to_trigger(
                    conversation,
                    trigger,
                    text,
                    user_id=user_id,
                    open_dingtalk_id=open_dingtalk_id,
                )
                return attempt_number - 1, send_result
            except Exception as exc:
                if getattr(exc, "needs_authorization", False):
                    raise exc
                errors.append(f"attempt {attempt_number}: {exc}")
        raise RuntimeError(" | ".join(errors))

    def _send_with_retry(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> tuple[int, dict | None]:
        errors: list[str] = []
        for attempt_number in range(1, self.send_attempts + 1):
            try:
                send_result = self._send(
                    conversation_id,
                    text,
                    at_users=at_users,
                    user_id=user_id,
                    open_dingtalk_id=open_dingtalk_id,
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

    def _reply_at_users(self, trigger: DingTalkMessage) -> list[str]:
        current_user_id = self.store.get_current_user_id()
        sender_user_id = trigger.sender_user_id or self.dws.resolve_message_sender(
            trigger
        )
        users: list[str] = []
        for user_id in [sender_user_id, *trigger.mentioned_user_ids]:
            if not user_id:
                continue
            if current_user_id and user_id == current_user_id:
                continue
            if user_id not in users:
                users.append(user_id)
        return users

    @staticmethod
    def _format_reply_text(
        trigger: DingTalkMessage, reply_text: str, at_users: list[str]
    ) -> str:
        quote = DingTalkAutoReplyWorker._fake_quote(trigger)
        placeholders = " ".join(f"<@{user_id}>" for user_id in at_users)
        if placeholders:
            return f"{quote}\n\n{placeholders} {reply_text}"
        return f"{quote}\n\n{reply_text}"

    @staticmethod
    def _format_reply_delivery_text(
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        reply_text: str,
        at_users: list[str],
    ) -> str:
        if conversation.single_chat:
            return DingTalkAutoReplyWorker._format_reply_text(
                trigger,
                reply_text,
                at_users,
            )
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

    @staticmethod
    def _fake_quote(trigger: DingTalkMessage) -> str:
        normalized = DingTalkAutoReplyWorker._quote_source_text(trigger.content)
        normalized = redact_forbidden_leak_markers(normalized)
        excerpt = DingTalkAutoReplyWorker._truncate_quote_text(
            normalized,
            unit_limit=QUOTE_INFORMATION_UNIT_LIMIT,
        )
        return f"> {trigger.sender_name}: {excerpt}"

    @staticmethod
    def _quote_source_text(text: str) -> str:
        without_links = MEDIA_OR_LINK_PATTERN.sub(" ", text)
        without_mentions = QUOTE_MENTION_PATTERN.sub(" ", without_links)
        normalized = " ".join(without_mentions.split()).lstrip("，,。；;：:、?？!！")
        return normalized or "原消息"

    @staticmethod
    def _truncate_quote_text(text: str, unit_limit: int) -> str:
        matches = list(QUOTE_WORD_OR_CJK_PATTERN.finditer(text))
        if len(matches) <= unit_limit:
            return text
        end_index = matches[unit_limit - 1].end()
        return f"{text[:end_index].rstrip()}..."

    def _notify(
        self,
        title: str,
        message: str,
        conversation: DingTalkConversation | None = None,
    ) -> None:
        send_macos_notification(
            title=title,
            message=message,
            url=self._notification_url(conversation),
        )

    def _notification_url(self, conversation: DingTalkConversation | None) -> str | None:
        if conversation is None:
            return None
        client_conversation_id = self._client_conversation_id(
            conversation.open_conversation_id
        )
        if not client_conversation_id:
            return None
        return (
            f"{notification_bridge_base_url()}/open-dingtalk"
            f"?cid={quote(client_conversation_id, safe='')}"
        )

    def _client_conversation_id(self, open_conversation_id: str) -> str:
        if open_conversation_id in self._client_conversation_id_cache:
            return self._client_conversation_id_cache[open_conversation_id]
        resolver = getattr(self.dws, "client_conversation_id", None)
        if not callable(resolver):
            self._client_conversation_id_cache[open_conversation_id] = ""
            return ""
        try:
            client_conversation_id = str(resolver(open_conversation_id) or "")
        except Exception as exc:
            self.store.record_error(
                open_conversation_id,
                None,
                "dingtalk_client_cid",
                str(exc),
            )
            client_conversation_id = ""
        self._client_conversation_id_cache[open_conversation_id] = (
            client_conversation_id
        )
        return client_conversation_id

    def _mark_seen(self, messages: list[DingTalkMessage]) -> None:
        if self.dry_run:
            return
        for message in messages:
            self.store.mark_seen(message.open_message_id, message.open_conversation_id)

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
        self, query: str, *, limit: int = 3, candidate_limit: int = 50
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
        query_chars = set(query)
        scored: list[tuple[int, ReplyAttempt]] = []
        for attempt in attempts:
            haystack = "\n".join(
                [
                    attempt.conversation_title,
                    attempt.trigger_sender,
                    attempt.trigger_text,
                    attempt.codex_reason,
                    attempt.reviewer_feedback,
                    attempt.corrected_reply_text,
                ]
            )
            score = len(query_chars & set(haystack))
            if score > 0:
                scored.append((score, attempt))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return []

        minimum_score = max(3, int(scored[0][0] * 0.35))
        return [attempt for score, attempt in scored[:limit] if score >= minimum_score]

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
