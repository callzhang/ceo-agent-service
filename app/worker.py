import json
import logging
import re
import shlex
import shutil
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
from app.agent_result import (
    AgentError,
    AgentOutcome,
    AgentResult,
    ExecutionReceipt,
    InconsistentAgentResultError,
    SideEffectState,
    validate_completion_evidence,
)
from app.agent_runner import (
    LEASE_SECONDS,
    AgentReadOnlyViolationError,
    AgentRunNoEffectEvidenceError,
    AgentRunUnavailableError,
    ReconciliationResult,
    ReconciliationDependencyError,
    DirectAgentRunResult,
    DirectAgentRunner,
    structured_execution_evidence,
    unknown_effect_reference,
)
from app.channel_gate import (
    ChannelGate,
    ChannelGateResult,
    ChannelGateState,
    LoginCoordinator,
    default_channel_gates,
    start_lark_auth_login,
)
from app.config import (
    agent_mention_aliases,
    assistant_signature,
    broadcast_mention_aliases,
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
from app.codex_runner import selected_codex_model_provider
from app.notification import (
    dingtalk_conversation_notification_url,
    send_macos_notification,
)
from app.oa_approval import extract_oa_url
from app.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    refresh_org_cache,
)
from app.permission import PermissionGate
from app.prompt import MaterialReferenceContext
from app.store import (
    AgentRunLeaseLostError,
    FAST_PATH_UNREAD_BACKOFF_TASK_ERROR,
    AutoReplyStore,
    ReplyTask,
)
logger = logging.getLogger(__name__)

HANDOFF_ACK = handoff_ack()
HANDOFF_TEXT_EMOTION = "我去叫"
HANDOFF_NOTIFICATION_PREFIX = "【CEO Agent 转人工通知】"
# Historical auto-ack marker. Keep filtering it from context, but do not send
# new processing acknowledgements before final replies.
PROCESSING_ACK = "收到，我正在处理（by 分身）"
CODEX_LOGIN_REQUIRED_PREFIX = "codex_login_required"
CODEX_PROVIDER_AUTH_FAILED_PREFIX = "codex_provider_auth_failed"
CODEX_PROVIDER_UNAVAILABLE_PREFIX = "codex_provider_unavailable"
CRITICAL_INFO_UNAVAILABLE_PREFIX = "critical_info_unavailable:"
XIAOQING_CRITICAL_INFO_UNAVAILABLE_MARKER = (
    f"{CRITICAL_INFO_UNAVAILABLE_PREFIX}xiaoqing_interview"
)
BLOCKED_UNRECOVERABLE_EXTERNAL_AUTH_PREFIX = "blocked_unrecoverable_external_auth"
DEFAULT_TEXT_EMOTION_BACKGROUND_ID = "im_bg_5"
SPLIT_PERSON_SIGNATURE = assistant_signature()
STALE_PROCESSING_TASK_SECONDS = 30 * 60
MAX_REPLY_TASK_ATTEMPTS = 3
REPLY_TASK_RETRY_BASE_DELAY_SECONDS = 60
REPLY_TASK_RETRY_MAX_DELAY_SECONDS = 15 * 60
PERSISTENT_AGENT_RUNTIME_DEPENDENCY_ERRORS = frozenset(
    {
        "codex_process_failed",
        "codex_process_timeout",
        "codex_stream_invalid",
    }
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
        and (
            "session has ended" in normalized
            or "invalid refresh token" in normalized
        )
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


def _blocked_unrecoverable_external_auth_error(reason: str) -> str:
    return f"{BLOCKED_UNRECOVERABLE_EXTERNAL_AUTH_PREFIX}: {reason}"


def _normalize_codex_stop_error_reason(reason: str) -> str:
    if _is_codex_authorization_wait_reason(reason):
        return reason
    if _is_codex_provider_transport_error(reason):
        return _codex_provider_transport_error(reason)
    if _is_codex_provider_auth_error(reason):
        return _codex_provider_auth_error(reason)
    if _is_codex_login_required_error(reason):
        return f"{CODEX_LOGIN_REQUIRED_PREFIX}: {reason}"
    return reason


def _is_codex_authorization_wait_reason(reason: str) -> bool:
    return reason.startswith(
        (
            CODEX_PROVIDER_AUTH_FAILED_PREFIX,
            CODEX_PROVIDER_UNAVAILABLE_PREFIX,
            CODEX_LOGIN_REQUIRED_PREFIX,
        )
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
IMAGE_MESSAGE_MEDIA_ID_PATTERN = re.compile(r"\[图片消息]\(mediaId=(?P<media_id>[^)]+)\)")
MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*]\((?P<url>https?://[^)]+)\)")
DINGTALK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
GROUP_CONTEXT_RECOVERY_WINDOW = timedelta(hours=24)
RECENT_REPLY_WINDOW = timedelta(hours=24)
RECENT_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=7)
REFERENCED_FILE_CONTEXT_WINDOW = timedelta(minutes=10)
DOWNLOADED_FILE_MAX_BYTES = 50 * 1024 * 1024
DOWNLOADED_IMAGE_MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
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


class CodexAuthorizationRequiredError(ReplyTaskProcessingError):
    """Raised when Codex login or selected provider credentials must be restored."""

    needs_authorization = True


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
        direct_agent_runner: DirectAgentRunner | None = None,
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
                "dingtalk": lambda: getattr(self.dws, "dws", self.dws).start_auth_login(),
                "lark": start_lark_auth_login,
            },
            now=lambda: self._now().astimezone(timezone.utc),
        )
        self._pass_channel_results: dict[str, ChannelGateResult] = {}
        self.direct_agent_runner = direct_agent_runner

    def _direct_agent_runner(self) -> DirectAgentRunner:
        if self.direct_agent_runner is not None:
            return self.direct_agent_runner
        runner = getattr(self.codex, "runner", None)
        workspace = getattr(runner, "workspace", None)
        if workspace is None:
            raise RuntimeError("native Codex runner workspace is unavailable")
        self.direct_agent_runner = DirectAgentRunner(
            store=self.store,
            workspace=Path(workspace),
            codex_bin=str(getattr(runner, "codex_bin", "codex")),
        )
        return self.direct_agent_runner

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
            return result
        except Exception as exc:
            if raise_authorization and self._is_authorization_error(exc):
                raise
            pat_authorization_requested = False
            is_login_error = self._is_dws_login_error(exc)
            if is_login_error:
                if self._ensure_dws_auth_login(exc):
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
            elif self._is_missing_direct_chat_recent_context(kind, exc):
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
            and DwsClient.is_message_read_retryable_error_code(exc.code)
        )

    @staticmethod
    def _is_missing_direct_chat_recent_context(kind: str, exc: Exception) -> bool:
        return (
            kind == "read_recent_messages"
            and isinstance(exc, DwsError)
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

    @staticmethod
    def required_channels_for_task(task: ReplyTask) -> set[str]:
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
    def _calendar_event_has_attendee(event: DwsCalendarEvent, attendee_name: str) -> bool:
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
        self.reconcile_unknown_agent_runs(limit=limit)
        self._recover_stale_agent_reply_tasks()
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
            try:
                completed = self._process_queued_task(conversation, task)
            except AgentRunLeaseLostError:
                continue
            except Exception as exc:
                error = str(exc)
                authorization_wait_error = _normalize_codex_stop_error_reason(error)
                if self._is_authorization_error(
                    exc
                ) or _is_codex_authorization_wait_reason(authorization_wait_error):
                    provider_recovery = authorization_wait_error.startswith(
                        CODEX_PROVIDER_UNAVAILABLE_PREFIX
                    )
                    notify_authorization_wait = (
                        task.error.strip() != authorization_wait_error
                    )
                    if self._dws_authorization_required_scopes(exc):
                        self._ensure_dws_pat_authorization(exc)
                    try:
                        self.store.defer_reply_task_for_authorization(
                            task.id,
                            authorization_wait_error,
                            expected_execution_generation=task.execution_generation,
                            available_at=(
                                self._reply_task_retry_available_at(task.attempts)
                                if provider_recovery
                                else self._reply_task_authorization_available_at()
                            ),
                        )
                    except AgentRunLeaseLostError:
                        continue
                    self.store.record_error(
                        task.conversation_id,
                        task.trigger_message_id,
                        (
                            "reply_task_provider_recovery"
                            if provider_recovery
                            else "reply_task_authorization"
                        ),
                        authorization_wait_error,
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
                    task_status = self._record_agent_runtime_failure_attempt(
                        task,
                        error,
                        retryable=True,
                        retry_beyond_limit=(
                            error in PERSISTENT_AGENT_RUNTIME_DEPENDENCY_ERRORS
                        ),
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
                self._notify(
                    title=f"CEO task failed: {task.conversation_title}",
                    message=error[:120],
                    conversation=conversation,
                )
                continue
            if completed:
                processed_tasks += 1
        return processed_tasks

    def _recover_stale_agent_reply_tasks(self) -> None:
        stale_tasks = self.store.list_stale_processing_reply_tasks(
            STALE_PROCESSING_TASK_SECONDS
        )
        if not stale_tasks:
            return
        recovered = 0
        for task in stale_tasks:
            run = self.store.get_agent_run_for_task_generation(
                task.id,
                task.execution_generation,
            )
            if run is None:
                self.store.requeue_reply_task(
                    task.id,
                    "stale_before_agent_start",
                    expected_execution_generation=task.execution_generation,
                )
                recovered += 1
                continue
            if run.status == "unknown":
                continue
            if run.status == "completed" and run.final_result_json:
                payload = json.loads(run.final_result_json)
                if isinstance(payload, dict) and "proof" in payload:
                    ReconciliationResult.model_validate_json(run.final_result_json)
                    self.store.fail_reply_task(
                        task.id,
                        "inconsistent_terminal_reconciliation_state",
                        expected_execution_generation=task.execution_generation,
                    )
                    continue
                result = AgentResult.model_validate(payload)
                self._apply_agent_result(
                    task,
                    DirectAgentRunResult(
                        run_id=run.id,
                        result=result,
                        transcript_start_line=run.transcript_start_line,
                        transcript_end_line=run.transcript_end_line,
                        events=tuple(run.tool_events),
                    ),
                )
                continue
            if run.status == "failed":
                try:
                    structured_error = json.loads(run.structured_error_json or "{}")
                except json.JSONDecodeError:
                    structured_error = {}
                retryable = (
                    isinstance(structured_error, dict)
                    and structured_error.get("retryable") is True
                    and run.side_effect_state == "none"
                )
                error = str(structured_error.get("code") or "agent_run_failed")
                task_status = self._record_agent_runtime_failure_attempt(
                    task,
                    error,
                    retryable=retryable,
                )
                if task_status == "pending":
                    recovered += 1
                continue
            if run.status != "running":
                self.store.fail_reply_task(
                    task.id,
                    f"invalid_agent_run_state:{run.status}",
                    expected_execution_generation=task.execution_generation,
                )
                continue
            if run.side_effect_state == "unknown":
                run = self.store.mark_expired_agent_run_unknown(
                    run.id,
                    {"code": "agent_side_effect_unknown"},
                    expected_execution_generation=task.execution_generation,
                    now=self._sqlite_timestamp(self._now()),
                )
                self._apply_unknown_agent_run(
                    task,
                    run,
                    "agent_side_effect_unknown",
                )
                continue
            if not run.codex_session_id:
                self.store.fail_expired_agent_run(
                    run.id,
                    {"code": "stale_agent_run_missing_session"},
                    expected_execution_generation=task.execution_generation,
                    now=self._sqlite_timestamp(self._now()),
                )
                self._record_agent_runtime_failure_attempt(
                    task,
                    "stale_agent_run_missing_session",
                    retryable=False,
                )
                continue
            self.store.requeue_reply_task(
                task.id,
                "stale_agent_run_resume",
                expected_execution_generation=task.execution_generation,
            )
            recovered += 1
            self.store.record_error(
                task.conversation_id,
                task.trigger_message_id,
                "reply_task_stale",
                (
                    "requeued stale task for same-generation session resume: "
                    f"task={task.id} generation={task.execution_generation}"
                ),
            )
        if recovered:
            self._notify(
                title="CEO task retrying stale tasks",
                message=f"requeued {recovered} stale task(s)",
            )

    def reconcile_unknown_agent_runs(self, *, limit: int = 50) -> int:
        resolved = 0
        now = self._sqlite_timestamp(self._now())
        for run in self.store.list_suspended_unknown_agent_runs(limit=limit):
            try:
                unknown_effect_reference(run.tool_events)
            except AgentRunNoEffectEvidenceError:
                try:
                    self.store.resume_suspended_unknown_agent_run(
                        run.id,
                        expected_execution_generation=run.execution_generation,
                        now=now,
                    )
                except AgentRunLeaseLostError:
                    continue
            except ValueError:
                continue
        unknown_runs = self.store.list_unknown_agent_runs(limit=limit, now=now)
        if not unknown_runs:
            return 0
        runner = self._direct_agent_runner()
        reconcile = getattr(runner, "reconcile", None)
        for run in unknown_runs:
            task = self.store.get_reply_task(run.reply_task_id)
            if task is None:
                continue
            if not self._required_channels_ready(self.required_channels_for_task(task)):
                continue
            if not callable(reconcile):
                claim = self.store.claim_unknown_agent_run(
                    run.id,
                    owner=runner.owner,
                    lease_seconds=LEASE_SECONDS,
                    now=now,
                )
                if claim.claimed:
                    self._defer_agent_reconciliation(
                        run.id,
                        runner.owner,
                        code="reconciliation_tool_unavailable",
                        retryable=False,
                    )
                continue
            context = self._build_agent_reconciliation_context(task)
            try:
                result = reconcile(run, context, now=now)
            except (AgentRunUnavailableError, AgentRunLeaseLostError):
                continue
            except AgentRunNoEffectEvidenceError as exc:
                try:
                    self.store.resolve_unknown_agent_run_absent(
                        run.id,
                        task.id,
                        code=str(exc),
                        owner=runner.owner,
                        now=now,
                    )
                except AgentRunLeaseLostError:
                    continue
                resolved += 1
                continue
            except AgentReadOnlyViolationError:
                self._defer_agent_reconciliation(
                    run.id,
                    runner.owner,
                    code="reconciliation_write_forbidden",
                    retryable=False,
                )
                continue
            except ReconciliationDependencyError as exc:
                service_channel = "dingtalk" if exc.channel == "dws" else "lark"
                if exc.gate_state is ChannelGateState.NEEDS_LOGIN:
                    self._pass_channel_results.pop(service_channel, None)
                    self.login_coordinator.handle(
                        ChannelGateResult(
                            channel=service_channel,
                            state=ChannelGateState.NEEDS_LOGIN,
                            reason_code=exc.code,
                        )
                    )
                self._defer_agent_reconciliation(
                    run.id,
                    runner.owner,
                    code=exc.code,
                    retryable=(
                        exc.gate_state is ChannelGateState.NEEDS_LOGIN
                        or (
                            exc.gate_state is ChannelGateState.UNAVAILABLE
                            and exc.retryable
                        )
                    ),
                    gate_state=exc.gate_state,
                )
                continue
            except Exception as exc:
                code = str(exc).strip() or "reconciliation_tool_unavailable"
                retryable = code in {
                    "codex_process_failed",
                    "codex_process_timeout",
                    "codex_stream_invalid",
                    "reconciliation_tool_unavailable",
                }
                self._defer_agent_reconciliation(
                    run.id,
                    runner.owner,
                    code=code,
                    retryable=retryable,
                )
                continue

            outcome = result.result.outcome
            if outcome is AgentOutcome.COMPLETED:
                try:
                    self.store.resolve_unknown_agent_run_confirmed(
                        run.id,
                        task.id,
                        result.result.model_dump(mode="json"),
                        owner=runner.owner,
                        transcript_end_line=result.transcript_end_line,
                        now=now,
                    )
                except AgentRunLeaseLostError:
                    continue
                resolved += 1
                continue
            if outcome is AgentOutcome.NO_ACTION:
                code = "reconciliation_confirmed_no_effect"
                try:
                    self.store.resolve_unknown_agent_run_absent(
                        run.id,
                        task.id,
                        code=code,
                        owner=runner.owner,
                        transcript_end_line=result.transcript_end_line,
                        now=now,
                    )
                except AgentRunLeaseLostError:
                    continue
                resolved += 1
                continue
            code = result.result.error.code or (
                "reconciliation_needs_human"
                if outcome is AgentOutcome.NEEDS_HUMAN
                else "reconciliation_failed"
            )
            terminalized = self._defer_agent_reconciliation(
                run.id,
                runner.owner,
                code=code,
                retryable=result.result.error.retryable,
                authorization_required=result.result.error.authorization_required,
            )
            if not terminalized:
                self._record_agent_attempt(
                    task,
                    result,
                    send_status=(
                        "blocked" if outcome is AgentOutcome.NEEDS_HUMAN else "failed"
                    ),
                    send_error=code,
                )
        return resolved

    def _defer_agent_reconciliation(
        self,
        run_id: int,
        owner: str,
        *,
        code: str,
        retryable: bool,
        authorization_required: bool = False,
        gate_state: ChannelGateState | None = None,
    ) -> bool:
        run = self.store.get_agent_run(run_id)
        if run is None:
            return False
        structured_error = {
            "code": code,
            "retryable": retryable,
            **(
                {"gate_state": gate_state.value}
                if gate_state is not None
                else {"authorization_required": authorization_required}
            ),
        }
        if not retryable:
            self.store.terminate_unknown_agent_run_unrecoverable(
                run_id,
                owner=owner,
                code=code,
                expected_execution_generation=run.execution_generation,
                structured_error=structured_error,
                now=self._sqlite_timestamp(self._now()),
            )
            return True
        next_attempt = ""
        delay_seconds = min(
            3600,
            60 * (2 ** max(run.reconciliation_attempts - 1, 0)),
        )
        next_attempt = self._sqlite_timestamp(
            self._now() + timedelta(seconds=delay_seconds)
        )
        self.store.defer_unknown_agent_run_reconciliation(
            run_id,
            structured_error,
            owner=owner,
            expected_execution_generation=run.execution_generation,
            next_attempt_at=next_attempt,
            suspended=False,
            now=self._sqlite_timestamp(self._now()),
        )
        return False

    def _build_agent_reconciliation_context(
        self,
        task: ReplyTask,
    ) -> AgentTaskContext:
        try:
            trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)
        except (ValueError, TypeError):
            trigger = DingTalkMessage(
                open_conversation_id=task.conversation_id,
                open_message_id=task.trigger_message_id,
                conversation_title=task.conversation_title,
                single_chat=task.single_chat,
                sender_name=task.trigger_sender,
                create_time=task.trigger_create_time,
                content=task.trigger_text,
                raw_payload={},
            )
        conversation = DingTalkConversation(
            open_conversation_id=task.conversation_id,
            title=task.conversation_title,
            single_chat=task.single_chat,
            unread_point=1,
        )
        return self._build_agent_task_context(
            conversation=conversation,
            task=task,
            trigger=trigger,
            context_messages=[],
        )

    def _record_agent_runtime_failure_attempt(
        self,
        task: ReplyTask,
        error: str,
        *,
        retryable: bool,
        retry_beyond_limit: bool = False,
    ) -> str:
        run = self.store.get_agent_run_for_task_generation(
            task.id,
            task.execution_generation,
        )
        task_status = (
            "pending"
            if retryable
            and (
                retry_beyond_limit
                or task.attempts < self.max_task_attempts
            )
            else "failed"
        )
        available_at = (
            self._reply_task_retry_available_at(task.attempts)
            if task_status == "pending"
            else ""
        )
        if run is not None and run.status == "failed":
            self.store.finalize_agent_reply_task(
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
            return task_status
        self.store.finalize_reply_task_without_run(
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
        return task_status

    def _pending_reply_task_candidates(
        self, *, page_size: int, now: str, max_id: int | None
    ) -> Iterator[ReplyTask]:
        if max_id is None:
            return
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
            yield from page
            after_id = page[-1].id

    def _reply_task_retry_available_at(self, attempts: int) -> str:
        delay_seconds = min(
            REPLY_TASK_RETRY_BASE_DELAY_SECONDS * (2 ** max(attempts - 1, 0)),
            REPLY_TASK_RETRY_MAX_DELAY_SECONDS,
        )
        return self._sqlite_timestamp(
            self._now().astimezone(timezone.utc) + timedelta(seconds=delay_seconds)
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
        existing_run = self.store.get_agent_run_for_task_generation(
            task.id,
            task.execution_generation,
        )
        if existing_run is not None:
            if existing_run.status == "completed":
                result = AgentResult.model_validate_json(existing_run.final_result_json)
                try:
                    return self._apply_agent_result(
                        task,
                        DirectAgentRunResult(
                            run_id=existing_run.id,
                            result=result,
                            transcript_start_line=existing_run.transcript_start_line,
                            transcript_end_line=existing_run.transcript_end_line,
                            events=tuple(existing_run.tool_events),
                        ),
                    )
                except AgentRunLeaseLostError:
                    return False
            if existing_run.status == "unknown":
                self._apply_unknown_agent_run(
                    task,
                    existing_run,
                    "agent_side_effect_unknown",
                )
                return False
            if existing_run.status == "failed":
                try:
                    structured_error = json.loads(
                        existing_run.structured_error_json or "{}"
                    )
                except json.JSONDecodeError:
                    structured_error = {}
                retryable = (
                    isinstance(structured_error, dict)
                    and structured_error.get("retryable") is True
                    and existing_run.side_effect_state == "none"
                )
                error_code = str(
                    structured_error.get("code") or "agent_run_failed"
                )
                retry_beyond_limit = (
                    error_code in PERSISTENT_AGENT_RUNTIME_DEPENDENCY_ERRORS
                )
                if not retryable or (
                    task.attempts > self.max_task_attempts
                    and not retry_beyond_limit
                ):
                    self._record_agent_runtime_failure_attempt(
                        task,
                        error_code,
                        retryable=False,
                    )
                    return False
            if (
                existing_run.status == "running"
                and existing_run.lease_expires_at
                > self._sqlite_timestamp(self._now())
            ):
                self.store.defer_reply_task(
                    task.id,
                    "agent_run_active",
                    expected_execution_generation=task.execution_generation,
                    available_at=existing_run.lease_expires_at,
                )
                return False
        context = self._build_agent_task_context(
            conversation=conversation,
            task=task,
            trigger=trigger,
            context_messages=context_messages,
        )
        try:
            run_result = self._direct_agent_runner().run(
                task,
                context,
                read_only=self.dry_run,
            )
        except Exception as exc:
            run = self.store.get_agent_run_for_task_generation(
                task.id,
                task.execution_generation,
            )
            if run is not None and run.status == "unknown":
                self._apply_unknown_agent_run(task, run, str(exc))
                return False
            raise
        try:
            return self._apply_agent_result(task, run_result)
        except AgentRunLeaseLostError:
            return False

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
        prior_receipts = self._agent_prior_receipts(task)
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
            prior_receipts=prior_receipts,
            manual_rerun=manual_rerun,
            trigger_raw_payload=dict(trigger.raw_payload),
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
                    read_commands=tuple(command for command in read_commands if command),
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
                if kind == "url":
                    image_reference = payload.get("url", "")
                    commands = ()
                elif kind == "media_id":
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
                        " --data \"$(jq -cn --arg downloadCode "
                        + shlex.quote(download_code)
                        + " --arg robotCode \"$DINGTALK_DING_ROBOT_CODE\""
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

        oa_sources = [trigger, *context_messages]
        for message in oa_sources:
            oa_url = (
                task.oa_url.strip()
                if message.open_message_id == trigger.open_message_id
                and task.oa_url.strip()
                else extract_oa_url(message.content)
            )
            process_instance_id = self._oa_process_instance_id_from_url(oa_url)
            task_id = self._oa_task_id_from_url(oa_url)
            raw_process_id, raw_task_id = self._raw_oa_identifiers(
                message.raw_payload
            )
            process_instance_id = process_instance_id or raw_process_id
            task_id = task_id or raw_task_id
            if process_instance_id:
                detail_command = (
                    "dws oa approval detail --instance-id "
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
                    message.open_message_id,
                    (detail_command,),
                )
            elif oa_url or self._is_oa_approval_message(message):
                add(
                    "dingtalk_oa",
                    oa_url or message.open_message_id,
                    message.open_message_id,
                    ("dws oa +list-pending --format json",),
                )
        return tuple(references)

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

    def _apply_agent_result(
        self,
        task: ReplyTask,
        run_result: DirectAgentRunResult,
    ) -> bool:
        persisted_run = self.store.get_agent_run(run_result.run_id)
        if persisted_run is None:
            raise RuntimeError("agent run was not persisted")
        effect_events, embedded_receipts = structured_execution_evidence(
            persisted_run.tool_events
        )
        persisted_receipts = tuple(
            ExecutionReceipt(
                receipt_id=receipt.receipt_id,
                operation_id=receipt.operation_id,
                completed=receipt.completed,
                persisted=receipt.persisted,
                safe_to_confirm=receipt.safe_to_confirm,
            )
            for receipt in self.store.list_agent_execution_receipts(
                run_result.run_id
            )
        )
        receipts = (
            *embedded_receipts,
            *persisted_receipts,
            *run_result.receipts,
        )
        try:
            evidence_state = validate_completion_evidence(
                run_result.result,
                events=effect_events,
                receipts=receipts,
            )
        except InconsistentAgentResultError:
            self._finalize_agent_attempt_and_task(
                task,
                run_result,
                send_status="failed",
                send_error="completion_evidence_inconsistent",
                task_status="failed",
            )
            return False
        if (
            evidence_state is SideEffectState.UNKNOWN
            or run_result.result.error.side_effect_state is SideEffectState.UNKNOWN
        ):
            run = self.store.get_agent_run(run_result.run_id)
            if run is None:
                raise RuntimeError("agent run was not persisted")
            self._apply_unknown_agent_run(
                task,
                run,
                run_result.result.error.code or "agent_side_effect_unknown",
                result=run_result.result,
            )
            return False

        result = run_result.result
        send_error = result.error.code
        if result.outcome is AgentOutcome.COMPLETED:
            send_status = "completed"
            task_status = "done"
        elif result.outcome is AgentOutcome.NO_ACTION:
            send_status = "skipped"
            task_status = "done"
        elif result.outcome is AgentOutcome.NEEDS_HUMAN:
            send_status = "blocked"
            task_status = "done"
            send_error = send_error or "needs_human"
        else:
            send_status = "failed"
            if evidence_state is SideEffectState.CONFIRMED:
                task_status = "failed"
                send_error = (
                    "agent_failed_after_confirmed_effect:"
                    f"{send_error or 'agent_failed'}"
                )
            else:
                task_status = "pending" if result.error.retryable else "failed"
                send_error = send_error or "agent_failed"

        available_at = ""
        retry_beyond_limit = (
            send_error in PERSISTENT_AGENT_RUNTIME_DEPENDENCY_ERRORS
        )
        if task_status == "pending" and (
            retry_beyond_limit or task.attempts < self.max_task_attempts
        ):
            available_at = self._reply_task_retry_available_at(task.attempts)
        elif task_status == "pending":
            task_status = "failed"
        self._finalize_agent_attempt_and_task(
            task,
            run_result,
            send_status=send_status,
            send_error=send_error,
            task_status=task_status,
            available_at=available_at,
        )
        if task_status == "done":
            return True
        return False

    def _finalize_agent_attempt_and_task(
        self,
        task: ReplyTask,
        run_result: DirectAgentRunResult,
        *,
        send_status: str,
        send_error: str,
        task_status: str,
        available_at: str = "",
    ) -> int:
        run = self.store.get_agent_run(run_result.run_id)
        if run is None:
            raise RuntimeError("agent run was not persisted")
        return self.store.finalize_agent_reply_task(
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
            codex_reason=run_result.result.summary,
            codex_session_id=run.codex_session_id,
            codex_transcript_start_line=run_result.transcript_start_line,
            codex_transcript_end_line=run_result.transcript_end_line,
            audit_tool_events_json=json.dumps(
                run_result.events,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            audit_summary=run_result.result.summary,
            send_status=send_status,
            send_error=send_error,
            channel=task.channel,
        )

    def _record_agent_attempt(
        self,
        task: ReplyTask,
        run_result: DirectAgentRunResult,
        *,
        send_status: str,
        send_error: str = "",
    ) -> int:
        run = self.store.get_agent_run(run_result.run_id)
        if run is None:
            raise RuntimeError("agent run was not persisted")
        attempt_id = self.store.record_reply_attempt(
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            action="agent_run",
            sensitivity_kind="general",
            codex_reason=run_result.result.summary,
            codex_session_id=run.codex_session_id,
            codex_transcript_start_line=run_result.transcript_start_line,
            codex_transcript_end_line=run_result.transcript_end_line,
            audit_tool_events_json=json.dumps(
                run_result.events,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            audit_summary=run_result.result.summary,
            send_status=send_status,
            channel=task.channel,
        )
        if send_error:
            self.store.update_reply_attempt(attempt_id, send_error=send_error)
        return attempt_id

    def _apply_unknown_agent_run(
        self,
        task: ReplyTask,
        run,
        reason: str,
        *,
        result: AgentResult | None = None,
    ) -> None:
        summary = result.summary if result is not None else "外部动作结果未知，等待只读核对。"
        run_result = DirectAgentRunResult(
            run_id=run.id,
            result=result
            or AgentResult(
                outcome=AgentOutcome.FAILED,
                summary=summary,
                error=AgentError(
                    code=reason or "agent_side_effect_unknown",
                    side_effect_state=SideEffectState.UNKNOWN,
                ),
            ),
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=run.transcript_end_line,
            events=tuple(run.tool_events),
        )
        self._record_agent_attempt(
            task,
            run_result,
            send_status="blocked",
            send_error=reason or "agent_side_effect_unknown",
        )

    def _queued_task_prompt_context_messages(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> tuple[list[DingTalkMessage], list[DingTalkMessage]]:
        if self._is_oa_pending_scan_trigger(trigger):
            return [trigger], [trigger]
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

    @staticmethod
    def _is_oa_pending_scan_trigger(trigger: DingTalkMessage) -> bool:
        return str(trigger.raw_payload.get("source") or "") == "oa_pending_scan"

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

    def _robot_direct_messages_by_conversation(self) -> dict[str, list[DingTalkMessage]]:
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
            event
            for event in candidates
            if self._calendar_event_is_self_pending(event)
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
                message
                for message in messages
                if message.addresses_principal()
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
