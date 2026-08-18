import errno
import hashlib
import io
import json
import logging
import os
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs, unquote, urlparse, urlsplit, urlunsplit

from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    ManualRerunInstruction,
    MaterialReference,
    PriorReceipt,
)
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult, DecisionOption
from app.agent_orchestrator import AgentOrchestrator, OrchestrationResult
from app.audit_agent import AuditAgentRunner
from app.channel_gate import (
    ChannelGate,
    ChannelGateResult,
    ChannelGateState,
    LoginCoordinator,
    default_channel_gates,
    start_lark_auth_login,
)
from app.consumer_agent import ConsumerAgentRunner
from app.config import (
    agent_mention_aliases,
    assistant_signature,
    broadcast_mention_aliases,
    codex_capacity_retry_duration,
    env_duration,
    fast_path_unread_backoff_duration,
    handoff_ack,
    message_recovery_interval,
    single_chat_read_recovery_limit,
    single_chat_read_recovery_window,
)
from app.corpus import MEDIA_OR_LINK_PATTERN, count_information_units
from app.dws_client import (
    DINGTALK_MESSAGE_TIME_ZONE,
    DwsCalendarEvent,
    DwsClient,
    DwsError,
)
from app.corpus import (
    CorpusRecord,
    extract_retrieval_keywords,
)
from app.dingtalk_models import (
    DingTalkConversation,
    DingTalkMessage,
)
from app.codex_runner import (
    recover_native_codex_auth_failures,
    selected_codex_model_provider,
)
from app.codex_capacity import (
    CODEX_CAPACITY_EXHAUSTED_MESSAGE,
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    CODEX_PROVIDER_UNAVAILABLE,
    is_codex_capacity_exhausted,
    is_codex_provider_recovery_code,
)
from app.notification import (
    dismiss_browser_notification,
    dingtalk_conversation_notification_url,
    send_browser_notification,
    send_macos_notification,
)
from app.native_cli_metadata import describe_native_command
from app.leak_check import contains_forbidden_leak, redact_forbidden_leak_markers
from app.oa_approval import extract_oa_url
from app.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    refresh_org_cache,
)
from app.permission import PermissionGate
from app.prompt import MaterialReferenceContext
from app.store import (
    AgentRun,
    AgentRunLeaseLostError,
    FAST_PATH_UNREAD_BACKOFF_TASK_ERROR,
    AutoReplyStore,
    ReplyTask,
)
from app.work_profile import safe_excerpt
from PIL import Image, UnidentifiedImageError


ORCHESTRATION_ATTEMPT_STATUS = {
    "executed": ("completed", "done"),
    "no_action": ("skipped", "done"),
    "needs_human": ("needs_human", "done"),
    "failed_retryable": ("failed", "pending"),
    "failed_terminal": ("failed", "failed"),
    "unknown": ("pending_reconciliation", "pending"),
}
RESOURCE_DEADLOCK_WAIT_ERROR = "os_resource_deadlock_wait"
logger = logging.getLogger(__name__)

HANDOFF_ACK = handoff_ack()
HANDOFF_TEXT_EMOTION = "我去叫"
HANDOFF_NOTIFICATION_PREFIX = "【CEO Agent 转人工通知】"
# Historical auto-ack marker. Keep filtering it from context, but do not send
# new processing acknowledgements before final replies.
PROCESSING_ACK = "收到，我正在处理（by 分身）"
CODEX_LOGIN_REQUIRED_PREFIX = "codex_login_required"
CODEX_PROVIDER_AUTH_FAILED_PREFIX = "codex_provider_auth_failed"
CODEX_PROVIDER_UNAVAILABLE_PREFIX = CODEX_PROVIDER_UNAVAILABLE
CRITICAL_INFO_UNAVAILABLE_PREFIX = "critical_info_unavailable:"
XIAOQING_CRITICAL_INFO_UNAVAILABLE_MARKER = (
    f"{CRITICAL_INFO_UNAVAILABLE_PREFIX}xiaoqing_interview"
)
DEFAULT_TEXT_EMOTION_BACKGROUND_ID = "im_bg_5"
SPLIT_PERSON_SIGNATURE = assistant_signature()
STALE_PROCESSING_TASK_SECONDS = 30 * 60
MAX_REPLY_TASK_ATTEMPTS = 3
REPLY_TASK_RETRY_BASE_DELAY_SECONDS = 60
REPLY_TASK_RETRY_MAX_DELAY_SECONDS = 15 * 60
RECOVERABLE_AGENT_RUNTIME_ERRORS = frozenset(
    {
        "codex_process_failed",
        "codex_process_timeout",
        "codex_result_invalid",
        "codex_result_missing",
        "codex_stream_invalid",
    }
)
INVALID_AGENT_RESULT_ERRORS = frozenset(
    {"codex_result_invalid", "codex_result_missing"}
)
STALE_CODEX_RESUME_ATTEMPTS = 2
CALENDAR_PENDING_INVITE_LOOKAHEAD_DAYS = 14
CALENDAR_PENDING_INVITE_EVENT_MATCH_SECONDS = 5 * 60
CALENDAR_PENDING_INVITE_NO_CHANGE_TIME_START_LOOKAHEAD = timedelta(hours=24)
CALENDAR_CONTEXT_MATCH_MIN_SCORE = 0.05
CALENDAR_CONTEXT_MATCH_LOOKBACK = timedelta(minutes=10)
CALENDAR_ORGANIZER_RESPONSE_ERROR = "Cannot change response status of event organizer"
CALENDAR_EVENT_NOT_FOUND_ERROR = "Event does not exist"
OA_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=14)
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
LARK_DOC_URL_PATTERN = re.compile(
    r"https://[^\s)\]]+/(?:docx|wiki)/[^\s)\]]+",
    re.IGNORECASE,
)


def _is_codex_login_required_error(reason: str) -> bool:
    normalized = reason.lower()
    return (
        "failed to refresh token" in normalized
        and ("session has ended" in normalized or "invalid refresh token" in normalized)
    ) or "token_invalidated" in normalized


def _is_codex_provider_auth_error(reason: str) -> bool:
    normalized = reason.lower()
    responses_api_auth_failed = (
        "unexpected status 401 unauthorized" in normalized
        and (
            "missing bearer or basic authentication" in normalized
            or "invalid api key" in normalized
        )
        and "/v1/responses" in normalized
    )
    chatgpt_codex_forbidden = (
        "unexpected status 403 forbidden" in normalized
        and "chatgpt.com/backend-api/codex/responses" in normalized
    )
    return responses_api_auth_failed or chatgpt_codex_forbidden


def _codex_provider_auth_error(reason: str) -> str:
    normalized = reason.lower()
    if "missing bearer or basic authentication" in normalized:
        detail = "OpenAI Responses API was called without a bearer/basic auth header"
    elif "invalid api key" in normalized:
        detail = "configured Codex model provider rejected its API key"
    elif (
        "unexpected status 403 forbidden" in normalized
        and "chatgpt.com/backend-api/codex/responses" in normalized
    ):
        detail = "ChatGPT Codex backend rejected the service session with 403 Forbidden"
    else:
        detail = "Codex model provider authentication failed"
    return (
        f"{CODEX_PROVIDER_AUTH_FAILED_PREFIX}: {detail}; "
        "native codex exec selected a Responses API model provider without "
        "usable provider credentials; verify codex exec works in the service "
        "environment before rerunning"
    )


def _is_codex_provider_transport_error(reason: str) -> bool:
    normalized = reason.lower()
    if "/v1/responses" not in normalized:
        return False
    native_missing_auth_header = (
        "unexpected status 401 unauthorized" in normalized
        and "missing bearer or basic authentication" in normalized
        and selected_codex_model_provider() == "openai"
    )
    return (
        "stream disconnected before completion" in normalized
        or "error sending request" in normalized
        or "process produced no output" in normalized
        or native_missing_auth_header
    )


def _codex_provider_transport_error(reason: str) -> str:
    normalized = reason.lower()
    if "process produced no output" in normalized:
        detail = "Codex provider request produced no output before the idle timeout"
    elif "missing bearer or basic authentication" in normalized:
        detail = "native Codex temporarily omitted the authenticated request header"
    else:
        detail = "Codex provider request disconnected before completion"
    return (
        f"{CODEX_PROVIDER_UNAVAILABLE_PREFIX}: {detail}; "
        "wait for network/provider recovery or verify native codex exec works "
        "in the service environment before rerunning"
    )


def _is_dingteam_okr_login_error(reason: str) -> bool:
    normalized = reason.strip().lower()
    return (
        "dingteam okr" in normalized
        and "api error 103" in normalized
        and "未登录" in reason
    )


def _normalize_codex_stop_error_reason(reason: str) -> str:
    if is_codex_capacity_exhausted(reason):
        return CODEX_PROVIDER_CAPACITY_EXHAUSTED
    if _is_codex_provider_recovery_wait_reason(reason):
        return reason
    if _is_codex_provider_auth_error(reason):
        return _codex_provider_auth_error(reason)
    if _is_codex_login_required_error(reason):
        return f"{CODEX_LOGIN_REQUIRED_PREFIX}: {reason}"
    if _is_codex_provider_transport_error(reason):
        return _codex_provider_transport_error(reason)
    return reason


def _is_codex_provider_recovery_wait_reason(reason: str) -> bool:
    return is_codex_provider_recovery_code(reason)


def _is_terminal_codex_auth_failure(reason: str) -> bool:
    return reason.startswith(
        (CODEX_PROVIDER_AUTH_FAILED_PREFIX, CODEX_LOGIN_REQUIRED_PREFIX)
    )


def _extract_text_emotion_id(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("emotionId", "emotion_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
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
            if isinstance(value, int) and not isinstance(value, bool):
                return str(value)
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


FILE_MESSAGE_PATTERN = re.compile(r"^\s*\[文件]\s*(?P<name>.+?)\s*$")
DINGTALK_FILE_ID_PATTERN = re.compile(r"(?:^|\s)fileId:\s*(?P<file_id>\S+)")
IMAGE_MESSAGE_MEDIA_ID_PATTERN = re.compile(
    r"\[图片消息]\(mediaId=(?P<media_id>[^)]+)\)"
)
DINGTALK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GROUP_CONTEXT_RECOVERY_WINDOW = timedelta(hours=24)
RECENT_REPLY_WINDOW = timedelta(hours=24)
RECENT_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=7)
REFERENCED_FILE_CONTEXT_WINDOW = timedelta(minutes=10)
DOWNLOADED_FILE_MAX_BYTES = 50 * 1024 * 1024
DOWNLOADED_IMAGE_MAX_BYTES = 20 * 1024 * 1024
DWS_UPGRADE_CHECKED_DATE_STATE_KEY = "dws_upgrade_checked_date"
DWS_UPGRADE_CHECK_RESULT_STATE_KEY = "dws_upgrade_check_result"
MESSAGE_RECOVERY_CHECKED_AT_STATE_KEY = "message_recovery_checked_at"
MESSAGE_FAST_PATH_CHECKED_AT_STATE_KEY = "message_fast_path_checked_at"
ROBOT_DIRECT_MESSAGE_LOOKBACK = env_duration(
    "CEO_ROBOT_DIRECT_MESSAGE_LOOKBACK",
    timedelta(hours=4),
)
DWS_AUTH_LOGIN_STATE_KEY = "channel_login_request:dingtalk"
DWS_PAT_AUTHORIZATION_STATE_KEY = "dws_pat_authorization"
DWS_PAT_AUTHORIZATION_REQUEST_SUPPRESSION_WINDOW = timedelta(hours=1)
DWS_FORBIDDEN_CONVERSATIONS_STATE_KEY = "dws_forbidden_conversations"
DWS_FORBIDDEN_CONVERSATION_COOLDOWN = timedelta(minutes=5)
ORG_CACHE_REFRESH_INTERVAL = timedelta(days=7)
MESSAGE_RECOVERY_INTERVAL = message_recovery_interval()
FAST_PATH_UNREAD_BACKOFF = fast_path_unread_backoff_duration()
SINGLE_CHAT_READ_RECOVERY_WINDOW = single_chat_read_recovery_window()
SINGLE_CHAT_READ_RECOVERY_LIMIT = single_chat_read_recovery_limit()


@dataclass(frozen=True)
class CalendarConflictContext:
    invite: DwsCalendarEvent
    conflicts: list[DwsCalendarEvent]


class ReplyTaskProcessingError(RuntimeError):
    """Raised after recording a processing failure so queued tasks can retry."""


class DwsAuthorizationRequiredError(ReplyTaskProcessingError):
    """Raised when DWS auth is not ready before starting a Codex agent."""

    needs_authorization = True


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
        channel_gates: dict[str, ChannelGate] | None = None,
        login_coordinator: LoginCoordinator | None = None,
        agent_orchestrator: AgentOrchestrator | None = None,
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
        self.channel_gates = channel_gates or default_channel_gates(
            dws_binary=str(getattr(dws, "dws_bin", "dws"))
        )
        self.login_coordinator = login_coordinator or LoginCoordinator(
            store=store,
            launchers={
                "dingtalk": lambda: getattr(
                    self.dws, "dws", self.dws
                ).start_auth_login(),
                "lark": start_lark_auth_login,
            },
            now=lambda: self._now().astimezone(timezone.utc),
        )
        self._pass_channel_results: dict[str, ChannelGateResult] = {}
        self._task_image_paths: dict[int, set[Path]] = {}
        self.agent_orchestrator = agent_orchestrator

    def _agent_orchestrator(self) -> AgentOrchestrator:
        if self.agent_orchestrator is not None:
            return self.agent_orchestrator
        runner = getattr(self.codex, "runner", None)
        workspace = getattr(runner, "workspace", None)
        if workspace is None:
            raise RuntimeError("native Codex runner workspace is unavailable")
        codex_bin = str(getattr(runner, "codex_bin", "codex"))
        self.agent_orchestrator = AgentOrchestrator(
            store=self.store,
            consumer=ConsumerAgentRunner(
                store=self.store,
                workspace=Path(workspace),
                codex_bin=codex_bin,
            ),
            audit=AuditAgentRunner(
                store=self.store,
                workspace=Path(workspace),
                codex_bin=codex_bin,
                dry_run=self.dry_run,
            ),
        )
        return self.agent_orchestrator

    @staticmethod
    def _default_material_read_commands(
        kind: str,
        reference: str,
    ) -> tuple[str, ...]:
        quoted_reference = shlex.quote(reference)
        if kind == "dingtalk_doc":
            return (
                f"dws doc info --node {quoted_reference} --format json",
                f"dws doc read --node {quoted_reference} --format json",
            )
        if kind == "dingtalk_minutes":
            return (f"dws minutes get info --id {quoted_reference} --format json",)
        if kind == "lark_doc":
            return (
                "lark-cli docs +fetch "
                f"--doc {quoted_reference} --doc-format markdown --format json --as bot",
            )
        return ()

    def run_once(self, max_batches: int | None = None) -> None:
        self.produce_once(max_tasks=max_batches)
        self.consume_once(max_tasks=max_batches)

    def _call_dws(
        self,
        kind: str,
        call: Callable[[], T],
        *,
        conversation_id: str | None = None,
        message_id: str | None = None,
        notify_title: str | None = None,
        raise_authorization: bool = False,
        raise_errors: bool = False,
        record_forbidden_error: bool = True,
        allow_missing_direct_chat_target: bool = False,
        default: T,
    ) -> T:
        try:
            result = call()
            self._clear_dws_transient_error(kind)
            if conversation_id:
                self._clear_dws_read_forbidden(conversation_id)
            return result
        except Exception as exc:
            if raise_authorization and self._is_authorization_error(exc):
                raise
            pat_authorization_requested = False
            is_login_error = self._is_dws_login_error(exc)
            if is_login_error:
                if self._ensure_dws_auth_login(exc) and not raise_errors:
                    return default
            else:
                required_scopes = self._dws_authorization_required_scopes(exc)
                if required_scopes:
                    pat_authorization_requested = self._ensure_dws_pat_authorization(
                        exc
                    )
            is_forbidden_read = bool(
                conversation_id and self._is_dws_forbidden_read_error(exc)
            )
            if is_forbidden_read:
                self._mark_dws_read_forbidden(conversation_id)
            should_notify = bool(notify_title)
            should_record_error = record_forbidden_error or not is_forbidden_read
            if pat_authorization_requested:
                should_record_error = False
                should_notify = False
            if (
                self._is_dws_transient_error(exc)
                or self._is_dws_token_verified_read_error(kind, exc)
                or self._is_dws_message_read_retryable_error(kind, exc)
            ):
                self._record_dws_transient_error(kind, str(exc))
                should_record_error = False
                should_notify = False
            elif (
                allow_missing_direct_chat_target
                and self._is_missing_direct_chat_target_error(exc)
            ):
                self._clear_dws_transient_error(kind)
                should_record_error = False
                should_notify = False
            if should_record_error:
                self.store.record_error(conversation_id, message_id, kind, str(exc))
            if should_notify and notify_title:
                self._notify(
                    title=notify_title,
                    message=str(exc)[:120],
                )
            if raise_errors:
                raise
            return default

    @staticmethod
    def _is_dws_transient_error(exc: Exception) -> bool:
        if not isinstance(exc, DwsError):
            return False
        if exc.code in DwsClient.RETRYABLE_ERROR_CODES:
            return True
        normalized = str(exc).casefold()
        return any(
            marker in normalized
            for marker in DwsClient.STRUCTURED_NETWORK_ERROR_MARKERS
        )

    @staticmethod
    def _is_dws_message_read_kind(kind: str) -> bool:
        return kind in {
            "list_unread_conversations",
            "read_unread_messages",
            "read_recent_messages",
            "read_mentioned_messages",
            "read_broadcast_messages",
            "read_robot_direct_messages",
            "list_messages_by_ids",
            "read_recent_messages_fallback",
            "read_unread_messages_fallback",
            "read_recent_messages_calendar_context",
            "read_recent_messages_rerun",
            "read_unread_messages_rerun",
            "list_messages_by_ids_rerun",
        }

    @staticmethod
    def _is_dws_token_verified_read_error(kind: str, exc: Exception) -> bool:
        return (
            DingTalkAutoReplyWorker._is_dws_message_read_kind(kind)
            and isinstance(exc, DwsError)
            and exc.code in DwsClient.TOKEN_VERIFIED_RETRYABLE_ERROR_CODES
        )

    @staticmethod
    def _is_dws_message_read_retryable_error(kind: str, exc: Exception) -> bool:
        return (
            DingTalkAutoReplyWorker._is_dws_message_read_kind(kind)
            and isinstance(exc, DwsError)
            and (
                DwsClient.is_message_read_retryable_error_code(exc.code)
                # DWS returns this generic code before it invokes a read tool.
                # It is retryable for reads, but must not mask write failures.
                or exc.code in DwsClient.GENERIC_BUSINESS_RETRYABLE_ERROR_CODES
            )
        )

    @staticmethod
    def _is_missing_direct_chat_target_error(exc: Exception) -> bool:
        return (
            isinstance(exc, DwsError)
            and exc.code == DwsError.DIRECT_CHAT_TARGET_NOT_FOUND_CODE
        )

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
        raise_errors: bool = False,
        default: T,
    ) -> T:
        if self._is_dws_read_forbidden(conversation.open_conversation_id):
            if raise_errors:
                raise RuntimeError("conversation_context_refresh_forbidden")
            return default
        return self._call_dws(
            kind,
            reader,
            conversation_id=conversation.open_conversation_id,
            message_id=message_id,
            raise_authorization=raise_authorization,
            raise_errors=raise_errors,
            record_forbidden_error=False,
            allow_missing_direct_chat_target=conversation.single_chat,
            default=default,
        )

    def _channel_result(self, channel: str) -> ChannelGateResult:
        cached = self._pass_channel_results.get(channel)
        if cached is not None:
            return cached
        gate = self.channel_gates.get(channel)
        result = (
            gate.check()
            if gate is not None
            else ChannelGateResult(
                channel=channel,
                state=ChannelGateState.BLOCKED,
                reason_code="gate_not_configured",
            )
        )
        self._pass_channel_results[channel] = result
        checked_at = self._now().astimezone(timezone.utc).isoformat()
        self.store.set_service_state(
            f"channel_gate:{channel}",
            json.dumps(
                {
                    "status": result.state.value,
                    "reason_code": result.reason_code,
                    "checked_at": checked_at,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        if result.state is ChannelGateState.READY:
            self.store.set_service_state(
                f"channel_gate_last_success:{channel}",
                checked_at,
            )
        self.login_coordinator.handle(result)
        return result

    def _required_channels_ready(self, channels: set[str]) -> bool:
        results = [self._channel_result(channel) for channel in sorted(channels)]
        return all(result.state is ChannelGateState.READY for result in results)

    def required_channels_for_task(self, task: ReplyTask) -> set[str]:
        channels = {task.channel}
        try:
            payload = json.loads(task.trigger_message_json)
        except (json.JSONDecodeError, TypeError):
            payload = None
        references = [task.oa_url]
        references.extend(DingTalkAutoReplyWorker._task_reference_strings(payload))
        for value in references:
            for token in value.split():
                candidate = token.strip("()[]{}<>\"',.;，。；：")
                host = (urlsplit(candidate).hostname or "").casefold()
                if DingTalkAutoReplyWorker._host_matches(
                    host, ("dingtalk.com", "alidocs.com")
                ):
                    channels.add("dingtalk")
                if DingTalkAutoReplyWorker._host_matches(
                    host, ("feishu.cn", "larksuite.com", "larkoffice.com")
                ):
                    channels.add("lark")
        return channels

    @staticmethod
    def _task_reference_strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from DingTalkAutoReplyWorker._task_reference_strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from DingTalkAutoReplyWorker._task_reference_strings(nested)

    @staticmethod
    def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)

    def produce_once(self, max_tasks: int | None = None) -> int:
        if max_tasks == 0:
            return 0
        self._pass_channel_results = {}
        if not self._required_channels_ready({"dingtalk"}):
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
            conversations = []
        else:
            self._mark_dws_auth_healthy()
        if conversations and not recovery_due:
            conversations = self._conversations_due_for_fast_path(conversations)
        unread_conversation_ids = {
            conversation.open_conversation_id for conversation in conversations
        }
        robot_direct_messages = self._robot_direct_messages_by_conversation()
        mentioned_messages = self._mentioned_messages_by_conversation(conversations)
        agent_named_messages = self._agent_named_messages_by_conversation()
        broadcast_messages = self._broadcast_messages_by_conversation()
        addressed_messages = self._merge_message_groups(
            robot_direct_messages,
            mentioned_messages,
            agent_named_messages,
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
        conversations = self._prioritize_conversations_with_messages(
            conversations,
            robot_direct_messages,
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
            candidate_source_messages = self._discard_service_handoff_notifications(
                candidate_source_messages
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
    def _calendar_event_has_attendee(
        event: DwsCalendarEvent, attendee_name: str
    ) -> bool:
        expected = attendee_name.strip()
        if not expected:
            return False
        return any(attendee.strip() == expected for attendee in event.attendees)

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
            conversation.open_conversation_id for conversation in conversations
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
        if isinstance(exc, DwsError) and exc.needs_authorization:
            return False
        detail = str(exc).lower()
        permission_denied = (
            "forbidden request" in detail
            or "auth_permission_denied" in detail
            or "permission denied" in detail
        )
        if not permission_denied:
            return False
        if isinstance(exc, DwsError):
            return exc.code in {None, "1001"}
        return True

    def _mark_dws_read_forbidden(self, conversation_id: str) -> None:
        forbidden_until = (
            self._now().astimezone(timezone.utc) + DWS_FORBIDDEN_CONVERSATION_COOLDOWN
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
            str(key): value for key, value in payload.items() if isinstance(value, str)
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
            last_checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
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
        except Exception as exc:
            self.store.set_service_state(
                DWS_UPGRADE_CHECK_RESULT_STATE_KEY,
                json.dumps(
                    {
                        "status": "check_failed",
                        "checked_at": self._now().astimezone(timezone.utc).isoformat(),
                        "detail": str(exc),
                    },
                    ensure_ascii=False,
                ),
            )
            self.store.set_service_state(DWS_UPGRADE_CHECKED_DATE_STATE_KEY, today)
            return
        self.store.set_service_state(
            DWS_UPGRADE_CHECK_RESULT_STATE_KEY,
            json.dumps(
                {
                    "status": "ok",
                    "checked_at": self._now().astimezone(timezone.utc).isoformat(),
                    "needs_upgrade": upgrade_check.get("needs_upgrade") is True,
                    "current_version": str(upgrade_check.get("current_version") or ""),
                    "latest_version": str(upgrade_check.get("latest_version") or ""),
                },
                ensure_ascii=False,
            ),
        )
        try:
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

    def _dws_pat_authorization_state(self) -> dict[str, Any]:
        raw = self.store.get_service_state(DWS_PAT_AUTHORIZATION_STATE_KEY)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _set_dws_pat_authorization_state(self, state: dict[str, Any]) -> None:
        self.store.set_service_state(
            DWS_PAT_AUTHORIZATION_STATE_KEY,
            json.dumps(state, ensure_ascii=False, sort_keys=True),
        )

    def _dws_pat_authorization_request_is_recent(
        self, state: dict[str, Any], scopes: tuple[str, ...]
    ) -> bool:
        if state.get("status") != "requested":
            return False
        state_scopes = state.get("scopes")
        if not isinstance(state_scopes, list) or tuple(state_scopes) != scopes:
            return False
        requested_at = state.get("requested_at")
        if not isinstance(requested_at, str):
            return False
        requested = self._parse_service_state_datetime(requested_at)
        if requested is None:
            return False
        age = self._now().astimezone(timezone.utc) - requested.astimezone(timezone.utc)
        return timedelta(0) <= age < DWS_PAT_AUTHORIZATION_REQUEST_SUPPRESSION_WINDOW

    @classmethod
    def _dws_authorization_required_scopes(cls, exc: Exception) -> tuple[str, ...]:
        scopes: list[str] = []
        current: BaseException | None = exc
        while current is not None:
            required_scopes = getattr(current, "required_scopes", ())
            if isinstance(required_scopes, (list, tuple)):
                scopes.extend(
                    scope
                    for scope in required_scopes
                    if isinstance(scope, str) and scope.strip()
                )
            current = current.__cause__
        return tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))

    def _ensure_dws_pat_authorization(self, exc: Exception) -> bool:
        scopes = self._dws_authorization_required_scopes(exc)
        state = self._dws_pat_authorization_state()
        now = self._now().astimezone(timezone.utc).isoformat()
        if not scopes:
            self._set_dws_pat_authorization_state(
                {
                    "status": "blocked",
                    "reason": str(exc),
                    "error": "missing PAT required scopes",
                    "updated_at": now,
                }
            )
            self._notify(
                title="CEO DWS PAT authorization blocked",
                message="DWS requested authorization but did not return required scopes.",
            )
            return False
        if self._dws_pat_authorization_request_is_recent(state, scopes):
            return True
        start_pat_authorization = getattr(self.dws, "start_pat_authorization", None)
        if not callable(start_pat_authorization):
            self._set_dws_pat_authorization_state(
                {
                    "status": "failed",
                    "reason": str(exc),
                    "scopes": list(scopes),
                    "error": "DWS client does not support PAT authorization",
                    "updated_at": now,
                }
            )
            return False
        try:
            process = start_pat_authorization(list(scopes))
        except Exception as start_exc:
            self.store.record_error(None, None, "dws_pat_authorization", str(start_exc))
            self._set_dws_pat_authorization_state(
                {
                    "status": "failed",
                    "reason": str(exc),
                    "scopes": list(scopes),
                    "error": str(start_exc),
                    "requested_at": now,
                    "updated_at": now,
                }
            )
            self._notify(
                title="CEO DWS PAT authorization failed",
                message=str(start_exc)[:120],
            )
            return False
        request_state: dict[str, Any] = {
            "status": "requested",
            "reason": str(exc),
            "scopes": list(scopes),
            "requested_at": now,
            "updated_at": now,
        }
        pid = getattr(process, "pid", None)
        if isinstance(pid, int):
            request_state["pid"] = pid
        self._set_dws_pat_authorization_state(request_state)
        self._notify(
            title="CEO DWS PAT authorization required",
            message=(
                f"Started DWS PAT authorization for {len(scopes)} scope(s). "
                "Please complete DingTalk authorization."
            ),
        )
        return True

    def _ensure_dws_auth_login(self, exc: Exception) -> bool:
        del exc
        result = self._channel_result("dingtalk")
        handled = self.login_coordinator.handle(result)
        return (
            result.state is ChannelGateState.READY
            or handled.launched
            or handled.suppressed
        )

    def _mark_dws_auth_healthy(self) -> None:
        self.login_coordinator.handle(
            ChannelGateResult(
                channel="dingtalk",
                state=ChannelGateState.READY,
                reason_code="ready",
            )
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
        self._pass_channel_results = {}
        limit = max_tasks if max_tasks is not None else 50
        processed_tasks = 0
        self._backfill_confirmed_direct_reply_ledgers(limit=limit)
        self._recover_due_unknown_agent_reply_tasks(limit=limit)
        self.store.suspend_reconciliation_event_limited_agent_runs()
        self._recover_stale_agent_reply_tasks()
        recover_native_codex_auth_failures(self.store, channel="dingtalk")
        if self.store.active_codex_capacity_pause(now=self._now()):
            return 0
        claimed_tasks = 0
        scan_now = self._sqlite_timestamp(self._now())
        max_task_id = self.store.max_pending_reply_task_id(
            now=scan_now,
            channel="dingtalk",
        )
        for pending_task in self._pending_reply_task_candidates(
            page_size=min(max(limit * 4, 50), 200),
            now=scan_now,
            max_id=max_task_id,
        ):
            if claimed_tasks >= limit:
                break
            if not self._required_channels_ready(
                self.required_channels_for_task(pending_task)
            ):
                continue
            task = self.store.claim_reply_task(
                pending_task.id,
                now=self._sqlite_timestamp(self._now()),
            )
            if task is None:
                continue
            claimed_tasks += 1
            conversation = DingTalkConversation(
                open_conversation_id=task.conversation_id,
                title=task.conversation_title,
                single_chat=task.single_chat,
                unread_point=1,
            )
            run_snapshot = self._agent_run_snapshot(task)
            try:
                completed = self._process_queued_task(conversation, task)
            except AgentRunLeaseLostError:
                continue
            except Exception as exc:
                error = str(exc)
                if self._is_pre_agent_resource_deadlock(
                    task,
                    prior_run_snapshot=run_snapshot,
                    exc=exc,
                ):
                    self.store.defer_reply_task(
                        task.id,
                        RESOURCE_DEADLOCK_WAIT_ERROR,
                        expected_execution_generation=task.execution_generation,
                        available_at=self._reply_task_retry_available_at(
                            max(task.attempts, 1)
                        ),
                    )
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "reply_task_resource_deadlock_wait",
                        error,
                    )
                    continue
                authorization_wait_error = _normalize_codex_stop_error_reason(error)
                terminal_codex_auth_failure = _is_terminal_codex_auth_failure(
                    authorization_wait_error
                )
                if terminal_codex_auth_failure:
                    error = authorization_wait_error
                if not terminal_codex_auth_failure and (
                    self._is_authorization_error(exc)
                    or _is_codex_provider_recovery_wait_reason(
                        authorization_wait_error
                    )
                ):
                    provider_recovery = _is_codex_provider_recovery_wait_reason(
                        authorization_wait_error
                    )
                    capacity_exhausted = is_codex_capacity_exhausted(
                        authorization_wait_error
                    )
                    notify_authorization_wait = (
                        not capacity_exhausted
                        and task.error.strip() != authorization_wait_error
                    )
                    required_scopes = self._dws_authorization_required_scopes(exc)
                    authorization_requested = bool(required_scopes) and (
                        self._ensure_dws_pat_authorization(exc)
                    )
                    authorization_cannot_start = (
                        not provider_recovery
                        and not capacity_exhausted
                        and self._is_authorization_error(exc)
                        and not authorization_requested
                    )
                    terminal_authorization_error = (
                        f"{authorization_wait_error}: authorization request has no "
                        "actionable scopes"
                        if authorization_cannot_start
                        else authorization_wait_error
                    )
                    if (
                        not provider_recovery
                        and not capacity_exhausted
                        and (
                            authorization_cannot_start
                            or task.attempts >= self.max_task_attempts
                        )
                    ):
                        try:
                            task_status, attempt_id = self._record_agent_runtime_failure_attempt(
                                task,
                                terminal_authorization_error,
                                retryable=False,
                                prior_run_snapshot=run_snapshot,
                            )
                        except AgentRunLeaseLostError:
                            continue
                        self.store.record_error(
                            task.conversation_id,
                            task.trigger_message_id,
                            "reply_task_authorization_exhausted",
                            terminal_authorization_error,
                        )
                        self._notify_problem_attempt(
                            task,
                            attempt_id=attempt_id,
                            send_status="failed",
                            message=terminal_authorization_error,
                        )
                        continue
                    try:
                        defer = (
                            self.store.defer_reply_task
                            if provider_recovery or capacity_exhausted
                            else self.store.defer_reply_task_for_authorization
                        )
                        defer(
                            task.id,
                            authorization_wait_error,
                            expected_execution_generation=task.execution_generation,
                            available_at=(
                                self._codex_capacity_retry_available_at()
                                if capacity_exhausted
                                else self._reply_task_retry_available_at(task.attempts)
                                if provider_recovery
                                else self._reply_task_authorization_available_at()
                            ),
                        )
                    except AgentRunLeaseLostError:
                        continue
                    if not capacity_exhausted or self._open_codex_capacity_pause():
                        self.store.record_error(
                            task.conversation_id,
                            task.trigger_message_id,
                            (
                                "codex_capacity_pause"
                                if capacity_exhausted
                                else (
                                    "reply_task_provider_recovery"
                                    if provider_recovery
                                    else "reply_task_authorization"
                                )
                            ),
                            (
                                CODEX_CAPACITY_EXHAUSTED_MESSAGE
                                if capacity_exhausted
                                else authorization_wait_error
                            ),
                        )
                    if notify_authorization_wait:
                        notification_prefix = (
                            "CEO task waiting for Codex provider recovery: "
                            if provider_recovery
                            else "CEO task waiting for authorization: "
                        )
                        self._notify(
                            title=notification_prefix + task.conversation_title,
                            message=authorization_wait_error[:120],
                            conversation=conversation,
                        )
                    continue
                try:
                    failed_run = self._latest_failed_agent_run(task, run_snapshot)
                    clean_session_retry = (
                        error in INVALID_AGENT_RESULT_ERRORS
                        and task.attempts == self.max_task_attempts
                        and failed_run is not None
                        and bool(failed_run.codex_session_id)
                        and self.store.get_codex_session_id(task.conversation_id)
                        == failed_run.codex_session_id
                    )
                    if error in RECOVERABLE_AGENT_RUNTIME_ERRORS:
                        if failed_run is not None:
                            conversation_session = self.store.get_codex_session_id(
                                task.conversation_id
                            )
                            if conversation_session == failed_run.codex_session_id:
                                self.store.clear_codex_session_if_matches(
                                    task.conversation_id,
                                    failed_run.codex_session_id,
                                )
                            self.store.clear_agent_run_session(
                                task.id,
                                task.execution_generation,
                                role=failed_run.role,
                                proposal_revision=failed_run.proposal_revision,
                                turn_attempt=failed_run.turn_attempt,
                            )
                    task_status, attempt_id = self._record_agent_runtime_failure_attempt(
                        task,
                        error,
                        retryable=not terminal_codex_auth_failure,
                        prior_run_snapshot=run_snapshot,
                        allow_clean_session_retry=clean_session_retry,
                    )
                except AgentRunLeaseLostError:
                    continue
                if task_status == "pending":
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "reply_task_retry",
                        error,
                    )
                    continue
                self.store.record_error(
                    task.conversation_id,
                    task.trigger_message_id,
                    "reply_task",
                    error,
                )
                self._notify_problem_attempt(
                    task,
                    attempt_id=attempt_id,
                    send_status="failed",
                    message=error,
                )
                continue
            if completed:
                processed_tasks += 1
        return processed_tasks

    def _recover_due_unknown_agent_reply_tasks(self, *, limit: int) -> int:
        recovered = self.store.settle_done_unknown_audit_runs_with_sent_reply(
            limit=limit
        )
        for run in self.store.list_unknown_agent_runs(limit=limit):
            task = self.store.get_reply_task(run.reply_task_id)
            if task is None:
                continue
            try:
                if task.status == "failed":
                    self.store.requeue_failed_unknown_audit_reconciliation(
                        task.id,
                        run.id,
                        reason="unknown_agent_run_reconciliation",
                    )
                else:
                    self.store.requeue_reply_task(
                        task.id,
                        "unknown_agent_run_reconciliation",
                        expected_execution_generation=run.execution_generation,
                    )
            except (AgentRunLeaseLostError, ValueError):
                continue
            recovered += 1
        return recovered

    def _recover_stale_agent_reply_tasks(self) -> None:
        stale_tasks = self.store.list_stale_processing_reply_tasks(
            STALE_PROCESSING_TASK_SECONDS
        )
        if not stale_tasks:
            return
        for task in stale_tasks:
            runs = self.store.list_agent_runs_for_task_generation(
                task.id,
                task.execution_generation,
            )
            if not runs:
                recovery_error = "stale_before_agent_start"
                if (
                    task.channel == "wechat"
                    and task.error == "wechat_read_only_decision_running"
                ):
                    recovery_error = "interrupted_read_only_decision"
                self.store.requeue_reply_task(
                    task.id,
                    recovery_error,
                    expected_execution_generation=task.execution_generation,
                )
                continue
            self.store.requeue_reply_task(
                task.id,
                "stale_agent_turn_recovery",
                expected_execution_generation=task.execution_generation,
            )

    def _record_agent_runtime_failure_attempt(
        self,
        task: ReplyTask,
        error: str,
        *,
        retryable: bool,
        prior_run_snapshot: dict[int, tuple[object, ...]],
        allow_clean_session_retry: bool = False,
    ) -> tuple[str, int]:
        run = self._latest_failed_agent_run(task, prior_run_snapshot)
        task_status = (
            "pending"
            if retryable
            and (
                task.attempts < self.max_task_attempts
                or allow_clean_session_retry
            )
            else "failed"
        )
        available_at = (
            self._reply_task_retry_available_at(task.attempts)
            if task_status == "pending"
            else ""
        )
        if run is not None and run.status == "failed":
            attempt_id = self.store.finalize_orchestrated_reply_task(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                run_id=run.id,
                task_status=task_status,
                task_error=error,
                available_at=available_at,
                conversation_id=task.conversation_id,
                conversation_title=task.conversation_title,
                trigger_message_id=task.trigger_message_id,
                trigger_sender=task.trigger_sender,
                trigger_text=task.trigger_text,
                codex_reason=error,
                codex_session_id=run.codex_session_id,
                codex_transcript_start_line=run.transcript_start_line,
                codex_transcript_end_line=run.transcript_end_line,
                audit_tool_events_json=json.dumps(
                    run.tool_events,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                audit_summary=error,
                send_status="failed",
                send_error=error,
                channel=task.channel,
            )
        else:
            attempt_id = self.store.finalize_reply_task_without_run(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                task_status=task_status,
                task_error=error,
                available_at=available_at,
                conversation_id=task.conversation_id,
                conversation_title=task.conversation_title,
                trigger_message_id=task.trigger_message_id,
                trigger_sender=task.trigger_sender,
                trigger_text=task.trigger_text,
                codex_reason=error,
                audit_summary=error,
                send_status="failed",
                send_error=error,
                channel=task.channel,
            )
        # consume_once emits the terminal notification after recording the
        # persisted attempt, so the notification inbox has the same source.
        return task_status, attempt_id

    @staticmethod
    def _agent_run_fingerprint(run: AgentRun) -> tuple[object, ...]:
        return (
            run.status,
            run.codex_session_id,
            run.transcript_end_line,
            run.final_result_json,
            run.structured_error_json,
            run.side_effect_state,
            run.completed_at,
            run.updated_at,
        )

    def _agent_run_snapshot(self, task: ReplyTask) -> dict[int, tuple[object, ...]]:
        return {
            run.id: self._agent_run_fingerprint(run)
            for run in self.store.list_agent_runs_for_task_generation(
                task.id,
                task.execution_generation,
            )
        }

    def _latest_failed_agent_run(
        self,
        task: ReplyTask,
        prior_run_snapshot: dict[int, tuple[object, ...]],
    ) -> AgentRun | None:
        runs = self.store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
        )
        return next(
            (
                item
                for item in reversed(runs)
                if item.status == "failed"
                and prior_run_snapshot.get(item.id)
                != self._agent_run_fingerprint(item)
            ),
            None,
        )

    def _is_pre_agent_resource_deadlock(
        self,
        task: ReplyTask,
        *,
        prior_run_snapshot: dict[int, tuple[object, ...]],
        exc: Exception,
    ) -> bool:
        """Retry only locks that occurred before this task made agent progress."""
        is_deadlock = (
            isinstance(exc, OSError)
            and exc.errno == errno.EDEADLK
        ) or "resource deadlock avoided" in str(exc).casefold()
        if not is_deadlock:
            return False
        current_runs = self.store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
        )
        return all(
            prior_run_snapshot.get(run.id) == self._agent_run_fingerprint(run)
            for run in current_runs
        )

    def _pending_reply_task_candidates(
        self, *, page_size: int, now: str, max_id: int | None
    ) -> Iterator[ReplyTask]:
        if max_id is None:
            return
        reconciliation_tasks = self.store.peek_pending_reconciliation_reply_tasks(
            page_size,
            now=now,
            channel="dingtalk",
            max_id=max_id,
        )
        reconciliation_task_ids = {task.id for task in reconciliation_tasks}
        yield from reconciliation_tasks
        after_id: int | None = None
        while True:
            page = self.store.peek_reply_tasks(
                page_size,
                now=now,
                channel="dingtalk",
                after_id=after_id,
                max_id=max_id,
            )
            if not page:
                return
            yield from (
                task for task in page if task.id not in reconciliation_task_ids
            )
            after_id = page[-1].id

    def _reply_task_retry_available_at(self, attempts: int) -> str:
        delay_seconds = min(
            REPLY_TASK_RETRY_BASE_DELAY_SECONDS * (2 ** max(attempts - 1, 0)),
            REPLY_TASK_RETRY_MAX_DELAY_SECONDS,
        )
        return self._sqlite_timestamp(
            self._now().astimezone(timezone.utc) + timedelta(seconds=delay_seconds)
        )

    def _codex_capacity_retry_available_at(self) -> str:
        return self._sqlite_timestamp(
            self._now().astimezone(timezone.utc) + codex_capacity_retry_duration()
        )

    def _open_codex_capacity_pause(self) -> bool:
        return self.store.open_codex_capacity_pause(
            retry_at=self._codex_capacity_retry_available_at(),
            now=self._now(),
        )

    def _reply_task_authorization_available_at(self) -> str:
        return self._sqlite_timestamp(
            self._now().astimezone(timezone.utc)
            + timedelta(seconds=REPLY_TASK_RETRY_MAX_DELAY_SECONDS)
        )

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
        _context_messages, prompt_context_messages = (
            self._queued_task_prompt_context_messages(conversation, trigger)
        )
        return self._process_agent_queued_task(
            conversation,
            task,
            trigger,
            prompt_context_messages,
        )

    def _process_agent_queued_task(
        self,
        conversation: DingTalkConversation,
        task: ReplyTask,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> bool:
        try:
            context = self._build_agent_task_context(
                conversation=conversation,
                task=task,
                trigger=trigger,
                context_messages=context_messages,
            )
            result = self._agent_orchestrator().process(
                task,
                context,
                refresh_context=lambda: self._refresh_agent_task_context(
                    conversation=conversation,
                    task=task,
                    trigger=trigger,
                ),
            )
            try:
                return self._apply_orchestration_result(task, result)
            except AgentRunLeaseLostError:
                return False
        finally:
            self._cleanup_task_image_paths(task.id)

    def _refresh_agent_task_context(
        self,
        *,
        conversation: DingTalkConversation,
        task: ReplyTask,
        trigger: DingTalkMessage,
    ) -> AgentTaskContext:
        _context_messages, prompt_context_messages = (
            self._queued_task_prompt_context_messages(
                conversation,
                trigger,
                strict=True,
            )
        )
        return self._build_agent_task_context(
            conversation=conversation,
            task=task,
            trigger=trigger,
            context_messages=prompt_context_messages,
        )

    def _apply_orchestration_result(
        self,
        task: ReplyTask,
        result: OrchestrationResult,
    ) -> bool:
        provider_recovery = _is_codex_provider_recovery_wait_reason(result.error.code)
        capacity_exhausted = is_codex_capacity_exhausted(result.error.code)
        authorization_wait = result.error.authorization_required
        active_recovery_wait = result.error.code in {
            "agent_run_unavailable",
            "codex_session_locked",
        }
        if result.status == "failed_retryable" and result.final_run_id == 0:
            error = result.error.code or "agent_orchestration_deferred"
            if (
                provider_recovery
                or authorization_wait
                or active_recovery_wait
                or task.attempts < self.max_task_attempts
            ):
                available_at = (
                    self._reply_task_authorization_available_at()
                    if authorization_wait
                    else (
                        self._codex_capacity_retry_available_at()
                        if capacity_exhausted
                        else self._reply_task_retry_available_at(max(task.attempts, 1))
                    )
                )
                if capacity_exhausted and self._open_codex_capacity_pause():
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        "codex_capacity_pause",
                        CODEX_CAPACITY_EXHAUSTED_MESSAGE,
                    )
                self.store.defer_reply_task(
                    task.id,
                    error,
                    expected_execution_generation=task.execution_generation,
                    available_at=available_at,
                )
                return False
            attempt_id = self.store.finalize_reply_task_without_run(
                task_id=task.id,
                expected_execution_generation=task.execution_generation,
                task_status="failed",
                task_error=error,
                available_at="",
                conversation_id=task.conversation_id,
                conversation_title=task.conversation_title,
                trigger_message_id=task.trigger_message_id,
                trigger_sender=task.trigger_sender,
                trigger_text=task.trigger_text,
                codex_reason=result.summary or error,
                audit_summary=result.summary or error,
                send_status="failed",
                send_error=error,
                channel=task.channel,
            )
            self._notify_problem_attempt(
                task,
                attempt_id=attempt_id,
                send_status="failed",
                message=result.summary or error,
            )
            return False

        try:
            send_status, task_status = ORCHESTRATION_ATTEMPT_STATUS[result.status]
        except KeyError as exc:
            raise ValueError("invalid orchestration status") from exc
        if (
            result.status == "failed_retryable"
            and task.attempts >= self.max_task_attempts
            and not provider_recovery
            and not authorization_wait
            and not active_recovery_wait
        ):
            task_status = "failed"
        send_error = result.error.code
        if result.status == "needs_human":
            send_error = send_error or "needs_human"
        elif result.status == "unknown":
            send_error = send_error or "agent_side_effect_unknown"
        elif result.status in {"failed_retryable", "failed_terminal"}:
            send_error = send_error or "agent_failed"

        available_at = ""
        if task_status == "pending":
            available_at = (
                self._reply_task_authorization_available_at()
                if authorization_wait
                else (
                    self._codex_capacity_retry_available_at()
                    if capacity_exhausted
                    else self._reply_task_retry_available_at(task.attempts)
                )
            )

        if capacity_exhausted and self._open_codex_capacity_pause():
            self.store.record_error(
                task.conversation_id,
                task.trigger_message_id,
                "codex_capacity_pause",
                CODEX_CAPACITY_EXHAUSTED_MESSAGE,
            )

        run = self.store.get_agent_run(result.final_run_id)
        if run is None:
            raise RuntimeError("orchestration final run was not persisted")
        sent_reply = self._confirmed_direct_reply_ledger_entry(
            task,
            result.consumer_result,
            result.audit_result,
        )
        decision_options: tuple[DecisionOption, ...] = (
            result.consumer_result.decision_options
            if result.consumer_result is not None
            else (
                result.audit_result.decision_options
                if result.audit_result is not None
                else ()
            )
        )
        if not decision_options and result.status == "needs_human" and result.feedback:
            decision_options = (
                DecisionOption(
                    key="apply_audit_revision",
                    label="按审计意见修订",
                    instruction=result.feedback.requested_revision,
                    consequence="Agent 会按已核验的审计意见重新规划并再次审计。",
                ),
                DecisionOption(
                    key="stop_without_action",
                    label="停止当前事项",
                    instruction="停止当前事项，不执行新的外部动作。",
                    consequence="保留审计记录并关闭当前事项。",
                ),
            )
        if not decision_options and result.status == "needs_human":
            decision_options = (
                DecisionOption(
                    key="recheck_and_offer_choices",
                    label="重新核验并给出方案",
                    instruction=(
                        "重新读取当前事实，只生成两到四个可执行选项供我选择；"
                        "不要执行新的外部动作。"
                    ),
                    consequence="Agent 只会补齐具体选项，不会直接执行外部动作。",
                ),
                DecisionOption(
                    key="stop_without_action",
                    label="停止当前事项",
                    instruction="停止当前事项，不执行新的外部动作。",
                    consequence="保留审计记录并关闭当前事项。",
                ),
            )
        attempt_id = self.store.finalize_orchestrated_reply_task(
            task_id=task.id,
            expected_execution_generation=task.execution_generation,
            run_id=run.id,
            task_status=task_status,
            task_error=send_error,
            available_at=available_at,
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            codex_reason=result.summary,
            codex_session_id=run.codex_session_id,
            codex_transcript_start_line=run.transcript_start_line,
            codex_transcript_end_line=run.transcript_end_line,
            audit_tool_events_json=json.dumps(
                run.tool_events,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            audit_summary=result.summary,
            human_decision_options_json=json.dumps(
                [option.model_dump(mode="json") for option in decision_options],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            send_status=send_status,
            send_error=send_error,
            channel=task.channel,
            preserve_attempt_budget=(
                provider_recovery or authorization_wait or active_recovery_wait
            )
            and task_status == "pending",
            sent_reply_text=sent_reply[0] if sent_reply is not None else "",
            sent_reply_result_json=sent_reply[1] if sent_reply is not None else "",
            **self._orchestration_oa_metadata(task, result),
        )
        if send_status == "needs_human" or task_status == "failed":
            self._notify_problem_attempt(
                task,
                attempt_id=attempt_id,
                send_status=send_status,
                message=result.summary or send_error,
            )
        elif task_status == "done":
            self._dismiss_problem_notification(task)
        return task_status == "done"

    def _backfill_confirmed_direct_reply_ledgers(self, *, limit: int) -> int:
        """Repair only ledger omissions proven by persisted Audit readback."""
        repaired = 0
        # Older confirmed audits include OA and group operations that have no
        # direct-message ledger contract.  Scan past them, but bound actual
        # writes to the worker batch size.
        candidate_limit = max(limit * 100, 100)
        for audit_run in self.store.list_confirmed_audit_runs_missing_sent_reply(
            limit=candidate_limit,
        ):
            task = self.store.get_reply_task(audit_run.reply_task_id)
            consumer_run = (
                self.store.get_agent_run(audit_run.parent_agent_run_id)
                if audit_run.parent_agent_run_id is not None
                else None
            )
            if task is None or consumer_run is None:
                continue
            try:
                consumer_result = ConsumerAgentResult.model_validate_json(
                    consumer_run.final_result_json
                )
                audit_result = AuditAgentResult.model_validate_json(
                    audit_run.final_result_json
                )
            except (TypeError, ValueError):
                continue
            entry = self._confirmed_direct_reply_ledger_entry(
                task,
                consumer_result,
                audit_result,
            )
            if entry is None:
                continue
            if self.store.record_confirmed_sent_reply_if_absent(
                audit_run_id=audit_run.id,
                reply_text=entry[0],
                send_result_json=entry[1],
            ):
                repaired += 1
                if repaired >= limit:
                    break
        if repaired:
            logger.info("backfilled %s confirmed direct delivery ledger row(s)", repaired)
        return repaired

    @staticmethod
    def _confirmed_direct_reply_ledger_entry(
        task: ReplyTask,
        consumer_result: ConsumerAgentResult | None,
        audit_result: AuditAgentResult | None,
    ) -> tuple[str, str] | None:
        """Return a ledger entry only for a live-confirmed, single direct reply."""
        if consumer_result is None or audit_result is None:
            return None
        proposal = consumer_result.proposal
        external_result = audit_result.external_result
        if proposal is None or external_result is None:
            return None
        if (
            audit_result.outcome.value != "executed"
            or audit_result.side_effect_state.value != "confirmed"
        ):
            return None
        reference = external_result.live_result_reference
        conversation_id = reference.get("conversation_id")
        message_id = reference.get("message_id")
        if (
            not isinstance(conversation_id, str)
            or conversation_id != task.conversation_id
            or not isinstance(message_id, str)
            or not message_id.strip()
            or len(proposal.actions) != 1
        ):
            return None
        action = proposal.actions[0]
        if action.capability != "agent_cli.dws":
            return None
        descriptor = describe_native_command(
            {"type": "command_execution", **action.payload}
        )
        if (
            descriptor is None
            or descriptor.cli != "dws"
            or descriptor.command_path not in {
                "chat message send",
                "chat +messages-send",
            }
        ):
            return None
        target_keys = set(descriptor.target_identifiers)
        target_keys.update(str(key).replace("_", "-") for key in action.target)
        if not {"open-dingtalk-id", "user"} & target_keys:
            return None
        argv = action.payload.get("argv")
        if not isinstance(argv, (list, tuple)):
            return None
        reply_text = ""
        for index, value in enumerate(argv):
            if not isinstance(value, str):
                return None
            if value == "--text" and index + 1 < len(argv):
                candidate = argv[index + 1]
                if isinstance(candidate, str):
                    reply_text = candidate
                break
            if value.startswith("--text="):
                reply_text = value.partition("=")[2]
                break
        if not reply_text.strip():
            return None
        return (
            reply_text,
            json.dumps(
                {
                    "source": "agent_audit_readback",
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "verification_summary": external_result.verification_summary,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    @staticmethod
    def _orchestration_oa_metadata(
        task: ReplyTask,
        result: OrchestrationResult,
    ) -> dict[str, str]:
        audit_result = result.audit_result
        if audit_result is None or audit_result.external_result is None:
            return {}
        reference = audit_result.external_result.live_result_reference
        process_instance_id = str(reference.get("process_instance_id") or "").strip()
        if not process_instance_id:
            return {}
        action_result = reference.get("result")
        if not isinstance(action_result, dict):
            action_result = reference
        return {
            "oa_process_instance_id": process_instance_id,
            "oa_task_id": str(reference.get("task_id") or "").strip(),
            "oa_url": task.oa_url,
            "oa_action": str(reference.get("action") or "").strip(),
            "oa_remark": str(reference.get("remark") or "").strip(),
            "oa_action_result_json": json.dumps(
                action_result,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }

    def _build_agent_task_context(
        self,
        *,
        conversation: DingTalkConversation,
        task: ReplyTask,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> AgentTaskContext:
        messages = [
            AgentContextMessage(
                message_id=message.open_message_id,
                sender=message.sender_name,
                text=message.content,
                create_time=message.create_time,
            )
            for message in context_messages
        ]
        if trigger.quoted_content:
            messages.insert(
                0,
                AgentContextMessage(
                    message_id=trigger.quoted_message_id or trigger.open_message_id,
                    sender="quoted_message",
                    text=trigger.quoted_content,
                    create_time=trigger.create_time,
                ),
            )
        materials = self._agent_material_references(
            task=task,
            trigger=trigger,
            context_messages=context_messages,
        )
        image_paths = self._collect_agent_image_paths(
            task_id=task.id,
            trigger=trigger,
            context_messages=context_messages,
        )
        manual_rerun = None
        if task.manual_rerun_attempt_id:
            source_attempt = self.store.get_reply_attempt(task.manual_rerun_attempt_id)
            if source_attempt is not None:
                manual_rerun = ManualRerunInstruction(
                    source_attempt_id=source_attempt.id,
                    reviewer_feedback=source_attempt.reviewer_feedback.strip(),
                    suggested_reply_text=(
                        source_attempt.corrected_reply_text.strip()
                        or source_attempt.draft_reply_text.strip()
                        or source_attempt.final_reply_text.strip()
                    ),
                )
        return AgentTaskContext(
            task_id=task.id,
            channel=task.channel,
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            single_chat=task.single_chat,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            trigger_create_time=task.trigger_create_time,
            trigger_sender_user_id=trigger.sender_user_id or "",
            trigger_sender_open_dingtalk_id=trigger.sender_open_dingtalk_id or "",
            trigger_mentioned_user_ids=tuple(trigger.mentioned_user_ids),
            messages=tuple(messages),
            materials=materials,
            prior_receipts=self._agent_prior_receipts(task),
            manual_rerun=manual_rerun,
            trigger_raw_payload=dict(trigger.raw_payload),
            image_paths=tuple(str(path.resolve()) for path in image_paths),
            image_sha256s=tuple(
                hashlib.sha256(path.read_bytes()).hexdigest() for path in image_paths
            ),
        )

    def _agent_material_references(
        self,
        *,
        task: ReplyTask,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> tuple[MaterialReference, ...]:
        references: list[MaterialReference] = []
        seen: set[tuple[str, str]] = set()

        def add(
            kind: str,
            reference: str,
            source_message_id: str,
            read_commands: tuple[str, ...],
        ) -> None:
            if not reference or (kind, reference) in seen:
                return
            seen.add((kind, reference))
            references.append(
                MaterialReference(
                    kind=kind,
                    reference=reference,
                    source_message_id=source_message_id,
                    read_commands=tuple(
                        command for command in read_commands if command
                    ),
                )
            )

        for reference in self._material_references([trigger], context_messages):
            read_commands = (
                (reference.read_command,)
                if reference.read_command
                else self._default_material_read_commands(
                    reference.kind,
                    reference.reference,
                )
            )
            add(
                reference.kind,
                reference.reference,
                reference.source_message_id,
                read_commands,
            )

        for message in self._referenced_document_messages(
            [trigger],
            context_messages,
        ):
            for _source_key, payload in self._message_image_sources(message):
                kind = payload.get("kind", "")
                if kind == "media_id":
                    media_id = payload.get("media_id", "")
                    image_reference = json.dumps(
                        {
                            "conversation_id": message.open_conversation_id,
                            "media_id": media_id,
                            "message_id": message.open_message_id,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    commands = (
                        "dws chat message download-media --type mediaId"
                        " --resource-id "
                        + shlex.quote(media_id)
                        + " --message-id "
                        + shlex.quote(message.open_message_id)
                        + " --open-conversation-id "
                        + shlex.quote(message.open_conversation_id)
                        + " --output <local-path> --format json --yes",
                    )
                elif kind == "download_code":
                    download_code = payload.get("download_code", "")
                    image_reference = json.dumps(
                        {
                            "download_code": download_code,
                            "message_id": message.open_message_id,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    commands = (
                        "dws api POST /v1.0/robot/messageFiles/download"
                        ' --data "$(jq -cn --arg downloadCode '
                        + shlex.quote(download_code)
                        + ' --arg robotCode "$DINGTALK_DING_ROBOT_CODE"'
                        " '{downloadCode:$downloadCode,robotCode:$robotCode}')\""
                        " --format json",
                    )
                else:
                    continue
                add(
                    "dingtalk_image",
                    image_reference,
                    message.open_message_id,
                    commands,
                )

        calendar_event_id = self._raw_calendar_event_id(trigger)
        if calendar_event_id:
            add(
                "dingtalk_calendar",
                json.dumps(
                    {
                        "event_id": calendar_event_id,
                        "source": trigger.content,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                trigger.open_message_id,
                (
                    "dws calendar event get --id "
                    + shlex.quote(calendar_event_id)
                    + " --format json",
                ),
            )
        elif self._is_calendar_message(trigger):
            start, end = self._calendar_pending_invite_search_window(trigger)
            add(
                "dingtalk_calendar",
                trigger.content,
                trigger.open_message_id,
                (
                    "dws calendar event list --start "
                    + shlex.quote(start)
                    + " --end "
                    + shlex.quote(end)
                    + " --format json",
                ),
            )

        for message in (trigger,):
            task_oa_url = (
                task.oa_url.strip()
                if message.open_message_id == trigger.open_message_id
                else ""
            )
            message_oa_url = extract_oa_url(message.content)
            quoted_oa_url = extract_oa_url(message.quoted_content or "")
            if task_oa_url:
                oa_url = task_oa_url
                oa_source_message_id = message.open_message_id
                oa_from_quote = False
            elif message_oa_url:
                oa_url = message_oa_url
                oa_source_message_id = message.open_message_id
                oa_from_quote = False
            elif quoted_oa_url:
                oa_url = quoted_oa_url
                oa_source_message_id = (
                    message.quoted_message_id or message.open_message_id
                )
                oa_from_quote = True
            else:
                oa_url = ""
                oa_source_message_id = message.open_message_id
                oa_from_quote = False

            process_instance_id = self._oa_process_instance_id_from_url(oa_url)
            task_id = self._oa_task_id_from_url(oa_url)
            raw_process_id, raw_task_id = self._raw_oa_identifiers(message.raw_payload)
            if not oa_from_quote:
                process_instance_id = process_instance_id or raw_process_id
                task_id = task_id or raw_task_id
            if oa_from_quote and not (process_instance_id and task_id):
                continue
            if process_instance_id:
                detail_command = (
                    ".venv/bin/python -m app.cli "
                    "read-oa-approval-detail --instance-id "
                    + shlex.quote(process_instance_id)
                )
                tasks_command = (
                    "dws oa approval tasks --instance-id "
                    + shlex.quote(process_instance_id)
                    + " --format json"
                )
                reference = json.dumps(
                    {
                        "process_instance_id": process_instance_id,
                        "task_id": task_id,
                        "url": oa_url,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                add(
                    "dingtalk_oa",
                    reference,
                    oa_source_message_id,
                    (detail_command, tasks_command),
                )
            elif oa_url or self._is_oa_approval_message(message):
                reference = json.dumps(
                    {
                        "process_instance_id": process_instance_id,
                        "task_id": task_id,
                        "url": oa_url,
                        "original_reference": message.content,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                add(
                    "dingtalk_oa",
                    reference,
                    oa_source_message_id,
                    (),
                )
        return tuple(references)

    def _collect_agent_image_paths(
        self,
        *,
        task_id: int,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> list[Path]:
        image_paths: list[Path] = []
        seen_sources: set[str] = set()
        for message in self._referenced_document_messages(
            [trigger],
            context_messages,
        ):
            for source_key, payload in self._message_image_sources(message):
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                try:
                    path = self._resolve_message_image(task_id, message, payload)
                except Exception as exc:
                    self.store.record_error(
                        message.open_conversation_id,
                        message.open_message_id,
                        "image_download",
                        self._image_download_error_detail(message, payload, str(exc)),
                    )
                    continue
                if path is None:
                    self.store.record_error(
                        message.open_conversation_id,
                        message.open_message_id,
                        "image_download",
                        self._image_download_error_detail(
                            message,
                            payload,
                            "trusted local image path unavailable",
                        ),
                    )
                    continue
                image_paths.append(path)
        return image_paths

    @staticmethod
    def _image_download_error_detail(
        message: DingTalkMessage,
        payload: dict[str, str],
        error: str,
    ) -> str:
        source = (
            payload.get("media_id")
            or payload.get("download_code")
            or payload.get("url")
        )
        source_text = f" resource {source}" if source else ""
        return f"{message.open_message_id}:{source_text} error {error}"

    def _resolve_message_image(
        self,
        task_id: int,
        message: DingTalkMessage,
        payload: dict[str, str],
    ) -> Path | None:
        kind = payload.get("kind")
        if kind == "url":
            return None
        elif kind == "media_id":
            download = self.dws.get_resource_download_url(
                message.open_conversation_id,
                message.open_message_id,
                payload["media_id"],
                "mediaId",
            )
        elif kind == "download_code":
            download = self.dws.download_robot_message_file(payload["download_code"])
        else:
            return None
        local_path = self._local_image_path_from_payload(download)
        if local_path is None:
            return None
        data = local_path.read_bytes()
        if len(data) > DOWNLOADED_IMAGE_MAX_BYTES:
            raise DwsError("dingtalk_image_too_large")
        suffix = self._decoded_image_suffix(data)
        return self._write_message_image(task_id, message, suffix, data)

    @staticmethod
    def _local_image_path_from_payload(payload: object) -> Path | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get("localPath")
        if not isinstance(value, str) or not value.strip():
            return None
        path = Path(value)
        if not path.is_file():
            return None
        if path.stat().st_size > DOWNLOADED_IMAGE_MAX_BYTES:
            raise DwsError("dingtalk_image_too_large")
        return path

    @staticmethod
    def _decoded_image_suffix(data: bytes) -> str:
        suffixes = {
            "GIF": ".gif",
            "JPEG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp",
        }
        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = str(image.format or "").upper()
                if image_format not in suffixes:
                    raise DwsError("dingtalk_image_format_unsupported")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise DwsError("dingtalk_image_invalid") from exc
        return suffixes[image_format]

    def _write_message_image(
        self,
        task_id: int,
        message: DingTalkMessage,
        suffix: str,
        data: bytes,
    ) -> Path:
        image_root = self.store.path.parent / "image-attachments"
        image_root.mkdir(mode=0o700, exist_ok=True)
        image_root.chmod(0o700)
        image_dir = image_root / f"task-{task_id}"
        image_dir.mkdir(mode=0o700, exist_ok=True)
        image_dir.chmod(0o700)
        safe_message_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", message.open_message_id)
        digest = hashlib.sha256(data).hexdigest()[:16]
        path = image_dir / f"{safe_message_id}_{digest}{suffix}"
        self._task_image_paths.setdefault(task_id, set()).add(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        with os.fdopen(os.open(path, flags, 0o600), "wb") as image_file:
            image_file.write(data)
        path.chmod(0o600)
        return path

    def _cleanup_task_image_paths(self, task_id: int) -> None:
        paths = self._task_image_paths.pop(task_id, set())
        directories = {path.parent for path in paths}
        for path in paths:
            path.unlink(missing_ok=True)
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                continue

    @staticmethod
    def _raw_calendar_event_id(message: DingTalkMessage) -> str:
        stack: list[object] = [message.raw_payload]
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not isinstance(value, dict):
                continue
            for key, item in value.items():
                normalized = str(key).replace("_", "").casefold()
                if normalized in {"eventid", "uniqueid"} and isinstance(item, str):
                    if item.strip():
                        return item.strip()
                if isinstance(item, dict | list):
                    stack.append(item)
        decoded = unquote(message.content)
        match = re.search(r"(?:^|[?&])uniqueId=([^&\s]+)", decoded)
        return unquote(match.group(1)).strip() if match else ""

    @classmethod
    def _raw_oa_identifiers(cls, payload: object) -> tuple[str, str]:
        process_instance_id = ""
        task_id = ""
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not isinstance(value, dict):
                continue
            for key, item in value.items():
                normalized = str(key).replace("_", "").casefold()
                if not process_instance_id and normalized in {
                    "processinstanceid",
                    "procinstid",
                }:
                    process_instance_id = str(item or "").strip()
                elif not task_id and normalized == "taskid":
                    task_id = str(item or "").strip()
                elif isinstance(item, dict | list):
                    stack.append(item)
        return process_instance_id, task_id

    def _agent_prior_receipts(self, task: ReplyTask) -> tuple[PriorReceipt, ...]:
        process_instance_id = self._oa_process_instance_id_from_url(task.oa_url)
        if process_instance_id:
            receipts = []
            for attempt in self.store.list_oa_attempt_history(
                process_instance_id,
                limit=10,
            ):
                if not attempt.oa_action.strip():
                    continue
                if attempt.send_status not in {"commented", "completed"}:
                    continue
                summary = (attempt.oa_remark or attempt.audit_summary).strip()
                if not summary:
                    continue
                receipts.append(
                    PriorReceipt(
                        receipt_id=f"reply-attempt-{attempt.id}",
                        operation=attempt.oa_action,
                        summary=summary,
                        completed=True,
                    )
                )
            return tuple(receipts)
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            task.conversation_id,
            task.trigger_message_id,
        )
        if attempt is None:
            return ()
        if attempt.send_status == "skipped" or attempt.action in {
            "no_action",
            "no_reply",
        }:
            return ()
        summary = (attempt.audit_summary or attempt.codex_reason).strip()
        if not summary:
            return ()
        return (
            PriorReceipt(
                receipt_id=f"reply-attempt-{attempt.id}",
                operation=attempt.action,
                summary=summary,
                completed=attempt.send_status
                in {
                    "calendar",
                    "commented",
                    "completed",
                    "document",
                    "reacted",
                    "sent",
                },
            ),
        )

    def _queued_task_prompt_context_messages(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        *,
        strict: bool = False,
    ) -> tuple[list[DingTalkMessage], list[DingTalkMessage]]:
        if self._is_service_task_trigger(trigger):
            return [trigger], [trigger]
        context_messages: list[DingTalkMessage] = []
        unread_messages: list[DingTalkMessage] = []
        context_messages = self._read_conversation_messages(
            "read_recent_messages_fallback",
            conversation,
            lambda: self.dws.read_recent_messages(conversation),
            message_id=trigger.open_message_id,
            raise_authorization=True,
            raise_errors=strict,
            default=[],
        )
        unread_messages = self._read_conversation_messages(
            "read_unread_messages_fallback",
            conversation,
            lambda: self.dws.read_unread_messages(conversation),
            message_id=trigger.open_message_id,
            raise_authorization=True,
            raise_errors=strict,
            default=[],
        )
        return context_messages, self._prompt_context_messages(
            context_messages,
            unread_messages,
        )

    @staticmethod
    def _is_service_task_trigger(trigger: DingTalkMessage) -> bool:
        return bool(trigger.raw_payload.get("service_task")) or (
            str(trigger.raw_payload.get("source") or "") == "oa_pending_scan"
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
                channel="dingtalk",
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
            channel="dingtalk",
        )
        if inserted:
            return True
        updated = self.store.update_pending_reply_task_trigger_for_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
            channel="dingtalk",
        )
        return updated > 0

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
            DingTalkAutoReplyWorker._latest_trigger_message(group) for group in groups
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
            DingTalkAutoReplyWorker._latest_trigger_message(group) for group in groups
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
        _, latest = max(
            enumerate(messages),
            key=lambda item: (
                DingTalkAutoReplyWorker._message_create_time_as_instant(item[1]),
                item[0],
            ),
        )
        if len(messages) == 1:
            return latest
        ordered_messages = sorted(
            messages,
            key=DingTalkAutoReplyWorker._message_create_time_as_instant,
        )
        raw_payload = dict(latest.raw_payload)
        raw_payload["coalesced_message_ids"] = [
            message.open_message_id for message in ordered_messages
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

    def _agent_named_messages_by_conversation(self) -> dict[str, list[DingTalkMessage]]:
        aliases = agent_mention_aliases()
        if not aliases:
            return {}
        messages = self._call_dws(
            "read_agent_name_mentions",
            lambda: self.dws.read_broadcast_messages(
                aliases,
                limit=100,
                lookback_hours=24,
            ),
            notify_title="CEO read agent name mentions failed",
            default=[],
        )
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
            if self._is_current_user_message_for_candidate_filter(message):
                continue
            grouped.setdefault(message.open_conversation_id, []).append(message)
        return grouped

    def _robot_direct_messages_by_conversation(
        self,
    ) -> dict[str, list[DingTalkMessage]]:
        read_robot_direct_messages = getattr(
            self.dws,
            "read_robot_direct_messages",
            None,
        )
        if read_robot_direct_messages is None:
            return {}
        messages = self._call_dws(
            "read_robot_direct_messages",
            lambda: read_robot_direct_messages(
                lookback_minutes=max(
                    1,
                    int(ROBOT_DIRECT_MESSAGE_LOOKBACK.total_seconds() // 60),
                ),
                limit=100,
            ),
            notify_title="CEO read robot direct messages failed",
            default=[],
        )
        grouped: dict[str, list[DingTalkMessage]] = {}
        for message in messages:
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

    @staticmethod
    def _prioritize_conversations_with_messages(
        conversations: list[DingTalkConversation],
        priority_messages: dict[str, list[DingTalkMessage]],
    ) -> list[DingTalkConversation]:
        priority_conversation_ids = {
            conversation_id
            for conversation_id, messages in priority_messages.items()
            if messages
        }
        if not priority_conversation_ids:
            return conversations
        prioritized = [
            conversation
            for conversation in conversations
            if conversation.open_conversation_id in priority_conversation_ids
        ]
        remaining = [
            conversation
            for conversation in conversations
            if conversation.open_conversation_id not in priority_conversation_ids
        ]
        return [*prioritized, *remaining]

    def rerun_message(
        self,
        conversation: DingTalkConversation,
        message_id: str,
        *,
        force_new_decision: bool = False,
        oa_url: str = "",
    ) -> str:
        trigger = self._persisted_service_task_trigger(conversation, message_id)
        if trigger is None:
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
            trigger = (
                candidates[-1]
                if candidates
                else self._lookup_rerun_message_by_id(
                    conversation,
                    message_id,
                )
            )
        if trigger is None:
            raise ValueError(
                f"message not found in recent DingTalk context: {message_id}"
            )
        if trigger.is_recalled():
            self._record_trigger_recalled_after_backoff_skip(conversation, trigger)
            self._mark_seen([trigger])
            return trigger.open_message_id
        trigger = self._restore_richer_rerun_trigger(trigger)
        if force_new_decision:
            task = self.store.enqueue_manual_rerun_reply_task(
                conversation_id=conversation.open_conversation_id,
                conversation_title=conversation.title,
                single_chat=conversation.single_chat,
                trigger_message_id=trigger.open_message_id,
                trigger_create_time=trigger.create_time,
                trigger_sender=trigger.sender_name,
                trigger_text=trigger.content,
                trigger_message_json=trigger.model_dump_json(),
                oa_url=oa_url,
                force_rotation=True,
            )
        else:
            self.store.enqueue_reply_task(
                conversation_id=conversation.open_conversation_id,
                conversation_title=conversation.title,
                single_chat=conversation.single_chat,
                trigger_message_id=trigger.open_message_id,
                trigger_create_time=trigger.create_time,
                trigger_sender=trigger.sender_name,
                trigger_text=trigger.content,
                trigger_message_json=trigger.model_dump_json(),
                oa_url=oa_url,
            )
            task = self.store.get_reply_task_for_message(
                conversation.open_conversation_id,
                trigger.open_message_id,
            )
            if task is None:
                raise RuntimeError("rerun reply task was not persisted")
        self._process_queued_task(conversation, task)
        return trigger.open_message_id

    def _persisted_service_task_trigger(
        self,
        conversation: DingTalkConversation,
        message_id: str,
    ) -> DingTalkMessage | None:
        task = self.store.get_reply_task_for_message(
            conversation.open_conversation_id,
            message_id,
        )
        if task is None:
            return None
        try:
            trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
        except (ValueError, TypeError):
            return None
        if (
            trigger.open_message_id != message_id
            or trigger.open_conversation_id != conversation.open_conversation_id
            or not self._is_service_task_trigger(trigger)
        ):
            return None
        return trigger.model_copy(
            update={
                "conversation_title": conversation.title or trigger.conversation_title,
                "single_chat": conversation.single_chat,
            }
        )

    def _restore_richer_rerun_trigger(
        self,
        trigger: DingTalkMessage,
    ) -> DingTalkMessage:
        if trigger.content.strip() != "[互动卡片]":
            return trigger

        persisted_task = self.store.get_reply_task_for_message(
            trigger.open_conversation_id,
            trigger.open_message_id,
        )
        if persisted_task is not None:
            try:
                persisted_message = DingTalkMessage.model_validate_json(
                    persisted_task.trigger_message_json
                )
            except (ValueError, TypeError):
                persisted_message = None
            if (
                persisted_message is not None
                and persisted_message.open_message_id == trigger.open_message_id
                and persisted_message.content.strip() != "[互动卡片]"
            ):
                return persisted_message

        attempt = self.store.get_latest_reply_attempt_for_trigger(
            trigger.open_conversation_id,
            trigger.open_message_id,
        )
        if attempt is None or attempt.trigger_text.strip() in {"", "[互动卡片]"}:
            return trigger
        raw_payload = dict(trigger.raw_payload)
        raw_payload["content"] = attempt.trigger_text
        return trigger.model_copy(
            update={
                "content": attempt.trigger_text,
                "raw_payload": raw_payload,
            }
        )

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

    @staticmethod
    def _is_oa_approval_message(message: DingTalkMessage) -> bool:
        content = message.content.strip()
        return bool(
            DINGTALK_APPROVAL_LINK_PATTERN.search(content)
            or DINGTALK_APPROVAL_REMINDER_PATTERN.search(content)
        )

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
        pending_candidates = [
            event for event in candidates if self._calendar_event_is_self_pending(event)
        ]
        if include_resolved:
            matched = self._calendar_pending_invite_from_sender_candidates(
                message,
                pending_candidates,
                context_messages=context_messages,
            )
            if matched is not None:
                return matched
        return self._calendar_pending_invite_from_sender_candidates(
            message,
            candidates,
            context_messages=context_messages,
        )

    def _calendar_pending_invite_from_sender_candidates(
        self,
        message: DingTalkMessage,
        candidates: list[DwsCalendarEvent],
        *,
        context_messages: list[DingTalkMessage] | None = None,
    ) -> DwsCalendarEvent | None:
        sender_name = message.sender_name.strip()
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
                or (include_resolved and self._calendar_event_has_self_response(event))
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
        upcoming_candidate = self._closest_upcoming_calendar_event_without_change_time(
            candidates,
            message,
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
        return sum(
            left[keyword] * right[keyword] for keyword in left if keyword in right
        )

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
            DingTalkAutoReplyWorker._message_create_time_as_instant(message).timestamp()
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

    @staticmethod
    def _is_calendar_message(message: DingTalkMessage) -> bool:
        message_type = (message.message_type or "").strip().lower()
        content = message.content.strip()
        decoded_content = unquote(content)
        return (
            message_type
            in {
                "calendar",
                "schedule",
            }
            or content.startswith("[日程]")
            or any(
                marker in decoded_content
                for marker in (
                    "newCalendar=1",
                    "calendarDetail",
                    "uniqueId=",
                )
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
        return (
            content.startswith(RENDERED_NON_TEXT_PREFIXES)
            or RENDERED_NON_TEXT_PREFIX_PATTERN.match(content) is not None
        )

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
            ignore_current_user_cutoff = True
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
                message for message in messages if message.addresses_principal()
            ]
            ignore_current_user_cutoff = False
        candidates = [
            message
            for message in eligible_messages
            if not self._is_current_user_message_for_candidate_filter(message)
            and (
                ignore_current_user_cutoff
                or latest_current_user_message_time is None
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
                [*unread_messages, *(mentioned_messages or [])],
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

    def _discard_service_handoff_notifications(
        self,
        messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        service_notifications: list[DingTalkMessage] = []
        remaining_messages: list[DingTalkMessage] = []
        for message in messages:
            if self._is_service_handoff_notification(message):
                service_notifications.append(message)
            else:
                remaining_messages.append(message)
        if service_notifications:
            self._mark_seen(service_notifications)
        return remaining_messages

    @staticmethod
    def _is_service_handoff_notification(message: DingTalkMessage) -> bool:
        return message.content.startswith(HANDOFF_NOTIFICATION_PREFIX)

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
            self.store.has_seen(message.open_message_id) for message in context_messages
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
        if self._is_robot_direct_trigger(message):
            return False
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

    def _material_references(
        self,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[MaterialReferenceContext]:
        references: list[MaterialReferenceContext] = []
        seen: set[tuple[str, str]] = set()

        def add(
            kind: str,
            reference: str,
            message: DingTalkMessage,
            *,
            read_command: str = "",
        ) -> None:
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
                    read_command=read_command,
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
                for match in LARK_DOC_URL_PATTERN.finditer(text):
                    add(
                        "lark_doc",
                        self._canonical_doc_url(match.group(0)),
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
                    add(
                        "dingtalk_file",
                        file_name,
                        source,
                        read_command=self._referenced_file_read_command(source),
                    )
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
            file_name = cls._file_name_from_message_text(text)
            if not file_name:
                return
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
        for download_code in self._download_codes_from_payload(message.raw_payload):
            sources.append(
                (
                    f"download_code:{message.open_message_id}:{download_code}",
                    {"kind": "download_code", "download_code": download_code},
                )
            )
        return sources

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

    @classmethod
    def _referenced_document_messages(
        cls,
        new_messages: list[DingTalkMessage],
        context_messages: list[DingTalkMessage],
    ) -> list[DingTalkMessage]:
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        context_by_message_id = {
            message.open_message_id: message for message in context_messages
        }

        def add_message(message: DingTalkMessage) -> bool:
            if message.open_message_id in seen_message_ids:
                return False
            result.append(message)
            seen_message_ids.add(message.open_message_id)
            return cls._message_has_material_reference(message)

        direct_material_found = False
        for message in new_messages:
            direct_material_found = add_message(message) or direct_material_found
            for coalesced_message in cls._coalesced_messages(message):
                direct_material_found = (
                    add_message(coalesced_message) or direct_material_found
                )
            if (
                message.quoted_message_id
                and message.quoted_message_id in context_by_message_id
                and message.quoted_message_id not in seen_message_ids
            ):
                quoted = context_by_message_id[message.quoted_message_id]
                direct_material_found = add_message(quoted) or direct_material_found
        if direct_material_found or not new_messages:
            return result

        trigger = new_messages[-1]
        try:
            trigger_time = datetime.strptime(trigger.create_time, DINGTALK_TIME_FORMAT)
        except ValueError:
            return result
        window_start = trigger_time - REFERENCED_FILE_CONTEXT_WINDOW
        for message in context_messages:
            if message.sender_name != trigger.sender_name:
                continue
            try:
                message_time = datetime.strptime(
                    message.create_time,
                    DINGTALK_TIME_FORMAT,
                )
            except ValueError:
                continue
            if not window_start <= message_time <= trigger_time:
                continue
            if cls._message_has_material_reference(message):
                add_message(message)
        return result

    @classmethod
    def _message_has_material_reference(cls, message: DingTalkMessage) -> bool:
        for text in (message.content, message.quoted_content or ""):
            if (
                DINGTALK_DOC_URL_PATTERN.search(text)
                or DINGTALK_SHANJI_DOC_SELECTOR_PATTERN.search(text)
                or DINGTALK_MINUTES_LINK_PATTERN.search(text)
                or LARK_DOC_URL_PATTERN.search(text)
                or cls._file_name_from_message_text(text)
            ):
                return True
        return False

    @staticmethod
    def _coalesced_messages(message: DingTalkMessage) -> list[DingTalkMessage]:
        raw_messages = message.raw_payload.get("coalesced_messages")
        if not isinstance(raw_messages, list):
            return []
        messages: list[DingTalkMessage] = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                continue
            content = str(raw_message.get("content") or "")
            message_id = str(raw_message.get("open_message_id") or "").strip()
            if (
                not content.strip()
                or not message_id
                or message_id == message.open_message_id
            ):
                continue
            messages.append(
                DingTalkMessage(
                    open_conversation_id=message.open_conversation_id,
                    open_message_id=message_id,
                    conversation_title=message.conversation_title,
                    single_chat=message.single_chat,
                    sender_name=str(
                        raw_message.get("sender_name") or message.sender_name
                    ),
                    sender_open_dingtalk_id=message.sender_open_dingtalk_id,
                    sender_user_id=message.sender_user_id,
                    message_type=message.message_type,
                    create_time=str(
                        raw_message.get("create_time") or message.create_time
                    ),
                    content=content,
                    raw_payload=raw_message,
                )
            )
        return messages

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
            file_name = cls._file_name_from_message_text(text)
            if not file_name:
                return
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

    @staticmethod
    def _file_name_from_message_text(text: str) -> str:
        stripped = text.strip()
        match = FILE_MESSAGE_PATTERN.match(stripped)
        if not match:
            return ""
        name = match.group("name").strip()
        file_id_match = DINGTALK_FILE_ID_PATTERN.search(name)
        if file_id_match:
            name = name[: file_id_match.start()].strip()
        return name

    @staticmethod
    def _file_id_from_message_text(text: str) -> str:
        match = DINGTALK_FILE_ID_PATTERN.search(text)
        return match.group("file_id").strip() if match else ""

    @classmethod
    def _referenced_file_read_command(cls, message: DingTalkMessage) -> str:
        file_id = cls._file_id_from_message_text(message.content)
        if not file_id:
            file_id = cls._file_id_from_message_text(message.quoted_content or "")
        if not file_id:
            return ""
        return (
            f"dws drive download --node {shlex.quote(file_id)} "
            "--output <local-path> --format json"
        )

    @staticmethod
    def _minutes_task_uuid_from_selector_url(url: str) -> str:
        cleaned = DingTalkAutoReplyWorker._clean_link_url(url)
        parsed = urlsplit(cleaned)
        query = parse_qs(parsed.query)
        resource_id = query.get("resourceId", [""])[0]
        return resource_id.strip()

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
    def _canonical_doc_url(url: str) -> str:
        parts = urlsplit(url.rstrip(".,;，。；"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _is_robot_direct_trigger(trigger: DingTalkMessage) -> bool:
        return trigger.raw_payload.get("ceo_agent_source") == "robot_direct"

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

    def _notify_problem_attempt(
        self,
        task: ReplyTask,
        *,
        attempt_id: int,
        send_status: str,
        message: str,
    ) -> None:
        if self.dry_run:
            return
        title, notification_message = self._problem_notification_content(
            task,
            send_status=send_status,
            message=message,
        )
        conversation = DingTalkConversation(
            open_conversation_id=task.conversation_id,
            title=task.conversation_title,
            single_chat=task.single_chat,
            unread_point=1,
        )
        delivered = send_browser_notification(
            title=title,
            message=notification_message,
            url=self._notification_url(conversation, attempt_id=attempt_id),
            notification_id=self._problem_notification_id(task),
            detail_url=f"/attempts/{attempt_id}",
        )
        if send_status == "needs_human" and not delivered:
            send_macos_notification(
                title=title,
                message=notification_message,
            )

    @staticmethod
    def _problem_notification_id(task: ReplyTask) -> str:
        identity = "\0".join(
            (task.channel, task.conversation_id, task.trigger_message_id)
        ).encode("utf-8")
        return f"ceo-agent-service-trigger-{hashlib.sha256(identity).hexdigest()[:20]}"

    def _dismiss_problem_notification(self, task: ReplyTask) -> None:
        if not self.dry_run:
            dismiss_browser_notification(self._problem_notification_id(task))

    @staticmethod
    def _problem_notification_content(
        task: ReplyTask,
        *,
        send_status: str,
        message: str,
    ) -> tuple[str, str]:
        subject = DingTalkAutoReplyWorker._notification_excerpt(
            task.trigger_text,
            limit=72,
        ) or DingTalkAutoReplyWorker._notification_excerpt(
            task.conversation_title,
            limit=72,
        )
        detail = DingTalkAutoReplyWorker._notification_excerpt(message, limit=160)
        state = "等待你的选择" if send_status == "needs_human" else send_status
        if send_status == "needs_human":
            detail_lines = (
                "需要你确认：系统不会代为作出这项管理决定。",
                f"事项：{subject or '未提供事项'}",
                f"已核验：{detail or '未提供具体说明'}",
                "操作：打开审计页阅读已核验事实，并提交具体处理指令。",
            )
            return f"CEO 需要确认：{subject or '未命名事项'}", "\n".join(detail_lines)
        next_step = (
            "打开审计页查看原因并继续处理。"
        )
        return (
            f"CEO 待处理：{subject or '未命名事项'}",
            "\n".join(
                (
                    f"事项：{subject or '未提供事项'}",
                    f"状态：{state}",
                    f"原因：{detail or '未提供具体说明'}",
                    f"操作：{next_step}",
                )
            ),
        )

    @staticmethod
    def _notification_excerpt(value: str, *, limit: int) -> str:
        without_links = MEDIA_OR_LINK_PATTERN.sub(" ", value)
        if contains_forbidden_leak(without_links):
            without_links = redact_forbidden_leak_markers(
                without_links,
                replacement="自动处理",
            )
        return safe_excerpt(without_links, limit=limit)

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
        return dingtalk_conversation_notification_url(
            open_conversation_id,
            attempt_id=attempt_id,
        )

    def _mark_seen(self, messages: list[DingTalkMessage]) -> None:
        if self.dry_run:
            return
        for message in messages:
            self.store.mark_seen(message.open_message_id, message.open_conversation_id)
            for message_id in message.raw_payload.get("coalesced_message_ids", []):
                if isinstance(message_id, str) and message_id.strip():
                    self.store.mark_seen(message_id, message.open_conversation_id)
