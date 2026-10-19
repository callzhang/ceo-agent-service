"""Finite source-bound Business migration and inverse."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from functools import lru_cache

from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_email_storage import _quote, _sequence, _set_sequence
from app.action_storage import _encoded_native_sqlite_value
from app.business_definitions import (
    BUSINESS_COMMON, BUSINESS_FIELDS, BUSINESS_FRESH_FIELDS,
    BUSINESS_REFERENCES,
    BUSINESS_TABLE_DDL, BUSINESS_INDEX_DDL, BUSINESS_TRIGGER_DDL,
    business_column, business_payload_fields, _definitions,
)
from app.business_storage import create_business_schema
from app.task_schema_release import _foreign_keys
from app.action_followup_sync_definitions import SYNC_TYPES_ORDER
from app.business_candidate_definitions import CANDIDATE_SOURCE_TABLES, SOURCE_PROFILES

CORE_SOURCE_PARENTS = {
    "task": "business_tasks",
    "project": "business_projects",
    "anchor": "business_anchors",
    "project_context_revision": "business_project_context_revisions",
    "work_cluster": "business_work_clusters",
    "todo_evidence_candidate": "todo_evidence_candidates",
    "work_project": "work_projects",
    "work_todo": "work_todos",
}
SERVICE_BUGFIX_SOURCE = "service_bugfix_candidates"
SERVICE_BUGFIX_SOURCE_FINGERPRINT = (
    "e8640bda0d50ad27d78193a894f4ec795d88bb3e106ccf48730ad74e4e8b3550"
)
SOURCE_PARENTS = {
    **CORE_SOURCE_PARENTS,
    "service_bugfix_candidate": SERVICE_BUGFIX_SOURCE,
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
    "business_work_cluster_tasks": {"cluster_id": "work_cluster", "task_id": "task"},
    "business_task_anchor_links": {"task_id": "task", "anchor_id": "anchor"},
    "business_project_candidates": {"cluster_id": "work_cluster", "confirmed_project_id": "project"},
    "business_attention_items": {"anchor_id": "anchor"},
    "business_attention_tasks": {"task_id": "task"},
    "business_attention_proposal_tasks": {"task_id": "task"},
    "business_legacy_links": {
        "task_id": "task", "cluster_id": "work_cluster", "anchor_id": "anchor", "project_id": "project",
        "work_project_id": "work_project", "work_todo_id": "work_todo",
    },
}
PUBLIC_ALIAS = {
    "task": "task_public_id", "project": "project_public_id",
    "anchor": "anchor_public_id", "work_cluster": "work_cluster_public_id",
    "work_project": "work_project_public_id", "work_todo": "work_todo_public_id",
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
        "209a571b544e6f3d2bd7bcc52f654e57db065581c62983715306a9327a6c9a7c",
        "82469a32d895faa593a5b0a3d9473a1ade2a6c524e8c4db6164d9af7fd0e3db1",
        "cea4bbe59b2ec9eed9d1aa2627f33274f2b7590d3baf5862cbeb03940f1e2f18",
        "88d58f837631cdee8ce6759581761a210482009027de146b662e8f9a5eeb7eb1",
    ),
    "fresh": (
        "19341a7f00ed976b0fee462b1fcfe1bcadc061ebc555d6757d9e89c1151b81c0",
        "4c75ae1efd72dc5b27772a9a69454a2f1bcca619b35376fa087ec6206c70cf98",
        "d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f",
        "2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8",
        "209a571b544e6f3d2bd7bcc52f654e57db065581c62983715306a9327a6c9a7c",
        "82469a32d895faa593a5b0a3d9473a1ade2a6c524e8c4db6164d9af7fd0e3db1",
        "ffcd892f1f998eef267adb48aa4a69c5e5fc1480302ec9cc85df821b4eb0d616",
        "c062a44c1af10067e6ea874e479038bb7ab32e3915cf55c706342411edf416de",
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
    "actual": "6f6835aa1002b863942f51084e960d92fedabae4064e4bfd41d779b3aaf214eb",
    "fresh": "1a8f0532255e3f8e81761fa86d1ab8cc9c74a787fd1a03810f103c46f60df1f6",
}
FOLLOWUP_SYNC_TARGET_CHILD_HASHES = {
    "actual": "3c7c963ddee6ab9f37178dce1c88b720847b5148685f699affe5d5c84207cc77",
    "fresh": "ccb2f5ef1b4949c284c3f1315cd1a071edf33ae77ffbe26c2463ccb948dcb742",
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
    if _fingerprint(parents[SERVICE_BUGFIX_SOURCE]) != SERVICE_BUGFIX_SOURCE_FINGERPRINT:
        raise ValueError("unsupported service bug-fix candidate source declaration")
    parent_hashes = tuple(_fingerprint(parents[name]) for name in CORE_SOURCE_PARENTS.values())
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


def _insert_rows(db: sqlite3.Connection, name: str, snap: dict) -> None:
    columns = (("rowid",) if snap["hidden_rowid"] else ()) + snap["columns"]
    sql = (f"insert into main.{_quote(name)}("
           + ",".join(_quote(column) for column in columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    db.executemany(sql, snap["rows"])


def _restore_rows(db: sqlite3.Connection, name: str, snap: dict) -> None:
    _insert_rows(db, name, snap)
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


def _mapped_parent_facts(facts: dict, variant: str) -> dict:
    fields_by_kind = BUSINESS_FRESH_FIELDS if variant == "fresh" else BUSINESS_FIELDS
    mapped = {}
    for kind, snapshot in facts.items():
        columns = snapshot["columns"]
        positions = tuple(columns.index(field) for field in fields_by_kind[kind])
        mapped[kind] = {
            "columns": fields_by_kind[kind],
            "rows": tuple(tuple(row[index] for index in positions)
                          for row in snapshot["rows"]),
            "types": tuple(tuple(row[index] for index in positions)
                           for row in snapshot["types"]),
            "hidden_rowid": False,
        }
    return mapped


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
               "status", "created_at", "updated_at", "source_type", "source_ref",
               "source_created_at", "completed_at", "merged_task_ref",
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
                common["updated_at"], common["source_type"], common["source_ref"],
                common["source_created_at"], common["completed_at"], refs["merged_task_ref"],
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
    for kind, snap in _mapped_parent_facts(
            parent_facts, source["variant"]).items():
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


@lru_cache(maxsize=2)
def _candidate_prior_parent_declarations(variant: str) -> dict:
    with sqlite3.connect(':memory:') as db:
        result = {}
        for name in ('business_attention_items', 'business_project_candidates'):
            profile = SOURCE_PROFILES[variant][name]
            db.execute(profile['sql'])
            for sql in (*profile['indexes'], *profile['triggers']):
                db.execute(sql)
            result[name] = _source_declaration(db, name)
        return result


def _candidate_children_as_prior_target(children: dict, variant: str) -> dict:
    """Compare the unchanged child shape against the reviewed 28-table hashes."""
    from app.business_candidate_storage import CHILD_FKS, _translated_child_sql

    for name, references in CHILD_FKS.items():
        if name not in children:
            continue
        item = children[name]
        item['sql'] = _translated_child_sql(name, item['sql'], inverse=True)
        for foreign_key in item['foreign_keys']:
            kind = references.get(foreign_key['from'])
            if kind is not None:
                if (foreign_key['table'], foreign_key['to']) != ('business', kind + '_public_id'):
                    raise ValueError('Business candidate child FK mismatch')
                foreign_key['table'] = CANDIDATE_SOURCE_TABLES[kind]
                foreign_key['to'] = 'id'
        if _fingerprint(item) != SOURCE_PROFILES[variant][name]['hash']:
            # The child may also have undergone an earlier Business binding;
            # the full reviewed child hash below remains authoritative.
            if name == 'business_attention_events':
                raise ValueError('Business candidate event declaration mismatch')
    children.update(_candidate_prior_parent_declarations(variant))
    return children


def _candidate_event_is_current(db: sqlite3.Connection, variant: str) -> bool:
    from app.business_candidate_storage import _translated_child_sql
    item = _source_declaration(db, 'business_attention_events')
    try:
        item['sql'] = _translated_child_sql('business_attention_events', item['sql'], inverse=True)
    except ValueError:
        return False
    for foreign_key in item['foreign_keys']:
        if foreign_key['from'] == 'attention_item_id':
            if (foreign_key['table'], foreign_key['to']) != ('business', 'attention_item_public_id'):
                return False
            foreign_key['table'], foreign_key['to'] = 'business_attention_items', 'id'
    return _fingerprint(item) == SOURCE_PROFILES[variant]['business_attention_events']['hash']


def _canonical_create(sql: str) -> str:
    for prefix in ("create unique index", "create index", "create table", "create trigger"):
        if sql.startswith(prefix):
            return prefix.upper() + sql[len(prefix):]
    raise ValueError("unsupported Business DDL")


def business_schema_is_current(
    db: sqlite3.Connection,
    variant: str | None = None,
    *,
    include_followup_sync: bool = False,
    include_candidates: bool = False,
    include_business_actions: bool = False,
    inline_legacy_owner: bool = False,
) -> bool:
    if not business_schema_parent_is_current(db, include_candidates=include_candidates,
                                             include_business_actions=include_business_actions,
                                             inline_legacy_owner=inline_legacy_owner):
        return False
    if include_business_actions:
        if not include_candidates or not include_followup_sync:
            return False
        from app.action_business_definitions import BUSINESS_ACTION_KINDS
        if any(_source_declaration(db, name)['sql'] is not None
               for name in (*BUSINESS_ACTION_KINDS,
                            'business_attention_items', 'business_project_candidates')):
            return False
        if inline_legacy_owner:
            return _source_declaration(db, 'business_legacy_links')['sql'] is None
        # Historical selected12 source retains its native LegacyLinks child.
        return _fingerprint(_source_declaration(db, 'business_legacy_links')) in {
            'd3f6cb2d64af459929b71f5955dd9eb91d7671377ffdc4e1042505bef86e69ec',
            '402759df74d6bb6b7fd8a981b9f0e97d70b498d82090e8028bf90e9bcf04caac',
        }
    if include_followup_sync:
        expected_hashes = ((FOLLOWUP_SYNC_TARGET_CHILD_HASHES.get(variant),)
                           if variant is not None else
                           tuple(FOLLOWUP_SYNC_TARGET_CHILD_HASHES.values()))
        if not expected_hashes or any(expected is None for expected in expected_hashes):
            return False
        current_children = tuple(
            name for name in RETAINED_CHILD_FKS if name not in SYNC_TYPES_ORDER
        )
        children = _objects(db, (name for name in current_children if
                                not include_candidates or name not in
                                ('business_attention_items', 'business_project_candidates')))
        if include_candidates:
            for current in ((variant,) if variant is not None else tuple(FOLLOWUP_SYNC_TARGET_CHILD_HASHES)):
                try:
                    normalized = _candidate_children_as_prior_target(
                        deepcopy(children), current)
                except ValueError:
                    continue
                if (_fingerprint({name: normalized[name] for name in current_children}) ==
                        FOLLOWUP_SYNC_TARGET_CHILD_HASHES[current] and
                        _candidate_event_is_current(db, current)):
                    return True
            return False
        return _fingerprint(children) in expected_hashes
    versions = (variant,) if variant is not None else tuple(TARGET_CHILD_HASHES)
    if include_candidates:
        children = _objects(db, (name for name in RETAINED_CHILD_FKS if name not in
                                ('business_attention_items', 'business_project_candidates')))
        for current in versions:
            try:
                normalized = _candidate_children_as_prior_target(
                    deepcopy(children), current)
            except ValueError:
                continue
            if (_fingerprint({name: normalized[name] for name in RETAINED_CHILD_FKS}) ==
                    TARGET_CHILD_HASHES[current] and _candidate_event_is_current(db, current)):
                return True
        return False
    return any(_target_children_are_current(db, current) for current in versions)


def business_schema_parent_is_current(db: sqlite3.Connection, *, include_candidates: bool = False,
                                      include_business_actions: bool = False,
                                      inline_legacy_owner: bool = False) -> bool:
    """Check the typed parent before Store's transactional repair of child tables.

    Store still runs the full child fingerprint check before committing startup.
    This narrower check only lets that path rebuild a missing known child table;
    it does not accept a partial schema as current.
    """
    if db.execute(
        "select 1 from main.sqlite_schema where type='table' and name in "
        "('business_tasks','business_projects','business_anchors',"
        "'business_project_context_revisions','service_bugfix_candidates',"
        "'business_work_clusters','todo_evidence_candidates','work_projects','work_todos'"
        + (",'business_attention_items','business_project_candidates','wechat_memory_candidates'"
           if include_candidates else "") + ") limit 1"
    ).fetchone():
        return False
    table = db.execute(
        "select sql from main.sqlite_schema where type='table' and name='business'"
    ).fetchone()
    expected_table, expected_indexes, expected_triggers = (
        _definitions(include_candidates=True, include_business_actions=include_business_actions) if include_candidates else
        (BUSINESS_TABLE_DDL, BUSINESS_INDEX_DDL, BUSINESS_TRIGGER_DDL)
    )
    if inline_legacy_owner:
        from app.business_legacy_owner_storage import _business_declaration
        expected_table = _business_declaration(target=True)['sql']
    else:
        expected_table = _canonical_create(expected_table)
    if table is None or table[0] != expected_table:
        return False
    expected = {_canonical_create(sql) for sql in
                (*expected_indexes, *expected_triggers)}
    actual = {row[0] for row in db.execute(
        "select sql from main.sqlite_schema where tbl_name='business' "
        "and type in ('index','trigger') and sql is not null"
    )}
    if actual != expected:
        return False
    return True


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
    # DROP TABLE removes sqlite_sequence['business'], but the six public-ID
    # allocator aliases are manually named rows and survive that DROP.
    for kind in SOURCE_PARENTS:
        _set_sequence(db, "business:" + kind, None)
    _recreate_source(db, source)
    for kind, snap in parents.items():
        _insert_rows(db, SOURCE_PARENTS[kind], snap)
    for name, snap in children.items():
        _restore_rows(db, name, snap)
    for kind, water in parent_waters.items():
        _set_sequence(db, SOURCE_PARENTS[kind], water)
    for name, water in child_waters.items():
        _set_sequence(db, name, water)
    restored_parent_facts = _parent_facts(db)
    if _mapped_parent_facts(restored_parent_facts, variant) != parents:
        raise ValueError("Business current facts differ after inverse")
    if any(_source_declaration(db, name) != definition
           for name, definition in source["parents"].items()) or any(
               _source_declaration(db, name) != definition
               for name, definition in source["children"].items()
           ):
        raise ValueError("Business original declarations differ after inverse")
    if _foreign_keys(db) != before_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Business inverse FK or quick_check differs")
