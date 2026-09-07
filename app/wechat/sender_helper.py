"""Entrypoint for the dedicated Accessibility-trusted WeChat sender."""
from __future__ import annotations

import argparse
import logging
import threading

from app import config
from app.wechat.accessibility import MacWechatAccessibility
from app.wechat.sender_ipc import WechatSenderRpcService, WechatSenderUnixServer


LOGGER = logging.getLogger(__name__)
WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"


def _frontmost_identity() -> tuple[str, int]:
    """Return only front-app identity; never inspect UI or message content."""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.bundleIdentifier() or ""), int(app.processIdentifier())
    except Exception:
        return "", 0


def _watch_wechat_foreground(stop: threading.Event) -> None:
    """Persist evidence whenever WeChat becomes foreground without UI access."""
    previous = _frontmost_identity()
    while not stop.wait(0.1):
        current = _frontmost_identity()
        if current != previous and current[0] == WECHAT_BUNDLE_ID:
            LOGGER.warning(
                "wechat_foreground_observed previous_bundle_id=%s wechat_pid=%s",
                previous[0],
                current[1],
            )
        previous = current


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dedicated local WeChat sender")
    parser.add_argument("serve", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--socket", default=str(config.wechat_sender_socket()))
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=config.wechat_send_idle_seconds(),
    )
    parser.add_argument(
        "--min-interaction-interval",
        type=float,
        default=config.wechat_send_min_interval_seconds(),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    runner = MacWechatAccessibility(
        idle_seconds=max(0.0, args.idle_seconds),
        min_interaction_interval=max(0.0, args.min_interaction_interval),
    )
    server = WechatSenderUnixServer(
        args.socket, WechatSenderRpcService(runner),
    )
    watcher_stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_wechat_foreground,
        args=(watcher_stop,),
        name="ceo-wechat-foreground-monitor",
        daemon=True,
    )
    watcher.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher_stop.set()
        watcher.join(timeout=1)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
