from app.store import AgentRole, AutoReplyStore
from app.web_api.attempts import build_attempt_detail


def test_audited_no_action_detail_preserves_skipped_without_rewriting_history(tmp_path):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    store.enqueue_reply_task(
        conversation_id='current-group', conversation_title='Current discussion',
        single_chat=False, trigger_message_id='trigger-1',
        trigger_create_time='2026-10-07 01:00:00', trigger_sender='Reporter',
        trigger_text='Please review this update.',
    )
    task = store.get_reply_task_for_message('current-group', 'trigger-1')
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id='', owner='test-consumer',
    ).run
    store.complete_agent_run(consumer.id, {
        'outcome': 'no_action', 'summary': 'The principal already followed up; no duplicate reply is needed.',
    }, owner='test-consumer')
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id='current-review', owner='test-audit',
    ).run
    store.complete_agent_run(audit.id, {
        'outcome': 'approve', 'summary': 'No duplicate action is appropriate.',
        'proposal_revision': 0, 'candidate_digest': 'a' * 64,
    }, owner='test-audit')
    attempt_id = store.record_reply_attempt(
        conversation_id='current-group', conversation_title='Current discussion',
        trigger_message_id='trigger-1', trigger_sender='Reporter',
        trigger_text='Please review this update.', action='agent_run',
        sensitivity_kind='general', send_status='skipped',
    )
    with store._connect() as db:
        db.execute('update reply_attempts set agent_run_id=? where id=?', (audit.id, attempt_id))
        db.execute("update reply_tasks set status='done' where id=?", (task.id,))
    original_attempt = store.get_reply_attempt(attempt_id)
    original_task = store.get_reply_task(task.id)
    status, detail = build_attempt_detail(store, attempt_id)
    assert status == 200
    assert detail['status']['raw'] == 'skipped'
    assert detail['actions']['terminal'] is True
    assert detail['actions']['action_label'] == '无需操作'
    assert detail['what_happened']['reached_the_outside_world'] is False
    assert store.get_reply_attempt(attempt_id) == original_attempt
    assert store.get_reply_task(task.id) == original_task
