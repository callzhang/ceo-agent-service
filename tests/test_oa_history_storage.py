import json

from app.approval_history import ApprovalHistoryResult, resolve_approval_history_result
from app.oa_history_normalization import load_oa_history_source, normalize_oa_history
from app.oa_history_storage import list_recorded_oa_results, record_current_oa_facts
from app.store import AutoReplyStore


def test_normalization_preserves_typed_result_and_quality_independently(tmp_path):
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    body = '{"readback":{"taskStatus":"COMPLETED","taskResult":"AGREE"}}'
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action_result_json=body, send_status='failed')
    with store._connect() as db:
        first = normalize_oa_history(db)
        attempts, runs = load_oa_history_source(db)
        [attempt] = attempts
        facts = list_recorded_oa_results(db, [attempt_id])
        assert attempt.oa_action_result_json == body
        assert resolve_approval_history_result(attempt, runs[attempt_id], recorded_actions=facts) is ApprovalHistoryResult.APPROVED
        assert json.loads(facts[0]['outcome_json'])['success'] is None
        assert db.execute('select count(*) from action_observers').fetchone()[0] == 0
        assert db.execute("select count(*) from action_receipts where status='verified'").fetchone()[0] == 0
        assert normalize_oa_history(db)['facts'] == 0
        assert first['facts'] == 1


def test_current_flat_fact_preserves_sqlite_types(tmp_path):
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    body = '{"success":1,"taskStatus":"COMPLETED"}'
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action_result_json=body, send_status='failed')
    with store._connect() as db:
        normalize_oa_history(db)
        receipt_id = db.execute('select receipt_id from reply_attempt_receipt_refs').fetchone()[0]
        assert record_current_oa_facts(db, attempt_id=attempt_id, receipt_id=receipt_id, expected_body=body, process_instance_id='p', task_id='t') == 0
        [fact] = list_recorded_oa_results(db, [attempt_id])
        assert json.loads(fact['outcome_json']) == {'success': 1, 'taskStatus': 'COMPLETED'}


def test_historical_quality_sqlite_projection_preserves_types():
    import sqlite3
    from app.oa_history_normalization import _historical_quality_projection
    db = sqlite3.connect(':memory:')
    for raw, expected in [
        ('{"success":1,"taskStatus":"COMPLETED"}', 1),
        ('{"success":"1","taskStatus":"COMPLETED"}', '1'),
        ('{"success":true,"taskStatus":"COMPLETED"}', 1),
        ('{"result":{"success":1,"taskStatus":"COMPLETED"}}', 1),
        ('invalid', None),
    ]:
        values = json.loads(_historical_quality_projection(db, raw))
        assert values.get('success') == expected
        value = db.execute("select json_extract(?,'$.success')=1", (json.dumps(values),)).fetchone()[0]
        assert bool(value) == (type(expected) is int and expected == 1)


def test_exact_existing_reference_and_failed_comment_are_preserved(tmp_path):
    from app.action_entity_storage import create_action, append_action_receipt
    from app.reply_receipt_reference import add_reference
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    with store._connect() as db:
        create_action(db, values={})
    body = '{"success":false}'
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action_result_json=body, send_status='commented')
    with store._connect() as db:
        owner = db.execute("select id from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?", (attempt_id,)).fetchone()[0]
        assert owner != attempt_id
        action = create_action(db, values={})
        receipt = append_action_receipt(db, action['id'], values={'provider_result_json': body})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=body)
        assert normalize_oa_history(db)['bodies'] == 0
        assert db.execute('select receipt_id from reply_attempt_receipt_refs where attempt_id=?', (owner,)).fetchone()[0] == receipt
        assert db.execute('select provider_result_json from action_receipts where id=?', (receipt,)).fetchone()[0] == body
        attempts, runs = load_oa_history_source(db)
        assert resolve_approval_history_result(attempts[0], runs[attempt_id], recorded_actions=list_recorded_oa_results(db, [attempt_id])) is ApprovalHistoryResult.UNKNOWN


def test_source_finds_whitespace_padded_action_without_process_id(tmp_path):
    from app.schema_sources import oa_history_28_5 as source
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='\u2003 OA_APPROVAL \t', sensitivity_kind='normal', oa_action='approve', oa_action_result_json='{"success":true}', send_status='completed')
    with store._connect() as db:
        attempts, runs = load_oa_history_source(db)
        [attempt] = attempts
        assert source.resolve_approval_history_result(attempt, runs[attempt_id]) is ApprovalHistoryResult.APPROVED
        normalize_oa_history(db)
        attempts, runs = load_oa_history_source(db)
        assert resolve_approval_history_result(attempts[0], runs[attempt_id], recorded_actions=list_recorded_oa_results(db, [attempt_id])) is ApprovalHistoryResult.APPROVED


def test_normalized_real_attempts_preserve_source_failures_and_identifier_mismatches(tmp_path):
    from app.schema_sources import oa_history_28_5 as source
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    bodies = [
        '{"taskStatus":"COMPLETED","taskResult":"AGREE"}',
        '{"success":false,"taskStatus":"COMPLETED","taskResult":"AGREE"}',
        '{"success":true,"processInstanceId":"other"}',
        '{"result":{"success":true,"taskId":"other"}}',
        '{"success":true,"result":{"success":false}}',
    ]
    for index, body in enumerate(bodies):
        store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id=str(index), trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action='approve', oa_action_result_json=body, send_status='completed')
    with store._connect() as db:
        attempts, runs = load_oa_history_source(db)
        expected = {a.id: source.resolve_approval_history_result(a, runs[a.id]) for a in attempts}
        assert list(expected.values()) == [ApprovalHistoryResult.APPROVED] + [ApprovalHistoryResult.UNKNOWN] * 4
        normalize_oa_history(db)
        attempts, runs = load_oa_history_source(db)
        facts = list_recorded_oa_results(db, [a.id for a in attempts])
        assert {a.id: resolve_approval_history_result(a, runs[a.id], recorded_actions=facts) for a in attempts} == expected


def test_current_flat_result_stays_unknown_after_repeated_normalization(tmp_path):
    from app.action_entity_storage import create_action, append_action_receipt
    from app.reply_receipt_reference import add_reference
    from app.action_storage import update_action
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    body = '{"success":true,"taskStatus":"COMPLETED"}'
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action='approve', oa_action_result_json=body, send_status='completed')
    with store._connect() as db:
        action = create_action(db, values={})
        receipt = append_action_receipt(db, action['id'], values={'provider_result_json': body})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=body)
        update_action(db, 'reply_attempts', attempt_id, {'oa_action_result_json': ''})
        record_current_oa_facts(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=body, process_instance_id='p', task_id='t')
        for _ in range(2):
            normalize_oa_history(db)
            attempts, runs = load_oa_history_source(db)
            facts = list_recorded_oa_results(db, [attempt_id])
            assert resolve_approval_history_result(attempts[0], runs[attempt_id], recorded_actions=facts) is ApprovalHistoryResult.UNKNOWN
            assert len(facts) == 1 and facts[0]['status'] is None


def test_preflight_rejects_distinct_inline_body_before_any_mutation(tmp_path):
    import pytest
    from app.action_entity_storage import create_action, append_action_receipt
    from app.reply_receipt_reference import add_reference
    from app.action_storage import update_action
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action_result_json='body B', send_status='failed')
    with store._connect() as db:
        action = create_action(db, values={})
        receipt = append_action_receipt(db, action['id'], values={'provider_result_json': 'body A'})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt, expected_body='body A')
        before = db.execute('select count(*) from action_receipts').fetchone()[0]
        with pytest.raises(ValueError, match=f'OA Attempt {attempt_id}'):
            normalize_oa_history(db)
        assert db.execute("select attempt_oa_action_result_json from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?", (attempt_id,)).fetchone()[0] == 'body B'
        assert db.execute('select provider_result_json from action_receipts where id=?', (receipt,)).fetchone()[0] == 'body A'
        assert db.execute('select count(*) from action_receipts').fetchone()[0] == before
        update_action(db, 'reply_attempts', attempt_id, {'oa_action_result_json': 'body A'})
        normalize_oa_history(db)
        assert db.execute("select attempt_oa_action_result_json from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?", (attempt_id,)).fetchone()[0] == ''
        assert db.execute('select receipt_id from reply_attempt_receipt_refs').fetchone()[0] == receipt


def test_unrelated_metadata_does_not_claim_oa_facts(tmp_path):
    from app.action_entity_storage import create_action, append_action_receipt
    from app.reply_receipt_reference import add_reference
    store = AutoReplyStore(tmp_path / 'oa.sqlite3')
    body = '{"success":false}'
    attempt_id = store.record_reply_attempt(conversation_id='c', conversation_title='c', trigger_message_id='m', trigger_sender='s', trigger_text='t', action='oa_approval', sensitivity_kind='normal', oa_process_instance_id='p', oa_task_id='t', oa_action='approve', oa_action_result_json=body, send_status='completed')
    target = json.dumps({'process_instance_id': 'p', 'task_id': 't'}, separators=(',', ':'))
    with store._connect() as db:
        action = create_action(db, values={})
        receipt = append_action_receipt(db, action['id'], values={'provider_result_json': body})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=body)
        for outcome in (None, '{}', '{"success":1}', 'invalid'):
            append_action_receipt(db, action['id'], values={'provider_receipt_id': receipt, 'operation': 'approve', 'status': 'done', 'target': target, 'outcome_json': outcome})
        assert list_recorded_oa_results(db, [attempt_id]) == []
        normalize_oa_history(db)
        facts = list_recorded_oa_results(db, [attempt_id])
        assert len(facts) == 1 and facts[0]['status'] is None
        attempts, runs = load_oa_history_source(db)
        assert resolve_approval_history_result(attempts[0], runs[attempt_id], recorded_actions=facts) is ApprovalHistoryResult.UNKNOWN
