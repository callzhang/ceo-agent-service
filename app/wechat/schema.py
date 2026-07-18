"""WeChat 4.1.x decrypted-schema helpers (verified on build 268880).

Messages live in per-conversation tables ``Msg_<md5(conversation_username)>``.
Sender integers resolve through per-shard ``Name2Id.rowid -> user_name``.
Display names resolve through ``contact.db`` (contact/chat_room). Message text in
``message_content`` may be WCDB-zstd-compressed (``WCDB_CT_message_content==4``,
magic 28b52ffd, no dictionary).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib

_ZSTD = None
for _cand in (
    "/Users/derek/miniforge3/lib/libzstd.dylib",
    "/opt/homebrew/lib/libzstd.dylib",
    ctypes.util.find_library("zstd"),
):
    if _cand:
        try:
            _ZSTD = ctypes.CDLL(_cand)
            break
        except OSError:
            continue
if _ZSTD is not None:
    _ZSTD.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
    _ZSTD.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    _ZSTD.ZSTD_decompress.restype = ctypes.c_size_t
    _ZSTD.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    _ZSTD.ZSTD_isError.restype = ctypes.c_uint
    _ZSTD.ZSTD_isError.argtypes = [ctypes.c_size_t]

ZSTD_MAGIC = bytes.fromhex("28b52ffd")


def zstd_decompress(blob: bytes) -> bytes:
    if _ZSTD is None:
        raise RuntimeError("libzstd unavailable")
    n = _ZSTD.ZSTD_getFrameContentSize(blob, len(blob))
    out = ctypes.create_string_buffer(n)
    r = _ZSTD.ZSTD_decompress(out, n, blob, len(blob))
    if _ZSTD.ZSTD_isError(r):
        raise RuntimeError("zstd decompress error")
    return out.raw[:r]


def decode_content(blob, ct_flag) -> str:
    if blob is None:
        return ""
    b = bytes(blob)
    if ct_flag == 4 or b[:4] == ZSTD_MAGIC:
        try:
            b = zstd_decompress(b)
        except Exception:
            return ""
    return b.decode("utf-8", "replace")


def table_for(conversation_username: str) -> str:
    return "Msg_" + hashlib.md5(conversation_username.encode()).hexdigest()


def kind_for(local_type: int) -> str:
    # 1 = text; images/voice/video/file map to non-text kinds; others -> unknown
    return {1: "text"}.get(local_type, "unknown" if local_type != 10000 else "system")


def name2id_map(conn) -> dict[int, str]:
    try:
        return {rid: u for rid, u in conn.execute("SELECT rowid, user_name FROM Name2Id")}
    except Exception:
        return {}


def message_tables(conn) -> list[str]:
    return [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg\\_%' ESCAPE '\\'"
        )
    ]
