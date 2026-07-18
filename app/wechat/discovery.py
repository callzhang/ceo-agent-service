"""Deterministic, read-only discovery of the WeChat install and account dirs.

Never launches or modifies WeChat. Account candidates are sorted by stable
directory name (not mtime); zero, one, or many may be returned. The caller must
never silently choose among multiple accounts.
"""
from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WechatInstall:
    app_path: Path
    bundle_id: str
    version: str


@dataclass(frozen=True)
class WechatAccountDirectory:
    account_id: str
    account_dir: Path
    db_dir: Path


def discover_wechat_install(
    app_path: Path = Path("/Applications/WeChat.app"),
) -> WechatInstall:
    with (app_path / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    return WechatInstall(
        app_path=app_path,
        bundle_id=str(info["CFBundleIdentifier"]),
        version=str(info.get("CFBundleShortVersionString") or info["CFBundleVersion"]),
    )


def discover_account_directories(xwechat_root: Path) -> list[WechatAccountDirectory]:
    result: list[WechatAccountDirectory] = []
    if not xwechat_root.is_dir():
        return result
    for account_dir in sorted(xwechat_root.iterdir(), key=lambda path: path.name):
        if not account_dir.is_dir():
            continue
        db_dir = account_dir / "db_storage"
        if db_dir.is_dir() and next(db_dir.rglob("*.db"), None) is not None:
            result.append(
                WechatAccountDirectory(
                    account_id=account_dir.name,
                    account_dir=account_dir,
                    db_dir=db_dir,
                )
            )
    return result


def default_xwechat_root() -> Path:
    return (
        Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
