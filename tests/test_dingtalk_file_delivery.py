"""Reviewed file bytes and destination are dispatched only by System."""
import hashlib
import json
from pathlib import Path

import pytest

from app.agent_contracts import ConsumerAgentResult, ProposedAction
from app.dws_client import DwsClient, DwsError
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole, AutoReplyStore
from app.system_action_handlers import native_dws_handlers
from app.system_executor import SystemExecutor


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    from app import task_artifacts
    monkeypatch.setattr(task_artifacts, 'TASK_ARTIFACT_ROOT', tmp_path / 'artifacts')
    store = AutoReplyStore(tmp_path / 'store.sqlite3')
    store.enqueue_reply_task(channel='dingtalk', conversation_id='finance',
        conversation_title='Finance', single_chat=False, trigger_message_id='request',
        trigger_sender='Requester', trigger_create_time='2026-11-06 00:00:00', trigger_text='Send original')
    task = store.claim_reply_tasks(1)[0]
    root = task_artifacts.task_artifact_directory(task.id, task.execution_generation, create=True)
    data = b'%PDF-1.7\noriginal bill\n'
    (root / 'invoice.pdf').write_bytes(data)
    action = {'description': 'Deliver original', 'action_identity': 'deliver-original',
        'capability': 'dingtalk-chat', 'operation': 'send_file',
        'target': {'conversation_id': 'finance'},
        'payload': {'name': 'invoice.pdf', 'sha256': hashlib.sha256(data).hexdigest()}}
    class Dws(DwsClient):
        def __init__(self):
            super().__init__(dws_bin='dws')
            self.sends = []
            self.queries = []
            self.status = 'success'
            self.receipt = {'result': {'openTaskId': 'send-task'}}
        def run_json(self, command, **kwargs):
            if '+messages-send' in command:
                path = Path(kwargs['cwd']) / command[command.index('--file') + 1]
                self.sends.append((command, path.read_bytes()))
                return self.receipt
            self.queries.append(command)
            if self.status == 'timeout':
                raise DwsError('read timed out')
            return {'result': {'sendStatus': self.status, 'openMessageId': 'sent-file',
                'openConversationId': 'finance'}}
    return store, task, root, data, action, Dws()


def reviewed(store, task, action):
    result = ConsumerAgentResult.model_validate({'outcome': 'proposal', 'summary': 'Deliver original',
        'proposal': {'objective': 'Deliver original', 'actions': [action], 'sourced_facts': [],
            'authored_judgment': 'Requested original'},
        'error': {'code': '', 'retryable': False, 'authorization_required': False},
        'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0, 'information_completeness': 1.0})
    consumer = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None, operation_id='', owner='consumer').run
    store.complete_agent_run(consumer.id, result.model_dump(mode='json'), owner='consumer')
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id='audit', owner='audit').run
    body = {'outcome': 'approve', 'candidate_digest': candidate_digest(result)}
    store.complete_agent_run(audit.id, body, owner='audit')
    review = store.record_candidate_review(candidate['id'], audit.id, body)
    return candidate, review


def execute(delivery):
    store, task, _, _, action, dws = delivery
    candidate, review = reviewed(store, task, action)
    executor = SystemExecutor(store, native_dws_handlers(store, dws), owner='system')
    result = executor.execute(task, candidate['id'], review['id'])
    return executor, candidate, review, result


def test_file_delivery_uses_reviewed_bytes_target_and_provider_receipt(delivery):
    store, task, root, data, _, dws = delivery
    executor, candidate, review, result = execute(delivery)
    assert result.outcome == 'executed'
    assert len(dws.sends) == 1
    command, sent = dws.sends[0]
    assert sent == data
    assert command[command.index('--group') + 1] == 'finance'
    assert command[command.index('--file') + 1] == 'invoice.pdf'
    assert '--yes' in command and '--uuid' in command
    assert '--markdown' not in command and '--wait-delivery' not in command
    assert dws.queries
    receipt = store.get_candidate_external_action(result.external_result.operation_id)
    assert json.loads(receipt['provider_result_json'])['file']['sha256'] == hashlib.sha256(data).hexdigest()
    assert executor.execute(task, candidate['id'], review['id']).outcome == 'executed'
    assert len(dws.sends) == 1
    assert list(root.parent.glob('.file-delivery-*')) == []


@pytest.mark.parametrize('mutation', ['bytes', 'symlink', 'missing'])
def test_changed_or_missing_file_is_not_sent(delivery, mutation):
    _, _, root, _, _, dws = delivery
    path = root / 'invoice.pdf'
    if mutation == 'bytes':
        path.write_bytes(b'another invoice')
    else:
        path.unlink()
        if mutation == 'symlink':
            path.symlink_to(root.parent / 'elsewhere.pdf')
    *_, result = execute(delivery)
    assert result.outcome == 'failed'
    assert not dws.sends


def test_target_changed_after_audit_does_not_send(delivery):
    store, task, _, _, action, dws = delivery
    candidate, review = reviewed(store, task, action)
    # Persisted review digest remains the original. Corrupted/current candidate must fail closed.
    original = store.current_reviewed_candidate
    def changed(*args):
        row = dict(original(*args))
        body = json.loads(row['candidate_json'])
        body['proposal']['actions'][0]['target'] = {'open_dingtalk_id': 'wrong-person'}
        row['candidate_json'] = json.dumps(body)
        return row
    store.current_reviewed_candidate = changed
    result = SystemExecutor(store, native_dws_handlers(store, dws), owner='system').execute(
        task, candidate['id'], review['id'])
    assert result.outcome == 'failed'
    assert not dws.sends


@pytest.mark.parametrize('status', ['pending', 'timeout'])
def test_unconfirmed_file_send_only_reconciles_across_restart(delivery, status):
    store, task, _, data, _, dws = delivery
    dws.status = status
    _, candidate, review, result = execute(delivery)
    assert result.outcome == 'failed'
    assert len(dws.sends) == 1
    # Provider identity survives the failed readback and process restart.
    reopened = AutoReplyStore(store.path)
    dws.status = 'success'
    result = SystemExecutor(reopened, native_dws_handlers(reopened, dws), owner='restarted').execute(
        task, candidate['id'], review['id'])
    assert result.outcome == 'executed'
    assert len(dws.sends) == 1
    assert len(dws.queries) >= 2


def test_no_send_identity_remains_unconfirmed_and_is_never_resent(delivery):
    _, task, _, _, _, dws = delivery
    dws.receipt = {'success': True}
    executor, candidate, review, result = execute(delivery)
    assert result.outcome == 'failed'
    assert executor.execute(task, candidate['id'], review['id']).outcome == 'failed'
    assert len(dws.sends) == 1


@pytest.mark.parametrize('target', [{}, {'conversation_id': 'finance', 'user_id': 'someone'},
                                  {'user_id': 12}])
def test_file_proposal_requires_one_exact_string_target(target):
    with pytest.raises(ValueError):
        ProposedAction(description='File', action_identity='file', capability='dingtalk-chat',
            operation='send_file', target=target, payload={'name': 'bill.pdf', 'sha256': 'a' * 64})


@pytest.mark.parametrize('name,digest', [('../bill.pdf', 'a' * 64), ('/tmp/bill.pdf', 'a' * 64),
    ('bill.pdf', 'bad'), ('bill.pdf', 'A' * 64)])
def test_file_proposal_requires_safe_artifact_name_and_sha256(name, digest):
    with pytest.raises(ValueError):
        ProposedAction(description='File', action_identity='file', capability='dingtalk-chat',
            operation='send_file', target={'conversation_id': 'finance'},
            payload={'name': name, 'sha256': digest})


def test_file_upload_timeout_does_not_repeat_provider_dispatch(tmp_path, monkeypatch):
    import subprocess
    path = tmp_path / 'file.pdf'
    path.write_bytes(b'%PDF-original')
    calls = []
    def timeout(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(DwsClient, '_run_cli_process', staticmethod(timeout))
    client = DwsClient(dws_bin='dws', transient_retry_attempts=3, transient_retry_delay_seconds=0)
    with pytest.raises(DwsError):
        client.send_file(path, conversation_id='finance', idempotency_uuid='one-send')
    assert len(calls) == 1


def test_receipt_from_wrong_conversation_is_not_completion(delivery):
    _, _, _, _, _, dws = delivery
    original = dws.run_json
    def wrong_target(command, **kwargs):
        result = original(command, **kwargs)
        if '+messages-send' not in command:
            result['result']['openConversationId'] = 'other-group'
        return result
    dws.run_json = wrong_target
    *_, result = execute(delivery)
    assert result.outcome == 'failed'
    assert len(dws.sends) == 1


@pytest.mark.parametrize('field,value', [('name', 'other.pdf'), ('sha256', 'b' * 64)])
def test_changed_file_after_audit_does_not_send(delivery, field, value):
    store, task, _, _, action, dws = delivery
    candidate, review = reviewed(store, task, action)
    original = store.current_reviewed_candidate
    def changed(*args):
        row = dict(original(*args))
        body = json.loads(row['candidate_json'])
        body['proposal']['actions'][0]['payload'][field] = value
        row['candidate_json'] = json.dumps(body)
        return row
    store.current_reviewed_candidate = changed
    result = SystemExecutor(store, native_dws_handlers(store, dws), owner='system').execute(
        task, candidate['id'], review['id'])
    assert result.outcome == 'failed'
    assert not dws.sends


def test_file_delivery_is_discoverable_in_canonical_action_contract():
    from app.action_contract_catalog import read_system_action_contract
    contract = read_system_action_contract('dingtalk-chat', 'send_file')
    assert contract['supported_operations'] == ['send_file']
    assert '`sha256`' in contract['contract']
