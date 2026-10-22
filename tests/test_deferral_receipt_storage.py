import sqlite3
from datetime import timedelta

from app.reply_task_deferral import resume_deferral
from tests.test_reply_task_deferral import ready, apply_ready, NOW


def test_apply_and_resume_do_not_create_a_physical_receipt_table(tmp_path):
    path, task_id, backup = ready(tmp_path)
    with sqlite3.connect(path) as db:
        before = db.execute("select name from sqlite_schema where type='table' order by name").fetchall()
    paused = apply_ready(path, task_id, backup)
    assert apply_ready(path, task_id, backup) == paused
    resumed = resume_deferral(path, task_id, receipt_id=paused['id'], request_id='resume', now=NOW+timedelta(seconds=1))
    assert resumed['status'] == 'resumed'
    assert resume_deferral(path, task_id, receipt_id=paused['id'], request_id='resume', now=NOW+timedelta(seconds=2)) == resumed
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_schema where type='table' order by name").fetchall() == before


def test_current_receipt_roundtrip_restores_native_tool_shape(tmp_path):
    from app.deferral_receipt_storage import restore_native_receipts, migrate_native_receipts, find_receipt
    path, task_id, backup = ready(tmp_path)
    paused = apply_ready(path, task_id, backup)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute('begin immediate')
        restore_native_receipts(db, preserve_empty=False)
        assert dict(db.execute('select * from reply_task_deferral_receipts').fetchone()) == paused
        migrate_native_receipts(db)
        assert find_receipt(db, receipt_id=paused['id']) == paused
        assert not db.execute("select 1 from sqlite_schema where name='reply_task_deferral_receipts'").fetchone()


def test_unrecognized_native_dependency_is_rejected_before_receipt_transfer(tmp_path):
    import pytest
    from app.deferral_receipt_storage import NATIVE_DDL, migrate_native_receipts
    from app.store import AutoReplyStore
    path = tmp_path / 'dependency.sqlite3'
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.executescript(NATIVE_DDL)
        db.execute('create view unrelated_deferral_view as select * from reply_task_deferral_receipts')
        db.execute('begin immediate')
        before = db.execute('select count(*) from state').fetchone()[0]
        with pytest.raises(ValueError):
            migrate_native_receipts(db)
        assert db.execute('select count(*) from state').fetchone()[0] == before
        assert db.execute("select 1 from sqlite_schema where name='reply_task_deferral_receipts'").fetchone()
