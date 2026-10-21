"""A reviewed OKR score must bind one current batch, KR and evaluator."""

from __future__ import annotations

import json
import subprocess

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


def test_browser_timeout_stops_its_chrome_process_group(monkeypatch):
    from app import dingtang_okr_action as action_module
    from scripts import dingteam_okr_headless_source as source

    stopped = []

    class Process:
        def communicate(self, *, input, timeout):
            assert timeout == 290
            assert json.loads(input)["operation"] == "score_kr"
            raise subprocess.TimeoutExpired("score", timeout)

    def start(_command, **kwargs):
        assert kwargs["start_new_session"] is True
        return Process()

    monkeypatch.setattr(action_module.subprocess, "Popen", start)
    monkeypatch.setattr(source, "_stop_process_group", stopped.append)
    with pytest.raises(action_module.DingtangOkrPageRequestError) as error:
        action_module.DingtangOkrBrowserClient()._call({"operation": "score_kr"})
    assert error.value.may_have_written is True
    assert len(stopped) == 1


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


def _run_page_script(request, *, batch="batch-1", evaluator="evaluator-1", principal="evaluator-1", batch_name="2026年1季度", detail_period="period-1", score_type=1, is_current=True, after_score=0.8, already=False, write_error=False, read_error=False):
    from scripts.dingteam_okr_score import _page_script

    source = _page_script(request, "result")
    harness = """
const calls = [];
let result = '';
let detailReads = 0;
const config = __CONFIG__;
global.window = global;
global.document = {documentElement: {setAttribute: (_key, value) => {result = value;}}};
global.webpackChunkallinone = {push: ([, , callback]) => callback(id => {
  if (id === 86036) return {A: {userId: config.principal}};
  if (id === 1872) return {A: {
    person: {period: {list: async body => {
      calls.push({path: '/data/okr/person/period/list', body});
      return {list: [{okrId: 'period-1', name: '2026年1季度'}]};
    }}},
    objective: Object.fromEntries([
      'getObjectiveScoreBatch', 'findScoreInfos', 'getScoreDetail', 'makeScore'
    ].map(method => [method, async body => {
      calls.push({path: '/data/okr/objective/' + method, body});
      if (method === 'getObjectiveScoreBatch' && config.read_error) throw new Error('read unavailable');
      if (method === 'makeScore' && config.write_error) throw new Error('lost response');
      if (method === 'getObjectiveScoreBatch') return {cells: [{batchId: config.batch, name: config.batch_name, isCurrent: config.is_current}]};
      if (method === 'findScoreInfos') return {scoreType: config.score_type};
      if (method === 'getScoreDetail') {
        detailReads += 1;
        const scored = detailReads > 1 || config.already;
        return {objectiveId: 'objective-1', okrId: config.detail_period, krScoreDetails: [{krId: 'kr-1', roleUsers: [{users: [{
          userId: config.evaluator,
          status: scored ? 'complete' : 'wait',
          score: scored ? config.after_score : null,
          info: scored ? 'Verified delivery evidence' : ''
        }]}]}]};
      }
      return {};
    }]))
  }};
  throw new Error('unexpected page module');
})};
__SOURCE__
setTimeout(() => console.log(JSON.stringify({calls, result: JSON.parse(result)})), 0);
""".replace("__SOURCE__", source).replace("__CONFIG__", json.dumps({
        "batch": batch, "evaluator": evaluator, "principal": principal,
        "batch_name": batch_name, "detail_period": detail_period,
        "score_type": score_type, "is_current": is_current,
        "after_score": after_score,
        "already": already, "write_error": write_error, "read_error": read_error,
    }))
    completed = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, check=True
    )
    return json.loads(completed.stdout)


def test_browser_read_injection_only_calls_current_batch_and_detail():
    result = _run_page_script({
        "operation": "read_score_detail",
        "period_id": "period-1",
        "objective_id": "objective-1",
    })
    assert [call["path"] for call in result["calls"]] == [
        "/data/okr/person/period/list",
        "/data/okr/objective/getObjectiveScoreBatch",
        "/data/okr/objective/findScoreInfos",
        "/data/okr/objective/getScoreDetail",
    ]
    assert result["calls"][3]["body"] == {
        "objectiveId": "objective-1", "batchId": "batch-1"
    }
    assert result["result"]["data"]["batch_id"] == "batch-1"


def test_browser_write_injection_uses_one_exact_reviewed_record():
    result = _run_page_script({
        "operation": "score_kr",
        "period_id": "period-1",
        "objective_id": "objective-1",
        "batch_id": "batch-1",
        "kr_id": "kr-1",
        "evaluator_user_id": "evaluator-1",
        "score": 0.8,
        "description": "Verified delivery evidence",
    })
    writes = [call for call in result["calls"] if call["path"].endswith("makeScore")]
    assert writes == [{
        "path": "/data/okr/objective/makeScore",
        "body": {"objectiveId": "objective-1", "records": [{
            "mainId": "kr-1", "score": 0.8,
            "scoreDesc": "Verified delivery evidence", "type": 2,
        }]},
    }]
    assert result["result"]["data"]["status"] == "verified"


@pytest.mark.parametrize("changed", [
    {"batch": "batch-2"},
    {"evaluator": "another-reviewer"},
    {"principal": "another-reviewer"},
    {"batch_name": "another-period"},
    {"detail_period": "another-period"},
    {"score_type": 2},
    {"is_current": False},
])
def test_browser_refuses_changed_target_before_write(changed):
    request = {
        "operation": "score_kr", "period_id": "period-1",
        "objective_id": "objective-1", "batch_id": "batch-1",
        "kr_id": "kr-1", "evaluator_user_id": "evaluator-1",
        "score": 0.8, "description": "Verified delivery evidence",
    }
    result = _run_page_script(request, **changed)
    assert result["result"]["data"]["status"] == "failed"
    assert not any(call["path"].endswith("makeScore") for call in result["calls"])


def test_browser_does_not_repeat_exact_existing_score():
    request = {
        "operation": "score_kr", "period_id": "period-1",
        "objective_id": "objective-1", "batch_id": "batch-1",
        "kr_id": "kr-1", "evaluator_user_id": "evaluator-1",
        "score": 0.8, "description": "Verified delivery evidence",
    }
    result = _run_page_script(request, already=True)
    assert result["result"]["data"]["status"] == "verified"
    assert not any(call["path"].endswith("makeScore") for call in result["calls"])


def test_browser_lost_write_response_uses_readback_without_resubmitting():
    request = {
        "operation": "score_kr", "period_id": "period-1",
        "objective_id": "objective-1", "batch_id": "batch-1",
        "kr_id": "kr-1", "evaluator_user_id": "evaluator-1",
        "score": 0.8, "description": "Verified delivery evidence",
    }
    result = _run_page_script(request, write_error=True, after_score=0.6)
    assert result["result"]["data"]["status"] == "uncertain"
    assert sum(call["path"].endswith("makeScore") for call in result["calls"]) == 1


def test_browser_read_failure_before_write_is_final_without_a_write():
    request = {
        "operation": "score_kr", "period_id": "period-1",
        "objective_id": "objective-1", "batch_id": "batch-1",
        "kr_id": "kr-1", "evaluator_user_id": "evaluator-1",
        "score": 0.8, "description": "Verified delivery evidence",
    }
    result = _run_page_script(request, read_error=True)
    assert result["result"]["data"]["status"] == "failed"
    assert not any(call["path"].endswith("makeScore") for call in result["calls"])


def test_browser_request_uses_existing_headless_service_context(monkeypatch):
    from contextlib import nullcontext
    from scripts import dingteam_okr_score as source

    calls = []

    class Page:
        url = "https://dingokr.dingteam.com/web/okr/pc/index.html"

        def set_default_timeout(self, timeout):
            calls.append(("timeout", timeout))

        def goto(self, url, **kwargs):
            calls.append(("goto", url))

        def wait_for_timeout(self, milliseconds):
            calls.append(("wait", milliseconds))

        def evaluate(self, script, argument=None):
            calls.append(("evaluate", argument))
            if "__ceo_okr_ready_" in script:
                return True
            if argument is not None:
                return json.dumps({"ok": True, "data": {"batch_id": "batch-1", "detail": {}}})

    class Context:
        def new_page(self):
            return Page()

    monkeypatch.setattr(source.headless, "_headless_browser_lock", nullcontext)
    monkeypatch.setattr(source.headless, "sync_playwright", nullcontext)
    monkeypatch.setattr(source.headless, "_service_browser", lambda _playwright: nullcontext(Context()))
    monkeypatch.setattr(source.headless, "_maybe_attempt_local_dingtalk_sso", lambda *a, **kw: False)
    payload = source.run_request({
        "operation": "read_score_detail", "period_id": "period-1",
        "objective_id": "objective-1",
    })
    assert payload["batch_id"] == "batch-1"
    assert [call[0] for call in calls] == [
        "timeout", "goto", "wait", "wait", "evaluate", "evaluate", "evaluate"
    ]


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_score_detail_read_is_declared_to_both_roles(role):
    import asyncio
    from app.agent_cli import build_role_server
    from app.wechat.codex_safety import AGENT_CLI_READ_TOOLS

    tools = {tool.name: tool for tool in asyncio.run(build_role_server(role).list_tools())}
    assert tools["read_dingtang_okr_score_detail"].annotations.readOnlyHint is True
    assert "read_dingtang_okr_score_detail" in AGENT_CLI_READ_TOOLS
