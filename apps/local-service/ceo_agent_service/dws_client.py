import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from ceo_agent_service.dingtalk_models import DingTalkConversation, DingTalkMessage

TITLE_INFORMATION_UNIT_LIMIT = 20
TITLE_WORD_OR_CJK_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[-_'][A-Za-z0-9]+)*|[\u4e00-\u9fff]"
)
TITLE_AT_FILE_ESCAPE_PREFIX = "回复："
TEXT_AT_FILE_ESCAPE_PREFIX = " "


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


class DwsUserProfile(BaseModel):
    user_id: str
    name: str = ""
    open_dingtalk_id: str | None = None
    manager_user_id: str | None = None
    department_ids: set[str] = set()


class DwsDocumentSearchResult(BaseModel):
    node_id: str
    name: str = ""
    extension: str = ""
    content_type: str = ""
    node_type: str = ""
    doc_url: str = ""


class DwsClient:
    # DWS returns generic code 6 for transient discovery/network failures such as
    # TLS handshake timeouts before the request reaches a business API.
    RETRYABLE_ERROR_CODES = {"TIMEOUT_ERROR", "6"}
    DISCOVERY_CACHE_REFRESH_CODES = {"6"}
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
        self.ding_robot_code = ding_robot_code or os.getenv("DINGTALK_DING_ROBOT_CODE")
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

    def build_send_message_command(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
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
        command.extend(["--title", self._literal_cli_value(self._message_title(text), is_title=True)])
        if at_users:
            command.extend(["--at-users", ",".join(at_users)])
            text = self._with_at_placeholders(text, at_users)
        command.extend(["--text", self._literal_cli_value(text), "--format", "json", "--yes"])
        return command

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
            forward=True,
        )

    def build_read_mentioned_messages_command(
        self,
        conversation: DingTalkConversation,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ) -> list[str]:
        local_time_zone = _local_time_zone()
        end_time = datetime.now(tz=local_time_zone)
        start_time = end_time - timedelta(hours=lookback_hours)
        return [
            self.dws_bin,
            "chat",
            "message",
            "list-mentions",
            "--group",
            conversation.open_conversation_id,
            "--start",
            start_time.isoformat(),
            "--end",
            end_time.isoformat(),
            "--limit",
            str(limit),
            "--cursor",
            cursor,
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

    def build_download_doc_command(self, node: str) -> list[str]:
        return [
            self.dws_bin,
            "doc",
            "download",
            "--node",
            node,
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

    def search_conversations(self, query: str) -> list[DingTalkConversation]:
        payload = self.run_json(self.build_search_conversations_command(query))
        return self.parse_search_conversations(payload)

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
        conversation: DingTalkConversation,
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
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
        )

    def read_doc(self, node: str) -> dict[str, Any]:
        payload = self.run_json(self.build_read_doc_command(node))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc read response")
        return payload

    def search_documents(
        self, query: str, page_size: int = 5
    ) -> list[DwsDocumentSearchResult]:
        payload = self.run_json(self.build_search_documents_command(query, page_size))
        return self.parse_document_search_results(payload)

    def download_doc(self, node: str) -> dict[str, Any]:
        payload = self.run_json(self.build_download_doc_command(node))
        if not isinstance(payload, dict):
            raise DwsError("invalid doc download response")
        return payload

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            self.build_send_message_command(
                conversation_id,
                text,
                at_users,
                user_id=user_id,
                open_dingtalk_id=open_dingtalk_id,
            )
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
        return self.parse_user_profiles(payload)

    def get_user_profile(self, user_id: str) -> DwsUserProfile:
        profiles = self.get_user_profiles([user_id])
        matches = [profile for profile in profiles if profile.user_id == user_id]
        if len(matches) != 1:
            raise DwsError(f"expected one user profile for {user_id}, got {len(matches)}")
        return matches[0]

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]:
        payload = self.run_json(self.build_search_user_command(query))
        return self.parse_user_profiles(payload)

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
            if code in self.RETRYABLE_ERROR_CODES and remaining_retries > 0:
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

    def _sleep_before_retry(self, attempt_index: int) -> None:
        if self.transient_retry_delay_seconds <= 0:
            return
        time.sleep(self.transient_retry_delay_seconds * (attempt_index + 1))

    def _refresh_cache(self) -> None:
        subprocess.run(
            [self.dws_bin, "cache", "refresh", "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
        )

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
        local_time_zone = _local_time_zone()
        if last_message_create_at is None:
            return datetime.now(tz=local_time_zone).strftime("%Y-%m-%d %H:%M:%S")
        return datetime.fromtimestamp(
            last_message_create_at / 1000,
            tz=local_time_zone,
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
    def parse_messages(
        payload: dict[str, Any], conversation_title: str, single_chat: bool
    ) -> list[DingTalkMessage]:
        result = payload.get("result", {})
        messages = result.get("messages", [])
        if not messages and isinstance(result.get("conversationMessagesList"), list):
            messages = []
            for conversation_payload in result["conversationMessagesList"]:
                if not isinstance(conversation_payload, dict):
                    continue
                conversation_messages = conversation_payload.get("messages", [])
                if isinstance(conversation_messages, list):
                    messages.extend(conversation_messages)
        parsed_messages = []
        for message in messages:
            quoted_message = message.get("quotedMessage") or {}
            parsed_messages.append(
                DingTalkMessage(
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
                )
            )
        return parsed_messages

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
                    open_dingtalk_id=user_payload.get("openDingTalkId")
                    or user_payload.get("openConversationId")
                    or user_payload.get("openId"),
                    manager_user_id=user_payload.get("orgMasterUserId")
                    or user_payload.get("managerUserId")
                    or user_payload.get("masterUserId"),
                    department_ids=DwsClient._department_ids(user_payload),
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
