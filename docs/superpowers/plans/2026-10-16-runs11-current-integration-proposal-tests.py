"""Unselected current51 integration probes; run only against an isolated code copy."""
from itertools import permutations
from pathlib import Path
import sqlite3

import pytest

from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
from app.runs11_current_schema import (
    create_current_run11_schema, current_run11_schema_is_current,
)
from app.configs_run_schema import _declarations as source_runs8
from app.runs11_definitions import RUN11_NEW_KINDS

CONSTRUCTORS = (AutoReplyStore, EmailStore, WorkbenchStore)
RETIRED = (*RUN11_NEW_KINDS, 'codex_session_search_fts')


@pytest.mark.parametrize('order', tuple(permutations(CONSTRUCTORS)))
def test_cold_orders_and_reopen_are_current51(tmp_path: Path, order):
    path = tmp_path / 'current.sqlite3'
    for constructor in order:
        constructor(path)
    for constructor in CONSTRUCTORS:
        constructor(path)
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute(
            "select name from sqlite_schema where type='table' and name not like 'sqlite_%'")}
        assert len(tables) == 51
        assert not tables.intersection(RETIRED)
        assert current_run11_schema_is_current(db)
        assert db.execute('pragma foreign_key_check').fetchall() == []
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 52


def test_missing_runs11_index_repairs_without_counter_reset(tmp_path: Path):
    path = tmp_path / 'repair.sqlite3'
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("insert into sqlite_sequence(name,seq) values('runs:codex_session_search_index',700)")
        db.execute('drop index idx_runs11_metadata_source')
        before = tuple(db.execute('select name,seq from sqlite_sequence order by name'))
    assert not _current(path)
    assert _current(path, allow_missing=True)
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.execute('select name,seq from sqlite_sequence order by name')) == before
        assert current_run11_schema_is_current(db)


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
