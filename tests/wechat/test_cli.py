from types import SimpleNamespace

import pytest

from app.store import AutoReplyStore
from app.wechat import cli
from app.wechat.models import WechatMessage


class RecordingReader:
    def __init__(self, self_user_id: str):
        self.self_user_id = self_user_id
        self.read_account = None

    def read_messages(self, account, **kwargs):
        del kwargs
        self.read_account = account
        return [
            WechatMessage(
                account_id=account.account_id,
                conversation_id="filehelper",
                message_id="m1",
                sender_id=self.self_user_id,
                sender_display_name="Derek",
                conversation_type="direct",
                direction="outbound",
                sent_at="2026-07-20T10:00:00+08:00",
                kind="text",
                text="hello",
                source_version=account.app_version,
            )
        ]


def _args(db):
    return SimpleNamespace(
        db=str(db), target_id="filehelper", type="direct", limit=100,
        include_text=False,
    )


def test_read_recent_uses_persisted_ready_account_self_id(tmp_path, monkeypatch):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    built_with = []
    reader = RecordingReader("self-1")

    def build_reader(*, self_username=""):
        built_with.append(self_username)
        return reader

    monkeypatch.setattr(cli, "_reader", build_reader)

    assert cli.cmd_read_recent(_args(db)) == 0
    assert built_with == ["self-1"]
    assert reader.read_account.self_user_id == "self-1"


def test_read_recent_detects_missing_self_id_for_unique_ready_account(
    tmp_path, monkeypatch,
):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="", capability_status="ready",
    )
    detector = SimpleNamespace(detect_self_username=lambda account: "self-1")
    reader = RecordingReader("self-1")
    built_with = []

    def build_reader(*, self_username=""):
        built_with.append(self_username)
        return detector if not self_username else reader

    monkeypatch.setattr(cli, "_reader", build_reader)

    assert cli.cmd_read_recent(_args(db)) == 0
    assert built_with == ["", "self-1"]
    assert reader.read_account.self_user_id == "self-1"
    assert AutoReplyStore(db).get_wechat_read_state("acct-1")["self_user_id"] == "self-1"


def test_read_recent_refuses_to_guess_direction_without_self_id(
    tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="", capability_status="ready",
    )
    detector = SimpleNamespace(detect_self_username=lambda account: "")
    monkeypatch.setattr(cli, "_reader", lambda *, self_username="": detector)

    assert cli.cmd_read_recent(_args(db)) == 1
    assert "cannot determine current WeChat user" in capsys.readouterr().out


def test_read_recent_refuses_zero_persisted_ready_accounts(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(
        cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no read")),
    )

    assert cli.cmd_read_recent(_args(tmp_path / "worker.sqlite3")) == 1
    assert "exactly one persisted ready" in capsys.readouterr().out


def test_read_recent_refuses_blocked_persisted_account(tmp_path, monkeypatch, capsys):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="blocked",
    )
    monkeypatch.setattr(
        cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no read")),
    )

    assert cli.cmd_read_recent(_args(db)) == 1
    assert "exactly one persisted ready" in capsys.readouterr().out


def test_read_recent_refuses_multiple_persisted_ready_accounts(
    tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    for account_id in ("acct-1", "acct-2"):
        store.upsert_wechat_read_state(
            account_id=account_id, account_dir=f"/{account_id}",
            db_dir=f"/{account_id}/db_storage", app_version="4.1.10",
            self_user_id=f"self-{account_id}", capability_status="ready",
        )
    monkeypatch.setattr(
        cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no read")),
    )

    assert cli.cmd_read_recent(_args(db)) == 1
    assert "exactly one persisted ready" in capsys.readouterr().out


def test_read_recent_parser_accepts_db_path():
    parser = cli.build_parser()
    args = parser.parse_args([
        "read-recent", "--db", "/tmp/w.sqlite3", "--target-id", "filehelper",
    ])
    assert args.db == "/tmp/w.sqlite3"


def test_produce_once_builds_direction_aware_reader(tmp_path, monkeypatch):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    reader = object()
    built_with = []
    captured = []
    monkeypatch.setattr(
        cli, "_reader",
        lambda *, self_username="": built_with.append(self_username) or reader,
    )
    monkeypatch.setattr(
        cli.service, "run_produce_once",
        lambda store, used_reader, account, *, self_user_id: captured.append(
            (used_reader, account.self_user_id, self_user_id)
        ) or 0,
    )

    assert cli.cmd_produce_once(SimpleNamespace(db=str(db))) == 0
    assert built_with == ["self-1"]
    assert captured == [(reader, "self-1", "self-1")]


def test_consume_once_builds_direction_aware_reader(tmp_path, monkeypatch):
    from app import codex_decision

    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready",
    )
    reader = object()
    built_with = []
    captured = []
    monkeypatch.setattr(codex_decision, "CodexDecisionRunner", lambda **kwargs: object())
    monkeypatch.setattr(
        cli, "_reader",
        lambda *, self_username="": built_with.append(self_username) or reader,
    )
    monkeypatch.setattr(
        cli.service, "run_consume_once",
        lambda store, runner, used_reader, account: captured.append(
            (used_reader, account.self_user_id)
        ) or 0,
    )

    assert cli.cmd_consume_once(SimpleNamespace(db=str(db))) == 0
    assert built_with == ["self-1"]
    assert captured == [(reader, "self-1")]


@pytest.mark.parametrize("command", [cli.cmd_produce_once, cli.cmd_consume_once])
def test_automatic_once_commands_reject_ready_account_without_self_id(
    command, tmp_path, monkeypatch, capsys,
):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="", capability_status="ready",
    )
    monkeypatch.setattr(
        cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no read")),
    )

    assert command(SimpleNamespace(db=str(db))) == 1
    assert "no single ready account" in capsys.readouterr().out
