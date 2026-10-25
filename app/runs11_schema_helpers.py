"""Target-object listings for the current Run tables."""

from contextlib import closing
import sqlite3
from app.configs_run_schema import _objects
from app.runs11_definitions import (
    create_runs11_schema,
)
from app.workbench_storage import SEARCH_FTS_DDL


def _target_objects():
    with closing(sqlite3.connect(':memory:')) as db:
        create_runs11_schema(db)
        return _objects(db)


def _fts_objects():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute(SEARCH_FTS_DDL)
        return tuple(sorted(tuple(row) for row in db.execute(
            'select type,name,tbl_name,sql from main.sqlite_schema where sql is not null')))
