"""Numeric public-id readers must be able to use the partial idx_actions_public_id.

The index is partial (record_type<>'action' and ...). SQLite cannot derive
record_type<>'action' from record_type='<kind>', so every reader has to state the
whole predicate itself or it silently falls back to idx_actions_email_public_key
(a scan of the kind, plus a temp sort for ordered reads).
"""
import pytest

from app import action_storage, shared_action_lifecycle
from app.action_error_definitions import error_projection
from app.action_storage import _email_max_id
from app.store import AutoReplyStore


def _plan(db, sql, args=()):
    return " ".join(str(row[3]) for row in db.execute("explain query plan " + sql, args))


@pytest.fixture
def db(tmp_path):
    store = AutoReplyStore(tmp_path / "plans.sqlite3")
    with store._connect() as connection:
        yield connection


def _captured(db, call, needle):
    statements = []
    db.set_trace_callback(statements.append)
    try:
        call()
    finally:
        db.set_trace_callback(None)
    return next(sql for sql in statements if needle in sql.lower())


def test_action_storage_max_id_uses_the_public_id_index(db):
    sql = _captured(db, lambda: _email_max_id(db, "errors"), "select max(cast(legacy_key")
    plan = _plan(db, sql)
    assert "idx_actions_public_id" in plan, plan


def test_shared_lifecycle_allocator_uses_the_public_id_index(db):
    sql = _captured(
        db, lambda: shared_action_lifecycle._allocate(db, "work_updates"),
        "select max(cast(legacy_key",
    )
    plan = _plan(db, sql)
    assert "idx_actions_public_id" in plan, plan


@pytest.mark.parametrize("kind", sorted(action_storage.REPLY_ACTION_TYPES))
def test_reply_identity_lookup_seeks_the_numeric_id(db, kind):
    predicate = action_storage._identity_predicate(kind)
    args = action_storage._identity_arguments(kind, 5)
    plan = _plan(db, f"select 1 from actions where {predicate}", args)
    # A reply-family index may serve the seek; what must not happen is a scan of the kind.
    assert "<expr>=?" in plan, plan


@pytest.mark.parametrize("kind", ["errors", "work_updates"])
def test_numeric_identity_lookup_uses_the_public_id_index(db, kind):
    predicate, args = action_storage._email_identity(kind, 5)
    plan = _plan(db, f"select 1 from actions where {predicate}", args)
    assert "idx_actions_public_id" in plan, plan


def test_error_projection_reads_in_id_order_without_a_sort(db):
    plan = _plan(db, f"select id from ({error_projection()}) order by id limit 5")
    assert "idx_actions_public_id" in plan, plan
    assert "TEMP B-TREE" not in plan, plan
