from __future__ import annotations

import sqlite3

from app.task_models import WorkSummaryInput
from app.store import AutoReplyStore, WORK_SUMMARY_INPUT_SQL
from tests.support.native_task_fields import set_native_task_fields
from app.storage_maintenance import compact_terminal_work_summary_inputs
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
            f"select payload_json from ({WORK_SUMMARY_INPUT_SQL}) where id=?", (input_id,)
        ).fetchone()[0]
    assert isinstance(stored, bytes)
    assert decode_work_summary_payload(stored) == payload
    assert store._work_summary_input_provenance(stored) == (
        "",
        store._work_summary_input_provenance(payload)[1],
        len(payload.encode("utf-8")),
    )


def test_migration_is_bounded_lossless_and_idempotent(tmp_path):
    database = tmp_path / "payloads.sqlite3"
    payload = '{"body":"' + ("周报正文" * 500) + '"}'
    store = AutoReplyStore(database)
    ids = [
        store.enqueue_work_summary_input("local_file", f"file:{index}", body)
        for index, body in enumerate((payload, "{}", payload), start=1)
    ]
    assert ids == [1, 2, 3]
    with store._immediate_write_transaction() as db:
        # Rows written uncompressed before the codec existed.
        db.executemany(
            "update work_summary_inputs set payload_json=? where task_id=?",
            [(payload, 1), ("{}", 2), (payload, 3)],
        )
        set_native_task_fields(db, "work_summary_inputs", 3, {"status": "done"})

    first = migrate_work_summary_payloads(database, batch_size=1)
    with sqlite3.connect(database) as db:
        rows = db.execute(
            f"select id,payload_json from ({WORK_SUMMARY_INPUT_SQL}) order by id"
        ).fetchall()
    assert first == {
        "scanned": 2,
        "compressed": 1,
        "bytes_before": len(payload.encode("utf-8")),
        "bytes_after": len(encode_work_summary_payload(payload)),
    }
    assert decode_work_summary_payload(rows[0][1]) == payload
    assert rows[1][1] == "{}"
    assert rows[2][1] == payload

    second = migrate_work_summary_payloads(database, batch_size=1)
    assert second["compressed"] == 0


def test_terminal_compaction_reads_compressed_input_and_preserves_provenance(tmp_path):
    database = tmp_path / "terminal.sqlite3"
    store = AutoReplyStore(database)
    payload = (
        '{"source":{"created_at":"2026-10-10T12:00:00"},"summary":"'
        + ("完成事项" * 400)
        + '"}'
    )
    input_id = store.enqueue_work_summary_input("local_file", "file:done", payload)
    with store._immediate_write_transaction() as db:
        set_native_task_fields(db, "work_summary_inputs", input_id, {"status": "done"})

    result = compact_terminal_work_summary_inputs(database)

    with sqlite3.connect(database) as db:
        row = db.execute(
            "select payload_json, source_created_at, body_sha256, body_bytes, body_compacted "
            f"from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (input_id,),
        ).fetchone()
    assert result["rows_compacted"] == 1
    assert row == (
        "{}",
        "2026-10-10T12:00:00",
        store._work_summary_input_provenance(payload)[1],
        len(payload.encode("utf-8")),
        1,
    )
