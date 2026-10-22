"""Final storage is selected by the real paired protocol, including its inverse."""
import sqlite3
from contextlib import closing

from tests.test_email_action_schema_release import source as source, _prepare
from app.final_action_schema import final_action_schema_is_current


def test_pair_selects_final_actions_and_provenance_and_restores_source(source):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert final_action_schema_is_current(db)
        assert db.execute("select 1 from sqlite_schema where name='provenance'").fetchone()
        assert not db.execute("select 1 from sqlite_schema where name='business_source_documents'").fetchone()
    assert "final_actions" in release.source_schema
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select 1 from sqlite_schema where name='business_source_documents'").fetchone()
        assert not db.execute("select 1 from sqlite_schema where name='provenance'").fetchone()
    release.apply()
    release.finalize()


def test_pair_inverse_retains_new_console_fact_without_original_copy(source):
    from app.action_email_schema_release import prepare_action_email_schema_release, TARGET_STORE_VERSION, TARGET_EMAIL_VERSION
    from app.store import AutoReplyStore
    from app.task_console_actions import decide_candidate
    from app.business_source_reader import AvailableSource
    from pathlib import Path
    release = prepare_action_email_schema_release(Path(__file__).resolve().parents[1], source,
        target_store_version=TARGET_STORE_VERSION, target_email_version=TARGET_EMAIL_VERSION)
    release.apply()
    store = AutoReplyStore(source)
    task_id = store.create_business_task(title="新候选事项", stage="candidate")
    decide_candidate(store, task_id, "ignore", operator_name="测试操作员")
    [fact] = store.list_business_task_signals()
    original = store.business_source_reader().resolve(fact.source)
    assert isinstance(original, AvailableSource)
    expected_task = store.get_business_task(task_id)
    expected_events = store.list_business_task_events(task_id)
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select body from business_source_documents").fetchone() == (original.body,)
    release.apply()
    cold = AutoReplyStore(source)
    assert cold.get_business_task(task_id) == expected_task
    assert cold.list_business_task_events(task_id) == expected_events
    assert cold.get_business_task_signal(fact.id) == fact
    reading = cold.business_source_reader().resolve(fact.source)
    assert isinstance(reading, AvailableSource) and reading.body == original.body


def test_pair_inverse_preserves_new_task_run_and_operator_pause_receipt(source, tmp_path):
    from app.action_email_schema_release import prepare_action_email_schema_release, TARGET_STORE_VERSION, TARGET_EMAIL_VERSION
    from tests.test_reply_task_deferral import ready, apply_ready
    from app.deferral_receipt_storage import find_receipt
    from app.store import AutoReplyStore
    from pathlib import Path
    release = prepare_action_email_schema_release(Path(__file__).resolve().parents[1], source,
        target_store_version=TARGET_STORE_VERSION, target_email_version=TARGET_EMAIL_VERSION)
    release.apply()
    path, task_id, backup = ready(tmp_path, path=source)
    paused = apply_ready(path, task_id, backup)
    expected_task = AutoReplyStore(path).get_reply_task(task_id)
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        assert dict(db.execute('select * from reply_task_deferral_receipts').fetchone()) == paused
    release.apply()
    assert AutoReplyStore(path).get_reply_task(task_id) == expected_task
    with closing(sqlite3.connect(path)) as db:
        assert find_receipt(db, receipt_id=paused['id']) == paused
        assert not db.execute("select 1 from sqlite_schema where name='reply_task_deferral_receipts'").fetchone()
