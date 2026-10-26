"""Current Runs declarations and cold constructors; use isolated code copies."""
from itertools import permutations
from pathlib import Path
import sqlite3

import pytest

from app.current_schema_manifest import schema_is_current
from app.store import AutoReplyStore
from tests.support.current_inventory import CURRENT_TABLES
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
from app.runs11_current_schema import (
    create_current_run11_schema, current_run11_schema_is_current,
)
from app.configs_run_schema import _declarations as source_runs8

CONSTRUCTORS = (AutoReplyStore, EmailStore, WorkbenchStore)
@pytest.mark.parametrize('order', tuple(permutations(CONSTRUCTORS)))
def test_cold_orders_and_reopen_are_final_inventory(tmp_path: Path, order):
    path = tmp_path / 'current.sqlite3'
    for constructor in order:
        constructor(path)
    for constructor in CONSTRUCTORS:
        constructor(path)
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute(
            "select name from sqlite_schema where type='table' and name not like 'sqlite_%'")}
        assert tables == CURRENT_TABLES
        assert schema_is_current(db)
        assert db.execute('pragma foreign_key_check').fetchall() == []
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 54


def _current(path, *, allow_missing=False):
    with sqlite3.connect(path) as db:
        return current_run11_schema_is_current(db, allow_missing_objects=allow_missing)


def test_native_mapped_runs8_cannot_be_current():
    with sqlite3.connect(':memory:') as db:
        for sql in source_runs8(mapped=True).values():
            db.execute(sql)
        assert not current_run11_schema_is_current(db, allow_missing_objects=True)
        db.execute('drop table runs')
        create_current_run11_schema(db)
        assert current_run11_schema_is_current(db)
