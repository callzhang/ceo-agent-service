"""Entrypoint for the dedicated CEO WeChat Reader process.

This is intentionally the only runtime composition module that imports the
WCDB backend, passphrase provider, and WeChat container discovery functions.
The main CEO Agent process connects to it through ``reader_ipc``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app import config
from app.wechat import discovery
from app.wechat.backend import WcdbReaderBackend
from app.wechat.key_provider import PassphraseFileKeyProvider
from app.wechat.models import WechatAccount
from app.wechat.reader import WechatReader
from app.wechat.reader_ipc import WechatReaderRpcService, WechatReaderUnixServer


def build_local_reader(
    mirror_dir: str | Path,
    passphrase_file: str | Path,
    *,
    self_username: str = "",
) -> WechatReader:
    backend = WcdbReaderBackend(Path(mirror_dir), self_username=self_username)
    return WechatReader(backend, PassphraseFileKeyProvider(Path(passphrase_file)))


def discover_accounts() -> list[WechatAccount]:
    try:
        version = discovery.discover_wechat_install().version
    except Exception:
        version = ""
    return [
        WechatAccount(
            account_id=item.account_id,
            display_name=item.account_id,
            self_user_id="",
            account_dir=str(item.account_dir),
            db_dir=str(item.db_dir),
            app_version=version,
        )
        for item in discovery.discover_account_directories(discovery.default_xwechat_root())
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dedicated local WeChat database reader")
    parser.add_argument("serve", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--socket", default=str(config.wechat_reader_socket()))
    parser.add_argument("--mirror-dir", default=str(config.wechat_mirror_dir()))
    parser.add_argument("--passphrase-file", default=str(config.wechat_passphrase_file()))
    parser.add_argument("--self-username", default=config.wechat_self_user_id())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reader = build_local_reader(
        args.mirror_dir,
        args.passphrase_file,
        self_username=args.self_username,
    )
    rpc_service = WechatReaderRpcService(reader, discover_accounts)
    server = WechatReaderUnixServer(args.socket, rpc_service)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
