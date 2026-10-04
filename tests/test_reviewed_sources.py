"""Concrete source facts are bound before review and reread before dispatch."""
from dataclasses import replace

import pytest

from app.reviewed_sources import capture_candidate_sources, changed_candidate_sources
from tests.test_reviewed_orchestration import candidate, setup


def test_context_binding_excludes_clock_receipts_and_human_selection(tmp_path):
    _, _, context = setup(tmp_path)
    prepared = capture_candidate_sources(candidate(), context, None)
    assert not changed_candidate_sources(prepared, context, None)
    assert changed_candidate_sources(prepared, replace(context, trigger_text='New applicant fact'), None)
    assert not changed_candidate_sources(prepared, replace(context, prior_human_decisions=({'kind':'selection'},)), None)


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
    from app.reviewed_sources import read_provider_source
    class Dws:
        def read_oa_approval_detail(self, ref):
            return {'success':False, 'result':{}}
    with pytest.raises(ValueError, match='unavailable'):
        read_provider_source(Dws(), 'dingtalk-oa', 'process')
