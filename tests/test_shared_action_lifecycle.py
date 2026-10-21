"""Shared typed owners retain the real reviewed and direct-email contracts."""

import sqlite3

import pytest

from app import email_store as email_module
from app import shared_action_lifecycle as lifecycle
from app import shared_action_definitions as shared
from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from app.store import AutoReplyStore
from tests.test_email_store import _classification, _task2_account_values
from tests.test_reviewed_candidate_store import _reviewed


def _email(store, *, action=EmailAction.ARCHIVE, classification_id=11):
    value = _classification(
        status=EmailClassificationStatus.PROCESSED,
        classification_id=classification_id,
        actions=(action,), action_parameters={},
    )
    store.persist_scan_result(value, model_text='fixture features')
    return store.direct_action_ids_for_plan(value.action_plan.action_plan_id)[0]


def test_reviewed_and_email_callers_write_common_action_without_origin_dispatch(tmp_path, monkeypatch):
    path = tmp_path / 'shared.sqlite3'
    store = AutoReplyStore(path)
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    calls = []
    transition = lifecycle.transition
    def observed(db, origin, event, **values):
        calls.append((origin, event))
        return transition(db, origin, event, **values)
    monkeypatch.setattr(lifecycle, 'transition', observed)
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60)
    attempt = store.begin_candidate_action(execution['id'], 'owner', 0, 'shared-key')
    assert attempt['status'] == 'dispatched'
    action_id = _email(email)
    action = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
    assert action is not None
    assert calls == []
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from actions where record_type='action'").fetchone()[0] == 2
        assert db.execute("select count(*) from actions where record_type='action_attempts'").fetchone()[0] == 0
        assert db.execute('pragma foreign_key_check').fetchall() == []
    assert attempt['action_owner_id'] != attempt['id']


def test_retry_claim_projection_does_not_reuse_previous_terminal_result(tmp_path):
    path = tmp_path / 'retry.sqlite3'
    AutoReplyStore(path)
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)
    action = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
    email.complete_direct_action_attempt(action, status='failed', provider_operation='archive',
        provider_target=action.locator.stable_message_identity, provider_result_id='',
        error='temporary failure', finished_at='2026-10-19T10:01:00+00:00')
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        old = dict(db.execute(f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?", (action_id,)).fetchone())
        assert old['attempt_count'] == 1 and old['error'] == 'temporary failure'
    retry = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T11:00:00+00:00')
    assert retry is not None and retry.attempt_number == 2
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute(f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?", (action_id,)).fetchone())
        assert row['status'] == 'processing' and row['attempt_count'] == 1
        assert row['started_at'] == retry.claim_started_at
        assert all(row[field] == '' for field in shared.DIRECT_TERMINAL_FIELDS)
        assert db.execute("select count(*) from actions where id=? and record_type='action'", (action_id,)).fetchone()[0] == 1
    email.complete_direct_action_attempt(retry, status='done', provider_operation='archive',
        provider_target=retry.locator.stable_message_identity, provider_result_id='receipt-2',
        error='', finished_at='2026-10-19T11:01:00+00:00')
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute(f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?", (action_id,)).fetchone())
        assert (row['status'], row['attempt_count'], row['provider_result_id']) == ('done', 2, 'receipt-2')
        assert db.execute("select count(*) from actions where id=? and record_type='action'", (action_id,)).fetchone()[0] == 1


def test_completion_rechecks_claim_after_attempt_insert_and_rolls_back(tmp_path):
    from app.email_store import EmailActionAttemptConflict
    path = tmp_path / 'cas.sqlite3'
    AutoReplyStore(path)
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)
    claim = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
    with email._connect() as db:
        db.execute("""create trigger fixture_receipt_failure before insert on action_receipts
            when new.attempt_number is not null
            begin select raise(abort,'fixture receipt failure'); end""")
    with pytest.raises(sqlite3.IntegrityError, match='fixture receipt failure'):
        email.complete_direct_action_attempt(claim, status='done', provider_operation='archive',
            provider_target=claim.locator.stable_message_identity, provider_result_id='receipt',
            error='', finished_at='2026-10-19T10:01:00+00:00')
    assert email.list_action_attempts(action_id) == []
    with email._connect() as db:
        row = db.execute(f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?", (action_id,)).fetchone()
        assert (row['status'], row['started_at']) == ('processing', claim.claim_started_at)


def test_all_six_direct_operations_and_existing_provider_results_use_shared_attempts(tmp_path):
    from app.email_classifier_contracts import DIRECT_ACTIONS
    from app.email_provider_actions import DeterministicEmailActionExecutor, ImapDeterministicProvider
    from tests.test_email_provider_actions import StatefulFakeImapProvider, FakeWritableImapSession

    path = tmp_path / 'providers.sqlite3'
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    satisfied = dict(labels={'work'}, is_read=True, archived=True, folder='Projects',
                     trashed=True, important=True)
    cases = [(operation, mode) for mode in ('apply', 'noop') for operation in DIRECT_ACTIONS]
    cases += [(operation, 'missing') for operation in (
        EmailAction.LABEL, EmailAction.ARCHIVE, EmailAction.MOVE, EmailAction.TRASH)]
    for number, (operation, mode) in enumerate(cases, 101):
        parameters = ({'labels': ['work']} if operation is EmailAction.LABEL else
                      {'target_folder': 'Projects'} if operation is EmailAction.MOVE else {})
        classification = _classification(status=EmailClassificationStatus.PROCESSED,
            message_id=f'provider-fixture-{number}', classification_id=number,
            actions=(operation,), action_parameters={operation: parameters})
        email.persist_scan_result(classification, model_text='fixture features')
        action_id = email.direct_action_ids_for_plan(classification.action_plan.action_plan_id)[0]
        claim = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
        assert claim is not None
        if mode == 'missing':
            session = FakeWritableImapSession(messages={'INBOX': {}, 'Projects': {}})
            provider = ImapDeterministicProvider(session, account_id=claim.account_id)
        else:
            provider = StatefulFakeImapProvider(**(satisfied if mode == 'noop' else {}))
        result = DeterministicEmailActionExecutor(provider).execute(claim)
        expected_status = 'skipped' if mode == 'missing' and operation is not EmailAction.TRASH else 'done'
        assert result.status == expected_status, (operation, mode, result)
        if mode == 'noop':
            assert result.provider_operation == 'readback_noop'
            assert provider.command_log == ['READ']
        if mode == 'missing':
            assert result.provider_operation == ('readback_noop' if operation is EmailAction.TRASH else 'readback_missing')
            assert not any(call[:2] in (('uid', 'STORE'), ('uid', 'COPY')) for call in session.calls)
        attempt = email.complete_direct_action_attempt(claim, status=result.status,
            provider_operation=result.provider_operation, provider_target=result.provider_target,
            provider_result_id=result.provider_result_id, error=result.error,
            retryable=result.retryable, updated_locator=result.updated_locator,
            finished_at='2026-10-19T10:01:00+00:00')
        assert email.list_action_attempts(action_id) == [attempt]
        with email._connect() as db:
            row = db.execute('select * from (' + email_module.DIRECT_EMAIL_ACTION_SQL
                + ') where action_id=?', (action_id,)).fetchone()
            assert row['status'] == result.status and row['attempt_count'] == 1
            for field in shared.DIRECT_TERMINAL_FIELDS:
                assert row[field] == attempt[field]
            assert db.execute("select count(*) from action_receipts r where r.action_id=? and r.attempt_number is not null", (action_id,)).fetchone()[0] == 1
            assert db.execute("select count(*) from runs where record_type='agent_runs'").fetchone()[0] == 0
    EmailStore(path)


def test_one_claim_winner_and_original_cross_table_guards_with_new_step_kind(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / 'guards.sqlite3'
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: email.claim_direct_action(action_id=action_id,
            claimed_at='2026-10-19T10:00:00+00:00'), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    with email._connect() as db:
        row = db.execute("select record_type,action_status from actions where id=?", (action_id,)).fetchone()
        assert tuple(row) == ('action', 'processing')
        statements = (
            "update emails set class_current_action_plan_id='replacement' where classification_id=11",
            "update emails set class_status='failed' where classification_id=11",
            "update configs set payload_json=payload_json where record_type='email_accounts'",
            "delete from configs where record_type='email_accounts'",
        )
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError, match='email_direct_action_in_flight'):
                db.execute(statement)
        assert db.execute('pragma foreign_key_check').fetchall() == []


def _read_publication(path):
    from app.action_schema_release import publication_candidate_source
    from app.action_receipt_schema_release import publication_receipt_sources
    from app.action_postfix_schema_release import publication_postfix_sources
    from app.action_delivery_schema_release import publication_delivery_sources
    from app.email_publication_sources import publication_email_action_source
    from app.repository_updater import in_flight_work
    from app.scheduled_config_storage import publication_scheduled_source
    from app.task_schema_release import publication_task_sources, publication_meeting_sources

    with sqlite3.connect(path) as db:
        for query in (publication_candidate_source(db), publication_email_action_source(db),
            publication_scheduled_source(db), *publication_task_sources(db), *publication_meeting_sources(db),
            *publication_receipt_sources(db).values(), *publication_postfix_sources(db).values(),
            *publication_delivery_sources(db).values()):
            db.execute(query).fetchall()
    return in_flight_work(path)


def test_current_publication_readers_use_shared_schema(tmp_path):
    path = tmp_path / 'publication.sqlite3'
    store, email = AutoReplyStore(path), EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)
    claim = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
    assert claim is not None
    with store._connect() as db:
        row = db.execute(
            f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
            (action_id,),
        ).fetchone()
        assert row['status'] == 'processing'


def test_current_step_poll_and_latest_attempt_reads_stay_indexed(tmp_path):
    from app.action_storage import insert_email_action
    email = EmailStore(tmp_path / 'indexes.sqlite3')
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)

    def measure():
        statements, steps = [], [0]
        with email._connect() as db:
            db.set_trace_callback(statements.append)
            db.set_progress_handler(lambda: steps.__setitem__(0, steps[0] + 1) or 0, 1)
            row = email._next_direct_action_row(db, '2026-10-19T10:00:00+00:00', None)
            db.set_progress_handler(None, 0)
            db.set_trace_callback(None)
            plans = [tuple(tuple(item) for item in db.execute('explain query plan ' + statement))
                     for statement in statements if statement.lstrip().lower().startswith('select')]
        return row['action_id'], steps[0], str(plans)

    before, before_steps, before_plans = measure()
    assert before == action_id and 'SEARCH a USING INDEX' in before_plans
    assert 'SCAN tally' not in before_plans and 'SCAN t)' not in before_plans
    with email._connect() as db:
        for number in range(1, 3001):
            insert_email_action(db, 'email_unsubscribe_cancellations',
                {'action_identity': f'unrelated-{number}', 'decision_json': '{}'}, source_rowid=number)
    after, after_steps, after_plans = measure()
    assert after == before
    assert 'SEARCH a USING INDEX' in after_plans
    assert after_steps <= before_steps + 100, (before_steps, after_steps)
    print(f'shared direct poll VM steps: {before_steps} -> {after_steps} with 3000 unrelated actions')


def test_direct_email_owner_and_important_listing_read_current_attempt_fields(tmp_path):
    from datetime import datetime, timezone
    from app.audit_web import _queue_attention_rows, _email_action_queue_snapshot

    path = tmp_path / 'readers.sqlite3'
    store, email = AutoReplyStore(path), EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=False))
    action_id = _email(email)
    claim = email.claim_direct_action(action_id=action_id, claimed_at='2026-10-19T10:00:00+00:00')
    email.complete_direct_action_attempt(claim, status='failed', provider_operation='archive',
        provider_target=claim.locator.stable_message_identity, provider_result_id='',
        error='readers failure', retryable=False, finished_at='2026-10-19T10:01:00+00:00')
    with store._connect() as db:
        failed = db.execute(
            f"select * from ({email_module.DIRECT_EMAIL_ACTION_SQL}) where action_id=?",
            (action_id,),
        ).fetchone()
        assert failed['status'] == 'failed'
        assert failed['error'] == 'readers failure'
    assert email.list_action_attempts(action_id)[0]['status'] == 'failed'
    total, rows = store.list_operation_logs_with_count(limit=10, statuses=('failed',), source_tables=('email_actions',))
    assert total == 1 and rows[0].status == 'failed'
    attention = [row for row in _queue_attention_rows(store) if row['category'] == 'Email action']
    assert len(attention) == 1 and attention[0]['id'] == action_id
    with store._connect() as db:
        assert _email_action_queue_snapshot(db)['failed'] == 1
    important = _classification(status=EmailClassificationStatus.PROCESSED, message_id='important-fixture',
        classification_id=12, actions=(EmailAction.FLAG_IMPORTANT,), action_parameters={})
    email.persist_scan_result(important, model_text='fixture features')
    important_id = email.direct_action_ids_for_plan(important.action_plan.action_plan_id)[0]
    claim = email.claim_direct_action(action_id=important_id, claimed_at='2026-10-19T11:00:00+00:00')
    email.complete_direct_action_attempt(claim, status='done', provider_operation='STORE IMPORTANT',
        provider_target=claim.locator.stable_message_identity, provider_result_id='important-receipt',
        error='', finished_at='2026-10-19T11:01:00+00:00')
    flagged = email.list_flagged_important_between(datetime(2020, 1, 1, tzinfo=timezone.utc),
                                                   datetime(2100, 1, 1, tzinfo=timezone.utc))
    assert [(row['action_id'], row['finished_at']) for row in flagged] == [(important_id, '2026-10-19T11:01:00+00:00')]
