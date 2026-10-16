"""Unselected typed Skill receipt action storage and exact public projection."""
from dataclasses import dataclass
import json
import sqlite3

from app.action_storage import _encoded_sqlite_value, _native_integer_primary_key, _raise_autoincrement_full
from app.action_skill_config_definitions import SKILL_RECEIPT_KINDS

@dataclass(frozen=True)
class SkillLoadReceipt:
    id: int
    config_id: int
    pid: int
    loaded_json: str
    error: str
    created_at: str

def loaded_body(db: sqlite3.Connection, config_ref: int, error: str) -> str:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    if error:
        return "{}"
    rows = db.execute(
        "select b.skill_binding_skill_id,r.skill_revision_sha256 from configs b "
        "join configs r on r.id=b.skill_binding_revision_ref "
        "where b.record_type='runtime_skill_bindings' and "
        "b.skill_binding_config_ref=? and b.skill_binding_enabled=1",
        (config_ref,),
    ).fetchall()
    return json.dumps({int(skill_id): digest for skill_id, digest in rows}, sort_keys=True)

def load_receipt_projection(db: sqlite3.Connection, action_id: int) -> SkillLoadReceipt:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute(
        "select cast(a.legacy_key as integer),a.skill_config_id,a.skill_pid,"
        "a.runtime_config_ref,a.skill_error,a.skill_created_at from actions a "
        "where a.id=? and a.record_type='runtime_skill_load_receipts'",
        (action_id,),
    ).fetchone()
    if row is None:
        raise LookupError(action_id)
    public_id, config_id, pid, config_ref, error, created_at = row
    return SkillLoadReceipt(int(public_id), int(config_id), int(pid),
                            str(loaded_body(db, config_ref, error)), str(error), str(created_at))

def compare_source_load_body(db: sqlite3.Connection, action_id: int, source_body) -> None:
    projected = load_receipt_projection(db, action_id)
    if not isinstance(source_body, str) or source_body.encode("utf-8") != projected.loaded_json.encode("utf-8"):
        raise ValueError("source load body is not byte-exactly reconstructable")


MAX_ROWID = 2**63 - 1

def _skill_parent(db: sqlite3.Connection, kind: str, key: int) -> int:
    row = db.execute("select id from configs where record_type=? and numeric_public_key=?",
                     (kind, key)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("Skill receipt parent missing")
    return row[0]

def next_skill_receipt_id(db: sqlite3.Connection, kind: str) -> int:
    if kind not in SKILL_RECEIPT_KINDS:
        raise ValueError(kind)
    name = "actions:" + kind
    row = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
    watermark = row[0] if row else 0
    maximum = db.execute("select max(cast(legacy_key as integer)) from actions "
                         "where record_type=?", (kind,)).fetchone()[0]
    last = max(watermark, maximum or 0, 0)
    if last == MAX_ROWID:
        if db.in_transaction:
            db.rollback()
        _raise_autoincrement_full()
    return last + 1

def insert_skill_receipt(db: sqlite3.Connection, kind: str, values: dict,
                         *, source_id: int | None = None) -> int:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt writes require caller transaction")
    if kind not in SKILL_RECEIPT_KINDS:
        raise ValueError(kind)
    export = kind == "managed_skill_export_receipts"
    allowed = ({"revision_id", "sha256", "path", "created_at"} if export else
               {"config_id", "pid", "error", "loaded_json", "created_at"})
    if set(values) - allowed:
        raise ValueError("unknown Skill receipt source field")
    required = {"revision_id", "sha256", "path"} if export else {"config_id", "pid"}
    if not required <= set(values):
        raise ValueError("missing Skill receipt source field")
    identity = (next_skill_receipt_id(db, kind) if source_id is None else
                _native_integer_primary_key(source_id))
    payload = dict(values)
    source_body = payload.pop("loaded_json", "{}")
    payload.setdefault("created_at", db.execute("select current_timestamp").fetchone()[0])
    if export:
        ref = _skill_parent(db, "managed_skill_revisions", payload["revision_id"])
        ref_col = "skill_revision_ref"
    else:
        payload.setdefault("error", "")
        ref = _skill_parent(db, "runtime_skill_configs", payload["config_id"])
        ref_col = "runtime_config_ref"
        exact = loaded_body(db, ref, payload["error"])
        if not isinstance(source_body, str) or source_body.encode("utf-8") != exact.encode("utf-8"):
            raise ValueError("source load body is not byte-exactly reconstructable")
    sql = ("insert into actions(record_type,legacy_key,payload_json," + ref_col +
           ") values(?,?,?,?)")
    encoded = {field: _encoded_sqlite_value(value) for field, value in payload.items()}
    db.execute(sql, (kind, str(identity), json.dumps(encoded, ensure_ascii=False,
               separators=(",", ":")), ref))
    return identity

def list_skill_load_receipts(db: sqlite3.Connection, config_id: int) -> list[SkillLoadReceipt]:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    rows = db.execute("select id from actions where record_type='runtime_skill_load_receipts' "
                      "and skill_config_id=? order by cast(legacy_key as integer)",
                      (config_id,)).fetchall()
    return [load_receipt_projection(db, row[0]) for row in rows]

def latest_skill_load_receipt_for_pid(db: sqlite3.Connection, pid: int) -> SkillLoadReceipt | None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select id from actions where record_type='runtime_skill_load_receipts' "
                     "and skill_pid=? order by cast(legacy_key as integer) desc limit 1",
                     (pid,)).fetchone()
    return load_receipt_projection(db, row[0]) if row else None

@dataclass(frozen=True)
class SkillExportReceipt:
    id: int
    revision_id: int
    sha256: str
    path: str
    created_at: str

def export_receipt_projection(db: sqlite3.Connection, action_id: int) -> SkillExportReceipt:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select cast(legacy_key as integer),skill_revision_id,skill_sha256,"
                     "skill_path,skill_created_at from actions where id=? and "
                     "record_type='managed_skill_export_receipts'", (action_id,)).fetchone()
    if row is None:
        raise LookupError(action_id)
    public_id, revision_id, sha256, path, created_at = row
    return SkillExportReceipt(int(public_id), int(revision_id), str(sha256),
                              str(path), str(created_at))

def latest_managed_skill_export_receipt(db: sqlite3.Connection,
                                         revision_id: int) -> SkillExportReceipt | None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Skill receipt read requires caller snapshot")
    row = db.execute("select id from actions where record_type='managed_skill_export_receipts' "
                     "and skill_revision_id=? order by cast(legacy_key as integer) desc limit 1",
                     (revision_id,)).fetchone()
    return export_receipt_projection(db, row[0]) if row else None
