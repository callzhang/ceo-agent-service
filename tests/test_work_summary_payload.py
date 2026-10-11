from __future__ import annotations

import sqlite3

from app.task_models import WorkSummaryInput
from app.store import AutoReplyStore
from app.work_summary_payload import (
    decode_work_summary_payload,
    encode_work_summary_payload,
    migrate_work_summary_payloads,
)


def test_work_summary_payload_codec_is_lossless_and_only_compresses_when_smaller():
    large = '{"body":"' + ("重复正文内容" * 1000) + '"}'
    encoded = encode_work_summary_payload(large)
    assert isinstance(encoded, bytes)
    assert len(encoded) < len(large.encode("utf-8"))
    assert decode_work_summary_payload(encoded) == large
    assert encode_work_summary_payload(encoded) == encoded

    small = "{}"
    assert encode_work_summary_payload(small) == small
    assert decode_work_summary_payload(small) == small


def test_work_summary_input_model_decodes_compressed_database_value():
    payload = '{"body":"' + ("内容" * 300) + '"}'
    model = WorkSummaryInput(
        id=1,
        source_type="local_file",
        source_ref="file:1",
        payload_json=encode_work_summary_payload(payload),
        status="pending",
        created_at="2026-10-10 00:00:00",
        updated_at="2026-10-10 00:00:00",
    )
    assert model.payload_json == payload


def test_store_round_trips_compressed_work_summary_input(tmp_path):
    database = tmp_path / "service.sqlite3"
    store = AutoReplyStore(database)
    payload = '{"body":"' + ("管理周报正文" * 300) + '"}'
    input_id = store.enqueue_work_summary_input("local_file", "file:42", payload)

    assert store.get_work_summary_input(input_id).payload_json == payload
    with sqlite3.connect(database) as db:
        stored = db.execute(
            "select payload_json from work_summary_inputs where id=?", (input_id,)
        ).fetchone()[0]
    assert isinstance(stored, bytes)
    assert decode_work_summary_payload(stored) == payload


def test_migration_is_bounded_lossless_and_idempotent(tmp_path):
    database = tmp_path / "payloads.sqlite3"
    payload = '{"body":"' + ("周报正文" * 500) + '"}'
    with sqlite3.connect(database) as db:
        db.execute(
            "create table work_summary_inputs (id integer primary key, payload_json text not null)"
        )
        db.executemany(
            "insert into work_summary_inputs values (?, ?)",
            [(1, payload), (2, "{}")],
        )

    first = migrate_work_summary_payloads(database, batch_size=1)
    with sqlite3.connect(database) as db:
        rows = db.execute(
            "select id,payload_json from work_summary_inputs order by id"
        ).fetchall()
    assert first == {
        "scanned": 2,
        "compressed": 1,
        "bytes_before": len(payload.encode("utf-8")),
        "bytes_after": len(encode_work_summary_payload(payload)),
    }
    assert decode_work_summary_payload(rows[0][1]) == payload
    assert rows[1][1] == "{}"

    second = migrate_work_summary_payloads(database, batch_size=1)
    assert second["compressed"] == 0
