import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.consumer_purpose import purpose_output_model


def test_oa_return_is_a_real_business_action():
    output = purpose_output_model('oa').model_validate({
        'action': 'return', 'reason': 'Access denied; unable to request access',
        'target': {'process_instance_id': 'process', 'task_id': '001', 'target_activity_id': 'applicant'},
        'payload': {'remark': 'Please provide readable materials', 'revert_action': 'REVERT_FOR_RESUBMIT'},
        'evidence_refs': ['read:denied'],
    })
    action = output.to_proposal().actions[0]
    assert (action.capability, action.operation) == ('dingtalk-oa', 'revert_task')
    assert action.target['task_id'] == '001'


@pytest.mark.parametrize('change', [
    {'action': 'invented'},
    {'target': {'process_instance_id': 'p', 'task_id': 123, 'target_activity_id': 'a'}},
    {'payload': {'remark': 'r'}},
    {'target': {'process_instance_id': 'p', 'task_id': 't'}},
])
def test_oa_action_contract_is_strict_in_parser_and_schema(change):
    value = {'action': 'return', 'reason': 'r',
             'target': {'process_instance_id': 'p', 'task_id': 't', 'target_activity_id': 'a'},
             'payload': {'remark': 'r', 'revert_action': 'REVERT_FOR_RESUBMIT'}, 'evidence_refs': ['read:r']}
    value.update(change)
    model = purpose_output_model('oa')
    with pytest.raises(ValidationError):
        model.model_validate(value)
    assert list(Draft202012Validator(model.model_json_schema()).iter_errors(value))


def test_purpose_schema_does_not_offer_other_purposes_actions():
    schema = purpose_output_model('general').model_json_schema()
    assert 'return' not in schema['properties']['action']['enum']
    with pytest.raises(ValidationError):
        purpose_output_model('general').model_validate({
            'action': 'return', 'reason': 'r', 'target': {}, 'payload': {}, 'evidence_refs': []})


def test_retry_has_no_business_proposal_and_requires_evidence():
    model = purpose_output_model('oa')
    value = {'action': 'retry', 'reason': 'access requested', 'target': {}, 'payload': {},
             'evidence_refs': ['request-receipt']}
    result = model.model_validate(value)
    assert result.to_proposal() is None
    value['evidence_refs'] = []
    with pytest.raises(ValidationError):
        model.model_validate(value)


def test_historical_candidate_serialization_does_not_add_new_metadata():
    from test_agent_orchestrator import candidate
    from app.agent_contracts import parse_persisted_consumer_result
    original = candidate().model_dump(mode='json')
    original.pop('purpose_output', None)
    restored = parse_persisted_consumer_result(original)
    assert restored.model_dump(mode='json') == original


def test_retry_wire_schema_does_not_offer_a_technical_error_state():
    from app.agent_wire_contracts import consumer_wire_model
    model = consumer_wire_model('oa')
    value = {'action': 'retry', 'reason': 'access requested', 'target': {}, 'payload': {},
             'evidence_refs': ['receipt'], 'summary': 'Waiting for permission',
             'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0,
             'information_completeness': 0.3, 'durable_memories': []}
    schema = model.model_json_schema()
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(value))
    assert model.model_validate(value).to_result().outcome.value == 'retry'
    for bad in [{'error_code': 'access_requested'}, {'error_retryable': True},
                {'error_authorization_required': True}]:
        invalid = {**value, **bad}
        assert list(validator.iter_errors(invalid))
        with pytest.raises(ValidationError):
            model.model_validate(invalid)
