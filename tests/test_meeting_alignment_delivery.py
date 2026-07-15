import subprocess

import pytest

import app.meeting_alignment_delivery as meeting_alignment_delivery
from app.dingtalk_models import DingTalkMessage
from app.dws_client import DwsError, DwsUserProfile
from app.meeting_alignment_delivery import (
    MeetingDeliveryAmbiguous,
    MeetingDeliveryError,
    MeetingDeliveryRetry,
    deliver_meeting_alignment,
)
from app.meeting_alignment_models import MeetingAlignmentDecision, MeetingSource


def meeting_source(*, one_to_one: bool = False, unresolved_other: bool = False):
    participants = [
        {"name": "Derek", "user_id": "u-derek", "open_dingtalk_id": "open-derek"},
        {
            "name": "A",
            "user_id": "" if unresolved_other else "u-a",
            "open_dingtalk_id": "" if unresolved_other else "open-a",
        },
    ]
    if not one_to_one:
        participants.append(
            {"name": "B", "user_id": "u-b", "open_dingtalk_id": "open-b"}
        )
    return MeetingSource.model_validate(
        {
            "meeting_id": "minutes-1",
            "title": "上线评审",
            "status": "ended",
            "started_at": "2026-07-14T09:00:00+08:00",
            "ended_at": "2026-07-14T10:00:00+08:00",
            "participants": participants,
            "current_user_id": "u-derek",
            "summary": "",
            "transcript": [],
        }
    )


def send_decision(*, target="group", mention_names=None):
    if target == "group":
        target_payload = {
            "kind": "group",
            "conversation_id": "cid-first",
            "direct_user_id": "",
            "title": "项目群",
            "candidates": [
                {
                    "conversation_id": "cid-first",
                    "title": "项目群",
                    "evidence": ["最近讨论"],
                },
                {
                    "conversation_id": "cid-second",
                    "title": "备用群",
                    "evidence": ["参会人重合"],
                },
            ],
        }
    elif target == "direct":
        target_payload = {
            "kind": "direct",
            "conversation_id": "",
            "direct_user_id": "u-a",
            "title": "A",
            "candidates": [],
        }
    else:
        target_payload = None
    return MeetingAlignmentDecision.model_validate(
        {
            "action": "send",
            "trigger_reasons": ["unresolved_disagreement"],
            "topics": [
                {
                    "title": "上线范围",
                    "state": "unresolved",
                    "views": [
                        {"speaker": "A", "view": "全量", "reason": "效率"},
                        {"speaker": "B", "view": "灰度", "reason": "风险"},
                    ],
                    "conclusion": "",
                    "alignment_reason": "",
                }
            ],
            "derek_viewpoint": None,
            "key_questions": [
                {"question": "选择哪个范围？", "answer_owner_names": ["A"]}
            ],
            "mention_names": mention_names if mention_names is not None else ["A", "B"],
            "target": target_payload,
            "final_message": "会后对齐｜上线评审\n\n需要回答的关键问题",
            "audit_summary": "发现未对齐议题",
            "confidence": 0.7,
        }
    )


def message(sender_name, *, user_id="", open_id=""):
    return DingTalkMessage(
        open_conversation_id="cid-first",
        open_message_id=f"msg-{sender_name}",
        conversation_title="项目群",
        single_chat=False,
        sender_name=sender_name,
        sender_user_id=user_id or None,
        sender_open_dingtalk_id=open_id or None,
        create_time="2026-07-14 10:01:00",
        content="上下文",
    )


class FakeDws:
    def __init__(self):
        self.conversation_info = {
            "openConversationId": "cid-first",
            "title": "项目群",
            "singleChat": False,
            "memberCount": 3,
        }
        self.profiles: dict[str, list[DwsUserProfile]] = {}
        self.recent_messages: list[DingTalkMessage] = []
        self.send_result = {"success": True, "result": {"openMessageId": "msg-sent"}}
        self.send_status_result = {"success": True, "result": {"status": "SUCCESS"}}
        self.send_error = None
        self.verify_error = None
        self.sent: list[dict] = []
        self.status_queries: list[str] = []
        self.search_queries: list[str] = []

    def get_conversation_info(self, conversation_id):
        assert conversation_id == "cid-first"
        return self.conversation_info

    def search_user_profiles(self, query):
        self.search_queries.append(query)
        return list(self.profiles.get(query, []))

    def read_recent_messages(self, conversation, limit=50):
        assert conversation.open_conversation_id == "cid-first"
        return self.recent_messages

    def send_message(self, conversation_id, text, **kwargs):
        self.sent.append(
            {"conversation_id": conversation_id, "text": text, **kwargs}
        )
        if self.send_error is not None:
            raise self.send_error
        return self.send_result

    def verify_message_send_result(self, send_result):
        if self.verify_error is not None:
            raise self.verify_error
        task_id = send_result.get("result", {}).get("openTaskId", "")
        if send_result.get("result", {}).get("openMessageId"):
            return {"state": "sent", "open_task_id": "", "status_result": {}}
        if not task_id:
            return {"state": "ambiguous", "open_task_id": "", "status_result": {}}
        self.status_queries.append(task_id)
        status = self.send_status_result.get("result", {}).get("status")
        state = {"SUCCESS": "sent", "FAILED": "failed"}.get(status, "ambiguous")
        return {
            "state": state,
            "open_task_id": task_id,
            "status_result": self.send_status_result,
        }


def test_group_delivery_uses_first_candidate_and_real_mentions():
    dws = FakeDws()

    result = deliver_meeting_alignment(
        send_decision(), meeting_source(), dws
    )

    assert result.status == "sent"
    assert dws.sent[0]["conversation_id"] == "cid-first"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-a", "open-b"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["A", "B"]
    assert dws.sent[0].get("user_id") is None
    assert dws.sent[0]["text"].startswith(
        "【会议跟进】上线评审（2026-07-14 09:00-10:00）\n\n"
    )
    assert dws.sent[0]["text"].endswith(send_decision().final_message)
    assert result.message_text == dws.sent[0]["text"]


def test_multi_person_no_group_never_falls_back_to_direct():
    dws = FakeDws()

    with pytest.raises(MeetingDeliveryRetry, match="sendable group"):
        deliver_meeting_alignment(send_decision(target=None), meeting_source(), dws)

    assert dws.sent == []


def test_multi_person_direct_target_is_rejected():
    with pytest.raises(MeetingDeliveryError, match="multi-party.*direct"):
        deliver_meeting_alignment(
            send_decision(target="direct"), meeting_source(), FakeDws()
        )


def test_group_must_be_sendable_through_conversation_info():
    dws = FakeDws()
    dws.conversation_info["singleChat"] = True

    with pytest.raises(MeetingDeliveryRetry, match="sendable group"):
        deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert dws.sent == []


def test_one_to_one_direct_delivery_resolves_empty_participant_id_uniquely():
    dws = FakeDws()
    dws.profiles["A"] = [
        DwsUserProfile(user_id="u-a", name="A", open_dingtalk_id="open-a")
    ]
    decision = send_decision(target="direct", mention_names=["A"])
    payload = decision.model_dump()
    payload["target"]["direct_user_id"] = ""
    decision = MeetingAlignmentDecision.model_validate(payload)

    result = deliver_meeting_alignment(
        decision, meeting_source(one_to_one=True, unresolved_other=True), dws
    )

    assert result.status == "sent"
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] == "u-a"


def test_one_to_one_direct_delivery_uses_authoritative_open_id_without_search():
    dws = FakeDws()
    decision_payload = send_decision(
        target="direct", mention_names=["A"]
    ).model_dump()
    decision_payload["target"]["direct_user_id"] = ""
    source_payload = meeting_source(
        one_to_one=True, unresolved_other=True
    ).model_dump()
    source_payload["participants"][1]["open_dingtalk_id"] = "open-a"

    result = deliver_meeting_alignment(
        MeetingAlignmentDecision.model_validate(decision_payload),
        MeetingSource.model_validate(source_payload),
        dws,
    )

    assert result.status == "sent"
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] == "open-a"
    assert dws.sent[0].get("user_id") is None
    assert dws.search_queries == []


def test_direct_delivery_conversation_id_comes_from_verified_send_result():
    dws = FakeDws()
    dws.send_result = {"success": True, "result": {"openTaskId": "task-1"}}
    dws.send_status_result = {
        "success": True,
        "result": {
            "status": "SUCCESS",
            "openConversationId": "cid-direct-claire",
        },
    }

    result = deliver_meeting_alignment(
        send_decision(target="direct", mention_names=["A"]),
        meeting_source(one_to_one=True),
        dws,
    )

    assert (
        meeting_alignment_delivery.meeting_delivery_conversation_id(result)
        == "cid-direct-claire"
    )


def test_one_to_one_unresolved_identity_retries_without_sending():
    dws = FakeDws()
    dws.profiles["A"] = [
        DwsUserProfile(user_id="u-a-1", name="A", open_dingtalk_id="open-a-1"),
        DwsUserProfile(user_id="u-a-2", name="A", open_dingtalk_id="open-a-2"),
    ]
    decision = send_decision(target="direct", mention_names=["A"])
    payload = decision.model_dump()
    payload["target"]["direct_user_id"] = ""

    with pytest.raises(MeetingDeliveryRetry, match="1:1 target identity"):
        deliver_meeting_alignment(
            MeetingAlignmentDecision.model_validate(payload),
            meeting_source(one_to_one=True, unresolved_other=True),
            dws,
        )

    assert dws.sent == []


def test_ambiguous_mention_is_omitted_without_blocking_group_delivery():
    dws = FakeDws()
    dws.profiles["张三"] = [
        DwsUserProfile(user_id="u-1", name="张三", open_dingtalk_id="open-1"),
        DwsUserProfile(user_id="u-2", name="张三", open_dingtalk_id="open-2"),
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["张三"]), meeting_source(), dws
    )

    assert result.status == "sent"
    assert result.resolved_mentions == []
    assert result.unresolved_mention_names == ["张三"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == []


def test_non_participant_mention_is_not_resolved_from_directory_only():
    dws = FakeDws()
    dws.profiles["曹督军"] = [
        DwsUserProfile(
            user_id="u-cao",
            name="曹督军",
            open_dingtalk_id="open-cao",
        )
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["曹督军"]),
        meeting_source(),
        dws,
    )

    assert result.resolved_mentions == []
    assert result.unresolved_mention_names == ["曹督军"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == []


def test_non_participant_mention_is_allowed_when_transcript_assigns_task():
    dws = FakeDws()
    dws.profiles["曹督军"] = [
        DwsUserProfile(
            user_id="u-cao",
            name="曹督军",
            open_dingtalk_id="open-cao",
        )
    ]
    payload = meeting_source().model_dump()
    payload["transcript"] = [
        {
            "speaker_name": "A",
            "speaker_user_id": "u-a",
            "timestamp": "00:10:00",
            "text": "客户 sample 时间这个任务交给曹督军负责确认。",
        }
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["曹督军"]),
        MeetingSource.model_validate(payload),
        dws,
    )

    assert [mention.open_dingtalk_id for mention in result.resolved_mentions] == [
        "open-cao"
    ]
    assert result.unresolved_mention_names == []
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-cao"]


def test_duplicate_canonical_participant_names_are_inherently_ambiguous():
    dws = FakeDws()
    source_payload = meeting_source().model_dump()
    source_payload["participants"].append(
        {
            "name": " a ",
            "user_id": "u-a-duplicate",
            "open_dingtalk_id": "open-a-duplicate",
        }
    )
    dws.profiles["A"] = [
        DwsUserProfile(user_id="u-a", name="A", open_dingtalk_id="open-a")
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["A"]),
        MeetingSource.model_validate(source_payload),
        dws,
    )

    assert result.resolved_mentions == []
    assert result.unresolved_mention_names == ["A"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == []
    assert dws.search_queries == []


def test_participant_user_id_mismatch_is_not_replaced_by_name_match():
    dws = FakeDws()
    source = meeting_source()
    payload = source.model_dump()
    payload["participants"][1]["open_dingtalk_id"] = ""
    dws.profiles["A"] = [
        DwsUserProfile(
            user_id="u-someone-else",
            name="A",
            open_dingtalk_id="open-someone-else",
        )
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["A"]),
        MeetingSource.model_validate(payload),
        dws,
    )

    assert result.resolved_mentions == []
    assert result.unresolved_mention_names == ["A"]


def test_single_fuzzy_name_match_is_not_selected():
    dws = FakeDws()
    dws.profiles["张三丰"] = [
        DwsUserProfile(user_id="u-1", name="张三", open_dingtalk_id="open-1")
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["张三丰"]), meeting_source(), dws
    )

    assert result.resolved_mentions == []
    assert result.unresolved_mention_names == ["张三丰"]


def test_department_and_title_context_uniquely_resolve_same_name():
    dws = FakeDws()
    dws.profiles["张三 销售 总监"] = [
        DwsUserProfile(
            user_id="u-1",
            name="张三",
            title="工程师",
            department_names={"研发"},
            open_dingtalk_id="open-1",
        ),
        DwsUserProfile(
            user_id="u-2",
            name="张三",
            title="总监",
            department_names={"销售"},
            open_dingtalk_id="open-2",
        ),
    ]
    source_payload = meeting_source().model_dump()
    source_payload["transcript"] = [
        {
            "speaker_name": "A",
            "speaker_user_id": "u-a",
            "timestamp": "00:11:00",
            "text": "客户报价这件事交给张三 销售 总监负责确认。",
        }
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["张三 销售 总监"]),
        MeetingSource.model_validate(source_payload),
        dws,
    )

    assert [mention.open_dingtalk_id for mention in result.resolved_mentions] == [
        "open-2"
    ]


def test_recent_group_sender_disambiguates_same_name():
    dws = FakeDws()
    dws.profiles["张三"] = [
        DwsUserProfile(user_id="u-1", name="张三", open_dingtalk_id="open-1"),
        DwsUserProfile(user_id="u-2", name="张三", open_dingtalk_id="open-2"),
    ]
    dws.recent_messages = [message("张三", user_id="u-2", open_id="open-2")]
    source_payload = meeting_source().model_dump()
    source_payload["transcript"] = [
        {
            "speaker_name": "A",
            "speaker_user_id": "u-a",
            "timestamp": "00:11:00",
            "text": "这个任务后面找张三跟进。",
        }
    ]

    result = deliver_meeting_alignment(
        send_decision(mention_names=["张三"]),
        MeetingSource.model_validate(source_payload),
        dws,
    )

    assert [mention.open_dingtalk_id for mention in result.resolved_mentions] == [
        "open-2"
    ]


def test_open_task_id_success_is_queried_without_second_send():
    dws = FakeDws()
    dws.send_result = {"success": True, "result": {"openTaskId": "task-1"}}

    result = deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert result.status == "sent"
    assert dws.status_queries == ["task-1"]
    assert len(dws.sent) == 1


def test_confirmed_send_failure_retries_without_second_send():
    dws = FakeDws()
    dws.send_result = {"success": True, "result": {"openTaskId": "task-1"}}
    dws.send_status_result = {"success": True, "result": {"status": "FAILED"}}

    with pytest.raises(MeetingDeliveryRetry, match="confirmed failed") as caught:
        deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert caught.value.result.send_verification["open_task_id"] == "task-1"
    assert len(dws.sent) == 1


def test_send_without_verifiable_id_raises_ambiguous_outcome_once():
    dws = FakeDws()
    dws.send_result = {"success": True, "result": {}}

    with pytest.raises(MeetingDeliveryAmbiguous, match="ambiguous") as caught:
        deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert caught.value.result.status == "ambiguous"
    assert caught.value.result.send_result == dws.send_result
    assert len(dws.sent) == 1


@pytest.mark.parametrize(
    "send_error",
    [
        DwsError("connection reset"),
        subprocess.TimeoutExpired(["dws", "chat", "message", "send"], 30),
    ],
)
def test_send_transport_uncertainty_is_auditable_and_never_resent(send_error):
    dws = FakeDws()
    dws.send_error = send_error

    with pytest.raises(MeetingDeliveryAmbiguous, match="ambiguous") as caught:
        deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert caught.value.result.status == "ambiguous"
    assert caught.value.result.send_result == {}
    assert caught.value.result.send_verification["state"] == "ambiguous"
    assert "connection reset" in caught.value.result.send_verification["send_error"] \
        or "timed out" in caught.value.result.send_verification["send_error"]
    assert len(dws.sent) == 1


def test_status_query_uncertainty_preserves_original_task_and_never_resends():
    dws = FakeDws()
    dws.send_result = {"success": True, "result": {"openTaskId": "task-1"}}
    dws.verify_error = DwsError("status query timed out")

    with pytest.raises(MeetingDeliveryAmbiguous) as caught:
        deliver_meeting_alignment(send_decision(), meeting_source(), dws)

    assert caught.value.result.send_result == dws.send_result
    assert caught.value.result.send_verification["open_task_id"] == "task-1"
    assert caught.value.result.send_verification["status_error"] == (
        "status query timed out"
    )
    assert len(dws.sent) == 1
