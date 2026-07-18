from app.store import AutoReplyStore
from app.wechat.service import (
    account_from_state, ready_account_state, wechat_loop_names,
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
