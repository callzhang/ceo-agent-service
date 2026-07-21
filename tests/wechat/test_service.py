from app.store import AutoReplyStore
from app.wechat import service as wechat_service
from app.wechat.service import (
    account_from_state, build_reader, ready_account_state, wechat_loop_names,
)


def test_no_loops_by_default():
    assert wechat_loop_names(reader_enabled=False, capability_ready=False) == []
    assert wechat_loop_names(reader_enabled=True, capability_ready=False) == []


def test_loops_only_when_reader_ready():
    names = wechat_loop_names(reader_enabled=True, capability_ready=True)
    assert set(names) == {
        "ceo-agent-service-wechat-producer",
        "ceo-agent-service-wechat-consumer",
    }


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


def test_build_reader_is_an_ipc_client_and_ignores_legacy_db_credentials(tmp_path):
    reader = build_reader(
        tmp_path / "legacy-plain-mirror",
        tmp_path / "legacy-passphrase.hex",
        socket_path=tmp_path / "reader.sock",
    )

    assert reader.socket_path == tmp_path / "reader.sock"
    assert not hasattr(reader, "backend")
    assert not hasattr(reader, "key_provider")


def test_build_sender_is_a_dedicated_ipc_client(tmp_path):
    build_sender = getattr(wechat_service, "build_sender", None)
    assert build_sender is not None
    sender = build_sender(socket_path=tmp_path / "sender.sock")

    assert sender.socket_path == tmp_path / "sender.sock"
    assert sender.__class__.__name__ == "WechatSenderClient"
