"""Finite Business four-kind source-bound migration reference."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_email_storage import _quote, _sequence, _set_sequence
from app.action_storage import _encoded_native_sqlite_value
from app.business_definitions import (
    BUSINESS_COMMON, BUSINESS_FIELDS, BUSINESS_FRESH_FIELDS,
    BUSINESS_REFERENCES,
    BUSINESS_TABLE_DDL, BUSINESS_INDEX_DDL, BUSINESS_TRIGGER_DDL,
    business_column, business_payload_fields,
)
from app.business_storage import create_business_schema
from app.task_schema_release import _foreign_keys

SOURCE_PARENTS = {
    "task": "business_tasks",
    "project": "business_projects",
    "anchor": "business_anchors",
    "project_context_revision": "business_project_context_revisions",
}
RETAINED_CHILD_FKS = {
    "business_task_evidence": {"task_id": "task"},
    "business_task_date_evidence": {"task_id": "task"},
    "business_task_events": {"task_id": "task"},
    "business_project_evidence": {"project_id": "project"},
    "business_task_dingtalk_links": {"business_task_id": "task"},
    "business_task_follow_ups": {"business_task_id": "task"},
    "business_task_todo_sync_outbox": {"business_task_id": "task"},
    "business_task_relations": {"from_task_id": "task", "to_task_id": "task"},
    "business_work_cluster_tasks": {"task_id": "task"},
    "business_task_anchor_links": {"task_id": "task", "anchor_id": "anchor"},
    "business_project_candidates": {"confirmed_project_id": "project"},
    "business_attention_items": {"anchor_id": "anchor"},
    "business_attention_tasks": {"task_id": "task"},
    "business_attention_proposal_tasks": {"task_id": "task"},
    "business_legacy_links": {
        "task_id": "task", "anchor_id": "anchor", "project_id": "project",
    },
}
PUBLIC_ALIAS = {
    "task": "task_public_id", "project": "project_public_id",
    "anchor": "anchor_public_id",
}
COMPOSITE_ROWID_CHILDREN = (
    "business_task_evidence", "business_project_evidence",
    "business_task_relations", "business_work_cluster_tasks",
    "business_attention_tasks", "business_attention_proposal_tasks",
)
AUTOINCREMENT_CHILDREN = tuple(
    name for name in RETAINED_CHILD_FKS if name not in COMPOSITE_ROWID_CHILDREN
)
PARENT_HASHES = {
    "actual": (
        "91b5763374243a76dc446881190d1f8e68ea9526a82ba98c920e0fe935957d07",
        "6d73ac34c4cc559f164afbe344d76e913d3709ebfecb76646b2307b37e7f85b4",
        "d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f",
        "2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8",
    ),
    "fresh": (
        "19341a7f00ed976b0fee462b1fcfe1bcadc061ebc555d6757d9e89c1151b81c0",
        "4c75ae1efd72dc5b27772a9a69454a2f1bcca619b35376fa087ec6206c70cf98",
        "d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f",
        "2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8",
    ),
}
SOURCE_CHILD_HASHES = {
    "actual": "d59d3ae841b3e410305079267d4c2a12b26103fddc06929cf316ec78ab8e28c1",
    "fresh": "d4bab7b6cd1e59bbcd2c5154d4b43bfd06196ed5ba15cae5f8149d63aef02719",
}
SOURCE_CHILD_FULL_HASHES = {
    "actual": "0b30b896fce6bb46d0cc1a8e5b20041059965e52da6339c91509763fd37a77f4",
    "fresh": "0ab4033f06c805811bf6193fd48e32a66f61a7e1885e238c2bdc9f08c6e6b78d",
}
TARGET_CHILD_HASHES = {
    "actual": "789a625b97df6c223520542904c3bf3a56b8da127800e908d470e1930ad39587",
    "fresh": "bda7bbe8141657cf0e8b9937b1aa9c3eb44d6f9b48c15996720ceff3e69cd8b1",
}


def translated_child_sql(table: str, sql: str, *, inverse: bool = False) -> str:
    for column, kind in RETAINED_CHILD_FKS[table].items():
        source = f"foreign key({column}) references {SOURCE_PARENTS[kind]}(id)"
        target = f"foreign key({column}) references business({PUBLIC_ALIAS[kind]})"
        old, new = (target, source) if inverse else (source, target)
        if sql.count(old) != 1:
            raise ValueError(f"unsupported {table}.{column} FK declaration")
        sql = sql.replace(old, new, 1)
    return sql


def _shape(declarations: dict) -> dict:
    return {
        name: {key: item[key] for key in ("sql", "columns", "foreign_keys")}
        for name, item in declarations.items()
    }


def _shape_hash(declarations: dict) -> str:
    return hashlib.sha256(json.dumps(
        _shape(declarations), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _objects(db: sqlite3.Connection, names) -> dict:
    return {name: _source_declaration(db, name) for name in names}


def _source_variant(parents: dict, children: dict) -> str:
    parent_hashes = tuple(_fingerprint(parents[name]) for name in SOURCE_PARENTS.values())
    child_hash = _shape_hash(children)
    for variant in PARENT_HASHES:
        if (parent_hashes == PARENT_HASHES[variant]
                and child_hash == SOURCE_CHILD_HASHES[variant]
                and _fingerprint(children) == SOURCE_CHILD_FULL_HASHES[variant]):
            return variant
    raise ValueError("unsupported Business source declarations")


def _snapshot(db: sqlite3.Connection, name: str, *, hidden_rowid: bool = False) -> dict:
    columns = tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")
                    if row[6] == 0)
    selected = (("rowid",) if hidden_rowid else ()) + columns
    selected_sql = ",".join(_quote(column) for column in selected)
    values = tuple(tuple(row) for row in db.execute(
        f"select {selected_sql} from main.{_quote(name)} order by rowid"
    ))
    types_sql = ",".join(f"typeof({_quote(column)})" for column in selected)
    types = tuple(tuple(row) for row in db.execute(
        f"select {types_sql} from main.{_quote(name)} order by rowid"
    ))
    return {"columns": columns, "rows": values, "types": types,
            "hidden_rowid": hidden_rowid}


def _restore_rows(db: sqlite3.Connection, name: str, snap: dict) -> None:
    columns = (("rowid",) if snap["hidden_rowid"] else ()) + snap["columns"]
    sql = (f"insert into main.{_quote(name)}("
           + ",".join(_quote(column) for column in columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    db.executemany(sql, snap["rows"])
    if _snapshot(db, name, hidden_rowid=snap["hidden_rowid"]) != snap:
        raise ValueError(f"{name} copied values or storage classes differ")


def _create_auxiliary(db: sqlite3.Connection, definition: dict) -> None:
    for item in reversed(definition["indexes"]):
        if item["sql"] is not None:
            db.execute(item["sql"])
    for item in definition["triggers"]:
        db.execute(item["sql"])


def _child_waters(db: sqlite3.Connection) -> dict:
    return {name: _sequence(db, name) for name in AUTOINCREMENT_CHILDREN}


def _parent_waters(db: sqlite3.Connection, *, target: bool) -> dict:
    return {kind: _sequence(db, "business:" + kind if target else table)
            for kind, table in SOURCE_PARENTS.items()}


def _source_fk_findings(db: sqlite3.Connection) -> tuple:
    findings = _foreign_keys(db)
    moved = set(SOURCE_PARENTS.values())
    if any(table in moved or parent in moved for table, _rowid, parent, _id in findings):
        raise ValueError("broken moved Business parent source")
    return findings


def _assert_no_temp_relation_collision(db: sqlite3.Connection, names) -> None:
    names = tuple(names)
    placeholders = ",".join("?" for _ in names)
    if db.execute(
        "select 1 from temp.sqlite_schema where type in ('table','view') "
        f"and name collate nocase in ({placeholders}) limit 1", names,
    ).fetchone():
        raise ValueError("Business TEMP name collision")


def capture_business_source(db: sqlite3.Connection) -> dict:
    # Unqualified index/trigger DDL resolves its ON table through TEMP first,
    # even when CREATE TABLE and row snapshots target MAIN explicitly.
    _assert_no_temp_relation_collision(db, ("business", *RETAINED_CHILD_FKS))
    parents = _objects(db, SOURCE_PARENTS.values())
    children = _objects(db, RETAINED_CHILD_FKS)
    variant = _source_variant(parents, children)
    if db.execute("select 1 from main.sqlite_schema where name='business'").fetchone():
        raise ValueError("mixed Business source")
    if db.execute("select 1 from main.sqlite_sequence where name like 'business:%' limit 1").fetchone():
        raise ValueError("mixed Business allocator source")
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=(*SOURCE_PARENTS.values(), *RETAINED_CHILD_FKS))
    for sql in (BUSINESS_TABLE_DDL, *BUSINESS_INDEX_DDL, *BUSINESS_TRIGGER_DDL):
        words = sql.split()
        name = words[2 if words[1] in ("table", "index", "trigger") else 3]
        if db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (name,)
        ).fetchone():
            raise ValueError("Business target name collision")
    return {"variant": variant, "parents": parents, "children": children,
            "fk_findings": _source_fk_findings(db)}


def business_inverse_metadata(source: dict) -> dict:
    return {key: source[key] for key in ("variant", "parents", "children")}


def _assert_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0] != 0:
        raise sqlite3.ProgrammingError("Business migration needs caller FK-off transaction")


def _parent_facts(db: sqlite3.Connection) -> dict:
    return {kind: _snapshot(db, table) for kind, table in SOURCE_PARENTS.items()}


def _child_facts(db: sqlite3.Connection) -> dict:
    return {name: _snapshot(db, name, hidden_rowid=name in COMPOSITE_ROWID_CHILDREN)
            for name in RETAINED_CHILD_FKS}


def _parent_dicts(facts: dict) -> dict:
    return {kind: [dict(zip(snap["columns"], row, strict=True)) for row in snap["rows"]]
            for kind, snap in facts.items()}


def _physical_ids(parents: dict) -> dict:
    pairs = ((kind, row["id"]) for kind in SOURCE_PARENTS for row in parents[kind])
    physical = {pair: position for position, pair in enumerate(pairs, start=1)}
    if len(physical) != sum(len(rows) for rows in parents.values()):
        raise ValueError("duplicate Business public ID within kind")
    for kind, ref in BUSINESS_REFERENCES.items():
        field, _column, parent_kind = ref
        for row in parents[kind]:
            if row[field] is not None and (parent_kind, row[field]) not in physical:
                raise ValueError("broken moved Business parent source")
    return physical


def _drop_source(db: sqlite3.Connection) -> None:
    for name in RETAINED_CHILD_FKS:
        db.execute(f"drop table main.{_quote(name)}")
    for name in SOURCE_PARENTS.values():
        db.execute(f"drop table main.{_quote(name)}")


def _create_children(db: sqlite3.Connection, declarations: dict, *, target: bool) -> None:
    for name, definition in declarations.items():
        sql = translated_child_sql(name, definition["sql"]) if target else definition["sql"]
        db.execute(sql)
    for definition in declarations.values():
        _create_auxiliary(db, definition)


def _insert_business_parents(db: sqlite3.Connection, parents: dict, physical: dict) -> None:
    columns = ("id", "record_type", "legacy_key", "payload_json", "title",
               "status", "created_at", "updated_at", "merged_task_ref",
               "canonical_anchor_ref", "context_project_ref")
    sql = ("insert into main.business(" + ",".join(columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    for kind in SOURCE_PARENTS:
        for row in parents[kind]:
            refs = {"merged_task_ref": None, "canonical_anchor_ref": None,
                    "context_project_ref": None}
            if kind in BUSINESS_REFERENCES:
                field, column, parent_kind = BUSINESS_REFERENCES[kind]
                if row[field] is not None:
                    refs[column] = physical[(parent_kind, row[field])]
            payload = json.dumps(
                {field: _encoded_native_sqlite_value(row[field])
                 for field in business_payload_fields(kind)},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            )
            common = {field: row.get(field, "") for field in BUSINESS_COMMON}
            db.execute(sql, (
                physical[(kind, row["id"])], kind, str(row["id"]), payload,
                common["title"], common["status"], common["created_at"],
                common["updated_at"], refs["merged_task_ref"],
                refs["canonical_anchor_ref"], refs["context_project_ref"],
            ))


def _projected_parent_facts(db: sqlite3.Connection, kind: str, variant: str) -> dict:
    fields = (BUSINESS_FRESH_FIELDS if variant == "fresh" else BUSINESS_FIELDS)[kind]
    columns = tuple(business_column(kind, field) for field in fields)
    selected = ",".join(f"{_quote(column)} as {_quote(field)}"
                        for field, column in zip(fields, columns, strict=True))
    types = ",".join(f"typeof({_quote(column)})" for column in columns)
    source = " from main.business where record_type=? order by public_id"
    rows = tuple(tuple(row) for row in db.execute(
        f"select {selected}{source}", (kind,)
    ))
    classes = tuple(tuple(row) for row in db.execute(
        f"select {types}{source}", (kind,)
    ))
    return {"columns": fields, "rows": rows, "types": classes,
            "hidden_rowid": False}


def _verify_target_rows(db: sqlite3.Connection, source: dict,
                        parent_facts: dict, child_facts: dict,
                        source_parent_waters: dict, source_child_waters: dict) -> None:
    for kind, snap in parent_facts.items():
        if _projected_parent_facts(db, kind, source["variant"]) != snap:
            raise ValueError(f"{kind} Business projection differs from source")
    for name, snap in child_facts.items():
        if _snapshot(db, name, hidden_rowid=snap["hidden_rowid"]) != snap:
            raise ValueError(f"{name} retained child differs from source")
    if (_parent_waters(db, target=True) != source_parent_waters or
            _child_waters(db) != source_child_waters):
        raise ValueError("Business public allocator waters differ")
    if _foreign_keys(db) != source["fk_findings"]:
        raise ValueError("Business source FK findings changed")
    if db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Business copy quick_check failed")


def migrate_business_schema(db: sqlite3.Connection) -> dict:
    _assert_transaction(db)
    source = capture_business_source(db)
    source_parent_waters = _parent_waters(db, target=False)
    source_child_waters = _child_waters(db)
    parent_facts, child_facts = _parent_facts(db), _child_facts(db)
    parents = _parent_dicts(parent_facts)
    physical = _physical_ids(parents)
    _drop_source(db)
    create_business_schema(db)
    _insert_business_parents(db, parents, physical)
    _create_children(db, source["children"], target=True)
    for name, snap in child_facts.items():
        _restore_rows(db, name, snap)
    for kind, water in source_parent_waters.items():
        _set_sequence(db, "business:" + kind, water)
    for name, water in source_child_waters.items():
        _set_sequence(db, name, water)
    _verify_target_rows(db, source, parent_facts, child_facts,
                        source_parent_waters, source_child_waters)
    return business_inverse_metadata(source)


def _target_children_are_current(db: sqlite3.Connection, variant: str) -> bool:
    return _fingerprint(_objects(db, RETAINED_CHILD_FKS)) == TARGET_CHILD_HASHES[variant]


def _canonical_create(sql: str) -> str:
    for prefix in ("create unique index", "create index", "create table", "create trigger"):
        if sql.startswith(prefix):
            return prefix.upper() + sql[len(prefix):]
    raise ValueError("unsupported Business DDL")


def business_schema_is_current(db: sqlite3.Connection, variant: str | None = None) -> bool:
    if db.execute(
        "select 1 from main.sqlite_schema where type='table' and name in "
        "('business_tasks','business_projects','business_anchors',"
        "'business_project_context_revisions') limit 1"
    ).fetchone():
        return False
    table = db.execute(
        "select sql from main.sqlite_schema where type='table' and name='business'"
    ).fetchone()
    if table is None or table[0] != _canonical_create(BUSINESS_TABLE_DDL):
        return False
    expected = {_canonical_create(sql) for sql in
                (*BUSINESS_INDEX_DDL, *BUSINESS_TRIGGER_DDL)}
    actual = {row[0] for row in db.execute(
        "select sql from main.sqlite_schema where tbl_name='business' "
        "and type in ('index','trigger') and sql is not null"
    )}
    if actual != expected:
        return False
    versions = (variant,) if variant is not None else tuple(TARGET_CHILD_HASHES)
    return any(_target_children_are_current(db, current) for current in versions)


def _target_parent_facts(db: sqlite3.Connection, variant: str) -> dict:
    return {kind: _projected_parent_facts(db, kind, variant)
            for kind in SOURCE_PARENTS}


def _recreate_source(db: sqlite3.Connection, source: dict) -> None:
    for definition in source["parents"].values():
        db.execute(definition["sql"])
    for definition in source["parents"].values():
        _create_auxiliary(db, definition)
    _create_children(db, source["children"], target=False)


def restore_business_schema(db: sqlite3.Connection, source: dict) -> None:
    _assert_transaction(db)
    _assert_no_temp_relation_collision(db, (*SOURCE_PARENTS.values(), *RETAINED_CHILD_FKS))
    variant = source["variant"]
    if _source_variant(source["parents"], source["children"]) != variant:
        raise ValueError("unsupported Business source declarations")
    if not business_schema_is_current(db, variant):
        raise ValueError("unsupported current Business declarations")
    before_fk = _foreign_keys(db)
    parents = _target_parent_facts(db, variant)
    children = _child_facts(db)
    parent_waters = _parent_waters(db, target=True)
    child_waters = _child_waters(db)
    for name in RETAINED_CHILD_FKS:
        db.execute(f"drop table main.{_quote(name)}")
    db.execute("drop table main.business")
    # DROP TABLE removes sqlite_sequence['business'], but the four public-ID
    # allocator aliases are manually named rows and survive that DROP.
    for kind in SOURCE_PARENTS:
        _set_sequence(db, "business:" + kind, None)
    _recreate_source(db, source)
    for kind, snap in parents.items():
        _restore_rows(db, SOURCE_PARENTS[kind], snap)
    for name, snap in children.items():
        _restore_rows(db, name, snap)
    for kind, water in parent_waters.items():
        _set_sequence(db, SOURCE_PARENTS[kind], water)
    for name, water in child_waters.items():
        _set_sequence(db, name, water)
    if any(_source_declaration(db, name) != definition
           for name, definition in source["parents"].items()) or any(
               _source_declaration(db, name) != definition
               for name, definition in source["children"].items()
           ):
        raise ValueError("Business original declarations differ after inverse")
    if _foreign_keys(db) != before_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Business inverse FK or quick_check differs")
