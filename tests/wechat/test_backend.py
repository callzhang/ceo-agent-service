import os
import shutil
import sqlite3
from datetime import datetime, timedelta

from app.wechat import schema
from app.wechat.backend import WcdbReaderBackend


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
