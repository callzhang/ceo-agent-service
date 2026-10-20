"""Reply and unsubscribe share physical step claims without conflating proof."""
import sqlite3

import pytest

from app import shared_action_lifecycle as lifecycle
from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_store import EmailStore
from app.store import AutoReplyStore
from tests.test_email_action_reply_dao import _reply_arguments, _receipt_arguments
from tests.test_email_action_unsubscribe_dao import _authorization, _continuation_terminal_cycle
from tests.test_email_store import _classification, _task2_account_values


@pytest.fixture
def specialized(tmp_path):
    path = tmp_path / 'specialized.sqlite3'
    AutoReplyStore(path)
    email = EmailStore(path)
    email.create_account(_task2_account_values('dingtalk-account', enabled=True))
    email._now = lambda: '2026-10-19T10:00:00+00:00'
    values = []
    for number, action in ((11, EmailAction.AUTO_REPLY), (12, EmailAction.UNSUBSCRIBE)):
        value = _classification(status=EmailClassificationStatus.PROCESSED,
            classification_id=number, message_id=f'specialized-{number}', actions=(action,),
            action_parameters={EmailAction.AUTO_REPLY: {'instruction': 'Acknowledge receipt.'}} if action == EmailAction.AUTO_REPLY else {},
            thread_id='thread-unsubscribe-41' if action == EmailAction.UNSUBSCRIBE else 'thread-reply')
        email.persist_scan_result(value, model_text='fixture features')
        values.append(value)
    return email, *values


def test_specialized_claims_and_results_use_common_writer(specialized, monkeypatch):
    email, reply, unsubscribe = specialized
    calls = []
    original = lifecycle.transition
    def observed(db, origin, event, **values):
        calls.append((origin, event))
        return original(db, origin, event, **values)
    monkeypatch.setattr(lifecycle, 'transition', observed)
    args, owner = _reply_arguments(email, reply)
    assert email.claim_email_reply_dispatch(**args, owner=owner)['acquired']
    email.mark_email_reply_dispatch_uncertain(args['action_identity'], owner=owner)
    receipt = email.persist_email_reply_receipt(**_receipt_arguments(args), claim_owner=owner)
    assert receipt['provider_operation'] == 'sent_readback'
    assert not email.claim_email_reply_dispatch(**args, owner=owner)['acquired']
    a, unsub_owner = _authorization(unsubscribe)
    assert email.claim_email_unsubscribe_write(**a, owner=unsub_owner)['acquired']
    terminal = email.persist_email_unsubscribe_terminal(**a, outcome='done',
        receipt_id='test-receipt', evidence='terminal-page', claim_owner=unsub_owner)
    assert terminal['receipt_id'] == 'test-receipt'
    assert {('email_reply', 'claim'), ('email_reply', 'state'), ('email_reply', 'outcome'),
            ('email_unsubscribe', 'claim'), ('email_unsubscribe', 'outcome')} <= set(calls)
    with sqlite3.connect(email.path) as db:
        assert db.execute("select _kind,action_family,action_origin from actions where record_type='email_reply_dispatch_claims'").fetchone() == (68, 'effect_steps', 'email_reply')
        assert db.execute("select _kind,action_family,action_origin from actions where record_type='email_unsubscribe_claims'").fetchone() == (69, 'effect_steps', 'email_unsubscribe')
        assert db.execute("select count(*) from actions where record_type='external_action_results'").fetchone()[0] == 0
        assert db.execute('pragma foreign_key_check').fetchall() == []


def test_unsubscribe_keeps_effect_versions_and_observation_steps(specialized):
    email, _, unsubscribe = specialized
    result = _continuation_terminal_cycle(email, unsubscribe)
    a, _ = _authorization(unsubscribe)
    with sqlite3.connect(email.path) as db:
        assert db.execute("select _kind from actions where record_type='email_unsubscribe_claims'").fetchone()[0] == 69
        assert db.execute("select count(*) from actions where record_type='email_unsubscribe_effects'").fetchone()[0] == 2
        assert db.execute("select email_sequence from actions where record_type='email_unsubscribe_steps' order by email_sequence").fetchall() == [(1,), (2,)]
    assert email.get_email_unsubscribe_claim(a['action_identity'])['status'] == 'done'
    assert result[4] == result[5]


@pytest.mark.parametrize('origin', ['email_reply', 'email_unsubscribe'])
def test_concurrent_claim_has_exactly_one_owner_and_atomic_receipt_conflict(specialized, origin):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.email_store import EmailReplyDispatchConflict, EmailUnsubscribeClaimConflict
    email, reply, unsubscribe = specialized
    args, owner = _reply_arguments(email, reply) if origin == 'email_reply' else _authorization(unsubscribe)
    claim = email.claim_email_reply_dispatch if origin == 'email_reply' else email.claim_email_unsubscribe_write
    owners = [owner, owner | {'generation': owner['generation'] + 1, 'lease_token':'contender'}]
    ready = threading.Barrier(2)
    def run(index):
        ready.wait(timeout=10)
        return claim(**args, owner=owners[index])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sum(result['acquired'] for result in results) == 1
    winner = next(index for index, result in enumerate(results) if result['acquired'])
    if origin == 'email_reply':
        with pytest.raises(EmailReplyDispatchConflict, match='owner fence'):
            email.persist_email_reply_receipt(**_receipt_arguments(args), claim_owner=owners[1-winner])
        assert email.get_email_reply_receipt(args['action_identity']) is None
        email.persist_email_reply_receipt(**_receipt_arguments(args), claim_owner=owners[winner])
    else:
        with pytest.raises(EmailUnsubscribeClaimConflict, match='owner fence'):
            email.persist_email_unsubscribe_terminal(**args, outcome='done', receipt_id='proof',
                evidence='terminal-page', claim_owner=owners[1-winner])
        assert email.get_email_unsubscribe_receipt(args['action_identity']) is None
        email.persist_email_unsubscribe_terminal(**args, outcome='done', receipt_id='proof',
            evidence='terminal-page', claim_owner=owners[winner])
    assert not claim(**args, owner=owners[1-winner])['acquired']


def test_reply_outgoing_identity_unique_and_state_update_seeks_physical_row(specialized):
    email, reply, _ = specialized
    args, owner = _reply_arguments(email, reply)
    email.claim_email_reply_dispatch(**args, owner=owner)
    other = _classification(status=EmailClassificationStatus.PROCESSED,
        classification_id=13, message_id='other-reply', thread_id='other-thread', actions=(EmailAction.AUTO_REPLY,),
        action_parameters={EmailAction.AUTO_REPLY:{'instruction':'Acknowledge receipt.'}})
    email.persist_scan_result(other, model_text='fixture features')
    other_args, other_owner = _reply_arguments(email, other)
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        email.claim_email_reply_dispatch(**other_args, owner=other_owner)
    assert email.get_email_reply_dispatch_claim(other_args['action_identity']) is None
    with email._connect() as db:
        predicate, parameters = lifecycle._predicate('email_reply_dispatch_claims', None,
            'action_identity=?', (args['action_identity'],))
        details = [row[3] for row in db.execute('explain query plan update actions set payload_json=payload_json where '+predicate, parameters)]
    assert any('SEARCH actions USING INTEGER PRIMARY KEY' in detail for detail in details)
    assert any('SEARCH a USING INDEX idx_actions_email_email_reply_dispatch_claims' in detail for detail in details)
