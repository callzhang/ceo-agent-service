from pathlib import Path

from app.wechat.discovery import discover_account_directories


def test_discover_accounts_ignores_directories_without_db_storage(tmp_path):
    container = tmp_path / "Documents" / "xwechat_files"
    valid = container / "acct_a" / "db_storage" / "message"
    valid.mkdir(parents=True)
    (valid / "message_0.db").write_bytes(b"db")
    (container / "cache_only").mkdir()
    (container / "all_users").mkdir()

    accounts = discover_account_directories(container)

    assert [item.account_id for item in accounts] == ["acct_a"]


def test_discovery_checks_only_reader_message_shards_not_recursive_db_tree(
    tmp_path, monkeypatch
):
    container = tmp_path / "xwechat_files"
    message_dir = container / "acct_a" / "db_storage" / "message"
    message_dir.mkdir(parents=True)
    (message_dir / "message_0.db").write_bytes(b"db")

    def recursive_search_is_not_supported(*_args, **_kwargs):
        raise AssertionError("account discovery must not recursively scan the database tree")

    monkeypatch.setattr(Path, "rglob", recursive_search_is_not_supported)

    accounts = discover_account_directories(container)

    assert [item.account_id for item in accounts] == ["acct_a"]


def test_discover_accounts_returns_empty_for_missing_root(tmp_path):
    assert discover_account_directories(tmp_path / "nope") == []
