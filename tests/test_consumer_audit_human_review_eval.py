from hashlib import sha256
import json

from scripts import eval_consumer_audit_human_review as review


def test_frozen_questions_and_settings():
    v1 = review.ROOT / 'evals/consumer_audit_human_review/v1.json'
    v2 = review.ROOT / 'evals/consumer_audit_human_review/v2.json'
    assert sha256(v1.read_bytes()).hexdigest() == "fe981f2b18c98c2032f79243de686e87134ccae48e99d9a421d3aa7811124d1c"
    assert sha256(v2.read_bytes()).hexdigest() == "ebddd919f764a244c30b08fcc726bcfedfc4c4b535e897bc5f4af20e055228a8"
    assert sha256(review.MANIFEST.read_bytes()).hexdigest() == "cc94a3f7783ea20742194ac81d11a137bec49301c6f7df1992aeb71a6f842bbe"
    manifest = json.loads(review.MANIFEST.read_text())
    assert manifest['version'] == 'consumer-audit-human-review.v3'
    assert len(manifest['cases']) == 8
    assert sum(case['expected_audit'] == ['approve'] for case in manifest['cases']) == 2
    assert manifest['settings'] == {
        'model': 'gpt-5.6-sol', 'reasoning_effort': 'high', 'concurrency': 1,
        'timeout_seconds_per_case': 300, 'tools': 'none',
        'external_facts': 'synthetic-inline-only',
    }
    for case in manifest['cases']:
        deliberately_invalid_new_output = case['expected_audit'] != ['approve']
        subject = review.business.normalize_review_subject(
            review.ROOT, case['consumer_result'],
            persisted_evidence=deliberately_invalid_new_output,
        )
        assert subject['ok'], case['id']
        if deliberately_invalid_new_output:
            current = review.business.normalize_review_subject(
                review.ROOT, case['consumer_result'],
            )
            assert not current['ok']
        assert subject['result']['outcome'] == 'needs_human'
        assert subject['digest']


def test_native_audit_binding_and_outcome_are_both_required(monkeypatch):
    manifest = json.loads(review.MANIFEST.read_text())
    manifest['cases'] = manifest['cases'][:1]
    subject = review.business.normalize_review_subject(
        review.ROOT, manifest['cases'][0]['consumer_result'])
    monkeypatch.setattr(review.business, 'run_role', lambda **kwargs: {
        'ok': True, 'result': {}, 'tool_item_types': [],
    })
    monkeypatch.setattr(review.business, 'normalize_role_result', lambda *args: {
        'ok': True, 'result': {'outcome': 'approve', 'proposal_revision': 0,
                               'candidate_digest': 'wrong-candidate'},
    })
    assert review.run_suite(review.ROOT, manifest)['passed'] == 0
    monkeypatch.setattr(review.business, 'normalize_role_result', lambda *args: {
        'ok': True, 'result': {'outcome': 'approve', 'proposal_revision': 0,
                               'candidate_digest': subject['digest']},
    })
    assert review.run_suite(review.ROOT, manifest)['passed'] == 1


def test_corrected_open_input_stimulus_is_frozen_without_invented_options():
    from copy import deepcopy
    from app.agent_contracts import ConsumerAgentResult

    v2_path = review.ROOT / 'evals/consumer_audit_human_review/v2.json'
    assert sha256(v2_path.read_bytes()).hexdigest() == "ebddd919f764a244c30b08fcc726bcfedfc4c4b535e897bc5f4af20e055228a8"
    v2 = json.loads(v2_path.read_text())
    assert sha256(review.MANIFEST.read_bytes()).hexdigest() == "cc94a3f7783ea20742194ac81d11a137bec49301c6f7df1992aeb71a6f842bbe"
    manifest = json.loads(review.MANIFEST.read_text())
    candidate = next(case['consumer_result'] for case in manifest['cases']
                     if case['id'] == 'valid_principal_only_fact')
    result = ConsumerAgentResult.model_validate(candidate)
    assert candidate['rule_coverage'] == 0.0
    assert result.decision_options == ()
    assert result.requested_input
    assert 'no selectable execution plan' in result.decision_basis.quality_explanation
    assert 'recipient are verified' not in result.decision_basis.quality_explanation
    corrected_ids = {'valid_current_choice', 'valid_principal_only_fact'}
    for before, after in zip(v2['cases'], manifest['cases'], strict=True):
        expected = deepcopy(before)
        if before['id'] in corrected_ids:
            expected['consumer_result']['rule_coverage'] = 0.0
        assert expected == after
