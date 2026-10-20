"""Finite Task20 -> lifecycle conversion and inverse from current facts."""
from app import task20_definitions as source
from app.task20_current_schema import _current_objects
from app.task_lifecycle_current_schema import task_lifecycle_objects
from app.task_lifecycle_definitions import (
    LIFECYCLE_FIELDS, SOURCE_FIELDS, eligible_adapter,
)
from app.task_lifecycle_jsonb import register, restore_sql
from app.storage_payload import TASK_RECORD_TYPES
from app.run_storage import _schema_objects

_ROWS = "_task_lifecycle_rows"
_WATER = "_task_lifecycle_water"
_BASE = ("id", "_kind", "legacy_key", "payload_json", "parent_id", "current_input_id", "claimed_input_id")


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _require(db, *, current):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Task lifecycle conversion requires caller-owned FK-off transaction")
    for name in ("tasks", _ROWS, _WATER):
        if db.execute("select 1 from temp.sqlite_schema where name=? collate nocase", (name,)).fetchone():
            raise ValueError("Task lifecycle TEMP source or staging collision")
    expected = task_lifecycle_objects() if current else _current_objects()["tasks"]
    if _schema_objects(db, "tasks") != expected:
        raise ValueError("unsupported Task lifecycle source declaration")
    if db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Task lifecycle source integrity mismatch")
    return expected


def _stage(db, *, current):
    fields = (*_BASE, *LIFECYCLE_FIELDS)
    db.execute("create temp table " + _ROWS + "(" + ",".join(map(_quote, fields)) + ")")
    # No CTAS affinity or row archive. This stage only lives in the conversion
    # transaction; its ID index bounds each source-qualified scalar transfer.
    db.execute("create index temp.idx_task_lifecycle_stage_id on " + _ROWS + "(id)")
    selected = fields if current else _BASE
    names = ",".join(map(_quote, selected))
    db.execute(f"insert into temp.{_ROWS}({names}) select {names} from main.tasks")
    db.execute(f"create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence")


def _forward_values(db):
    for kind, fields in SOURCE_FIELDS.items():
        old = source.TASK20_PAYLOAD_FIELDS[kind]
        assignments = [_quote(target) + "=ceo_task_cell(payload_json," + str(old.index(field)) + ")"
                       for field, target in fields.items()]
        assignments.append("payload_json=ceo_task_domain(" + repr(kind) + ",payload_json)")
        db.execute(f"update temp.{_ROWS} set " + ",".join(assignments) + " where _kind=?",
                   (TASK_RECORD_TYPES.code(kind),))
    # The unchanged State is passive metadata. Only the exact currently eligible
    # adapter/source key supplies Task claim facts; missing means SQL NULL.
    mapping = {"lease_owner": "owner", "owner_pid": "owner_pid", "generation": "generation",
               "lease_expires_at": "lease_expires_at", "adapter_name": "adapter_name"}
    join = (" from main.state ledger join main.tasks original on original.id=staged.id "
            "where ledger.record_type='dispatcher_claim_leases' and ledger.adapter_name="
            + eligible_adapter("original") + " and ledger.source_id=original.legacy_key")
    db.execute(f"update temp.{_ROWS} as staged set " + ",".join(
        _quote(target) + "=(select jsonb(ledger.payload_json -> " + repr("$." + field) + ")" + join + ")"
        for target, field in mapping.items()
    ) + " where _kind in (" + ",".join(str(TASK_RECORD_TYPES.code(k)) for k in (
        "reply_tasks", "work_summary_inputs", "meeting_alignment_jobs", "okr_review_requests")) + ")")


def _inverse_payload(kind, alias=""):
    prefix = alias + "." if alias else ""
    return restore_sql(kind, prefix + "payload_json", alias)


def _rebuild(db, *, current):
    register(db)
    findings = sorted(tuple(r) for r in db.execute("pragma foreign_key_check"))
    _stage(db, current=current)
    if current:
        for kind in SOURCE_FIELDS:
            db.execute(f"update temp.{_ROWS} set payload_json=" + _inverse_payload(kind)
                       + " where _kind=?", (TASK_RECORD_TYPES.code(kind),))
    else:
        _forward_values(db)
        # Exact byte comparison of the current-fact inverse before DDL mutation.
        for kind in SOURCE_FIELDS:
            restored = _inverse_payload(kind, "staged")
            if db.execute(f"select 1 from temp.{_ROWS} staged join main.tasks original on original.id=staged.id "
                          "where original.record_type=? and " + restored + " is not original.payload_json limit 1",
                          (kind,)).fetchone():
                raise ValueError("Task lifecycle source JSONB does not reconstruct exactly")
    objects = _current_objects()["tasks"] if current else task_lifecycle_objects()
    db.execute("drop table main.tasks")
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    fields = _BASE if current else (*_BASE, *LIFECYCLE_FIELDS)
    names = ",".join(map(_quote, fields))
    db.execute("insert into main.tasks(" + names + ") select " + names + f" from temp.{_ROWS} order by id")
    for category in ("index", "trigger"):
        for obj in objects:
            if obj["type"] == category:
                db.execute(obj["sql"])
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}")
    typed = names + "," + ",".join("typeof(" + _quote(f) + ")" for f in fields)
    for left, right in (("main.tasks", "temp." + _ROWS), ("temp." + _ROWS, "main.tasks")):
        if db.execute(f"select {typed} from {left} except select {typed} from {right} limit 1").fetchone():
            raise ValueError("Task lifecycle conversion changed stored identity/value/type")
    if (_schema_objects(db, "tasks") != objects
            or sorted(tuple(r) for r in db.execute("pragma foreign_key_check")) != findings):
        raise ValueError("Task lifecycle conversion changed declaration/FK findings")
    db.execute(f"drop table temp.{_ROWS}")
    db.execute(f"drop table temp.{_WATER}")


def migrate_task_lifecycle(db):
    declarations = _require(db, current=False)
    _rebuild(db, current=False)
    return {"tasks": declarations}


def restore_task_lifecycle(db, metadata):
    _require(db, current=True)
    if metadata != {"tasks": _current_objects()["tasks"]}:
        raise ValueError("unsupported Task lifecycle inverse metadata")
    _rebuild(db, current=True)
