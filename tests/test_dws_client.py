import subprocess
from datetime import datetime, timedelta
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
)
from app import dws_client
from app.dws_client import (
    DWS_AGENT_CODE_ENV,
    DwsClient,
    DwsError,
    DwsMinutesPermissionRequest,
    DwsOaApprovalCandidate,
)

TEST_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class RecordingDwsClient(DwsClient):
    def __init__(self, payload):
        super().__init__(dws_bin="dws")
        self.payload = payload
        self.commands: list[list[str]] = []

    def run_json(self, command: list[str]):
        self.commands.append(command)
        return self.payload


class SequenceRecordingDwsClient(DwsClient):
    def __init__(self, payloads: list[dict | Exception]):
        super().__init__(dws_bin="dws")
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def run_json(self, command: list[str]):
        self.commands.append(command)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def make_message(content: str) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=False,
        sender_name="Xiaomin张晓民",
        create_time="2026-05-13 15:16:49",
        content=content,
    )


def test_dingtalk_message_mentions_principal_for_english_name():
    message = make_message("@Alex Chen(明哥) 这个看一下")

    assert message.mentions_principal() is True


def test_dingtalk_message_mentions_principal_for_chinese_name():
    message = make_message("@明哥 这个看一下")

    assert message.mentions_principal() is True


def test_dingtalk_message_mentions_principal_false_for_name_without_at():
    message = make_message("这个要和明哥对一下")

    assert message.mentions_principal() is False


def test_dingtalk_message_mentions_principal_false_for_unrelated_content():
    message = make_message("这个请俊杰看一下")

    assert message.mentions_principal() is False


def test_codex_action_values_match_output_protocol():
    assert [action.value for action in CodexAction] == [
        "send_reply",
        "ask_clarifying_question",
        "handoff_to_human",
        "no_reply",
        "stop_with_error",
    ]


def test_codex_decision_defaults():
    decision = CodexDecision(action=CodexAction.NO_REPLY)

    assert decision.reply_text == ""
    assert decision.reason == ""
    assert decision.ding_self is False
    assert decision.macos_notify is True
    assert decision.sensitivity_kind == "general"
    assert decision.personnel_subject_user_id is None
    assert decision.candidate_context_known is False
    assert decision.candidate_department_ids == []


def test_dws_client_defaults_to_dws_binary():
    client = DwsClient()

    assert client.dws_bin == "dws"


def test_dws_client_defaults_to_30_second_timeout():
    client = DwsClient()

    assert client.timeout_seconds == 30


def test_list_unread_conversations_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_unread_conversations_command(count=50)

    assert command == [
        "dws",
        "chat",
        "message",
        "list-unread-conversations",
        "--count",
        "50",
        "--format",
        "json",
    ]


def test_list_messages_by_ids_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_messages_by_ids_command(["msg-1", "msg-2"])

    assert command == [
        "dws",
        "chat",
        "message",
        "list-by-ids",
        "--msg-ids",
        "msg-1,msg-2",
        "--format",
        "json",
    ]


def test_list_messages_by_ids_returns_parsed_messages():
    client = RecordingDwsClient(
        {
            "result": {
                "messages": [
                    {
                        "openConversationId": "cid-1",
                        "openMessageId": "msg-1",
                        "sender": "Mina",
                        "senderOpenDingTalkId": "sender-1",
                        "createTime": "2026-05-13 20:25:00",
                        "content": "这个怎么处理？",
                    },
                ]
            }
        }
    )

    messages = client.list_messages_by_ids(["msg-1"])

    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "list-by-ids",
            "--msg-ids",
            "msg-1",
            "--format",
            "json",
        ]
    ]
    assert len(messages) == 1
    assert messages[0].open_message_id == "msg-1"
    assert messages[0].conversation_title == ""


def test_create_markdown_doc_command_shape_and_response():
    client = RecordingDwsClient(
        {"result": {"nodeId": "doc-1", "url": "https://alidocs.example/doc-1"}}
    )

    payload = client.create_markdown_doc("CEO回复", "# 标题\n\n正文")

    assert payload["result"]["url"] == "https://alidocs.example/doc-1"
    assert client.commands == [
        [
            "dws",
            "doc",
            "create",
            "--name",
            "CEO回复",
            "--content",
            "# 标题\n\n正文",
            "--content-format",
            "markdown",
            "--format",
            "json",
            "--yes",
        ]
    ]


def test_add_doc_editor_permission_command_shape_and_response():
    client = RecordingDwsClient({"success": True})

    payload = client.add_doc_editor_permission(
        "doc-1",
        ["user-1", "user-1", " user-2 "],
    )

    assert payload == {"success": True}
    assert client.commands == [
        [
            "dws",
            "doc",
            "permission",
            "add",
            "--node",
            "doc-1",
            "--user",
            "user-1,user-2",
            "--role",
            "EDITOR",
            "--format",
            "json",
            "--yes",
        ]
    ]


def test_dws_upgrade_check_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_upgrade_check_command()

    assert command == ["dws", "upgrade", "--check", "--format", "json"]


def test_dws_upgrade_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_upgrade_command()

    assert command == ["dws", "upgrade", "-y", "--format", "json"]


def test_dws_error_marks_exit_code_2_as_login_required():
    error = DwsError("dws command failed with exit code 2", code="2")

    assert error.needs_login is True


def test_dws_error_marks_session_ended_as_login_required():
    error = DwsError(
        "Failed to refresh token: 400 Bad Request: Your session has ended."
    )

    assert error.needs_login is True


def test_dws_error_marks_not_authenticated_reason_as_login_required():
    error = DwsError(
        'dws command failed; stderr={"error.reason":"not_authenticated"}',
        code="not_authenticated",
    )

    assert error.needs_login is True


def test_dws_error_does_not_treat_pat_authorization_as_login_required():
    error = DwsError("PAT requires extra scope", code="PAT_HIGH_RISK_NO_PERMISSION")

    assert error.needs_authorization is True
    assert error.needs_login is False


def test_auth_login_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_auth_login_command()

    assert command == ["dws", "auth", "login"]


def test_auth_export_command_shape(tmp_path):
    client = DwsClient(dws_bin="dws")
    archive_path = tmp_path / "dws-auth.tar.gz"

    command = client.build_auth_export_command(archive_path)

    assert command == [
        "dws",
        "auth",
        "export",
        "-o",
        str(archive_path),
        "--format",
        "json",
    ]


def test_auth_import_command_shape(tmp_path):
    client = DwsClient(dws_bin="dws")
    archive_path = tmp_path / "dws-auth.tar.gz"

    command = client.build_auth_import_command(archive_path)

    assert command == [
        "dws",
        "auth",
        "import",
        "-i",
        str(archive_path),
        "--force",
        "--format",
        "json",
    ]


def test_auth_status_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_auth_status_command()

    assert command == ["dws", "auth", "status", "--format", "json"]


def test_build_todo_create_command():
    client = DwsClient(dws_bin="dws")

    assert client.build_todo_create_command(
        title="给客户同步验收 ETA",
        executor_user_id="owner-1",
        due="2026-07-01T18:00:00+08:00",
        priority=30,
    ) == [
        "dws",
        "todo",
        "task",
        "create",
        "--title",
        "给客户同步验收 ETA",
        "--executors",
        "owner-1",
        "--due",
        "2026-07-01T18:00:00+08:00",
        "--priority",
        "30",
        "--format",
        "json",
        "--yes",
    ]


def test_build_todo_get_and_done_commands():
    client = DwsClient(dws_bin="dws")

    assert client.build_todo_get_command("dt-task-1") == [
        "dws",
        "todo",
        "task",
        "get",
        "--task-id",
        "dt-task-1",
        "--format",
        "json",
    ]
    assert client.build_todo_done_command("dt-task-1", done=True) == [
        "dws",
        "todo",
        "task",
        "done",
        "--task-id",
        "dt-task-1",
        "--status",
        "true",
        "--format",
        "json",
        "--yes",
    ]
    assert client.build_todo_done_command("dt-task-1", done=False) == [
        "dws",
        "todo",
        "task",
        "done",
        "--task-id",
        "dt-task-1",
        "--status",
        "false",
        "--format",
        "json",
        "--yes",
    ]


def test_build_todo_done_command_requires_bool_done():
    client = DwsClient(dws_bin="dws")

    with pytest.raises(ValueError, match="DingTalk todo done must be a bool"):
        client.build_todo_done_command("dt-task-1", done="false")


def test_todo_task_wrappers_return_dict_payloads():
    client = RecordingDwsClient({"result": {"taskId": "dt-task-1"}})

    assert client.create_todo_task(
        title="给客户同步验收 ETA",
        executor_user_id="owner-1",
        due="2026-07-01T18:00:00+08:00",
        priority=30,
    ) == {"result": {"taskId": "dt-task-1"}}
    assert client.commands == [
        [
            "dws",
            "todo",
            "task",
            "create",
            "--title",
            "给客户同步验收 ETA",
            "--executors",
            "owner-1",
            "--due",
            "2026-07-01T18:00:00+08:00",
            "--priority",
            "30",
            "--format",
            "json",
            "--yes",
        ]
    ]

    client = RecordingDwsClient({"result": {"taskId": "dt-task-1"}})

    assert client.get_todo_task("dt-task-1") == {"result": {"taskId": "dt-task-1"}}
    assert client.commands == [
        [
            "dws",
            "todo",
            "task",
            "get",
            "--task-id",
            "dt-task-1",
            "--format",
            "json",
        ]
    ]

    client = RecordingDwsClient({"success": True})

    assert client.mark_todo_task_done("dt-task-1", done=True) == {"success": True}
    assert client.commands == [
        [
            "dws",
            "todo",
            "task",
            "done",
            "--task-id",
            "dt-task-1",
            "--status",
            "true",
            "--format",
            "json",
            "--yes",
        ]
    ]


@pytest.mark.parametrize(
    ("method_name", "kwargs", "message"),
    [
        (
            "create_todo_task",
            {
                "title": "给客户同步验收 ETA",
                "executor_user_id": "owner-1",
                "due": "2026-07-01T18:00:00+08:00",
                "priority": 30,
            },
            "invalid todo create response",
        ),
        ("get_todo_task", {"task_id": "dt-task-1"}, "invalid todo get response"),
        (
            "mark_todo_task_done",
            {"task_id": "dt-task-1", "done": True},
            "invalid todo done response",
        ),
    ],
)
def test_todo_task_wrappers_reject_non_dict_payloads(method_name, kwargs, message):
    client = RecordingDwsClient([])

    with pytest.raises(DwsError, match=message):
        getattr(client, method_name)(**kwargs)


def test_run_json_maps_plain_exit_code_2_to_login_required(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        return SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="dws command failed with exit code 2",
        )

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError) as error_info:
        DwsClient().run_json(["dws", "chat", "message", "list"])

    assert error_info.value.code == "2"
    assert error_info.value.needs_login is True


def test_run_json_maps_dotted_error_code_to_login_required(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                '{"error.code":2,'
                '"error.message":"未登录，请先执行 dws auth login",'
                '"error.reason":"not_authenticated"}'
            ),
        )

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError) as error_info:
        DwsClient().run_json(["dws", "chat", "message", "list"])

    assert error_info.value.code == "2"
    assert error_info.value.needs_login is True


def test_run_json_does_not_pass_app_oauth_env_to_dws_cli(monkeypatch):
    monkeypatch.setenv("DWS_CLIENT_ID", "app-client-id")
    monkeypatch.setenv("DWS_CLIENT_SECRET", "app-client-secret")
    monkeypatch.setenv("DINGTALK_APP_KEY", "app-key")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "app-secret")

    def fake_run(command, text, capture_output, check, timeout, env=None):
        assert env is not None
        assert "DWS_CLIENT_ID" not in env
        assert "DWS_CLIENT_SECRET" not in env
        assert "DINGTALK_APP_KEY" not in env
        assert "DINGTALK_APP_SECRET" not in env
        return SimpleNamespace(returncode=0, stdout='{"success": true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    assert DwsClient().run_json(["dws", "auth", "status", "--format", "json"]) == {
        "success": True
    }


def test_read_doc_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_read_doc_command(
        "https://alidocs.dingtalk.com/i/nodes/doc123"
    )

    assert command == [
        "dws",
        "doc",
        "read",
        "--node",
        "https://alidocs.dingtalk.com/i/nodes/doc123",
        "--format",
        "json",
    ]


def test_search_documents_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_search_documents_command(
        "02_下一步推进建议.md",
        page_size=5,
    )

    assert command == [
        "dws",
        "doc",
        "search",
        "--query",
        "02_下一步推进建议.md",
        "--page-size",
        "5",
        "--format",
        "json",
    ]


def test_download_doc_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_download_doc_command("node-1", "/tmp/doc-download")

    assert command == [
        "dws",
        "doc",
        "download",
        "--node",
        "node-1",
        "--output",
        "/tmp/doc-download",
        "--format",
        "json",
    ]


def test_download_doc_supplies_required_output_path():
    client = RecordingDwsClient(
        {"success": True, "resourceUrl": "https://example.test/a"}
    )

    payload = client.download_doc("node-1")

    assert payload["success"] is True
    command = client.commands[0]
    output_index = command.index("--output") + 1
    assert command[:5] == ["dws", "doc", "download", "--node", "node-1"]
    assert command[output_index]
    assert command[output_index] != "--format"


def test_minutes_read_commands_shape():
    client = DwsClient(dws_bin="dws")

    assert client.build_list_minutes_command(max_results=20) == [
        "dws",
        "minutes",
        "list",
        "all",
        "--max",
        "20",
        "--format",
        "json",
    ]
    assert client.build_minutes_info_command("minutes-1") == [
        "dws",
        "minutes",
        "get",
        "info",
        "--id",
        "minutes-1",
        "--format",
        "json",
    ]
    assert client.build_minutes_summary_command("minutes-1") == [
        "dws",
        "minutes",
        "get",
        "summary",
        "--id",
        "minutes-1",
        "--format",
        "json",
    ]
    assert client.build_minutes_todos_command("minutes-1") == [
        "dws",
        "minutes",
        "get",
        "todos",
        "--id",
        "minutes-1",
        "--format",
        "json",
    ]
    assert client.build_minutes_transcription_command("minutes-1") == [
        "dws",
        "minutes",
        "get",
        "transcription",
        "--id",
        "minutes-1",
        "--direction",
        "forward",
        "--format",
        "json",
    ]


def test_list_minutes_returns_parsed_items():
    client = RecordingDwsClient(
        {
            "result": {
                "items": [
                    {"id": "minutes-1", "name": "售前知识库周会"},
                    '{"task_uuid": "minutes-2", "title": "产品例会"}',
                    "minutes-3",
                ]
            }
        }
    )

    rows = client.list_minutes(scope="mine", max_results=3)

    assert rows == [
        {
            "id": "minutes-1",
            "name": "售前知识库周会",
            "taskUuid": "minutes-1",
            "title": "售前知识库周会",
        },
        {"task_uuid": "minutes-2", "title": "产品例会", "taskUuid": "minutes-2"},
        {"taskUuid": "minutes-3", "title": "minutes-3"},
    ]
    assert client.commands == [
        [
            "dws",
            "minutes",
            "list",
            "mine",
            "--max",
            "3",
            "--format",
            "json",
        ]
    ]


def test_list_minutes_page_returns_pagination_metadata():
    client = RecordingDwsClient(
        {
            "result": {
                "itemList": [{"uuid": "minutes-1", "title": "周会"}],
                "hasMore": True,
                "nextToken": "token-2",
            }
        }
    )

    page = client.list_minutes_page(
        scope="all",
        max_results=1,
        next_token="token-1",
    )

    assert page == {
        "items": [{"uuid": "minutes-1", "title": "周会", "taskUuid": "minutes-1"}],
        "has_more": True,
        "next_token": "token-2",
    }
    assert client.commands == [
        [
            "dws",
            "minutes",
            "list",
            "all",
            "--max",
            "1",
            "--next-token",
            "token-1",
            "--format",
            "json",
        ]
    ]


def test_parse_minutes_list_accepts_common_wrappers():
    assert DwsClient.parse_minutes_list(
        {"data": {"records": [{"minutesId": "minutes-1", "title": "周会"}]}}
    ) == [{"minutesId": "minutes-1", "title": "周会", "taskUuid": "minutes-1"}]
    assert DwsClient.parse_minutes_list(
        {"list": [{"taskUuid": "minutes-2", "title": "复盘"}]}
    ) == [{"taskUuid": "minutes-2", "title": "复盘"}]


def test_parse_minutes_list_accepts_live_item_list_shape():
    payload = {
        "result": {
            "itemList": [
                {
                    "uuid": "minutes-1",
                    "title": "吴柯欣 - 招聘专员-2026050701 - 三面",
                    "startTimeISO": "2026-06-07T13:24:06+08:00",
                }
            ]
        }
    }

    assert DwsClient.parse_minutes_list(payload) == [
        {
            "uuid": "minutes-1",
            "title": "吴柯欣 - 招聘专员-2026050701 - 三面",
            "startTimeISO": "2026-06-07T13:24:06+08:00",
            "taskUuid": "minutes-1",
        }
    ]


def test_minutes_transcription_command_shape_with_next_token():
    client = DwsClient(dws_bin="dws")

    command = client.build_minutes_transcription_command(
        "minutes-1",
        next_token="token-2",
    )

    assert command == [
        "dws",
        "minutes",
        "get",
        "transcription",
        "--id",
        "minutes-1",
        "--direction",
        "forward",
        "--next-token",
        "token-2",
        "--format",
        "json",
    ]


def test_get_resource_download_url_command_uses_chat_download_media():
    client = DwsClient(dws_bin="dws")

    command = client.build_get_resource_download_url_command(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        resource_id="@img-token-1",
        resource_type="mediaId",
        output_path="/tmp/message-image",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "download-media",
        "--type",
        "mediaId",
        "--resource-id",
        "@img-token-1",
        "--message-id",
        "msg-1",
        "--open-conversation-id",
        "cid-1",
        "--output",
        "/tmp/message-image",
        "--format",
        "json",
        "--yes",
        "--timeout",
        "30",
    ]


def test_json_from_mixed_stdout_reads_trailing_json():
    payload = DwsClient._json_from_mixed_stdout(
        "downloadUrl: https://signed.example/image.png\n"
        "导出完成: /tmp/message-image (10 bytes)\n"
        '{"response":{"content":{"result":{"downloadUrl":"https://signed.example/image.png"}}}}'
    )

    assert payload == {
        "response": {
            "content": {
                "result": {"downloadUrl": "https://signed.example/image.png"}
            }
        }
    }


def test_get_resource_download_url_keeps_url_when_dws_download_stage_fails(
    monkeypatch,
):
    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["dws"],
            returncode=5,
            stdout=(
                "downloadUrl: https://signed.example/message-image.png?token=abc\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(dws_client.subprocess, "run", fake_run)
    client = DwsClient(dws_bin="dws")

    payload = client.get_resource_download_url(
        "cid-1",
        "msg-1",
        "@img-token-1",
        "mediaId",
    )

    assert payload == {
        "downloadUrl": "https://signed.example/message-image.png?token=abc"
    }


def test_download_robot_message_file_command_uses_official_download_api(monkeypatch):
    monkeypatch.setenv("CEO_DING_ROBOT_CODE", "ding-robot-1")
    client = DwsClient(dws_bin="dws")

    command = client.build_download_robot_message_file_command("download-code-1")

    assert command[:4] == [
        "dws",
        "api",
        "POST",
        "/v1.0/robot/messageFiles/download",
    ]
    data_index = command.index("--data")
    assert json.loads(command[data_index + 1]) == {
        "downloadCode": "download-code-1",
        "robotCode": "ding-robot-1",
    }
    assert command[-2:] == ["--format", "json"]


def test_read_agoal_user_objective_list_calls_official_api():
    calls = []

    class ApiRecordingClient(DwsClient):
        def _read_dingtalk_app_access_token(self, config_path=None):
            calls.append(("token", config_path))
            return "access-token-1"

        def _http_json(self, method, url, payload=None, *, headers=None):
            calls.append((method, url, payload, headers))
            return {"content": [{"objectiveId": "objective-1"}]}

    client = ApiRecordingClient()

    payload = client.read_agoal_user_objective_list(
        ding_user_id="ding-user-1",
        objective_rule_id="rule-1",
        period_ids=["period-q2"],
        config_path="/tmp/dingtalk-config",
    )

    assert payload == {"content": [{"objectiveId": "objective-1"}]}
    assert calls == [
        ("token", "/tmp/dingtalk-config"),
        (
            "POST",
            "https://api.dingtalk.com/v1.0/agoal/users/objectiveLists/query",
            {
                "dingUserId": "ding-user-1",
                "objectiveRuleId": "rule-1",
                "periodIds": ["period-q2"],
            },
            {"x-acs-dingtalk-access-token": "access-token-1"},
        ),
    ]


def test_dingtalk_access_token_prefers_dws_client_env(monkeypatch):
    calls = []

    class ApiRecordingClient(DwsClient):
        def _http_json(self, method, url, payload=None, *, headers=None):
            del headers
            calls.append((method, url, payload))
            return {"accessToken": "access-token-1"}

    monkeypatch.setenv("DWS_CLIENT_ID", "ding-env-client")
    monkeypatch.setenv("DWS_CLIENT_SECRET", "secret-env-client")
    monkeypatch.setenv("DINGTALK_APP_KEY", "ding-config-client")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "secret-config-client")

    token = ApiRecordingClient()._read_dingtalk_app_access_token()

    assert token == "access-token-1"
    assert calls == [
        (
            "POST",
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            {
                "appKey": "ding-env-client",
                "appSecret": "secret-env-client",
            },
        )
    ]


def test_read_agoal_org_objective_rule_list_calls_official_api():
    calls = []

    class ApiRecordingClient(DwsClient):
        def _read_dingtalk_app_access_token(self, config_path=None):
            calls.append(("token", config_path))
            return "access-token-1"

        def _http_json(self, method, url, payload=None, *, headers=None):
            calls.append((method, url, payload, headers))
            return {"content": [{"objectiveRuleId": "rule-1"}]}

    client = ApiRecordingClient()

    client.read_agoal_org_objective_rule_list()

    assert calls == [
        ("token", None),
        (
            "GET",
            "https://api.dingtalk.com/v1.0/agoal/objectiveRules/lists",
            None,
            {"x-acs-dingtalk-access-token": "access-token-1"},
        ),
    ]


def test_read_agoal_objective_rule_list_calls_official_api():
    calls = []

    class ApiRecordingClient(DwsClient):
        def _read_dingtalk_app_access_token(self, config_path=None):
            calls.append(("token", config_path))
            return "access-token-1"

        def _http_json(self, method, url, payload=None, *, headers=None):
            calls.append((method, url, payload, headers))
            return {"content": {"result": [{"objectiveRuleId": "rule-1"}]}}

    client = ApiRecordingClient()

    payload = client.read_agoal_objective_rule_list(
        page_number=2,
        page_size=50,
        config_path="/tmp/dingtalk-config",
    )

    assert payload == {"content": {"result": [{"objectiveRuleId": "rule-1"}]}}
    assert calls == [
        ("token", "/tmp/dingtalk-config"),
        (
            "GET",
            "https://api.dingtalk.com/v1.0/agoal/objectiveRuleLists/query?pageNumber=2&pageSize=50",
            None,
            {"x-acs-dingtalk-access-token": "access-token-1"},
        ),
    ]


def test_read_agoal_objective_progress_list_calls_official_api():
    calls = []

    class ApiRecordingClient(DwsClient):
        def _read_dingtalk_app_access_token(self, config_path=None):
            calls.append(("token", config_path))
            return "access-token-1"

        def _http_json(self, method, url, payload=None, *, headers=None):
            calls.append((method, url, payload, headers))
            return {"content": {"result": []}}

    client = ApiRecordingClient()

    client.read_agoal_objective_progress_list(
        "objective-1",
        page_number=2,
        page_size=50,
    )

    assert calls == [
        ("token", None),
        (
            "GET",
            "https://api.dingtalk.com/v1.0/agoal/objectives/progresses/lists?objectiveId=objective-1&pageNumber=2&pageSize=50",
            None,
            {"x-acs-dingtalk-access-token": "access-token-1"},
        ),
    ]


def test_http_json_exposes_openapi_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        del request, timeout
        raise HTTPError(
            url="https://api.dingtalk.com/v1.0/agoal/objectiveRules/lists",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=BytesIO(b'{"code":"Forbidden"}'),
        )

    monkeypatch.setattr(dws_client, "urlopen", fake_urlopen)

    with pytest.raises(DwsError, match='HTTP 403 .*Forbidden'):
        DwsClient._http_json(
            "GET",
            "https://api.dingtalk.com/v1.0/agoal/objectiveRules/lists",
        )


def test_build_doc_list_command_uses_read_only_list():
    client = DwsClient(dws_bin="dws")

    assert client.build_doc_list_command(
        workspace_id="space-1", folder_id=None, page_token=""
    ) == [
        "dws",
        "doc",
        "list",
        "--workspace",
        "space-1",
        "--format",
        "json",
    ]


def test_build_doc_info_command_is_read_only():
    client = DwsClient(dws_bin="dws")

    assert client.build_doc_info_command("node-1") == [
        "dws",
        "doc",
        "info",
        "--node",
        "node-1",
        "--format",
        "json",
    ]


def test_build_aitable_read_commands_are_read_only():
    client = DwsClient(dws_bin="dws")

    assert client.build_aitable_base_get_command("base-1") == [
        "dws",
        "aitable",
        "base",
        "get",
        "--base-id",
        "base-1",
        "--format",
        "json",
    ]
    assert client.build_aitable_table_get_command("base-1", ["tbl-1"]) == [
        "dws",
        "aitable",
        "table",
        "get",
        "--base-id",
        "base-1",
        "--table-ids",
        "tbl-1",
        "--format",
        "json",
    ]
    assert client.build_aitable_record_query_command("base-1", "tbl-1", 10) == [
        "dws",
        "aitable",
        "record",
        "query",
        "--base-id",
        "base-1",
        "--table-id",
        "tbl-1",
        "--limit",
        "10",
        "--format",
        "json",
    ]


def test_message_read_commands_do_not_mark_dingtalk_messages_seen():
    client = DwsClient(dws_bin="dws")
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=3,
        last_message_create_at=1778666181403,
    )

    commands = [
        client.build_list_unread_conversations_command(count=50),
        client.build_read_recent_messages_command(conversation, limit=20),
        client.build_read_unread_messages_command(conversation),
    ]

    for command in commands:
        joined = " ".join(command)
        assert "mark" not in joined
        assert "seen" not in joined
        assert "--mark-read" not in command
        assert "--read" not in command


def test_send_message_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="收到（by明哥分身）",
        at_open_dingtalk_ids=["open-1"],
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "send",
        "--group",
        "cid-1",
        "--title",
        "收到（by明哥分身）",
        "--at-open-dingtalk-ids",
        "open-1",
        "--text",
        "收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_command_keeps_existing_text_without_injecting_at_placeholder():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="<@open-1> 收到（by明哥分身）",
        at_open_dingtalk_ids=["open-1"],
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "send",
        "--group",
        "cid-1",
        "--title",
        "收到（by明哥分身）",
        "--at-open-dingtalk-ids",
        "open-1",
        "--text",
        "<@open-1> 收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_command_does_not_emit_stale_at_users_flag():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="收到（by明哥分身）",
        at_users=["user-1"],
    )

    assert "--at-users" not in command
    assert command[command.index("--text") + 1] == "收到（by明哥分身）"


def test_send_message_command_uses_open_dingtalk_id_mentions():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="请同步进展",
        at_open_dingtalk_ids=["open-owner-1"],
    )

    assert "--at-users" not in command
    assert command[command.index("--at-open-dingtalk-ids") + 1] == "open-owner-1"
    assert command[command.index("--text") + 1] == "请同步进展"


def test_send_message_command_adds_visible_names_for_structured_group_mentions():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="请同步进展",
        at_open_dingtalk_ids=["open-owner-1"],
        at_open_dingtalk_names=["磊哥"],
    )

    assert command[command.index("--at-open-dingtalk-ids") + 1] == "open-owner-1"
    assert command[command.index("--text") + 1] == " @磊哥 请同步进展"


def test_send_message_command_does_not_duplicate_existing_visible_mentions():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text=" @磊哥 请同步进展",
        at_open_dingtalk_ids=["open-owner-1"],
        at_open_dingtalk_names=["磊哥"],
    )

    assert command[command.index("--text") + 1] == " @磊哥 请同步进展"


def test_send_message_command_does_not_duplicate_mentions_already_in_body():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="@ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。",
        at_open_dingtalk_ids=["open-et", "open-roy"],
        at_open_dingtalk_names=["ET", "Roy Han"],
    )

    assert command[command.index("--text") + 1] == (
        " @ET(张毅倜) 先出方案；@Roy Han(韩露) 补材料。"
    )


def test_direct_send_message_ignores_open_dingtalk_id_mentions_without_group_at_flag():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id=None,
        text="请同步进展",
        at_open_dingtalk_ids=["open-owner-1"],
        user_id="owner-1",
    )

    assert "--at-open-dingtalk-ids" not in command
    assert command[command.index("--text") + 1] == "请同步进展"


def test_send_message_command_supports_title_override():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text=(
            "收到\n\n"
            "反馈：[👍](https://feedback.example.com/up)"
            "｜[👎](https://feedback.example.com/down)"
        ),
        title="收到",
    )

    assert command[command.index("--title") + 1] == "收到"
    assert "https://feedback.example.com/up" in command[command.index("--text") + 1]


def test_send_message_command_supports_direct_user_target():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id=None,
        text="收到（by明哥分身）",
        user_id="user-1",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--title",
        "收到（by明哥分身）",
        "--text",
        "收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_oa_approval_action_command_maps_review_action_to_dws_command():
    client = DwsClient(dws_bin="dws")

    approve = client.build_oa_approval_action_command(
        process_instance_id="proc-1",
        task_id="task-1",
        action="通过",
        remark="同意。",
    )
    reject = client.build_oa_approval_action_command(
        process_instance_id="proc-1",
        task_id="task-1",
        action="拒绝",
        remark="材料不符合规则，拒绝。",
    )

    assert approve == [
        "dws",
        "oa",
        "approval",
        "approve",
        "--instance-id",
        "proc-1",
        "--task-id",
        "task-1",
        "--remark",
        "同意。",
        "--format",
        "json",
        "--yes",
    ]
    assert reject == [
        "dws",
        "oa",
        "approval",
        "reject",
        "--instance-id",
        "proc-1",
        "--task-id",
        "task-1",
        "--remark",
        "材料不符合规则，拒绝。",
        "--format",
        "json",
        "--yes",
    ]


def test_oa_approval_action_command_does_not_map_return_to_reject():
    client = DwsClient(dws_bin="dws")

    with pytest.raises(ValueError, match="distinct OA return action"):
        client.build_oa_approval_action_command(
            process_instance_id="proc-1",
            task_id="task-1",
            action="退回",
            remark="请补材料。",
        )


def test_oa_approval_comment_command_uses_stable_oa_comment_command():
    client = DwsClient(dws_bin="dws")

    assert client.build_oa_approval_comment_command(
        process_instance_id="proc-1",
        text="请补充预算来源和项目归属后重新提交。",
    ) == [
        "dws",
        "oa",
        "approval",
        "oa-comments",
        "--instance-id",
        "proc-1",
        "--text",
        "请补充预算来源和项目归属后重新提交。",
        "--format",
        "json",
        "--yes",
    ]


def test_list_pending_oa_approvals_command_and_parser():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_pending_oa_approvals_command(page=2, size=10)
    approvals = DwsClient.parse_pending_oa_approvals(
        {
            "result": {
                "list": [
                    {
                        "processInstanceId": "proc-1",
                        "processInstanceTitle": "刘瑞安提交的录用申请",
                        "processName": "录用申请",
                    }
                ]
            }
        }
    )

    assert command == [
        "dws",
        "oa",
        "approval",
        "list-pending",
        "--page",
        "2",
        "--size",
        "10",
        "--format",
        "json",
    ]
    assert approvals == [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="刘瑞安提交的录用申请",
            process_name="录用申请",
        )
    ]


def test_parse_pending_oa_approvals_accepts_process_instance_list():
    approvals = DwsClient.parse_pending_oa_approvals(
        {
            "result": {
                "processInstanceList": [
                    {
                        "processInstanceId": "proc-1",
                        "processInstanceTitle": "郑威格提交的项目立项全流程（第三曲线）",
                        "processName": "项目立项全流程（第三曲线）",
                    }
                ]
            }
        }
    )

    assert approvals == [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="郑威格提交的项目立项全流程（第三曲线）",
            process_name="项目立项全流程（第三曲线）",
        )
    ]


def test_reply_message_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_reply_message_command(
        conversation_id="cid-1",
        ref_message_id="msg-1",
        ref_sender_open_dingtalk_id="open-1",
        text="收到（by明哥分身）",
        at_users=["user-1", "user-2"],
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "reply",
        "--conversation-id",
        "cid-1",
        "--ref-msg-id",
        "msg-1",
        "--ref-sender",
        "open-1",
        "--text",
        "收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_reply_to_trigger_prefers_native_reply_over_group_at_send():
    client = RecordingDwsClient({"success": True})
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="CEO-2 管理群",
        single_chat=False,
        unread_point=1,
    )
    trigger = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="CEO-2 管理群",
        single_chat=False,
        sender_name="Lily",
        sender_open_dingtalk_id="open-lily",
        create_time="2026-06-09 09:00:00",
        content="@Derek Zen(磊哥) 看一下",
    )

    client.send_reply_to_trigger(
        conversation,
        trigger,
        "@ET(张毅倜) 先出方案。",
        at_open_dingtalk_ids=["open-et"],
        at_open_dingtalk_names=["ET"],
    )

    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "reply",
            "--conversation-id",
            "cid-1",
            "--ref-msg-id",
            "msg-1",
            "--ref-sender",
            "open-lily",
            "--text",
            " @ET(张毅倜) 先出方案。",
            "--format",
            "json",
            "--yes",
        ]
    ]


def test_send_reply_to_trigger_chunks_splits_long_text_and_extracts_recall_key():
    client = SequenceRecordingDwsClient(
        [
            {"result": {"processQueryKey": "recall-1"}},
            {"result": {"processQueryKey": "recall-2"}},
        ]
    )
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="CEO-2 管理群",
        single_chat=False,
        unread_point=1,
    )
    trigger = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="CEO-2 管理群",
        single_chat=False,
        sender_name="Lily",
        sender_open_dingtalk_id="open-lily",
        create_time="2026-06-09 09:00:00",
        content="@Derek Zen(磊哥) 看一下",
    )

    result = client.send_reply_to_trigger_chunks(
        conversation,
        trigger,
        "第一段" * 500 + "\n\n" + "第二段" * 500,
        at_users=["user-lily"],
    )

    assert len(result["chunks"]) == 2
    assert result["chunks"][0]["text"].startswith("【1/2】")
    assert DwsClient.extract_recall_key(result) == "recall-1"
    first_text = client.commands[0][client.commands[0].index("--text") + 1]
    second_text = client.commands[1][client.commands[1].index("--text") + 1]
    assert first_text.startswith("【1/2】")
    assert second_text.startswith("【2/2】")
    assert "--at-user-ids" not in client.commands[1]


def test_send_message_title_uses_reply_body_after_fake_quote():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id=None,
        text=(
            "> Phina: 请根据这篇大纲来判断，这篇文章是在单纯地讲...\n\n"
            "不算单纯讲道理，它已经有比较清楚的业务场景、痛点拆解和解决路径。"
        ),
        user_id="user-1",
    )

    assert command[command.index("--title") + 1] == (
        "不算单纯讲道理，它已经有比较清楚的业务场景..."
    )


def test_create_doc_comment_command_uses_doc_comment_create():
    client = DwsClient(dws_bin="dws")

    command = client.build_create_doc_comment_command("https://example.com/doc", "处理结果")

    assert command == [
        "dws",
        "doc",
        "comment",
        "create",
        "--nodeId",
        "https://example.com/doc",
        "--content",
        "处理结果",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_escapes_at_prefixed_title_for_dws_cli():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text=(
            "> 周俊杰: 我在本地分支改了\n\n"
            "<@user-1> @周俊杰 明白，先把 diff 发出来。"
        ),
        at_users=["user-1"],
    )

    assert command[command.index("--title") + 1] == "回复：@周俊杰 明白，先把 diff 发出来。"
    assert command[command.index("--text") + 1].startswith("> 周俊杰")
    assert "<@user-1> @周俊杰" in command[command.index("--text") + 1]


def test_send_message_escapes_at_prefixed_text_for_dws_cli():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="@周俊杰 明白，先把 diff 发出来。",
    )

    assert command[command.index("--title") + 1].startswith("回复：@")
    assert command[command.index("--text") + 1].startswith(" @")


def test_build_list_all_messages_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_all_messages_command(
        start="2026-06-30 15:00:00",
        end="2026-06-30 15:30:00",
        limit=100,
        cursor="cursor-1",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "list-all",
        "--start",
        "2026-06-30 15:00:00",
        "--end",
        "2026-06-30 15:30:00",
        "--limit",
        "100",
        "--cursor",
        "cursor-1",
        "--format",
        "json",
    ]


def test_build_send_message_by_bot_command_shape():
    client = DwsClient(dws_bin="dws", ding_robot_code="robot-code")

    command = client.build_send_message_by_bot_command(
        text="你好",
        user_ids=["user-1"],
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "send-by-bot",
        "--robot-code",
        "robot-code",
        "--users",
        "user-1",
        "--title",
        "你好",
        "--text",
        "你好",
        "--format",
        "json",
        "--yes",
    ]


def test_send_direct_message_by_bot_uses_short_title():
    class CapturingDwsClient(DwsClient):
        def __init__(self):
            super().__init__(dws_bin="dws", ding_robot_code="robot-code")
            self.commands = []

        def run_json(self, command):
            self.commands.append(command)
            return {"success": True}

    client = CapturingDwsClient()

    result = client.send_direct_message_by_bot(
        "user-1",
        "你好，有事你说。\n\n反馈：[👍](https://feedback.example.com/up)",
    )

    command = client.commands[0]
    assert result == {"success": True}
    assert command[command.index("--title") + 1] == "回复"
    assert command[command.index("--text") + 1].startswith("你好，有事你说。")


def test_read_robot_direct_messages_filters_configured_bot_chat(monkeypatch):
    monkeypatch.setenv("CEO_CHAT_BOT_NAMES", "磊哥")
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 30, 15, 30, 0, tzinfo=tz)

    monkeypatch.setattr(dws_client, "datetime", FixedDatetime)
    client = SequenceRecordingDwsClient(
        [
            {
                "result": {
                    "bots": [
                        {
                            "name": "磊哥",
                            "botOpenDingTalkId": "bot-open-1",
                        }
                    ]
                }
            },
            {
                "result": {
                    "conversationMessagesList": [
                        {
                            "openConversationId": "cid-bot",
                            "singleChat": True,
                            "title": "磊哥",
                            "messages": [
                                {
                                    "openConversationId": "cid-bot",
                                    "openMessageId": "msg-user",
                                    "sender": "磊哥",
                                    "senderOpenDingTalkId": "user-open-1",
                                    "createTime": "2026-06-30 15:29:17",
                                    "content": "hi",
                                },
                                {
                                    "openConversationId": "cid-bot",
                                    "openMessageId": "msg-bot",
                                    "sender": "磊哥",
                                    "senderOpenDingTalkId": "bot-open-1",
                                    "createTime": "2026-06-30 15:29:20",
                                    "content": "bot reply",
                                },
                            ],
                        },
                        {
                            "openConversationId": "cid-other",
                            "singleChat": True,
                            "title": "张静",
                            "messages": [
                                {
                                    "openConversationId": "cid-other",
                                    "openMessageId": "msg-other",
                                    "sender": "磊哥",
                                    "senderOpenDingTalkId": "user-open-1",
                                    "createTime": "2026-06-30 15:29:30",
                                    "content": "普通私聊",
                                }
                            ],
                        },
                    ],
                    "hasMore": False,
                }
            },
        ]
    )

    messages = client.read_robot_direct_messages(lookback_minutes=30)

    assert [message.open_message_id for message in messages] == ["msg-user"]
    assert messages[0].raw_payload["ceo_agent_source"] == "robot_direct"
    assert messages[0].raw_payload["robot_open_dingtalk_ids"] == ["bot-open-1"]
    assert client.commands[0][:5] == ["dws", "chat", "bot", "find", "--query"]
    assert client.commands[1][:4] == ["dws", "chat", "message", "list-all"]


def test_recall_bot_message_command_shape():
    client = DwsClient(dws_bin="dws", ding_robot_code="robot-code")

    command = client.build_recall_bot_message_command(
        conversation_id="cid-1",
        process_query_key="key-1",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "recall-by-bot",
        "--robot-code",
        "robot-code",
        "--group",
        "cid-1",
        "--keys",
        "key-1",
        "--format",
        "json",
        "--yes",
    ]


def test_recall_message_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_recall_message_command(
        conversation_id="cid-1",
        message_id="msg-1",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "recall",
        "--conversation-id",
        "cid-1",
        "--msg-id",
        "msg-1",
        "--format",
        "json",
        "--yes",
    ]


def test_add_message_emoji_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_add_message_emoji_command(
        conversation_id="cid-1",
        message_id="msg-1",
        emoji="👍",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "add-emoji",
        "--group",
        "cid-1",
        "--msg-id",
        "msg-1",
        "--emoji",
        "👍",
        "--format",
        "json",
        "--yes",
    ]


def test_add_message_text_emotion_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_add_message_text_emotion_command(
        conversation_id="cid-1",
        message_id="msg-1",
        text="收到",
        emotion_id="emotion-1",
        emotion_name="收到",
        background_id="bg-1",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "add-text-emotion",
        "--group",
        "cid-1",
        "--msg-id",
        "msg-1",
        "--text",
        "收到",
        "--emotion-id",
        "emotion-1",
        "--emotion-name",
        "收到",
        "--background-id",
        "bg-1",
        "--format",
        "json",
        "--yes",
    ]


def test_create_message_text_emotion_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_create_message_text_emotion_command(
        text="我去摇人",
        emotion_name="我去摇人",
        background_id="im_bg_5",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "create-text-emotion",
        "--text",
        "我去摇人",
        "--emotion-name",
        "我去摇人",
        "--background-id",
        "im_bg_5",
        "--format",
        "json",
        "--yes",
    ]


def test_recall_bot_message_requires_robot_code():
    client = DwsClient(dws_bin="dws")

    with pytest.raises(DwsError, match="DING robot code is not configured"):
        client.build_recall_bot_message_command("cid-1", "key-1")


def test_extract_recall_key_from_send_result():
    assert (
        DwsClient.extract_recall_key(
            {"result": {"processQueryKey": "key-1"}}
        )
        == "key-1"
    )
    assert (
        DwsClient.extract_recall_key(
            {"result": {"processQueryKeys": ["key-2"]}}
        )
        == "key-2"
    )
    assert DwsClient.extract_recall_key({"result": {"open_taskId": "task-1"}}) == ""


def test_parse_unread_conversations_response():
    payload = {
        "success": True,
        "result": {
            "conversations": [
                {
                    "openConversationId": "cid-1",
                    "title": "Friday",
                    "singleChat": False,
                    "unreadPoint": 3,
                    "notificationOff": 0,
                    "lastMsgCreateAt": 1778666181403,
                    "userId": "user-1",
                    "openDingTalkId": "open-1",
                }
            ]
        },
    }

    conversations = DwsClient.parse_unread_conversations(payload)

    assert conversations == [
        DingTalkConversation(
            open_conversation_id="cid-1",
            title="Friday",
            single_chat=False,
            unread_point=3,
            notification_off=False,
            last_message_create_at=1778666181403,
            direct_user_id="user-1",
            direct_open_dingtalk_id="open-1",
        )
    ]


def test_parse_document_search_results_response():
    payload = {
        "documents": [
            {
                "nodeId": "node-1",
                "name": "02_下一步推进建议",
                "extension": "md",
                "contentType": "OTHER",
                "nodeType": "file",
                "docUrl": "https://alidocs.dingtalk.com/i/nodes/node-1",
            },
            {"name": "missing node"},
        ]
    }

    results = DwsClient.parse_document_search_results(payload)

    assert len(results) == 1
    assert results[0].node_id == "node-1"
    assert results[0].name == "02_下一步推进建议"
    assert results[0].extension == "md"
    assert results[0].content_type == "OTHER"
    assert results[0].node_type == "file"
    assert results[0].doc_url == "https://alidocs.dingtalk.com/i/nodes/node-1"


def test_parse_messages_response_keeps_quoted_message():
    payload = {
        "success": True,
        "result": {
            "messages": [
                {
                    "openConversationId": "cid-1",
                    "openMessageId": "msg-1",
                    "sender": "Xiaomin张晓民",
                    "senderOpenDingTalkId": "sender-1",
                    "senderUserId": "sender-user-1",
                    "msgType": "text",
                    "createTime": "2026-05-13 15:16:49",
                    "content": "@Alex Chen(明哥) 我和俊杰聊下",
                    "atUserIds": ["principal-user-1", "jun-jie-user-1"],
                    "quotedMessage": {
                        "openMessageId": "msg-0",
                        "content": "这个ACL表看一下",
                        "createTime": "2026-05-13 15:15:14",
                        "sender": "null",
                    },
                }
            ]
        },
    }

    messages = DwsClient.parse_messages(
        payload, conversation_title="Friday", single_chat=False
    )

    assert messages[0] == DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=False,
        sender_name="Xiaomin张晓民",
        sender_open_dingtalk_id="sender-1",
        sender_user_id="sender-user-1",
        message_type="text",
        create_time="2026-05-13 15:16:49",
        content="@Alex Chen(明哥) 我和俊杰聊下",
        mentioned_user_ids=["principal-user-1", "jun-jie-user-1"],
        quoted_message_id="msg-0",
        quoted_content="这个ACL表看一下",
        raw_payload=payload["result"]["messages"][0],
    )


def test_calendar_invite_from_message_parses_structured_calendar_payload():
    client = DwsClient(dws_bin="dws")
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="Mina",
        create_time="2026-05-13 15:16:49",
        content="[日程]",
        message_type="calendar",
        raw_payload={
            "calendarEvent": {
                "eventId": "event-1",
                "summary": "客户升级问题决策",
                "start": {"dateTime": "2026-05-14T10:00:00+08:00"},
                "end": {"dateTime": "2026-05-14T11:00:00+08:00"},
                "description": "客户 CEO 会参加，需要 Alex 决策。",
                "organizer": {"displayName": "Mina"},
            }
        },
    )

    event = client.calendar_invite_from_message(message)

    assert event is not None
    assert event.event_id == "event-1"
    assert event.title == "客户升级问题决策"
    assert event.start_time == "2026-05-14T10:00:00+08:00"
    assert event.end_time == "2026-05-14T11:00:00+08:00"
    assert event.description == "客户 CEO 会参加，需要 Alex 决策。"
    assert event.organizer == "Mina"


def test_calendar_invite_from_message_parses_calendar_comments():
    client = DwsClient(dws_bin="dws")
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="Mina",
        create_time="2026-05-13 15:16:49",
        content="[日程]",
        message_type="calendar",
        raw_payload={
            "calendarEvent": {
                "eventId": "event-1",
                "summary": "客户升级问题决策",
                "start": {"dateTime": "2026-05-14T10:00:00+08:00"},
                "end": {"dateTime": "2026-05-14T11:00:00+08:00"},
                "description": "客户 CEO 会参加，需要 Alex 决策。",
                "organizer": {"displayName": "Mina"},
                "commentList": [
                    {
                        "creator": {"displayName": "Mina"},
                        "content": "请会前看完客户升级材料。",
                    },
                    "补充：客户希望当天定方案。",
                ],
            }
        },
    )

    event = client.calendar_invite_from_message(message)

    assert event is not None
    assert event.comments == [
        "Mina: 请会前看完客户升级材料。",
        "补充：客户希望当天定方案。",
    ]


def test_calendar_invite_from_message_accepts_nested_event_without_event_id():
    client = DwsClient(dws_bin="dws")
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="Mina",
        create_time="2026-05-13 15:16:49",
        content="[日程]",
        message_type="calendar",
        raw_payload={
            "schedule": {
                "title": "客户升级问题决策",
                "startTime": "2026-05-14T10:00:00+08:00",
                "endTime": "2026-05-14T11:00:00+08:00",
                "description": "客户 CEO 会参加，需要 Alex 决策。",
            }
        },
    )

    event = client.calendar_invite_from_message(message)

    assert event is not None
    assert event.event_id == ""
    assert event.title == "客户升级问题决策"
    assert event.start_time == "2026-05-14T10:00:00+08:00"
    assert event.end_time == "2026-05-14T11:00:00+08:00"


def test_calendar_invite_from_message_fetches_detail_from_calendar_link():
    client = RecordingDwsClient(
        {
            "success": True,
            "result": {
                "id": "event-1",
                "summary": "国寿Demo思路",
                "start": {"dateTime": "2026-05-30T14:00:00+08:00"},
                "end": {"dateTime": "2026-05-30T15:00:00+08:00"},
                "description": "需要 Alex 参与 Demo 判断。",
                "organizer": {"displayName": "韩露"},
                "created": 1780045392000,
                "updated": 1780046750260,
                "attendees": [
                    {
                        "displayName": "明哥",
                        "responseStatus": "needsAction",
                        "self": True,
                    }
                ],
                "status": "confirmed",
            },
        }
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="韩露",
        single_chat=True,
        sender_name="韩露",
        create_time="2026-05-29 17:26:25",
        content=(
            "好的明哥\n"
            "dingtalk://dingtalkclient/action/open_mini_app?"
            "page=pages%2Fdetail%2Findex%3FuniqueId%3Devent-1%26recurrenceId%3D"
        ),
    )

    event = client.calendar_invite_from_message(message)

    assert event is not None
    assert event.event_id == "event-1"
    assert event.title == "国寿Demo思路"
    assert event.description == "需要 Alex 参与 Demo 判断。"
    assert event.attendees == ["明哥"]
    assert event.self_response_status == "needsAction"
    assert event.status == "confirmed"
    assert event.created_ms == 1780045392000
    assert event.updated_ms == 1780046750260
    assert client.commands == [
        [
            "dws",
            "calendar",
            "event",
            "get",
            "--id",
            "event-1",
            "--format",
            "json",
        ]
    ]


def test_get_calendar_event_returns_none_when_detail_is_unavailable():
    class CalendarDetailUnavailableClient(DwsClient):
        def __init__(self):
            super().__init__(dws_bin="dws")
            self.commands: list[list[str]] = []

        def run_json(self, command: list[str]):
            self.commands.append(command)
            raise DwsError(
                "dws command failed with exit code 1; "
                "command=dws calendar event get --id event-1 --format json; "
                'stderr={"error.code": 1, "error.message": '
                '"business error: success=false"}',
                code="1",
            )

    client = CalendarDetailUnavailableClient()

    assert client.get_calendar_event("event-1") is None
    assert client.commands == [
        [
            "dws",
            "calendar",
            "event",
            "get",
            "--id",
            "event-1",
            "--format",
            "json",
        ]
    ]


def test_calendar_invite_from_message_returns_none_for_unavailable_event_detail():
    class CalendarDetailUnavailableClient(DwsClient):
        def run_json(self, command: list[str]):
            raise DwsError(
                "dws command failed with exit code 1; "
                "command=dws calendar event get --id event-1 --format json; "
                'stderr={"error.code": 1, "error.message": '
                '"business error: success=false"}',
                code="1",
            )

    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="韩露",
        single_chat=True,
        sender_name="韩露",
        create_time="2026-05-29 17:26:25",
        content=(
            "好的明哥\n"
            "dingtalk://dingtalkclient/action/open_mini_app?"
            "page=pages%2Fdetail%2Findex%3FuniqueId%3Devent-1%26recurrenceId%3D"
        ),
    )

    assert CalendarDetailUnavailableClient(dws_bin="dws").calendar_invite_from_message(
        message
    ) is None


def test_list_calendar_events_uses_dws_calendar_event_list():
    client = RecordingDwsClient(
        {
            "success": True,
            "result": {
                "events": [
                    {
                        "id": "event-1",
                        "title": "产品周会",
                        "startTime": "2026-05-14T10:30:00+08:00",
                        "endTime": "2026-05-14T11:30:00+08:00",
                        "description": "固定例会",
                    }
                ]
            },
        }
    )

    events = client.list_calendar_events(
        "2026-05-14T10:00:00+08:00",
        "2026-05-14T11:00:00+08:00",
    )

    assert client.commands == [
        [
            "dws",
            "calendar",
            "event",
            "list",
            "--start",
            "2026-05-14T10:00:00+08:00",
            "--end",
            "2026-05-14T11:00:00+08:00",
            "--format",
            "json",
        ]
    ]
    assert len(events) == 1
    assert events[0].event_id == "event-1"
    assert events[0].title == "产品周会"
    assert events[0].description == "固定例会"


def test_respond_calendar_event_uses_mcp_calendar_respond():
    client = RecordingDwsClient({"success": True})

    result = client.respond_calendar_event("event-1", "accepted")

    assert client.commands == [
        [
            "dws",
            "calendar",
            "event",
            "respond",
            "--id",
            "event-1",
            "--status",
            "accepted",
            "--format",
            "json",
            "--yes",
        ]
    ]
    assert result == {"success": True}


def test_minutes_permission_request_from_message_parses_structured_payload():
    client = DwsClient(dws_bin="dws")
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="Mina",
        create_time="2026-05-13 15:16:49",
        content="[dingtalk://dingtalkclient/page/flash_minutes_detail?x=1]",
        raw_payload={
            "card": {
                "minutesPermissionRequest": {
                    "uuids": ["minutes-1"],
                    "memberUids": [451416406],
                    "policyId": 3,
                    "roleSubResourceIds": ["OrigContent", "Summary"],
                    "coverPermission": "false",
                }
            }
        },
    )

    request = client.minutes_permission_request_from_message(message)

    assert request == DwsMinutesPermissionRequest(
        uuids=["minutes-1"],
        member_uids=[451416406],
        policy_id=3,
        role_sub_resource_ids=["OrigContent", "Summary"],
        cover_permission=False,
    )


def test_minutes_permission_request_does_not_treat_plain_detail_link_as_request():
    client = DwsClient(dws_bin="dws")
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="Mina",
        create_time="2026-05-13 15:16:49",
        content="[dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId=minutes-1&from=8]",
        raw_payload={
            "content": "[dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId=minutes-1&from=8]"
        },
    )

    assert client.minutes_permission_request_from_message(message) is None


def test_add_minutes_member_permission_uses_canonical_mcp_command():
    client = RecordingDwsClient({"success": True})
    request = DwsMinutesPermissionRequest(
        uuids=["minutes-1"],
        member_uids=[451416406],
        policy_id=3,
        role_sub_resource_ids=["OrigContent", "Summary"],
        cover_permission=False,
    )

    assert client.add_minutes_member_permission(request) == {"success": True}

    assert client.commands == [
        [
            "dws",
            "mcp",
            "minutes",
            "add_member_permission",
            "--uuids",
            "minutes-1",
            "--memberUids",
            "451416406",
            "--policyId",
            "3",
            "--coverPermission",
            "false",
            "--roleSubResourceIds",
            "OrigContent,Summary",
            "--format",
            "json",
            "--yes",
        ]
    ]


def test_build_get_user_profiles_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_get_user_profiles_command(["user-1", "user-2"])

    assert command == [
        "dws",
        "contact",
        "user",
        "get",
        "--ids",
        "user-1,user-2",
        "--format",
        "json",
    ]


def test_parse_user_profiles_response():
    payload = {
        "result": [
            {
                "orgEmployeeModel": {
                    "orgUserId": "user-1",
                    "orgUserName": "张三",
                    "openDingTalkId": "open-1",
                    "orgMasterUserId": "manager-1",
                    "orgMasterDisplayName": "李四",
                    "depts": [
                        {"deptId": "dept-1", "deptName": "产品部"},
                        {"id": "dept-2", "name": "售前解决方案部"},
                    ],
                    "labels": [
                        {"groupName": "职务", "name": "产品负责人"},
                        {"groupName": "岗位", "name": "管理层"},
                    ],
                    "hasSubordinate": True,
                }
            }
        ]
    }

    users = DwsClient.parse_user_profiles(payload)

    assert len(users) == 1
    assert users[0].user_id == "user-1"
    assert users[0].name == "张三"
    assert users[0].open_dingtalk_id == "open-1"
    assert users[0].manager_user_id == "manager-1"
    assert users[0].manager_name == "李四"
    assert users[0].department_ids == {"dept-1", "dept-2"}
    assert users[0].department_names == {"产品部", "售前解决方案部"}
    assert users[0].org_labels == ["职务: 产品负责人", "岗位: 管理层"]
    assert users[0].has_subordinate is True


def test_parse_user_profiles_keeps_search_result_title():
    payload = {
        "result": [
            {
                "userId": "user-1",
                "name": "邹婧玮",
                "nick": "Mina 邹",
                "openDingTalkId": "open-1",
                "title": "首席人力资源专家兼HRVP",
            }
        ]
    }

    users = DwsClient.parse_user_profiles(payload)

    assert users[0].title == "首席人力资源专家兼HRVP"


def test_get_user_profile_enriches_missing_title_from_contact_search():
    client = SequenceRecordingDwsClient(
        [
            {
                "result": [
                    {
                        "orgEmployeeModel": {
                            "orgUserId": "user-1",
                            "orgUserName": "邹婧玮",
                        }
                    }
                ]
            },
            {
                "result": [
                    {
                        "userId": "user-1",
                        "name": "邹婧玮",
                        "title": "首席人力资源专家兼HRVP",
                    }
                ]
            },
        ]
    )

    profile = client.get_user_profile("user-1")

    assert profile.title == "首席人力资源专家兼HRVP"
    assert client.commands == [
        client.build_get_user_profiles_command(["user-1"]),
        client.build_search_user_command("邹婧玮"),
    ]


def test_dws_error_treats_missing_agent_code_as_authorization_not_login():
    error = DwsError(
        "dws command failed with exit code 4; code=AGENT_CODE_NOT_EXISTS",
        code=DwsError.AGENT_CODE_NOT_EXISTS_CODE,
    )

    assert error.needs_authorization is True
    assert error.needs_login is False


def test_get_user_profile_keeps_base_profile_when_title_enrichment_needs_agent_code():
    client = SequenceRecordingDwsClient(
        [
            {
                "result": [
                    {
                        "orgEmployeeModel": {
                            "orgUserId": "user-1",
                            "orgUserName": "蔡琳",
                        }
                    }
                ]
            },
            DwsError(
                "dws command failed with exit code 4; code=AGENT_CODE_NOT_EXISTS",
                code=DwsError.AGENT_CODE_NOT_EXISTS_CODE,
            ),
        ]
    )

    profile = client.get_user_profile("user-1")

    assert profile.user_id == "user-1"
    assert profile.name == "蔡琳"
    assert profile.title == ""
    assert client.commands == [
        client.build_get_user_profiles_command(["user-1"]),
        client.build_search_user_command("蔡琳"),
    ]


def test_dws_cli_environment_forces_host_owned_pat_without_browser(monkeypatch):
    monkeypatch.delenv(DWS_AGENT_CODE_ENV, raising=False)
    monkeypatch.delenv("CEO_DWS_AGENT_CODE", raising=False)

    env = DwsClient._cli_environment()

    assert env[DWS_AGENT_CODE_ENV] == "ceo-agent-service"


def test_dws_cli_environment_preserves_configured_agent_code(monkeypatch):
    monkeypatch.setenv(DWS_AGENT_CODE_ENV, "configured-agent")

    env = DwsClient._cli_environment()

    assert env[DWS_AGENT_CODE_ENV] == "configured-agent"


def test_get_user_profiles_enriches_missing_titles_from_contact_search():
    client = SequenceRecordingDwsClient(
        [
            {
                "result": [
                    {
                        "orgEmployeeModel": {
                            "orgUserId": "user-1",
                            "orgUserName": "邹婧玮",
                        }
                    },
                    {
                        "orgEmployeeModel": {
                            "orgUserId": "user-2",
                            "orgUserName": "张三",
                            "title": "产品经理",
                        }
                    },
                ]
            },
            {
                "result": [
                    {
                        "userId": "user-1",
                        "name": "邹婧玮",
                        "title": "首席人力资源专家兼HRVP",
                    }
                ]
            },
        ]
    )

    profiles = client.get_user_profiles(["user-1", "user-2"])

    assert [profile.title for profile in profiles] == [
        "首席人力资源专家兼HRVP",
        "产品经理",
    ]
    assert client.commands == [
        client.build_get_user_profiles_command(["user-1", "user-2"]),
        client.build_search_user_command("邹婧玮"),
    ]


def test_resolve_message_sender_uses_sender_user_id_without_search():
    client = RecordingDwsClient(payload={})
    msg = make_message("hi")
    msg.sender_user_id = "sender-user-1"

    assert client.resolve_message_sender(msg) == "sender-user-1"
    assert client.commands == []


def test_resolve_message_sender_matches_unique_open_dingtalk_id():
    payload = {
        "result": [
            {
                "orgEmployeeModel": {
                    "userId": "user-1",
                    "orgUserName": "张三",
                    "openDingTalkId": "open-1",
                }
            },
            {
                "orgEmployeeModel": {
                    "userId": "user-2",
                    "orgUserName": "张三",
                    "openDingTalkId": "open-2",
                }
            },
        ]
    }
    client = RecordingDwsClient(payload)
    msg = make_message("hi")
    msg.sender_user_id = None
    msg.sender_open_dingtalk_id = "open-2"
    msg.sender_name = "张三"

    assert client.resolve_message_sender(msg) == "user-2"


def test_resolve_message_sender_rejects_ambiguous_name_match():
    payload = {
        "result": [
            {"orgEmployeeModel": {"userId": "user-1", "orgUserName": "张三"}},
            {"orgEmployeeModel": {"userId": "user-2", "orgUserName": "张三"}},
        ]
    }
    client = RecordingDwsClient(payload)
    msg = make_message("hi")
    msg.sender_user_id = None
    msg.sender_open_dingtalk_id = None
    msg.sender_name = "张三"

    with pytest.raises(DwsError, match="unique"):
        client.resolve_message_sender(msg)


def test_user_in_manager_chain_follows_direct_managers():
    payloads = [
        {
            "result": [
                {
                    "orgEmployeeModel": {
                        "userId": "subject",
                        "orgMasterUserId": "manager-1",
                    }
                }
            ]
        },
        {
            "result": [
                {
                    "orgEmployeeModel": {
                        "userId": "manager-1",
                        "orgMasterUserId": "manager-2",
                    }
                }
            ]
        },
    ]

    class SequencedDwsClient(DwsClient):
        def __init__(self):
            super().__init__(dws_bin="dws")
            self.commands = []

        def run_json(self, command):
            self.commands.append(command)
            return payloads.pop(0)

    client = SequencedDwsClient()

    assert client.user_in_manager_chain("manager-2", "subject") is True


def test_get_user_department_ids_returns_profile_departments():
    payload = {
        "result": [
            {
                "orgEmployeeModel": {
                    "userId": "user-1",
                    "depts": [{"deptId": "dept-1"}],
                }
            }
        ]
    }
    client = RecordingDwsClient(payload)

    assert client.get_user_department_ids("user-1") == {"dept-1"}


def test_get_user_department_ids_errors_when_profile_has_no_departments():
    payload = {
        "result": [
            {
                "orgEmployeeModel": {
                    "userId": "user-1",
                    "depts": [],
                }
            }
        ]
    }
    client = RecordingDwsClient(payload)

    with pytest.raises(DwsError, match="department"):
        client.get_user_department_ids("user-1")


def test_parse_department_ids_accepts_top_level_dept_list():
    payload = {
        "deptList": [
            {"deptId": 59442475, "deptName": "<red>人力资源</red>部"},
            {"deptId": "920067298", "deptName": "<red>人力资源</red>部-行政部"},
        ],
        "hasMore": False,
    }

    assert DwsClient.parse_department_ids(payload) == {"59442475", "920067298"}


def test_list_unread_conversations_high_level_method_uses_command():
    payload = {
        "result": {
            "conversations": [
                {
                    "openConversationId": "cid-1",
                    "title": "Friday",
                    "singleChat": False,
                    "unreadPoint": 3,
                }
            ]
        }
    }
    client = RecordingDwsClient(payload)

    conversations = client.list_unread_conversations(count=50)

    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "list-unread-conversations",
            "--count",
            "50",
            "--format",
            "json",
        ]
    ]
    assert conversations[0].open_conversation_id == "cid-1"


def test_read_recent_messages_high_level_method_uses_group_command(monkeypatch):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ) + timedelta(seconds=1)
    expected_time = expected_time.strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    payload = {
        "result": {
            "messages": [
                {
                    "openConversationId": "cid-1",
                    "openMessageId": "msg-1",
                    "sender": "Xiaomin张晓民",
                    "createTime": "2026-05-13 15:16:49",
                    "content": "@Alex Chen(明哥) 看一下",
                }
            ]
        }
    }
    client = RecordingDwsClient(payload)
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=1,
        last_message_create_at=last_message_create_at,
    )

    messages = client.read_recent_messages(conversation, limit=7)

    assert [message.open_message_id for message in messages] == ["msg-1"]
    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "list",
            "--group",
            "cid-1",
            "--time",
            expected_time,
            "--forward=false",
            "--limit",
            "7",
            "--format",
            "json",
        ]
    ]


def test_search_conversations_parses_group_results():
    payload = {
        "result": {
            "value": [
                {
                    "openConversationId": "cid-1",
                    "title": "【招聘】大模型项目经理/大模型数据解决方案专家",
                }
            ]
        }
    }
    client = RecordingDwsClient(payload)

    conversations = client.search_conversations("大模型项目经理")

    assert client.commands == [
        [
            "dws",
            "chat",
            "search",
            "--query",
            "大模型项目经理",
            "--format",
            "json",
        ]
    ]
    assert conversations[0].open_conversation_id == "cid-1"
    assert conversations[0].title == "【招聘】大模型项目经理/大模型数据解决方案专家"


def test_client_conversation_id_uses_conversation_info():
    payload = {
        "result": {
            "conversationInfo": {
                "openConversationId": "cid-open",
                "clientCid": "75217569357",
            }
        }
    }
    client = RecordingDwsClient(payload)

    client_cid = client.client_conversation_id("cid-open")

    assert client.commands == [
        [
            "dws",
            "chat",
            "conversation-info",
            "--group",
            "cid-open",
            "--format",
            "json",
        ]
    ]
    assert client_cid == "75217569357"


def test_read_mentioned_messages_parses_conversation_messages_list(monkeypatch):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    payload = {
        "result": {
            "conversationMessagesList": [
                {
                    "openConversationId": "cid-1",
                    "singleChat": False,
                    "title": "Friday",
                    "messages": [
                        {
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-1",
                            "sender": "Mina 邹",
                            "senderOpenDingTalkId": "open-1",
                            "createTime": "2026-05-25 13:30:26",
                            "content": "@Alex Chen(明哥) 明哥分身，大模型项目经理需要具备什么能力",
                        }
                    ],
                }
            ]
        }
    }
    client = RecordingDwsClient(payload)
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=0,
    )

    messages = client.read_mentioned_messages(conversation, limit=100)

    assert client.commands[0][:4] == ["dws", "chat", "message", "list-mentions"]
    assert "--group" in client.commands[0]
    assert client.commands[0][client.commands[0].index("--group") + 1] == "cid-1"
    assert "--start" in client.commands[0]
    assert "--end" in client.commands[0]
    assert client.commands[0][client.commands[0].index("--end") + 2] == "--group"
    assert messages[0].sender_name == "Mina 邹"
    assert messages[0].open_message_id == "msg-1"


def test_parse_messages_skips_malformed_message_payloads():
    payload = {
        "result": {
            "conversationMessagesList": [
                {
                    "openConversationId": "cid-1",
                    "singleChat": False,
                    "title": "Friday",
                    "messages": [
                        {
                            "openConversationId": "cid-1",
                            "sender": "Broken",
                            "createTime": "2026-05-25 13:30:20",
                            "content": "missing message id",
                        },
                        "not-a-message",
                        {
                            "openConversationId": "cid-1",
                            "openMessageId": "msg-1",
                            "sender": "Mina 邹",
                            "senderOpenDingTalkId": "open-1",
                            "createTime": "2026-05-25 13:30:26",
                            "content": "@Alex Chen(明哥) 明哥分身，大模型项目经理需要具备什么能力",
                        },
                    ],
                }
            ]
        }
    }

    messages = DwsClient.parse_messages(
        payload,
        conversation_title="",
        single_chat=False,
    )

    assert [message.open_message_id for message in messages] == ["msg-1"]


def test_read_mentioned_messages_without_conversation_uses_global_mentions(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    payload = {"result": {"conversationMessagesList": []}}
    client = RecordingDwsClient(payload)

    client.read_mentioned_messages(limit=100)

    command = client.commands[0]
    assert command[:4] == ["dws", "chat", "message", "list-mentions"]
    assert "--group" not in command
    assert command[command.index("--end") + 2] == "--limit"
    assert command[-6:] == ["--limit", "100", "--cursor", "0", "--format", "json"]


def test_build_read_unread_messages_command_reads_latest_unread_window(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ) + timedelta(seconds=1)
    expected_time = expected_time.strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    client = DwsClient(dws_bin="dws")
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=3,
        last_message_create_at=last_message_create_at,
    )

    command = client.build_read_unread_messages_command(conversation)

    assert command == [
        "dws",
        "chat",
        "message",
        "list",
        "--group",
        "cid-1",
        "--time",
        expected_time,
        "--forward=false",
        "--limit",
        "5",
        "--format",
        "json",
    ]


def test_build_read_unread_messages_command_keeps_single_unread_limit(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    client = DwsClient(dws_bin="dws")
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=1,
        last_message_create_at=1778666181403,
    )

    command = client.build_read_unread_messages_command(conversation)

    assert command[command.index("--limit") + 1] == "1"


def test_build_read_unread_messages_command_keeps_larger_unread_window(monkeypatch):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    client = DwsClient(dws_bin="dws")
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        unread_point=9,
        last_message_create_at=1778666181403,
    )

    command = client.build_read_unread_messages_command(conversation)

    assert command[command.index("--limit") + 1] == "9"


def test_build_read_recent_messages_command_uses_direct_list_for_single_chat(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ) + timedelta(seconds=1)
    expected_time = expected_time.strftime("%Y-%m-%d %H:%M:%S")
    client = DwsClient(dws_bin="dws")
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Mina 邹",
        single_chat=True,
        unread_point=1,
        last_message_create_at=last_message_create_at,
        direct_user_id="user-1",
    )

    command = client.build_read_recent_messages_command(conversation, limit=20)

    assert command == [
        "dws",
        "chat",
        "message",
        "list-direct",
        "--user",
        "user-1",
        "--time",
        expected_time,
        "--forward=false",
        "--limit",
        "20",
        "--format",
        "json",
    ]


def test_build_list_messages_by_sender_command_uses_sender_and_cursor():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_messages_by_sender_command(
        sender_user_id="principal-user-1",
        start="2025-11-14T00:00:00-08:00",
        end="2026-05-14T23:59:59-07:00",
        limit=100,
        cursor="0",
    )

    assert command == [
        "dws",
        "chat",
        "message",
        "list-by-sender",
        "--sender-user-id",
        "principal-user-1",
        "--start",
        "2025-11-14T00:00:00-08:00",
        "--end",
        "2026-05-14T23:59:59-07:00",
        "--limit",
        "100",
        "--cursor",
        "0",
        "--format",
        "json",
    ]


def test_read_unread_messages_reads_latest_window_and_returns_chronological_order(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ) + timedelta(seconds=1)
    expected_time = expected_time.strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    payload = {
        "result": {
            "messages": [
                {
                    "openConversationId": "cid-1",
                    "openMessageId": "msg-newer",
                    "sender": "Mina 邹",
                    "createTime": "2026-05-13 20:26:00",
                    "content": "好的",
                },
                {
                    "openConversationId": "cid-1",
                    "openMessageId": "msg-older",
                    "sender": "Mina 邹",
                    "createTime": "2026-05-13 20:25:00",
                    "content": "收到",
                },
            ]
        }
    }
    client = SequenceRecordingDwsClient(
        [
            {
                "result": [
                    {
                        "userId": "user-1",
                        "name": "Mina 邹",
                        "openDingTalkId": "open-1",
                    }
                ]
            },
            payload,
        ]
    )
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Mina 邹",
        single_chat=True,
        unread_point=2,
        last_message_create_at=last_message_create_at,
    )

    messages = client.read_unread_messages(conversation)

    assert client.commands == [
        [
            "dws",
            "contact",
            "user",
            "search",
            "--query",
            "Mina 邹",
            "--format",
            "json",
        ],
        [
            "dws",
            "chat",
            "message",
            "list-direct",
            "--user",
            "user-1",
            "--time",
            expected_time,
            "--forward=false",
            "--limit",
            "5",
            "--format",
            "json",
        ]
    ]
    assert [message.open_message_id for message in messages] == [
        "msg-older",
        "msg-newer",
    ]


def test_read_recent_messages_prefers_exact_single_chat_nick_match(monkeypatch):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    payload = {"result": {"messages": []}}
    client = SequenceRecordingDwsClient(
        [
            {
                "result": [
                    {
                        "userId": "principal-user",
                        "name": "Derek Zen",
                        "nick": "磊哥",
                        "openDingTalkId": "open-principal",
                    },
                    {
                        "userId": "assistant-user",
                        "name": "磊哥助理",
                        "nick": "磊哥助理",
                        "openDingTalkId": "open-assistant",
                    },
                ]
            },
            payload,
        ]
    )
    conversation = DingTalkConversation(
        open_conversation_id="cid-direct",
        title="磊哥",
        single_chat=True,
        unread_point=0,
        last_message_create_at=1778666181403,
    )

    assert client.read_recent_messages(conversation, limit=5) == []

    assert client.commands[1][0:6] == [
        "dws",
        "chat",
        "message",
        "list-direct",
        "--user",
        "principal-user",
    ]


def test_read_recent_messages_reports_missing_single_chat_direct_target():
    client = SequenceRecordingDwsClient([{"result": []}])
    conversation = DingTalkConversation(
        open_conversation_id="cid-direct",
        title="张静",
        single_chat=True,
        unread_point=0,
        last_message_create_at=1778666181403,
    )

    with pytest.raises(DwsError) as exc_info:
        client.read_recent_messages(conversation, limit=5)

    assert exc_info.value.code == DwsError.DIRECT_CHAT_TARGET_NOT_FOUND_CODE
    assert client.commands == [
        ["dws", "contact", "user", "search", "--query", "张静", "--format", "json"]
    ]


def test_message_list_time_uses_dingtalk_message_timezone(monkeypatch):
    monkeypatch.setattr(
        dws_client,
        "_local_time_zone",
        lambda: ZoneInfo("America/Los_Angeles"),
    )

    assert DwsClient._message_list_time(1779061565339) == "2026-05-18 07:46:06"

    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: ZoneInfo("Asia/Shanghai"))

    assert DwsClient._message_list_time(1779061565339) == "2026-05-18 07:46:06"


def test_read_unread_messages_skips_dws_when_unread_point_is_zero():
    client = RecordingDwsClient({"result": {"messages": []}})
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Mina 邹",
        single_chat=True,
        unread_point=0,
    )

    assert client.read_unread_messages(conversation) == []
    assert client.commands == []


def test_send_message_high_level_method_uses_command():
    client = RecordingDwsClient({"success": True})

    client.send_message(
        "cid-1",
        "收到（by明哥分身）",
        at_open_dingtalk_ids=["open-1"],
    )

    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "send",
            "--group",
            "cid-1",
            "--title",
            "收到（by明哥分身）",
            "--at-open-dingtalk-ids",
            "open-1",
            "--text",
            "收到（by明哥分身）",
            "--format",
            "json",
            "--yes",
        ]
    ]


def test_build_ding_self_command_shape():
    client = DwsClient(dws_bin="dws")

    with pytest.raises(DwsError, match="DINGTALK_DING_ROBOT_CODE"):
        client.build_ding_self_command(
            receiver_user_id="user-1",
            text="请看一下",
        )


def test_build_search_bots_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_search_bots_command("极简云机器人")

    assert command == [
        "dws",
        "chat",
        "bot",
        "search",
        "--name",
        "极简云机器人",
        "--format",
        "json",
    ]


def test_ding_robot_code_can_resolve_from_robot_name():
    client = RecordingDwsClient(
        {
            "robotList": [
                {
                    "robotCode": "resolved-code",
                    "robotName": "极简云机器人",
                }
            ]
        }
    )
    client.ding_robot_name = "极简云机器人"

    command = client.build_ding_self_command("user-1", "请看一下")

    assert client.commands == [
        [
            "dws",
            "chat",
            "bot",
            "search",
            "--name",
            "极简云机器人",
            "--format",
            "json",
        ]
    ]
    assert command[-4:] == ["--robot-code", "resolved-code", "--format", "json"]
    assert client.ding_robot_code == "resolved-code"


def test_ding_robot_name_requires_exact_single_match():
    client = RecordingDwsClient({"robotList": []})
    client.ding_robot_name = "极简云机器人"

    with pytest.raises(DwsError, match="expected one DingTalk robot"):
        client.build_ding_self_command("user-1", "请看一下")


def test_build_ding_self_command_shape_with_robot_code():
    client = DwsClient(dws_bin="dws", ding_robot_code="robot-code")

    command = client.build_ding_self_command("user-1", "请看一下")

    assert command == [
        "dws",
        "ding",
        "message",
        "send",
        "--users",
        "user-1",
        "--type",
        "app",
        "--content",
        "请看一下",
        "--robot-code",
        "robot-code",
        "--format",
        "json",
    ]


def test_build_ding_self_command_can_include_robot_code():
    client = DwsClient(dws_bin="dws", ding_robot_code="robot-code")

    command = client.build_ding_self_command(
        receiver_user_id="user-1",
        text="请看一下",
    )

    assert command[-4:] == ["--robot-code", "robot-code", "--format", "json"]


def test_ding_self_uses_configured_receiver():
    client = RecordingDwsClient({"success": True})
    client.ding_robot_code = "robot-code"
    client.ding_receiver_user_id = "user-1"

    client.ding_self("请看一下")

    assert client.commands == [
        [
            "dws",
            "ding",
            "message",
            "send",
            "--users",
            "user-1",
            "--type",
            "app",
            "--content",
            "请看一下",
            "--robot-code",
            "robot-code",
            "--format",
            "json",
        ]
    ]


def test_ding_user_uses_explicit_receiver_without_get_self():
    client = RecordingDwsClient({"success": True})
    client.ding_robot_code = "robot-code"

    client.ding_user("user-1", "请看一下")

    assert client.commands == [
        [
            "dws",
            "ding",
            "message",
            "send",
            "--users",
            "user-1",
            "--type",
            "app",
            "--content",
            "请看一下",
            "--robot-code",
            "robot-code",
            "--format",
            "json",
        ]
    ]


def test_get_current_user_id_parses_get_self_response():
    payload = {
        "result": [
            {
                "orgEmployeeModel": {
                    "userId": "self-user-1",
                    "orgUserName": "Alex",
                }
            }
        ]
    }
    client = RecordingDwsClient(payload)

    assert client.get_current_user_id() == "self-user-1"
    assert client.commands == [
        ["dws", "contact", "user", "get-self", "--format", "json"]
    ]


def test_ding_self_uses_current_user_when_receiver_not_configured():
    payloads = [
        {"result": [{"orgEmployeeModel": {"userId": "self-user-1"}}]},
        {"success": True},
    ]

    class SequencedDwsClient(DwsClient):
        def __init__(self):
            super().__init__(dws_bin="dws", ding_robot_code="robot-code")
            self.commands = []

        def run_json(self, command):
            self.commands.append(command)
            return payloads.pop(0)

    client = SequencedDwsClient()

    client.ding_self("请看一下")

    assert client.commands[-1] == [
        "dws",
        "ding",
        "message",
        "send",
        "--users",
        "self-user-1",
        "--type",
        "app",
        "--content",
        "请看一下",
        "--robot-code",
        "robot-code",
        "--format",
        "json",
    ]


def test_is_current_user_message_compares_resolved_sender():
    client = RecordingDwsClient({"result": [{"orgEmployeeModel": {"userId": "self-user-1"}}]})
    msg = make_message("我来处理")
    msg.sender_user_id = "self-user-1"

    assert client.is_current_user_message(msg) is True


def test_is_current_user_message_does_not_use_display_name_without_sender_id():
    client = RecordingDwsClient(
        {"result": [{"orgEmployeeModel": {"userId": "self-user-1", "name": "明哥"}}]}
    )
    msg = make_message("我来处理")
    msg.sender_name = "明哥"
    msg.sender_user_id = None
    msg.sender_open_dingtalk_id = None

    assert client.is_current_user_message(msg) is False
    assert client.commands == []


def test_run_json_raises_dws_error_on_nonzero_exit(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        assert command == ["dws", "probe"]
        assert text is True
        assert capture_output is True
        assert check is False
        assert timeout == 7
        return SimpleNamespace(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="exit code 1"):
        DwsClient(timeout_seconds=7).run_json(["dws", "probe"])


def test_run_json_uses_per_call_timeout_when_provided(monkeypatch):
    seen_timeouts = []

    def fake_run(command, text, capture_output, check, timeout, env=None):
        seen_timeouts.append(timeout)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    assert DwsClient(timeout_seconds=7).run_json(
        ["dws", "probe"],
        timeout_seconds=120,
    ) == {"ok": True}
    assert seen_timeouts == [120]


def test_run_json_extracts_error_code_from_stdout_and_retries_transient_timeout(
    monkeypatch,
):
    calls = []
    sleeps = []
    timeout_payload = (
        '{"error":{"server_error_code":"TIMEOUT_ERROR",'
        '"message":"请求超时。服务响应较慢，请稍后重试"}}'
    )

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout=timeout_payload, stderr="1")
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert calls == [
        ["dws", "chat", "message", "list"],
        ["dws", "chat", "message", "list"],
    ]
    assert sleeps == [1.0]


def test_run_json_retries_transient_network_error(monkeypatch):
    calls = []
    sleeps = []
    network_payload = (
        '{"error":{"code":1,"server_error_code":"NETWORK_ERROR",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=network_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert calls == [
        ["dws", "chat", "message", "list"],
        ["dws", "chat", "message", "list"],
    ]
    assert sleeps == [1.0]


def test_run_json_prefers_specific_server_error_code_over_generic_nested_code(
    monkeypatch,
):
    calls = []
    timeout_payload = (
        '{"error":{"code":1,"server_error_code":"TIMEOUT_ERROR",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=timeout_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert len(calls) == 2


def test_run_json_refreshes_cache_before_retrying_dws_discovery_code(monkeypatch):
    calls = []
    sleeps = []
    timeout_payload = (
        '{"error":{"category":"discovery","code":6,'
        '"cause":"Post \\"https://mcp-gw.dingtalk.com/server/...\\": '
        'net/http: TLS handshake timeout",'
        '"message":"request to DingTalk gateway failed"}}'
    )

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=6, stdout="", stderr=timeout_payload)
        if len(calls) == 2:
            return SimpleNamespace(returncode=0, stdout='{"refreshed":true}', stderr="")
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert calls == [
        ["dws", "chat", "message", "list"],
        ["dws", "cache", "refresh", "--format", "json"],
        ["dws", "chat", "message", "list"],
    ]
    assert sleeps == [1.0]


def test_run_json_still_retries_when_discovery_cache_refresh_times_out(monkeypatch):
    calls = []
    sleeps = []
    timeout_payload = '{"error":{"category":"discovery","code":6}}'

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=6, stdout="", stderr=timeout_payload)
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(command, timeout)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert calls == [
        ["dws", "chat", "message", "list"],
        ["dws", "cache", "refresh", "--format", "json"],
        ["dws", "chat", "message", "list"],
    ]
    assert sleeps == [1.0]


def test_run_json_retries_doc_read_internal_error(monkeypatch):
    calls = []
    sleeps = []
    internal_error_payload = (
        '{"error":{"code":1,"server_error_code":"internalError",'
        '"message":"文档内容尚未就绪，请稍后重试。","reason":"business_error"}}'
    )
    command = [
        "dws",
        "doc",
        "read",
        "--node",
        "https://alidocs.dingtalk.com/i/nodes/doc123",
        "--format",
        "json",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=internal_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


def test_run_json_does_not_retry_send_internal_error(monkeypatch):
    calls = []
    internal_error_payload = (
        '{"error":{"code":1,"server_error_code":"internalError",'
        '"message":"send failed","reason":"business_error"}}'
    )
    command = ["dws", "chat", "message", "send", "--group", "cid-1"]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        return SimpleNamespace(returncode=1, stdout="", stderr=internal_error_payload)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="internalError"):
        DwsClient().run_json(command)
    assert calls == [command]


def test_run_json_retries_chat_message_list_system_error(monkeypatch):
    calls = []
    sleeps = []
    system_error_payload = (
        '{"error":{"code":1,"server_error_code":"SYSTEM_ERROR",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = [
        "dws",
        "chat",
        "message",
        "list",
        "--group",
        "cid-1",
        "--format",
        "json",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=system_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


def test_run_json_does_not_retry_chat_message_send_system_error(monkeypatch):
    calls = []
    system_error_payload = (
        '{"error":{"code":1,"server_error_code":"SYSTEM_ERROR",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = ["dws", "chat", "message", "send", "--group", "cid-1"]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        return SimpleNamespace(returncode=1, stdout="", stderr=system_error_payload)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="SYSTEM_ERROR"):
        DwsClient().run_json(command)
    assert calls == [command]


def test_run_json_retries_chat_message_list_mentions_token_verified_failed(
    monkeypatch,
):
    calls = []
    sleeps = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = [
        "dws",
        "chat",
        "message",
        "list-mentions",
        "--start",
        "2026-06-10T19:00:09.208579-07:00",
        "--end",
        "2026-06-11T19:00:09.208579-07:00",
        "--format",
        "json",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


def test_run_json_retries_chat_message_list_token_verified_failed(monkeypatch):
    calls = []
    sleeps = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = [
        "dws",
        "chat",
        "message",
        "list",
        "--group",
        "cid-1",
        "--format",
        "json",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    "command",
    [
        [
            "dws",
            "chat",
            "message",
            "list-direct",
            "--user",
            "user-1",
            "--time",
            "2026-07-05 09:49:38",
            "--forward=false",
            "--limit",
            "1",
            "--format",
            "json",
        ],
        [
            "dws",
            "contact",
            "user",
            "search",
            "--query",
            "Lily",
            "--format",
            "json",
        ],
    ],
)
def test_run_json_retries_read_only_token_verified_failed_commands(
    monkeypatch, command
):
    calls = []
    sleeps = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


def test_run_json_retries_calendar_event_get_token_verified_failed(monkeypatch):
    calls = []
    sleeps = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = [
        "dws",
        "calendar",
        "event",
        "get",
        "--id",
        "event-1",
        "--format",
        "json",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(command) == {"ok": True}
    assert calls == [command, command]
    assert sleeps == [1.0]


def test_run_json_does_not_retry_chat_message_send_token_verified_failed(
    monkeypatch,
):
    calls = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = ["dws", "chat", "message", "send", "--group", "cid-1"]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="TOKEN_VERIFIED_FAILED"):
        DwsClient().run_json(command)
    assert calls == [command]


def test_run_json_does_not_retry_calendar_event_respond_token_verified_failed(
    monkeypatch,
):
    calls = []
    token_error_payload = (
        '{"error":{"code":1,"server_error_code":"TOKEN_VERIFIED_FAILED",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )
    command = [
        "dws",
        "calendar",
        "event",
        "respond",
        "--id",
        "event-1",
        "--status",
        "accepted",
        "--format",
        "json",
        "--yes",
    ]

    def fake_run(command_arg, text, capture_output, check, timeout, env=None):
        calls.append(command_arg)
        return SimpleNamespace(returncode=1, stdout="", stderr=token_error_payload)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="TOKEN_VERIFIED_FAILED"):
        DwsClient().run_json(command)
    assert calls == [command]


def test_run_json_uses_process_exit_code_when_dws_stderr_is_not_json(monkeypatch):
    calls = []
    sleeps = []

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=6,
                stdout="",
                stderr=(
                    'dws command failed: {"error":{"category":"discovery",'
                    '"code":6,"message":"gateway timeout"}}'
                ),
            )
        if len(calls) == 2:
            return SimpleNamespace(returncode=0, stdout='{"refreshed":true}', stderr="")
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    assert DwsClient().run_json(["dws", "chat", "message", "list"]) == {"ok": True}
    assert calls == [
        ["dws", "chat", "message", "list"],
        ["dws", "cache", "refresh", "--format", "json"],
        ["dws", "chat", "message", "list"],
    ]
    assert sleeps == [1.0]


def test_run_json_uses_configured_retry_count_and_linear_backoff(monkeypatch):
    calls = []
    sleeps = []
    timeout_payload = '{"error":{"category":"discovery","code":6}}'

    def fake_run(command, text, capture_output, check, timeout, env=None):
        calls.append(command)
        if len([call for call in calls if call == ["dws", "probe"]]) <= 3:
            return SimpleNamespace(returncode=6, stdout="", stderr=timeout_payload)
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", sleeps.append)

    client = DwsClient(
        transient_retry_attempts=3,
        transient_retry_delay_seconds=0.25,
    )

    assert client.run_json(["dws", "probe"]) == {"ok": True}
    assert [call for call in calls if call == ["dws", "probe"]] == [
        ["dws", "probe"],
        ["dws", "probe"],
        ["dws", "probe"],
        ["dws", "probe"],
    ]
    assert sleeps == [0.25, 0.5, 0.75]


def test_run_json_error_includes_sanitized_command_and_output_previews(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        return SimpleNamespace(returncode=1, stdout="raw stdout", stderr="raw stderr")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError) as exc_info:
        DwsClient().run_json(
            ["dws", "chat", "message", "send", "--robot-code", "secret-code"]
        )

    message = str(exc_info.value)
    assert "command=dws chat message send --robot-code <redacted>" in message
    assert "stderr=raw stderr" in message
    assert "stdout=raw stdout" in message
    assert "secret-code" not in message


def test_run_json_sanitizes_pat_authorization_error(monkeypatch):
    stderr = (
        '{"code":"PAT_HIGH_RISK_NO_PERMISSION","data":{'
        '"authorizationUrl":"https://open-dev.dingtalk.com/secret",'
        '"requiredScopes":[{"scope":"chat.message:send"}]}}'
    )

    def fake_run(command, text, capture_output, check, timeout, env=None):
        return SimpleNamespace(returncode=4, stdout="", stderr=stderr)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError) as exc_info:
        DwsClient().run_json(["dws", "chat", "message", "send"])

    error = exc_info.value
    assert error.code == "PAT_HIGH_RISK_NO_PERMISSION"
    assert error.needs_authorization is True
    assert error.required_scopes == ("chat.message:send",)
    assert "PAT_HIGH_RISK_NO_PERMISSION" in str(error)
    assert "authorizationUrl" not in str(error)
    assert "open-dev.dingtalk.com" not in str(error)


def test_start_pat_authorization_uses_interactive_environment(monkeypatch):
    calls = []
    monkeypatch.setenv("DINGTALK_DWS_AGENTCODE", "ceo-agent-service")
    monkeypatch.setenv("CEO_DWS_AGENT_CODE", "ceo-agent-service")

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    def fake_popen(command, text, start_new_session, env):
        calls.append(
            {
                "command": command,
                "text": text,
                "start_new_session": start_new_session,
                "env": env,
            }
        )
        return FakeProcess()

    monkeypatch.setattr(dws_client.subprocess, "Popen", fake_popen)

    process = DwsClient(dws_bin="dws-test").start_pat_authorization(
        ["chat.message:list"]
    )

    assert process.pid == 1234
    assert calls[0]["command"] == [
        "dws-test",
        "pat",
        "chmod",
        "chat.message:list",
        "--grant-type",
        "once",
        "--yes",
        "--format",
        "json",
    ]
    assert "DINGTALK_DWS_AGENTCODE" not in calls[0]["env"]
    assert "CEO_DWS_AGENT_CODE" not in calls[0]["env"]


def test_run_json_raises_dws_error_on_invalid_json(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        assert timeout == 30
        return SimpleNamespace(returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="invalid JSON"):
        DwsClient().run_json(["dws", "probe"])


def test_run_text_returns_stdout_on_success(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        assert command == ["dws", "upgrade", "-y", "--format", "json"]
        assert text is True
        assert capture_output is True
        assert check is False
        assert timeout == 11
        return SimpleNamespace(returncode=0, stdout="upgraded", stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    assert (
        DwsClient(timeout_seconds=11).run_text(
            ["dws", "upgrade", "-y", "--format", "json"]
        )
        == "upgraded"
    )


def test_run_text_uses_per_call_timeout_when_provided(monkeypatch):
    seen_timeouts = []

    def fake_run(command, text, capture_output, check, timeout, env=None):
        seen_timeouts.append(timeout)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    assert (
        DwsClient(timeout_seconds=11).run_text(
            ["dws", "upgrade", "-y", "--format", "json"],
            timeout_seconds=180,
        )
        == "ok"
    )
    assert seen_timeouts == [180]


def test_upgrade_uses_longer_process_timeout(monkeypatch):
    seen_timeouts = []

    def fake_run(command, text, capture_output, check, timeout, env=None):
        assert command == ["dws", "upgrade", "-y", "--format", "json"]
        seen_timeouts.append(timeout)
        return SimpleNamespace(returncode=0, stdout="upgraded", stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    assert DwsClient(timeout_seconds=30).upgrade() == "upgraded"
    assert seen_timeouts == [180]


def test_run_text_raises_dws_error_on_nonzero_exit(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        return SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="exit code 1"):
        DwsClient().run_text(["dws", "upgrade", "-y"])


def test_run_json_raises_dws_error_on_timeout(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout, env=None):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="timed out"):
        DwsClient(timeout_seconds=3).run_json(["dws", "probe"])


def test_download_oa_process_attachment_uses_official_download_url(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"docx-bytes"

    class FakeClient(DwsClient):
        def _dingtalk_api_json(
            self,
            method,
            path,
            *,
            params=None,
            payload=None,
            config_path=None,
        ):
            calls.append((method, path, params, payload, config_path))
            return {"result": {"downloadUri": "https://signed.example/download"}}

    def fake_urlopen(url, timeout):
        assert url == "https://signed.example/download"
        assert timeout == 90
        return FakeResponse()

    monkeypatch.setattr(dws_client, "urlopen", fake_urlopen)

    data = FakeClient(timeout_seconds=3).download_oa_process_attachment(
        "proc-1",
        "file-1",
    )

    assert data == b"docx-bytes"
    assert calls == [
        (
            "POST",
            "/v1.0/workflow/processInstances/spaces/files/urls/download",
            None,
            {"processInstanceId": "proc-1", "fileId": "file-1"},
            None,
        )
    ]
