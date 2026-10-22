"""Persistent purpose retry reservations, independent of Agent sessions."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.state_definitions import state_projection
from app.state_storage import state_json_field, upsert_state
from app.external_retry import MAX_AUTOMATIC_RETRIES

MAX_PURPOSE_RETRIES = MAX_AUTOMATIC_RETRIES
MAX_PERMISSION_WAIT = timedelta(days=3)
PERMISSION_RETRY_INTERVAL = timedelta(hours=12)
PERMISSION_RETRY_INTERVAL_SECONDS = int(PERMISSION_RETRY_INTERVAL.total_seconds())
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
        reader = getattr(self.store, 'get_service_state', None)
        raw = reader(self.key) if callable(reader) else None
        return json.loads(raw) if raw else None

    @staticmethod
    def _check(state, now):
        if now >= utc_time(state['deadline']):
            raise RetryBudgetExhausted('permission retry deadline reached (72 hours)')
        if state['count'] >= MAX_PURPOSE_RETRIES:
            raise RetryBudgetExhausted(
                f'permission retry count reached ({MAX_PURPOSE_RETRIES})'
            )

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

    def access_request_for_file(self, file_id: str):
        state = self.state() or {}
        matches = [item for item in state.get('access_requests', [])
                   if item.get('file_id') == file_id]
        if len(matches) > 1:
            raise ValueError('file has multiple permission request records')
        return matches[0] if matches else None

    def begin_access_request(self, *, file_id: str, action_key: str,
                             candidate_id: int, now: datetime):
        if not file_id.strip() or not action_key.strip() or candidate_id <= 0:
            raise ValueError('permission request identity is incomplete')
        def mutate(state):
            state = state or {'access_requests': []}
            requests = state.setdefault('access_requests', [])
            current = next((item for item in requests if item.get('file_id') == file_id), None)
            if current is not None:
                if current.get('action_key') != action_key or current.get('candidate_id') != candidate_id:
                    raise ValueError('a permission request already exists for this file')
                return state, (current, False)
            current = {'file_id': file_id, 'action_key': action_key,
                       'candidate_id': candidate_id, 'status': 'dispatching',
                       'created_at': now.astimezone(timezone.utc).isoformat(),
                       'result': {}}
            requests.append(current)
            return state, (current, True)
        return self._update(mutate)

    def finish_access_request(self, *, file_id: str, action_key: str,
                              status: str, result: dict, now: datetime):
        if status not in {'verified', 'uncertain', 'failed'}:
            raise ValueError('invalid permission request result')
        def mutate(state):
            if not isinstance(state, dict):
                raise ValueError('permission request reservation is missing')
            current = next((item for item in state.get('access_requests', [])
                            if item.get('file_id') == file_id), None)
            if current is None or current.get('action_key') != action_key:
                raise ValueError('permission request reservation does not match')
            if current.get('status') == 'verified' and status != 'verified':
                return state, current
            current.update({'status': status, 'result': result,
                            'updated_at': now.astimezone(timezone.utc).isoformat()})
            return state, current
        return self._update(mutate)

    def schedule(self, receipt: str, requested_at: datetime, review: str, now: datetime) -> datetime:
        if not receipt.strip() or not review or requested_at > now:
            raise ValueError('verified request receipt and valid request time are required')

        def mutate(state):
            if state is None:
                state = {'receipts': [], 'requested_at': requested_at.isoformat(),
                         'deadline': (requested_at + MAX_PERMISSION_WAIT).isoformat(),
                         'count': 0, 'reservations': [], 'review': '',
                         'retry_interval_seconds': PERMISSION_RETRY_INTERVAL_SECONDS}
            else:
                state.setdefault('access_requests', [])
                if 'requested_at' not in state:
                    state.update({'receipts': [], 'requested_at': requested_at.isoformat(),
                                  'deadline': (requested_at + MAX_PERMISSION_WAIT).isoformat(),
                                  'count': 0, 'reservations': [], 'review': '',
                                  'retry_interval_seconds': PERMISSION_RETRY_INTERVAL_SECONDS})
                # Existing persisted budgets used one receipt. Promote it into
                # the receipt set in place, preserving the original deadline,
                # count and reservation history.
                receipts = state.get('receipts')
                if receipts is None:
                    old_receipt = state.pop('receipt', None)
                    receipts = ([{'receipt': old_receipt,
                                  'requested_at': state['requested_at']}]
                                if old_receipt else [])
                    state['receipts'] = receipts
            receipts = state['receipts']
            if not any(item['receipt'] == receipt for item in receipts):
                receipts.append({'receipt': receipt, 'requested_at': requested_at.isoformat()})
            self._check(state, now)
            state['closed'] = False
            if (
                state.get('retry_interval_seconds')
                != PERMISSION_RETRY_INTERVAL_SECONDS
                or state['review'] != review
            ):
                state['next_at'] = min(
                    now + PERMISSION_RETRY_INTERVAL,
                    utc_time(state['deadline']),
                ).isoformat()
                state['review'] = review
                state['retry_interval_seconds'] = PERMISSION_RETRY_INTERVAL_SECONDS
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

    def claim_manual_access_check(self, *, attempt_id: int, generation: int, now: datetime) -> bool:
        """Reserve the single human-triggered access check after exhaustion."""
        def mutate(state):
            if not isinstance(state, dict):
                return state, False
            if state.get("manual_access_check"):
                return state, False
            exhausted = (
                state.get("count", 0) >= MAX_PURPOSE_RETRIES
                or (
                    state.get("deadline") is not None
                    and now >= utc_time(state["deadline"])
                )
            )
            verified_request = bool(state.get("receipts")) or any(
                item.get("status") == "verified"
                for item in state.get("access_requests", [])
            )
            if not exhausted or not verified_request:
                return state, False
            state["manual_access_check"] = {
                "attempt_id": attempt_id,
                "generation": generation,
                "reserved_at": now.astimezone(timezone.utc).isoformat(),
            }
            return state, True
        return self._update(mutate)

    def claim_oa_return_turn(self, *, source_consumer_run_id: int, now: datetime,
                             failed_access_request: bool = False,
                             proposal_revision: int, parent_run_id: int | None,
                             stage_index: int, predecessor_review_id: int | None) -> bool:
        """Reserve one Consumer turn to propose an audited OA return."""
        if source_consumer_run_id <= 0:
            return False

        def mutate(state):
            if not isinstance(state, dict):
                return state, False
            exhausted = (
                state.get("count", 0) >= MAX_PURPOSE_RETRIES
                or (
                    state.get("deadline") is not None
                    and now >= utc_time(state["deadline"])
                )
            )
            verified_request = bool(state.get("receipts")) or any(
                item.get("status") == "verified"
                for item in state.get("access_requests", [])
            )
            known_failed_request = failed_access_request and any(
                item.get("status") == "failed"
                for item in state.get("access_requests", [])
            )
            if not ((exhausted and verified_request) or known_failed_request):
                return state, False
            claimed = state.get("oa_return_source_consumer_run_id")
            if claimed is None:
                state["oa_return_source_consumer_run_id"] = source_consumer_run_id
                state["oa_return_turn"] = {
                    "source_consumer_run_id": source_consumer_run_id,
                    "proposal_revision": proposal_revision,
                    "parent_run_id": parent_run_id,
                    "stage_index": stage_index,
                    "predecessor_review_id": predecessor_review_id,
                }
                return state, True
            return state, claimed == source_consumer_run_id

        return self._update(mutate)


    def close(self):
        if self.state() is None:
            return
        def mutate(state):
            state['closed'] = True
            return state, None
        self._update(mutate)


def verified_access_request(store, task, evidence_refs):
    """Bind retry evidence to an actually verified Consumer permission request."""
    # Purpose evidence can carry a label from the source receipt projection,
    # while the verified-action store keeps the external_action_key itself.
    # Strip only a known label once; retain exact key matching afterwards.
    prefixes = ('receipt:', 'prior_receipt_id=')
    referenced_keys = {
        next((ref[len(prefix):] for prefix in prefixes if ref.startswith(prefix)), ref)
        for ref in evidence_refs
    }
    eligible = []
    for purpose in ('oa', 'general'):
        business_key = getattr(task, 'business_object_key', None) or f'reply-task:{task.id}'
        budget_state = PurposeRetryBudget(store, business_key, purpose).state() or {}
        for item in budget_state.get('receipts', []):
            if item.get('receipt') in referenced_keys:
                eligible.append((item['receipt'], utc_time(item['requested_at'])))
    for row in store.list_verified_candidate_actions(task.id):
        if row['external_action_key'] not in referenced_keys:
            continue
        candidate = store.get_review_candidate(row['candidate_id'])
        candidate_json = json.loads(candidate['candidate_json'])
        if 'purpose_output' in candidate_json:
            output = candidate_json.get('purpose_output') or {}
            if output.get('action') != 'request_access':
                continue
        else:
            # Before purpose_output was introduced, OA read-access requests
            # were reviewed comment proposals. Accept only the stable,
            # dedicated identity for that request and the exact matching
            # verified comment receipt; ordinary legacy comments do not count.
            proposal_actions = (candidate_json.get('proposal') or {}).get('actions') or []
            action_identity = row.get('action_identity', '')
            if (
                row.get('operation') != 'comment'
                or not action_identity.startswith('oa-comment:')
                or not action_identity.endswith(':material-read-access-block')
                or len(proposal_actions) != 1
                or proposal_actions[0].get('operation') != 'comment'
                or proposal_actions[0].get('action_identity') != action_identity
                or not str((proposal_actions[0].get('payload') or {}).get('content') or '').strip()
            ):
                continue
        eligible.append((row['external_action_key'], utc_time(row['created_at'])))
    unique = dict(eligible)
    if len(unique) != 1:
        raise ValueError('retry must reference exactly one verified access-request receipt for this task')
    return next(iter(unique.items()))


def verified_access_request_for_file(store, task, file_id: str):
    """Find the exact verified permission request for one source file."""
    if not isinstance(file_id, str) or not file_id.strip():
        raise ValueError('file-scoped access request requires a file id')
    matches = []
    business_key = getattr(task, 'business_object_key', None) or f'reply-task:{task.id}'
    for purpose in ('oa', 'general'):
        state = PurposeRetryBudget(store, business_key, purpose).state() or {}
        for request in state.get('access_requests', []):
            if request.get('file_id') == file_id and request.get('status') == 'verified':
                matches.append((request['action_key'], utc_time(request['created_at'])))
    for row in store.list_verified_candidate_actions(task.id):
        candidate = store.get_review_candidate(row['candidate_id'])
        result = json.loads(candidate['candidate_json'])
        output = result.get('purpose_output') or {}
        target = output.get('target') or {}
        if (output.get('action') == 'request_access'
                and target.get('file_id') == file_id):
            matches.append((row['external_action_key'], utc_time(row['created_at'])))
    unique = dict(matches)
    if len(unique) > 1:
        raise ValueError('file has multiple verified permission requests')
    return next(iter(unique.items())) if unique else None
