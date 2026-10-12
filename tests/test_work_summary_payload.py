from __future__ import annotations

import sqlite3

from app.task_models import WorkSummaryInput
from app.store import AutoReplyStore
from app.task_storage import WORK_SUMMARY_INPUT_SQL, insert_task
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
    AutoReplyStore(database)
    with sqlite3.connect(database) as db:
        for input_id, body, status in (
            (1, payload, "pending"), (2, "{}", "pending"),
            (3, payload, "done"),
        ):
            insert_task(db, "work_summary_inputs", {
                "id": input_id, "legacy_key": input_id,
                "source_type": "local_file", "source_ref": f"file:{input_id}",
                "payload_json": body, "status": status,
            })

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
    with sqlite3.connect(database) as db:
        db.execute(
            "update tasks set status='done' where record_type='work_summary_inputs' "
            "and cast(legacy_key as integer)=?", (input_id,)
        )

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
