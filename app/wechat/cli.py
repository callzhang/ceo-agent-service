"""Self-contained WeChat channel CLI (diagnostics + one-shot loops).

Kept separate from the large app/cli.py so it can be wired in as a thin
subcommand later. Read commands print redacted metadata by default; --include-text
is the explicit opt-in for a local verification run.

  python -m app.wechat.cli status [--db ...]
  python -m app.wechat.cli read-recent --target-id filehelper [--type direct] [--limit 100] [--include-text]
  python -m app.wechat.cli produce-once [--db ...]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app import config
from app.store import AutoReplyStore
from app.wechat import discovery, service
from app.wechat.models import WechatAccount

DEFAULT_DB = "data/auto-reply.sqlite3"


def _accounts():
    return discovery.discover_account_directories(discovery.default_xwechat_root())


def _install_version() -> str:
    try:
        return discovery.discover_wechat_install().version
    except Exception:
        return ""


def _reader(*, self_username: str = ""):
    return service.build_reader(
        config.wechat_mirror_dir(), config.wechat_passphrase_file(),
        self_username=self_username,
    )


def cmd_status(args) -> int:
    store = AutoReplyStore(Path(args.db))
    reader = _reader()
    version = _install_version()
    accounts = _accounts()
    if not accounts:
        print("no WeChat account directory found")
        return 1
    for a in accounts:
        acct = WechatAccount(
            account_id=a.account_id, display_name=a.account_id, self_user_id="",
            account_dir=str(a.account_dir), db_dir=str(a.db_dir), app_version=version,
        )
        cap = reader.probe(acct)
        self_user_id = config.wechat_self_user_id()
        if not self_user_id and cap.status == "ready":
            self_user_id = reader.detect_self_username(acct)
        suffix = f" self={self_user_id}" if self_user_id else ""
        print(f"{a.account_id}: {cap.status} {cap.reason}".rstrip() + suffix)
        store.upsert_wechat_read_state(
            account_id=acct.account_id, account_dir=acct.account_dir, db_dir=acct.db_dir,
            app_version=acct.app_version, self_user_id=self_user_id,
            capability_status=cap.status, capability_reason=cap.reason,
        )
    return 0


def cmd_consume_once(args) -> int:
    store = AutoReplyStore(Path(args.db))
    state = service.ready_account_state(store)
    if state is None:
        print("no single ready account; run status first")
        return 1
    account = service.account_from_state(state)
    from app.codex_decision import CodexDecisionRunner

    runner = CodexDecisionRunner(workspace=config.workspace_path())
    n = service.run_consume_once(store, runner, _reader(), account)
    print(f"processed {n} wechat reply task(s)")
    return 0


def _single_account() -> WechatAccount | None:
    accounts = _accounts()
    if len(accounts) != 1:
        return None
    a = accounts[0]
    return WechatAccount(
        account_id=a.account_id, display_name=a.account_id, self_user_id="",
        account_dir=str(a.account_dir), db_dir=str(a.db_dir), app_version=_install_version(),
    )


def cmd_read_recent(args) -> int:
    store = AutoReplyStore(Path(args.db))
    state = service.ready_account_state(store)
    account = service.account_from_state(state) if state is not None else _single_account()
    if account is None:
        print("expected one ready or discovered WeChat account")
        return 1
    self_user_id = account.self_user_id
    if not self_user_id:
        self_user_id = _reader().detect_self_username(account)
    if not self_user_id:
        print("cannot determine current WeChat user; run status and verify self_user_id")
        return 1
    account = account.model_copy(update={"self_user_id": self_user_id})
    reader = _reader(self_username=self_user_id)
    messages = reader.read_messages(
        account, conversation_id=args.target_id, conversation_type=args.type,
        limit=args.limit,
    )
    print(f"{len(messages)} messages in {args.target_id} ({args.type}):")
    for m in messages:
        if args.include_text:
            print(f"  [{m.sent_at[:19]}] {m.direction} {m.sender_display_name}: {m.text[:80]}")
        else:
            print(f"  [{m.sent_at[:19]}] {m.direction} {m.kind} len={len(m.text)}")
    return 0


def cmd_produce_once(args) -> int:
    store = AutoReplyStore(Path(args.db))
    state = service.ready_account_state(store)
    if state is None:
        print("no single ready account; run status first")
        return 1
    account = service.account_from_state(state)
    n = service.run_produce_once(store, _reader(), account, self_user_id=state.get("self_user_id", ""))
    print(f"enqueued {n} wechat reply task(s)")
    return 0


def cmd_pending(args) -> int:
    store = AutoReplyStore(Path(args.db))
    pend = service.pending_wechat_deliveries(store)
    print(f"{len(pend)} pending [mode={config.wechat_send_mode()}]:")
    for d in pend:
        print(f"  #{d.id} -> [{d.target_type}] {d.target_id}: {d.reply_text[:70]}")
    return 0


def cmd_approve(args) -> int:
    from app.wechat.accessibility import MacWechatAccessibility, WechatSender
    store = AutoReplyStore(Path(args.db))
    sender = WechatSender(store, MacWechatAccessibility())
    status = service.approve_wechat_delivery(store, sender, args.id)
    print(f"delivery #{args.id}: {status}")
    return 0


def cmd_reject(args) -> int:
    store = AutoReplyStore(Path(args.db))
    service.reject_wechat_delivery(store, args.id)
    print(f"delivery #{args.id}: rejected")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status"); p.add_argument("--db", default=DEFAULT_DB); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("read-recent")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--target-id", required=True)
    p.add_argument("--type", default="direct", choices=["direct", "group"])
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--include-text", action="store_true")
    p.set_defaults(fn=cmd_read_recent)
    p = sub.add_parser("produce-once"); p.add_argument("--db", default=DEFAULT_DB); p.set_defaults(fn=cmd_produce_once)
    p = sub.add_parser("consume-once"); p.add_argument("--db", default=DEFAULT_DB); p.set_defaults(fn=cmd_consume_once)
    p = sub.add_parser("pending"); p.add_argument("--db", default=DEFAULT_DB); p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("approve"); p.add_argument("--db", default=DEFAULT_DB); p.add_argument("--id", type=int, required=True); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject"); p.add_argument("--db", default=DEFAULT_DB); p.add_argument("--id", type=int, required=True); p.set_defaults(fn=cmd_reject)

    return parser


def main(argv=None) -> int:
    parser = build_parser()

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
