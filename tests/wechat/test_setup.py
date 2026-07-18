import pytest

from app.store import AutoReplyStore
from app.wechat.models import WechatAccount, WechatCapability, WechatReplyScope
from app.wechat.setup import WechatSetupService


class FakeReader:
    def __init__(self, status="ready", targets=None):
        self.status = status
        self.targets = targets or []

    def probe(self, account):
        return WechatCapability(status=self.status, account_id=account.account_id)

    def list_targets(self, account, *, kind, query, limit, offset):
        return [t for t in self.targets if t["target_type"] == kind][offset:offset + limit]


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
    svc = WechatSetupService(store, FakeReader(status="ready"), lambda: "ready",
                             accounts_provider=lambda: [_account()])
    result = svc.connect()
    assert result.status == "done"
    assert result.next_step_status == "ready"
    assert result.evidence["database_status"] == "ready"
    assert store.get_wechat_read_state("acct-1")["capability_status"] == "ready"


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
