"""Entrypoint for the dedicated Accessibility-trusted WeChat sender."""
from __future__ import annotations

import argparse

from app import config
from app.wechat.accessibility import MacWechatAccessibility
from app.wechat.sender_ipc import WechatSenderRpcService, WechatSenderUnixServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dedicated local WeChat sender")
    parser.add_argument("serve", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--socket", default=str(config.wechat_sender_socket()))
    parser.add_argument("--idle-seconds", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = MacWechatAccessibility(idle_seconds=max(0.0, args.idle_seconds))
    server = WechatSenderUnixServer(
        args.socket, WechatSenderRpcService(runner),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
