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
