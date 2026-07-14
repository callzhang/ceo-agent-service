from datetime import datetime
from zoneinfo import ZoneInfo

from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from app.org_cache import (
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from app.store import AutoReplyStore
from app.worker import DingTalkAutoReplyWorker, PROCESSING_ACK
from app.dws_client import DwsUserProfile
from app.dws_client import DwsCalendarAttendee, DwsCalendarEvent
from app.audit_web import render_attempt_list
from app.meeting_alignment import (
    consume_meeting_alignment_jobs,
    produce_meeting_alignment_jobs,
)
from app.meeting_alignment_models import MeetingAlignmentDecision


def fixed_worker_now():
    return datetime(2026, 5, 13, 10, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeDws:
    def __init__(self):
        self.sent = []
        self.sent_at_users = []
        self.dings = []
        self.created_text_emotions = []
        self.message_text_emotions = []
        self.org_calls = []
        self.chat_calls = []
        self.conversation = DingTalkConversation(
            open_conversation_id="cid-1",
            title="HR direct",
            single_chat=True,
            unread_point=1,
        )
        self.message = DingTalkMessage(
            open_conversation_id="cid-1",
            open_message_id="msg-1",
            conversation_title="HR direct",
            single_chat=True,
            sender_name="HR",
            sender_open_dingtalk_id="open-hr",
            sender_user_id="hr-user",
            create_time="2026-05-13 18:00:00",
            content="张三转正怎么看？",
        )

    def get_current_user_id(self):
        self.org_calls.append("get_current_user_id")
        return "principal-user"

    def auth_status(self):
        return {
            "authenticated": True,
            "token_valid": True,
            "refresh_token_valid": True,
        }

    def search_department_ids(self, query):
        self.org_calls.append(("search_department_ids", query))
        return {"hr-dept"}

    def list_department_member_profiles(self, department_ids):
        self.org_calls.append(
            ("list_department_member_profiles", tuple(department_ids))
        )
        return [
            DwsUserProfile(
                user_id="hr-user",
                name="HR",
                open_dingtalk_id="open-hr",
                manager_user_id=None,
                department_ids={"hr-dept"},
            )
        ]

    def get_user_profiles(self, user_ids):
        self.org_calls.append(("get_user_profiles", tuple(user_ids)))
        profiles = {
            "principal-user": DwsUserProfile(
                user_id="principal-user",
                name="Alex",
                open_dingtalk_id="open-principal",
                manager_user_id=None,
                department_ids={"exec-dept"},
            ),
            "hr-user": DwsUserProfile(
                user_id="hr-user",
                name="HR",
                open_dingtalk_id="open-hr",
                manager_user_id=None,
                department_ids={"hr-dept"},
            ),
            "subject-user": DwsUserProfile(
                user_id="subject-user",
                name="张三",
                open_dingtalk_id="open-subject",
                manager_user_id="manager-user",
                department_ids={"dept-1"},
            ),
            "manager-user": DwsUserProfile(
                user_id="manager-user",
                name="经理",
                open_dingtalk_id="open-manager",
                manager_user_id=None,
                department_ids={"dept-1"},
            ),
        }
        return [profiles[user_id] for user_id in user_ids if user_id in profiles]

    def list_unread_conversations(self, count):
        self.chat_calls.append(("list_unread_conversations", count))
        return [self.conversation]

    def read_recent_messages(self, conversation, limit=50):
        self.chat_calls.append(
            ("read_recent_messages", conversation.open_conversation_id, limit)
        )
        return [self.message]

    def read_unread_messages(self, conversation):
        self.chat_calls.append(
            ("read_unread_messages", conversation.open_conversation_id)
        )
        return [self.message]

    def read_mentioned_messages(
        self,
        conversation=None,
        limit=50,
        cursor="0",
        lookback_hours=24,
    ):
        self.chat_calls.append(("read_mentioned_messages", limit, cursor, lookback_hours))
        return []

    def minutes_permission_request_from_message(self, message):
        self.chat_calls.append(("minutes_permission_request_from_message", message.open_message_id))
        return None

    def calendar_invite_from_message(self, message):
        self.chat_calls.append(("calendar_invite_from_message", message.open_message_id))
        return None

    def list_calendar_events(self, start, end):
        self.chat_calls.append(("list_calendar_events", start, end))
        return []

    def send_message(
        self,
        conversation_id,
        text,
        at_users=None,
        at_open_dingtalk_ids=None,
        at_open_dingtalk_names=None,
        user_id=None,
        open_dingtalk_id=None,
    ):
        del at_open_dingtalk_names
        self.chat_calls.append(("send_message", conversation_id))
        self.sent.append((conversation_id, text))
        self.sent_at_users.append(at_open_dingtalk_ids or at_users or [])

    def reply_message(
        self,
        conversation_id,
        ref_message_id,
        ref_sender_open_dingtalk_id,
        text,
        at_users=None,
    ):
        self.chat_calls.append(("reply_message", conversation_id, ref_message_id))
        self.sent.append((conversation_id, text))
        self.sent_at_users.append(at_users or [])

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text,
        at_users=None,
        at_open_dingtalk_ids=None,
        at_open_dingtalk_names=None,
    ):
        del at_open_dingtalk_ids, at_open_dingtalk_names
        return self.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
            at_users=at_users,
        )

    def ding_user(self, user_id, text):
        self.chat_calls.append(("ding_user", user_id))
        self.dings.append((user_id, text))

    def create_message_text_emotion(
        self,
        *,
        text,
        emotion_name,
        background_id="",
    ):
        self.created_text_emotions.append((text, emotion_name, background_id))
        return {
            "emotionId": f"created-{len(self.created_text_emotions)}",
            "backgroundId": "created-bg",
        }

    def add_message_text_emotion(
        self,
        conversation_id,
        message_id,
        *,
        text,
        emotion_id,
        emotion_name,
        background_id,
    ):
        self.message_text_emotions.append(
            (
                conversation_id,
                message_id,
                text,
                emotion_id,
                emotion_name,
                background_id,
            )
        )
        return {"success": True}


class FakeCodex:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []
        self.last_session_id = None

    def decide(self, prompt, session_id, image_paths=None):
        self.calls.append((prompt, session_id, image_paths or []))
        self.last_session_id = "session-1"
        return self.decision


def final_sent(dws: FakeDws):
    return [sent for sent in dws.sent if sent[1] != PROCESSING_ACK]


def final_sent_at_users(dws: FakeDws):
    return [
        at_users
        for sent, at_users in zip(dws.sent, dws.sent_at_users)
        if sent[1] != PROCESSING_ACK
    ]


class FakeMeetingDws:
    def __init__(self):
        self.sent = []
        self.meeting = {
            "taskUuid": "minutes-e2e-1",
            "title": "上线评审",
            "startTimeISO": "2026-07-14T09:00:00+08:00",
            "endTimeISO": "2026-07-14T10:00:00+08:00",
            "status": "ended",
        }
        self.calendar_event = DwsCalendarEvent(
            event_id="event-e2e-1",
            title="上线评审",
            start_time="2026-07-14T09:00:00+08:00",
            end_time="2026-07-14T10:00:00+08:00",
            status="confirmed",
            attendee_details=[
                DwsCalendarAttendee(
                    display_name="Derek",
                    is_self=True,
                    user_id="u-derek",
                    open_dingtalk_id="open-derek",
                ),
                DwsCalendarAttendee(
                    display_name="A",
                    user_id="u-a",
                    open_dingtalk_id="open-a",
                ),
                DwsCalendarAttendee(
                    display_name="B",
                    user_id="u-b",
                    open_dingtalk_id="open-b",
                ),
            ],
        )

    def list_minutes_page(self, *, limit, cursor, start, end):
        return {"items": [self.meeting], "has_more": False, "next_token": ""}

    def get_minutes_info(self, meeting_id):
        assert meeting_id == "minutes-e2e-1"
        return self.meeting

    def get_current_user_id(self):
        return "u-derek"

    def list_calendar_events_page(self, *, start, end, limit, cursor):
        return {
            "events": [self.calendar_event],
            "has_more": False,
            "next_cursor": "",
        }

    def get_minutes_summary(self, meeting_id):
        return {"result": {"fullSummary": "A 主张全量，B 主张灰度，尚未一致。"}}

    def get_all_minutes_transcription(self, meeting_id):
        return {
            "paragraphs": [
                {"nickName": "A", "paragraph": "全量上线效率最高。"},
                {"nickName": "B", "paragraph": "灰度上线风险更低。"},
                {"nickName": "Derek", "paragraph": "先明确风险预算。"},
            ]
        }

    def get_conversation_info(self, conversation_id):
        assert conversation_id == "cid-first"
        return {
            "openConversationId": "cid-first",
            "title": "项目群",
            "singleChat": False,
            "memberCount": 3,
        }

    def search_user_profiles(self, query):
        return []

    def read_recent_messages(self, conversation, limit=50):
        return []

    def send_message(self, conversation_id, text, **kwargs):
        self.sent.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return {"success": True, "result": {"openMessageId": "msg-meeting-e2e"}}

    def verify_message_send_result(self, send_result):
        return {"state": "sent", "open_task_id": "", "status_result": {}}


class FakeMeetingRunner:
    last_session_id = "meeting-e2e-session"
    last_transcript_start_line = 0
    last_transcript_end_line = 10
    last_audit_tool_events = []

    def decide(self, *, prompt):
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
                    {
                        "question": "可接受的最大故障半径是多少？",
                        "answer_owner_names": ["A", "B"],
                    }
                ],
                "mention_names": ["A", "B"],
                "target": {
                    "kind": "group",
                    "conversation_id": "cid-first",
                    "direct_user_id": "",
                    "title": "项目群",
                    "candidates": [
                        {
                            "conversation_id": "cid-first",
                            "title": "项目群",
                            "evidence": ["参会人和主题最匹配"],
                        },
                        {
                            "conversation_id": "cid-second",
                            "title": "备用群",
                            "evidence": ["参会人部分重合"],
                        },
                    ],
                },
                "final_message": "会后对齐：@A @B 可接受的最大故障半径是多少？",
                "audit_summary": "发现上线范围分歧，尚未明确对齐。",
                "confidence": 0.9,
            }
        )


def test_meeting_alignment_pipeline_sends_once_and_appears_in_history(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeMeetingDws()
    runner = FakeMeetingRunner()
    now = datetime.fromisoformat("2026-07-14T10:11:00+08:00")

    assert produce_meeting_alignment_jobs(store, dws, now=now) == 1
    assert consume_meeting_alignment_jobs(store, dws, runner, now=now, limit=1) == 1

    job = store.get_meeting_alignment_job_by_meeting_id("minutes-e2e-1")
    assert job is not None
    assert job.status == "sent"
    assert len(store.list_meeting_alignment_runs(job.id)) == 1
    assert len(dws.sent) == 1
    assert dws.sent[0]["conversation_id"] == "cid-first"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-a", "open-b"]
    assert "会后对齐" in render_attempt_list(store)

    assert produce_meeting_alignment_jobs(store, dws, now=now) == 0
    assert consume_meeting_alignment_jobs(store, dws, runner, now=now, limit=1) == 0
    assert len(dws.sent) == 1


def test_local_pipeline_refreshes_org_cache_then_replies_without_runtime_org_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    raw_dws = FakeDws()
    refresh_org_cache(store, raw_dws, user_ids={"hr-user", "subject-user"})
    raw_dws.org_calls.clear()
    cached_dws = CachedDwsClient(raw_dws, CachedOrgDirectory(store))
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="建议先观察一个月",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user",
        )
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=cached_dws,
        codex=codex,
        dry_run=False,
        now_provider=fixed_worker_now,
    )

    worker.run_once()

    assert raw_dws.org_calls == []
    assert final_sent(raw_dws) == [
        ("cid-1", "建议先观察一个月（by明哥分身）")
    ]
    assert final_sent_at_users(raw_dws) == [["hr-user"]]
    assert store.has_seen("msg-1") is True
    assert store.get_codex_session_id("cid-1") == "session-1"


def test_local_pipeline_handoff_reacts_and_dings_without_runtime_org_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.worker.send_macos_notification", lambda **_: None
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    raw_dws = FakeDws()
    raw_dws.message.content = "不要分身，真人看一下"
    refresh_org_cache(store, raw_dws, user_ids={"hr-user"})
    raw_dws.org_calls.clear()
    cached_dws = CachedDwsClient(raw_dws, CachedOrgDirectory(store))
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=cached_dws,
        codex=FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN)),
        dry_run=False,
        now_provider=fixed_worker_now,
    )

    worker.run_once()

    assert raw_dws.org_calls == []
    assert final_sent(raw_dws) == []
    assert raw_dws.created_text_emotions == [("我去叫", "我去叫", "im_bg_5")]
    assert raw_dws.message_text_emotions == [
        ("cid-1", "msg-1", "我去叫", "created-1", "我去叫", "created-bg")
    ]
    assert raw_dws.dings == [
        (
            "principal-user",
            "HR direct\nHR: 不要分身，真人看一下\nprevious split-person reply: none",
        )
    ]
