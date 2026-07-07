import pytest

from app.dingtalk_models import DingTalkMessage
from app.dws_client import DwsError, DwsUserProfile
from app.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from app.store import AutoReplyStore


def message(
    sender_user_id: str | None = None,
    sender_open_dingtalk_id: str | None = "open-1",
    sender_name: str = "张三",
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name=sender_name,
        sender_open_dingtalk_id=sender_open_dingtalk_id,
        sender_user_id=sender_user_id,
        create_time="2026-05-13 18:00:00",
        content="hi",
    )


def test_cached_directory_resolves_sender_from_cache(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_org_user_profile(
        user_id="user-1",
        name="张三",
        open_dingtalk_id="open-1",
        manager_user_id=None,
        department_ids={"dept-1"},
    )
    directory = CachedOrgDirectory(store)

    assert directory.resolve_message_sender(message()) == "user-1"


def test_cached_directory_rejects_missing_profile(tmp_path):
    directory = CachedOrgDirectory(AutoReplyStore(tmp_path / "worker.sqlite3"))

    with pytest.raises(DwsError, match="cache"):
        directory.resolve_message_sender(message())


def test_cached_directory_checks_hr_and_manager_chain(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_hr_department_ids({"hr-dept"})
    store.upsert_org_user_profile(
        user_id="hr-user",
        name="HR",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids={"hr-dept"},
    )
    store.upsert_org_user_profile(
        user_id="subject",
        name="员工",
        open_dingtalk_id=None,
        manager_user_id="manager-1",
        department_ids={"dept-1"},
    )
    store.upsert_org_user_profile(
        user_id="manager-1",
        name="上级",
        open_dingtalk_id=None,
        manager_user_id="manager-2",
        department_ids={"dept-1"},
    )
    directory = CachedOrgDirectory(store)

    assert directory.is_hr_user("hr-user") is True
    assert directory.user_in_manager_chain("manager-2", "subject") is True


def test_cached_directory_current_user_uses_cache_metadata(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user")
    directory = CachedOrgDirectory(store)

    assert directory.is_current_user_message(message(sender_user_id="principal-user")) is True


def test_cached_directory_current_user_does_not_use_display_name(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user")
    store.upsert_org_user_profile(
        user_id="principal-user",
        name="明哥",
        open_dingtalk_id="principal-open-id",
        manager_user_id=None,
        department_ids=set(),
    )
    directory = CachedOrgDirectory(store)

    assert (
        directory.is_current_user_message(
            message(
                sender_user_id=None,
                sender_open_dingtalk_id=None,
                sender_name="明哥",
            )
        )
        is False
    )


def test_cached_dws_client_delegates_message_io_and_uses_cached_org(tmp_path):
    class FakeDws:
        def __init__(self):
            self.sent = []
            self.replies = []
            self.dings = []
            self.emojis = []
            self.text_emotions = []
            self.created_text_emotions = []
            self.doc_comments = []
            self.markdown_docs = []
            self.doc_editor_permissions = []
            self.robot_direct_reads = []
            self.bot_direct_messages = []
            self.bot_group_messages = []

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
            self.sent.append(
                (
                    conversation_id,
                    text,
                    at_users or [],
                    at_open_dingtalk_ids or [],
                    at_open_dingtalk_names or [],
                )
            )

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
            at_users=None,
        ):
            self.replies.append(
                (
                    conversation_id,
                    ref_message_id,
                    ref_sender_open_dingtalk_id,
                    text,
                    at_users or [],
                )
            )

        def ding_user(self, user_id, text):
            self.dings.append((user_id, text))

        def add_message_emoji(self, conversation_id, message_id, emoji):
            self.emojis.append((conversation_id, message_id, emoji))
            return {"success": True}

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
            self.text_emotions.append(
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

        def create_message_text_emotion(
            self,
            *,
            text,
            emotion_name,
            background_id="",
        ):
            self.created_text_emotions.append((text, emotion_name, background_id))
            return {"emotionId": "created-1"}

        def create_doc_comment(self, node_id, content):
            self.doc_comments.append((node_id, content))
            return {"result": {"commentKey": "comment-1"}}

        def create_markdown_doc(self, name, content):
            self.markdown_docs.append((name, content))
            return {"result": {"url": "https://alidocs.example/doc-1"}}

        def add_doc_editor_permission(self, node, user_ids):
            self.doc_editor_permissions.append((node, user_ids))
            return {"success": True}

        def read_robot_direct_messages(self, *, lookback_minutes=30, limit=100):
            self.robot_direct_reads.append((lookback_minutes, limit))
            return ["robot-message"]

        def send_direct_message_by_bot(self, user_id, text):
            self.bot_direct_messages.append((user_id, text))
            return {"success": True}

        def send_group_message_by_bot(self, conversation_id, text):
            self.bot_group_messages.append((conversation_id, text))
            return {"success": True}

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user")
    cached = CachedDwsClient(FakeDws(), CachedOrgDirectory(store))

    cached.send_message("cid-1", "ok", at_users=["user-1"])
    cached.reply_message("cid-1", "msg-1", "open-1", "reply")
    cached.add_message_emoji("cid-1", "msg-1", "👍")
    cached.add_message_text_emotion(
        "cid-1",
        "msg-1",
        text="收到",
        emotion_id="ok",
        emotion_name="OK",
        background_id="blue",
    )
    cached.create_message_text_emotion(
        text="我去摇人",
        emotion_name="我去摇人",
        background_id="im_bg_5",
    )
    cached.create_doc_comment("https://alidocs.example/doc", "处理好了")
    cached.create_markdown_doc("CEO回复", "# 正文")
    cached.add_doc_editor_permission("doc-1", ["user-1"])
    robot_messages = cached.read_robot_direct_messages(
        lookback_minutes=240,
        limit=50,
    )
    cached.send_direct_message_by_bot("user-1", "你好")
    cached.send_group_message_by_bot("cid-1", "群消息")
    cached.ding_self("handoff")

    assert cached.dws.sent == [("cid-1", "ok", ["user-1"], [], [])]
    assert cached.dws.replies == [("cid-1", "msg-1", "open-1", "reply", [])]
    assert cached.dws.emojis == [("cid-1", "msg-1", "👍")]
    assert cached.dws.text_emotions == [
        ("cid-1", "msg-1", "收到", "ok", "OK", "blue")
    ]
    assert cached.dws.created_text_emotions == [("我去摇人", "我去摇人", "im_bg_5")]
    assert cached.dws.doc_comments == [
        ("https://alidocs.example/doc", "处理好了")
    ]
    assert cached.dws.markdown_docs == [("CEO回复", "# 正文")]
    assert cached.dws.doc_editor_permissions == [("doc-1", ["user-1"])]
    assert robot_messages == ["robot-message"]
    assert cached.dws.robot_direct_reads == [(240, 50)]
    assert cached.dws.bot_direct_messages == [("user-1", "你好")]
    assert cached.dws.bot_group_messages == [("cid-1", "群消息")]
    assert cached.dws.dings == [("principal-user", "handoff")]
    assert cached.is_current_user_message(message(sender_user_id="principal-user")) is True


def test_cached_dws_client_delegates_auth_archives(tmp_path):
    class FakeDws:
        def __init__(self):
            self.export_paths = []
            self.import_paths = []
            self.auth_status_calls = 0
            self.pat_scopes = []

        def export_auth_archive(self, output_path):
            self.export_paths.append(output_path)

        def import_auth_archive(self, input_path):
            self.import_paths.append(input_path)

        def auth_status(self):
            self.auth_status_calls += 1
            return {"authenticated": True}

        def start_pat_authorization(self, scopes):
            self.pat_scopes.append(scopes)
            return "process"

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    fake = FakeDws()
    cached = CachedDwsClient(fake, CachedOrgDirectory(store))
    archive_path = tmp_path / "dws-auth.tar.gz"

    cached.export_auth_archive(archive_path)
    cached.import_auth_archive(archive_path)
    auth_status = cached.auth_status()
    process = cached.start_pat_authorization(["chat.message:list"])

    assert fake.export_paths == [archive_path]
    assert fake.import_paths == [archive_path]
    assert fake.auth_status_calls == 1
    assert auth_status == {"authenticated": True}
    assert fake.pat_scopes == [["chat.message:list"]]
    assert process == "process"


def test_cached_dws_client_delegates_linked_material_reads(tmp_path):
    class FakeDws:
        def __init__(self):
            self.calls = []

        def doc_info(self, node):
            self.calls.append(("doc_info", node))
            return {"extension": "able"}

        def read_doc(self, node):
            self.calls.append(("read_doc", node))
            return {"markdown": "正文"}

        def get_aitable_base(self, base_id):
            self.calls.append(("get_aitable_base", base_id))
            return {"data": {"baseName": "看板"}}

        def get_aitable_tables(self, base_id, table_ids=None):
            self.calls.append(("get_aitable_tables", base_id, table_ids))
            return {"data": {"tables": []}}

        def query_aitable_records(self, base_id, table_id, limit=10):
            self.calls.append(("query_aitable_records", base_id, table_id, limit))
            return {"data": {"records": []}}

        def get_resource_download_url(
            self,
            open_conversation_id,
            open_message_id,
            resource_id,
            resource_type,
        ):
            self.calls.append(
                (
                    "get_resource_download_url",
                    open_conversation_id,
                    open_message_id,
                    resource_id,
                    resource_type,
                )
            )
            return {"downloadUrl": "https://example.test/image.png"}

        def download_robot_message_file(self, download_code):
            self.calls.append(("download_robot_message_file", download_code))
            return {"downloadUrl": "https://example.test/file.png"}

    cached = CachedDwsClient(
        FakeDws(),
        CachedOrgDirectory(AutoReplyStore(tmp_path / "worker.sqlite3")),
    )

    assert cached.doc_info("node-1") == {"extension": "able"}
    assert cached.read_doc("node-1") == {"markdown": "正文"}
    assert cached.get_aitable_base("base-1") == {"data": {"baseName": "看板"}}
    assert cached.get_aitable_tables("base-1", ["tbl-1"]) == {
        "data": {"tables": []}
    }
    assert cached.query_aitable_records("base-1", "tbl-1", 5) == {
        "data": {"records": []}
    }
    assert cached.get_resource_download_url("cid-1", "msg-1", "res-1", "image") == {
        "downloadUrl": "https://example.test/image.png"
    }
    assert cached.download_robot_message_file("download-code-1") == {
        "downloadUrl": "https://example.test/file.png"
    }
    assert cached.dws.calls == [
        ("doc_info", "node-1"),
        ("read_doc", "node-1"),
        ("get_aitable_base", "base-1"),
        ("get_aitable_tables", "base-1", ["tbl-1"]),
        ("query_aitable_records", "base-1", "tbl-1", 5),
        (
            "get_resource_download_url",
            "cid-1",
            "msg-1",
            "res-1",
            "image",
        ),
        ("download_robot_message_file", "download-code-1"),
    ]


def test_cached_dws_client_delegates_external_action_helpers(tmp_path):
    request = object()
    event = object()

    class FakeDws:
        def __init__(self):
            self.calls = []

        def minutes_permission_request_from_message(self, msg):
            self.calls.append(("minutes_permission_request_from_message", msg))
            return request

        def add_minutes_member_permission(self, permission_request):
            self.calls.append(("add_minutes_member_permission", permission_request))
            return {"success": True}

        def get_minutes_info(self, task_uuid):
            self.calls.append(("get_minutes_info", task_uuid))
            return {"result": {"taskUuid": task_uuid}}

        def get_minutes_summary(self, task_uuid):
            self.calls.append(("get_minutes_summary", task_uuid))
            return {"result": {"summary": "ok"}}

        def get_minutes_todos(self, task_uuid):
            self.calls.append(("get_minutes_todos", task_uuid))
            return {"result": {"actions": []}}

        def get_minutes_transcription(self, task_uuid, *, next_token=""):
            self.calls.append(("get_minutes_transcription", task_uuid, next_token))
            return {"result": {"paragraphList": []}}

        def calendar_invite_from_message(self, msg):
            self.calls.append(("calendar_invite_from_message", msg))
            return event

        def list_calendar_events(self, start, end):
            self.calls.append(("list_calendar_events", start, end))
            return [event]

        def get_calendar_event(self, event_id):
            self.calls.append(("get_calendar_event", event_id))
            return event

        def respond_calendar_event(self, event_id, response_status):
            self.calls.append(("respond_calendar_event", event_id, response_status))
            return {"success": True}

        def comment_oa_approval(self, process_instance_id, text):
            self.calls.append(("comment_oa_approval", process_instance_id, text))
            return {"success": True}

    cached = CachedDwsClient(
        FakeDws(),
        CachedOrgDirectory(AutoReplyStore(tmp_path / "worker.sqlite3")),
    )
    msg = message()

    assert cached.minutes_permission_request_from_message(msg) is request
    assert cached.add_minutes_member_permission(request) == {"success": True}
    assert cached.get_minutes_info("minutes-1") == {
        "result": {"taskUuid": "minutes-1"}
    }
    assert cached.get_minutes_summary("minutes-1") == {"result": {"summary": "ok"}}
    assert cached.get_minutes_todos("minutes-1") == {"result": {"actions": []}}
    assert cached.get_minutes_transcription("minutes-1", next_token="n1") == {
        "result": {"paragraphList": []}
    }
    assert cached.calendar_invite_from_message(msg) is event
    assert cached.list_calendar_events("start", "end") == [event]
    assert cached.get_calendar_event("event-1") is event
    assert cached.respond_calendar_event("event-1", "accepted") == {"success": True}
    assert cached.comment_oa_approval("proc-1", "请补材料") == {"success": True}
    assert cached.dws.calls == [
        ("minutes_permission_request_from_message", msg),
        ("add_minutes_member_permission", request),
        ("get_minutes_info", "minutes-1"),
        ("get_minutes_summary", "minutes-1"),
        ("get_minutes_todos", "minutes-1"),
        ("get_minutes_transcription", "minutes-1", "n1"),
        ("calendar_invite_from_message", msg),
        ("list_calendar_events", "start", "end"),
        ("get_calendar_event", "event-1"),
        ("respond_calendar_event", "event-1", "accepted"),
        ("comment_oa_approval", "proc-1", "请补材料"),
    ]


def test_cached_dws_client_resolves_and_caches_sender_on_cache_miss(tmp_path):
    class FakeDws:
        def __init__(self):
            self.resolved = []

        def resolve_message_sender(self, msg):
            self.resolved.append(msg.sender_open_dingtalk_id)
            return "user-1"

        def get_user_profile(self, user_id):
            assert user_id == "user-1"
            return DwsUserProfile(
                user_id="user-1",
                name="张三",
                manager_user_id="manager-1",
                department_ids={"dept-1"},
            )

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    cached = CachedDwsClient(FakeDws(), CachedOrgDirectory(store))

    assert cached.resolve_message_sender(message()) == "user-1"
    assert cached.dws.resolved == ["open-1"]
    profile = store.find_org_user_by_open_dingtalk_id("open-1")
    assert profile is not None
    assert profile.user_id == "user-1"
    assert profile.name == "张三"


def test_cached_dws_client_current_user_check_uses_live_sender_resolution_on_cache_miss(
    tmp_path,
):
    class FakeDws:
        def __init__(self):
            self.resolved = []

        def resolve_message_sender(self, msg):
            self.resolved.append(msg.sender_open_dingtalk_id)
            return "principal-user"

        def get_user_profile(self, user_id):
            assert user_id == "principal-user"
            return DwsUserProfile(
                user_id="principal-user",
                name="Alex Chen",
                manager_user_id=None,
                department_ids={"dept-1"},
            )

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user")
    cached = CachedDwsClient(FakeDws(), CachedOrgDirectory(store))

    assert cached.is_current_user_message(message()) is True
    assert cached.dws.resolved == ["open-1"]
    assert store.find_org_user_by_open_dingtalk_id("open-1") is not None


def test_cached_dws_client_checks_live_hr_membership_when_hr_cache_missing(tmp_path):
    class FakeDws:
        def __init__(self):
            self.checked_user_ids = []

        def get_user_profile(self, user_id):
            assert user_id == "hr-user"
            return DwsUserProfile(
                user_id="hr-user",
                name="HR",
                manager_user_id=None,
                department_ids={"hr-dept"},
            )

        def is_hr_user(self, user_id):
            self.checked_user_ids.append(user_id)
            return user_id == "hr-user"

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_org_user_profile(
        user_id="hr-user",
        name="HR",
        open_dingtalk_id=None,
        manager_user_id=None,
        department_ids={"hr-dept"},
    )
    cached = CachedDwsClient(FakeDws(), CachedOrgDirectory(store))

    assert cached.is_hr_user("hr-user") is True
    assert cached.dws.checked_user_ids == ["hr-user"]


def test_cached_dws_client_ding_fails_closed_without_cached_current_user(tmp_path):
    class FakeDws:
        def get_current_user_id(self):
            raise AssertionError("runtime must not query current user from dws")

    cached = CachedDwsClient(
        FakeDws(),
        CachedOrgDirectory(AutoReplyStore(tmp_path / "worker.sqlite3")),
    )

    with pytest.raises(DwsError, match="current user cache"):
        cached.ding_self("handoff")


def test_refresh_org_cache_updates_current_user_hr_departments_and_known_users(tmp_path):
    class FakeDws:
        def __init__(self):
            self.requested_user_ids = []

        def get_current_user_id(self):
            return "principal-user"

        def search_department_ids(self, query):
            assert query == "人力资源"
            return {"hr-dept"}

        def list_department_member_profiles(self, department_ids):
            assert department_ids == ["hr-dept"]
            return [
                DwsUserProfile(
                    user_id="hr-user",
                    name="HR",
                    manager_user_id=None,
                    department_ids={"hr-dept"},
                )
            ]

        def get_user_profiles(self, user_ids):
            self.requested_user_ids.append(set(user_ids))
            profiles = {
                "principal-user": DwsUserProfile(
                    user_id="principal-user",
                    name="Alex",
                    manager_user_id=None,
                    department_ids={"exec-dept"},
                ),
                "subject": DwsUserProfile(
                    user_id="subject",
                    name="员工",
                    manager_user_id="manager-1",
                    department_ids={"dept-1"},
                ),
                "manager-1": DwsUserProfile(
                    user_id="manager-1",
                    name="经理",
                    manager_user_id=None,
                    department_ids={"dept-1"},
                ),
            }
            return [profiles[user_id] for user_id in user_ids if user_id in profiles]

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    refreshed = refresh_org_cache(store, FakeDws(), user_ids={"subject"})

    assert refreshed >= 4
    assert store.get_current_user_id() == "principal-user"
    assert store.get_hr_department_ids() == {"hr-dept"}
    assert store.get_org_user_profile("subject").manager_user_id == "manager-1"
    assert store.get_org_user_profile("manager-1") is not None
    assert store.get_org_user_profile("hr-user") is not None
    assert store.get_service_state(ORG_CACHE_REFRESHED_DATE_STATE_KEY)


def test_refresh_org_cache_fetches_known_users_in_bounded_batches(tmp_path):
    class FakeDws:
        def __init__(self):
            self.requested_user_ids = []

        def get_current_user_id(self):
            return "principal-user"

        def search_department_ids(self, query):
            assert query == "人力资源"
            return {"hr-dept"}

        def list_department_member_profiles(self, department_ids):
            assert department_ids == ["hr-dept"]
            return []

        def get_user_profiles(self, user_ids):
            self.requested_user_ids.append(list(user_ids))
            return [
                DwsUserProfile(
                    user_id=user_id,
                    name=user_id,
                    manager_user_id=None,
                    department_ids={"dept-1"},
                )
                for user_id in user_ids
            ]

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws()
    user_ids = {f"user-{index:02d}" for index in range(45)}

    refreshed = refresh_org_cache(store, dws, user_ids=user_ids)

    assert refreshed == 46
    assert len(dws.requested_user_ids) == 3
    assert all(len(batch) <= 20 for batch in dws.requested_user_ids)
