import subprocess
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
)
from ceo_agent_service import dws_client
from ceo_agent_service.dws_client import DwsClient, DwsError

TEST_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class RecordingDwsClient(DwsClient):
    def __init__(self, payload):
        super().__init__(dws_bin="dws")
        self.payload = payload
        self.commands: list[list[str]] = []

    def run_json(self, command: list[str]):
        self.commands.append(command)
        return self.payload


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


def test_dingtalk_message_mentions_derek_for_english_name():
    message = make_message("@Derek Zen(磊哥) 这个看一下")

    assert message.mentions_derek() is True


def test_dingtalk_message_mentions_derek_for_chinese_name():
    message = make_message("@磊哥 这个看一下")

    assert message.mentions_derek() is True


def test_dingtalk_message_mentions_derek_false_for_name_without_at():
    message = make_message("这个要和磊哥对一下")

    assert message.mentions_derek() is False


def test_dingtalk_message_mentions_derek_false_for_unrelated_content():
    message = make_message("这个请俊杰看一下")

    assert message.mentions_derek() is False


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
        text="收到（by磊哥分身）",
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
        "回复",
        "--at-users",
        "user-1",
        "--text",
        "<@user-1> 收到（by磊哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_command_does_not_duplicate_existing_at_placeholder():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id="cid-1",
        text="<@user-1> 收到（by磊哥分身）",
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
        "回复",
        "--at-users",
        "user-1",
        "--text",
        "<@user-1> 收到（by磊哥分身）",
        "--format",
        "json",
        "--yes",
    ]


def test_send_message_command_supports_direct_user_target():
    client = DwsClient(dws_bin="dws")

    command = client.build_send_message_command(
        conversation_id=None,
        text="收到（by磊哥分身）",
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
        "回复",
        "--text",
        "收到（by磊哥分身）",
        "--format",
        "json",
        "--yes",
    ]


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
                    "content": "@Derek Zen(磊哥) 我和俊杰聊下",
                    "atUserIds": ["derek-user-1", "jun-jie-user-1"],
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
        content="@Derek Zen(磊哥) 我和俊杰聊下",
        mentioned_user_ids=["derek-user-1", "jun-jie-user-1"],
        quoted_message_id="msg-0",
        quoted_content="这个ACL表看一下",
    )


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
                    "depts": [{"deptId": "dept-1"}, {"id": "dept-2"}],
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
    assert users[0].department_ids == {"dept-1", "dept-2"}


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
    ).strftime(
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
                    "content": "@Derek Zen(磊哥) 看一下",
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
            "--forward",
            "false",
            "--limit",
            "7",
            "--format",
            "json",
        ]
    ]
    assert messages[0].open_message_id == "msg-1"


def test_build_read_unread_messages_command_reads_forward_from_unread_cursor(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ).strftime(
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
        "--forward",
        "true",
        "--limit",
        "3",
        "--format",
        "json",
    ]


def test_build_list_messages_by_sender_command_uses_sender_and_cursor():
    client = DwsClient(dws_bin="dws")

    command = client.build_list_messages_by_sender_command(
        sender_user_id="derek-user-1",
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
        "derek-user-1",
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


def test_read_unread_messages_uses_forward_command_and_returns_chronological_order(
    monkeypatch,
):
    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: TEST_LOCAL_TZ)
    last_message_create_at = 1778666181403
    expected_time = datetime.fromtimestamp(
        last_message_create_at / 1000,
        tz=TEST_LOCAL_TZ,
    ).strftime(
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
            "--forward",
            "true",
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


def test_message_list_time_uses_machine_local_timezone(monkeypatch):
    monkeypatch.setattr(
        dws_client,
        "_local_time_zone",
        lambda: ZoneInfo("America/Los_Angeles"),
    )

    assert DwsClient._message_list_time(1779061565339) == "2026-05-17 16:46:05"

    monkeypatch.setattr(dws_client, "_local_time_zone", lambda: ZoneInfo("Asia/Shanghai"))

    assert DwsClient._message_list_time(1779061565339) == "2026-05-18 07:46:05"


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

    client.send_message("cid-1", "<@user-1> 收到（by磊哥分身）", at_users=["user-1"])

    assert client.commands == [
        [
            "dws",
            "chat",
            "message",
            "send",
            "--group",
            "cid-1",
            "--title",
            "回复",
            "--at-users",
            "user-1",
            "--text",
            "<@user-1> 收到（by磊哥分身）",
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
                    "orgUserName": "Derek",
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


def test_run_json_raises_dws_error_on_nonzero_exit(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
        assert command == ["dws", "probe"]
        assert text is True
        assert capture_output is True
        assert check is False
        assert timeout == 7
        return SimpleNamespace(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("ceo_agent_service.dws_client.time.sleep", sleeps.append)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("ceo_agent_service.dws_client.time.sleep", lambda seconds: None)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("ceo_agent_service.dws_client.time.sleep", sleeps.append)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("ceo_agent_service.dws_client.time.sleep", sleeps.append)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)

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

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)

    with pytest.raises(DwsError, match="invalid JSON"):
        DwsClient().run_json(["dws", "probe"])


def test_run_json_raises_dws_error_on_timeout(monkeypatch):
    def fake_run(command, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr("ceo_agent_service.dws_client.subprocess.run", fake_run)
    monkeypatch.setattr("ceo_agent_service.dws_client.time.sleep", lambda seconds: None)

    with pytest.raises(DwsError, match="timed out"):
        DwsClient(timeout_seconds=3).run_json(["dws", "probe"])
