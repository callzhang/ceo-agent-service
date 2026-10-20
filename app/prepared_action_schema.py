"""Rehome current Postfix child facts; inverse reconstructs current children.

Parent and unrelated physical IDs, values, payload bytes and real sequence rows
are exact. Retired private child IDs and JSON legacy-key spelling are not public
source facts and are deliberately not retained in another ledger.
"""
from functools import lru_cache

from app import action_storage as actions
from app.action_email_storage import _columns, _quote
from app.model_action_schema import _dependencies
from app.model_result_schema import _inbound_dependencies
from app.prepared_action_definitions import PARENT, CHILDREN, COLUMNS, SLOTS, prepared_child_projection
from app.specialized_action_schema import specialized_action_objects

_STAGING = "_prepared_action_rows"
_WATER = "_prepared_action_water"


@lru_cache(maxsize=2)
def prepared_action_objects(reply_variant):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True,
        postfix_owner=True, **ACTION_OPTIONS)


def _require_source(db, *, owned):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("prepared owner transition requires caller-owned FK-off transaction")
    _collision(db, ("actions", _STAGING, _WATER))
    objects = actions._objects(db, "actions")
    declaration = prepared_action_objects if owned else specialized_action_objects
    variant = next((variant for variant in actions.REPLY_VARIANTS if objects == declaration(variant)), None)
    if variant is None:
        raise ValueError("unsupported prepared owner Actions source declaration")
    _dependencies(db)
    _inbound_dependencies(db)
    if db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("prepared owner source integrity is invalid")
    return variant


def _facts(db, *, owned):
    """Read exact public composite identity, raw provider text and own clocks."""
    facts = {}
    for kind in (PARENT, *CHILDREN):
        sql = prepared_child_projection(kind) if owned and kind in CHILDREN else actions._projection(kind)
        rows = [tuple(row) for row in db.execute(sql + " order by channel,delivery_key")]
        for row in rows:
            actions._check_fields(kind, dict(zip(actions.SOURCE_COLUMNS[kind], row, strict=True)))
            if any(type(value) is not str for value in row):
                raise ValueError("unrepresentable prepared source fact")
        facts[kind] = rows
    parents = {row[:2] for row in facts[PARENT]}
    if any(row[:2] not in parents for kind in CHILDREN for row in facts[kind]):
        raise ValueError("prepared child lacks its exact parent")
    if not owned:
        unused = set(_columns(db, "actions")) - {"id", "_kind", "legacy_key", "payload_json"}
        if db.execute("select 1 from actions where record_type in (?,?) and ("
            + " or ".join(_quote(column) + " is not null" for column in sorted(unused))
            + ") limit 1", CHILDREN).fetchone():
            raise ValueError("retired prepared child has another stored fact")
    return facts


def _rebuild(db, variant, facts, *, owned):
    source_columns = _columns(db, "actions")
    preserved_columns = tuple(column for column in source_columns if column not in COLUMNS)
    names = ",".join(map(_quote, preserved_columns))
    findings = sorted(tuple(row) for row in db.execute("pragma foreign_key_check"))
    db.execute(f"create temp table {_STAGING} as select {names} from main.actions "
               "where record_type not in (?,?)", CHILDREN)
    db.execute(f"create unique index temp.idx_prepared_action_rows_id on {_STAGING}(id)")
    db.execute(f"create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence")
    if owned:
        for column in COLUMNS:
            db.execute(f"alter table temp.{_STAGING} add column {column} text")
        parent_ids = {(row[1], row[2]): row[0] for row in db.execute(
            "select id,channel,delivery_key from actions where record_type=?", (PARENT,))}
        for kind in CHILDREN:
            columns = tuple(SLOTS[kind].values())
            for channel, key, *values in facts[kind]:
                db.execute(f"update temp.{_STAGING} set "
                    + ",".join(column + "=?" for column in columns) + " where id=?",
                    (*values, parent_ids[channel, key]))
    objects = prepared_action_objects(variant) if owned else specialized_action_objects(variant)
    db.execute("drop table main.actions")
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    target_columns = _columns(db, "actions")
    target_names = ",".join(map(_quote, target_columns))
    db.execute(f"insert into main.actions({target_names}) select {target_names} from temp.{_STAGING} order by id")
    for category in ("index", "trigger"):
        for obj in objects:
            if obj["type"] == category:
                db.execute(obj["sql"])
    if not owned:
        for kind in CHILDREN:
            for row in facts[kind]:
                actions._insert_action(db, kind, dict(zip(actions.SOURCE_COLUMNS[kind], row, strict=True)))
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}")
    # The retained rows are compared byte-for-byte and with SQLite type tags.
    compared = target_columns if owned else preserved_columns
    values = ",".join(map(_quote, compared)) + "," + ",".join(
        "typeof(" + _quote(column) + ")" for column in compared)
    retained = "(select * from main.actions where record_type not in ('outbound_postfix_receipts','native_reply_dispatches'))"
    for left, right in ((retained, "temp." + _STAGING), ("temp." + _STAGING, retained)):
        if db.execute(f"select {values} from {left} except select {values} from {right} limit 1").fetchone():
            raise ValueError("prepared owner transition changed retained values or types")
    if (actions._objects(db, "actions") != objects or _facts(db, owned=owned) != facts
            or sorted(tuple(row) for row in db.execute("pragma foreign_key_check")) != findings
            or [tuple(row) for row in db.execute("select rowid,name,seq from main.sqlite_sequence order by rowid")]
            != [tuple(row) for row in db.execute(f"select rowid,name,seq from temp.{_WATER} order by rowid")]):
        raise ValueError("prepared owner transition changed declaration, facts, sequences or FK findings")
    db.execute(f"drop table temp.{_STAGING}")
    db.execute(f"drop table temp.{_WATER}")


def migrate_prepared_actions(db):
    variant = _require_source(db, owned=False)
    facts = _facts(db, owned=False)
    _rebuild(db, variant, facts, owned=True)
    return {"reply_variant": variant}


def restore_prepared_actions(db, metadata):
    variant = _require_source(db, owned=True)
    if metadata != {"reply_variant": variant}:
        raise ValueError("unsupported prepared owner inverse metadata")
    facts = _facts(db, owned=True)
    _rebuild(db, variant, facts, owned=False)
