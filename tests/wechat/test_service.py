from app.store import AutoReplyStore
from app import config
from app.wechat import service as wechat_service
from app.wechat.service import (
    account_from_state, build_reader, ready_account_state, wechat_loop_names,
)


def test_no_loops_by_default():
    assert wechat_loop_names(reader_enabled=False, capability_ready=False) == []
    assert wechat_loop_names(reader_enabled=True, capability_ready=False) == []


def test_reader_ready_does_not_restore_legacy_producer_or_consumer_loops():
    names = wechat_loop_names(reader_enabled=True, capability_ready=True)
    assert names == []


def test_ready_account_requires_exactly_one(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    assert ready_account_state(store) is None
    store.upsert_wechat_read_state(
        account_id="a1", account_dir="/a1", db_dir="/a1/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    store.upsert_wechat_read_state(
        account_id="a2", account_dir="/a2", db_dir="/a2/db_storage",
        app_version="4.1.10", self_user_id="self-2", capability_status="blocked",
    )
    state = ready_account_state(store)
    assert state is not None and state["account_id"] == "a1"
    account = account_from_state(state)
    assert account.db_dir == "/a1/db_storage"


def test_ready_account_requires_self_user_id(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    store.upsert_wechat_read_state(
        account_id="a1", account_dir="/a1", db_dir="/a1/db_storage",
        app_version="4.1.10", self_user_id="", capability_status="ready",
    )

    assert ready_account_state(store) is None


def test_build_reader_is_an_ipc_client(tmp_path):
    reader = build_reader(socket_path=tmp_path / "reader.sock")

    assert reader.socket_path == tmp_path / "reader.sock"
    assert not hasattr(reader, "backend")
    assert not hasattr(reader, "key_provider")


def test_build_sender_is_a_dedicated_ipc_client(tmp_path):
    build_sender = getattr(wechat_service, "build_sender", None)
    assert build_sender is not None
    sender = build_sender(socket_path=tmp_path / "sender.sock")

    assert sender.socket_path == tmp_path / "sender.sock"
    assert sender.__class__.__name__ == "WechatSenderClient"


def test_tutorial_setup_readiness_check_does_not_activate_wechat(monkeypatch, tmp_path):
    class Reader:
        @staticmethod
        def discover_accounts():
            return []

    class Sender:
        calls = []

        @classmethod
        def check_readiness(cls):
            cls.calls.append("passive")
            return "ready"

        @staticmethod
        def request_accessibility():
            return "ready"

    monkeypatch.setattr(wechat_service, "build_reader", lambda: Reader())
    monkeypatch.setattr(wechat_service, "build_sender", lambda: Sender())

    setup = wechat_service.build_setup_service(AutoReplyStore(tmp_path / "w.sqlite3"))

    assert setup.accessibility_readiness() == "ready"
    assert Sender.calls == ["passive"]


def test_reader_timeout_allows_serialized_mirror_refresh(monkeypatch):
    monkeypatch.delenv("CEO_WECHAT_READER_TIMEOUT_SECONDS", raising=False)

    assert config.wechat_reader_timeout_seconds() == 120.0
