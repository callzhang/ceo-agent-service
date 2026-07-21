from app.wechat.models import WechatAccount
from app.wechat import reader_helper


def test_helper_builds_the_only_local_wcdb_reader(tmp_path):
    reader = reader_helper.build_local_reader(
        mirror_dir=tmp_path / "plain",
        passphrase_file=tmp_path / "passphrase.hex",
        self_username="wxid_self",
    )

    assert reader.backend.__class__.__name__ == "WcdbReaderBackend"
    assert reader.key_provider.__class__.__name__ == "PassphraseFileKeyProvider"


def test_helper_discovers_accounts_inside_its_own_process(monkeypatch, tmp_path):
    account_dir = tmp_path / "acct"
    db_dir = account_dir / "db_storage"
    found = type("Found", (), {
        "account_id": "acct-1",
        "account_dir": account_dir,
        "db_dir": db_dir,
    })()
    install = type("Install", (), {"version": "4.1.10.80"})()
    monkeypatch.setattr(reader_helper.discovery, "discover_wechat_install", lambda: install)
    monkeypatch.setattr(reader_helper.discovery, "default_xwechat_root", lambda: tmp_path)
    monkeypatch.setattr(
        reader_helper.discovery,
        "discover_account_directories",
        lambda root: [found] if root == tmp_path else [],
    )

    assert reader_helper.discover_accounts() == [WechatAccount(
        account_id="acct-1",
        display_name="acct-1",
        self_user_id="",
        account_dir=str(account_dir),
        db_dir=str(db_dir),
        app_version="4.1.10.80",
    )]
