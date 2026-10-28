"""Row snapshot/restore and typed-write helpers the current Business storage still uses."""

from __future__ import annotations
import sqlite3
from app.action_email_storage import _quote


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
