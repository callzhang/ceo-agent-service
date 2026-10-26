"""Constructors reject schema damage without repairing the database."""
import sqlite3
import pytest

from app.store import AutoReplyStore
from app.email_store import EmailStore


@pytest.mark.parametrize('ddl', (
    'drop index idx_actions_public_id',
    'drop trigger trg_configs_skill_revision_update',
    'drop index idx_tasks_claim_due',
))
@pytest.mark.parametrize('constructor', (AutoReplyStore, EmailStore))
def test_missing_current_object_is_rejected_without_mutation(tmp_path, ddl, constructor):
    path = tmp_path / 'current.sqlite3'
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute(ddl)
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        constructor(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


def test_current_cold_open_preserves_rows_and_schema(tmp_path):
    path = tmp_path / 'current.sqlite3'
    EmailStore(path)
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    from app.store import _INITIALIZED_STORE_PATHS
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
