"""Concrete source facts are bound before review and reread before dispatch."""
from dataclasses import replace

import pytest

from app.agent_context import AgentContextMessage
from app.reviewed_sources import capture_candidate_sources, changed_candidate_sources
from tests.test_reviewed_orchestration import candidate, setup


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
    assert prepared.source_bindings[0].value["messages"][0]["text"] == "Card"

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
