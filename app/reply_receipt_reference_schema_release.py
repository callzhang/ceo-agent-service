"""Paired Store 2026-10-23.1 -> 2026-10-25.1 Reply receipt-reference release."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.current_schema_manifest import current_objects, schema_objects
from app.reply_receipt_reference import DDL, add_reference

SOURCE_STORE_VERSION = "2026-10-23.1"
TARGET_STORE_VERSION = "2026-10-25.1"
EMAIL_VERSION = 54
VERSION_KEY = "store_schema_version"
_OWNED_OBJECTS = {
    "reply_attempt_receipt_refs",
    "idx_reply_attempt_receipt_refs_receipt",
    "trg_reply_attempt_receipt_ref_insert",
    "trg_reply_attempt_receipt_ref_update",
}


def source_objects():
    return tuple(obj for obj in current_objects(email_ledger=True) if obj[1] not in _OWNED_OBJECTS)


def _marker(db):
    from app.state_definitions import state_projection
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) where key=?",
        (VERSION_KEY,),
    ).fetchone()


def _write_marker(db, version, updated_at):
    from app.state_storage import upsert_state
    upsert_state(db, "service_state", {"key": VERSION_KEY, "value": version},
                 {"value": "json_quote(excluded.value)", "updated_at": "json_quote(?)"},
                 update_parameters=(updated_at,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _matches(db):
    from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL
    from app.action_storage import reply_action_projection
    if db.execute("select 1 from temp.sqlite_schema where name='_reply_receipt_reference_match_source'").fetchone():
        raise ValueError("Reply receipt match TEMP source already exists")
    db.execute(f"create temp table _reply_receipt_reference_match_source as {NATIVE_EXTERNAL_ACTION_RESULT_SQL}")
    db.execute("create index temp.idx_reply_receipt_match_lineage "
               "on _reply_receipt_reference_match_source(first_agent_run_id,operation)")
    attempts = reply_action_projection(
        "reply_attempts", "fresh_memory_fk", canonical_attempt_fields=True,
    )
    sql = f"""with recursive lineage(attempt_id,run_id) as (
        select attempt.id,attempt.agent_run_id from ({attempts}) attempt
          where coalesce(attempt.oa_action_result_json,'')<>'' and attempt.agent_run_id is not null
        union
        select lineage.attempt_id,runs.parent_id from lineage join runs on runs.id=lineage.run_id
          where runs.parent_id is not null
    )
    select attempt.id,external.provider_receipt_id,attempt.oa_action_result_json,
           external.provider_result_json
    from ({attempts}) attempt
    join lineage on lineage.attempt_id=attempt.id
    join _reply_receipt_reference_match_source external
      on external.first_agent_run_id=lineage.run_id
     and external.operation=attempt.oa_action
     and external.provider_result_json=attempt.oa_action_result_json
     and json_extract(external.target_identifiers_json,'$.process_instance_id')=attempt.oa_process_instance_id
     and json_extract(external.target_identifiers_json,'$.task_id')=attempt.oa_task_id
    order by attempt.id,external.provider_receipt_id"""
    grouped = {}
    for row in db.execute(sql):
        grouped.setdefault(int(row[0]), {})[int(row[1])] = (row[2], row[3])
    db.execute("drop table temp._reply_receipt_reference_match_source")
    return {attempt_id: next(iter(receipts.items()))
            for attempt_id, receipts in grouped.items() if len(receipts) == 1}


@dataclass
class ReplyReceiptReferenceRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def publish(self):
        path = self.database_path
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if tuple(_marker(db) or ()) != self.source_marker or schema_objects(db) != source_objects():
                    raise ValueError("Reply receipt release source changed after preparation")
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Reply receipt release source FK findings changed")
                matches = _matches(db)
                for sql in DDL:
                    db.execute(sql)
                from app.action_storage import update_action
                for attempt_id, (receipt_id, (body, _provider_body)) in matches.items():
                    add_reference(db, attempt_id=attempt_id, receipt_id=receipt_id, expected_body=body)
                    update_action(db, "reply_attempts", attempt_id, {"oa_action_result_json": ""})
                _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                from app.current_schema_manifest import schema_is_current
                if (not schema_is_current(db)
                        or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("Reply receipt release target schema or integrity mismatch")
                db.commit()
                self.published = True
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        from app.current_schema_manifest import require_current_pair
        require_current_pair(self.database_path)

    def rollback(self):
        if not self.published:
            with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
                if tuple(_marker(db) or ()) != self.source_marker or schema_objects(db) != source_objects():
                    raise ValueError("Reply receipt release did not publish and source is no longer exact")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("Reply receipt release unchanged-source verification failed")
            return
        path = self.database_path
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                from app.current_schema_manifest import schema_is_current
                if _marker(db) is None or _marker(db)[0] != TARGET_STORE_VERSION or not schema_is_current(db):
                    raise ValueError("Reply receipt inverse requires the exact published target")
                refs = db.execute("""select cast(attempt.legacy_key as integer) as attempt_id,
                    receipt.provider_result_json
                    from reply_attempt_receipt_refs ref
                    join actions attempt on attempt.id=ref.attempt_id
                      and attempt.record_type='reply_attempts'
                    join action_receipts receipt on receipt.id=ref.receipt_id
                    order by attempt.id""").fetchall()
                if db.execute("select count(*) from reply_attempt_receipt_refs").fetchone()[0] != len(refs):
                    raise ValueError("Reply receipt inverse found an unresolved receipt reference")
                from app.action_storage import update_action
                for row in refs:
                    update_action(db, "reply_attempts", int(row["attempt_id"]),
                                  {"oa_action_result_json": row["provider_result_json"]})
                db.execute("drop table reply_attempt_receipt_refs")
                _write_marker(db, self.source_marker[0], self.source_marker[1])
                if (schema_objects(db) != source_objects()
                        or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("Reply receipt inverse schema, FK or integrity mismatch")
                db.commit()
                self.published = False
            except BaseException:
                db.rollback()
                raise


def prepare_reply_receipt_reference_release(path: Path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        from app.action_entity_schema_release import _markers
        marker = _marker(db)
        email_marker, email_versions = _markers(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or not email_versions or email_versions[-1][0] != EMAIL_VERSION
                or schema_objects(db) != source_objects()):
            raise ValueError("unsupported Store 2026-10-23.1 / Email54 source schema")
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Reply receipt release source quick_check failed")
        return ReplyReceiptReferenceRelease(path, tuple(marker), _foreign_keys(db))
