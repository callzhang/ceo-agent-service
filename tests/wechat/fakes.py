"""Test doubles for the WeChat channel. No real crypto or WeChat access."""
from __future__ import annotations

from app.wechat.key_provider import KeyProviderUnavailable
from app.wechat.models import WechatAccount


class StaticTestKeyProvider:
    def __init__(self, key: bytes):
        self.key = key

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        return self.key


class UnavailableTestKeyProvider:
    def __init__(self, reason: str):
        self.reason = reason

    def key_for(self, account: WechatAccount) -> bytes:
        del account
        raise KeyProviderUnavailable(self.reason)


class FakeCipherBackend:
    """Returns canned schema/rows/targets; ignores db_dir + passphrase."""

    def __init__(self, rows=None, tables=None, targets=None):
        self.rows = rows or []
        self.tables = tables if tables is not None else ["Message"]
        self.targets = targets or []

    def probe(self, db_dir, passphrase):
        del db_dir, passphrase
        return list(self.tables)

    def read_messages(
        self, db_dir, passphrase, *, conversation_id, conversation_type, since,
        limit, until="", order="newest",
    ):
        del db_dir, passphrase, since, until
        rows = self.rows
        if conversation_id:
            rows = [r for r in rows if r.get("conversation_id") == conversation_id]
        return rows[:limit] if order == "newest" else list(reversed(rows[:limit]))

    def list_targets(self, db_dir, passphrase, *, kind, query, limit, offset):
        del db_dir, passphrase
        items = [t for t in self.targets if t.get("target_type") == kind]
        if query:
            items = [t for t in items if query.lower() in t.get("display_name", "").lower()]
        return items[offset:offset + limit]
