import os
import shutil
import sqlite3
from datetime import datetime, timedelta

import pytest

from app.wechat import schema
from app.wechat.backend import WcdbReaderBackend
from app.wechat.models import WechatVoiceUnavailable


class CopyCipher:
    def decrypt(self, source, passphrase, dest):
        del passphrase
        shutil.copy2(source, dest)


def _create_message_db(path, conversation_id, rows):
    path.parent.mkdir(parents=True)
    table = schema.table_for(conversation_id)
    with sqlite3.connect(path) as db:
        db.execute("create table Name2Id (user_name text)")
        db.execute("insert into Name2Id(rowid, user_name) values (1, 'friend-1')")
        db.execute(
            f'''create table "{table}" (
                local_id integer, server_id integer, local_type integer,
                real_sender_id integer, create_time integer,
                message_content blob, WCDB_CT_message_content integer,
                source blob, WCDB_CT_source integer
            )'''
        )
        db.executemany(
            f'insert into "{table}" values (?, ?, 1, 1, ?, ?, 0, ?, 0)',
            [(local_id, server_id, timestamp, text.encode(), b"")
             for local_id, server_id, timestamp, text in rows],
        )


def test_oldest_order_keeps_boundary_overlap_outside_forward_limit(tmp_path):
    conversation_id = "friend-1"
    boundary = datetime.fromisoformat("2026-07-20T10:00:00+08:00")
    epoch = int(boundary.timestamp())
    source = tmp_path / "db_storage/message/message_0.db"
    _create_message_db(source, conversation_id, [
        (1, 1, epoch, "boundary"),
        (2, 2, epoch + 1, "next"),
        (3, 3, epoch + 2, "latest"),
    ])
    backend = WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())

    first = backend.read_messages(
        tmp_path / "db_storage", b"key", conversation_id=conversation_id,
        conversation_type="direct", since=boundary.isoformat(), limit=1,
        order="oldest",
    )
    assert [row["message_id"] for row in first] == ["1", "2"]

    with sqlite3.connect(source) as db:
        table = schema.table_for(conversation_id)
        db.execute(
            f'insert into "{table}" values (?, ?, 1, 1, ?, ?, 0, ?, 0)',
            (4, 4, epoch, b"late boundary", b""),
        )
    future = (boundary + timedelta(days=1)).timestamp()
    os.utime(source, (future, future))

    second = backend.read_messages(
        tmp_path / "db_storage", b"key", conversation_id=conversation_id,
        conversation_type="direct", since=boundary.isoformat(), limit=1,
        order="oldest",
    )
    assert [row["message_id"] for row in second] == ["1", "4", "2"]


def test_default_newest_order_is_preserved_for_diagnostics(tmp_path):
    conversation_id = "friend-1"
    boundary = datetime.fromisoformat("2026-07-20T10:00:00+08:00")
    epoch = int(boundary.timestamp())
    source = tmp_path / "db_storage/message/message_0.db"
    _create_message_db(source, conversation_id, [
        (1, 1, epoch, "old"),
        (2, 2, epoch + 1, "new"),
    ])
    backend = WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())

    rows = backend.read_messages(
        tmp_path / "db_storage", b"key", conversation_id=conversation_id,
        conversation_type="direct", since="", limit=1,
    )

    assert [row["message_id"] for row in rows] == ["2"]


def test_repeated_reads_reuse_the_same_plaintext_connection(tmp_path, monkeypatch):
    conversation_id = "friend-1"
    source = tmp_path / "db_storage/message/message_0.db"
    _create_message_db(source, conversation_id, [(1, 1, 1, "hello")])
    backend = WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())
    real_connect = sqlite3.connect
    opened = []
    statements = []

    def tracking_connect(*args, **kwargs):
        opened.append(args[0])
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr("app.wechat.backend.sqlite3.connect", tracking_connect)

    for _ in range(2):
        rows = backend.read_messages(
            tmp_path / "db_storage", b"key", conversation_id=conversation_id,
            conversation_type="direct", since="", limit=1,
        )
        assert [row["message_id"] for row in rows] == ["1"]

    assert len(opened) == 1
    assert sum("FROM Name2Id" in sql for sql in statements) == 1


def test_until_bound_is_applied_before_newest_limit(tmp_path):
    conversation_id = "friend-1"
    boundary = datetime.fromisoformat("2026-07-20T10:00:00+08:00")
    epoch = int(boundary.timestamp())
    source = tmp_path / "db_storage/message/message_0.db"
    _create_message_db(source, conversation_id, [
        (1, 1, epoch, "inside"),
        (2, 2, epoch + 100, "too new"),
    ])
    backend = WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())

    rows = backend.read_messages(
        tmp_path / "db_storage", b"key", conversation_id=conversation_id,
        conversation_type="direct", since="", until=boundary.isoformat(), limit=1,
    )

    assert [row["message_id"] for row in rows] == ["1"]


def test_since_timestamp_treats_naive_values_as_asia_shanghai():
    assert WcdbReaderBackend._since_ts("2026-07-17T10:00:00") == (
        WcdbReaderBackend._since_ts("2026-07-17T02:00:00Z")
    )


def test_shared_article_is_returned_as_text(tmp_path):
    conversation_id = "friend-1"
    source = tmp_path / "db_storage/message/message_0.db"
    source.parent.mkdir(parents=True)
    table = schema.table_for(conversation_id)
    with sqlite3.connect(source) as db:
        db.execute("create table Name2Id (user_name text)")
        db.execute("insert into Name2Id(rowid, user_name) values (1, 'friend-1')")
        db.execute(f'''create table "{table}" (
            local_id integer, server_id integer, local_type integer,
            real_sender_id integer, create_time integer, message_content blob,
            WCDB_CT_message_content integer, source blob, WCDB_CT_source integer)''')
        db.execute(f'''insert into "{table}" values (1, 1, 49, 1, 1, ?, 0, ?, 0)''',
                   (b"<appmsg><title>Article</title><url>https://example.com/a</url></appmsg>", b""))
    backend = WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())
    rows = backend.read_messages(tmp_path / "db_storage", b"key",
        conversation_id=conversation_id, conversation_type="direct", since="", limit=1)
    assert rows[0]["kind"] == "text"
    assert "https://example.com/a" in rows[0]["text"]


def _add_voice_message(path, conversation_id, *, local_id, server_id, local_type=34):
    table = schema.table_for(conversation_id)
    with sqlite3.connect(path) as db:
        db.execute(
            f'insert into "{table}" values (?, ?, ?, 1, 1000, ?, 0, ?, 0)',
            (local_id, server_id, local_type, b"<msg><voicemsg voicelength=\"3000\"/></msg>", b""),
        )


def _create_media_db(path, voices):
    with sqlite3.connect(path) as db:
        db.execute(
            "create table VoiceInfo(chat_name_id integer, create_time integer, "
            "local_id integer, svr_id integer, voice_data blob, data_index text)"
        )
        db.executemany(
            "insert into VoiceInfo values (1, 1000, 1, ?, ?, '0')", voices,
        )


def _voice_backend(tmp_path, *, media=None):
    conversation_id = "friend-1"
    shards = tmp_path / "db_storage/message"
    _create_message_db(shards / "message_0.db", conversation_id, [(1, 11, 1000, "hi")])
    _add_voice_message(shards / "message_0.db", conversation_id, local_id=2, server_id=22)
    _add_voice_message(shards / "message_0.db", conversation_id, local_id=3, server_id=33)
    for name, voices in (media or {}).items():
        _create_media_db(shards / name, voices)
    return conversation_id, WcdbReaderBackend(tmp_path / "mirror", cipher=CopyCipher())


def test_voice_audio_is_found_in_the_newest_media_shard_that_holds_it(tmp_path):
    conversation_id, backend = _voice_backend(tmp_path, media={
        "media_0.db": [(22, b"\x02#!SILK_V3 old"), (33, b"\x02#!SILK_V3 shared-old")],
        "media_1.db": [(33, b"\x02#!SILK_V3 new")],
    })
    read = lambda message_id: backend.read_voice(  # noqa: E731
        tmp_path / "db_storage", b"key", conversation_id=conversation_id, message_id=message_id,
    )

    assert read("22") == b"\x02#!SILK_V3 old"
    assert read("33") == b"\x02#!SILK_V3 new"
    assert read("message_0.db:2") == b"\x02#!SILK_V3 old"


def test_voice_without_audio_or_of_another_kind_or_conversation_is_unavailable(tmp_path):
    conversation_id, backend = _voice_backend(tmp_path, media={
        "media_1.db": [(33, b"\x02#!SILK_V3 new")],
    })

    def read(message_id, conversation=conversation_id):
        return backend.read_voice(
            tmp_path / "db_storage", b"key", conversation_id=conversation, message_id=message_id,
        )

    with pytest.raises(WechatVoiceUnavailable, match="not stored"):
        read("22")                      # voice message whose audio is not on this Mac
    with pytest.raises(WechatVoiceUnavailable, match="not a voice"):
        read("11")                      # a text message
    with pytest.raises(WechatVoiceUnavailable, match="not in this conversation"):
        read("33", conversation="friend-2")   # another conversation's table
    with pytest.raises(WechatVoiceUnavailable, match="not in this conversation"):
        read("nope")


def test_oversized_voice_audio_is_unavailable(tmp_path, monkeypatch):
    conversation_id, backend = _voice_backend(tmp_path, media={
        "media_1.db": [(33, b"x" * 100)],
    })
    monkeypatch.setattr("app.wechat.backend.MAX_VOICE_BYTES", 10)
    with pytest.raises(WechatVoiceUnavailable, match="too large"):
        backend.read_voice(
            tmp_path / "db_storage", b"key", conversation_id=conversation_id, message_id="33",
        )


def test_streaming_decrypt_matches_a_whole_file_decrypt(tmp_path):
    from app.wechat import cipher

    passphrase = b"p" * 32
    salt = b"s" * 16
    key = cipher.enc_key_from_passphrase(passphrase, salt)
    plain_pages = [bytes([i]) * (cipher.IV_OFF - (16 if i == 0 else 0)) for i in range(3)]
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    mac_key = cipher._mac_key(key, salt)
    encrypted = bytearray()
    import hashlib
    import hmac
    import struct
    for index, plain in enumerate(plain_pages):
        iv = bytes([index + 1]) * 16
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        body = enc.update(plain) + enc.finalize()
        page_prefix = salt if index == 0 else b""
        covered = body + iv
        mac = hmac.new(mac_key, covered + struct.pack("<I", index + 1), hashlib.sha512).digest()
        encrypted += page_prefix + covered + mac
        encrypted += b"\x00" * (cipher.PAGE - (len(page_prefix) + len(covered) + len(mac)))
    source = tmp_path / "enc.db"
    source.write_bytes(bytes(encrypted) + b"partial-tail")
    out = cipher.WcdbCipherBackend().decrypt(source, passphrase, tmp_path / "plain.db")

    data = out.read_bytes()
    assert len(data) == 3 * cipher.PAGE
    assert data[:16] == cipher.SQLITE_HDR
    assert data[16:cipher.IV_OFF] == plain_pages[0]
    assert data[cipher.PAGE:cipher.PAGE + cipher.IV_OFF] == plain_pages[1]

