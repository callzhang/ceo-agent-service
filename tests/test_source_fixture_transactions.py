"""Frozen migration fixtures roll back partial DDL and retain exact catalog facts."""
from contextlib import closing
import sqlite3

import pytest

from tests.support.store29_2_source import source_database
from tests.test_action_entity_schema_release import _source_database


@pytest.mark.parametrize('build', [source_database, _source_database], ids=['store29', 'action-source'])
def test_failed_source_schema_build_leaves_no_partial_catalog(tmp_path, monkeypatch, build):
    original = sqlite3.connect
    path = tmp_path / 'failed-source.sqlite3'

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        if str(args[0]) != str(path):
            return db

        def deny_explicit_index(action, name, *_args):
            if action == sqlite3.SQLITE_CREATE_INDEX and not name.startswith('sqlite_'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_explicit_index)
        return db

    monkeypatch.setattr(sqlite3, 'connect', connect)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        build(path)
    with closing(original(path)) as db:
        assert db.execute("select name from sqlite_schema where name not like 'sqlite_%'").fetchall() == []
