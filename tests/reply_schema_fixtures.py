"""Complete persisted Store fingerprint for rejected source-schema tests."""

import sqlite3

import pytest

import app.store as store_module
from app.store import AutoReplyStore


def assert_store_reopen_rejected_without_mutation(path):
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
