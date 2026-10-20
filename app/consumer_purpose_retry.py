"""Persistent purpose retry reservations, independent of Agent sessions."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.state_definitions import state_projection
from app.state_storage import state_json_field, upsert_state

MAX_PURPOSE_RETRIES = 10
MAX_PERMISSION_WAIT = timedelta(days=3)
RETRY_REQUESTED = 'consumer_purpose_retry_requested'
RETRY_EXHAUSTED = 'consumer_purpose_retry_exhausted'


class RetryBudgetExhausted(ValueError):
    pass


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class PurposeRetryBudget:
    def __init__(self, store, business_key: str, purpose: str):
        self.store = store
        self.key = 'consumer-purpose-retry:' + sha256(
            json.dumps([business_key, purpose], separators=(',', ':')).encode()
        ).hexdigest()

    def state(self):
        raw = self.store.get_service_state(self.key)
        return json.loads(raw) if raw else None

    @staticmethod
    def _check(state, now):
        if now >= utc_time(state['deadline']):
            raise RetryBudgetExhausted('permission retry deadline reached (72 hours)')
        if state['count'] >= MAX_PURPOSE_RETRIES:
            raise RetryBudgetExhausted('permission retry count reached (10)')

    def _update(self, mutate):
        with self.store._immediate_write_transaction() as db:
            row = db.execute(
                f"select value from ({state_projection('service_state')}) where key=?",
                (self.key,),
            ).fetchone()
            state = json.loads(row['value']) if row else None
            state, result = mutate(state)
            upsert_state(db, 'service_state', {'key': self.key, 'value': json.dumps(state)},
                         {'value': state_json_field('value'),
                          'updated_at': 'json_quote(current_timestamp)'})
            return result

    def schedule(self, receipt: str, requested_at: datetime, review: str, now: datetime) -> datetime:
        if not receipt.strip() or not review or requested_at > now:
            raise ValueError('verified request receipt and valid request time are required')

        def mutate(state):
            if state is None:
                state = {'receipt': receipt, 'requested_at': requested_at.isoformat(),
                         'deadline': (requested_at + MAX_PERMISSION_WAIT).isoformat(),
                         'count': 0, 'reservations': [], 'review': ''}
            if state['receipt'] != receipt:
                raise ValueError('a new receipt cannot reset the existing retry budget')
            self._check(state, now)
            state['closed'] = False
            if state['review'] != review:
                delay = min(300 * (2 ** state['count']), 12 * 3600)
                state['next_at'] = min(now + timedelta(seconds=delay), utc_time(state['deadline'])).isoformat()
                state['review'] = review
            return state, utc_time(state['next_at'])

        return self._update(mutate)

    def reserve(self, turn: str, now: datetime) -> int:
        def mutate(state):
            if state is None:
                raise ValueError('permission request has not been verified')
            if now >= utc_time(state['deadline']):
                raise RetryBudgetExhausted('permission retry deadline reached (72 hours)')
            # Rechecking an already-reserved invocation never consumes budget.
            if turn in state['reservations']:
                return state, state['count']
            self._check(state, now)
            if now < utc_time(state['next_at']):
                raise ValueError('permission retry is not due')
            state['count'] += 1
            state['reservations'].append(turn)
            return state, state['count']

        return self._update(mutate)


    def close(self):
        if self.state() is None:
            return
        def mutate(state):
            state['closed'] = True
            return state, None
        self._update(mutate)


def verified_access_request(store, task, evidence_refs):
    """Bind retry evidence to an actually verified, reviewed request action."""
    # Purpose evidence can carry a label from the source receipt projection,
    # while the verified-action store keeps the external_action_key itself.
    # Strip only a known label once; retain exact key matching afterwards.
    prefixes = ('receipt:', 'prior_receipt_id=')
    referenced_keys = {
        next((ref[len(prefix):] for prefix in prefixes if ref.startswith(prefix)), ref)
        for ref in evidence_refs
    }
    eligible = []
    for row in store.list_verified_candidate_actions(task.id):
        if row['external_action_key'] not in referenced_keys:
            continue
        candidate = store.get_review_candidate(row['candidate_id'])
        output = json.loads(candidate['candidate_json']).get('purpose_output') or {}
        if output.get('action') != 'request_access':
            continue
        eligible.append((row['external_action_key'], utc_time(row['created_at'])))
    unique = dict(eligible)
    if len(unique) != 1:
        raise ValueError('retry must reference exactly one verified access-request receipt for this task')
    return next(iter(unique.items()))
