"""Capability-gated, read-only WeChat reader.

The reader orchestrates: obtain the passphrase from a validated key provider,
delegate decryption + schema extraction to a CipherBackend, and normalize raw
rows into immutable ``WechatMessage`` values. It refuses to read when capability
is not ``ready`` and never queries the live database directly (the backend
snapshots first).
"""
from __future__ import annotations

from typing import Protocol

from app.wechat.key_provider import KeyProviderUnavailable, WechatKeyProvider
from app.wechat.models import WechatAccount, WechatCapability, WechatMessage


class ReaderBackend(Protocol):
    def probe(self, db_dir: str, passphrase: bytes) -> list[str]: ...
    def read_messages(
        self, db_dir: str, passphrase: bytes, *,
        conversation_id: str, conversation_type: str, since: str, limit: int,
    ) -> list[dict]: ...
    def list_targets(
        self, db_dir: str, passphrase: bytes, *,
        kind: str, query: str, limit: int, offset: int,
    ) -> list[dict]: ...


class WechatReaderNotReady(RuntimeError):
    pass


class WechatReader:
    def __init__(self, backend: ReaderBackend, key_provider: WechatKeyProvider):
        self.backend = backend
        self.key_provider = key_provider

    def probe(self, account: WechatAccount) -> WechatCapability:
        try:
            passphrase = self.key_provider.key_for(account)
        except KeyProviderUnavailable as exc:
            return WechatCapability(
                status="blocked", account_id=account.account_id,
                app_version=account.app_version, reason=str(exc),
            )
        try:
            tables = self.backend.probe(account.db_dir, passphrase)
        except Exception as exc:  # decryption / IO failure is a real blocker
            return WechatCapability(
                status="blocked", account_id=account.account_id,
                app_version=account.app_version, reason=f"probe_failed: {exc}",
            )
        if not tables:
            return WechatCapability(
                status="blocked", account_id=account.account_id,
                app_version=account.app_version, reason="empty_schema",
            )
        return WechatCapability(
            status="ready", account_id=account.account_id, app_version=account.app_version,
        )

    def _require_ready(self, account: WechatAccount) -> bytes:
        capability = self.probe(account)
        if capability.status != "ready":
            raise WechatReaderNotReady(capability.reason or "not ready")
        return self.key_provider.key_for(account)

    def _normalize(self, row: dict, account: WechatAccount) -> WechatMessage:
        return WechatMessage(
            account_id=account.account_id,
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            sender_id=row.get("sender_id", ""),
            sender_display_name=row.get("sender_name", ""),
            conversation_type=row.get("conversation_type", "direct"),
            direction=row.get("direction", "inbound"),
            sent_at=row.get("sent_at", ""),
            kind=row.get("kind", "text"),
            text=row.get("text", ""),
            mentioned_user_ids=frozenset(row.get("mentioned_user_ids") or ()),
            source_version=account.app_version,
        )

    def read_messages(
        self, account: WechatAccount, *,
        conversation_id: str = "", conversation_type: str = "direct",
        since: str = "", limit: int = 100,
    ) -> list[WechatMessage]:
        passphrase = self._require_ready(account)
        rows = self.backend.read_messages(
            account.db_dir, passphrase,
            conversation_id=conversation_id, conversation_type=conversation_type,
            since=since, limit=limit,
        )
        return [self._normalize(row, account) for row in rows]

    def list_targets(
        self, account: WechatAccount, *,
        kind: str = "direct", query: str = "", limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        passphrase = self._require_ready(account)
        return self.backend.list_targets(
            account.db_dir, passphrase, kind=kind, query=query, limit=limit, offset=offset,
        )
