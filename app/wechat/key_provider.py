"""In-memory key-provider boundary.

WeChat 4.1+ does not keep the raw SQLCipher key in memory; only a 32-byte
passphrase, from which each DB's key is derived (see cipher.py). A provider
returns that passphrase. It is a runtime secret: never log it, never write it to
the queue DB, config, or long-term memory. If no validated provider exists the
reader must stay blocked rather than guess.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.wechat.models import WechatAccount


class KeyProviderUnavailable(RuntimeError):
    pass


class WechatKeyProvider(Protocol):
    def key_for(self, account: WechatAccount) -> bytes:  # returns the passphrase
        raise NotImplementedError


class UnavailableKeyProvider:
    """Explicit blocked provider; keeps the reader honest when no key exists."""

    def __init__(self, reason: str):
        self.reason = reason

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        raise KeyProviderUnavailable(self.reason)


class PassphraseFileKeyProvider:
    """Reads a hex-encoded passphrase from a chmod-600 file outside the repo.

    The passphrase is captured once (shadow-copy + CCKeyDerivationPBKDF) and is
    account-stable across restarts; see the plan's Task 5 correction block.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        try:
            text = self.path.read_text().strip()
        except FileNotFoundError as exc:
            raise KeyProviderUnavailable(f"passphrase file missing: {self.path}") from exc
        if not text:
            raise KeyProviderUnavailable("passphrase file empty")
        try:
            data = bytes.fromhex(text)
        except ValueError as exc:
            raise KeyProviderUnavailable("passphrase file is not valid hex") from exc
        if len(data) != 32:
            raise KeyProviderUnavailable(f"expected 32-byte passphrase, got {len(data)}")
        return data
