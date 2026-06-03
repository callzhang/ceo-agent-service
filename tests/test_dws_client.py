import subprocess
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
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
    def __init__(self, payloads: list[dict]):
        super().__init__(dws_bin="dws")
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def run_json(self, command: list[str]):
        self.commands.append(command)
        return self.payloads.pop(0)


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


def test_dws_upgrade_check_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_upgrade_check_command()

    assert command == ["dws", "upgrade", "--check", "--format", "json"]


def test_dws_upgrade_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_upgrade_command()

    assert command == ["dws", "upgrade", "-y", "--format", "json"]


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


def test_get_resource_download_url_command_uses_mcp_chat_surface():
    client = DwsClient(dws_bin="dws")

    command = client.build_get_resource_download_url_command(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        resource_id="@img-token-1",
        resource_type="mediaId",
    )

    assert command == [
        "dws",
        "mcp",
        "chat",
        "get_resource_download_url",
        "--json",
        json.dumps(
            {
                "openConversationId": "cid-1",
                "openMessageId": "msg-1",
                "resourceId": "@img-token-1",
                "resourceType": "mediaId",
            }
        ),
        "--format",
        "json",
    ]


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
        at_users=["user-1"],
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
        "--at-users",
        "user-1",
        "--text",
        "<@user-1> 收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_command_does_not_duplicate_existing_at_placeholder():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="<@user-1> 收到（by明哥分身）",
        at_users=["user-1"],
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
        "--at-users",
        "user-1",
        "--text",
        "<@user-1> 收到（by明哥分身）",
        "--format",
        "json",
        "--yes",
    ]


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


def test_oa_approval_comment_command_uses_dws_mcp_comment_tool():
    client = DwsClient(dws_bin="dws")

    assert client.build_oa_approval_comment_command(
        process_instance_id="proc-1",
        text="请补充预算来源和项目归属后重新提交。",
    ) == [
        "dws",
        "mcp",
        "oa",
        "dingflow_comments",
        "--processInstanceId",
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


def test_reply_message_command_shape():
    client = DwsClient(dws_bin="dws")

    command = client.build_reply_message_command(
        conversation_id="cid-1",
        ref_message_id="msg-1",
        ref_sender_open_dingtalk_id="open-1",
        text="收到（by明哥分身）",
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
            "mcp",
            "calendar",
            "respond",
            "--eventId",
            "event-1",
            "--responseStatus",
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
        "3",
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
    client = RecordingDwsClient(payload)
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
            "chat",
            "message",
            "list",
            "--group",
            "cid-1",
            "--time",
            expected_time,
            "--forward=false",
            "--limit",
            "2",
            "--format",
            "json",
        ]
    ]
    assert [message.open_message_id for message in messages] == [
        "msg-older",
        "msg-newer",
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

    client.send_message("cid-1", "<@user-1> 收到（by明哥分身）", at_users=["user-1"])

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
            "--at-users",
            "user-1",
            "--text",
            "<@user-1> 收到（by明哥分身）",
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
    def fake_run(command, text, capture_output, check, timeout):
        assert command == ["dws", "probe"]
        assert text is True
        assert capture_output is True
        assert check is False
        assert timeout == 7
        return SimpleNamespace(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="exit code 1"):
        DwsClient(timeout_seconds=7).run_json(["dws", "probe"])


def test_run_json_extracts_error_code_from_stdout_and_retries_transient_timeout(
    monkeypatch,
):
    calls = []
    sleeps = []
    timeout_payload = (
        '{"error":{"server_error_code":"TIMEOUT_ERROR",'
        '"message":"请求超时。服务响应较慢，请稍后重试"}}'
    )

    def fake_run(command, text, capture_output, check, timeout):
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


def test_run_json_prefers_specific_server_error_code_over_generic_nested_code(
    monkeypatch,
):
    calls = []
    timeout_payload = (
        '{"error":{"code":1,"server_error_code":"TIMEOUT_ERROR",'
        '"message":"business error: success=false","reason":"business_error"}}'
    )

    def fake_run(command, text, capture_output, check, timeout):
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

    def fake_run(command, text, capture_output, check, timeout):
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

    def fake_run(command, text, capture_output, check, timeout):
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

    def fake_run(command_arg, text, capture_output, check, timeout):
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

    def fake_run(command_arg, text, capture_output, check, timeout):
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

    def fake_run(command_arg, text, capture_output, check, timeout):
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

    def fake_run(command_arg, text, capture_output, check, timeout):
        calls.append(command_arg)
        return SimpleNamespace(returncode=1, stdout="", stderr=system_error_payload)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="SYSTEM_ERROR"):
        DwsClient().run_json(command)
    assert calls == [command]


def test_run_json_uses_process_exit_code_when_dws_stderr_is_not_json(monkeypatch):
    calls = []
    sleeps = []

    def fake_run(command, text, capture_output, check, timeout):
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

    def fake_run(command, text, capture_output, check, timeout):
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
    def fake_run(command, text, capture_output, check, timeout):
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

    def fake_run(command, text, capture_output, check, timeout):
        return SimpleNamespace(returncode=4, stdout="", stderr=stderr)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError) as exc_info:
        DwsClient().run_json(["dws", "chat", "message", "send"])

    error = exc_info.value
    assert error.code == "PAT_HIGH_RISK_NO_PERMISSION"
    assert error.needs_authorization is True
    assert "PAT_HIGH_RISK_NO_PERMISSION" in str(error)
    assert "authorizationUrl" not in str(error)
    assert "open-dev.dingtalk.com" not in str(error)


def test_run_json_raises_dws_error_on_invalid_json(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
        assert timeout == 30
        return SimpleNamespace(returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="invalid JSON"):
        DwsClient().run_json(["dws", "probe"])


def test_run_text_returns_stdout_on_success(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
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


def test_run_text_raises_dws_error_on_nonzero_exit(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
        return SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="exit code 1"):
        DwsClient().run_text(["dws", "upgrade", "-y"])


def test_run_json_raises_dws_error_on_timeout(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr("app.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("app.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="timed out"):
        DwsClient(timeout_seconds=3).run_json(["dws", "probe"])
