import pytest

from ceo_agent_service.dingtalk_models import DingTalkMessage
from ceo_agent_service.dws_client import DwsError, DwsUserProfile
from ceo_agent_service.org_cache import (
    ORG_CACHE_REFRESHED_DATE_STATE_KEY,
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from ceo_agent_service.store import AutoReplyStore


def message(sender_user_id: str | None = None) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="张三",
        sender_open_dingtalk_id="open-1",
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
    store.set_current_user_id("derek-user")
    directory = CachedOrgDirectory(store)

    assert directory.is_current_user_message(message(sender_user_id="derek-user")) is True


def test_cached_dws_client_delegates_message_io_and_uses_cached_org(tmp_path):
    class FakeDws:
        def __init__(self):
            self.sent = []
            self.replies = []
            self.dings = []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            self.sent.append((conversation_id, text, at_users or []))

        def reply_message(
            self,
            conversation_id,
            ref_message_id,
            ref_sender_open_dingtalk_id,
            text,
        ):
            self.replies.append(
                (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
            )

        def ding_user(self, user_id, text):
            self.dings.append((user_id, text))

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("derek-user")
    cached = CachedDwsClient(FakeDws(), CachedOrgDirectory(store))

    cached.send_message("cid-1", "ok", at_users=["user-1"])
    cached.reply_message("cid-1", "msg-1", "open-1", "reply")
    cached.ding_self("handoff")

    assert cached.dws.sent == [("cid-1", "ok", ["user-1"])]
    assert cached.dws.replies == [("cid-1", "msg-1", "open-1", "reply")]
    assert cached.dws.dings == [("derek-user", "handoff")]
    assert cached.is_current_user_message(message(sender_user_id="derek-user")) is True


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


def test_cached_dws_client_delegates_calendar_and_minutes_helpers(tmp_path):
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

        def calendar_invite_from_message(self, msg):
            self.calls.append(("calendar_invite_from_message", msg))
            return event

        def list_calendar_events(self, start, end):
            self.calls.append(("list_calendar_events", start, end))
            return [event]

        def respond_calendar_event(self, event_id, response_status):
            self.calls.append(("respond_calendar_event", event_id, response_status))
            return {"success": True}

    cached = CachedDwsClient(
        FakeDws(),
        CachedOrgDirectory(AutoReplyStore(tmp_path / "worker.sqlite3")),
    )
    msg = message()

    assert cached.minutes_permission_request_from_message(msg) is request
    assert cached.add_minutes_member_permission(request) == {"success": True}
    assert cached.calendar_invite_from_message(msg) is event
    assert cached.list_calendar_events("start", "end") == [event]
    assert cached.respond_calendar_event("event-1", "accepted") == {"success": True}
    assert cached.dws.calls == [
        ("minutes_permission_request_from_message", msg),
        ("add_minutes_member_permission", request),
        ("calendar_invite_from_message", msg),
        ("list_calendar_events", "start", "end"),
        ("respond_calendar_event", "event-1", "accepted"),
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
            return "derek-user"

        def get_user_profile(self, user_id):
            assert user_id == "derek-user"
            return DwsUserProfile(
                user_id="derek-user",
                name="Derek Zen",
                manager_user_id=None,
                department_ids={"dept-1"},
            )

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("derek-user")
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
            return "derek-user"

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
                "derek-user": DwsUserProfile(
                    user_id="derek-user",
                    name="Derek",
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
    assert store.get_current_user_id() == "derek-user"
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
            return "derek-user"

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
