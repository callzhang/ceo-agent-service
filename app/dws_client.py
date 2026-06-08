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
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.dingtalk_models import DingTalkConversation, DingTalkMessage

TITLE_INFORMATION_UNIT_LIMIT = 20
TITLE_WORD_OR_CJK_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*|[\u4e00-\u9fff]"
)
TITLE_AT_FILE_ESCAPE_PREFIX = "回复："
TEXT_AT_FILE_ESCAPE_PREFIX = " "
DINGTALK_MESSAGE_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def _local_time_zone():
    return datetime.now().astimezone().tzinfo


def local_time_zone_name() -> str:
    path = os.path.realpath("/etc/localtime")
    for marker in ("/zoneinfo/", "/usr/share/zoneinfo/"):
        if marker in path:
            return path.split(marker, 1)[1]
    return str(_local_time_zone())


class DwsError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code

    @property
    def needs_authorization(self) -> bool:
        return self.code in {
            "PAT_HIGH_RISK_NO_PERMISSION",
            "PAT_MEDIUM_RISK_NO_PERMISSION",
        }


def native_reply_delivery_payload(
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    send_result: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(extra or {})
    payload["delivery"] = {
        "kind": "native_reply",
        "conversation_id": conversation.open_conversation_id,
        "ref_message_id": trigger.open_message_id,
        "ref_sender_open_dingtalk_id": trigger.sender_open_dingtalk_id or "",
    }
    payload["send_result"] = send_result or {}
    return payload


class DwsUserProfile(BaseModel):
    user_id: str
    name: str = ""
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
    RETRYABLE_ERROR_CODES = {"TIMEOUT_ERROR", "6"}
    DISCOVERY_CACHE_REFRESH_CODES = {"6"}
    DOC_READ_RETRYABLE_ERROR_CODES = {"internalError"}
    MESSAGE_LIST_RETRYABLE_ERROR_CODES = {"SYSTEM_ERROR"}
    SENSITIVE_COMMAND_FLAGS = {
        "--robot-code",
        "--webhook",
        "--secret",
        "--client-secret",
        "--access-token",
        "--token",
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

    def build_upgrade_check_command(self) -> list[str]:
        return [self.dws_bin, "upgrade", "--check", "--format", "json"]

    def build_upgrade_command(self) -> list[str]:
        return [self.dws_bin, "upgrade", "-y", "--format", "json"]

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
        if at_users:
            command.extend(["--at-users", ",".join(at_users)])
            text = self._with_at_placeholders(text, at_users)
        command.extend(["--text", self._literal_cli_value(text), "--format", "json", "--yes"])
        return command

    def build_reply_message_command(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
    ) -> list[str]:
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
        return self.build_message_list_command(
            conversation=conversation,
            limit=max(conversation.unread_point, 1),
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

    def build_list_calendar_events_command(self, start: str, end: str) -> list[str]:
        return [
            self.dws_bin,
            "calendar",
            "event",
            "list",
            "--start",
            start,
            "--end",
            end,
            "--format",
            "json",
        ]

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
            raise ValueError("missing doc comment nodeId")
        if not content.strip():
            raise ValueError("missing doc comment content")
        return [
            self.dws_bin,
            "doc",
            "comment",
            "create",
            "--nodeId",
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
        max_results: int = 20,
        next_token: str = "",
    ) -> list[str]:
        if scope not in {"all", "mine", "shared"}:
            raise ValueError("minutes scope must be one of: all, mine, shared")
        if max_results < 1:
            raise ValueError("max_results must be positive")
        command = [
            self.dws_bin,
            "minutes",
            "list",
            scope,
            "--max",
            str(max_results),
        ]
        if next_token:
            command.extend(["--next-token", next_token])
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
    ) -> list[str]:
        return [
            self.dws_bin,
            "mcp",
            "chat",
            "get_resource_download_url",
            "--json",
            json.dumps(
                {
                    "openConversationId": open_conversation_id,
                    "openMessageId": open_message_id,
                    "resourceId": resource_id,
                    "resourceType": resource_type,
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
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

    def list_unread_conversations(self, count: int) -> list[DingTalkConversation]:
        payload = self.run_json(self.build_list_unread_conversations_command(count))
        return self.parse_unread_conversations(payload)

    def check_upgrade(self) -> dict[str, Any]:
        payload = self.run_json(self.build_upgrade_check_command())
        if not isinstance(payload, dict):
            raise DwsError("invalid dws upgrade check response")
        return payload

    def upgrade(self) -> str:
        return self.run_text(self.build_upgrade_command())

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

    def get_calendar_event(self, event_id: str) -> DwsCalendarEvent | None:
        payload = self.run_json(self.build_get_calendar_event_command(event_id))
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
        max_results: int = 20,
        next_token: str = "",
    ) -> list[dict[str, Any]]:
        return self.list_minutes_page(
            scope=scope,
            max_results=max_results,
            next_token=next_token,
        )["items"]

    def list_minutes_page(
        self,
        *,
        scope: str = "all",
        max_results: int = 20,
        next_token: str = "",
    ) -> dict[str, Any]:
        payload = self.run_json(
            self.build_list_minutes_command(
                scope=scope,
                max_results=max_results,
                next_token=next_token,
            )
        )
        return {
            "items": self.parse_minutes_list(payload),
            "has_more": self.parse_minutes_has_more(payload),
            "next_token": self.parse_minutes_next_token(payload),
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
        payload = self.run_json(
            self.build_get_resource_download_url_command(
                open_conversation_id,
                open_message_id,
                resource_id,
                resource_type,
            )
        )
        if not isinstance(payload, dict):
            raise DwsError("invalid resource download response")
        return payload

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
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_send_message_command(
                conversation_id,
                text,
                at_users,
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
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_reply_message_command(
                conversation_id,
                ref_message_id,
                ref_sender_open_dingtalk_id,
                text,
            )
        )

    def send_reply_to_trigger(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        text: str,
    ) -> dict[str, Any]:
        if not trigger.sender_open_dingtalk_id:
            raise DwsError("missing trigger senderOpenDingTalkId for native reply")
        return self.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
        )

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

    @staticmethod
    def extract_recall_key(send_result: dict[str, Any] | None) -> str:
        if not send_result:
            return ""
        result = send_result.get("result")
        if not isinstance(result, dict):
            return ""
        process_query_key = result.get("processQueryKey")
        if isinstance(process_query_key, str):
            return process_query_key
        process_query_keys = result.get("processQueryKeys")
        if isinstance(process_query_keys, list) and process_query_keys:
            first = process_query_keys[0]
            if isinstance(first, str):
                return first
        return ""

    def ding_user(self, user_id: str, text: str) -> None:
        self.run_json(self.build_ding_self_command(user_id, text))

    def ding_self(self, text: str) -> None:
        receiver_user_id = self.ding_receiver_user_id or self.get_current_user_id()
        self.ding_user(receiver_user_id, text)

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
        profile = matches[0]
        if not profile.title and profile.name:
            profile = self._enrich_user_profile_from_search(profile)
        return profile

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
        payload = self.run_json(self.build_search_user_command(query))
        return self.parse_user_profiles(payload)

    def _enrich_user_profile_from_search(
        self, profile: DwsUserProfile
    ) -> DwsUserProfile:
        if profile.title or not profile.name:
            return profile
        search_matches = [
            item
            for item in self.search_user_profiles(profile.name)
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

    def run_json(self, command: list[str]) -> Any:
        remaining_retries = self.transient_retry_attempts
        attempt_index = 0
        while True:
            try:
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                if remaining_retries > 0:
                    self._sleep_before_retry(attempt_index)
                    attempt_index += 1
                    remaining_retries -= 1
                    continue
                raise DwsError(
                    f"dws command timed out after {self.timeout_seconds} seconds"
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
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DwsError("dws command returned invalid JSON") from exc

    def run_text(self, command: list[str]) -> str:
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DwsError(
                f"dws command timed out after {self.timeout_seconds} seconds"
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
            )
        return result.stdout.strip()

    def _sleep_before_retry(self, attempt_index: int) -> None:
        if self.transient_retry_delay_seconds <= 0:
            return
        time.sleep(self.transient_retry_delay_seconds * (attempt_index + 1))

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
    def _with_at_placeholders(text: str, at_users: list[str]) -> str:
        missing_placeholders = [
            f"<@{user_id}>" for user_id in at_users if f"<@{user_id}>" not in text
        ]
        if not missing_placeholders:
            return text
        return f"{' '.join(missing_placeholders)} {text}"

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
    def _process_error_code(cls, returncode: int) -> str | None:
        code = str(returncode)
        if code in cls.RETRYABLE_ERROR_CODES:
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
        candidates = [payload, result]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = candidate.get("nextToken") or candidate.get("next_token")
            if value:
                return str(value)
        return ""

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
                    parsed_messages.append(
                        DwsClient._parse_message(
                            message,
                            conversation_title=payload_title,
                            single_chat=payload_single_chat,
                        )
                    )
            return parsed_messages
        parsed_messages = []
        for message in messages:
            parsed_messages.append(
                DwsClient._parse_message(
                    message,
                    conversation_title=conversation_title,
                    single_chat=single_chat,
                )
            )
        return parsed_messages

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
            self_response_status=DwsClient._calendar_self_response_status(
                record.get("attendees")
            ),
            attendees=DwsClient._calendar_attendees(record.get("attendees")),
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
            for key in ("list", "items", "processInstances"):
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

    @staticmethod
    def _http_json(
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise DwsError("DingTalk OpenAPI request failed") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DwsError("DingTalk OpenAPI returned non-JSON response") from exc
        if not isinstance(parsed, dict):
            raise DwsError("DingTalk OpenAPI returned invalid JSON response")
        return parsed
