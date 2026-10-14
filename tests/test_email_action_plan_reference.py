import sqlite3
from contextlib import contextmanager

import pytest

from app.email_classifier_contracts import EmailClassificationStatus
from app.email_store import EMAIL_PLAN_SQL, EMAIL_SCHEMA_VERSION, EmailPersistenceCorruption, EmailStore
from app.action_storage import email_action_projection, update_email_action
from tests.email_schema_fixtures import restore_action_plan_snapshot_column
from tests.test_email_store import _classification, _persist_scan


def seed(path):
    store = EmailStore(path)
    for index, status in enumerate((EmailClassificationStatus.PROCESSED,
                                    EmailClassificationStatus.PENDING_FEEDBACK), start=1):
        _persist_scan(store, _classification(status=status, classification_id=index,
                                            message_id=f'reference-{index}'))
    return store


def rewind48(path):
    with sqlite3.connect(path) as db:
        db.execute('drop table email_direct_retry_requests')
        restore_action_plan_snapshot_column(db)
        db.execute('delete from email_schema_migrations where version>48')
        db.execute("insert or ignore into email_schema_migrations values(48, '2026-10-11T00:00:00Z')")


def test_stable_classification_existence_does_not_hydrate_corrupt_plan(tmp_path):
    path = tmp_path / "existence.sqlite3"
    store = seed(path)
    identity = store.get_classification(1)["stable_message_identity"]
    with sqlite3.connect(path) as db:
        plan_rows = db.execute(
            f"select rowid, action_plan_version from ({email_action_projection('email_action_plans', include_rowid=True)}) "
            "where classification_id=1"
        ).fetchall()
        for plan_row in plan_rows:
            update_email_action(db, "email_action_plans", plan_row[0], {
                "action_plan_version": plan_row[1] + 1,
            })
    assert store.has_stable_classification_record(identity)
    assert not store.has_stable_classification_record("absent-message")
    with pytest.raises(EmailPersistenceCorruption):
        store.has_stable_classification(identity)
    with pytest.raises(EmailPersistenceCorruption):
        store.get_classification_by_stable_identity(identity)


def business_rows(path):
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute('pragma table_info(email_classifications)')
                   if row[1] != 'action_plan_json']
        return {table: db.execute('select ' + (','.join(columns) if table == 'email_classifications' else '*')
                                 + f' from {table} order by rowid').fetchall()
                for table in ('email_classifications', 'email_messages', 'email_action_plans',
                              'email_actions', 'email_action_attempts', 'email_scan_cursors')}


def test_v48_reference_migration_preserves_business_and_api(tmp_path):
    path = tmp_path / 'reference.sqlite3'
    store = seed(path)
    before_api = [store.get_classification(index) for index in (1, 2)]
    before_history = store.list_historical_classification_history()
    before = business_rows(path)
    rewind48(path)
    migrated = EmailStore(path)
    assert EMAIL_SCHEMA_VERSION == 51
    with sqlite3.connect(path) as db:
        assert 'action_plan_json' not in {row[1] for row in db.execute('pragma table_info(email_classifications)')}
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == EMAIL_SCHEMA_VERSION
        assert db.execute('select count(*) from email_direct_retry_requests').fetchone()[0] == 0
        assert db.execute('pragma foreign_key_check').fetchall() == []
    assert business_rows(path) == before
    assert [migrated.get_classification(index) for index in (1, 2)] == before_api
    assert migrated.list_historical_classification_history() == before_history
    assert migrated.get_classification_by_stable_identity(before_api[0]['stable_message_identity']) == {
        key: value for key, value in before_api[0].items()
        if key not in {'attachment_metadata', 'message_text', 'cc', 'recipients'}}
    versions = business_rows(path)
    EmailStore(path)
    EmailStore(path, validate_rows=False)
    assert business_rows(path) == versions


@pytest.mark.parametrize('corruption', ['whitespace', 'partial_authorization', 'missing', 'cross_classification'])
def test_v48_reference_migration_rejects_corruption_and_rolls_back(tmp_path, corruption):
    path = tmp_path / 'corrupt.sqlite3'
    store = seed(path)
    if corruption == 'cross_classification':
        _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED,
                                            classification_id=3, message_id='cross-plan'))
    rewind48(path)
    with sqlite3.connect(path) as db:
        if corruption == 'whitespace':
            db.execute("update email_classifications set action_plan_json=' '||action_plan_json where id=1")
        elif corruption == 'partial_authorization':
            db.execute("update email_classifications set action_plan_json=json_remove(action_plan_json, '$.action_authorizations') where id=1")
        elif corruption == 'missing':
            db.execute("update email_classifications set current_action_plan_id='missing' where id=1")
        else:
            db.execute('update email_classifications set current_action_plan_id=(select current_action_plan_id from email_classifications where id=3) where id=1')
    before = business_rows(path)
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(path)
    assert business_rows(path) == before
    with sqlite3.connect(path) as db:
        assert 'action_plan_json' in {row[1] for row in db.execute('pragma table_info(email_classifications)')}
        assert db.execute('select max(version) from email_schema_migrations').fetchone()[0] == 48


def test_list_hydrates_canonical_plans_in_one_query_and_connection(tmp_path, monkeypatch):
    path = tmp_path / 'batch.sqlite3'
    store = EmailStore(path)
    for index in range(1, 21):
        _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED,
                                            classification_id=index, message_id=f'batch-{index}'))
    queries = []
    connections = []
    connect = store._connect

    @contextmanager
    def traced():
        connections.append(1)
        with connect() as db:
            db.set_trace_callback(queries.append)
            yield db

    monkeypatch.setattr(store, '_connect', traced)
    items, total = store.list_classifications(status=None, limit=20, offset=0)
    assert total == len(items) == 20
    assert len(connections) == 1
    plan_queries = [
        query for query in queries
        if query.lower().startswith(f"select * from ({EMAIL_PLAN_SQL})".lower())
        and "where action_plan_id in (select value from json_each(" in query.lower()
    ]
    assert len(plan_queries) == 1
    assert all(item['action_plan']['action_plan_id'] == item['current_action_plan_id'] for item in items)


def test_current_reference_survives_correction_feedback_and_scan_retry(tmp_path):
    from datetime import datetime, timezone
    from app.email_classifier_contracts import EmailAction, EmailCategory
    from app.email_pipeline import apply_human_confirmation
    from tests.test_email_store import _versioned_plan

    path = tmp_path / 'current-writers.sqlite3'
    store = seed(path)
    original = _classification(status=EmailClassificationStatus.PROCESSED,
                               classification_id=1, message_id='reference-1')
    assert original.action_plan is not None
    correction = _versioned_plan(original.action_plan, version=2,
                                 category=EmailCategory.NOTIFICATION,
                                 actions=(EmailAction.ARCHIVE,), action_parameters={})
    corrected = store.append_action_plan_version(1, correction,
                                                confirmed_category=EmailCategory.NOTIFICATION)
    assert corrected['action_plan'] == correction.model_dump(mode='json')
    assert store.repair_current_action_plan(correction) == corrected
    assert _persist_scan(store, original)['action_plan'] == correction.model_dump(mode='json')
    application = apply_human_confirmation(
        store, 2, 'work', feedback_request_id='reference-feedback',
        expected_current_action_plan_id=None,
        now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert application is not None
    assert store.get_classification(2)['action_plan'] == application.confirmed['action_plan']
    replay = apply_human_confirmation(
        store, 2, 'work', feedback_request_id='reference-feedback',
        expected_current_action_plan_id=None,
        now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert replay.confirmed == application.confirmed
    assert replay.applied is False
    with sqlite3.connect(path) as db:
        assert 'action_plan_json' not in {row[1] for row in db.execute('pragma table_info(email_classifications)')}
        db.row_factory = sqlite3.Row
        plans = db.execute(f'select * from ({EMAIL_PLAN_SQL}) where classification_id=1 order by action_plan_version').fetchall()
    assert [EmailStore._stored_action_plan(row).model_dump(mode='json') for row in plans] == [
        original.action_plan.model_dump(mode='json'), correction.model_dump(mode='json')]
    EmailStore(path)


@pytest.mark.parametrize('pointer', ['missing', 'cross_classification'])
def test_current_read_and_full_validation_reject_invalid_reference(tmp_path, pointer):
    path = tmp_path / 'invalid-pointer.sqlite3'
    store = seed(path)
    with sqlite3.connect(path) as db:
        if pointer == 'missing':
            db.execute("update email_classifications set current_action_plan_id='missing' where id=1")
            target = 1
        else:
            db.execute('update email_classifications set current_action_plan_id=(select current_action_plan_id from email_classifications where id=1) where id=2')
            target = 2
    with pytest.raises(EmailPersistenceCorruption, match='current ActionPlan'):
        store.get_classification(target)
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(path)


def test_v48_legacy_processed_without_plan_preserves_null_projection(tmp_path):
    path = tmp_path / 'legacy-null.sqlite3'
    store = seed(path)
    with sqlite3.connect(path) as db:
        db.execute("update email_classifications set status='processed', legacy_processed_without_plan=1 where id=2")
    before = store.get_classification(2)
    assert before['action_plan'] is None
    rewind48(path)
    migrated = EmailStore(path)
    assert migrated.get_classification(2) == before
