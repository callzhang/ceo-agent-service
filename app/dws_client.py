import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from robust_json import extract_all

from app.config import chat_bot_names, read_env_file
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.message_split import split_dingtalk_text

TITLE_INFORMATION_UNIT_LIMIT = 20
TITLE_WORD_OR_CJK_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*|[\u4e00-\u9fff]"
)
TITLE_AT_FILE_ESCAPE_PREFIX = "回复："
TEXT_AT_FILE_ESCAPE_PREFIX = " "
DINGTALK_MESSAGE_TIME_ZONE = ZoneInfo("Asia/Shanghai")
MIN_UNREAD_MESSAGE_LIST_LIMIT = 5
DWS_AGENT_CODE_ENV = "DINGTALK_DWS_AGENTCODE"
DWS_DEFAULT_AGENT_CODE = "ceo-agent-service"
BRACKETED_EMOJI_PATTERN = re.compile(r"^\[([^\[\]]+)\]$")


def _local_time_zone():
    return datetime.now().astimezone().tzinfo


def local_time_zone_name() -> str:
    path = os.path.realpath("/etc/localtime")
    for marker in ("/zoneinfo/", "/usr/share/zoneinfo/"):
        if marker in path:
            return path.split(marker, 1)[1]
    return str(_local_time_zone())


def normalize_message_emoji(emoji: str) -> str:
    normalized = emoji.strip()
    match = BRACKETED_EMOJI_PATTERN.match(normalized)
    if match:
        normalized = match.group(1).strip()
    return normalized


def extract_recall_key_from_send_result(send_result: dict[str, Any] | None) -> str:
    if not send_result:
        return ""
    chunks = send_result.get("chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            recall_key = extract_recall_key_from_send_result(chunk.get("send_result"))
            if recall_key:
                return recall_key
        return ""
    result = send_result.get("result")
    if not isinstance(result, dict):
        return ""
    recall_key = result.get("processQueryKey")
    if isinstance(recall_key, str):
        return recall_key
    recall_keys = result.get("processQueryKeys")
    if isinstance(recall_keys, list) and recall_keys:
        first = recall_keys[0]
        if isinstance(first, str):
            return first
    return ""


class DwsError(RuntimeError):
    DIRECT_CHAT_TARGET_NOT_FOUND_CODE = "DIRECT_CHAT_TARGET_NOT_FOUND"
    AGENT_CODE_NOT_EXISTS_CODE = "AGENT_CODE_NOT_EXISTS"
    LOGIN_ERROR_CODES = {"2", "not_authenticated"}
    LOGIN_ERROR_MARKERS = (
        "not_authenticated",
        "not authenticated",
        "your session has ended",
        "failed to refresh token",
        "未登录",
        "登录态失效",
    )

    def __init__(
        self,
        message: str,
        code: str | None = None,
        *,
        required_scopes: list[str] | tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.required_scopes = tuple(
            scope.strip()
            for scope in required_scopes
            if isinstance(scope, str) and scope.strip()
        )

    @property
    def needs_authorization(self) -> bool:
        return self.code in {
            "PAT_HIGH_RISK_NO_PERMISSION",
            "PAT_MEDIUM_RISK_NO_PERMISSION",
            self.AGENT_CODE_NOT_EXISTS_CODE,
        }

    @property
    def needs_login(self) -> bool:
        if self.code in self.LOGIN_ERROR_CODES:
            return True
        message = str(self).casefold()
        return any(marker in message for marker in self.LOGIN_ERROR_MARKERS)


def dws_noninteractive_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    next_env = dict(os.environ if env is None else env)
    agent_code = (
        next_env.get(DWS_AGENT_CODE_ENV)
        or next_env.get("CEO_DWS_AGENT_CODE")
        or DWS_DEFAULT_AGENT_CODE
    )
    next_env[DWS_AGENT_CODE_ENV] = agent_code
    return next_env


def native_reply_delivery_payload(
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    send_result: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
    delivery_kind: str = "native_reply",
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(extra or {})
    payload["delivery"] = {
        "kind": delivery_kind,
        "conversation_id": conversation.open_conversation_id,
        "ref_message_id": trigger.open_message_id,
        "ref_sender_open_dingtalk_id": trigger.sender_open_dingtalk_id or "",
    }
    payload["send_result"] = send_result or {}
    return payload


class DwsUserProfile(BaseModel):
    user_id: str
    name: str = ""
    nick: str = ""
    title: str = ""
    open_dingtalk_id: str | None = None
    manager_user_id: str | None = None
    manager_name: str = ""
    department_ids: set[str] = set()
    department_names: set[str] = set()
    org_labels: list[str] = Field(default_factory=list)
    has_subordinate: bool | None = None


class DwsDocumentSearchResult(BaseModel):
    node_id: str
    name: str = ""
    extension: str = ""
    content_type: str = ""
    node_type: str = ""
    doc_url: str = ""


class DwsCalendarAttendee(BaseModel):
    display_name: str = ""
    is_self: bool = False
    response_status: str = ""
    user_id: str = ""
    open_dingtalk_id: str = ""


class DwsCalendarEvent(BaseModel):
    event_id: str = ""
    title: str = ""
    start_time: str = ""
    end_time: str = ""
    description: str = ""
    organizer: str = ""
    response_status: str = ""
    self_response_status: str = ""
    attendees: list[str] = Field(default_factory=list)
    attendee_details: list[DwsCalendarAttendee] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    status: str = ""
    created_ms: int = 0
    updated_ms: int = 0

    @property
    def has_description(self) -> bool:
        return bool(self.description.strip())


class DwsMinutesPermissionRequest(BaseModel):
    uuids: list[str]
    member_uids: list[int]
    policy_id: int = 3
    role_sub_resource_ids: list[str] = Field(default_factory=list)
    cover_permission: bool = False


class DwsOaApprovalCandidate(BaseModel):
    process_instance_id: str
    title: str = ""
    process_name: str = ""


class DwsClient:
    # DWS returns generic code 6 for transient discovery/network failures such as
    # TLS handshake timeouts before the request reaches a business API.
    RETRYABLE_ERROR_CODES = {"TIMEOUT_ERROR", "NETWORK_ERROR", "6"}
    DISCOVERY_CACHE_REFRESH_CODES = {"6"}
    DOC_READ_RETRYABLE_ERROR_CODES = {"internalError"}
    MESSAGE_LIST_RETRYABLE_ERROR_CODES = {"SYSTEM_ERROR"}
    TOKEN_VERIFIED_RETRYABLE_ERROR_CODES = {"TOKEN_VERIFIED_FAILED"}
    TOKEN_VERIFIED_RETRYABLE_READ_COMMANDS = {
        ("calendar", "event", "get"),
        ("calendar", "event", "list"),
        ("chat", "conversation-info"),
        ("chat", "message", "list"),
        ("chat", "message", "list-direct"),
        ("chat", "message", "list-by-ids"),
        ("chat", "message", "list-by-sender"),
        ("chat", "message", "list-mentions"),
        ("chat", "message", "search"),
        ("chat", "message", "list-unread-conversations"),
        ("chat", "search"),
        ("contact", "user", "get"),
        ("contact", "user", "search"),
    }
    SENSITIVE_COMMAND_FLAGS = {
        "--robot-code",
        "--webhook",
        "--secret",
        "--client-secret",
        "--access-token",
        "--token",
    }
    CLI_AUTH_ENV_KEYS = {
        "DWS_CLIENT_ID",
        "DWS_CLIENT_SECRET",
        "DINGTALK_APP_KEY",
        "DINGTALK_APP_SECRET",
    }

    def __init__(
        self,
        dws_bin: str = "dws",
        timeout_seconds: int = 30,
        ding_robot_code: str | None = None,
        ding_robot_name: str | None = None,
        ding_receiver_user_id: str | None = None,
        transient_retry_attempts: int = 3,
        transient_retry_delay_seconds: float = 1.0,
    ):
        self.dws_bin = dws_bin
        self.timeout_seconds = timeout_seconds
        self.ding_robot_code = (
            ding_robot_code
            or os.getenv("DINGTALK_DING_ROBOT_CODE")
            or os.getenv("CEO_DING_ROBOT_CODE")
        )
        self.ding_robot_name = ding_robot_name
        self.ding_receiver_user_id = ding_receiver_user_id
        self.transient_retry_attempts = transient_retry_attempts
        self.transient_retry_delay_seconds = transient_retry_delay_seconds
        self._bot_open_dingtalk_ids_by_name: dict[str, set[str]] = {}

    def build_list_unread_conversations_command(self, count: int) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "message",
            "list-unread-conversations",
            "--count",
            str(count),
            "--format",
            "json",
        ]

    def build_list_messages_by_ids_command(
        self, message_ids: list[str],
    ) -> list[str]:
        if not message_ids:
            raise ValueError("at least one DingTalk message id is required")
        if len(message_ids) > 50:
            raise ValueError("DingTalk list-by-ids supports at most 50 message ids")
        return [
            self.dws_bin,
            "chat",
            "message",
            "list-by-ids",
            "--msg-ids",
            ",".join(message_ids),
            "--format",
            "json",
        ]

    def build_upgrade_check_command(self) -> list[str]:
        return [self.dws_bin, "upgrade", "--check", "--format", "json"]

    def build_upgrade_command(self) -> list[str]:
        return [self.dws_bin, "upgrade", "-y", "--format", "json"]

    def build_auth_login_command(self) -> list[str]:
        return [self.dws_bin, "auth", "login"]

    def build_pat_authorization_command(self, scopes: list[str]) -> list[str]:
        agent_code = dws_noninteractive_environment().get(
            DWS_AGENT_CODE_ENV, DWS_DEFAULT_AGENT_CODE
        )
        return [
            self.dws_bin,
            "pat",
            "chmod",
            *scopes,
            "--agentCode",
            agent_code,
            "--grant-type",
            "permanent",
            "--yes",
            "--format",
            "json",
        ]

    def build_auth_export_command(self, output_path: Path) -> list[str]:
        return [
            self.dws_bin,
            "auth",
            "export",
            "-o",
            str(output_path),
            "--format",
            "json",
        ]

    def build_auth_import_command(self, input_path: Path) -> list[str]:
        return [
            self.dws_bin,
            "auth",
            "import",
            "-i",
            str(input_path),
            "--force",
            "--format",
            "json",
        ]

    def build_auth_status_command(self) -> list[str]:
        return [self.dws_bin, "auth", "status", "--format", "json"]

    def build_list_messages_by_sender_command(
        self,
        sender_user_id: str,
        start: str,
        end: str,
        limit: int,
        cursor: str,
    ) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "message",
            "list-by-sender",
            "--sender-user-id",
            sender_user_id,
            "--start",
            start,
            "--end",
            end,
            "--limit",
            str(limit),
            "--cursor",
            cursor,
            "--format",
            "json",
        ]

    def build_search_messages_command(
        self,
        keyword: str,
        start: str,
        end: str,
        limit: int,
        cursor: str,
    ) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "message",
            "search",
            "--keyword",
            keyword,
            "--start",
            start,
            "--end",
            end,
            "--limit",
            str(limit),
            "--cursor",
            cursor,
            "--format",
            "json",
        ]

    def build_list_all_messages_command(
        self,
        start: str,
        end: str,
        limit: int,
        cursor: str = "0",
    ) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "message",
            "list-all",
            "--start",
            start,
            "--end",
            end,
            "--limit",
            str(limit),
            "--cursor",
            cursor,
            "--format",
            "json",
        ]

    def build_search_conversations_command(self, query: str) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "search",
            "--query",
            query,
            "--format",
            "json",
        ]

    def build_conversation_info_command(self, open_conversation_id: str) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "conversation-info",
            "--group",
            open_conversation_id,
            "--format",
            "json",
        ]

    def build_send_message_command(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
        title: str | None = None,
    ) -> list[str]:
        command = [
            self.dws_bin,
            "chat",
            "message",
            "send",
        ]
        targets = [
            value
            for value in (conversation_id, user_id, open_dingtalk_id)
            if value is not None
        ]
        if len(targets) != 1:
            raise ValueError("exactly one DingTalk send target is required")
        if conversation_id is not None:
            command.extend(["--group", conversation_id])
        elif user_id is not None:
            command.extend(["--user", user_id])
        else:
            command.extend(["--open-dingtalk-id", open_dingtalk_id or ""])
        command.extend(
            [
                "--title",
                self._literal_cli_value(
                    self._message_title(title if title is not None else text),
                    is_title=True,
                ),
            ]
        )
        if at_open_dingtalk_ids:
            if conversation_id is not None:
                command.extend(
                    ["--at-open-dingtalk-ids", ",".join(at_open_dingtalk_ids)]
                )
        del at_users
        send_text = text
        if conversation_id is not None and at_open_dingtalk_ids:
            send_text = self._with_visible_at_mentions(
                text,
                at_open_dingtalk_names or [],
            )
        command.extend(
            ["--text", self._literal_cli_value(send_text), "--format", "json", "--yes"]
        )
        return command

    def build_reply_message_command(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> list[str]:
        del at_users, at_open_dingtalk_ids, at_open_dingtalk_names
        if not conversation_id or not ref_message_id or not ref_sender_open_dingtalk_id:
            raise ValueError("conversation id, ref message id, and ref sender are required")
        return [
            self.dws_bin,
            "chat",
            "message",
            "reply",
            "--conversation-id",
            conversation_id,
            "--ref-msg-id",
            ref_message_id,
            "--ref-sender",
            ref_sender_open_dingtalk_id,
            "--text",
            self._literal_cli_value(text),
            "--format",
            "json",
            "--yes",
        ]

    def build_mail_reply_command(
        self,
        *,
        mailbox: str,
        message_id: str,
        subject: str,
        content: str,
    ) -> list[str]:
        if not all(
            value.strip() for value in (mailbox, message_id, subject, content)
        ):
            raise ValueError("mailbox, message id, subject, and content are required")
        return [
            self.dws_bin,
            "mail",
            "message",
            "reply",
            "--from",
            mailbox,
            "--id",
            message_id,
            "--subject",
            subject,
            "--content",
            content,
            "--format",
            "json",
            "--yes",
        ]

    def build_read_recent_messages_command(
        self, conversation: DingTalkConversation, limit: int = 50
    ) -> list[str]:
        return self.build_message_list_command(
            conversation=conversation,
            limit=limit,
            forward=False,
        )

    def build_read_unread_messages_command(
        self, conversation: DingTalkConversation
    ) -> list[str]:
        # DWS rejects tiny windows when multiple messages share the cursor timestamp.
        if conversation.unread_point == 1:
            limit = 1
        else:
            limit = max(conversation.unread_point, MIN_UNREAD_MESSAGE_LIST_LIMIT)
        return self.build_message_list_command(
            conversation=conversation,
            limit=limit,
            forward=False,
        )

    def build_read_mentioned_messages_command(
        self,
        conversation: DingTalkConversation | None = None,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ) -> list[str]:
        local_time_zone = _local_time_zone()
        end_time = datetime.now(tz=local_time_zone)
        start_time = end_time - timedelta(hours=lookback_hours)
        command = [
            self.dws_bin,
            "chat",
            "message",
            "list-mentions",
            "--start",
            start_time.isoformat(),
            "--end",
            end_time.isoformat(),
        ]
        if conversation is not None:
            command.extend(["--group", conversation.open_conversation_id])
        command.extend(
            [
                "--limit",
                str(limit),
                "--cursor",
                cursor,
                "--format",
                "json",
            ]
        )
        return command

    def build_list_calendar_events_command(
        self,
        start: str,
        end: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[str]:
        command = [
            self.dws_bin,
            "calendar",
            "event",
            "list",
            "--start",
            start,
            "--end",
            end,
        ]
        if limit is not None:
            command.extend(["--limit", str(limit)])
        if cursor is not None:
            command.extend(["--cursor", cursor])
        command.extend(["--format", "json"])
        return command

    def build_get_calendar_event_command(self, event_id: str) -> list[str]:
        return [
            self.dws_bin,
            "calendar",
            "event",
            "get",
            "--id",
            event_id,
            "--format",
            "json",
        ]

    def build_respond_calendar_event_command(
        self,
        event_id: str,
        response_status: str,
    ) -> list[str]:
        if response_status not in {
            "needsAction",
            "accepted",
            "declined",
            "tentative",
        }:
            raise ValueError(f"unsupported calendar response status: {response_status}")
        return [
            self.dws_bin,
            "calendar",
            "event",
            "respond",
            "--id",
            event_id,
            "--status",
            response_status,
            "--format",
            "json",
            "--yes",
        ]

    def build_add_minutes_member_permission_command(
        self, request: DwsMinutesPermissionRequest
    ) -> list[str]:
        command = [
            self.dws_bin,
            "mcp",
            "minutes",
            "add_member_permission",
            "--uuids",
            ",".join(request.uuids),
            "--memberUids",
            ",".join(str(uid) for uid in request.member_uids),
            "--policyId",
            str(request.policy_id),
            "--coverPermission",
            "true" if request.cover_permission else "false",
        ]
        if request.role_sub_resource_ids:
            command.extend(
                [
                    "--roleSubResourceIds",
                    ",".join(request.role_sub_resource_ids),
                ]
            )
        command.extend(["--format", "json", "--yes"])
        return command

    def build_oa_approval_action_command(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ) -> list[str]:
        if action == "通过":
            command_action = "approve"
        elif action == "拒绝":
            command_action = "reject"
        elif action == "退回":
            raise ValueError("DWS does not support a distinct OA return action")
        else:
            raise ValueError(f"unsupported OA approval action: {action}")
        return [
            self.dws_bin,
            "oa",
            "approval",
            command_action,
            "--instance-id",
            process_instance_id,
            "--task-id",
            task_id,
            "--remark",
            self._literal_cli_value(remark),
            "--format",
            "json",
            "--yes",
        ]

    def build_oa_approval_comment_command(
        self,
        process_instance_id: str,
        text: str,
    ) -> list[str]:
        if not process_instance_id.strip():
            raise ValueError("missing OA process instance id")
        if not text.strip():
            raise ValueError("missing OA approval comment text")
        return [
            self.dws_bin,
            "oa",
            "approval",
            "oa-comments",
            "--instance-id",
            process_instance_id,
            "--text",
            self._literal_cli_value(text),
            "--format",
            "json",
            "--yes",
        ]

    def build_list_pending_oa_approvals_command(
        self, page: int = 1, size: int = 30
    ) -> list[str]:
        return [
            self.dws_bin,
            "oa",
            "approval",
            "list-pending",
            "--page",
            str(page),
            "--size",
            str(size),
            "--format",
            "json",
        ]

    def build_read_oa_approval_detail_command(self, process_instance_id: str) -> list[str]:
        return [
            self.dws_bin,
            "oa",
            "approval",
            "detail",
            "--instance-id",
            process_instance_id,
            "--format",
            "json",
        ]

    def build_read_oa_approval_records_command(
        self, process_instance_id: str
    ) -> list[str]:
        return [
            self.dws_bin,
            "oa",
            "approval",
            "records",
            "--instance-id",
            process_instance_id,
            "--format",
            "json",
        ]

    def build_read_oa_approval_tasks_command(self, process_instance_id: str) -> list[str]:
        return [
            self.dws_bin,
            "oa",
            "approval",
            "tasks",
            "--instance-id",
            process_instance_id,
            "--format",
            "json",
        ]

    def build_message_list_command(
        self,
        conversation: DingTalkConversation,
        limit: int,
        forward: bool,
    ) -> list[str]:
        message_time = self._message_list_time(conversation.last_message_create_at)
        if conversation.single_chat:
            command = [
                self.dws_bin,
                "chat",
                "message",
                "list-direct",
            ]
            if conversation.direct_user_id:
                command.extend(["--user", conversation.direct_user_id])
            elif conversation.direct_open_dingtalk_id:
                command.extend(
                    ["--open-dingtalk-id", conversation.direct_open_dingtalk_id]
                )
            else:
                raise DwsError("single chat direct target is required")
            command.extend(
                [
                    "--time",
                    message_time,
                    f"--forward={'true' if forward else 'false'}",
                    "--limit",
                    str(limit),
                    "--format",
                    "json",
                ]
            )
            return command
        return [
            self.dws_bin,
            "chat",
            "message",
            "list",
            "--group",
            conversation.open_conversation_id,
            "--time",
            message_time,
            f"--forward={'true' if forward else 'false'}",
            "--limit",
            str(limit),
            "--format",
            "json",
        ]

    def build_get_user_profiles_command(self, user_ids: list[str]) -> list[str]:
        return [
            self.dws_bin,
            "contact",
            "user",
            "get",
            "--ids",
            ",".join(user_ids),
            "--format",
            "json",
        ]

    def build_search_user_command(self, query: str) -> list[str]:
        return [
            self.dws_bin,
            "contact",
            "user",
            "search",
            "--query",
            query,
            "--format",
            "json",
        ]

    def build_search_department_command(self, query: str) -> list[str]:
        return [
            self.dws_bin,
            "contact",
            "dept",
            "search",
            "--query",
            query,
            "--format",
            "json",
        ]

    def build_list_department_members_command(self, department_ids: list[str]) -> list[str]:
        return [
            self.dws_bin,
            "contact",
            "dept",
            "list-members",
            "--ids",
            ",".join(department_ids),
            "--format",
            "json",
        ]

    def build_get_current_user_command(self) -> list[str]:
        return [
            self.dws_bin,
            "contact",
            "user",
            "get-self",
            "--format",
            "json",
        ]

    def build_read_doc_command(self, node: str) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "read",
            "--node",
            node,
            "--format",
            "json",
        ]

    def build_doc_list_command(
        self,
        workspace_id: str | None = None,
        folder_id: str | None = None,
        page_token: str = "",
    ) -> list[str]:
        command = [self.dws_bin, "doc", "list"]
        if workspace_id:
            command.extend(["--workspace", workspace_id])
        if folder_id:
            command.extend(["--folder", folder_id])
        if page_token:
            command.extend(["--page-token", page_token])
        command.extend(["--format", "json"])
        return command

    def build_doc_info_command(self, node: str) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "info",
            "--node",
            node,
            "--format",
            "json",
        ]

    def build_create_markdown_doc_command(self, name: str, content: str) -> list[str]:
        if not name.strip():
            raise ValueError("missing doc name")
        if not content.strip():
            raise ValueError("missing doc content")
        return [
            self.dws_bin,
            "doc",
            "create",
            "--name",
            name,
            "--content",
            content,
            "--content-format",
            "markdown",
            "--format",
            "json",
            "--yes",
        ]

    def build_add_doc_editor_permission_command(
        self,
        node: str,
        user_ids: list[str],
    ) -> list[str]:
        node = node.strip()
        normalized_user_ids = self._unique_non_empty(user_ids)
        if not node:
            raise ValueError("missing doc node")
        if not normalized_user_ids:
            raise ValueError("missing doc editor user ids")
        return [
            self.dws_bin,
            "doc",
            "permission",
            "add",
            "--node",
            node,
            "--user",
            ",".join(normalized_user_ids),
            "--role",
            "EDITOR",
            "--format",
            "json",
            "--yes",
        ]

    def build_aitable_base_get_command(self, base_id: str) -> list[str]:
        return [
            self.dws_bin,
            "aitable",
            "base",
            "get",
            "--base-id",
            base_id,
            "--format",
            "json",
        ]

    def build_aitable_table_get_command(
        self, base_id: str, table_ids: list[str] | None = None
    ) -> list[str]:
        command = [
            self.dws_bin,
            "aitable",
            "table",
            "get",
            "--base-id",
            base_id,
        ]
        if table_ids:
            command.extend(["--table-ids", ",".join(table_ids[:10])])
        command.extend(["--format", "json"])
        return command

    def build_aitable_record_query_command(
        self, base_id: str, table_id: str, limit: int = 10
    ) -> list[str]:
        return [
            self.dws_bin,
            "aitable",
            "record",
            "query",
            "--base-id",
            base_id,
            "--table-id",
            table_id,
            "--limit",
            str(limit),
            "--format",
            "json",
        ]

    def build_search_documents_command(
        self, query: str, page_size: int = 5
    ) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "search",
            "--query",
            query,
            "--page-size",
            str(page_size),
            "--format",
            "json",
        ]

    def build_download_doc_command(self, node: str, output_path: str) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "download",
            "--node",
            node,
            "--output",
            output_path,
            "--format",
            "json",
        ]

    def build_create_doc_comment_command(self, node_id: str, content: str) -> list[str]:
        if not node_id.strip():
            raise ValueError("missing doc comment node")
        if not content.strip():
            raise ValueError("missing doc comment content")
        return [
            self.dws_bin,
            "doc",
            "comment",
            "create",
            "--node",
            node_id,
            "--content",
            content,
            "--format",
            "json",
            "--yes",
        ]

    def build_list_minutes_command(
        self,
        *,
        scope: str = "all",
        limit: int = 20,
        cursor: str = "",
        start: str = "",
        end: str = "",
    ) -> list[str]:
        if scope not in {"all", "mine", "shared"}:
            raise ValueError("minutes scope must be one of: all, mine, shared")
        if limit < 1:
            raise ValueError("limit must be positive")
        command = [
            self.dws_bin,
            "minutes",
            "list",
            scope,
            "--limit",
            str(limit),
        ]
        if cursor:
            command.extend(["--cursor", cursor])
        if start:
            command.extend(["--start", start])
        if end:
            command.extend(["--end", end])
        command.extend(["--format", "json"])
        return command

    def build_minutes_info_command(self, task_uuid: str) -> list[str]:
        return [
            self.dws_bin,
            "minutes",
            "get",
            "info",
            "--id",
            task_uuid,
            "--format",
            "json",
        ]

    def build_minutes_summary_command(self, task_uuid: str) -> list[str]:
        return [
            self.dws_bin,
            "minutes",
            "get",
            "summary",
            "--id",
            task_uuid,
            "--format",
            "json",
        ]

    def build_minutes_todos_command(self, task_uuid: str) -> list[str]:
        return [
            self.dws_bin,
            "minutes",
            "get",
            "todos",
            "--id",
            task_uuid,
            "--format",
            "json",
        ]

    def build_todo_create_command(
        self,
        *,
        title: str,
        executor_user_id: str,
        due: str,
        priority: int,
    ) -> list[str]:
        if not title.strip():
            raise ValueError("DingTalk todo title is required")
        if not executor_user_id.strip():
            raise ValueError("DingTalk todo executor_user_id is required")
        if not due.strip():
            raise ValueError("DingTalk todo due is required")
        if priority not in {10, 20, 30, 40}:
            raise ValueError("DingTalk todo priority must be one of 10, 20, 30, 40")
        return [
            self.dws_bin,
            "todo",
            "task",
            "create",
            "--title",
            title,
            "--executors",
            executor_user_id,
            "--due",
            due,
            "--priority",
            str(priority),
            "--format",
            "json",
            "--yes",
        ]

    def build_todo_get_command(self, task_id: str) -> list[str]:
        if not task_id.strip():
            raise ValueError("DingTalk todo task_id is required")
        return [
            self.dws_bin,
            "todo",
            "task",
            "get",
            "--task-id",
            task_id,
            "--format",
            "json",
        ]

    def build_todo_done_command(self, task_id: str, *, done: bool) -> list[str]:
        if not task_id.strip():
            raise ValueError("DingTalk todo task_id is required")
        if not isinstance(done, bool):
            raise ValueError("DingTalk todo done must be a bool")
        return [
            self.dws_bin,
            "todo",
            "task",
            "done",
            "--task-id",
            task_id,
            "--status",
            "true" if done else "false",
            "--format",
            "json",
            "--yes",
        ]

    def build_minutes_transcription_command(
        self,
        task_uuid: str,
        *,
        next_token: str = "",
    ) -> list[str]:
        command = [
            self.dws_bin,
            "minutes",
            "get",
            "transcription",
            "--id",
            task_uuid,
            "--direction",
            "forward",
        ]
        if next_token:
            command.extend(["--next-token", next_token])
        command.extend(["--format", "json"])
        return command

    def build_get_resource_download_url_command(
        self,
        open_conversation_id: str,
        open_message_id: str,
        resource_id: str,
        resource_type: str,
        output_path: str | Path,
    ) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "message",
            "download-media",
            "--type",
            resource_type,
            "--resource-id",
            resource_id,
            "--message-id",
            open_message_id,
            "--open-conversation-id",
            open_conversation_id,
            "--output",
            str(output_path),
            "--format",
            "json",
            "--yes",
            "--timeout",
            str(self.timeout_seconds),
        ]

    def build_download_robot_message_file_command(self, download_code: str) -> list[str]:
        robot_code = self._ding_robot_code()
        if not robot_code:
            raise DwsError(
                "DING robot code is not configured; set DINGTALK_DING_ROBOT_CODE, CEO_DING_ROBOT_CODE, or CEO_DING_ROBOT_NAME"
            )
        return [
            self.dws_bin,
            "api",
            "POST",
            "/v1.0/robot/messageFiles/download",
            "--data",
            json.dumps(
                {"downloadCode": download_code, "robotCode": robot_code},
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ]

    def build_ding_self_command(self, receiver_user_id: str, text: str) -> list[str]:
        robot_code = self._ding_robot_code()
        if not robot_code:
            raise DwsError(
                "DING robot code is not configured; set DINGTALK_DING_ROBOT_CODE, CEO_DING_ROBOT_CODE, or CEO_DING_ROBOT_NAME"
            )
        command = [
            self.dws_bin,
            "ding",
            "message",
            "send",
            "--users",
            receiver_user_id,
            "--type",
            "app",
            "--content",
            text,
        ]
        command.extend(["--robot-code", robot_code])
        command.extend(["--format", "json"])
        return command

    def build_send_message_by_bot_command(
        self,
        *,
        text: str,
        user_ids: list[str] | None = None,
        conversation_id: str | None = None,
        title: str | None = None,
    ) -> list[str]:
        robot_code = self._ding_robot_code()
        if not robot_code:
            raise DwsError(
                "DING robot code is not configured; set DINGTALK_DING_ROBOT_CODE, CEO_DING_ROBOT_CODE, or CEO_DING_ROBOT_NAME"
            )
        if bool(user_ids) == bool(conversation_id):
            raise ValueError("exactly one DingTalk bot send target is required")
        command = [
            self.dws_bin,
            "chat",
            "message",
            "send-by-bot",
            "--robot-code",
            robot_code,
        ]
        if conversation_id:
            command.extend(["--group", conversation_id])
        else:
            command.extend(["--users", ",".join(user_ids or [])])
        command.extend(
            [
                "--title",
                self._literal_cli_value(
                    self._message_title(title if title is not None else text),
                    is_title=True,
                ),
                "--text",
                self._literal_cli_value(text),
                "--format",
                "json",
                "--yes",
            ]
        )
        return command

    def build_recall_bot_message_command(
        self, conversation_id: str | None, process_query_key: str
    ) -> list[str]:
        robot_code = self._ding_robot_code()
        if not robot_code:
            raise DwsError("DING robot code is not configured")
        command = [
            self.dws_bin,
            "chat",
            "message",
            "recall-by-bot",
            "--robot-code",
            robot_code,
        ]
        if conversation_id is not None:
            command.extend(["--group", conversation_id])
        command.extend(["--keys", process_query_key, "--format", "json", "--yes"])
        return command

    def build_recall_message_command(
        self, conversation_id: str, message_id: str
    ) -> list[str]:
        if not conversation_id or not message_id:
            raise ValueError("conversation id and message id are required")
        return [
            self.dws_bin,
            "chat",
            "message",
            "recall",
            "--conversation-id",
            conversation_id,
            "--msg-id",
            message_id,
            "--format",
            "json",
            "--yes",
        ]

    def build_query_message_send_status_command(self, open_task_id: str) -> list[str]:
        if not open_task_id:
            raise ValueError("open task id is required")
        return [
            self.dws_bin,
            "chat",
            "message",
            "query-send-status",
            "--open-task-id",
            open_task_id,
            "--format",
            "json",
        ]

    def build_add_message_emoji_command(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str,
    ) -> list[str]:
        normalized_emoji = normalize_message_emoji(emoji)
        if not conversation_id or not message_id or not normalized_emoji:
            raise ValueError("conversation id, message id, and emoji are required")
        return [
            self.dws_bin,
            "chat",
            "message",
            "add-emoji",
            "--group",
            conversation_id,
            "--msg-id",
            message_id,
            "--emoji",
            normalized_emoji,
            "--format",
            "json",
            "--yes",
        ]

    def build_add_message_text_emotion_command(
        self,
        conversation_id: str,
        message_id: str,
        *,
        text: str,
        emotion_id: str,
        emotion_name: str,
        background_id: str,
    ) -> list[str]:
        if not all(
            value.strip()
            for value in (
                conversation_id,
                message_id,
                text,
                emotion_id,
                emotion_name,
                background_id,
            )
        ):
            raise ValueError(
                "conversation id, message id, text, emotion id, emotion name, and background id are required"
            )
        return [
            self.dws_bin,
            "chat",
            "message",
            "add-text-emotion",
            "--group",
            conversation_id,
            "--msg-id",
            message_id,
            "--text",
            text.strip(),
            "--emotion-id",
            emotion_id.strip(),
            "--emotion-name",
            emotion_name.strip(),
            "--background-id",
            background_id.strip(),
            "--format",
            "json",
            "--yes",
        ]

    def build_create_message_text_emotion_command(
        self,
        *,
        text: str,
        emotion_name: str,
        background_id: str = "",
    ) -> list[str]:
        if not text.strip() or not emotion_name.strip():
            raise ValueError("text and emotion name are required")
        command = [
            self.dws_bin,
            "chat",
            "message",
            "create-text-emotion",
            "--text",
            text.strip(),
            "--emotion-name",
            emotion_name.strip(),
        ]
        if background_id.strip():
            command.extend(["--background-id", background_id.strip()])
        command.extend(["--format", "json", "--yes"])
        return command

    def list_unread_conversations(self, count: int) -> list[DingTalkConversation]:
        payload = self.run_json(self.build_list_unread_conversations_command(count))
        return self.parse_unread_conversations(payload)

    def check_upgrade(self) -> dict[str, Any]:
        payload = self.run_json(self.build_upgrade_check_command())
        if not isinstance(payload, dict):
            raise DwsError("invalid dws upgrade check response")
        return payload

    def upgrade(self) -> str:
        return self.run_text(
            self.build_upgrade_command(),
            timeout_seconds=max(self.timeout_seconds, 180),
        )

    def start_auth_login(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self.build_auth_login_command(),
            text=True,
            start_new_session=True,
            env=self._cli_environment(),
        )

    def start_pat_authorization(
        self, scopes: list[str] | tuple[str, ...]
    ) -> subprocess.Popen[str]:
        normalized_scopes = self._unique_non_empty(list(scopes))
        if not normalized_scopes:
            raise DwsError("missing DWS PAT authorization scopes")
        return subprocess.Popen(
            self.build_pat_authorization_command(normalized_scopes),
            text=True,
            start_new_session=True,
            env=self._pat_authorization_environment(),
        )

    def export_auth_archive(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.name}.tmp")
        if temp_path.exists():
            temp_path.unlink()
        self.run_text(self.build_auth_export_command(temp_path))
        os.chmod(temp_path, 0o600)
        temp_path.replace(output_path)

    def import_auth_archive(self, input_path: Path) -> None:
        self.run_text(self.build_auth_import_command(input_path))

    def auth_status(self) -> dict[str, Any]:
        payload = self.run_json(self.build_auth_status_command())
        if not isinstance(payload, dict):
            raise DwsError("invalid dws auth status response")
        return payload

    def list_messages_by_sender(
        self,
        sender_user_id: str,
        start: str,
        end: str,
        limit: int,
        cursor: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_list_messages_by_sender_command(
                sender_user_id=sender_user_id,
                start=start,
                end=end,
                limit=limit,
                cursor=cursor,
            )
        )

    def list_all_messages(
        self,
        *,
        start: str,
        end: str,
        limit: int,
        cursor: str = "0",
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_list_all_messages_command(
                start=start,
                end=end,
                limit=limit,
                cursor=cursor,
            )
        )

    def search_messages(
        self,
        keyword: str,
        start: str,
        end: str,
        limit: int,
        cursor: str = "0",
    ) -> list[DingTalkMessage]:
        payload = self.run_json(
            self.build_search_messages_command(
                keyword=keyword,
                start=start,
                end=end,
                limit=limit,
                cursor=cursor,
            )
        )
        return self.parse_messages(payload, conversation_title="", single_chat=False)

    def search_conversations(self, query: str) -> list[DingTalkConversation]:
        payload = self.run_json(self.build_search_conversations_command(query))
        return self.parse_search_conversations(payload)

    def client_conversation_id(self, open_conversation_id: str) -> str:
        payload = self.run_json(self.build_conversation_info_command(open_conversation_id))
        return self.parse_client_conversation_id(payload, open_conversation_id)

    def read_recent_messages(
        self, conversation: DingTalkConversation, limit: int = 50
    ) -> list[DingTalkMessage]:
        conversation = self._with_single_chat_direct_target(conversation)
        payload = self.run_json(
            self.build_read_recent_messages_command(conversation, limit)
        )
        return self.parse_messages(
            payload,
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
        )

    def read_unread_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        if conversation.unread_point <= 0:
            return []
        conversation = self._with_single_chat_direct_target(conversation)
        payload = self.run_json(self.build_read_unread_messages_command(conversation))
        return list(
            reversed(
                self.parse_messages(
                    payload,
                    conversation_title=conversation.title,
                    single_chat=conversation.single_chat,
                )
            )
        )

    def list_messages_by_ids(self, message_ids: list[str]) -> list[DingTalkMessage]:
        if not message_ids:
            return []
        payload = self.run_json(self.build_list_messages_by_ids_command(message_ids))
        return self.parse_messages(
            payload,
            conversation_title="",
            single_chat=False,
        )

    def read_mentioned_messages(
        self,
        conversation: DingTalkConversation | None = None,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        payload = self.run_json(
            self.build_read_mentioned_messages_command(
                conversation,
                limit=limit,
                cursor=cursor,
                lookback_hours=lookback_hours,
            )
        )
        return self.parse_messages(
            payload,
            conversation_title=conversation.title if conversation is not None else "",
            single_chat=conversation.single_chat if conversation is not None else False,
        )

    def read_broadcast_messages(
        self,
        aliases: tuple[str, ...],
        limit: int = 100,
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        local_time_zone = _local_time_zone()
        end_time = datetime.now(tz=local_time_zone)
        start_time = end_time - timedelta(hours=lookback_hours)
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for alias in aliases:
            for message in self.search_messages(
                keyword=alias,
                start=start_time.isoformat(),
                end=end_time.isoformat(),
                limit=limit,
            ):
                if message.open_message_id in seen_message_ids:
                    continue
                if not message.addresses_principal():
                    continue
                seen_message_ids.add(message.open_message_id)
                result.append(message)
        return sorted(result, key=lambda message: message.create_time)

    def read_robot_direct_messages(
        self,
        *,
        lookback_minutes: int = 30,
        limit: int = 100,
        max_pages: int = 3,
    ) -> list[DingTalkMessage]:
        names = chat_bot_names()
        if not names:
            return []
        bot_open_ids_by_name = {
            name: self._bot_open_dingtalk_ids(name) for name in names
        }
        local_time_zone = _local_time_zone()
        end_time = datetime.now(tz=local_time_zone)
        start_time = end_time - timedelta(minutes=lookback_minutes)
        cursor = "0"
        result: list[DingTalkMessage] = []
        seen_message_ids: set[str] = set()
        for _ in range(max_pages):
            payload = self.list_all_messages(
                start=start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end=end_time.strftime("%Y-%m-%d %H:%M:%S"),
                limit=limit,
                cursor=cursor,
            )
            for message in self.parse_messages(
                payload,
                conversation_title="",
                single_chat=False,
            ):
                if message.open_message_id in seen_message_ids:
                    continue
                bot_open_ids = bot_open_ids_by_name.get(message.conversation_title)
                if not bot_open_ids:
                    continue
                if not message.single_chat:
                    continue
                if message.sender_open_dingtalk_id in bot_open_ids:
                    continue
                seen_message_ids.add(message.open_message_id)
                raw_payload = dict(message.raw_payload)
                raw_payload["ceo_agent_source"] = "robot_direct"
                raw_payload["robot_name"] = message.conversation_title
                raw_payload["robot_open_dingtalk_ids"] = sorted(bot_open_ids)
                result.append(message.model_copy(update={"raw_payload": raw_payload}))
            payload_result = payload.get("result", {})
            if not isinstance(payload_result, dict) or not payload_result.get("hasMore"):
                break
            next_cursor = payload_result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return sorted(result, key=lambda message: message.create_time)

    def calendar_invite_from_message(
        self, message: DingTalkMessage
    ) -> DwsCalendarEvent | None:
        if message.raw_payload:
            event = self._find_calendar_event_in_payload(message.raw_payload)
            if event is not None:
                return event
        event_id = self._calendar_event_id_from_message(message)
        if not event_id:
            return None
        return self.get_calendar_event(event_id)

    def list_calendar_events(self, start: str, end: str) -> list[DwsCalendarEvent]:
        payload = self.run_json(self.build_list_calendar_events_command(start, end))
        return self.parse_calendar_events(payload)

    def list_calendar_events_page(
        self,
        *,
        start: str,
        end: str,
        limit: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_list_calendar_events_command(
                start,
                end,
                limit=limit,
                cursor=cursor,
            )
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}
        return {
            "events": self.parse_calendar_events(payload),
            "has_more": result.get("hasMore"),
            "next_cursor": result.get("nextCursor"),
        }

    def get_calendar_event(self, event_id: str) -> DwsCalendarEvent | None:
        try:
            payload = self.run_json(self.build_get_calendar_event_command(event_id))
        except DwsError as exc:
            if self._calendar_event_detail_unavailable(exc):
                return None
            raise
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            return None
        return self._parse_calendar_event(result, require_event_id=True)

    def respond_calendar_event(
        self,
        event_id: str,
        response_status: str,
    ) -> dict:
        return self.run_json(
            self.build_respond_calendar_event_command(event_id, response_status)
        )

    @staticmethod
    def _calendar_event_detail_unavailable(exc: DwsError) -> bool:
        if exc.code not in {"1", "business_error"}:
            return False
        message = str(exc)
        return "calendar event get" in message and "success=false" in message

    def minutes_permission_request_from_message(
        self, message: DingTalkMessage
    ) -> DwsMinutesPermissionRequest | None:
        if not message.raw_payload:
            return None
        return self._find_minutes_permission_request(message.raw_payload)

    def add_minutes_member_permission(
        self, request: DwsMinutesPermissionRequest
    ) -> dict[str, Any]:
        return self.run_json(self.build_add_minutes_member_permission_command(request))

    def execute_oa_approval_action(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_oa_approval_action_command(
                process_instance_id,
                task_id,
                action,
                remark,
            )
        )

    def comment_oa_approval(
        self,
        process_instance_id: str,
        text: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_oa_approval_comment_command(process_instance_id, text)
        )

    def list_pending_oa_approvals(
        self, page: int = 1, size: int = 30
    ) -> list[DwsOaApprovalCandidate]:
        payload = self.run_json(self.build_list_pending_oa_approvals_command(page, size))
        return self.parse_pending_oa_approvals(payload)

    def read_oa_approval_detail(self, process_instance_id: str) -> dict[str, Any]:
        payload = self.run_json(
            self.build_read_oa_approval_detail_command(process_instance_id)
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid OA approval detail response")
        return payload

    def read_oa_approval_records(self, process_instance_id: str) -> dict[str, Any]:
        payload = self.run_json(
            self.build_read_oa_approval_records_command(process_instance_id)
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid OA approval records response")
        return payload

    def read_oa_approval_tasks(self, process_instance_id: str) -> dict[str, Any]:
        payload = self.run_json(
            self.build_read_oa_approval_tasks_command(process_instance_id)
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid OA approval tasks response")
        return payload

    def read_oa_process_instance_openapi(
        self,
        process_instance_id: str,
        *,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        credentials = self._read_dingtalk_skill_credentials(config_path)
        token_payload = self._http_json(
            "GET",
            "https://oapi.dingtalk.com/gettoken?"
            + urlencode(
                {
                    "appkey": credentials["DINGTALK_APP_KEY"],
                    "appsecret": credentials["DINGTALK_APP_SECRET"],
                }
            ),
        )
        token = token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise DwsError("DingTalk OpenAPI token response did not include access_token")
        return self._http_json(
            "POST",
            "https://oapi.dingtalk.com/topapi/processinstance/get?"
            + urlencode({"access_token": token}),
            {
                "process_instance_id": process_instance_id,
            },
        )

    def download_oa_process_attachment(
        self,
        process_instance_id: str,
        file_id: str,
        *,
        config_path: str | None = None,
    ) -> bytes:
        attempts = max(self.transient_retry_attempts, 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                payload = self._dingtalk_api_json(
                    "POST",
                    "/v1.0/workflow/processInstances/spaces/files/urls/download",
                    payload={
                        "processInstanceId": process_instance_id,
                        "fileId": file_id,
                    },
                    config_path=config_path,
                )
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise DwsError(
                        "DingTalk OA attachment response did not include result"
                    )
                download_uri = result.get("downloadUri")
                if not isinstance(download_uri, str) or not download_uri:
                    raise DwsError(
                        "DingTalk OA attachment response did not include downloadUri"
                    )
                with urlopen(
                    download_uri,
                    timeout=max(self.timeout_seconds, 90),
                ) as response:
                    return response.read()
            except (OSError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self.transient_retry_delay_seconds * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise DwsError("DingTalk OA attachment download failed")

    def read_agoal_objective_rule_list(
        self,
        *,
        page_number: int = 1,
        page_size: int = 100,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "GET",
            "/v1.0/agoal/objectiveRuleLists/query",
            params={"pageNumber": page_number, "pageSize": page_size},
            config_path=config_path,
        )

    def read_agoal_org_objective_rule_list(
        self,
        *,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "GET",
            "/v1.0/agoal/objectiveRules/lists",
            config_path=config_path,
        )

    def read_agoal_objective_rule_period_list(
        self,
        objective_rule_id: str,
        *,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "GET",
            "/v1.0/agoal/objectiveRules/periodLists",
            params={"objectiveRuleId": objective_rule_id},
            config_path=config_path,
        )

    def read_agoal_user_objective_list(
        self,
        *,
        ding_user_id: str,
        objective_rule_id: str,
        period_ids: list[str],
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "POST",
            "/v1.0/agoal/users/objectiveLists/query",
            payload={
                "dingUserId": ding_user_id,
                "objectiveRuleId": objective_rule_id,
                "periodIds": period_ids,
            },
            config_path=config_path,
        )

    def read_agoal_objective_detail(
        self,
        objective_id: str,
        *,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "GET",
            "/v1.0/agoal/objectives/details",
            params={"objectiveId": objective_id},
            config_path=config_path,
        )

    def read_agoal_objective_progress_list(
        self,
        objective_id: str,
        *,
        page_number: int = 1,
        page_size: int = 100,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return self._dingtalk_api_json(
            "GET",
            "/v1.0/agoal/objectives/progresses/lists",
            params={
                "objectiveId": objective_id,
                "pageNumber": page_number,
                "pageSize": page_size,
            },
            config_path=config_path,
        )

    def read_doc(self, node: str) -> dict[str, Any]:
        payload = self.run_json(self.build_read_doc_command(node))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc read response")
        return payload

    def list_doc_nodes(
        self,
        workspace_id: str | None = None,
        folder_id: str | None = None,
        page_token: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_doc_list_command(
                workspace_id=workspace_id,
                folder_id=folder_id,
                page_token=page_token,
            )
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid doc list response")
        return payload

    def doc_info(self, node: str) -> dict[str, Any]:
        payload = self.run_json(self.build_doc_info_command(node))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc info response")
        return payload

    def create_markdown_doc(self, name: str, content: str) -> dict[str, Any]:
        payload = self.run_json(self.build_create_markdown_doc_command(name, content))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc create response")
        return payload

    def add_doc_editor_permission(
        self,
        node: str,
        user_ids: list[str],
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_add_doc_editor_permission_command(node, user_ids)
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid doc permission response")
        return payload

    def get_aitable_base(self, base_id: str) -> dict[str, Any]:
        payload = self.run_json(self.build_aitable_base_get_command(base_id))
        if not isinstance(payload, dict):
            raise DwsError("invalid aitable base response")
        return payload

    def get_aitable_tables(
        self, base_id: str, table_ids: list[str] | None = None
    ) -> dict[str, Any]:
        payload = self.run_json(self.build_aitable_table_get_command(base_id, table_ids))
        if not isinstance(payload, dict):
            raise DwsError("invalid aitable table response")
        return payload

    def query_aitable_records(
        self, base_id: str, table_id: str, limit: int = 10
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_aitable_record_query_command(base_id, table_id, limit)
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid aitable record response")
        return payload

    def search_documents(
        self, query: str, page_size: int = 5
    ) -> list[DwsDocumentSearchResult]:
        payload = self.run_json(self.build_search_documents_command(query, page_size))
        return self.parse_document_search_results(payload)

    def download_doc(self, node: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ceo-agent-dws-doc-") as temp_dir:
            output_path = str(Path(temp_dir) / "download")
            payload = self.run_json(self.build_download_doc_command(node, output_path))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc download response")
        return payload

    def list_minutes(
        self,
        *,
        scope: str = "all",
        limit: int = 20,
        cursor: str = "",
        start: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        return self.list_minutes_page(
            scope=scope,
            limit=limit,
            cursor=cursor,
            start=start,
            end=end,
        )["items"]

    def list_minutes_page(
        self,
        *,
        scope: str = "all",
        limit: int = 20,
        cursor: str = "",
        start: str = "",
        end: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_list_minutes_command(
                scope=scope,
                limit=limit,
                cursor=cursor,
                start=start,
                end=end,
            )
        )
        result = payload.get("result")
        pagination = result if isinstance(result, dict) else {}
        return {
            "items": self.parse_minutes_list(payload),
            "has_more": pagination.get("hasMore"),
            "next_token": pagination.get("nextToken"),
        }

    def get_minutes_info(self, task_uuid: str) -> dict[str, Any]:
        payload = self.run_json(self.build_minutes_info_command(task_uuid))
        if not isinstance(payload, dict):
            raise DwsError("invalid minutes info response")
        return payload

    def get_minutes_summary(self, task_uuid: str) -> dict[str, Any]:
        payload = self.run_json(self.build_minutes_summary_command(task_uuid))
        if not isinstance(payload, dict):
            raise DwsError("invalid minutes summary response")
        return payload

    def get_minutes_todos(self, task_uuid: str) -> dict[str, Any]:
        payload = self.run_json(self.build_minutes_todos_command(task_uuid))
        if not isinstance(payload, dict):
            raise DwsError("invalid minutes todos response")
        return payload

    def create_todo_task(
        self,
        *,
        title: str,
        executor_user_id: str,
        due: str,
        priority: int,
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_todo_create_command(
                title=title,
                executor_user_id=executor_user_id,
                due=due,
                priority=priority,
            )
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid todo create response")
        return payload

    def get_todo_task(self, task_id: str) -> dict[str, Any]:
        payload = self.run_json(self.build_todo_get_command(task_id))
        if not isinstance(payload, dict):
            raise DwsError("invalid todo get response")
        return payload

    def mark_todo_task_done(self, task_id: str, *, done: bool = True) -> dict[str, Any]:
        payload = self.run_json(self.build_todo_done_command(task_id, done=done))
        if not isinstance(payload, dict):
            raise DwsError("invalid todo done response")
        return payload

    def get_minutes_transcription(
        self,
        task_uuid: str,
        *,
        next_token: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_minutes_transcription_command(
                task_uuid,
                next_token=next_token,
            )
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid minutes transcription response")
        return payload

    def get_all_minutes_transcription(self, task_uuid: str) -> dict[str, Any]:
        paragraphs: list[dict[str, Any]] = []
        next_token = ""
        seen_tokens: set[str] = set()
        for _ in range(100):
            page = self.get_minutes_transcription(
                task_uuid,
                next_token=next_token,
            )
            next_token = self.parse_minutes_next_token(page)
            has_next = self.parse_minutes_transcription_has_next(page)
            if (has_next is True and not next_token) or (
                has_next is False and bool(next_token)
            ):
                raise DwsError(
                    "minutes transcription pagination response is contradictory"
                )
            paragraphs.extend(self.parse_minutes_transcription_paragraphs(page))
            if has_next is False or (has_next is None and not next_token):
                return {"paragraphs": paragraphs}
            if next_token in seen_tokens:
                raise DwsError(
                    "minutes transcription pagination repeated next token"
                )
            seen_tokens.add(next_token)
        raise DwsError("minutes transcription pagination exceeded 100 pages")

    def create_doc_comment(self, node_id: str, content: str) -> dict[str, Any]:
        payload = self.run_json(self.build_create_doc_comment_command(node_id, content))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc comment response")
        return payload

    def get_resource_download_url(
        self,
        open_conversation_id: str,
        open_message_id: str,
        resource_id: str,
        resource_type: str,
    ) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(
            prefix="ceo-dingtalk-media-",
            delete=False,
        ) as file:
            output_path = Path(file.name)
        command = self.build_get_resource_download_url_command(
            open_conversation_id,
            open_message_id,
            resource_id,
            resource_type,
            output_path,
        )
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 15,
            env=self._cli_environment(),
        )
        if result.returncode != 0:
            download_url = self._download_url_from_mixed_stdout(result.stdout)
            output_path.unlink(missing_ok=True)
            if download_url:
                return {"downloadUrl": download_url}
            code = (
                self._error_code(result.stderr)
                or self._error_code(result.stdout)
                or self._process_error_code(result.returncode)
            )
            raise DwsError(
                self._format_command_error(command, result, code),
                code=code,
                required_scopes=self._pat_required_scopes(
                    result.stderr, result.stdout
                ),
            )
        if output_path.exists() and output_path.stat().st_size > 0:
            try:
                payload = self._json_from_mixed_stdout(result.stdout)
            except DwsError:
                return {"localPath": str(output_path)}
            if not isinstance(payload, dict):
                output_path.unlink(missing_ok=True)
                raise DwsError("invalid resource download response")
            payload["localPath"] = str(output_path)
            return payload
        output_path.unlink(missing_ok=True)
        payload = self._json_from_mixed_stdout(result.stdout)
        if not isinstance(payload, dict):
            raise DwsError("invalid resource download response")
        return payload

    @staticmethod
    def _json_from_mixed_stdout(stdout: str) -> Any:
        text = stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        candidates = extract_all(text, allow_partial=False)
        for candidate in reversed(candidates):
            if text[candidate.end :].strip():
                continue
            try:
                return json.loads(candidate.text)
            except json.JSONDecodeError:
                break
        raise DwsError("dws command returned invalid JSON")

    @staticmethod
    def _download_url_from_mixed_stdout(stdout: str) -> str:
        for line in stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "downloadUrl":
                return value.strip()
        return ""

    def download_robot_message_file(self, download_code: str) -> dict[str, Any]:
        payload = self.run_json(self.build_download_robot_message_file_command(download_code))
        if not isinstance(payload, dict):
            raise DwsError("invalid robot message file download response")
        return payload

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_send_message_command(
                conversation_id,
                text,
                at_users,
                at_open_dingtalk_ids=at_open_dingtalk_ids,
                at_open_dingtalk_names=at_open_dingtalk_names,
                user_id=user_id,
                open_dingtalk_id=open_dingtalk_id,
                title=title,
            )
        )

    def reply_message(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_reply_message_command(
                conversation_id,
                ref_message_id,
                ref_sender_open_dingtalk_id,
                text,
                at_users=at_users,
                at_open_dingtalk_ids=at_open_dingtalk_ids,
                at_open_dingtalk_names=at_open_dingtalk_names,
            )
        )

    def reply_mail(
        self,
        mailbox: str,
        message_id: str,
        subject: str,
        content: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_mail_reply_command(
                mailbox=mailbox,
                message_id=message_id,
                subject=subject,
                content=content,
            )
        )

    def send_reply_to_trigger(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> dict[str, Any]:
        if not trigger.sender_open_dingtalk_id:
            raise DwsError("missing trigger senderOpenDingTalkId for native reply")
        return self.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
            at_users=at_users,
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
        )

    def send_reply_to_trigger_chunks(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
        *,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> dict[str, Any]:
        chunks = split_dingtalk_text(text)
        if not chunks:
            raise DwsError("empty DingTalk reply text")
        results = []
        for index, chunk in enumerate(chunks):
            chunk_at_users = at_users if index == 0 else []
            chunk_at_open_dingtalk_ids = at_open_dingtalk_ids if index == 0 else []
            chunk_at_open_dingtalk_names = at_open_dingtalk_names if index == 0 else []
            results.append(
                self.send_reply_to_trigger(
                    conversation,
                    trigger,
                    chunk,
                    at_users=chunk_at_users,
                    at_open_dingtalk_ids=chunk_at_open_dingtalk_ids,
                    at_open_dingtalk_names=chunk_at_open_dingtalk_names,
                )
            )
        return {
            "chunks": [
                {"index": index, "text": chunk, "send_result": result}
                for index, (chunk, result) in enumerate(zip(chunks, results), start=1)
            ]
        }

    @staticmethod
    def native_reply_delivery_payload(
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        send_result: dict[str, Any] | None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return native_reply_delivery_payload(
            conversation,
            trigger,
            send_result,
            extra=extra,
        )

    def recall_bot_message(
        self, conversation_id: str | None, process_query_key: str
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_recall_bot_message_command(conversation_id, process_query_key)
        )

    def recall_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        return self.run_json(
            self.build_recall_message_command(conversation_id, message_id)
        )

    def query_message_send_status(self, open_task_id: str) -> dict[str, Any]:
        return self.run_json(
            self.build_query_message_send_status_command(open_task_id)
        )

    def add_message_emoji(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_add_message_emoji_command(conversation_id, message_id, emoji)
        )

    def add_message_text_emotion(
        self,
        conversation_id: str,
        message_id: str,
        *,
        text: str,
        emotion_id: str,
        emotion_name: str,
        background_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_add_message_text_emotion_command(
                conversation_id,
                message_id,
                text=text,
                emotion_id=emotion_id,
                emotion_name=emotion_name,
                background_id=background_id,
            )
        )

    def create_message_text_emotion(
        self,
        *,
        text: str,
        emotion_name: str,
        background_id: str = "",
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_create_message_text_emotion_command(
                text=text,
                emotion_name=emotion_name,
                background_id=background_id,
            )
        )

    @staticmethod
    def extract_recall_key(send_result: dict[str, Any] | None) -> str:
        return extract_recall_key_from_send_result(send_result)

    def ding_user(self, user_id: str, text: str) -> None:
        self.run_json(self.build_ding_self_command(user_id, text))

    def ding_self(self, text: str) -> None:
        receiver_user_id = self.ding_receiver_user_id or self.get_current_user_id()
        self.ding_user(receiver_user_id, text)

    def send_direct_message_by_bot(self, user_id: str, text: str) -> dict[str, Any]:
        return self.run_json(
            self.build_send_message_by_bot_command(
                text=text,
                user_ids=[user_id],
                title="回复",
            )
        )

    def send_group_message_by_bot(
        self,
        conversation_id: str,
        text: str,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_send_message_by_bot_command(
                text=text,
                conversation_id=conversation_id,
                title="回复",
            )
        )

    def build_search_bots_command(self, name: str) -> list[str]:
        return [
            self.dws_bin,
            "chat",
            "bot",
            "search",
            "--name",
            name,
            "--format",
            "json",
        ]

    def build_find_bots_command(
        self,
        query: str,
        *,
        limit: int = 20,
        cursor: str = "",
    ) -> list[str]:
        command = [
            self.dws_bin,
            "chat",
            "bot",
            "find",
            "--query",
            query,
            "--limit",
            str(limit),
        ]
        if cursor:
            command.extend(["--cursor", cursor])
        command.extend(["--format", "json"])
        return command

    def _bot_open_dingtalk_ids(self, name: str) -> set[str]:
        cached = self._bot_open_dingtalk_ids_by_name.get(name)
        if cached is not None:
            return cached
        payload = self.run_json(self.build_find_bots_command(name))
        bot_list = payload.get("result", {}).get("bots", [])
        if not isinstance(bot_list, list):
            raise DwsError("invalid bot find response: missing bots")
        matches = {
            item["botOpenDingTalkId"]
            for item in bot_list
            if isinstance(item, dict)
            and item.get("name") == name
            and isinstance(item.get("botOpenDingTalkId"), str)
            and item.get("botOpenDingTalkId")
        }
        if not matches:
            raise DwsError(f"DingTalk robot named {name!r} has no botOpenDingTalkId")
        self._bot_open_dingtalk_ids_by_name[name] = matches
        return matches

    def _ding_robot_code(self) -> str | None:
        if self.ding_robot_code:
            return self.ding_robot_code
        if not self.ding_robot_name:
            return None
        payload = self.run_json(self.build_search_bots_command(self.ding_robot_name))
        robot_list = payload.get("robotList")
        if not isinstance(robot_list, list):
            raise DwsError("invalid bot search response: missing robotList")
        matches = [
            item
            for item in robot_list
            if isinstance(item, dict) and item.get("robotName") == self.ding_robot_name
        ]
        if len(matches) != 1:
            raise DwsError(
                f"expected one DingTalk robot named {self.ding_robot_name!r}, got {len(matches)}"
            )
        robot_code = matches[0].get("robotCode")
        if not isinstance(robot_code, str) or not robot_code:
            raise DwsError(
                f"DingTalk robot named {self.ding_robot_name!r} has no robotCode"
            )
        self.ding_robot_code = robot_code
        return robot_code

    def get_current_user_id(self) -> str:
        payload = self.run_json(self.build_get_current_user_command())
        profiles = self.parse_user_profiles(payload)
        if len(profiles) != 1:
            raise DwsError(f"expected one current user profile, got {len(profiles)}")
        return profiles[0].user_id

    def get_user_profiles(self, user_ids: list[str]) -> list[DwsUserProfile]:
        if not user_ids:
            return []
        payload = self.run_json(self.build_get_user_profiles_command(user_ids))
        return [
            self._enrich_user_profile_from_search(profile)
            for profile in self.parse_user_profiles(payload)
        ]

    def get_user_profile(self, user_id: str) -> DwsUserProfile:
        profiles = self.get_user_profiles([user_id])
        matches = [profile for profile in profiles if profile.user_id == user_id]
        if len(matches) != 1:
            raise DwsError(f"expected one user profile for {user_id}, got {len(matches)}")
        return matches[0]

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
        payload = self.run_json(self.build_search_user_command(query))
        return self.parse_user_profiles(payload)

    def _with_single_chat_direct_target(
        self, conversation: DingTalkConversation
    ) -> DingTalkConversation:
        if not conversation.single_chat:
            return conversation
        if conversation.direct_user_id or conversation.direct_open_dingtalk_id:
            return conversation
        matches = self.search_user_profiles(conversation.title)
        exact_matches = [
            match
            for match in matches
            if self._matches_direct_chat_title(match, conversation.title)
        ]
        if len(exact_matches) == 1:
            matches = exact_matches
        if len(matches) != 1:
            raise DwsError(
                f"expected one direct chat user for {conversation.title!r}, got {len(matches)}",
                code=DwsError.DIRECT_CHAT_TARGET_NOT_FOUND_CODE,
            )
        match = matches[0]
        return conversation.model_copy(
            update={
                "direct_user_id": match.user_id,
                "direct_open_dingtalk_id": match.open_dingtalk_id or "",
            }
        )

    @staticmethod
    def _matches_direct_chat_title(profile: DwsUserProfile, title: str) -> bool:
        normalized_title = title.strip().casefold()
        if not normalized_title:
            return False
        return any(
            candidate.strip().casefold() == normalized_title
            for candidate in (profile.name, profile.nick)
            if candidate.strip()
        )

    def _enrich_user_profile_from_search(
        self, profile: DwsUserProfile
    ) -> DwsUserProfile:
        if profile.title or not profile.name:
            return profile
        try:
            matches = self.search_user_profiles(profile.name)
        except DwsError as exc:
            if exc.needs_authorization:
                return profile
            raise
        search_matches = [
            item
            for item in matches
            if item.user_id == profile.user_id
        ]
        if len(search_matches) != 1:
            return profile
        search_profile = search_matches[0]
        return profile.model_copy(
            update={
                "title": search_profile.title or profile.title,
                "open_dingtalk_id": profile.open_dingtalk_id
                or search_profile.open_dingtalk_id,
            }
        )

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        if message.sender_user_id:
            return message.sender_user_id
        profiles = self.search_user_profiles(message.sender_name)
        if message.sender_open_dingtalk_id:
            matches = [
                profile
                for profile in profiles
                if profile.open_dingtalk_id == message.sender_open_dingtalk_id
            ]
        else:
            matches = [profile for profile in profiles if profile.name == message.sender_name]
        if len(matches) != 1:
            raise DwsError(
                f"could not resolve unique DingTalk sender for {message.sender_name}"
            )
        return matches[0].user_id

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        if message.sender_user_id:
            return message.sender_user_id == self.get_current_user_id()
        if not message.sender_open_dingtalk_id:
            return False
        return self.resolve_message_sender(message) == self.get_current_user_id()

    def get_user_department_ids(self, user_id: str) -> set[str]:
        department_ids = self.get_user_profile(user_id).department_ids
        if not department_ids:
            raise DwsError(f"department data is missing for user {user_id}")
        return department_ids

    def user_in_manager_chain(
        self, manager_user_id: str, subject_user_id: str, max_depth: int = 20
    ) -> bool:
        current_user_id = subject_user_id
        visited: set[str] = set()
        for _ in range(max_depth):
            if current_user_id in visited:
                raise DwsError("manager chain contains a cycle")
            visited.add(current_user_id)
            profile = self.get_user_profile(current_user_id)
            if not profile.manager_user_id:
                raise DwsError(f"user {current_user_id} has no manager chain field")
            if profile.manager_user_id == manager_user_id:
                return True
            current_user_id = profile.manager_user_id
        raise DwsError("manager chain exceeded max depth")

    def is_hr_user(self, user_id: str) -> bool:
        profile = self.get_user_profile(user_id)
        hr_department_ids = self.search_department_ids("人力资源")
        if profile.department_ids & hr_department_ids:
            return True
        if not hr_department_ids:
            raise DwsError("HR membership source is not configured")
        payload = self.run_json(
            self.build_list_department_members_command(sorted(hr_department_ids))
        )
        member_profiles = self.parse_department_member_profiles(payload)
        return any(member.user_id == user_id for member in member_profiles)

    def list_department_member_profiles(
        self, department_ids: list[str]
    ) -> list[DwsUserProfile]:
        payload = self.run_json(self.build_list_department_members_command(department_ids))
        return self.parse_department_member_profiles(payload)

    def search_department_ids(self, query: str) -> set[str]:
        payload = self.run_json(self.build_search_department_command(query))
        return self.parse_department_ids(payload)

    def run_json(
        self,
        command: list[str],
        *,
        timeout_seconds: int | None = None,
    ) -> Any:
        command_timeout_seconds = timeout_seconds or self.timeout_seconds
        remaining_retries = self.transient_retry_attempts
        attempt_index = 0
        while True:
            try:
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=command_timeout_seconds,
                    env=self._cli_environment(),
                )
            except subprocess.TimeoutExpired as exc:
                if remaining_retries > 0:
                    self._sleep_before_retry(attempt_index)
                    attempt_index += 1
                    remaining_retries -= 1
                    continue
                raise DwsError(
                    f"dws command timed out after {command_timeout_seconds} seconds"
                ) from exc
            if result.returncode == 0:
                break
            code = (
                self._error_code(result.stderr)
                or self._error_code(result.stdout)
                or self._process_error_code(result.returncode)
            )
            if self._is_retryable_error(command, code) and remaining_retries > 0:
                if code in self.DISCOVERY_CACHE_REFRESH_CODES:
                    self._refresh_cache()
                self._sleep_before_retry(attempt_index)
                attempt_index += 1
                remaining_retries -= 1
                continue
            raise DwsError(
                self._format_command_error(command, result, code),
                code=code,
                required_scopes=self._pat_required_scopes(
                    result.stderr, result.stdout
                ),
            )
        return self._json_from_mixed_stdout(result.stdout)

    def run_text(
        self,
        command: list[str],
        *,
        timeout_seconds: int | None = None,
    ) -> str:
        command_timeout_seconds = timeout_seconds or self.timeout_seconds
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=command_timeout_seconds,
                env=self._cli_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise DwsError(
                f"dws command timed out after {command_timeout_seconds} seconds"
            ) from exc
        if result.returncode != 0:
            code = (
                self._error_code(result.stderr)
                or self._error_code(result.stdout)
                or self._process_error_code(result.returncode)
            )
            raise DwsError(
                self._format_command_error(command, result, code),
                code=code,
                required_scopes=self._pat_required_scopes(
                    result.stderr, result.stdout
                ),
            )
        return result.stdout.strip()

    @classmethod
    def _cli_environment(cls) -> dict[str, str]:
        env = dws_noninteractive_environment()
        for key in cls.CLI_AUTH_ENV_KEYS:
            env.pop(key, None)
        return env

    @classmethod
    def _pat_authorization_environment(cls) -> dict[str, str]:
        env = dict(os.environ)
        env.pop(DWS_AGENT_CODE_ENV, None)
        env.pop("CEO_DWS_AGENT_CODE", None)
        for key in cls.CLI_AUTH_ENV_KEYS:
            env.pop(key, None)
        return env

    def _sleep_before_retry(self, attempt_index: int) -> None:
        if self.transient_retry_delay_seconds <= 0:
            return
        time.sleep(self.transient_retry_delay_seconds * (attempt_index + 1))

    @staticmethod
    def _unique_non_empty(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @classmethod
    def _is_retryable_error(cls, command: list[str], code: str | None) -> bool:
        if code in cls.RETRYABLE_ERROR_CODES:
            return True
        if (
            code in cls.MESSAGE_LIST_RETRYABLE_ERROR_CODES
            and len(command) >= 4
            and command[1:4] == ["chat", "message", "list"]
        ):
            return True
        if code in cls.TOKEN_VERIFIED_RETRYABLE_ERROR_CODES:
            command_path = tuple(command[1:])
            return any(
                command_path[: len(retryable_path)] == retryable_path
                for retryable_path in cls.TOKEN_VERIFIED_RETRYABLE_READ_COMMANDS
            )
        return (
            code in cls.DOC_READ_RETRYABLE_ERROR_CODES
            and len(command) >= 3
            and command[1:3] == ["doc", "read"]
        )

    def _refresh_cache(self) -> None:
        try:
            subprocess.run(
                [self.dws_bin, "cache", "refresh", "--format", "json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._cli_environment(),
            )
        except subprocess.TimeoutExpired:
            return

    @classmethod
    def _format_command_error(
        cls,
        command: list[str],
        result: subprocess.CompletedProcess[str],
        code: str | None,
    ) -> str:
        parts = [
            f"dws command failed with exit code {result.returncode}",
            f"command={cls._sanitize_command(command)}",
        ]
        if code:
            parts.append(f"code={code}")
        stderr = cls._safe_output_preview(result.stderr)
        stdout = cls._safe_output_preview(result.stdout)
        if stderr:
            parts.append(f"stderr={stderr}")
        if stdout:
            parts.append(f"stdout={stdout}")
        return "; ".join(parts)

    @classmethod
    def _sanitize_command(cls, command: list[str]) -> str:
        sanitized: list[str] = []
        redact_next = False
        for token in command:
            if redact_next:
                sanitized.append("<redacted>")
                redact_next = False
                continue
            sanitized.append(token)
            if token in cls.SENSITIVE_COMMAND_FLAGS:
                redact_next = True
        return " ".join(sanitized)

    @staticmethod
    def _preview(value: str, limit: int = 400) -> str:
        compact = " ".join(value.strip().split())
        if len(compact) <= limit:
            return compact
        return f"{compact[:limit]}..."

    @classmethod
    def _safe_output_preview(cls, value: str) -> str:
        compact = value.strip()
        if not compact:
            return ""
        try:
            payload = json.loads(compact)
        except json.JSONDecodeError:
            return cls._preview(compact)
        if not isinstance(payload, dict):
            return cls._preview(compact)
        safe_fields: dict[str, Any] = {}
        for key in ("code", "message", "reason", "server_error_code"):
            field_value = payload.get(key)
            if isinstance(field_value, (str, int)):
                safe_fields[key] = field_value
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("code", "message", "reason", "server_error_code"):
                field_value = error.get(key)
                if isinstance(field_value, (str, int)):
                    safe_fields[f"error.{key}"] = field_value
        if not safe_fields:
            return "<structured error>"
        return cls._preview(json.dumps(safe_fields, ensure_ascii=False))

    @staticmethod
    def _message_title(text: str) -> str:
        source = DwsClient._message_title_source(text)
        matches = list(TITLE_WORD_OR_CJK_PATTERN.finditer(source))
        if len(matches) <= TITLE_INFORMATION_UNIT_LIMIT:
            return source or "回复"
        end_index = matches[TITLE_INFORMATION_UNIT_LIMIT - 1].end()
        return f"{source[:end_index].rstrip()}..."

    @staticmethod
    def _literal_cli_value(value: str, *, is_title: bool = False) -> str:
        if value.startswith("@"):
            prefix = TITLE_AT_FILE_ESCAPE_PREFIX if is_title else TEXT_AT_FILE_ESCAPE_PREFIX
            return f"{prefix}{value}"
        return value

    @staticmethod
    def _with_visible_at_mentions(text: str, names: list[str]) -> str:
        mention_names = []
        for name in names:
            clean_name = name.strip()
            if clean_name.startswith("@"):
                clean_name = clean_name[1:].strip()
            if clean_name and f"@{clean_name}" not in text:
                mention_names.append(clean_name)
        if not mention_names:
            return text
        mention_text = " ".join(f"@{name}" for name in mention_names)
        if text.lstrip().startswith(mention_text):
            return text
        return f" {mention_text} {text}"

    @staticmethod
    def _message_title_source(text: str) -> str:
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped and not stripped.startswith(">"):
                break
            index += 1
        source = " ".join(line.strip() for line in lines[index:] if line.strip())
        source = " ".join(source.split())
        while source.startswith("<@"):
            placeholder_end = source.find(">")
            if placeholder_end < 0:
                break
            source = source[placeholder_end + 1 :].lstrip()
        return source or "回复"

    @staticmethod
    def _error_code(stderr: str) -> str | None:
        try:
            payload = json.loads(stderr)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        dotted_error_code = payload.get("error.code")
        if isinstance(dotted_error_code, str) and dotted_error_code:
            return dotted_error_code
        if isinstance(dotted_error_code, int):
            return str(dotted_error_code)
        dotted_error_reason = payload.get("error.reason")
        if isinstance(dotted_error_reason, str) and dotted_error_reason:
            return dotted_error_reason
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
        error = payload.get("error")
        if isinstance(error, dict):
            server_error_code = error.get("server_error_code")
            if isinstance(server_error_code, str) and server_error_code:
                return server_error_code
            nested_code = error.get("code")
            if isinstance(nested_code, str) and nested_code:
                return nested_code
            if isinstance(nested_code, int):
                return str(nested_code)
        return None

    @classmethod
    def _pat_required_scopes(cls, *outputs: str) -> list[str]:
        scopes: list[str] = []
        for output in outputs:
            if not output.strip():
                continue
            try:
                payload = json.loads(output)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            cls._collect_pat_required_scopes(payload, scopes)
        return cls._unique_non_empty(scopes)

    @classmethod
    def _collect_pat_required_scopes(cls, value: object, scopes: list[str]) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_pat_required_scopes(item, scopes)
            return
        if not isinstance(value, dict):
            return
        scope = value.get("scope")
        if isinstance(scope, str) and scope.strip():
            scopes.append(scope)
        for key in ("requiredScopes", "required_scopes"):
            required = value.get(key)
            if isinstance(required, list):
                cls._collect_pat_required_scopes(required, scopes)
        for key in ("data", "error", "result"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                cls._collect_pat_required_scopes(nested, scopes)

    @classmethod
    def _process_error_code(cls, returncode: int) -> str | None:
        code = str(returncode)
        if code == "2" or code in cls.RETRYABLE_ERROR_CODES:
            return code
        return None

    @staticmethod
    def _message_list_time(last_message_create_at: int | None) -> str:
        if last_message_create_at is None:
            return datetime.now(tz=DINGTALK_MESSAGE_TIME_ZONE).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return (
            datetime.fromtimestamp(
                last_message_create_at / 1000,
                tz=DINGTALK_MESSAGE_TIME_ZONE,
            )
            + timedelta(seconds=1)
        ).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def parse_unread_conversations(payload: dict[str, Any]) -> list[DingTalkConversation]:
        conversations = payload.get("result", {}).get("conversations", [])
        return [
            DingTalkConversation(
                open_conversation_id=conversation["openConversationId"],
                title=conversation["title"],
                single_chat=conversation["singleChat"],
                unread_point=conversation["unreadPoint"],
                notification_off=bool(conversation.get("notificationOff", False)),
                last_message_create_at=conversation.get("lastMsgCreateAt"),
                direct_user_id=str(
                    conversation.get("userId")
                    or conversation.get("userid")
                    or conversation.get("directUserId")
                    or ""
                ),
                direct_open_dingtalk_id=str(
                    conversation.get("openDingTalkId")
                    or conversation.get("directOpenDingTalkId")
                    or ""
                ),
            )
            for conversation in conversations
        ]

    @staticmethod
    def parse_search_conversations(payload: dict[str, Any]) -> list[DingTalkConversation]:
        conversations = payload.get("result", {}).get("value", [])
        if not isinstance(conversations, list):
            return []
        return [
            DingTalkConversation(
                open_conversation_id=conversation["openConversationId"],
                title=conversation["title"],
                single_chat=False,
                unread_point=0,
                last_message_create_at=None,
            )
            for conversation in conversations
            if isinstance(conversation, dict)
            and conversation.get("openConversationId")
            and conversation.get("title")
        ]

    @staticmethod
    def parse_client_conversation_id(
        payload: dict[str, Any],
        open_conversation_id: str,
    ) -> str:
        info = payload.get("result", {}).get("conversationInfo", {})
        if not isinstance(info, dict):
            return ""
        for key in ("clientCid", "cid", "conversationId"):
            value = info.get(key)
            if value is not None and str(value) != open_conversation_id:
                return str(value)
        return ""

    @staticmethod
    def parse_document_search_results(
        payload: dict[str, Any]
    ) -> list[DwsDocumentSearchResult]:
        documents = payload.get("documents") or payload.get("result", {}).get("documents", [])
        if not isinstance(documents, list):
            return []
        results: list[DwsDocumentSearchResult] = []
        for item in documents:
            if not isinstance(item, dict):
                continue
            node_id = item.get("nodeId") or item.get("dentryUuid") or item.get("fileId")
            if not node_id:
                continue
            results.append(
                DwsDocumentSearchResult(
                    node_id=str(node_id),
                    name=str(item.get("name") or item.get("title") or ""),
                    extension=str(item.get("extension") or ""),
                    content_type=str(item.get("contentType") or ""),
                    node_type=str(item.get("nodeType") or ""),
                    doc_url=str(item.get("docUrl") or item.get("url") or ""),
                )
            )
        return results

    @staticmethod
    def parse_minutes_list(payload: Any) -> list[dict[str, Any]]:
        rows = DwsClient._unwrap_minutes_list_rows(payload)
        results: list[dict[str, Any]] = []
        for item in rows:
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if text.startswith("{"):
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    item = parsed
                else:
                    results.append({"taskUuid": text, "title": text})
                    continue
            if not isinstance(item, dict):
                continue
            task_uuid = (
                item.get("taskUuid")
                or item.get("minutesId")
                or item.get("id")
                or item.get("task_uuid")
                or item.get("uuid")
            )
            if not task_uuid:
                continue
            row = dict(item)
            row["taskUuid"] = str(task_uuid)
            if "title" not in row and row.get("name"):
                row["title"] = str(row["name"])
            results.append(row)
        return results

    @staticmethod
    def parse_minutes_has_more(payload: Any) -> bool:
        result = payload.get("result") if isinstance(payload, dict) else None
        candidates = [payload, result]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = candidate.get("hasMore")
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() == "true"
        return False

    @staticmethod
    def parse_minutes_next_token(payload: Any) -> str:
        result = payload.get("result") if isinstance(payload, dict) else None
        data = payload.get("data") if isinstance(payload, dict) else None
        result_data = result.get("data") if isinstance(result, dict) else None
        candidates = [payload, result, data, result_data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = candidate.get("nextToken") or candidate.get("next_token")
            if value:
                return str(value)
        return ""

    @staticmethod
    def parse_minutes_transcription_has_next(payload: Any) -> bool | None:
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        data = payload.get("data")
        result_data = result.get("data") if isinstance(result, dict) else None
        values: list[bool] = []
        for candidate in (payload, result, data, result_data):
            if not isinstance(candidate, dict) or "hasNext" not in candidate:
                continue
            value = candidate["hasNext"]
            if isinstance(value, bool):
                values.append(value)
            elif isinstance(value, str) and value.casefold() in {"true", "false"}:
                values.append(value.casefold() == "true")
            else:
                raise DwsError(
                    "invalid minutes transcription pagination hasNext value"
                )
        if not values:
            return None
        if any(value != values[0] for value in values[1:]):
            raise DwsError(
                "minutes transcription pagination response is contradictory"
            )
        return values[0]

    @staticmethod
    def parse_minutes_transcription_paragraphs(
        payload: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        data = payload.get("data")
        result_data = result.get("data") if isinstance(result, dict) else None
        for candidate in (payload, result, data, result_data):
            if not isinstance(candidate, dict):
                continue
            paragraphs = candidate.get("paragraphList")
            if not isinstance(paragraphs, list):
                paragraphs = candidate.get("paragraphs")
            if isinstance(paragraphs, list):
                return [item for item in paragraphs if isinstance(item, dict)]
        return []

    @staticmethod
    def _unwrap_minutes_list_rows(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        candidates: list[Any] = [payload]
        for key in ("result", "data", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                candidates.append(value)

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("items", "itemList", "list", "records", "minutes", "value"):
                value = candidate.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def parse_messages(
        payload: dict[str, Any], conversation_title: str, single_chat: bool
    ) -> list[DingTalkMessage]:
        result = payload.get("result", {})
        messages = result.get("messages", [])
        if not messages and isinstance(result.get("conversationMessagesList"), list):
            parsed_messages = []
            for conversation_payload in result["conversationMessagesList"]:
                if not isinstance(conversation_payload, dict):
                    continue
                conversation_messages = conversation_payload.get("messages", [])
                if not isinstance(conversation_messages, list):
                    continue
                payload_title = str(
                    conversation_payload.get("title") or conversation_title
                )
                payload_single_chat = bool(
                    conversation_payload.get("singleChat", single_chat)
                )
                for message in conversation_messages:
                    parsed_message = DwsClient._parse_message_or_none(
                        message,
                        conversation_title=payload_title,
                        single_chat=payload_single_chat,
                    )
                    if parsed_message is not None:
                        parsed_messages.append(parsed_message)
            return parsed_messages
        parsed_messages = []
        for message in messages:
            parsed_message = DwsClient._parse_message_or_none(
                message,
                conversation_title=conversation_title,
                single_chat=single_chat,
            )
            if parsed_message is not None:
                parsed_messages.append(parsed_message)
        return parsed_messages

    @staticmethod
    def _parse_message_or_none(
        message: Any, conversation_title: str, single_chat: bool
    ) -> DingTalkMessage | None:
        if not isinstance(message, dict):
            return None
        required_keys = (
            "openConversationId",
            "openMessageId",
            "sender",
            "createTime",
            "content",
        )
        if any(key not in message for key in required_keys):
            return None
        return DwsClient._parse_message(
            message,
            conversation_title=conversation_title,
            single_chat=single_chat,
        )

    @staticmethod
    def _parse_message(
        message: dict[str, Any], conversation_title: str, single_chat: bool
    ) -> DingTalkMessage:
        quoted_message = message.get("quotedMessage") or {}
        return DingTalkMessage(
            open_conversation_id=message["openConversationId"],
            open_message_id=message["openMessageId"],
            conversation_title=conversation_title,
            single_chat=single_chat,
            sender_name=message["sender"],
            sender_open_dingtalk_id=message.get("senderOpenDingTalkId"),
            sender_user_id=message.get("senderUserId"),
            message_type=DwsClient._message_type(message),
            create_time=message["createTime"],
            content=message["content"],
            mentioned_user_ids=DwsClient._mentioned_user_ids(message),
            quoted_message_id=quoted_message.get("openMessageId"),
            quoted_content=quoted_message.get("content"),
            raw_payload=message,
        )

    @staticmethod
    def parse_calendar_events(payload: dict[str, Any]) -> list[DwsCalendarEvent]:
        records = DwsClient._calendar_event_records(payload)
        events: list[DwsCalendarEvent] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            event = DwsClient._parse_calendar_event(record)
            if event is not None:
                events.append(event)
        return events

    @staticmethod
    def _calendar_event_records(payload: dict[str, Any]) -> list[Any]:
        result = payload.get("result", payload)
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return []
        for key in (
            "events",
            "items",
            "calendarEvents",
            "eventList",
            "list",
            "data",
        ):
            value = result.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _find_calendar_event_in_payload(payload: Any) -> DwsCalendarEvent | None:
        if isinstance(payload, dict):
            event = DwsClient._parse_calendar_event(payload, require_event_id=True)
            if event is not None:
                return event
            for key in (
                "calendarEvent",
                "calendar",
                "event",
                "schedule",
                "meeting",
                "content",
                "rawContent",
            ):
                value = payload.get(key)
                event = (
                    DwsClient._parse_calendar_event(value)
                    if isinstance(value, dict)
                    else None
                )
                if event is None:
                    event = DwsClient._find_calendar_event_in_payload(value)
                if event is not None:
                    return event
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    event = DwsClient._find_calendar_event_in_payload(value)
                    if event is not None:
                        return event
        elif isinstance(payload, list):
            for value in payload:
                event = DwsClient._find_calendar_event_in_payload(value)
                if event is not None:
                    return event
        return None

    @staticmethod
    def _calendar_event_id_from_message(message: DingTalkMessage) -> str:
        for value in DwsClient._string_values(
            {
                "content": message.content,
                "raw_payload": message.raw_payload,
            }
        ):
            event_id = DwsClient._calendar_event_id_from_text(value)
            if event_id:
                return event_id
        return ""

    @staticmethod
    def _string_values(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            result: list[str] = []
            for child in value.values():
                result.extend(DwsClient._string_values(child))
            return result
        if isinstance(value, list):
            result = []
            for child in value:
                result.extend(DwsClient._string_values(child))
            return result
        return []

    @staticmethod
    def _calendar_event_id_from_text(value: str) -> str:
        texts = [value]
        for _ in range(3):
            decoded = unquote(texts[-1])
            if decoded == texts[-1]:
                break
            texts.append(decoded)
        for text in texts:
            for event_id in DwsClient._calendar_event_ids_from_query_text(text):
                return event_id
        return ""

    @staticmethod
    def _calendar_event_ids_from_query_text(text: str) -> list[str]:
        result: list[str] = []
        keys = ("uniqueId", "eventId", "calendarEventId")
        query_values = [text]
        if "?" in text:
            query_values.append(text.split("?", 1)[1])
        for query in query_values:
            parsed = parse_qs(query, keep_blank_values=False)
            for key in keys:
                for value in parsed.get(key, []):
                    if value.strip():
                        result.append(value.strip())
            for key in keys:
                token = f"{key}="
                start = query.find(token)
                if start < 0:
                    continue
                start += len(token)
                end = len(query)
                for separator in ("&", ")", "]", " ", "\n", "\t"):
                    index = query.find(separator, start)
                    if index >= 0:
                        end = min(end, index)
                value = query[start:end].strip()
                if value:
                    result.append(value)
        return result

    @staticmethod
    def _parse_calendar_event(
        record: dict[str, Any],
        *,
        require_event_id: bool = False,
    ) -> DwsCalendarEvent | None:
        event_id = DwsClient._first_string(
            record,
            "eventId",
            "eventID",
            "calendarEventId",
            "scheduleId",
            "id",
            "event_id",
        )
        if require_event_id and not event_id:
            return None
        start_time = DwsClient._calendar_time(record, "start")
        end_time = DwsClient._calendar_time(record, "end")
        if not start_time or not end_time:
            return None
        return DwsCalendarEvent(
            event_id=event_id,
            title=DwsClient._first_string(
                record,
                "summary",
                "title",
                "subject",
                "name",
            ),
            start_time=start_time,
            end_time=end_time,
            description=DwsClient._first_string(
                record,
                "description",
                "richTextDescription",
                "body",
                "content",
                "remark",
            ),
            organizer=DwsClient._calendar_person(record.get("organizer")),
            response_status=DwsClient._first_string(
                record,
                "responseStatus",
                "status",
            ),
            self_response_status=(
                DwsClient._first_string(
                    record,
                    "selfResponseStatus",
                    "self_response_status",
                    "selfStatus",
                )
                or DwsClient._calendar_self_response_status(record.get("attendees"))
            ),
            attendees=DwsClient._calendar_attendees(record.get("attendees")),
            attendee_details=DwsClient._calendar_attendee_details(
                record.get("attendees")
            ),
            comments=DwsClient._calendar_comments(record),
            status=DwsClient._first_string(record, "status"),
            created_ms=DwsClient._first_int(record, "created", "createTime"),
            updated_ms=DwsClient._first_int(record, "updated", "updateTime"),
        )

    @staticmethod
    def _calendar_time(record: dict[str, Any], prefix: str) -> str:
        value = record.get(prefix)
        if isinstance(value, dict):
            nested = DwsClient._first_string(
                value,
                "dateTime",
                "date",
                "time",
                "value",
            )
            if nested:
                return nested
        if isinstance(value, str) and value.strip():
            return value.strip()
        return DwsClient._first_string(
            record,
            f"{prefix}Time",
            f"{prefix}DateTime",
            f"{prefix}_time",
            f"{prefix}_date_time",
        )

    @staticmethod
    def _calendar_person(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return DwsClient._first_string(
                value,
                "displayName",
                "name",
                "email",
                "userId",
                "id",
            )
        return ""

    @staticmethod
    def _calendar_attendees(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            person = DwsClient._calendar_person(item)
            if person:
                result.append(person)
        return result

    @staticmethod
    def _calendar_attendee_details(value: Any) -> list[DwsCalendarAttendee]:
        if not isinstance(value, list):
            return []
        result: list[DwsCalendarAttendee] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    result.append(DwsCalendarAttendee(display_name=item.strip()))
                continue
            if not isinstance(item, dict):
                continue
            is_self = item.get("self", item.get("isSelf", False))
            if isinstance(is_self, str):
                is_self = is_self.casefold() == "true"
            result.append(
                DwsCalendarAttendee(
                    display_name=DwsClient._calendar_person(item),
                    is_self=bool(is_self),
                    response_status=DwsClient._first_string(
                        item, "responseStatus", "response_status", "status"
                    ),
                    user_id=DwsClient._first_string(
                        item, "userId", "user_id", "staffId"
                    ),
                    open_dingtalk_id=DwsClient._first_string(
                        item,
                        "openDingTalkId",
                        "openDingtalkId",
                        "openId",
                        "open_id",
                    ),
                )
            )
        return result

    @staticmethod
    def _calendar_comments(record: dict[str, Any]) -> list[str]:
        result: list[str] = []
        for key in (
            "comments",
            "commentList",
            "calendarComments",
            "dingComments",
        ):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                result.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        result.append(item.strip())
                    elif isinstance(item, dict):
                        text = DwsClient._first_string(
                            item,
                            "content",
                            "text",
                            "comment",
                            "message",
                            "remark",
                        )
                        if text:
                            author = DwsClient._calendar_person(
                                item.get("creator")
                                or item.get("author")
                                or item.get("sender")
                            )
                            result.append(f"{author}: {text}" if author else text)
        return result

    @staticmethod
    def _calendar_self_response_status(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        for item in value:
            if not isinstance(item, dict) or item.get("self") is not True:
                continue
            return DwsClient._first_string(item, "responseStatus", "status")
        return ""

    @staticmethod
    def _first_string(record: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _first_int(record: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = record.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            if isinstance(value, str):
                timestamp_ms = DwsClient._datetime_string_to_epoch_ms(value)
                if timestamp_ms > 0:
                    return timestamp_ms
        return 0

    @staticmethod
    def _datetime_string_to_epoch_ms(value: str) -> int:
        text = value.strip()
        if not text:
            return 0
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
        return int(parsed.timestamp() * 1000)

    @staticmethod
    def _find_minutes_permission_request(
        payload: Any,
    ) -> DwsMinutesPermissionRequest | None:
        if isinstance(payload, dict):
            request = DwsClient._parse_minutes_permission_request(payload)
            if request is not None:
                return request
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    request = DwsClient._find_minutes_permission_request(value)
                    if request is not None:
                        return request
        elif isinstance(payload, list):
            for value in payload:
                request = DwsClient._find_minutes_permission_request(value)
                if request is not None:
                    return request
        return None

    @staticmethod
    def _parse_minutes_permission_request(
        record: dict[str, Any],
    ) -> DwsMinutesPermissionRequest | None:
        uuids = DwsClient._string_list(
            record.get("uuids")
            or record.get("minutesUuids")
            or record.get("taskUuids")
            or record.get("minutesIds")
        )
        if not uuids:
            uuid = DwsClient._first_string(
                record,
                "uuid",
                "minutesUuid",
                "taskUuid",
                "minutesId",
            )
            if uuid:
                uuids = [uuid]
        member_uids = DwsClient._int_list(
            record.get("memberUids")
            or record.get("memberUid")
            or record.get("requesterUid")
            or record.get("applicantUid")
        )
        if not uuids or not member_uids:
            return None
        role_sub_resource_ids = DwsClient._string_list(
            record.get("roleSubResourceIds")
        )
        return DwsMinutesPermissionRequest(
            uuids=uuids,
            member_uids=member_uids,
            policy_id=DwsClient._int_value(record.get("policyId"), default=3),
            role_sub_resource_ids=role_sub_resource_ids,
            cover_permission=DwsClient._bool_value(
                record.get("coverPermission"),
                default=False,
            ),
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def _int_list(value: Any) -> list[int]:
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        result: list[int] = []
        for item in values:
            parsed = DwsClient._int_value(item)
            if parsed is not None:
                result.append(parsed)
        return result

    @staticmethod
    def _int_value(value: Any, default: int | None = None) -> int | None:
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return default

    @staticmethod
    def _bool_value(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        return default

    @staticmethod
    def _mentioned_user_ids(message: dict[str, Any]) -> list[str]:
        raw_mentions = message.get("atUserIds") or message.get("mentionedUserIds") or []
        if isinstance(raw_mentions, str):
            return [item for item in raw_mentions.split(",") if item]
        if isinstance(raw_mentions, list):
            return [str(item) for item in raw_mentions if item]
        return []

    @staticmethod
    def _message_type(message: dict[str, Any]) -> str | None:
        for key in (
            "msgType",
            "messageType",
            "contentType",
            "content_type",
            "msg_type",
            "type",
        ):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def parse_user_profiles(payload: dict[str, Any]) -> list[DwsUserProfile]:
        records = payload.get("result", [])
        if isinstance(records, dict):
            for key in ("users", "userList", "deptUserList"):
                if isinstance(records.get(key), list):
                    records = records[key]
                    break
            else:
                records = [records]
        profiles = []
        for record in records:
            user_payload = DwsClient._user_payload(record)
            user_id = (
                user_payload.get("userId")
                or user_payload.get("userid")
                or user_payload.get("orgUserId")
                or user_payload.get("id")
            )
            if not user_id:
                continue
            profiles.append(
                DwsUserProfile(
                    user_id=str(user_id),
                    name=str(
                        user_payload.get("orgUserName")
                        or user_payload.get("name")
                        or user_payload.get("nick")
                        or ""
                    ),
                    nick=str(user_payload.get("nick") or ""),
                    title=str(
                        user_payload.get("title")
                        or user_payload.get("position")
                        or user_payload.get("jobTitle")
                        or ""
                    ),
                    open_dingtalk_id=user_payload.get("openDingTalkId")
                    or user_payload.get("openConversationId")
                    or user_payload.get("openId"),
                    manager_user_id=user_payload.get("orgMasterUserId")
                    or user_payload.get("managerUserId")
                    or user_payload.get("masterUserId"),
                    manager_name=str(
                        user_payload.get("orgMasterDisplayName")
                        or user_payload.get("managerName")
                        or user_payload.get("masterName")
                        or ""
                    ),
                    department_ids=DwsClient._department_ids(user_payload),
                    department_names=DwsClient._department_names(user_payload),
                    org_labels=DwsClient._org_labels(user_payload),
                    has_subordinate=DwsClient._has_subordinate(user_payload),
                )
            )
        return profiles

    @staticmethod
    def parse_department_member_profiles(payload: dict[str, Any]) -> list[DwsUserProfile]:
        result = payload.get("result", [])
        records = []
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and isinstance(item.get("deptUserList"), list):
                    records.extend(item["deptUserList"])
                else:
                    records.append(item)
        elif isinstance(result, dict):
            records = result.get("deptUserList") or result.get("users") or []
        return DwsClient.parse_user_profiles({"result": records})

    @staticmethod
    def parse_pending_oa_approvals(
        payload: dict[str, Any],
    ) -> list[DwsOaApprovalCandidate]:
        result = payload.get("result", {})
        records = []
        if isinstance(result, dict):
            for key in ("list", "items", "processInstances", "processInstanceList"):
                if isinstance(result.get(key), list):
                    records = result[key]
                    break
        elif isinstance(result, list):
            records = result
        approvals = []
        for record in records:
            if not isinstance(record, dict):
                continue
            process_instance_id = (
                record.get("processInstanceId")
                or record.get("process_instance_id")
                or record.get("instanceId")
            )
            if not process_instance_id:
                continue
            approvals.append(
                DwsOaApprovalCandidate(
                    process_instance_id=str(process_instance_id),
                    title=str(
                        record.get("processInstanceTitle")
                        or record.get("title")
                        or ""
                    ),
                    process_name=str(record.get("processName") or ""),
                )
            )
        return approvals

    @staticmethod
    def parse_department_ids(payload: dict[str, Any]) -> set[str]:
        records = payload.get("result", [])
        if not records:
            records = payload.get("deptList") or payload.get("departments") or []
        if isinstance(records, dict):
            for key in ("departments", "deptList", "list"):
                if isinstance(records.get(key), list):
                    records = records[key]
                    break
            else:
                records = [records]
        department_ids = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            dept_id = record.get("deptId") or record.get("id") or record.get("dept_id")
            if dept_id:
                department_ids.add(str(dept_id))
        return department_ids

    @staticmethod
    def _user_payload(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict):
            return {}
        user_info = record.get("userInfo")
        if isinstance(user_info, dict):
            return DwsClient._user_payload(user_info)
        employee = record.get("orgEmployeeModel")
        if isinstance(employee, dict):
            return employee
        return record

    @staticmethod
    def _department_ids(user_payload: dict[str, Any]) -> set[str]:
        department_ids = set()
        for key in ("deptIdList", "deptIds", "departmentIds"):
            values = user_payload.get(key)
            if isinstance(values, list):
                department_ids.update(str(value) for value in values if value)
        depts = user_payload.get("depts") or user_payload.get("departments") or []
        if isinstance(depts, list):
            for dept in depts:
                if isinstance(dept, dict):
                    dept_id = dept.get("deptId") or dept.get("id") or dept.get("dept_id")
                    if dept_id:
                        department_ids.add(str(dept_id))
                elif dept:
                    department_ids.add(str(dept))
        return department_ids

    @staticmethod
    def _department_names(user_payload: dict[str, Any]) -> set[str]:
        department_names = set()
        depts = user_payload.get("depts") or user_payload.get("departments") or []
        if isinstance(depts, list):
            for dept in depts:
                if not isinstance(dept, dict):
                    continue
                dept_name = dept.get("deptName") or dept.get("name")
                if dept_name:
                    department_names.add(str(dept_name))
        return department_names

    @staticmethod
    def _org_labels(user_payload: dict[str, Any]) -> list[str]:
        labels = user_payload.get("labels") or []
        if not isinstance(labels, list):
            return []
        result = []
        for label in labels:
            if not isinstance(label, dict):
                continue
            group_name = str(label.get("groupName") or "").strip()
            name = str(label.get("name") or "").strip()
            if group_name and name:
                result.append(f"{group_name}: {name}")
            elif name:
                result.append(name)
        return result

    @staticmethod
    def _has_subordinate(user_payload: dict[str, Any]) -> bool | None:
        value = user_payload.get("hasSubordinate")
        if isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _read_dingtalk_skill_credentials(
        config_path: str | None = None,
    ) -> dict[str, str]:
        if config_path is None:
            env_file_values = read_env_file()
            env_values = {
                "DINGTALK_APP_KEY": os.getenv("DWS_CLIENT_ID")
                or env_file_values.get("DWS_CLIENT_ID", "")
                or os.getenv("DINGTALK_APP_KEY", "")
                or env_file_values.get("DINGTALK_APP_KEY", ""),
                "DINGTALK_APP_SECRET": os.getenv("DWS_CLIENT_SECRET")
                or env_file_values.get("DWS_CLIENT_SECRET", "")
                or os.getenv("DINGTALK_APP_SECRET", "")
                or env_file_values.get("DINGTALK_APP_SECRET", ""),
            }
            if env_values["DINGTALK_APP_KEY"] and env_values["DINGTALK_APP_SECRET"]:
                return env_values
        path = config_path or os.path.expanduser("~/.dingtalk-skills/config")
        values: dict[str, str] = {}
        with open(path, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
        missing = [
            key
            for key in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET")
            if not values.get(key)
        ]
        if missing:
            raise DwsError("DingTalk OpenAPI config is missing required credentials")
        return values

    def _read_dingtalk_app_access_token(
        self,
        config_path: str | None = None,
    ) -> str:
        credentials = self._read_dingtalk_skill_credentials(config_path)
        token_payload = self._http_json(
            "POST",
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            {
                "appKey": credentials["DINGTALK_APP_KEY"],
                "appSecret": credentials["DINGTALK_APP_SECRET"],
            },
        )
        token = token_payload.get("accessToken")
        if not isinstance(token, str) or not token:
            raise DwsError("DingTalk OpenAPI token response did not include accessToken")
        return token

    def _dingtalk_api_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise DwsError("DingTalk OpenAPI path must start with /")
        token = self._read_dingtalk_app_access_token(config_path)
        url = f"https://api.dingtalk.com{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        return self._http_json(
            method,
            url,
            payload,
            headers={"x-acs-dingtalk-access-token": token},
        )

    @staticmethod
    def _http_json(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data = None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DwsError(
                f"DingTalk OpenAPI request failed: HTTP {exc.code} {detail}"
            ) from exc
        except Exception as exc:
            raise DwsError("DingTalk OpenAPI request failed") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DwsError("DingTalk OpenAPI returned non-JSON response") from exc
        if not isinstance(parsed, dict):
            raise DwsError("DingTalk OpenAPI returned invalid JSON response")
        return parsed
