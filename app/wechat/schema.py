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
from pathlib import Path
import sys
import hashlib
import re

_ZSTD = None
for _cand in (
    str(Path(sys.prefix) / "lib" / "libzstd.dylib"),
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


_ATLIST_RE = re.compile(rb"<atuserlist>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</atuserlist>", re.S)


def parse_mentions(source_blob, ct_flag) -> list[str]:
    """Extract @-mention wxids from a message's ``source`` column.

    Verified on 4.1.10: group @-mentions are stored as a comma-separated wxid
    list inside ``<msgsource><atuserlist><![CDATA[wxid_a,wxid_b]]></atuserlist>``.
    This is the reliable wxid-based signal for @self — never match display names.
    The source blob may be WCDB-zstd-compressed (``WCDB_CT_source==4``).
    """
    if source_blob is None:
        return []
    b = bytes(source_blob)
    if ct_flag == 4 or b[:4] == ZSTD_MAGIC:
        try:
            b = zstd_decompress(b)
        except Exception:
            return []
    match = _ATLIST_RE.search(b)
    if not match:
        return []
    inner = match.group(1).strip()
    return [x.decode("utf-8", "replace") for x in re.split(rb"[,\s]+", inner) if x]


_APPMSG_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
_APPMSG_DES = re.compile(r"<des>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</des>", re.S)
_APPMSG_URL = re.compile(r"<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>", re.S)


def decode_message(content, ct_flag, local_type) -> str:
    """Human-readable text for any message. Text (base type 1) is returned as-is;
    a shared link/article (appmsg — v4 encodes it as ``(subtype<<32)|49``) becomes
    ``[链接]《title》 des <url>`` so context readers (the Codex prompt + article
    enrichment) see what was shared and can fetch it, instead of an empty string —
    for intel/news groups that non-text content is most of the signal."""
    raw = decode_content(content, ct_flag)
    if (local_type & 0xFFFFFFFF) == 1:
        return raw
    if (local_type & 0xFFFFFFFF) == 34:
        return voice_placeholder(raw)
    if "<appmsg" in raw or "<title>" in raw:
        tm = _APPMSG_TITLE.search(raw)
        dm = _APPMSG_DES.search(raw)
        um = _APPMSG_URL.search(raw)
        title = (tm.group(1).strip() if tm else "")
        des = (dm.group(1).strip() if dm else "")
        url = (um.group(1).strip().replace("&amp;", "&") if um else "")
        if _is_unsupported_placeholder(title, url):
            return unsupported_placeholder(raw)
        if title:
            out = f"[链接]《{title}》"
            if des:
                out += f" {des[:100]}"
            if url.startswith("http"):
                out += f" {url}"
            return out
    return ""


_UNSUPPORTED_URL_PREFIX = "https://support.weixin.qq.com/update"
_UNSUPPORTED_TITLE_MARKERS = (
    "does not support this content",
    "不支持该内容",
    "不支持展示该内容",
    "不支持此内容",
)
_APPMSG_TYPE = re.compile(r"<type>(\d+)</type>")
UNSUPPORTED_PREFIX = "[无法显示的内容"


def _is_unsupported_placeholder(title: str, url: str) -> bool:
    """WeChat substitutes its own title and update link for an appmsg this
    client version cannot render. That text is WeChat's placeholder, not what
    the sender shared, so it must not be presented as a link."""
    if url.startswith(_UNSUPPORTED_URL_PREFIX):
        return True
    lowered = title.lower()
    return any(marker in lowered for marker in _UNSUPPORTED_TITLE_MARKERS)


def unsupported_placeholder(raw: str) -> str:
    """Text for a message whose content this WeChat version cannot show.

    It says plainly that the sender's content was not read, so an Agent reading
    the conversation does not take WeChat's update notice for a shared article.
    """
    match = _APPMSG_TYPE.search(raw)
    kind = f"，appmsg 类型 {match.group(1)}" if match else ""
    return f"{UNSUPPORTED_PREFIX}·本机微信版本不支持这类消息{kind}，看不到发送方发了什么]"


_VOICE_LENGTH = re.compile(r'voicelength="(\d+)"')


def voice_placeholder(raw: str) -> str:
    """Text standing in for a voice message until the Agent transcribes it.

    The audio is not in the message row, so the text must say that the message
    has content that was not read; an empty text would look like nothing to answer.
    """
    match = _VOICE_LENGTH.search(raw)
    seconds = f"{max(1, round(int(match.group(1)) / 1000))}秒" if match else ""
    return f"[语音{seconds}·未转写，用 save_message_audio 取音频再转写]"


def table_for(conversation_username: str) -> str:
    return "Msg_" + hashlib.md5(conversation_username.encode()).hexdigest()


def kind_for(local_type: int, decoded_text: str = "") -> str:
    """Map WeChat's local type to the normalized message kind.

    Shared articles use appmsg type 49.  Their decoded title/URL is usable
    text context, so keep them eligible for the normal text reply path.  An
    appmsg this WeChat version cannot render decodes to the unsupported
    placeholder, not to ``[链接]``, so like an image it stays non-text: the
    content was not read, and there is nothing for a reply to answer.  Image
    and other binary messages remain non-text until the reader supplies media
    bytes or a vision input path.
    """
    base_type = local_type & 0xFFFFFFFF
    if base_type == 1 or (base_type == 49 and decoded_text.startswith("[链接]")):
        return "text"
    if base_type == 34:
        return "voice"
    return "unknown" if local_type != 10000 else "system"


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
