from types import SimpleNamespace

import pytest

from app.store import AutoReplyStore
from app.wechat import cli
from app.wechat.models import WechatAccount, WechatCapability, WechatMessage


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


def test_status_discovers_accounts_through_reader_process(tmp_path, monkeypatch):
    account = WechatAccount(
        account_id="acct-1", display_name="Derek", self_user_id="wxid_self",
        account_dir="/account", db_dir="/account/db_storage", app_version="4.1.10.80",
    )

    class Reader:
        def discover_accounts(self):
            return [account]

        def probe(self, selected):
            return WechatCapability(status="ready", account_id=selected.account_id)

        def detect_self_username(self, selected):
            return selected.self_user_id

    monkeypatch.setattr(cli, "_reader", lambda **kwargs: Reader())

    assert cli.cmd_status(SimpleNamespace(db=str(tmp_path / "worker.sqlite3"))) == 0


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


def test_import_memory_uses_unique_ready_account_and_explicit_bounds(tmp_path, monkeypatch):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="acct-1", account_dir="/account", db_dir="/account/db_storage",
        app_version="4.1.10", self_user_id="self-1", capability_status="ready")
    captured = {}
    class Importer:
        def __init__(self, store, reader, codex, matcher):
            captured.update(store=store, reader=reader, codex=codex, matcher=matcher)
        def run(self, **kwargs):
            captured.update(kwargs)
            return {"import_run_id":"run", "messages":3, "candidates":1}
    monkeypatch.setattr(cli, "WechatMemoryImporter", Importer)
    monkeypatch.setattr(cli, "CodexMemoryExtractionRunner", lambda workspace: "runner")
    monkeypatch.setattr(cli, "CodexMemoryRecallMatcher", lambda workspace: "matcher")
    monkeypatch.setattr(cli, "_reader", lambda **kwargs: "reader")
    args = SimpleNamespace(db=str(db), account_id="acct-1", target_id=["u1", "g@chatroom"],
                           since="2026-07-01", until="2026-07-20", limit=50)
    assert cli.cmd_import_memory(args) == 0
    assert captured["account"].account_id == "acct-1"
    assert captured["target_ids"] == ["u1", "g@chatroom"]


def test_import_memory_fails_closed_with_multiple_ready_accounts(tmp_path, monkeypatch, capsys):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    for account_id in ("a", "b"):
        store.upsert_wechat_read_state(
            account_id=account_id, account_dir=f"/{account_id}", db_dir=f"/{account_id}/db",
            app_version="4", self_user_id="self", capability_status="ready")
    monkeypatch.setattr(cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError()))
    args = SimpleNamespace(db=str(db), account_id="", target_id=["u1"],
                           since="2026-07-01", until="", limit=50)
    assert cli.cmd_import_memory(args) == 1
    assert "single ready account" in capsys.readouterr().out


def test_import_memory_fails_closed_without_self_identity(tmp_path, monkeypatch, capsys):
    db = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db)
    store.upsert_wechat_read_state(
        account_id="a", account_dir="/a", db_dir="/a/db", app_version="4",
        self_user_id="", capability_status="ready")
    monkeypatch.setattr(cli, "_reader", lambda **kwargs: (_ for _ in ()).throw(AssertionError()))
    args = SimpleNamespace(db=str(db), account_id="", target_id=["u1"],
                           since="2026-07-01", until="", limit=50)
    assert cli.cmd_import_memory(args) == 1
    assert "single ready account" in capsys.readouterr().out


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
