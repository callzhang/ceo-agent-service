from types import SimpleNamespace

from app.store import AutoReplyStore
from app.wechat import cli
from app.wechat.models import WechatAccount, WechatMessage


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


def test_read_recent_fallback_detects_self_id_before_reading(tmp_path, monkeypatch):
    discovered = WechatAccount(
        account_id="acct-1", display_name="acct-1", self_user_id="",
        account_dir="/account", db_dir="/account/db_storage", app_version="4.1.10",
    )
    detector = SimpleNamespace(detect_self_username=lambda account: "self-1")
    reader = RecordingReader("self-1")
    built_with = []

    def build_reader(*, self_username=""):
        built_with.append(self_username)
        return detector if not self_username else reader

    monkeypatch.setattr(cli, "_single_account", lambda: discovered)
    monkeypatch.setattr(cli, "_reader", build_reader)

    assert cli.cmd_read_recent(_args(tmp_path / "worker.sqlite3")) == 0
    assert built_with == ["", "self-1"]
    assert reader.read_account.self_user_id == "self-1"


def test_read_recent_refuses_to_guess_direction_without_self_id(
    tmp_path, monkeypatch, capsys,
):
    discovered = WechatAccount(
        account_id="acct-1", display_name="acct-1", self_user_id="",
        account_dir="/account", db_dir="/account/db_storage", app_version="4.1.10",
    )
    detector = SimpleNamespace(detect_self_username=lambda account: "")
    monkeypatch.setattr(cli, "_single_account", lambda: discovered)
    monkeypatch.setattr(cli, "_reader", lambda *, self_username="": detector)

    assert cli.cmd_read_recent(_args(tmp_path / "worker.sqlite3")) == 1
    assert "cannot determine current WeChat user" in capsys.readouterr().out


def test_read_recent_parser_accepts_db_path():
    parser = cli.build_parser()
    args = parser.parse_args([
        "read-recent", "--db", "/tmp/w.sqlite3", "--target-id", "filehelper",
    ])
    assert args.db == "/tmp/w.sqlite3"
