import json

import pytest

from app.agent_contracts import ConsumerAgentResult, ReviewedSourceBinding
from app.agent_turn_runner import (
    RuntimeResultValidationError,
    _contains_local_runtime_value,
    _contains_sensitive_value,
    _project_runtime_domain_result,
    _validate_runtime_reference_domain_result,
)


def _result(value, *, object_ref="message:source", summary="No external action is needed."):
    return ConsumerAgentResult.model_validate({
        "outcome": "no_action", "summary": summary, "proposal": None,
        "decision_options": [], "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0, "information_completeness": 1.0,
        "source_bindings": [ReviewedSourceBinding(provider="task_context", object_ref=object_ref, value=value)],
    })


def _provider_form_value():
    return {"formValueVOS": [{"value": json.dumps({
        "section": {"entries": [{"fields": [{"content": "ordinary source evidence"}]}]},
    })}]}


def test_valid_source_depth_is_not_charged_for_candidate_packaging():
    value = _provider_form_value()
    result = _result(value)
    original = result.model_dump_json()
    assert not _contains_sensitive_value(value)
    assert not _contains_local_runtime_value(value)
    # Packaging depth must not consume the source object's structural budget.
    assert not _contains_sensitive_value(_project_runtime_domain_result(result))
    assert not _contains_local_runtime_value(_project_runtime_domain_result(result))
    _validate_runtime_reference_domain_result(result, allow_configured_feedback_links=True)
    assert result.model_dump_json() == original


@pytest.mark.parametrize("value", [
    {"access_token": "private-source-secret"},
    {"resource": "https://example.test/download?signature=private-source-secret"},
    {"encoded": json.dumps({"access_token": "private-source-secret"})},
])
def test_source_credentials_and_download_capabilities_still_fail(value):
    with pytest.raises(RuntimeResultValidationError, match="runtime_result_source_invalid"):
        _validate_runtime_reference_domain_result(_result(value), allow_configured_feedback_links=True)


def test_source_decoded_depth_limit_still_fails():
    nested = {"content": "ordinary"}
    for _ in range(14):
        nested = {"section": nested}
    value = {"encoded": json.dumps(nested)}
    with pytest.raises(RuntimeResultValidationError, match="runtime_result_source_invalid"):
        _validate_runtime_reference_domain_result(_result(value), allow_configured_feedback_links=True)


def _nested_oa_table_source(*, include_credential=False):
    stat = [{"access_token": "opaque"}] if include_credential else [{"value": "ordinary source evidence"}]
    return {
        "processInstanceId": "process",
        "formValueVOS": [
            {},
            {"extValue": json.dumps({"list": [{"rowValue": [
                {}, {}, {"extendValue": json.dumps({"statValue": stat})},
            ]}]})},
        ],
    }


def test_nested_oa_serialized_fields_do_not_consume_structure_depth_budget():
    value = _nested_oa_table_source()

    assert not _contains_sensitive_value(value)
    assert not _contains_local_runtime_value(value)
    _validate_runtime_reference_domain_result(
        _result(value),
        allow_configured_feedback_links=True,
    )


def test_normal_oa_table_class_metadata_one_level_deeper_remains_readable():
    row = [{} for _ in range(6)] + [{"extendValue": {
        "detailList": [{"classInfo": {"sections": [{"endAcross": "ordinary source evidence"}]}}],
    }}]
    value = {
        "processInstanceId": "process",
        "formValueVOS": [{} for _ in range(9)] + [{"value": json.dumps([{"rowValue": row}])}],
    }

    assert not _contains_sensitive_value(value)
    _validate_runtime_reference_domain_result(
        _result(value),
        allow_configured_feedback_links=True,
    )


def test_nested_oa_serialized_fields_still_reject_credentials():
    value = _nested_oa_table_source(include_credential=True)

    assert _contains_sensitive_value(value)
    with pytest.raises(RuntimeResultValidationError, match="runtime_result_source_invalid"):
        _validate_runtime_reference_domain_result(_result(value), allow_configured_feedback_links=True)


def test_nested_json_text_depth_limit_still_fails():
    nested = {"content": "ordinary"}
    for _ in range(13):
        nested = json.dumps(nested)
    value = {"encoded": nested}

    assert _contains_sensitive_value(value)
    with pytest.raises(RuntimeResultValidationError, match="runtime_result_source_invalid"):
        _validate_runtime_reference_domain_result(_result(value), allow_configured_feedback_links=True)


@pytest.mark.parametrize("field", ["summary", "object_ref"])
def test_authored_and_source_metadata_credentials_still_fail(field):
    kwargs = {field: "token=private-source-secret"}
    with pytest.raises(ValueError, match="agent_result_contains_sensitive_value"):
        _validate_runtime_reference_domain_result(_result({"content": "ordinary"}, **kwargs),
                                                 allow_configured_feedback_links=True)


def test_combined_sources_still_obey_the_total_utf8_codec_limit():
    from app.agent_turn_runner import _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES

    value = {"content": "界" * 7000}
    assert len(json.dumps(value, ensure_ascii=False).encode()) < _RUNTIME_DOMAIN_RESULT_CODEC_MAX_BYTES
    result = _result(value).model_copy(update={"source_bindings": tuple(
        ReviewedSourceBinding(provider="task_context", object_ref=f"message:source-{index}", value=value)
        for index in range(4)
    )})
    with pytest.raises(ValueError, match="runtime_result_reference_too_large"):
        _validate_runtime_reference_domain_result(result, allow_configured_feedback_links=True)
