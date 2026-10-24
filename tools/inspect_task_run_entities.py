#!/usr/bin/env python3
"""Read-only SQLite metadata check for the proposed Task/Run schema."""
import argparse
import json
import sqlite3

from app.task_run_entity_definitions import task_run_entity_objects


def inspect(path: str) -> dict:
    uri = "file:" + path + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        actual = {name: {"type": kind, "sql": sql} for name, kind, sql in rows}
        expected = task_run_entity_objects()
        return {
            "database": path,
            "expected_objects": sorted(expected),
            "actual_objects": sorted(actual),
            "missing_expected": sorted(set(expected) - set(actual)),
            "columns": {
                name: [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), row))
                       for row in db.execute(f'PRAGMA table_info("{name}")')]
                for name, item in actual.items() if item["type"] == "table"
            },
            "indexes": {name: [row[1] for row in db.execute(f'PRAGMA index_list("{name}")')]
                        for name, item in actual.items() if item["type"] == "table"},
            "foreign_keys": {name: [list(row) for row in db.execute(f'PRAGMA foreign_key_list("{name}")')]
                             for name, item in actual.items() if item["type"] == "table"},
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    args = parser.parse_args()
    print(json.dumps(inspect(args.database), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
