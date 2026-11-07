"""Concrete source facts are bound before review and reread before dispatch."""
from dataclasses import replace

import pytest

from app.agent_context import AgentContextMessage
from app.reviewed_sources import capture_candidate_sources, changed_candidate_sources
from tests.test_reviewed_orchestration import candidate, setup


@pytest.mark.parametrize("placement", ["history", "quoted", "trigger", "authored", "other_channel", "unidentified"])
def test_unused_history_text_does_not_become_candidate_source(tmp_path, placement):
    from app.agent_turn_runner import (
        RuntimeResultValidationError, _contains_sensitive_value,
        _validate_runtime_reference_domain_result,
    )
    # Deliberately invalid, generated test data; never a real credential.
    synthetic = "sk-" + "Ab9xK2mQ7rV4zP1tL8cH5nJ0dF3sW6yU" * 2
    assert _contains_sensitive_value(synthetic)
    _, _, context = setup(tmp_path)
    message = AgentContextMessage("old", "Sender", synthetic, "")
    if placement == "other_channel":
        context = replace(context, channel="wechat")
    if placement == "unidentified":
        message = replace(message, message_id="")
    if placement == "quoted":
        message = replace(message, sender="quoted_message")
    if placement == "trigger":
        context = replace(context, trigger_text=synthetic)
        message = replace(message, message_id=context.trigger_message_id)
    context = replace(context, messages=(message,))
    prepared = capture_candidate_sources(candidate(synthetic if placement == "authored" else "Safe reply"), context, None)
    if placement == "history":
        _validate_runtime_reference_domain_result(prepared)
        assert synthetic not in prepared.model_dump_json()
        assert prepared.source_bindings[0].value["messages"] == [{"message_id": "old"}]
        assert prepared.source_bindings[0].value["trigger_text"] == context.trigger_text
    else:
        with pytest.raises((RuntimeResultValidationError, ValueError)):
            _validate_runtime_reference_domain_result(prepared)


@pytest.mark.parametrize("channel", ["dingtalk", "wechat"])
def test_audit_keeps_history_text_when_candidate_binds_only_identity(tmp_path, channel):
    from app.agent_context import AuditTurnContext

    _, _, context = setup(tmp_path)
    context = replace(context, channel=channel, messages=(
        AgentContextMessage("old", "Sender", "Historical context only", ""),
        AgentContextMessage("quote", "quoted_message", "Explicitly quoted text", ""),
    ))
    prepared = capture_candidate_sources(candidate(), context, None)
    rendered = AuditTurnContext(
        task=context, proposal_revision=0, operation_id="op", candidate=prepared,
        candidate_digest="a" * 64, audit_rules="Review the original context.",
    ).render()
    assert "Historical context only" in rendered
    assert "Explicitly quoted text" in rendered
    reference = "Candidate revision.candidate.source_bindings[0].value.messages[0].text"
    assert (reference in rendered) is (channel != "dingtalk")
    assert "Candidate revision.candidate.source_bindings[0].value.messages[1].text" in rendered


def test_context_binding_excludes_clock_receipts_and_human_selection(tmp_path):
    _, _, context = setup(tmp_path)
    prepared = capture_candidate_sources(candidate(), context, None)
    assert not changed_candidate_sources(prepared, context, None)
    assert changed_candidate_sources(prepared, replace(context, trigger_text='New applicant fact'), None)
    assert not changed_candidate_sources(prepared, replace(context, prior_human_decisions=({'kind':'selection'},)), None)


@pytest.mark.parametrize(("channel", "changed_message", "expected_change"), [
    ("dingtalk", AgentContextMessage("old", "Sender", "Expanded card", ""), False),
    ("dingtalk", AgentContextMessage("other", "Sender", "Card", ""), True),
    ("dingtalk", None, True),
    ("wechat", AgentContextMessage("old", "Sender", "Changed text", ""), True),
])
def test_reviewed_history_uses_message_identity_for_dingtalk(
    tmp_path, channel, changed_message, expected_change,
):
    _, _, context = setup(tmp_path)
    original = AgentContextMessage("old", "Sender", "Card", "")
    context = replace(context, channel=channel, messages=(original,))
    prepared = capture_candidate_sources(candidate(), context, None)
    expected = {"message_id": "old"} if channel == "dingtalk" else {
        "message_id": "old", "sender": "Sender", "text": "Card",
    }
    assert prepared.source_bindings[0].value["messages"][0] == expected

    refreshed = replace(context, messages=(changed_message,) if changed_message else ())
    assert bool(changed_candidate_sources(prepared, refreshed, None)) is expected_change
    assert changed_candidate_sources(
        prepared,
        replace(context, messages=(original, AgentContextMessage("new", "Sender", "New", ""))),
        None,
    )


@pytest.mark.parametrize(("message_id", "sender"), [
    ("message", "Sender"),
    ("quoted", "quoted_message"),
])
def test_reviewed_dingtalk_trigger_and_quote_still_bind_text(tmp_path, message_id, sender):
    _, _, context = setup(tmp_path)
    message = AgentContextMessage(message_id, sender, "Original text", "")
    context = replace(context, messages=(message,))
    prepared = capture_candidate_sources(candidate(), context, None)

    refreshed = replace(context, messages=(replace(message, text="Changed text"),))
    assert changed_candidate_sources(prepared, refreshed, None)


def test_oa_binding_uses_verified_native_form_fields_without_own_task_transitions(tmp_path):
    _, _, context = setup(tmp_path)
    prepared = candidate()
    action = prepared.proposal.actions[0].model_copy(update={'capability':'dingtalk-oa','operation':'approve','target':{'process_instance_id':'process','task_id':'task'}})
    prepared = prepared.model_copy(update={'proposal':prepared.proposal.model_copy(update={'actions':(action,)})})
    class Dws:
        value = '100'
        status = 'RUNNING'
        def read_oa_approval_detail(self, ref):
            assert ref == 'process'
            return {'success':True, 'errcode':0, 'result':{'processInstanceId':'process', 'formValueVOS':[{'id':'amount','value':self.value}], 'tasks':[{'taskStatus':self.status}]}}
    dws = Dws()
    bound = capture_candidate_sources(prepared, context, dws)
    dws.status = 'COMPLETED'
    assert not changed_candidate_sources(bound, context, dws)
    dws.value = '900'
    [change] = changed_candidate_sources(bound, context, dws)
    assert change['provider'] == 'dingtalk-oa'
    assert change['current_value']['formValueVOS'] == [{'id':'amount','value':'900'}]


def test_failed_oa_source_read_cannot_create_a_reviewed_binding(tmp_path):
    from app.dws_client import DwsError
    from app.reviewed_sources import read_provider_source
    class Dws:
        def read_oa_approval_detail(self, ref):
            return {'success':False, 'result':{}}
    with pytest.raises(DwsError, match='unavailable'):
        read_provider_source(Dws(), 'dingtalk-oa', 'process')


@pytest.mark.parametrize("payload, code, message", [
    ({"success": False, "errcode": 90020, "errmsg": "Native quota exceeded"},
     "90020", "Native quota exceeded"),
    ({"success": False, "errorCode": "PAT_MEDIUM_RISK_NO_PERMISSION",
      "errorMessage": "Native resource permission denied"},
     "PAT_MEDIUM_RISK_NO_PERMISSION", "Native resource permission denied"),
])
def test_native_oa_failure_envelope_preserves_original_code_and_message(payload, code, message):
    from app.dws_client import DwsError
    from app.reviewed_sources import read_provider_source

    class Source:
        def read_oa_approval_detail(self, ref):
            assert ref == "process"
            return payload

    with pytest.raises(DwsError, match=message) as raised:
        read_provider_source(Source(), "dingtalk-oa", "process")
    assert raised.value.code == code
    assert raised.value.server_key == "dingtalk-oa"
    assert raised.value.retryable_external_dependency is False
