import pytest

from app.store import AutoReplyStore
from app.wechat.models import WechatAccount, WechatCapability, WechatReplyScope
from app.wechat.reader_ipc import ReaderIpcError
from app.wechat.setup import WechatSetupService


class FakeReader:
    def __init__(self, status="ready", targets=None, messages=None, read_error=None):
        self.status = status
        self.targets = ([{
            "target_type": "direct",
            "target_id": "u1",
            "conversation_id": "c1",
        }] if targets is None else targets)
        self.messages = ([{"message_id": "m1"}] if messages is None else messages)
        self.read_error = read_error
        self.target_calls = []
        self.read_calls = []

    def probe(self, account):
        return WechatCapability(status=self.status, account_id=account.account_id)

    def list_targets(self, account, *, kind, query, limit, offset):
        self.target_calls.append({
            "account": account,
            "kind": kind,
            "query": query,
            "limit": limit,
            "offset": offset,
        })
        return [t for t in self.targets if t["target_type"] == kind][offset:offset + limit]

    def read_messages(self, account, *, conversation_id, conversation_type, limit):
        self.read_calls.append({
            "account": account,
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "limit": limit,
        })
        if self.read_error is not None:
            raise self.read_error
        return self.messages

    def health(self):
        return {"status": "ready"}


def _account(aid="acct-1"):
    return WechatAccount(account_id=aid, display_name=aid, self_user_id="self-1",
                         account_dir=f"/{aid}", db_dir=f"/{aid}/db_storage", app_version="4.1.10")


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


def test_connect_requires_single_account(store):
    svc = WechatSetupService(store, FakeReader(), lambda: "ready",
                             accounts_provider=lambda: [_account("a"), _account("b")])
    result = svc.connect()
    assert result.status == "failed"
    assert result.evidence["account_count"] == 2


def test_connect_persists_capability_and_reports_status(store):
    reader = FakeReader(status="ready")
    svc = WechatSetupService(store, reader, lambda: "ready",
                             accounts_provider=lambda: [_account()])
    result = svc.connect()
    assert result.status == "done"
    assert result.next_step_status == "ready"
    assert result.evidence["database_status"] == "ready"
    assert result.evidence["message_read_verified"] is True
    assert store.get_wechat_read_state("acct-1")["capability_status"] == "ready"


def test_connect_requires_bounded_real_message_read(store):
    reader = FakeReader()
    svc = WechatSetupService(store, reader, lambda: "ready",
                             accounts_provider=lambda: [_account()])

    result = svc.connect()

    assert result.next_step_status == "ready"
    assert reader.target_calls == [{
        "account": _account(),
        "kind": "direct",
        "query": "",
        "limit": 1,
        "offset": 0,
    }]
    assert reader.read_calls == [{
        "account": _account(),
        "conversation_id": "c1",
        "conversation_type": "direct",
        "limit": 1,
    }]


def test_connect_blocks_when_no_message_can_be_read(store):
    reader = FakeReader(targets=[])
    svc = WechatSetupService(store, reader, lambda: "ready",
                             accounts_provider=lambda: [_account()])

    result = svc.connect()

    assert result.status == "done"
    assert result.next_step_status == "blocked"
    assert result.evidence["database_status"] == "ready"
    assert result.evidence["message_read_verified"] is False
    assert [call["kind"] for call in reader.target_calls] == ["direct", "group"]
    assert reader.read_calls == []


def test_connect_reports_reader_permission_error_without_retry(store):
    reader = FakeReader(read_error=ReaderIpcError(
        "Full Disk Access required", code="permission_required",
    ))
    svc = WechatSetupService(store, reader, lambda: "ready",
                             accounts_provider=lambda: [_account()])

    result = svc.connect()

    assert result.status == "done"
    assert result.next_step_status == "blocked"
    assert result.evidence["database_status"] == "permission_required"
    assert result.evidence["message_read_verified"] is False
    assert len(reader.read_calls) == 1


def test_blocked_reader_action_done_but_step_blocked(store):
    svc = WechatSetupService(store, FakeReader(status="blocked"), lambda: "ready",
                             accounts_provider=lambda: [_account()])
    result = svc.connect()
    assert result.status == "done"          # the action succeeded
    assert result.next_step_status == "blocked"  # but the channel is blocked


def test_verify_requires_scope_and_accessibility(store):
    svc = WechatSetupService(store, FakeReader(), lambda: "ready",
                             accounts_provider=lambda: [_account()])
    svc.connect()
    assert svc.verify().next_step_status == "blocked"  # no scopes yet
    store.replace_wechat_reply_scopes("acct-1", [
        WechatReplyScope(account_id="acct-1", target_type="direct", target_id="u1",
                         display_name="A", trigger_mode="every_inbound_text"),
    ])
    assert svc.verify().next_step_status == "done"


def test_check_requires_running_dedicated_reader(store):
    class OfflineReader(FakeReader):
        def health(self):
            raise RuntimeError("offline")

    svc = WechatSetupService(
        store, OfflineReader(), lambda: "ready", accounts_provider=lambda: [_account()],
    )
    assert svc.check().status == "needs_action"
    assert "Reader app" in svc.check().summary


def test_connect_requests_dedicated_sender_accessibility_when_needed(store):
    requested = []
    svc = WechatSetupService(
        store,
        FakeReader(),
        lambda: "accessibility_not_trusted",
        accessibility_request=lambda: requested.append(True) or "ready",
        accounts_provider=lambda: [_account()],
    )

    result = svc.connect()

    assert requested == [True]
    assert result.evidence["accessibility_status"] == "ready"


def test_check_requires_ready_dedicated_sender(store):
    svc = WechatSetupService(
        store,
        FakeReader(),
        lambda: "accessibility_not_trusted",
        accounts_provider=lambda: [_account()],
    )
    svc.connect()

    result = svc.check()

    assert result.status == "needs_action"
    assert "Sender app" in result.summary
