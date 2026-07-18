from app.wechat.discovery import discover_account_directories


def test_discover_accounts_ignores_directories_without_db_storage(tmp_path):
    container = tmp_path / "Documents" / "xwechat_files"
    valid = container / "acct_a" / "db_storage"
    valid.mkdir(parents=True)
    (valid / "message_0.db").write_bytes(b"db")
    (container / "cache_only").mkdir()
    (container / "all_users").mkdir()

    accounts = discover_account_directories(container)

    assert [item.account_id for item in accounts] == ["acct_a"]


def test_discover_accounts_returns_empty_for_missing_root(tmp_path):
    assert discover_account_directories(tmp_path / "nope") == []
