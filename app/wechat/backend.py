"""Real WCDB reader backend: decrypt -> private mirror -> stdlib SQLite queries.

Decryption is pure-Python (cipher.py); to avoid re-decrypting large shards on
every read, each source DB is decrypted once into a private, chmod-700 mirror and
refreshed only when the source mtime changes. The mirror holds plaintext message
data, so it lives outside the repo and must be treated as sensitive (purge on
disable). Conversation messages are unioned across all message_*.db shards.
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from app.wechat.cipher import WcdbCipherBackend
from app.wechat import schema


class WcdbReaderBackend:
    def __init__(self, mirror_dir: str | Path, *, self_username: str = "", cipher=None):
        self.mirror_dir = Path(mirror_dir).expanduser()
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.mirror_dir, 0o700)
        self.self_username = self_username
        self.cipher = cipher or WcdbCipherBackend()

    # ---- decryption + mirror ----
    def _plaintext(self, source: Path, passphrase: bytes) -> Path:
        source = Path(source)
        dest = self.mirror_dir / source.name
        source_mtime_ns = source.stat().st_mtime_ns
        if dest.exists() and dest.stat().st_mtime_ns == source_mtime_ns:
            return dest
        self.cipher.decrypt(source, passphrase, dest)
        os.chmod(dest, 0o600)
        # Make freshness an exact source-version marker. Comparing with ``>=``
        # misses updates when the mirror was created later than a source mtime.
        os.utime(dest, ns=(dest.stat().st_atime_ns, source_mtime_ns))
        return dest

    def _message_shards(self, db_dir: str | Path) -> list[Path]:
        return sorted(Path(p) for p in glob.glob(str(Path(db_dir) / "message" / "message_[0-9]*.db")))

    def detect_self_username(self, db_dir, passphrase) -> str:
        """Self wxid = the non-'filehelper' sender in the 文件传输助手 self-chat.

        Every message in the File Transfer Helper conversation is sent by the
        account owner, so its sender resolves to the account's own wxid.
        """
        import hashlib
        table = "Msg_" + hashlib.md5(b"filehelper").hexdigest()
        for shard in self._message_shards(db_dir):
            conn = sqlite3.connect(self._plaintext(shard, passphrase))
            try:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    continue
                id2u = schema.name2id_map(conn)
                counts: dict[str, int] = {}
                for (sid,) in conn.execute(f'SELECT real_sender_id FROM "{table}" LIMIT 500'):
                    user = id2u.get(sid)
                    if user and user != "filehelper":
                        counts[user] = counts.get(user, 0) + 1
                if counts:
                    return max(counts, key=lambda user: counts[user])
            except sqlite3.Error:
                pass
            finally:
                conn.close()
        return ""

    def _contacts(self, db_dir: str | Path, passphrase: bytes) -> dict[str, str]:
        contact_src = Path(db_dir) / "contact" / "contact.db"
        if not contact_src.exists():
            return {}
        conn = sqlite3.connect(self._plaintext(contact_src, passphrase))
        disp: dict[str, str] = {}
        try:
            for u, rk, nk in conn.execute("SELECT username, remark, nick_name FROM contact"):
                disp[u] = rk or nk or u
        except sqlite3.Error:
            pass
        try:
            for u, nk in conn.execute("SELECT username, nick_name FROM chat_room"):
                disp.setdefault(u, nk or u)
        except sqlite3.Error:
            pass
        conn.close()
        return disp

    @staticmethod
    def _iso(ts) -> str:
        if not ts:
            return ""
        return _dt.datetime.fromtimestamp(ts).astimezone().isoformat()

    @staticmethod
    def _since_ts(since: str) -> float:
        if not since:
            return 0.0
        try:
            parsed = _dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed.timestamp()
        except ValueError:
            return 0.0

    # ---- reader backend protocol ----
    def probe(self, db_dir, passphrase) -> list[str]:
        shards = self._message_shards(db_dir)
        if not shards:
            return []
        conn = sqlite3.connect(self._plaintext(shards[0], passphrase))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        return tables

    def read_messages(
        self, db_dir, passphrase, *, conversation_id, conversation_type, since,
        limit, until="", order="newest",
    ) -> list[dict]:
        if order not in {"newest", "oldest"}:
            raise ValueError(f"unsupported message order: {order}")
        table = schema.table_for(conversation_id)
        since_ts = self._since_ts(since)
        until_ts = self._since_ts(until) if until else None
        disp = self._contacts(db_dir, passphrase)
        rows: list[dict] = []
        for shard in self._message_shards(db_dir):
            conn = sqlite3.connect(self._plaintext(shard, passphrase))
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not has:
                conn.close()
                continue
            id2u = schema.name2id_map(conn)
            columns = (
                f'SELECT local_id, server_id, local_type, real_sender_id, create_time, '
                f'CAST(message_content AS BLOB), WCDB_CT_message_content, '
                f'CAST(source AS BLOB), WCDB_CT_source FROM "{table}" '
            )
            if order == "oldest":
                raw_rows = []
                if since and (until_ts is None or since_ts <= until_ts):
                    raw_rows.extend(conn.execute(
                        columns + "WHERE create_time = ? ORDER BY create_time, local_id",
                        (since_ts,),
                    ))
                if until_ts is None:
                    raw_rows.extend(conn.execute(
                        columns
                        + "WHERE create_time > ? ORDER BY create_time, local_id LIMIT ?",
                        (since_ts, limit),
                    ))
                else:
                    raw_rows.extend(conn.execute(
                        columns + "WHERE create_time > ? AND create_time <= ? "
                        "ORDER BY create_time, local_id LIMIT ?",
                        (since_ts, until_ts, limit),
                    ))
            else:
                if until_ts is None:
                    raw_rows = conn.execute(
                        columns + "WHERE create_time >= ? "
                        "ORDER BY create_time DESC, local_id DESC LIMIT ?",
                        (since_ts, limit),
                    )
                else:
                    raw_rows = conn.execute(
                        columns + "WHERE create_time >= ? AND create_time <= ? "
                        "ORDER BY create_time DESC, local_id DESC LIMIT ?",
                        (since_ts, until_ts, limit),
                    )
            for local_id, server_id, ltype, sender, ctime, content, flag, source, source_flag in raw_rows:
                sender_user = id2u.get(sender, str(sender))
                rows.append({
                    "message_id": str(server_id) if server_id else f"{shard.name}:{local_id}",
                    "conversation_id": conversation_id,
                    "sender_id": sender_user,
                    "sender_name": disp.get(sender_user, sender_user),
                    "conversation_type": conversation_type,
                    "direction": "outbound" if (self.self_username and sender_user == self.self_username) else "inbound",
                    "sent_at": self._iso(ctime),
                    "kind": schema.kind_for(ltype),
                    "text": schema.decode_message(content, flag, ltype),
                    "mentioned_user_ids": schema.parse_mentions(source, source_flag),
                    "_overlap": bool(since) and ctime == since_ts,
                })
            conn.close()
        if order == "oldest":
            overlap = sorted(
                (row for row in rows if row["_overlap"]),
                key=lambda row: (row["sent_at"], row["message_id"]),
            )
            forward = sorted(
                (row for row in rows if not row["_overlap"]),
                key=lambda row: (row["sent_at"], row["message_id"]),
            )[:limit]
            for row in overlap + forward:
                row.pop("_overlap")
            return overlap + forward
        for row in rows:
            row.pop("_overlap")
        rows.sort(
            key=lambda row: (row["sent_at"], row["message_id"]), reverse=True
        )
        return rows[:limit]

    def list_targets(self, db_dir, passphrase, *, kind, query, limit, offset) -> list[dict]:
        contact_src = Path(db_dir) / "contact" / "contact.db"
        if not contact_src.exists():
            return []
        conn = sqlite3.connect(self._plaintext(contact_src, passphrase))
        items: list[dict] = []
        try:
            for username, remark, nick in conn.execute("SELECT username, remark, nick_name FROM contact"):
                is_group = str(username).endswith("@chatroom")
                if (kind == "group") != is_group:
                    continue
                name = remark or nick or username
                if query and query.lower() not in str(name).lower():
                    continue
                items.append({
                    "target_type": kind, "target_id": username,
                    "conversation_id": username, "display_name": name,
                })
        except sqlite3.Error:
            pass
        conn.close()
        items.sort(key=lambda t: t["display_name"])
        return items[offset:offset + limit]
