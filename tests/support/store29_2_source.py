"""Frozen exact Store29.2 and Email54 source for release tests."""
import json
from pathlib import Path
import sqlite3

from app.state_storage import upsert_state
from app.task8_storage import register_task8_functions


def create_store29_2_source(db):
    catalog = json.loads((Path(__file__).parents[2] / 'app/schema_fixtures/store29_2_email54_catalog.json').read_text())
    register_task8_functions(db)
    if not db.in_transaction:
        db.execute('begin')
    for kind, _name, _table, sql in catalog:
        if kind == 'table':
            db.execute(sql)
    for kind, _name, _table, sql in catalog:
        if kind != 'table':
            db.execute(sql)
    upsert_state(db, 'service_state', {'key': 'store_schema_version', 'value': '2026-10-29.2'}, {'value': 'json_quote(excluded.value)'})
    db.execute("insert into email_schema_migrations values(54,'source-time')")


def source_database(path):
    with sqlite3.connect(path) as db:
        create_store29_2_source(db)
