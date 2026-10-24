import pytest

from app.wechat.models import WechatAccount


@pytest.fixture
def fake_account(tmp_path):
    db_dir = tmp_path / "db_storage"
    db_dir.mkdir()
    return WechatAccount(
        account_id="acct-1",
        display_name="derek",
        self_user_id="self-1",
        account_dir=str(tmp_path),
        db_dir=str(db_dir),
        app_version="4.1.10",
    )


@pytest.fixture(autouse=True)
def approved_sends(monkeypatch):
    """Tests that start a send get a genuinely approved delivery first.

    ``claim_wechat_delivery`` stays strict; only the ``mark_wechat_delivery_sending``
    convenience used to reach the ``sending`` state approves beforehand. Tests of
    the gate itself (``test_independent_audit_gate``) never call it.
    """
    from app.store import AutoReplyStore
    from tests.wechat.support import approve_delivery

    original = AutoReplyStore.mark_wechat_delivery_sending

    def mark_sending(self, delivery_id, *, now=""):
        approve_delivery(self, delivery_id)
        return original(self, delivery_id, now=now)

    monkeypatch.setattr(AutoReplyStore, "mark_wechat_delivery_sending", mark_sending)
