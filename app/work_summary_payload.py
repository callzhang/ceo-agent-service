"""Versioned, lossless storage codec for work-summary input JSON."""

from __future__ import annotations
from app.task8_storage import register_task8_functions

import argparse
import json
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path


WORK_SUMMARY_INPUT_SQL = "select t.id, t.status, w.payload_json from tasks t join work_summary_inputs w on w.task_id=t.id where t.kind='work_summary'"

_MAGIC = b"\x00WSI1"
_COMPRESSION_LEVEL = 6


def encode_work_summary_payload(value: str | bytes) -> str | bytes:
    """Compress JSON only when the versioned binary representation is smaller."""
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if raw.startswith(_MAGIC):
        decode_work_summary_payload(
            raw
        )  # Reject corrupt marked data; never double-wrap.
        return raw
    compressed = _MAGIC + zlib.compress(raw, level=_COMPRESSION_LEVEL)
    return compressed if len(compressed) < len(raw) else raw.decode("utf-8")


def decode_work_summary_payload(value: str | bytes | None) -> str:
    """Return the original JSON text for both legacy and compressed rows."""
    if value is None:
        return "{}"
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if raw.startswith(_MAGIC):
        raw = zlib.decompress(raw[len(_MAGIC) :])
    return raw.decode("utf-8")


def migrate_work_summary_payloads(
    database: Path, *, batch_size: int = 100
) -> dict[str, int]:
    """Compress eligible rows in bounded, independently committed batches.

    The operation is resumable: already-marked rows are left untouched. It does
    not VACUUM, so reclaiming free SQLite pages remains a separate maintenance
    operation after the service has been stopped and a verified backup exists.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    counts = {"scanned": 0, "compressed": 0, "bytes_before": 0, "bytes_after": 0}
    with closing(sqlite3.connect(database, timeout=30)) as db:
        register_task8_functions(db)
        db.execute("pragma busy_timeout=30000")
        last_id = 0
        while True:
            db.execute("begin immediate")
            rows = db.execute(
                f"select id,payload_json from ({WORK_SUMMARY_INPUT_SQL}) "
                "where id>? and status not in ('done','skipped') order by id limit ?",
                (last_id, batch_size),
            ).fetchall()
            if not rows:
                db.commit()
                break
            try:
                for row_id, current in rows:
                    last_id = int(row_id)
                    counts["scanned"] += 1
                    raw = (
                        current.encode("utf-8")
                        if isinstance(current, str)
                        else bytes(current)
                    )
                    encoded = encode_work_summary_payload(raw)
                    if isinstance(encoded, str):
                        stored = encoded.encode("utf-8")
                    else:
                        stored = encoded
                    if stored == raw:
                        continue
                    cursor = db.execute(
                        """update work_summary_inputs set payload_json=? where task_id=? and payload_json=? and exists(
                           select 1 from tasks where id=? and kind='work_summary' and status not in ('done','skipped'))""",
                        (encoded, row_id, current, row_id),
                    )
                    if cursor.rowcount:
                        counts["compressed"] += 1
                        counts["bytes_before"] += len(raw)
                        counts["bytes_after"] += len(stored)
                db.commit()
            except Exception:
                db.rollback()
                raise
        integrity = db.execute("pragma quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity!r}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    print(
        json.dumps(migrate_work_summary_payloads(args.db, batch_size=args.batch_size))
    )


if __name__ == "__main__":
    main()
