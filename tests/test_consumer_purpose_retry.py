from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.consumer_purpose_retry import (
    PurposeRetryBudget,
    RetryBudgetExhausted,
    verified_access_request,
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
