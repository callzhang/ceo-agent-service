"""Green deterministic repairs finish through the same native Agent conversation."""
import json

from app.automatic_repair_store import get_repair, TERMINAL
from app.automatic_repair_contracts import REPAIR_MERGE_CONTRACT
from tests.test_automatic_repair_gate import finish, store as store
from tests.test_repair_pr_feedback import attached, snapshot, failed_check, poll


def green(**kwargs):
    return snapshot(checks=[dict(failed_check(), conclusion='SUCCESS')], **kwargs)


def decision(outcome='merged', head='abc'):
    return json.dumps(dict(outcome=outcome, head_sha=head,
                           summary='Proven parser defect; red/green regression verified.',
                           deterministic_fix=outcome == 'merged',
                           regression_evidence=['pytest focused_case: failed before, passed after']))


def test_green_pr_starts_bounded_merge_turn_in_same_conversation(store, tmp_path):
    identity, before = attached(store, tmp_path)
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    assert entry['status'] == 'merging'
    assert entry['task_id'] == before['task_id']
    assert len(entry['turn_ids']) == 2
    turn = store.get_turn(entry['turn_ids'][-1])
    assert store.get_turn_result_contract(turn.id) == REPAIR_MERGE_CONTRACT
    assert '--match-head-commit abc' in turn.user_text
    assert '--merge' in turn.user_text and 'main' in turn.user_text
    poll(store, tmp_path, green())
    assert get_repair(store, 'errors', identity)['turn_ids'] == entry['turn_ids']


def test_merge_claim_requires_remote_merge_commit_and_does_not_resolve_source(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    finish(store, entry['turn_ids'][-1], decision())
    poll(store, tmp_path, green())
    assert get_repair(store, 'errors', identity)['status'] != 'merged'
    retry = get_repair(store, 'errors', identity)
    finish(store, retry['turn_ids'][-1], decision())
    poll(store, tmp_path, dict(green(state='MERGED'), mergeCommit={'oid': 'merged123'}, mergedAt='now'))
    entry = get_repair(store, 'errors', identity)
    assert entry['status'] == 'merged'
    assert entry['merge_commit'] == 'merged123'
    assert entry['merged_at'] == 'now'
    assert 'merged' in TERMINAL
    assert not store.get_error(identity).resolved_at


def test_product_tradeoff_stays_for_review_and_new_head_gets_new_assessment(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    finish(store, entry['turn_ids'][-1], decision('review_required'))
    poll(store, tmp_path, green())
    blocked = get_repair(store, 'errors', identity)
    assert blocked['status'] == 'review_required'
    poll(store, tmp_path, green())
    assert get_repair(store, 'errors', identity)['turn_ids'] == blocked['turn_ids']
    poll(store, tmp_path, green(head='new-head'))
    changed = get_repair(store, 'errors', identity)
    assert changed['status'] == 'merging'
    assert len(changed['turn_ids']) == len(blocked['turn_ids']) + 1


def test_missing_merge_receipt_does_not_end_monitoring(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, green(state='MERGED'))
    assert get_repair(store, 'errors', identity)['status'] == 'awaiting_pr'


def test_merge_intent_recovery_reuses_created_turn(store, tmp_path, monkeypatch):
    import app.automatic_repair as module
    import pytest
    identity, _ = attached(store, tmp_path)
    original = module.save_repair
    calls = 0
    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('checkpoint interruption')
        return original(*args)
    monkeypatch.setattr(module, 'save_repair', interrupted)
    with pytest.raises(RuntimeError, match='checkpoint interruption'):
        poll(store, tmp_path, green())
    saved = get_repair(store, 'errors', identity)
    prompt = saved['pending_feedback_turn']['prompt']
    monkeypatch.setattr(module, 'save_repair', original)
    poll(store, tmp_path, green(head='newer'))
    entry = get_repair(store, 'errors', identity)
    assert len(entry['turn_ids']) == 2
    assert store.get_turn(entry['turn_ids'][-1]).user_text == prompt
    assert '--match-head-commit abc' in prompt
    with store._connect() as db:
        assert db.execute('select count(*) from workbench_turns').fetchone()[0] == 2


def test_unverified_merge_is_bounded_and_new_head_restarts_assessment(store, tmp_path):
    from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
    identity, _ = attached(store, tmp_path)
    for _ in range(MAX_AUTOMATIC_ATTEMPTS):
        poll(store, tmp_path, green())
        entry = get_repair(store, 'errors', identity)
        finish(store, entry['turn_ids'][-1], decision())
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    assert entry['status'] == 'merge_blocked'
    assert len(entry['turn_ids']) == 1 + MAX_AUTOMATIC_ATTEMPTS
    poll(store, tmp_path, green(head='newer'))
    assert get_repair(store, 'errors', identity)['status'] == 'merging'


def test_other_base_merge_is_not_reported_as_main_merged(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, dict(green(state='MERGED'), baseRefName='other', mergeCommit={'oid': 'sha'}, mergedAt='now'))
    assert get_repair(store, 'errors', identity)['status'] == 'pr_closed'


def test_native_merge_schema_and_evidence_validation():
    from app.automatic_repair_contracts import RepairMergeResult
    from app.workbench.result_contracts import get_result_contract
    import pytest
    from pydantic import ValidationError
    contract = get_result_contract(REPAIR_MERGE_CONTRACT)
    assert json.loads(contract.schema_path.read_text()) == RepairMergeResult.model_json_schema()
    with pytest.raises(ValidationError, match='regression evidence'):
        RepairMergeResult.model_validate_json(json.dumps(dict(
            outcome='merged', head_sha='abc', summary='done', deterministic_fix=False,
            regression_evidence=[],
        )))


def test_stopped_merge_reads_real_receipt_without_starting_another_turn(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    store.request_stop(entry['turn_ids'][-1])
    poll(store, tmp_path, dict(green(state='MERGED'), mergeCommit={'oid': 'accepted'}, mergedAt='now'))
    result = get_repair(store, 'errors', identity)
    assert result['status'] == 'merged'
    assert result['merge_commit'] == 'accepted'
    assert result['turn_ids'] == entry['turn_ids']


def test_stopped_merge_without_effect_remains_stopped(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, green())
    entry = get_repair(store, 'errors', identity)
    store.request_stop(entry['turn_ids'][-1])
    poll(store, tmp_path, green())
    result = get_repair(store, 'errors', identity)
    assert result['status'] == 'stopped'
    assert result['turn_ids'] == entry['turn_ids']
