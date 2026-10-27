"""Row snapshot/restore and typed-write helpers the current Business storage still uses."""

from __future__ import annotations
import sqlite3
from app.action_email_storage import _quote
from app.business_definitions import _definitions


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


def _assert_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0] != 0:
        raise sqlite3.ProgrammingError("Business migration needs caller FK-off transaction")


def _canonical_create(sql: str) -> str:
    for prefix in ("create unique index", "create index", "create table", "create trigger"):
        if sql.startswith(prefix):
            return prefix.upper() + sql[len(prefix):]
    raise ValueError("unsupported Business DDL")


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
    expected_table, expected_indexes, expected_triggers = _definitions(
        include_candidates=include_candidates,
        include_business_actions=include_business_actions if include_candidates else False,
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
