"""Native SQL boundaries for scheduled trigger occurrences."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping


OCCURRENCE_COLUMNS = (
    "id, event_id, scheduled_task_id, trigger_kind, scheduled_for, "
    "first_scheduled_for, occurrence_count, dispatch_status, "
    "skip_or_error_reason, "
    "(select schedule_config_snapshot_json from configs "
    "where schedule_snapshot_public_id=snapshot_id) as snapshot_json, "
    "execution_kind, execution_id, lease_owner, lease_expires_at, "
    "created_at, dispatched_at, result_summary"
)


def insert_occurrence(db: sqlite3.Connection, values: Mapping[str, object]):
    fields = tuple(values)
    columns = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    return db.execute(
        f"insert into schedule_occurrences({columns}) values({placeholders})",
        tuple(values[field] for field in fields),
    )


def occurrence_query(where: str = "", *, order: str = "", limit: str = "") -> str:
    suffix = (" where " + where if where else "")
    suffix += (" order by " + order if order else "")
    suffix += (" limit " + limit if limit else "")
    return f"select {OCCURRENCE_COLUMNS} from schedule_occurrences{suffix}"
