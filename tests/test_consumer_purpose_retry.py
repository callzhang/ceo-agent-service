from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.consumer_purpose_retry import (
    PurposeRetryBudget,
    RetryBudgetExhausted,
    verified_access_request,
    verified_access_request_for_file,
)
from app.store import AutoReplyStore


NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def test_ten_retries_are_persisted_across_instances_and_revisions(tmp_path):
    path = tmp_path / 'state.db'
    budget = PurposeRetryBudget(AutoReplyStore(path), 'oa:process:task', 'oa')
    now = NOW
    for attempt in range(10):
        next_at = budget.schedule('receipt', NOW, f'review-{attempt}', now)
        # A repeated poll must not move the wakeup or deadline.
        assert budget.schedule('receipt', NOW, f'review-{attempt}', now + timedelta(seconds=1)) == next_at
        now = next_at
        budget = PurposeRetryBudget(AutoReplyStore(path), 'oa:process:task', 'oa')
        assert budget.reserve(f'new-session/revision-{attempt}', now) == attempt + 1
        assert budget.reserve(f'new-session/revision-{attempt}', now) == attempt + 1
    with pytest.raises(RetryBudgetExhausted, match='count'):
        budget.schedule('receipt', NOW, 'review-11', now)
    with pytest.raises(RetryBudgetExhausted):
        budget.reserve('session-11', now)


def test_deadline_starts_at_request_receipt_not_first_consumer_retry(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    boundary = NOW + timedelta(days=3)
    next_at = budget.schedule('receipt', NOW, 'review', boundary - timedelta(seconds=20))
    assert next_at == boundary
    with pytest.raises(RetryBudgetExhausted, match='deadline'):
        budget.reserve('turn', boundary)
    assert budget.state()['count'] == 0


def test_new_receipt_cannot_reset_existing_budget(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    first = budget.schedule('receipt', NOW, 'review', NOW)
    budget.reserve('turn', first)
    with pytest.raises(ValueError, match='receipt'):
        budget.schedule('different-receipt', NOW + timedelta(days=1), 'review-2', first)
    assert budget.state()['count'] == 1
    assert budget.state()['deadline'] == (NOW + timedelta(days=3)).isoformat()


def test_distinct_file_receipts_share_the_original_retry_budget(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    first = budget.schedule('receipt-file-a', NOW, 'file-a-review', NOW)
    budget.reserve('turn-1', first)

    second_request_time = NOW + timedelta(minutes=8)
    second = budget.schedule(
        'receipt-file-b', second_request_time, 'file-b-review', second_request_time,
    )

    state = budget.state()
    assert second == second_request_time + timedelta(minutes=10)
    assert state['count'] == 1
    assert state['deadline'] == (NOW + timedelta(days=3)).isoformat()
    assert {item['receipt'] for item in state['receipts']} == {
        'receipt-file-a', 'receipt-file-b',
    }


def test_consumer_permission_requests_are_file_scoped_and_audit_free_budget_is_shared(tmp_path):
    store = AutoReplyStore(tmp_path / 'state.db')
    task = SimpleNamespace(id=42, business_object_key='oa:process:task')
    budget = PurposeRetryBudget(store, task.business_object_key, 'oa')
    budget.begin_access_request(file_id='file-a', action_key='request-a', candidate_id=1, now=NOW)
    budget.finish_access_request(file_id='file-a', action_key='request-a', status='verified',
                                 result={'comment_id': 'comment-a'}, now=NOW)
    first = budget.schedule('request-a', NOW, 'consumer:1', NOW)
    with pytest.raises(ValueError, match='already exists'):
        budget.begin_access_request(file_id='file-a', action_key='request-a2', candidate_id=2, now=NOW)

    second_time = NOW + timedelta(hours=1)
    budget.begin_access_request(file_id='file-b', action_key='request-b', candidate_id=2, now=second_time)
    budget.finish_access_request(file_id='file-b', action_key='request-b', status='verified',
                                 result={'comment_id': 'comment-b'}, now=second_time)
    second = budget.schedule('request-b', second_time, 'consumer:2', second_time)

    state = budget.state()
    assert first == NOW + timedelta(seconds=300)
    assert second == second_time + timedelta(seconds=300)
    assert state['count'] == 0
    assert state['deadline'] == (NOW + timedelta(days=3)).isoformat()
    assert {row['receipt'] for row in state['receipts']} == {'request-a', 'request-b'}
    assert {row['file_id'] for row in state['access_requests']} == {'file-a', 'file-b'}


def test_readding_same_file_receipt_does_not_duplicate_or_extend_budget(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    next_at = budget.schedule('receipt-file-a', NOW, 'file-a-review', NOW)
    repeated = budget.schedule('receipt-file-a', NOW, 'file-a-review', NOW + timedelta(minutes=1))

    assert repeated == next_at
    assert len(budget.state()['receipts']) == 1
    assert budget.state()['deadline'] == (NOW + timedelta(days=3)).isoformat()


def test_missing_receipt_and_future_request_time_are_rejected(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    with pytest.raises(ValueError):
        budget.schedule('', NOW, 'review', NOW)
    with pytest.raises(ValueError):
        budget.schedule('receipt', NOW + timedelta(seconds=1), 'review', NOW)


def test_closed_budget_retains_the_original_counter_and_deadline(tmp_path):
    budget = PurposeRetryBudget(AutoReplyStore(tmp_path / 'state.db'), 'oa:x', 'oa')
    due = budget.schedule('receipt', NOW, 'review-1', NOW)
    budget.reserve('turn-1', due)
    budget.close()
    assert budget.state()['closed'] is True
    budget.schedule('receipt', NOW, 'review-2', due)
    assert budget.state()['count'] == 1
    assert budget.state()['deadline'] == (NOW + timedelta(days=3)).isoformat()


def test_retry_accepts_canonical_receipt_evidence_reference():
    class Store:
        def list_verified_candidate_actions(self, task_id):
            assert task_id == 42
            return [{
                'external_action_key': '11b23bef911e60d1048d41d02cfc0bab',
                'candidate_id': 'candidate-1',
                'created_at': NOW.isoformat(),
            }]

        def get_review_candidate(self, candidate_id):
            assert candidate_id == 'candidate-1'
            return {'candidate_json': json.dumps({
                'purpose_output': {'action': 'request_access'},
            })}

    receipt, requested_at = verified_access_request(
        Store(), SimpleNamespace(id=42),
        ['receipt:11b23bef911e60d1048d41d02cfc0bab'],
    )

    assert receipt == '11b23bef911e60d1048d41d02cfc0bab'
    assert requested_at == NOW


def test_retry_accepts_prior_receipt_id_evidence_reference():
    class Store:
        def list_verified_candidate_actions(self, _task_id):
            return [{
                'external_action_key': '11b23bef911e60d1048d41d02cfc0bab',
                'candidate_id': 'candidate-1',
                'created_at': NOW.isoformat(),
            }]

        def get_review_candidate(self, _candidate_id):
            return {'candidate_json': json.dumps({
                'purpose_output': {'action': 'request_access'},
            })}

    receipt, requested_at = verified_access_request(
        Store(), SimpleNamespace(id=42),
        ['prior_receipt_id=11b23bef911e60d1048d41d02cfc0bab'],
    )

    assert receipt == '11b23bef911e60d1048d41d02cfc0bab'
    assert requested_at == NOW


def test_retry_accepts_verified_legacy_material_read_access_comment():
    class Store:
        def list_verified_candidate_actions(self, task_id):
            assert task_id == 42
            return [{
                'external_action_key': '11b23bef911e60d1048d41d02cfc0bab',
                'candidate_id': 'legacy-candidate',
                'created_at': NOW.isoformat(),
                'operation': 'comment',
                'action_identity': (
                    'oa-comment:process-1:task-1:material-read-access-block'
                ),
            }]

        def get_review_candidate(self, candidate_id):
            assert candidate_id == 'legacy-candidate'
            # Before purpose_output existed, the approved proposal itself
            # identified the material permission request.
            return {'candidate_json': json.dumps({
                'proposal': {'actions': [{
                    'operation': 'comment',
                    'action_identity': (
                        'oa-comment:process-1:task-1:material-read-access-block'
                    ),
                    'payload': {'content': '请为审批人开放所提交材料的只读访问权限。'},
                }]},
            })}

    receipt, requested_at = verified_access_request(
        Store(), SimpleNamespace(id=42),
        ['prior_receipt_id=11b23bef911e60d1048d41d02cfc0bab'],
    )

    assert receipt == '11b23bef911e60d1048d41d02cfc0bab'
    assert requested_at == NOW


def test_retry_rejects_ordinary_legacy_comment_as_access_request():
    class Store:
        def list_verified_candidate_actions(self, _task_id):
            return [{
                'external_action_key': '11b23bef911e60d1048d41d02cfc0bab',
                'candidate_id': 'legacy-candidate',
                'created_at': NOW.isoformat(),
                'operation': 'comment',
                'action_identity': 'oa-comment:process-1:task-1:ordinary-comment',
            }]

        def get_review_candidate(self, _candidate_id):
            return {'candidate_json': json.dumps({
                'proposal': {'actions': [{
                    'operation': 'comment',
                    'action_identity': 'oa-comment:process-1:task-1:ordinary-comment',
                    'payload': {'content': '请关注项目进展。'},
                }]},
            })}

    with pytest.raises(ValueError, match='exactly one verified'):
        verified_access_request(
            Store(), SimpleNamespace(id=42),
            ['prior_receipt_id=11b23bef911e60d1048d41d02cfc0bab'],
        )


def test_retry_does_not_fuzzily_strip_receipt_reference():
    class Store:
        def list_verified_candidate_actions(self, _task_id):
            return [{
                'external_action_key': '11b23bef911e60d1048d41d02cfc0bab',
                'candidate_id': 'candidate-1',
                'created_at': NOW.isoformat(),
            }]

        def get_review_candidate(self, _candidate_id):
            return {'candidate_json': json.dumps({
                'purpose_output': {'action': 'request_access'},
            })}

    with pytest.raises(ValueError, match='exactly one verified'):
        verified_access_request(
            Store(), SimpleNamespace(id=42),
            ['receipt:receipt:11b23bef911e60d1048d41d02cfc0bab'],
        )


def test_verified_file_request_lookup_distinguishes_files():
    class Store:
        def list_verified_candidate_actions(self, _task_id):
            return [
                {'external_action_key': 'receipt-a', 'candidate_id': 'candidate-a',
                 'created_at': NOW.isoformat()},
                {'external_action_key': 'receipt-b', 'candidate_id': 'candidate-b',
                 'created_at': (NOW + timedelta(minutes=8)).isoformat()},
            ]

        def get_review_candidate(self, candidate_id):
            file_id = {'candidate-a': 'file-a', 'candidate-b': 'file-b'}[candidate_id]
            return {'candidate_json': json.dumps({'purpose_output': {
                'action': 'request_access', 'target': {'file_id': file_id},
            }})}

    store = Store()
    assert verified_access_request_for_file(store, SimpleNamespace(id=42), 'file-a') == (
        'receipt-a', NOW,
    )
    assert verified_access_request_for_file(store, SimpleNamespace(id=42), 'file-b') == (
        'receipt-b', NOW + timedelta(minutes=8),
    )
    assert verified_access_request_for_file(store, SimpleNamespace(id=42), 'file-c') is None
