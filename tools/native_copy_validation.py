"""Typed native fact hashing and bounded SQLite copy for release validation."""
from contextlib import closing
import hashlib
import json
import sqlite3
import time

from app.task8_storage import register_task8_functions


def _scalar(value):
    if value is None:
        return ("null", None)
    if isinstance(value, bytes):
        return ("blob", value.hex())
    if isinstance(value, float):
        return ("real", value.hex())
    if isinstance(value, int):
        return ("integer", str(value))
    return ("text", value)


def fact_digest(path, *, original_ceilings=None, original_identities=None):
    """Hash native stored values/types and hidden identities; never export bodies."""
    result = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("pragma query_only=on")
        db.execute("begin")
        table_rows = db.execute("pragma main.table_list").fetchall()
        for schema, table, kind, _, without_rowid, _ in table_rows:
            if schema != "main" or kind != "table" or (table.startswith("sqlite_") and table != "sqlite_sequence"):
                continue
            if (original_ceilings is not None or original_identities is not None) and table == "sqlite_sequence":
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = db.execute(f"pragma main.table_xinfo({quoted})").fetchall()
            retained = [row[1] for row in columns if row[6] == 0]
            names = ",".join('"' + name.replace('"', '""') + '"' for name in retained)
            projection = names if without_rowid else "rowid," + names
            order = ",".join('"' + row[1] + '"' for row in sorted(columns, key=lambda row: row[5]) if row[5]) if without_rowid else "rowid"
            clauses = ["not(record_type='service_state' and key='store_schema_version')"] if table == "state" else []
            parameters = ()
            if original_ceilings is not None and not without_rowid:
                clauses.append("rowid<=?")
                parameters = (original_ceilings[table],)
            where = " where " + " and ".join(clauses) if clauses else ""
            digest = hashlib.sha256(json.dumps(retained).encode())
            count = 0
            primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            for row in db.execute(f"select {projection} from {quoted}{where} order by {order}", parameters):
                if original_identities is not None:
                    identity = tuple(row[retained.index(name)] for name in primary) if without_rowid else row[0]
                    if identity not in original_identities[table]:
                        continue
                digest.update(json.dumps([_scalar(value) for value in row], ensure_ascii=False,
                                         separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def _identities(path):
    """Freeze exact native row identities; future rowids can fill existing holes."""
    result = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        db.execute("begin")
        for schema, table, kind, _, without_rowid, _ in db.execute("pragma main.table_list").fetchall():
            if schema != "main" or kind != "table" or table.startswith("sqlite_"):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = db.execute(f"pragma main.table_xinfo({quoted})").fetchall()
            keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            names = ",".join('"' + name.replace('"', '""') + '"' for name in keys) if without_rowid else "rowid"
            rows = db.execute(f"select {names} from {quoted}")
            result[table] = {tuple(row) if without_rowid else row[0] for row in rows}
    return result


def _ceilings(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        tables = db.execute("pragma main.table_list").fetchall()
        return {row[1]: db.execute('select max(rowid) from "' + row[1] + '"').fetchone()[0]
                for row in tables if row[0] == "main" and row[2] == "table" and not row[4]
                and not row[1].startswith("sqlite_")}


def _copy_source(source, target):
    if source == target or target.exists():
        raise ValueError("validation requires a new disposable copy path")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        src.execute("pragma query_only=on")
        src.execute("begin")
        src.execute("select count(*) from sqlite_schema").fetchone()
        deadline = time.monotonic() + 120

        def bounded_copy(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("validation copy exceeded its deadline")

        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, progress=bounded_copy, sleep=0.01)


