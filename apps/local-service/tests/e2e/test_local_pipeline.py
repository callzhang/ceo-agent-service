from datetime import datetime
from zoneinfo import ZoneInfo

from ceo_agent_service.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from ceo_agent_service.org_cache import (
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from ceo_agent_service.store import AutoReplyStore
from ceo_agent_service.worker import DingTalkAutoReplyWorker, PROCESSING_ACK
from ceo_agent_service.dws_client import DwsUserProfile


def fixed_worker_now():
    return datetime(2026, 5, 13, 10, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class FakeDws:
    def __init__(self):
        self.sent = []
        self.sent_at_users = []
        self.dings = []
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
        user_id=None,
        open_dingtalk_id=None,
    ):
        self.chat_calls.append(("send_message", conversation_id))
        self.sent.append((conversation_id, text))
        self.sent_at_users.append(at_users or [])

    def ding_user(self, user_id, text):
        self.chat_calls.append(("ding_user", user_id))
        self.dings.append((user_id, text))


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


def test_local_pipeline_refreshes_org_cache_then_replies_without_runtime_org_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
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
        (
            None,
            "> HR: 张三转正怎么看？\n\n建议先观察一个月（by明哥分身）",
        )
    ]
    assert final_sent_at_users(raw_dws) == [[]]
    assert store.has_seen("msg-1") is True
    assert store.get_codex_session_id("cid-1") == "session-1"


def test_local_pipeline_handoff_ding_uses_cached_current_user_without_runtime_org_calls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "ceo_agent_service.worker.send_macos_notification", lambda **_: None
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
    assert final_sent(raw_dws) == [
        (
            None,
            "> HR: 不要分身，真人看一下\n\n我让明哥本人看一下。（by明哥分身）",
        )
    ]
    assert raw_dws.dings == [
        (
            "principal-user",
            "HR direct\nHR: 不要分身，真人看一下\nprevious split-person reply: none",
        )
    ]
