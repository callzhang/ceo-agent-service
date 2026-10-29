"""A reviewed OKR score must bind one current batch, KR and evaluator."""

from __future__ import annotations


import pytest

from app.agent_contracts import ProposedAction


def _action() -> ProposedAction:
    return ProposedAction(
        description="Score one KR from reviewed evidence",
        action_identity="okr-period-1-objective-1-kr-1-evaluator-1",
        capability="dingtang-okr",
        operation="score_kr",
        target={
            "period_id": "period-1",
            "objective_id": "objective-1",
            "batch_id": "batch-1",
            "kr_id": "kr-1",
            "evaluator_user_id": "evaluator-1",
        },
        payload={"score": 0.8, "score_description": "Verified delivery evidence"},
        effect="external",
    )


def _detail(*, batch="batch-1", score=None, info="", evaluator="evaluator-1"):
    return {
        "batch_id": batch,
        "principal_user_id": "evaluator-1",
        "detail": {
            "objectiveId": "objective-1",
            "okrId": "period-1",
            "krScoreDetails": [{
                "krId": "kr-1",
                "roleUsers": [{"users": [{
                    "userId": evaluator,
                    "name": "Reviewer",
                    "status": "complete" if score is not None else "wait",
                    "score": score,
                    "info": info,
                }]}],
            }],
        },
    }


class FakeClient:
    def __init__(self, score_result=None, *reads):
        self.score_result = score_result
        self.reads = list(reads)
        self.score_calls = []

    def read_score_detail(self, *, period_id, objective_id):
        assert (period_id, objective_id) == ("period-1", "objective-1")
        return self.reads.pop(0)

    def score_kr(self, **kwargs):
        self.score_calls.append(kwargs)
        if isinstance(self.score_result, Exception):
            raise self.score_result
        return self.score_result


def test_score_dispatch_writes_exact_reviewed_value_and_reads_back():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient({
        "status": "verified",
        "current": _detail(score=0.8, info="Verified delivery evidence"),
    })
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "verified"
    assert client.score_calls == [{
        "period_id": "period-1", "objective_id": "objective-1",
        "batch_id": "batch-1", "kr_id": "kr-1",
        "evaluator_user_id": "evaluator-1", "score": 0.8,
        "description": "Verified delivery evidence",
    }]


def test_score_dispatch_refuses_wrong_evaluator_without_write():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient({"status": "failed", "reason": "okr_score_target_changed"})
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "failed"
    assert len(client.score_calls) == 1


def test_score_dispatch_refuses_changed_batch_without_write():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient({"status": "verified", "current": _detail(batch="batch-2", score=0.8, info="Verified delivery evidence")})
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "uncertain"


def test_score_dispatch_does_not_verify_another_logged_in_evaluator():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    detail = _detail(score=0.8, info="Verified delivery evidence")
    detail["principal_user_id"] = "another-reviewer"
    client = FakeClient({"status": "verified", "current": detail})
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "uncertain"


def test_score_dispatch_does_not_verify_another_period():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    detail = _detail(score=0.8, info="Verified delivery evidence")
    detail["detail"]["okrId"] = "another-period"
    client = FakeClient({"status": "verified", "current": detail})
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "uncertain"


def test_score_reconcile_reads_existing_effect_without_writing_again():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient(None, _detail(score=0.8, info="Verified delivery evidence"))
    result = DingtangOkrScoreHandler(client).reconcile(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result is not None and result.status == "verified"
    assert client.score_calls == []


def test_score_dispatch_does_not_rewrite_an_exact_existing_score():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient({
        "status": "verified", "write_attempted": False,
        "current": _detail(score=0.8, info="Verified delivery evidence"),
    })
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "verified"
    assert len(client.score_calls) == 1


def test_score_dispatch_keeps_ambiguous_write_as_uncertain():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient({"status": "uncertain", "reason": "okr_score_write_result_unknown"})
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "uncertain"
    assert len(client.score_calls) == 1


def test_score_dispatch_recovers_verified_write_after_lost_response():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    client = FakeClient(TimeoutError("lost response"), _detail(score=0.8, info="Verified delivery evidence"))
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "verified"
    assert len(client.score_calls) == 1


def test_score_dispatch_rejects_invalid_api_scale_without_provider_call():
    from app.dingtang_okr_action import DingtangOkrScoreHandler

    action = _action().model_copy(update={"payload": {"score": 80, "score_description": "Evidence"}})
    client = FakeClient()
    result = DingtangOkrScoreHandler(client).dispatch(
        action, action_key="reviewed-1", candidate={}
    )
    assert result.status == "failed"
    assert client.score_calls == []


def test_score_source_failure_before_page_write_does_not_reconcile_or_retry():
    from app.dingtang_okr_action import (
        DingtangOkrPageRequestError,
        DingtangOkrScoreHandler,
    )

    client = FakeClient(DingtangOkrPageRequestError(
        "SSO unavailable", may_have_written=False
    ))
    result = DingtangOkrScoreHandler(client).dispatch(
        _action(), action_key="reviewed-1", candidate={}
    )
    assert result.status == "failed"
    assert client.reads == []
    assert len(client.score_calls) == 1




@pytest.mark.parametrize("changed", [
    {"target": {"objective_id": "objective-1"}},
    {"payload": {"score": 80, "score_description": "Evidence"}},
    {"payload": {"score": 0.8, "score_description": ""}},
])
def test_score_contract_rejects_incomplete_or_invalid_plan(changed):
    payload = _action().model_dump()
    payload.update(changed)
    with pytest.raises(ValueError):
        ProposedAction.model_validate(payload)


def test_score_action_is_registered_and_discoverable():
    from app.action_contract_catalog import read_system_action_contract
    from app.system_action_handlers import native_dws_handlers

    contract = read_system_action_contract("dingtang-okr", "score_kr")
    assert "evaluator_user_id" in contract["contract"]
    assert ("dingtang-okr", "score_kr") in native_dws_handlers(object(), object())






















@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_score_detail_read_is_declared_to_both_roles(role):
    import asyncio
    from app.agent_cli import build_role_server
    from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS

    tools = {tool.name: tool for tool in asyncio.run(build_role_server(role).list_tools())}
    assert tools["read_dingtang_okr_score_detail"].annotations.readOnlyHint is True
    assert "read_dingtang_okr_score_detail" in AGENT_CLI_READ_TOOLS
